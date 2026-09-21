"""deep_translator.pool — datensparsamer Pool um GoogleTranslator.

Globale Rate (Token-Bucket + AIMD + Cooldown), Fehlerklassifikation, Retry,
Dedup, optionaler sqlite-Cache (Standard: AUS) und verifiziertes Batchen.

Nutzung:
    from deep_translator.pool import DeepTranslatorPool
    pool = DeepTranslatorPool()
    pool.translate_many(texts, target="de", on_error="raise")
"""

from deep_translator.pool.cache import TranslationCache
from deep_translator.pool.errors import (
    PoolAnomalyError,
    PoolPermanentError,
    PoolTransientError,
    TranslationPoolError,
    classify,
)
from deep_translator.pool.pool import DeepTranslatorPool

__all__ = [
    "DeepTranslatorPool",
    "TranslationCache",
    "TranslationPoolError",
    "PoolTransientError",
    "PoolPermanentError",
    "PoolAnomalyError",
    "classify",
]