"""Globale Ratengrenze: Token-Bucket (Kapazität 1) + AIMD + Cooldown.

Regeln (README „Rate-Regeln"):
- 429 → Rate halbiert (Floor min_rate) + globales Cooldown, exponentiell
  verdopplend von cooldown_start bis cooldown_max; Zähler steht bis zum
  nächsten Erfolg (ein zwischengelaufener 5xx resettet nicht).
- success_window Erfolge in Folge → Rate * 1.1, gekappt bei max_rate.
- Kapazität 1 ⇒ keine Bursts über die aktuelle Rate hinaus.
"""

import threading
import time


class TokenBucket:
    """Token-Bucket mit Kapazität 1 — blockiert, bis wieder 1 Token frei ist."""

    def __init__(self, rate: float, capacity: float = 1.0, clock=time.monotonic,
                 sleeper=time.sleep):
        if rate <= 0:
            raise ValueError("rate muss > 0 sein")
        self.rate = float(rate)
        self.capacity = float(capacity)
        self.clock = clock
        self.sleeper = sleeper
        self._tokens = float(capacity)
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Nimm 1 Token; gib die insgesamt gewartete Zeit in Sekunden zurück."""
        waited = 0.0
        while True:
            with self._lock:
                now = self.clock()
                self._tokens = min(
                    self.capacity,
                    self._tokens + (now - self._last) * self.rate,
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                wait = (1.0 - self._tokens) / self.rate
            # außerhalb des Locks schlafen
            self.sleeper(wait)
            waited += wait

    def set_rate(self, rate: float) -> None:
        with self._lock:
            self.rate = float(rate)


class AdaptiveRate:
    """AIMD-Regler über dem Bucket plus globalem Cooldown."""

    def __init__(self, rate: float = 4.0, min_rate: float = 0.25,
                 max_rate: float = 5.0, cooldown_start: float = 60.0,
                 cooldown_max: float = 1800.0, success_window: int = 100,
                 clock=time.monotonic, sleeper=time.sleep):
        self.rate = float(rate)
        self.min_rate = float(min_rate)
        self.max_rate = float(max_rate)
        self.cooldown_start = float(cooldown_start)
        self.cooldown_max = float(cooldown_max)
        self.success_window = int(success_window)
        self.clock = clock
        self.sleeper = sleeper
        self.bucket = TokenBucket(rate, 1.0, clock, sleeper)
        self.cooldown_until = clock()
        self._failures = 0
        self._successes = 0
        self._lock = threading.Lock()

    def before_request(self) -> None:
        """Warte Cooldown ab und blockiere auf dem Bucket."""
        while True:
            remaining = self.cooldown_until - self.clock()
            if remaining <= 0:
                break
            self.sleeper(remaining)
        self.bucket.acquire()

    def on_rate_limit(self) -> float:
        """429: Rate halbieren, Cooldown (exponentiell) setzen. → Cooldown-Sekunden."""
        with self._lock:
            self._failures += 1
            self._successes = 0
            self.rate = max(self.min_rate, self.rate / 2.0)
            self.bucket.set_rate(self.rate)
            cooldown = min(
                self.cooldown_start * (2 ** (self._failures - 1)),
                self.cooldown_max,
            )
            self.cooldown_until = self.clock() + cooldown
            return cooldown

    def on_transient(self) -> None:
        """5xx/Netzfehler/anomal: kein Erfolg, aber kein Rate-Signal."""
        with self._lock:
            self._successes = 0

    def on_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._successes += 1
            if self._successes % self.success_window == 0:
                self.rate = min(self.max_rate, self.rate * 1.1)
                self.bucket.set_rate(self.rate)