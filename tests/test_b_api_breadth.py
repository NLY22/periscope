"""Breadth of the API layer, and the shape rules that make it usable.

The measurement that motivated this file (2026-10-19, topic 固态电池):

    one query,  count=50   ->  50 results, 1.2s, 99% with body
    seven phrasings        -> 266 results, 5.4s, 99% with body, 92 publishers

The API caps a single query at 50 - a hard interface limit - so asking once caps
this layer at 50 no matter how good the ranking is. Those 50 are one rerank view
of the topic, not the topic: the 51st result is just as relevant and simply
ranked lower under that view.

These tests pin the *contract* of the widening, not its exact counts: a live
number would make the suite depend on the network and on the topic's popularity.
"""

from __future__ import annotations

import inspect

import pytest

from core import dsapi


# ------------------------------------------------------------ variant shaping
def test_depth_one_is_exactly_the_old_behaviour():
    """`--api-depth 1` must be a true single query, not a renamed default."""
    assert dsapi.topic_variants("固态电池", depth=1) == ["固态电池"]


def test_variants_grow_as_a_prefix():
    """A smaller depth must be a prefix of a larger one.

    That is what makes the cost predictable and means a run cut short by a
    budget still did the most valuable queries.
    """
    for depth in range(1, 9):
        small = dsapi.topic_variants("固态电池", depth=depth)
        large = dsapi.topic_variants("固态电池", depth=depth + 1)
        assert small == large[:len(small)], (depth, small, large)


def test_the_first_variant_is_always_the_plain_topic():
    """Site-limited and suffixed variants must supplement, never replace.

    If the baseline query were dropped, a topic that only exists under its own
    exact wording would return nothing.
    """
    for depth in range(1, 9):
        assert dsapi.topic_variants("固态电池", depth=depth)[0] == "固态电池"


def test_every_variant_contains_the_topic():
    for q in dsapi.topic_variants("固态电池", depth=9):
        assert "固态电池" in q, q


def test_variants_are_unique():
    """Paying for the same query twice is pure waste against a 5 QPS limit."""
    for depth in range(1, 9):
        vs = dsapi.topic_variants("固态电池", depth=depth)
        assert len(vs) == len(set(v.lower() for v in vs)), vs


def test_the_year_comes_from_the_caller():
    """Passed in so a test does not silently change meaning in January."""
    assert any("2031" in q for q in dsapi.topic_variants("X", depth=9, year="2031"))


def test_an_empty_topic_produces_no_queries():
    assert dsapi.topic_variants("", depth=5) == []
    assert dsapi.topic_variants("   ", depth=5) == []


def test_depth_is_clamped_to_at_least_one():
    """A 0 or negative depth must not produce an empty query list.

    `topic.py` uses 0 to mean "use the tier default" and resolves it before
    calling, but a direct caller passing 0 would otherwise get a silent no-op.
    """
    assert dsapi.topic_variants("固态电池", depth=0) == ["固态电池"]
    assert dsapi.topic_variants("固态电池", depth=-3) == ["固态电池"]


# --------------------------------------------------------------- merge contract
def test_wide_search_merges_by_url_and_keeps_the_first(monkeypatch):
    """Duplicate URLs across phrasings collapse; the baseline hit wins.

    The baseline is first in the variant list, so "first wins" means the caller
    sees the plain query's authority/rerank for a URL that several phrasings
    found. A `site:` hit for the same URL carries no better metadata.
    """

    async def fake_multi(queries, count=20, need_content=True):
        # Every phrasing returns the same URL, plus one unique to that phrasing.
        return [
            dsapi.SearchResponse(results=[
                dsapi.Result(title="共用标题", url="https://x/shared",
                             authority=4, rerank_score=2.0),
                dsapi.Result(title="独特-" + q, url="https://x/" + str(i),
                             authority=3, rerank_score=1.5),
            ])
            for i, q in enumerate(queries)
        ]

    monkeypatch.setattr(dsapi, "search_multi", fake_multi)
    monkeypatch.setattr(dsapi, "topic_variants", lambda t, d, year="": ["a", "b", "c"])

    import asyncio

    resp = asyncio.run(dsapi.search_wide("t", depth=3))
    urls = [r.url for r in resp.results]
    assert len(urls) == len(set(urls)), "a duplicate URL survived the merge"
    assert urls.count("https://x/shared") == 1
    assert len(urls) == 4, urls     # 3 unique + 1 shared
    shared = next(r for r in resp.results if r.url == "https://x/shared")
    assert shared.authority == 4, "the baseline metadata was overwritten"


