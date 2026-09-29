"""Per-host request throttling.

Two scrapers grew their own 429 handling (reddit, telegram) and both copied
the same bug: `int(headers["Retry-After"])` raises on the HTTP-date form the
RFC allows. This module is the single implementation.

The clock, the sleeper and the RNG are injectable. A throttle test that waits
for real is a wall-clock test, and this repo already had to remove one.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, Dict, Optional
from urllib.parse import urlsplit

import httpx

from ..models import RateLimit

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]

_DEFAULT_RETRY_AFTER = 5.0


def retry_after_seconds(
    response: httpx.Response, fallback: float = _DEFAULT_RETRY_AFTER
) -> float:
    """Parse a Retry-After header, falling back instead of raising.

    Only the delta-seconds form is honoured; an HTTP-date is treated as
    absent, because guessing a wall-clock offset is worse than a fixed delay.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return fallback
    try:
        return max(0.0, float(raw.strip()))
    except (TypeError, ValueError):
        return fallback


class Throttle:
    """Serialise requests per host and absorb 429s with one retry."""

    def __init__(
        self,
        default: Optional[RateLimit] = None,
        limits: Optional[Dict[str, RateLimit]] = None,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
        rng: Optional[random.Random] = None,
    ) -> None:
        self._default = default
        self._limits = dict(limits or {})
        self._clock = clock
        self._sleep = sleeper
        self._rng = rng if rng is not None else random.Random()
        self._next_ok: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _host(url_or_host: str) -> str:
        return urlsplit(url_or_host).hostname or url_or_host

    def limit_for(self, url_or_host: str) -> Optional[RateLimit]:
        return self._limits.get(self._host(url_or_host), self._default)

    async def acquire(self, url_or_host: str) -> None:
        """Wait until this host may be polled again."""
        limit = self.limit_for(url_or_host)
        if limit is None or limit.requests <= 0:
            return
        host = self._host(url_or_host)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = self._clock()
            scheduled = self._next_ok.get(host)
            interval = limit.per_seconds / limit.requests
            if limit.jitter:
                interval *= 1.0 + self._rng.uniform(-limit.jitter, limit.jitter)
            start = now if scheduled is None else max(now, scheduled)
            self._next_ok[host] = start + interval
            if scheduled is not None and scheduled > now:
                await self._sleep(scheduled - now)

    async def request(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs
    ) -> httpx.Response:
        """One throttled request, retried once when the host says 429."""
        await self.acquire(url)
        response = await client.request(method, url, **kwargs)
        if response.status_code != 429:
            return response
        await self._sleep(retry_after_seconds(response))
        await self.acquire(url)
        return await client.request(method, url, **kwargs)
