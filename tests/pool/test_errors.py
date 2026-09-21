"""Tests: Fehlerklassifikation."""

import requests

from deep_translator.exceptions import (
    LanguageNotSupportedException,
    NotValidLength,
    NotValidPayload,
    RequestError,
    TooManyRequests,
    TranslationNotFound,
)
from deep_translator.pool.errors import ANOMAL, PERMANENT, TRANSIENT, classify


def test_transient():
    assert classify(TooManyRequests()) == TRANSIENT
    assert classify(RequestError(500)) == TRANSIENT
    assert classify(RequestError(503)) == TRANSIENT
    assert classify(requests.exceptions.ConnectionError()) == TRANSIENT
    assert classify(requests.exceptions.Timeout()) == TRANSIENT


def test_permanent():
    assert classify(NotValidLength("x", 0, 5000)) == PERMANENT
    assert classify(NotValidPayload(42)) == PERMANENT
    assert classify(LanguageNotSupportedException("xx")) == PERMANENT


def test_anomal():
    assert classify(TranslationNotFound("x")) == ANOMAL
    assert classify(ValueError("x")) == ANOMAL
    assert classify(TypeError("x")) == ANOMAL  # z. B. ungefixter Fork-Bug


def test_permanent_wins_over_transient():
    # Reihenfolge: PERMANENT-Check zuerst
    assert classify(NotValidPayload(TooManyRequests())) == PERMANENT