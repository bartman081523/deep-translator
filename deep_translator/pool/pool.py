"""DeepTranslatorPool: Rate, Retry, Cache, Dedup, verifiziertes Batchen.

Verbraucher-API:
    pool = DeepTranslatorPool(cache_db="translations.db")
    pool.translate(text, target="de")                       -> str
    pool.translate_many(texts, target="de", on_error=...)   -> list[str|None]

Fehlervertrag: on_error="raise" wirft den ersten Pool-Fehler; "mark" setzt
fehlgeschlagene Positionen auf None und sammelt sie in ``pool.errors``.
"""

import random
import time
from concurrent.futures import ThreadPoolExecutor

from deep_translator.exceptions import TooManyRequests

from deep_translator.pool.cache import TranslationCache
from deep_translator.pool.errors import (
    ANOMAL,
    PERMANENT,
    TRANSIENT,
    PoolAnomalyError,
    PoolPermanentError,
    PoolTransientError,
    classify,
)
from deep_translator.pool.ratelimit import AdaptiveRate

GOOGLE_CHAR_LIMIT = 5000  # validate.py: len(text) < 5000 streng


def _default_translator_factory(source: str, target: str):
    from deep_translator import GoogleTranslator

    return GoogleTranslator(source=source, target=target)


class DeepTranslatorPool:
    """Pool-Komponente um GoogleTranslator (bartman-Fork).

    Frische Translator-Instanz pro Request — translate() mutiert
    ``self._url_params`` an der Instanz, geteilte Instanzen racen über
    Threads. Der Konstruktor der Library macht kein I/O.
    """

    def __init__(
        self,
        cache_db: str = None,
        rate: float = 4.0,
        min_rate: float = 0.25,
        max_rate: float = 5.0,
        cooldown_start: float = 60.0,
        cooldown_max: float = 1800.0,
        success_window: int = 100,
        max_retries: int = 4,
        backoff_base: float = 2.0,
        backoff_jitter: float = 0.25,
        anomaly_retries: int = 1,
        workers: int = 2,
        allow_batch: bool = False,
        batch_audit_samples: int = 2,
        translator_factory=None,
        clock=time.monotonic,
        sleeper=time.sleep,
    ):
        self.cache = TranslationCache(cache_db) if cache_db else None
        self.rate_ctrl = AdaptiveRate(
            rate=rate,
            min_rate=min_rate,
            max_rate=max_rate,
            cooldown_start=cooldown_start,
            cooldown_max=cooldown_max,
            success_window=success_window,
            clock=clock,
            sleeper=sleeper,
        )
        self.clock = clock
        self.sleeper = sleeper
        self.max_retries = int(max_retries)
        self.backoff_base = float(backoff_base)
        self.backoff_jitter = float(backoff_jitter)
        self.anomaly_retries = int(anomaly_retries)
        self.workers = int(workers)
        self.allow_batch = bool(allow_batch)
        self.batch_audit_samples = int(batch_audit_samples)
        self.batch_disabled = False
        self._batch_verified_pairs = set()  # (source, target)
        self._factory = translator_factory or _default_translator_factory
        self.errors = {}  # text -> Pool*Error (letzter translate_many-Aufruf)

    # ---------- öffentliche API ----------

    def translate(self, text: str, target: str = "en", source: str = "auto") -> str:
        return self.translate_many([text], target=target, source=source)[0]

    def translate_many(
        self,
        texts,
        target: str = "en",
        source: str = "auto",
        on_error: str = "raise",
        batch=None,
    ):
        """Übersetzte Liste in Eingabe-Reihenfolge.

        batch=True erzwingt den Batch-Modus (nur sinnvoll mit allow_batch /
        bestandener Verifikation); batch=False erzwingt Singles.
        """
        if on_error not in ("raise", "mark"):
            raise ValueError('on_error muss "raise" oder "mark" sein')
        texts = list(texts)
        self.errors = {}

        def norm_key(text):
            """Cache-/Dedup-Schlüssel: gestrippt; Whitespace-/Nicht-Str unverändert."""
            if not isinstance(text, str):
                return text
            stripped = text.strip()
            return stripped if stripped else text

        by_key = {}  # normierter Schlüssel -> str oder Exception
        pending = []
        seen_pending = set()
        for text in dict.fromkeys(texts):
            key = norm_key(text)
            if key in by_key:
                continue
            if not isinstance(text, str):
                exc = PoolPermanentError(f"Nicht-String-Eingabe: {text!r}")
                self.errors[key] = exc
                by_key[key] = exc
                continue
            if text.strip() == "":  # Whitespace-only → Identität, keine Netz-Anfrage
                by_key[key] = text
                continue
            cached = self.cache.get(key, target, source) if self.cache else None
            if cached is not None:
                by_key[key] = cached
            elif key not in seen_pending:
                seen_pending.add(key)
                pending.append(key)

        if pending:
            use_batch = (
                batch if batch is not None
                else self.allow_batch and len(pending) > 1
            )
            if use_batch:
                by_key.update(
                    self._translate_batched(pending, target, source, on_error)
                )
            else:
                by_key.update(
                    self._translate_singles(pending, target, source)
                )

        results = []
        for text in texts:
            value = by_key.get(norm_key(text))
            if isinstance(value, Exception):
                if on_error == "raise":
                    raise value
                results.append(None)
            else:
                results.append(value)
        return results

    # ---------- interne Pfade ----------

    def _translate_singles(self, texts, target: str, source: str):
        """Jeder Text einzeln; parallele Worker, eine globale Rate."""
        out = {}

        def work(text):
            try:
                result = self._translate_with_retries(text, target, source)
                out[text] = result
                if self.cache:  # Single-Pfad = verifiziert → cachebar
                    self.cache.put(text, target, source, result)
            except Exception as exc:  # Pool*-Fehler aus dem Retry-Pfad
                out[text] = exc
                self.errors[text] = exc

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            list(executor.map(work, texts))
        return out

    def _translate_with_retries(self, text: str, target: str, source: str) -> str:
        transient_failures = 0
        anomaly_failures = 0
        while True:
            self.rate_ctrl.before_request()
            translator = self._factory(source=source, target=target)
            try:
                result = translator.translate(text)
            except Exception as exc:
                kind = classify(exc)
                if kind == PERMANENT:
                    raise PoolPermanentError(f"{type(exc).__name__}: {exc}") from exc
                if kind == TRANSIENT:
                    if isinstance(exc, TooManyRequests):
                        self.rate_ctrl.on_rate_limit()
                    else:
                        self.rate_ctrl.on_transient()
                    transient_failures += 1
                    if transient_failures > self.max_retries - 1:
                        raise PoolTransientError(f"{type(exc).__name__}: {exc}") from exc
                else:  # anomal
                    self.rate_ctrl.on_transient()
                    anomaly_failures += 1
                    if anomaly_failures > self.anomaly_retries:
                        raise PoolAnomalyError(f"{type(exc).__name__}: {exc}") from exc
                self._backoff(transient_failures, anomaly_failures)
                continue
            if not isinstance(result, str) or result.strip() == "":
                # anomales Ergebnis (None/leer bei nichtleerem Input)
                self.rate_ctrl.on_transient()
                anomaly_failures += 1
                if anomaly_failures > self.anomaly_retries:
                    raise PoolAnomalyError(
                        f"leeres/ungültiges Ergebnis für: {text!r}"
                    )
                self._backoff(transient_failures, anomaly_failures)
                continue
            self.rate_ctrl.on_success()
            return result

    def _backoff(self, transient_failures: int, anomaly_failures: int) -> None:
        attempts = max(transient_failures, anomaly_failures)
        delay = self.backoff_base ** min(attempts - 1, 8)
        if self.backoff_jitter:
            delay *= 1.0 + random.uniform(0.0, self.backoff_jitter)
        self.sleeper(delay)

    # ---------- verifiziertes Batchen ----------

    def _translate_batched(self, texts, target: str, source: str, on_error: str):
        """Batch über \\n-Join mit Verifikation; bei Mismatch Fallback auf Singles.

        Rückgabe: dict text -> str (verifiziert) — nicht verifizierte Batch-
        Zeilen fließen hier nie aus (Mismatch → kompletter Fallback).
        """
        if self.batch_disabled or len(texts) < 2:
            return self._translate_singles(texts, target, source)
        joined = "\n".join(texts)
        if len(joined) >= GOOGLE_CHAR_LIMIT or any(
            len(t) >= GOOGLE_CHAR_LIMIT for t in texts
        ):
            return self._translate_singles(texts, target, source)

        pair = (source, target)
        try:
            batch_result = self._translate_with_retries(joined, target, source)
        except Exception as exc:
            # Batch-Pfad selbst fehlgeschlagen → Singles probieren
            self._note_batch_failure(f"Batch-Request fehlgeschlagen: {exc}")
            return self._translate_singles(texts, target, source)

        lines = batch_result.split("\n")
        if len(lines) != len(texts) or any(line.strip() == "" for line in lines):
            self._note_batch_failure(
                f"Batch-Verifikation fehlgeschlagen: {len(lines)} Zeilen für"
                f" {len(texts)} Texte"
            )
            return self._translate_singles(texts, target, source)

        audited = pair in self._batch_verified_pairs
        if not audited:
            audited = self._audit_batch(lines, texts, target, source)
            if audited:
                self._batch_verified_pairs.add(pair)
            else:
                self._note_batch_failure(
                    "Batch-Stichproben-Audit fehlgeschlagen"
                )
                return self._translate_singles(texts, target, source)

        # verifiziert: alle Zeilen cachebar (Batch-Pfad bestanden)
        out = {}
        for text, line in zip(texts, lines):
            out[text] = line
            if self.cache and self.batch_audit_samples > 0:
                self.cache.put(text.strip(), target, source, line)
        return out

    def _audit_batch(self, lines, texts, target: str, source: str) -> bool:
        """Vergleiche Stichproben einzeln übersetzt mit den Batch-Zeilen."""
        k = min(self.batch_audit_samples, len(texts))
        if k == 0:
            return False  # ohne Audit gilt kein Batch als verifiziert
        for idx in range(k):
            try:
                single = self._translate_with_retries(texts[idx], target, source)
            except Exception:
                return False
            if single != lines[idx]:
                return False
        return True

    def _note_batch_failure(self, reason: str) -> None:
        # Batch dauerhaft deaktivieren (dieser Pool-Instanz)
        self.batch_disabled = True
        self.errors["__batch__"] = PoolAnomalyError(reason)