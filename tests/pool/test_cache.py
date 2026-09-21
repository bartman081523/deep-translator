"""Tests: sqlite-Cache."""

import sqlite3

from deep_translator.pool.cache import TranslationCache


def test_roundtrip_per_language(tmp_path):
    cache = TranslationCache(str(tmp_path / "t.db"))
    assert cache.get("a", "de", "auto") is None
    cache.put("a", "de", "auto", "A")
    assert cache.get("a", "de", "auto") == "A"
    # andere Zielsprache = anderer Eintrag
    assert cache.get("a", "en", "auto") is None


def test_unverified_rows_never_readable(tmp_path):
    db = str(tmp_path / "t.db")
    cache = TranslationCache(db)
    cache.put("a", "de", "auto", "A")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR REPLACE INTO translations"
        " (text, target, source, result, verified, created_at)"
        " VALUES ('b', 'de', 'auto', 'B', 0, 'x')"
    )
    conn.commit()
    conn.close()
    assert cache.get("b", "de", "auto") is None  # verified=0 → unsichtbar
    assert cache.get("a", "de", "auto") == "A"


def test_put_is_idempotent_per_key(tmp_path):
    cache = TranslationCache(str(tmp_path / "t.db"))
    cache.put("a", "de", "auto", "A")
    cache.put("a", "de", "auto", "A2")
    assert cache.get("a", "de", "auto") == "A2"