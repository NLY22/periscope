"""`--budget` has to bound the HTTP layer, not just the browser subprocesses.

`topic.py --budget` was accepted on the default fast path and reached nothing:
`platform_lookup` passed it only to the browser-collection subprocesses, and
handed the HTTP fan-out hard-coded timeouts of 20/30/45 seconds. So a user who
asked for `--budget 5` waited for the slowest source anyway, and had no way to
tell that the flag had been ignored.

These tests replace `collector.collect` with a sleeping stub: the point is the
scheduler, and hitting thirteen live endpoints to prove it would make the suite
depend on the network.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from collectors import collector, fanout


@pytest.fixture
def slow_source(monkeypatch):
    """Every source takes `delay` seconds and returns nothing."""

    def install(delay: float):
        async def fake_collect(client, source, since=None):
            await asyncio.sleep(delay)
            return [], collector.FetchStats(source.key, source.name, source.kind)

        monkeypatch.setattr(collector, "collect", fake_collect)

    return install


def _run(budget: float, delay: float = 5.0, sources=("weibo_search", "zhihu_search")):
    return asyncio.run(fanout.collect_topic_async(
        "测试", sources=list(sources), pages=1, timeout=30, budget=budget))


def test_a_budget_cuts_the_run_short(slow_source):
    """A 1s budget must not wait 5s for a slow source."""
    slow_source(5.0)
    t0 = time.monotonic()
    rows, stat = _run(budget=1.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0, "the run ignored the budget (%0.1fs for a 1s ceiling)" % elapsed
    assert rows == []


def test_sources_that_miss_the_budget_are_named(slow_source):
    """`over_budget` is reported separately from `failed`.

    "We ran out of time" and "the endpoint refused" call for different repairs,
    so collapsing them into one list would make the note misleading.
    """
    slow_source(5.0)
    _rows, stat = _run(budget=1.0)
    assert set(stat["over_budget"]) == {"weibo_search", "zhihu_search"}
    assert stat["failed"] == [], "a timeout must not be reported as a failure"


def test_zero_budget_means_unlimited(slow_source):
    """0 is the documented "no ceiling", not "return immediately"."""
    slow_source(0.2)
    t0 = time.monotonic()
    _rows, stat = _run(budget=0.0)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.2, "budget=0 cancelled the work instead of leaving it unbounded"
    assert stat["over_budget"] == []


def test_work_that_finishes_inside_the_budget_is_kept(monkeypatch):
    """The budget must not discard sources that already answered."""

    async def fake_collect(client, source, since=None):
        await asyncio.sleep(0.05)
        return ([{"title": source.key, "url": "https://x/%s" % source.key}],
                collector.FetchStats(source.key, source.name, source.kind))

    monkeypatch.setattr(collector, "collect", fake_collect)
    rows, stat = _run(budget=10.0, delay=0)
    assert len(rows) == 2, "rows from sources that finished were dropped"
    assert stat["over_budget"] == []


def test_the_budget_is_reported_in_the_stats(slow_source):
    """A caller has to be able to tell which ceiling was applied."""
    slow_source(0.01)
    _rows, stat = _run(budget=2.5)
    assert stat["budget"] == 2.5
