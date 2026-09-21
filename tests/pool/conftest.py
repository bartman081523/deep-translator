"""Gemeinsame Test-Infrastruktur: Fake-Uhr, Fake-Sleeper, Fake-Translator.

Kein Netz im Testlauf — die translator_factory wird ersetzt.
"""

import pytest

from deep_translator.pool import DeepTranslatorPool


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSleeper:
    """Schläft nicht, sondern setzt die Fake-Uhr vor (deterministisch)."""

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.sleeps = []

    def __call__(self, seconds: float) -> None:
        assert seconds >= 0, f"negativer Sleep: {seconds}"
        self.sleeps.append(seconds)
        self.clock.advance(seconds)


class ScriptTranslator:
    """Verhält sich wie GoogleTranslator.translate(), gesteuert per Skript.

    Skript-Items: ("echo",) → ">{text}", ("fixed", s) → s, ("raise", exc).
    Nach aufgebrauchtem Skript: ("echo",).
    """

    def __init__(self, script: list, calls: list):
        self._script = script  # geteilt über Instanzen (jede Request-Instanz)
        self._calls = calls

    def translate(self, text: str) -> str:
        self._calls.append(text)
        outcome = self._script.pop(0) if self._script else ("echo",)
        kind = outcome[0]
        if kind == "echo":
            return ">" + text
        if kind == "fixed":
            return outcome[1]
        if kind == "raise":
            raise outcome[1]
        raise AssertionError(f"unbekanntes Skript-Item: {outcome!r}")


def make_factory(script: list, calls: list, ctor_log: list):
    def factory(source: str, target: str):
        ctor_log.append((source, target))
        return ScriptTranslator(script, calls)

    return factory


def make_pool(clock, sleeper, script=None, **kwargs):
    """Pool mit Fake-Uhr/Sleeper und Fake-Translator.

    Rückgabe: (pool, calls, ctor_log) — calls = übersetzte Texte in
    Request-Reihenfolge, ctor_log = (source, target) je Instanz.
    """
    calls = []
    ctor_log = []
    pool = DeepTranslatorPool(
        clock=clock,
        sleeper=sleeper,
        translator_factory=make_factory(script or [], calls, ctor_log),
        backoff_jitter=0.0,
        workers=1,
        **kwargs,
    )
    return pool, calls, ctor_log


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def sleeper(clock):
    return FakeSleeper(clock)