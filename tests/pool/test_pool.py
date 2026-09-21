"""Tests: Pool-Orchestrierung (gemocktes HTTP, Fake-Uhr)."""

import pytest
from deep_translator.exceptions import (
    NotValidLength,
    NotValidPayload,
    TooManyRequests,
    TranslationNotFound,
)

from deep_translator.pool.errors import (
    PoolAnomalyError,
    PoolPermanentError,
    PoolTransientError,
)

from .conftest import make_pool


def test_translate_single_echo(clock, sleeper):
    pool, calls, ctor_log = make_pool(clock, sleeper, [("echo",)])
    assert pool.translate("hallo", target="de") == ">hallo"
    assert calls == ["hallo"]
    assert ctor_log == [("auto", "de")]  # frische Instanz mit richtigen Sprachen


def test_fresh_translator_instance_per_request(clock, sleeper):
    pool, calls, ctor_log = make_pool(clock, sleeper, [("echo",), ("echo",)])
    pool.translate_many(["a", "b"])
    assert len(ctor_log) == 2  # keine geteilte Instanz (URL-Params-Race)


def test_dedup_and_order_preserved(clock, sleeper):
    pool, calls, _ = make_pool(clock, sleeper, [("echo",), ("echo",)])
    out = pool.translate_many(["b", "a", "b", "a"], target="de")
    assert out == [">b", ">a", ">b", ">a"]  # order-preserving
    assert calls == ["b", "a"]  # Dedup: jeder eindeutige Text nur 1 Request


def test_empty_text_identity_without_network(clock, sleeper):
    pool, calls, ctor_log = make_pool(clock, sleeper, [])
    out = pool.translate_many(["", "   "], target="de")
    assert out == ["", "   "]
    assert calls == []
    assert ctor_log == []


def test_cache_disabled_by_default(clock, sleeper):
    pool, _, _ = make_pool(clock, sleeper, [])
    assert pool.cache is None  # Cache standardmäßig AUS


def test_cache_hit_skips_http(clock, sleeper, tmp_path):
    pool, calls, _ = make_pool(
        clock, sleeper, [("echo",)], cache_db=str(tmp_path / "c.db")
    )
    assert pool.translate_many(["x"], target="de") == [">x"]
    requests_after_first = len(calls)
    assert pool.translate_many(["x"], target="de") == [">x"]
    assert len(calls) == requests_after_first  # kein zweiter Request


def test_permanent_error_no_retry(clock, sleeper):
    script = [("raise", NotValidLength("x", 0, 5000))]
    pool, calls, _ = make_pool(clock, sleeper, script)
    with pytest.raises(PoolPermanentError):
        pool.translate("x", target="de")
    assert len(calls) == 1  # kein Retry bei permanentem Fehler


def test_rate_limit_then_success(clock, sleeper):
    script = [("raise", TooManyRequests()), ("echo",)]
    pool, calls, _ = make_pool(clock, sleeper, script)
    assert pool.translate("x", target="de") == ">x"
    assert len(calls) == 2
    assert pool.rate_ctrl.rate == pytest.approx(2.0)  # AIMD: 4 → 2
    # Cooldown kumulativ abgewartet (Backoff 2 s + Rest-Cooldown 58 s)
    assert sum(sleeper.sleeps) >= 60.0


def test_rate_limit_exhaustion(clock, sleeper):
    script = [("raise", TooManyRequests())] * 4
    pool, calls, _ = make_pool(clock, sleeper, script)
    with pytest.raises(PoolTransientError):
        pool.translate("x", target="de")
    assert len(calls) == 4  # max_retries=4 → 4 Versuche
    assert pool.rate_ctrl.rate == pytest.approx(0.25)  # 4→2→1→0.5→0.25


def test_anomaly_retry_once_then_raise(clock, sleeper):
    script = [("raise", TranslationNotFound("x")), ("raise", TranslationNotFound("x"))]
    pool, calls, _ = make_pool(clock, sleeper, script)
    with pytest.raises(PoolAnomalyError):
        pool.translate("x", target="de")
    assert len(calls) == 2  # genau 1 Retry


