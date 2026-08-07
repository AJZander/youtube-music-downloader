"""Single global gate for all YouTube-touching network activity.
Everything — probes, searches, downloads — acquires this before hitting
the network, so pacing/backoff has exactly one implementation."""
import asyncio
import logging
import random
import time
from collections import deque

from ..config import settings
from ..events import bus
from ..models import ErrorClass

log = logging.getLogger("mdl.governor")

REQUEST_WEIGHT = 0.25  # searches/probes count fractionally against caps


class RateGovernor:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._history: deque[tuple[float, float]] = deque()  # (timestamp, weight)
        self._last_download_end = 0.0
        self._tracks_since_long_pause = 0
        self._backoff_level = 0
        self._pause_until = 0.0
        self._pause_reason = ""
        self._recent_flags: deque[float] = deque()  # rate-limit/bot events for breaker
        self._needs_canary = False

    # ---- public state ----
    def snapshot(self) -> dict:
        now = time.time()
        return {
            "paused_until": self._pause_until if self._pause_until > now else None,
            "pause_reason": self._pause_reason if self._pause_until > now else None,
            "backoff_level": self._backoff_level,
            "tracks_last_hour": round(self._weight_since(now - 3600), 1),
            "tracks_last_day": round(self._weight_since(now - 86400), 1),
            "needs_canary": self._needs_canary,
        }

    def _weight_since(self, cutoff: float) -> float:
        return sum(w for ts, w in self._history if ts >= cutoff)

    def _prune(self) -> None:
        cutoff = time.time() - 86400
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    # ---- acquisition ----
    async def acquire(self, weight: float = 1.0) -> None:
        """Blocks until it is polite to make the next request/download."""
        async with self._lock:
            while True:
                now = time.time()
                self._prune()
                if self._pause_until > now:
                    wait = self._pause_until - now
                    log.info("governor paused (%s) — waiting %.0fs", self._pause_reason, wait)
                    bus.publish("queue.stats", self.snapshot())
                    await asyncio.sleep(min(wait, 60))
                    continue
                if weight >= 1.0:  # full downloads honour hourly/daily caps (0 = unlimited)
                    hour_used = self._weight_since(now - 3600)
                    day_used = self._weight_since(now - 86400)
                    if settings.tracks_per_day > 0 and day_used + weight > settings.tracks_per_day:
                        self._set_pause(now + 3600, "daily cap reached")
                        continue
                    if settings.tracks_per_hour > 0 and hour_used + weight > settings.tracks_per_hour:
                        oldest_in_hour = next((ts for ts, _ in self._history if ts >= now - 3600), now)
                        self._set_pause(oldest_in_hour + 3600 + 5, "hourly cap reached")
                        continue
                    # inter-download spacing
                    gap = random.uniform(settings.inter_track_delay_min, settings.inter_track_delay_max)
                    since_last = now - self._last_download_end
                    if since_last < gap:
                        await asyncio.sleep(gap - since_last)
                    if self._tracks_since_long_pause >= settings.long_pause_every:
                        pause = random.uniform(settings.long_pause_min, settings.long_pause_max)
                        log.info("long pause: %.0fs after %d tracks", pause, self._tracks_since_long_pause)
                        self._tracks_since_long_pause = 0
                        await asyncio.sleep(pause)
                else:  # lightweight request: just a short jittered spacing
                    await asyncio.sleep(random.uniform(0.5, 2.0))
                self._history.append((time.time(), weight))
                if weight >= 1.0:
                    self._tracks_since_long_pause += 1
                return

    def mark_download_end(self) -> None:
        self._last_download_end = time.time()

    def _set_pause(self, until: float, reason: str) -> None:
        if until > self._pause_until:
            self._pause_until = until
            self._pause_reason = reason
            bus.publish("queue.stats", self.snapshot())

    # ---- feedback ----
    def report_success(self) -> None:
        self._backoff_level = 0

    def report_error(self, error_class: ErrorClass) -> None:
        if error_class not in (ErrorClass.RATE_LIMITED, ErrorClass.BOT_FLAGGED, ErrorClass.FORBIDDEN):
            return
        now = time.time()
        self._recent_flags.append(now)
        while self._recent_flags and self._recent_flags[0] < now - 1800:
            self._recent_flags.popleft()
        if len(self._recent_flags) >= 3:
            # circuit breaker: hard 6h pause, canary required before resume
            self._needs_canary = True
            self._set_pause(now + 6 * 3600, f"circuit breaker ({error_class})")
            log.warning("circuit breaker tripped: 3 flags in 30min, pausing 6h")
            return
        self._backoff_level = min(self._backoff_level + 1, 6)
        delay = min(6 * 3600, 60 * (4 ** (self._backoff_level - 1))) * random.uniform(0.5, 1.0)
        self._set_pause(now + delay, f"backoff after {error_class} (level {self._backoff_level})")
        log.warning("global backoff level %d: pausing %.0fs after %s",
                    self._backoff_level, delay, error_class)

    def clear_canary(self) -> None:
        self._needs_canary = False
        self._pause_until = 0.0
        self._recent_flags.clear()

    def resume(self) -> None:
        """Manual unpause from the API."""
        self._pause_until = 0.0
        self._pause_reason = ""
        bus.publish("queue.stats", self.snapshot())


governor = RateGovernor()
