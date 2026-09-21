"""Tests: Token-Bucket + AIMD (gemockte Uhr, kein Sleep)."""

import pytest

from deep_translator.pool.ratelimit import AdaptiveRate, TokenBucket


def test_bucket_paces_at_rate(clock, sleeper):
    bucket = TokenBucket(4.0, 1.0, clock, sleeper)
    assert bucket.acquire() == 0.0  # erster Token sofort (Kapazität 1)
    waited = bucket.acquire()
    assert waited == pytest.approx(0.25)  # 1/4 req/s


def test_bucket_refills_from_elapsed_time(clock, sleeper):
    bucket = TokenBucket(4.0, 1.0, clock, sleeper)
    bucket.acquire()
    clock.advance(1.0)  # 4 Tokens würden nachfließen
    assert bucket.acquire() == 0.0


def test_on_rate_limit_halves_rate_and_doubles_cooldown(clock, sleeper):
    rate = AdaptiveRate(clock=clock, sleeper=sleeper)
    assert rate.on_rate_limit() == 60.0
    assert rate.rate == pytest.approx(2.0)
    assert rate.on_rate_limit() == 120.0
    assert rate.rate == pytest.approx(1.0)


def test_on_rate_limit_respects_floor_and_cooldown_cap(clock, sleeper):
    rate = AdaptiveRate(rate=1.0, clock=clock, sleeper=sleeper)
    for _ in range(6):
        rate.on_rate_limit()
    assert rate.rate == 0.25  # min_rate-Floor
    last_cooldown = 0.0
    for _ in range(20):
        last_cooldown = rate.on_rate_limit()
    # Cooldown exponentiell gedeckelt bei 1800 s
    assert last_cooldown == 1800.0


def test_before_request_waits_cooldown_then_paces(clock, sleeper):
    rate = AdaptiveRate(clock=clock, sleeper=sleeper)
    rate.on_rate_limit()  # Rate 2.0, Cooldown bis t=60
    rate.before_request()
    assert clock.now == pytest.approx(60.0)  # Cooldown abgewartet
    # Token verbraucht → nächster Request paces mit halbierter Rate (0.5 s)
    rate.before_request()
    assert clock.now == pytest.approx(60.5)


def test_success_window_raises_rate_to_cap(clock, sleeper):
    rate = AdaptiveRate(rate=4.0, max_rate=5.0, clock=clock, sleeper=sleeper)
    for _ in range(100):
        rate.on_success()
    assert rate.rate == pytest.approx(4.4)
    for _ in range(300):
        rate.on_success()
    assert rate.rate == 5.0  # Cap, nie über 5 req/s


def test_success_resets_failure_streak(clock, sleeper):
    rate = AdaptiveRate(clock=clock, sleeper=sleeper)
    rate.on_rate_limit()
    rate.on_success()
    assert rate.on_rate_limit() == 60.0  # wieder Start-Cooldoown, nicht 120