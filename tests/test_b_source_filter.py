"""Sources that match on words instead of phrases must be filtered by us.

sov2ex (V2EX's third-party index) is a bag-of-words search. Measured
2026-10-19: `固态电池` returned 511 hits, and the first twenty were
「油车换电车」/「MacBook 二手」/「三星手机」 - it matched `固态` and `电池`
separately. Quoting the query returns 0, so the phrase constraint has to be
applied on our side.

Measured after the filter, per topic:

    固态电池   0 kept /20 filtered      (V2EX has no such posts)
    大模型     1 kept /19 filtered
    iPhone    12 kept / 8 filtered      (genuine matches survive)
    Rust       7 kept /13 filtered

The last two lines are the important ones: the filter must not be a blunt
"drop everything", or it would be indistinguishable from removing the source.
"""

from __future__ import annotations

import pytest

from collectors import collector


def _stats():
    return collector.FetchStats("v2ex_search", "V2EX", "community")


def _rows(*titles):
    return [{"title": t, "url": "https://v2ex.com/t/%d" % i}
            for i, t in enumerate(titles)]


def test_unrelated_rows_are_dropped():
    rows = _rows("纠结很久的油车换电车", "出一台战损版 MacBook Pro",
                 "出国行三星 Galaxy S26")
    kept = collector._keep_mentioning(rows, "固态电池", _stats())
    assert kept == []


def test_a_highlight_space_does_not_cause_a_false_reject():
    """CSDN returns `固态 电池 深度报告` for the query `固态电池`.

    Comparing raw strings would reject the relevant rows and keep nothing, which
    is why the comparison strips whitespace from both sides.
    """
    rows = _rows("固态 电池 深度报告", "全 固态 电池 界面阻抗")
    kept = collector._keep_mentioning(rows, "固态电池", _stats())
    assert len(kept) == 2


def test_genuine_matches_survive():
    """Bite proof: the filter must not remove the rows it exists to keep."""
    rows = _rows("合理预测 iPhone Duo 的加价持续时间",
                 "出港版 iPhone 17 Pro Max 橙色 256G",
                 "小米手机值得买吗")
    kept = collector._keep_mentioning(rows, "iPhone", _stats())
    assert len(kept) == 2
    assert all("iPhone" in r["title"] for r in kept)


def test_an_english_phrase_matches_across_case():
    rows = _rows("A deep dive into RUST lifetimes", "unrelated post")
    kept = collector._keep_mentioning(rows, "rust", _stats())
    assert len(kept) == 1


def test_the_dropped_count_is_recorded():
    """A filtered-empty source must be distinguishable from an empty source.

    Otherwise the next investigation starts from "the endpoint returned
    nothing", which is a different, and wrong, premise.
    """
    st = _stats()
    collector._keep_mentioning(_rows("无关一", "无关二"), "固态电池", st)
    assert st.filtered == 2
    assert st.items == 0, "filtered rows must not be counted as collected"


def test_an_empty_query_disables_the_filter():
    """No query means no constraint - do not silently drop everything."""
    rows = _rows("任意标题一", "任意标题二")
    assert len(collector._keep_mentioning(rows, "", _stats())) == 2


def test_the_flag_is_declared_only_where_measured():
    """Opt-in: a source whose API already guarantees relevance keeps its rows.

    Applying a title filter everywhere would cost CSDN/知乎 rows whose title
    paraphrases the query.
    """
    by_key = {s.key: s for s in collector.CATALOG}
    assert by_key["v2ex_search"].require_query_in_title is True
    for key in ("csdn_search", "zhihu_search", "toutiao_search"):
        assert by_key[key].require_query_in_title is False, key


def test_browser_only_sources_are_skipped_by_fanout():
    """hupu_search is client-rendered; attempting it burns budget for nothing.

    Measured: 121 KB of HTML carrying the query only in `<title>`/`<meta>`, plus
    intermittent HTTP 405 that the retry logic reads as throttling and retries
    three times - about 12s of a 30s budget, for a source that cannot yield a
    row either way.
    """
    import asyncio

    from collectors import fanout

    assert any(s.key == "hupu_search" and s.needs_browser
               for s in collector.CATALOG)

    # Drive the scheduler with a stub so no network is touched.
    seen = []

    async def fake_one(cli, src, topic, pages, timeout):
        seen.append(src.key)
        return src.key, [], 0.0, ""

    original = fanout._one
    fanout._one = fake_one
    try:
        asyncio.run(fanout.collect_topic_async(
            "t", sources=["csdn_search", "hupu_search"], pages=1, timeout=5))
    finally:
        fanout._one = original

    assert "csdn_search" in seen
    assert "hupu_search" not in seen, "a browser-only source was attempted over HTTP"
