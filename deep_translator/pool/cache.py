"""sqlite-Cache: nur verifizierte Ergebnisse (verified=1) werden gelesen."""

import datetime
import sqlite3
import threading

_SCHEMA = """
CREATE TABLE IF NOT EXISTS translations (
  text TEXT NOT NULL,
  target TEXT NOT NULL,
  source TEXT NOT NULL,
  result TEXT NOT NULL,
  verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT,
  PRIMARY KEY (text, target, source)
)
"""


class TranslationCache:
    """Thread-sicherer sqlite-Cache für Übersetzungen."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def get(self, text: str, target: str, source: str):
        """Gib das verifizierte Ergebnis zurück oder None (kein Treffer)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT result FROM translations"
                " WHERE text=? AND target=? AND source=? AND verified=1",
                (text, target, source),
            ).fetchone()
        return row[0] if row else None

    def put(self, text: str, target: str, source: str, result: str) -> None:
        """Nur für verifizierte Ergebnisse aufrufen (verified=1 fix)."""
        created = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO translations"
                " (text, target, source, result, verified, created_at)"
                " VALUES (?, ?, ?, ?, 1, ?)",
                (text, target, source, result, created),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()