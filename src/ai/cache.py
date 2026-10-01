"""Caching + throttling wrapper around any AIClient (Periscope Phase E).

The Agnes free tier is slow and rate-limited, and the same prompts recur
constantly: a re-run after a crash re-asks identical extraction prompts, a
follow-up turn re-answers with largely the same evidence. Every upstream
call is therefore gated behind a persistent (system, user, params) cache,
and only genuine cache misses hit the network — spaced by `throttle_sec`.

Deliberately a decorator over the AIClient protocol (not a base-class
feature): Horizon's analyzer/enricher/summarizer all speak `complete()`,
so wrapping once at the construction site covers every caller, and tests
keep injecting fakes directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional

from .client import AIClient

logger = logging.getLogger(__name__)

# The cache is an accelerator, never a correctness source: on any SQLite
# trouble we degrade to pass-through instead of failing the pipeline.
_MAX_ROWS = 5000


class ResponseCache:
    """Tiny SQLite (prompt_hash -> response) store, shared across runs."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_cache (
                   key TEXT PRIMARY KEY,
                   provider TEXT NOT NULL,
                   model TEXT NOT NULL,
                   created_at REAL NOT NULL,
                   response TEXT NOT NULL
               )"""
        )
        self._conn.commit()

    def get(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT response FROM llm_cache WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def put(self, key: str, provider: str, model: str, response: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO llm_cache (key, provider, model, created_at, response)"
            " VALUES (?, ?, ?, ?, ?)",
            (key, provider, model, time.time(), response),
        )
        self._conn.commit()
        self._trim()

    def _trim(self) -> None:
        count = self._conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
        if count > _MAX_ROWS:
            self._conn.execute(
                """DELETE FROM llm_cache WHERE key NOT IN (
                       SELECT key FROM llm_cache ORDER BY created_at DESC LIMIT ?)""",
                (_MAX_ROWS // 2,),
            )
            self._conn.commit()

    def stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT provider, COUNT(*) FROM llm_cache GROUP BY provider"
        ).fetchall()
        return {
            "entries": self._conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0],
            "by_provider": dict(rows),
        }

    def close(self) -> None:
        self._conn.close()


def prompt_key(
    provider: str, model: str, system: str, user: str, temperature, max_tokens
) -> str:
    raw = "\0".join(
        str(x) for x in (provider, model, temperature, max_tokens, system, user)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CachingAIClient(AIClient):
    """AIClient decorator: persistent response cache + minimum call spacing.

    Exposes `.config` from the wrapped client so analyzer throttle/
    concurrency introspection keeps working unchanged.
    """

    def __init__(
        self,
        inner: AIClient,
        cache: ResponseCache,
        throttle_sec: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        """`clock` is injectable so the spacing can be asserted exactly.

        Reading the wall clock here and then asserting the resulting delay made
        this test flaky on Windows: ~15ms clock granularity turned a 0.05s
        window into a 0.035s request under full-suite load.
        """
        self.inner = inner
        self.cache = cache
        self.config = getattr(inner, "config", None)
        self.throttle_sec = max(throttle_sec, 0.0)
        self._clock = clock
        self.hits = 0
        self.misses = 0
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    @property
    def provider_name(self) -> str:
        provider = getattr(self.config, "provider", None)
        return getattr(provider, "value", None) or str(provider or "unknown")

    @property
    def model_name(self) -> str:
        return str(getattr(self.config, "model", "") or "")

    async def complete(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        key = prompt_key(
            self.provider_name, self.model_name, system, user, temperature, max_tokens
        )
        try:
            cached = self.cache.get(key)
        except Exception as exc:  # poisoned DB -> pass-through
            logger.warning("llm cache read failed: %s", exc)
            cached = None
        if cached is not None:
            self.hits += 1
            return cached

        # Serialize upstream calls and space them: on a rate-limited free
        # tier, a burst of concurrent misses is the fastest way to 429.
        async with self._lock:
            # double-check: a sibling coroutine may have fetched this exact
            # prompt (or an in-process clone of it) while we queued on the lock
            try:
                cached = self.cache.get(key)
            except Exception:
                cached = None
            if cached is not None:
                self.hits += 1
                return cached
            if self.throttle_sec > 0:
                wait = self._last_call + self.throttle_sec - self._clock()
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_call = self._clock()
            response = await self.inner.complete(
                system, user, temperature=temperature, max_tokens=max_tokens
            )
        if response and response.strip():
            try:
                self.cache.put(key, self.provider_name, self.model_name, response)
            except Exception as exc:
                logger.warning("llm cache write failed: %s", exc)
        self.misses += 1
        return response
