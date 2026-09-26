"""Tests for the persistent LLM response cache (Phase E budget layer)."""

from __future__ import annotations

import asyncio

import pytest

from src.ai.cache import CachingAIClient, ResponseCache, prompt_key
from src.models import AIConfig, AIProvider


class FakeInner:
    """Counts calls; returns a canned reply."""

    def __init__(self, reply: str = '{"claims": []}', delay: float = 0.0):
        self.reply = reply
        self.delay = delay
        self.calls = 0
        self.config = AIConfig(
            provider=AIProvider.AGNES,
            model="agnes-2.5-flash",
            api_key_env="AGNES_API_KEY",
        )

    async def complete(self, system, user, temperature=None, max_tokens=None):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply


@pytest.fixture()
def cache(tmp_path):
    c = ResponseCache(tmp_path / "llm_cache.db")
    yield c
    c.close()


def test_prompt_key_varies_with_every_component() -> None:
    base = prompt_key("agnes", "m", "s", "u", 0.3, 4096)
    assert base == prompt_key("agnes", "m", "s", "u", 0.3, 4096)
    assert base != prompt_key("openai", "m", "s", "u", 0.3, 4096)
    assert base != prompt_key("agnes", "m2", "s", "u", 0.3, 4096)
    assert base != prompt_key("agnes", "m", "s2", "u", 0.3, 4096)
    assert base != prompt_key("agnes", "m", "s", "u2", 0.3, 4096)
    assert base != prompt_key("agnes", "m", "s", "u", 0.4, 4096)
    assert base != prompt_key("agnes", "m", "s", "u", 0.3, 1024)


def test_second_identical_call_is_served_from_cache(cache) -> None:
    inner = FakeInner('{"ok": true}')
    client = CachingAIClient(inner, cache)
    asyncio.run(client.complete("sys", "user"))
    asyncio.run(client.complete("sys", "user"))
    assert inner.calls == 1
    assert (client.hits, client.misses) == (1, 1)


def test_cache_persists_across_client_instances(cache) -> None:
    inner = FakeInner("answer")
    asyncio.run(CachingAIClient(inner, cache).complete("s", "u"))
    inner2 = FakeInner("should not be used")
    out = asyncio.run(CachingAIClient(inner2, cache).complete("s", "u"))
    assert out == "answer"
    assert inner2.calls == 0


def test_empty_response_is_not_cached(cache) -> None:
    inner = FakeInner("   ")
    client = CachingAIClient(inner, cache)
    asyncio.run(client.complete("s", "u"))
    asyncio.run(client.complete("s", "u"))
    assert inner.calls == 2
    assert client.hits == 0


def test_concurrent_misses_collapse_to_one_upstream_call(cache) -> None:
    # two coroutines with the same prompt must not double-bill the API
    inner = FakeInner("x", delay=0.02)
    client = CachingAIClient(inner, cache)

    async def both():
        return await asyncio.gather(
            client.complete("s", "u"), client.complete("s", "u")
        )

    results = asyncio.run(both())
    assert results == ["x", "x"]
    assert inner.calls == 1


def test_throttle_spaces_upstream_calls(cache, monkeypatch) -> None:
    """Assert the spacing the client asked for, not how long the OS actually slept.

    Timing the real sleep was flaky on Windows — clock granularity is ~15ms and
    a `sleep(0.05)` under full-suite load can return under a 0.04 assertion,
    turning CI red for reasons unrelated to the code. The contract here is
    "a cache miss waits out the throttle window", which is observable directly.
    """
    from src.ai import cache as cache_module

    requested: list[float] = []

    async def fake_sleep(delay, *args, **kwargs):
        requested.append(delay)

    monkeypatch.setattr(cache_module.asyncio, "sleep", fake_sleep)

    inner = FakeInner("x")
    client = CachingAIClient(inner, cache, throttle_sec=0.05)

    async def two_prompts():
        await client.complete("s", "one")
        await client.complete("s", "two")

    asyncio.run(two_prompts())

    assert inner.calls == 2 and client.misses == 2
    assert requested == [pytest.approx(0.05, abs=0.005)]

    # a hit must not be spaced at all: the throttle is for the upstream, not us
    asyncio.run(client.complete("s", "one"))
    assert len(requested) == 1 and client.hits == 1


def test_broken_cache_degrades_to_passthrough() -> None:
    import sqlite3

    class DeadCache:
        def get(self, key):
            raise sqlite3.OperationalError("disk I/O error")

        def put(self, *a):
            raise sqlite3.OperationalError("disk I/O error")

    inner = FakeInner("still works")
    client = CachingAIClient(inner, DeadCache())
    out = asyncio.run(client.complete("s", "u"))
    assert out == "still works"
    assert inner.calls == 1


def test_cache_stats_reports_entries(cache) -> None:
    inner = FakeInner("v")
    client = CachingAIClient(inner, cache)
    asyncio.run(client.complete("s", "u"))
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["by_provider"] == {"agnes": 1}
