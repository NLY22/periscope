"""Platform rows must carry their own family's credibility, not one constant.

`topic.py` used to stamp `trust=0.62` on every HTTP platform row and `0.58` on
every browser row, whatever the platform. The project already carries a
per-family table (`src.corpus.trust.SOURCE_PRIORS`) that the ingest path uses,
so the live query and the corpus it reads back were scoring on different scales.

The flat number was not neutral - it was flattering. Measured against the table:

    bilibili  prior 0.35   flat 0.62   +0.27
    reddit    prior 0.40   flat 0.62   +0.22   (虎扑, 什么值得买, 豆瓣)
    v2ex      prior 0.45   flat 0.62   +0.17   (知乎, CSDN, 掘金, V2EX, 百度知道)
    rss       prior 0.70   flat 0.62   -0.08

with the freshness bonus on top, a same-day 虎扑 reply scored 0.74 and beat
人民网 at authority 4 (0.667). That inversion is what the trust model exists to
prevent, and seven of the thirteen topic sources were on the inflated side.
"""

from __future__ import annotations

import pytest

import topic
from collectors import collector, fanout
from core import merge
from src.corpus.trust import SOURCE_PRIORS
from src.models import SourceType


def test_each_platform_resolves_to_its_family_prior():
    """Not one number: the table's number for that source's family."""
    cases = {
        "bili_search": SourceType.BILIBILI,
        "hupu_search": SourceType.REDDIT,
        "zhihu_search": SourceType.V2EX,
        "csdn_search": SourceType.V2EX,
        "weibo_search": SourceType.TWITTER,
        "youtube_cn": SourceType.YOUTUBE,
        "wechat_search": SourceType.RSS,
    }
    for key, family in cases.items():
        expected = SOURCE_PRIORS[family.name.lower()]
        assert topic._platform_trust(key) == pytest.approx(expected), (
            "%s should score as %s (%.2f)" % (key, family.name, expected))


def test_the_platforms_differ_from_each_other():
    """Bite proof: a flat constant would make every one of these equal."""
    values = {topic._platform_trust(k) for k in fanout.TOPIC_SOURCES}
    assert len(values) > 3, (
        "platform rows are back to a single credibility value: %s" % values)


def test_an_unknown_source_falls_back_to_the_community_default():
    assert topic._platform_trust("no_such_source") == pytest.approx(0.5)


def test_every_topic_source_is_mapped():
    """A source with no family would silently take the 0.5 fallback."""
    unmapped = [k for k in fanout.TOPIC_SOURCES
                if topic._platform_trust(k) == pytest.approx(0.5)]
    assert not unmapped, "topic sources with no credibility family: %s" % unmapped


def test_a_fresh_forum_reply_no_longer_outranks_a_major_paper():
    """The inversion, stated as a ranking property rather than a number.

    Same day, so the forum row gets the full freshness bonus - the case where
    the old flat value did its damage.
    """
    forum = {"trust": topic._platform_trust("hupu_search"),
             "_origin": "platform", "date": "2026-10-19"}
    major = {"authority": 4, "rerank": 1.0, "_origin": "api"}
    assert merge.score_of(major) > merge.score_of(forum), (
        "a same-day forum reply still outscores an authority-4 article")


def test_the_mapping_matches_the_ingest_path():
    """A topic run and an ingest must agree about what a source is worth.

    Both resolve through `tools/ingest_all`'s tables; if one of them grows a
    private mapping the two scales drift apart again.
    """
    from tools.ingest_all import KIND_TO_SOURCE, SOURCE_KEY_TO_SOURCE

    by_key = {s.key: s for s in collector.CATALOG}
    for key in fanout.TOPIC_SOURCES:
        src = by_key[key]
        expected = (SOURCE_KEY_TO_SOURCE.get(key)
                    or KIND_TO_SOURCE.get(src.kind))
        assert expected is not None, (
            "%s has no SourceType in the ingest tables, so the topic query and "
            "the ingest disagree" % key)


def test_the_boundary_still_holds():
    """The lookup moved to `collectors/` so `topic.py` need not import `src`.

    `tests/test_b_import_boundary.py` forbids `topic.py` from reaching into A at
    all. The obvious fix - import `src.corpus.trust` where the number is needed -
    would have meant weakening that rule for one call, so the lookup lives where
    the boundary already permits it.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "topic.py").read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src"):
            bad.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src"):
                    bad.append(alias.name)
    assert not bad, "topic.py imports A again: %s" % bad

    # And the number still comes from the project's table.
    from src.corpus.trust import SOURCE_PRIORS
    assert topic._platform_trust("bili_search") == pytest.approx(
        SOURCE_PRIORS["bilibili"])
