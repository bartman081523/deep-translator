"""Fehlerklassifikation für den Pool.

Klassen (README, Tabelle „Fehlerklassifikation"):
- transient: TooManyRequests (429), RequestError (HTTP ≠ 2xx), Netzfehler
- permanent: invalider Payload / Länge / Sprache — kein Retry
- anomal: TranslationNotFound, leeres Ergebnis, unerwartete Typen — 1 Retry
"""

import requests

from deep_translator.exceptions import (
    InvalidSourceOrTargetLanguage,
    LanguageNotSupportedException,
    NotValidLength,
    NotValidPayload,
    RequestError,
    TooManyRequests,
    TranslationNotFound,
)

TRANSIENT = "transient"
PERMANENT = "permanent"
ANOMAL = "anomal"


class TranslationPoolError(Exception):
    """Basisklasse aller Pool-Fehler."""


class PoolTransientError(TranslationPoolError):
    """Transienter Fehler (429/HTTP/Netz) nach Ausschöpfen aller Retries."""


class PoolPermanentError(TranslationPoolError):
    """Permanenter Fehler (Input/Language) — kein Retry sinnvoll."""


class PoolAnomalyError(TranslationPoolError):
    """Anomales Verhalten (kein Element, leeres Ergebnis) nach Retry."""


_PERMANENT_TYPES = (
    NotValidPayload,
    NotValidLength,
    LanguageNotSupportedException,
    InvalidSourceOrTargetLanguage,
)


def classify(exc: Exception) -> str:
    """Ordne eine Library-Exception einer Klasse zu (PERMANENT zuerst)."""
    if isinstance(exc, _PERMANENT_TYPES):
        return PERMANENT
    if isinstance(exc, (TooManyRequests, RequestError)):
        return TRANSIENT
    if isinstance(exc, requests.exceptions.RequestException):
        return TRANSIENT
    return ANOMAL