def test_one_failing_phrasing_does_not_lose_the_others(monkeypatch):
    """A `site:` query can fail alone; the rest of the union must survive."""

    async def fake_multi(queries, count=20, need_content=True):
        out = []
        for i, q in enumerate(queries):
            if i == 0:
                out.append(dsapi.SearchResponse(error="HTTP 500"))
                continue
            out.append(dsapi.SearchResponse(results=[
                dsapi.Result(title=q, url="https://x/" + str(i))]))
        return out

    monkeypatch.setattr(dsapi, "search_multi", fake_multi)
    monkeypatch.setattr(dsapi, "topic_variants", lambda t, d, year="": ["a", "b", "c"])

    import asyncio

    resp = asyncio.run(dsapi.search_wide("t", depth=3))
    assert resp.ok, "one failed phrasing made the whole search look failed"
    assert len(resp.results) == 2


def test_all_phrasings_failing_is_an_error_not_an_empty_success(monkeypatch):
    """An empty success and a failed request need different handling.

    Collapsing them is how "the network is down" becomes "there is no such
    topic" - and the caller has no way to tell.
    """

    async def fake_multi(queries, count=20, need_content=True):
        return [dsapi.SearchResponse(error="HTTP 503") for _ in queries]

    monkeypatch.setattr(dsapi, "search_multi", fake_multi)
    monkeypatch.setattr(dsapi, "topic_variants", lambda t, d, year="": ["a", "b"])

    import asyncio

    resp = asyncio.run(dsapi.search_wide("t", depth=2))
    assert not resp.ok, "an all-failed search reported success"
    assert "503" in resp.error


def test_an_empty_result_set_is_not_an_error(monkeypatch):
    """Bite proof: a genuinely empty union must stay a success."""

    async def fake_multi(queries, count=20, need_content=True):
        return [dsapi.SearchResponse(results=[]) for _ in queries]

    monkeypatch.setattr(dsapi, "search_multi", fake_multi)
    monkeypatch.setattr(dsapi, "topic_variants", lambda t, d, year="": ["a"])

    import asyncio

    resp = asyncio.run(dsapi.search_wide("t", depth=1))
    assert resp.ok
    assert resp.results == []


# ------------------------------------------------------------- wired into CLI
def test_the_default_tiers_are_wider_than_one_query():
    """The whole point: the default path must not still ask once.

    `--api-depth 1` exists for the old behaviour, so the tier table being 1
    everywhere would make this module dead code that passes its own tests.
    """
    import topic

    assert topic.API_DEPTH[topic.SPEED_FAST] >= 3
    assert topic.API_DEPTH[topic.SPEED_NORMAL] >= topic.API_DEPTH[topic.SPEED_FAST]
    assert topic.API_DEPTH[topic.SPEED_DEEP] >= topic.API_DEPTH[topic.SPEED_NORMAL]
    # Turning off platform collection frees time; it must not narrow the API.
    assert topic.API_DEPTH["off"] >= 3


def test_api_lookup_uses_the_depth_it_is_given(monkeypatch):
    import topic

    calls = {}

    def fake_single(t, count=40):
        calls["single"] = (t, count)
        return dsapi.SearchResponse(results=[])

    def fake_wide(t, count=50, depth=3):
        calls["wide"] = (t, count, depth)
        return dsapi.SearchResponse(results=[])

    monkeypatch.setattr(dsapi, "search_topic", fake_single)
    monkeypatch.setattr(dsapi, "search_topic_wide", fake_wide)

    topic.api_lookup("t", 50, 1)
    assert "single" in calls and "wide" not in calls

    calls.clear()
    topic.api_lookup("t", 50, 5)
    assert calls.get("wide") == ("t", 50, 5)