def test_anomaly_retry_succeeds(clock, sleeper):
    script = [("raise", TranslationNotFound("x")), ("echo",)]
    pool, calls, _ = make_pool(clock, sleeper, script)
    assert pool.translate("x", target="de") == ">x"
    assert len(calls) == 2


def test_on_error_mark(clock, sleeper):
    script = [("echo",), ("raise", NotValidPayload("bad"))]
    pool, calls, _ = make_pool(clock, sleeper, script)
    out = pool.translate_many(["ok", "bad"], target="de", on_error="mark")
    assert out == [">ok", None]
    assert "bad" in pool.errors


def test_non_string_input(clock, sleeper):
    pool, calls, _ = make_pool(clock, sleeper, [("echo",)])
    out = pool.translate_many(["ok", 42], on_error="mark")
    assert out == [">ok", None]
    assert 42 in pool.errors
    with pytest.raises(PoolPermanentError):
        pool.translate_many(["ok", 42])


def test_batch_verified_then_cached(clock, sleeper, tmp_path):
    script = [
        ("fixed", "A\nB"),  # Batch-Request (Join a\nb)
        ("fixed", "A"),  # Audit-Stichprobe 1
        ("fixed", "B"),  # Audit-Stichprobe 2
    ]
    db = str(tmp_path / "c.db")
    pool, calls, _ = make_pool(
        clock, sleeper, script, cache_db=db, allow_batch=True
    )
    out = pool.translate_many(["a", "b"], target="de")
    assert out == ["A", "B"]
    assert calls == ["a\nb", "a", "b"]  # 1 Batch + 2 Audit-Singles
    # nächster Pool, gleiche DB: alles aus Cache, kein Request
    pool2, calls2, _ = make_pool(clock, sleeper, [], cache_db=db)
    assert pool2.translate_many(["a", "b"], target="de") == ["A", "B"]
    assert calls2 == []


def test_batch_verify_fail_falls_back_to_singles(clock, sleeper):
    script = [
        ("fixed", "A B"),  # Batch: 1 Zeile statt 2 → Verifikation fail
        ("echo",),  # Fallback-Single a
        ("echo",),  # Fallback-Single b
    ]
    pool, calls, _ = make_pool(clock, sleeper, script, allow_batch=True)
    out = pool.translate_many(["a", "b"], target="de")
    assert out == [">a", ">b"]
    assert pool.batch_disabled is True
    assert len(calls) == 3


def test_batch_audit_mismatch_falls_back(clock, sleeper):
    script = [
        ("fixed", "A\nB"),  # Batch-Request
        ("fixed", "X"),  # Audit-Single für "a" ≠ Batch-Zeile "A" → fail
        ("echo",),  # Fallback-Single a
        ("echo",),  # Fallback-Single b
    ]
    pool, calls, _ = make_pool(
        clock, sleeper, script, allow_batch=True, batch_audit_samples=2
    )
    out = pool.translate_many(["a", "b"], target="de")
    assert out == [">a", ">b"]
    assert pool.batch_disabled is True
    assert len(calls) == 4


def test_batch_too_long_falls_back_without_join(clock, sleeper):
    long_a, long_b = "x" * 3000, "y" * 3000
    pool, calls, _ = make_pool(clock, sleeper, [("echo",), ("echo",)], allow_batch=True)
    out = pool.translate_many([long_a, long_b], target="de")
    assert out == [">" + long_a, ">" + long_b]
    assert calls == [long_a, long_b]  # kein 6001-Zeichen-Join übersendet


def test_batch_disabled_persists_after_failure(clock, sleeper):
    script = [
        ("fixed", "A B"),  # Verifikation fail
        ("echo",),
        ("echo",),
        ("echo",),  # nächster Aufruf: nur noch Singles
        ("echo",),
    ]
    pool, calls, _ = make_pool(clock, sleeper, script, allow_batch=True)
    pool.translate_many(["a", "b"], target="de")
    assert pool.batch_disabled is True
    pool.translate_many(["c", "d"], target="de")  # kein erneuter Batch-Versuch
    assert calls == ["a\nb", "a", "b", "c", "d"]