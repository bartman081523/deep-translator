"""Integrationstests über die echte GoogleTranslator-Pipeline.

Das HTTP-Layer ist gemockt (requests.get ersetzt); Parsing, Exception-
Klassen und die Fork-Fixes (a82d1fb: RequestError(status_code)) laufen echt.
Der Live-Endpunkt antwortet derzeit 302 → google.com/sorry → final 429;
requests folgt Redirects, sodass google.py den finalen Status sieht —
genau dieser Pfad (200 / 429 / 500) wird hier simuliert. Kein Netz.
"""

import pytest
import requests

from deep_translator import GoogleTranslator
from deep_translator.exceptions import RequestError, TooManyRequests

from deep_translator.pool import DeepTranslatorPool
from deep_translator.pool.errors import PoolTransientError
from deep_translator.pool.pool import _default_translator_factory

from .conftest import FakeClock, FakeSleeper

HTML_OK = (
    "<html><body>"
    '<div class="result-container">Hallo Welt</div>'
    "</body></html>"
)


class FakeResponse:
    """Minimale requests.Response-Ersatz (google.py braucht status_code/text/close)."""

    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.closed = False

    def close(self):
        self.closed = True


def make_live_pool(monkeypatch, responses, **kwargs):
    """Pool mit echter Factory + gemocktem requests.get.

    Rückgabe: (pool, calls, sleeper) — calls = (url, params) je Request.
    """
    calls = []
    seq = iter(responses)

    def fake_get(url, params=None, proxies=None):
        calls.append((url, dict(params)))
        try:
            return next(seq)
        except StopIteration:
            return responses[-1]

    monkeypatch.setattr(requests, "get", fake_get)
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    pool = DeepTranslatorPool(
        clock=clock, sleeper=sleeper, backoff_jitter=0.0, workers=1, **kwargs
    )
    return pool, calls, sleeper


def test_factory_builds_real_google_translator():
    translator = _default_translator_factory("auto", "de")
    assert isinstance(translator, GoogleTranslator)
    assert translator._source == "auto"
    assert translator._target == "de"


def test_translate_end_to_end_through_real_library(monkeypatch):
    pool, calls, _ = make_live_pool(monkeypatch, [FakeResponse(200, HTML_OK)])
    assert pool.translate("Hello world", target="de") == "Hallo Welt"
    url, params = calls[0]
    assert url == "https://translate.google.com/m"
    assert params["q"] == "Hello world"
    assert params["tl"] == "de"
    assert params["sl"] == "auto"


def test_translate_many_dedup_through_real_library(monkeypatch):
    pool, calls, _ = make_live_pool(monkeypatch, [FakeResponse(200, HTML_OK)])
    out = pool.translate_many(["Hello world", "Hello world"], target="de")
    assert out == ["Hallo Welt", "Hallo Welt"]
    assert len(calls) == 1  # Dedup vor dem HTTP-Request


def test_429_raises_real_toomanyrequests_and_halves_rate(monkeypatch):
    pool, calls, _ = make_live_pool(
        monkeypatch,
        [FakeResponse(429)],
        rate=2.0,
        max_retries=2,
        cooldown_max=90.0,
    )
    with pytest.raises(PoolTransientError) as exc_info:
        pool.translate("Hello", target="de")
    assert isinstance(exc_info.value.__cause__, TooManyRequests)
    assert len(calls) == 2  # max_retries=2 → 2 Versuche
    assert pool.rate_ctrl.rate == pytest.approx(0.5)  # AIMD: 2.0 → 1.0 → 0.5


def test_http_500_is_transient_without_rate_halving(monkeypatch):
    pool, calls, _ = make_live_pool(
        monkeypatch, [FakeResponse(500)], rate=2.0, max_retries=2
    )
    with pytest.raises(PoolTransientError) as exc_info:
        pool.translate("Hello", target="de")
    # Fork-Fix a82d1fb: RequestError(status_code) statt TypeError
    assert isinstance(exc_info.value.__cause__, RequestError)
    assert not isinstance(exc_info.value.__cause__, TypeError)
    assert pool.rate_ctrl.rate == pytest.approx(2.0)  # kein Rate-Signal
    assert len(calls) == 2