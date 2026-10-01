"""P1: explainable trust, publisher-based independence, noisy-OR aggregation.

The property these tests defend is the one widening sources actually threatens:
a score with no ceiling can be flooded. `test_a_flood_of_low_trust_items_cannot_
pass` is the reason noisy-OR replaced the sum, and it would fail against one.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.analysis.claims import Claim, ClaimAnalyzer, ClaimStore
from src.corpus.store import Corpus
from src.corpus.trust import (
    Thresholds,
    Vote,
    classify,
    compute_features,
    distinct_publishers,
    entity_checkable,
    freshness,
    jaccard,
    noisy_or,
    provenance_factor,
    publisher_of,
    roc_thresholds,
    shingles,
    template_score,
    trust_score,
)
from src.models import ContentItem, SourceType

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def make_item(corpus: Corpus, idx: str, *, source: SourceType = SourceType.RSS,
              author: str = "author-a", content: str = "body", trust: bool = True):
    item = ContentItem(
        id=f"p1:{idx}",
        source_type=source,
        title=f"title {idx}",
        url=f"https://e.com/{idx}",
        content=content,
        author=author,
        published_at=NOW,
        fetched_at=NOW,
    )
    corpus.add_items([item])
    return item


# ------------------------------------------------------------------- features
def test_prior_and_numbers_lift_trust_vagueness_lowers_it() -> None:
    solid = compute_features(
        source_type="rss",
        text="DeepSeek-V4 于 2026-09 发布，支持 1,000,000 token，定价 2 元/百万。",
        author="org",
        cluster_size=3,
    )
    vague = compute_features(
        source_type="twitter", text="很多人觉得不错", author=None, cluster_size=0
    )
    assert trust_score(solid) > trust_score(vague)
    assert solid.prior > vague.prior
    assert solid.entity_checkable > vague.entity_checkable


def test_legacy_marker_provenance_is_discounted_not_zeroed() -> None:
    author = compute_features(source_type="rss", text="same text here", provenance="author")
    legacy = compute_features(source_type="rss", text="same text here",
                             provenance="legacy_marker")
    assert provenance_factor("legacy_marker") == 0.8
    assert trust_score(legacy) < trust_score(author)
    assert trust_score(legacy) > 0.0            # discounted, not discarded


def test_ocr_uses_its_own_confidence() -> None:
    assert provenance_factor("ocr", 0.9) == 0.9
    assert provenance_factor("ocr", 0.3) == 0.3
    assert provenance_factor("ocr") == 0.5      # unknown confidence is neutral


def test_template_repost_scores_higher_than_a_unique_text() -> None:
    blurb = "限时免费领取内部资料点击链接查看完整版限时免费领取内部资料点击"
    peers = [blurb, blurb + "后缀一点点"]
    assert template_score(blurb, peers) > 0.8
    assert template_score(blurb, []) == 0.0
    unique = compute_features(source_type="rss", text=blurb, peers=peers)
    plain = compute_features(source_type="rss", text=blurb)
    assert unique.template > plain.template
    assert trust_score(unique) < trust_score(plain)


def test_jaccard_and_shingles_behave_on_short_and_empty_text() -> None:
    assert shingles("") == []
    assert shingles("abcd") == ["abcd"]
    assert jaccard([], ["a"]) == 0.0
    assert jaccard(list("abcde"), list("abcde")) == 1.0


def test_freshness_halves_then_treats_unknown_as_neutral() -> None:
    assert freshness(None) == 0.5
    assert freshness(NOW, now=NOW) == pytest.approx(1.0)
    two_half_lives = freshness(NOW - timedelta(days=180), now=NOW, half_life_days=90)
    assert two_half_lives == pytest.approx(0.25, abs=1e-9)


def test_publisher_resolution_falls_back_then_refuses() -> None:
    assert publisher_of("DeepSeekAI") == "deepseekai"
    assert publisher_of(None, "https://x.com/u/alice/status/9") == "alice"
    assert publisher_of(None, "@carol/123") == "carol"
    assert publisher_of(None, "https://example.com/story") is None


# ------------------------------------------------------------------ voting
def _votes(*pairs) -> list:
    return [Vote(source_type=s, publisher=p, trust=t) for s, p, t in pairs]


def test_identity_is_a_source_type_and_publisher_pair() -> None:
    """spec §5.2.2: a vote is identified by (source_type, publisher).

    The same author appearing under two source types counts twice — that is the
    spec's rule, and it is a known soft spot (one person with a newsletter and
    an HN account reads as two families). The guard against the *content* being
    repeated is cluster collapse in `recompute_independence`, which runs first;
    a repeat inside one (type, publisher) pair still counts once.
    """
    assert distinct_publishers(_votes(
        ("rss", "a", 0.9), ("hackernews", "a", 0.9)
    )) == 2
    # same (type, publisher) twice is one vote, whatever the trust
    assert distinct_publishers(_votes(
        ("rss", "a", 0.9), ("rss", "a", 0.95)
    )) == 1
    # and a repeated publisher cannot stack the aggregate
    assert noisy_or(_votes(("rss", "a", 0.4), ("rss", "a", 0.4))) < noisy_or(
        _votes(("rss", "a", 0.4), ("rss", "b", 0.4))
    )


def test_two_publishers_clear_the_gate_only_when_trust_aggregates_high() -> None:
    votes = _votes(("rss", "a", 0.9), ("hackernews", "b", 0.9))
    assert distinct_publishers(votes) == 2
    assert classify(noisy_or(votes), votes) == "supported"


def test_a_flood_of_low_trust_items_cannot_pass() -> None:
    """The concrete reason T is a noisy-OR rather than a sum."""
    weak = _votes(*[("twitter", f"user{i}", 0.2) for i in range(40)])
    T = noisy_or(weak)
    assert T < 1.0
    assert T < 0.999
    assert classify(T, weak, Thresholds(supported=0.99)) == "unsupported"


def test_anonymous_items_cast_no_vote_at_all() -> None:
    votes = _votes(("twitter", None, 0.99), ("twitter", None, 0.99))
    assert distinct_publishers(votes) == 0
    assert noisy_or(votes) == 0.0
    assert classify(0.99, votes) == "unsupported"


def test_single_forum_deep_corroboration_can_still_be_supported() -> None:
    """Otherwise "only one forum said it" is permanently un-supportable,
    which would contradict the point of the fork."""
    votes = _votes(*[("discourse", f"user{i}", 0.7) for i in range(4)])
    assert distinct_publishers(votes) == 4
    assert classify(noisy_or(votes), votes) == "supported"
    # the same shape under a source whose prior is below the floor is not
    # the same shape under a source whose prior is below the 0.40 floor is not
    thin = [Vote("bilibili", f"u{i}", 0.7) for i in range(4)]
    assert classify(noisy_or(thin), thin) == "unsupported"


def test_a_recorded_contradiction_forces_contested() -> None:
    votes = _votes(("rss", "a", 0.9), ("hackernews", "b", 0.9))
    assert classify(noisy_or(votes), votes, contradicted=True) == "contested"


def test_roc_refuses_to_invent_thresholds_without_two_classes() -> None:
    assert roc_thresholds([]) is None
    assert roc_thresholds([(0.9, True), (0.8, True)]) is None
    fitted = roc_thresholds([(0.9, True), (0.85, True), (0.2, False), (0.1, False)])
    assert fitted is not None
    assert 0.0 < fitted.triage <= fitted.supported < 1.0


# ------------------------------------------------------- store + claim wiring
@pytest.fixture()
def corpus(tmp_path) -> Corpus:
    c = Corpus(tmp_path / "corpus.db")
    yield c
    c.close()


def test_store_persists_trust_features_for_audit(corpus: Corpus) -> None:
    make_item(corpus, "1", source=SourceType.RSS, author="org",
              content="DeepSeek-V4 于 2026-09 发布，支持 1,000,000 token 上下文。")
    row = corpus._conn.execute(
        "SELECT trust, publisher, trust_features_json FROM items WHERE id='p1:1'"
    ).fetchone()
    assert row["trust"] is not None and 0.0 < row["trust"] < 1.0
    assert row["publisher"] == "org"
    features = json.loads(row["trust_features_json"])
    assert features["source_type"] == "rss"
    assert {"prior", "provenance", "template", "freshness"} <= set(features)


def test_pre_v4_database_gains_publisher_and_trust(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
CREATE TABLE items (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE, source_type TEXT NOT NULL, title TEXT NOT NULL,
    url TEXT NOT NULL, author TEXT, published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
    claimable TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}', fingerprint INTEGER NOT NULL,
    cluster_id TEXT, run_id INTEGER);
CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
    finished_at TEXT, since TEXT NOT NULL, items_new INTEGER NOT NULL DEFAULT 0,
    items_total_seen INTEGER NOT NULL DEFAULT 0, note TEXT);
"""
    )
    conn.execute(
        """INSERT INTO items (id, source_type, title, url, author, published_at,
                              fetched_at, content, claimable, fingerprint)
           VALUES ('old:1','v2ex','t','https://e.com/1','someone',
                   '2026-09-01T00:00:00+00:00','2026-09-01T00:00:00+00:00','body','body',0)"""
    )
    conn.commit()
    conn.close()

    migrated = Corpus(path)
    try:
        row = migrated._conn.execute(
            "SELECT publisher, trust FROM items WHERE id='old:1'"
        ).fetchone()
        assert row["publisher"] == "someone"
        assert row["trust"] is not None
    finally:
        migrated.close()


def test_independence_collapses_clusters_then_counts_publishers(corpus: Corpus) -> None:
    store = ClaimStore(corpus)
    a = make_item(corpus, "a", source=SourceType.RSS, author="same-author",
                  content="OpenForge shipped the parser rewrite.")
    b = make_item(corpus, "b", source=SourceType.GDELT, author="same-author",
                  content="OpenForge shipped the parser rewrite again.")
    corpus._conn.execute(
        "UPDATE items SET cluster_id='c1' WHERE id IN ('p1:a','p1:b')"
    )
    claim = Claim(id="claim:c1", item_id=a.id, text="OpenForge shipped the parser rewrite")
    store.upsert_claims([claim])
    for item in (a, b):
        corpus._conn.execute(
            "INSERT OR IGNORE INTO claim_evidence (claim_id,item_id,cluster_id,source_type)"
            " VALUES ('claim:c1',?, 'c1', ?)",
            (item.id, item.source_type.value),
        )
    store.recompute_independence()
    # one cluster => one representative, however many sources repeat it
    assert store.get_claim(claim.id).independent_sources == 1


def test_anonymous_evidence_reports_why_it_is_ungraded(corpus: Corpus) -> None:
    store = ClaimStore(corpus)
    item = make_item(corpus, "anon", source=SourceType.TWITTER, author=None,
                     content="nobody knows who posted this")
    item_row = corpus._conn.execute(
        "SELECT publisher FROM items WHERE id='p1:anon'").fetchone()
    assert item_row["publisher"] is None
    claim = Claim(id="claim:anon", item_id=item.id, text="unattributed assertion")
    store.upsert_claims([claim])
    corpus._conn.execute(
        "INSERT INTO claim_evidence (claim_id,item_id,source_type) VALUES"
        " ('claim:anon','p1:anon','twitter')"
    )
    store.recompute_independence()
    stored = store.get_claim(claim.id)
    assert stored.independent_sources == 0
    assert stored.ungraded_reason == "no_identified_publisher"


def test_triage_gate_selects_on_aggregated_trust(corpus: Corpus) -> None:
    store = ClaimStore(corpus)
    strong_a = make_item(corpus, "s1", source=SourceType.RSS, author="a",
                         content="DeepSeek-V4 2026-09 发布，支持 1,000,000 token。")
    strong_b = make_item(corpus, "s2", source=SourceType.GDELT, author="b",
                         content="DeepSeek-V4 2026-09 发布，支持 1,000,000 token。")
    weak = make_item(corpus, "w1", source=SourceType.TWITTER, author="c",
                     content="感觉还行吧")
    for claim_id, items in (("claim:strong", [strong_a, strong_b]), ("claim:weak", [weak])):
        store.upsert_claims([Claim(id=claim_id, item_id=items[0].id,
                                   text="DeepSeek-V4 shipped with a million token window")])
        # link_evidence is what normally moves a claim to `linked`; the gate
        # only ever considers linked claims.
        store._conn.execute("UPDATE claims SET status='linked' WHERE id=?", (claim_id,))
        for it in items:
            corpus._conn.execute(
                "INSERT OR IGNORE INTO claim_evidence (claim_id,item_id,source_type)"
                " VALUES (?,?,?)", (claim_id, it.id, it.source_type.value),
            )
    store.recompute_independence()
    strong = store.get_claim("claim:strong")
    weak_claim = store.get_claim("claim:weak")
    assert strong.trust > weak_claim.trust
    floor = (strong.trust + weak_claim.trust) / 2.0
    picked = [c.id for c in store.pending_grading(1, limit=5, min_trust=floor)]
    assert picked == ["claim:strong"]
    # with the gate off, both are eligible: the pre-P1 predicate is preserved
    assert sorted(c.id for c in store.pending_grading(1, limit=5)) == [
        "claim:strong", "claim:weak"
    ]


def test_contradiction_pairs_are_recorded_once_and_visible(corpus: Corpus) -> None:
    store = ClaimStore(corpus)
    store.upsert_claims([
        Claim(id="claim:x", item_id="i1", text="模型发布了"),
        Claim(id="claim:y", item_id="i2", text="模型从未发布"),
    ])
    assert store.record_contradiction("claim:x", "claim:y", ["p1:a"]) is True
    assert store.record_contradiction("claim:x", "claim:y", ["p1:a"]) is False
    assert store.record_contradiction("claim:x", "claim:x") is False
    assert store.contradictions_for("claim:x") == ["claim:y"]
    assert store.contradictions_for("claim:y") == ["claim:x"]
    row = corpus._conn.execute(
        "SELECT relation, evidence_json, created_by FROM claim_contradictions"
    ).fetchone()
    assert row["relation"] == "contradicts"
    assert json.loads(row["evidence_json"]) == ["p1:a"]
    assert row["created_by"] == "grader"


# ------------------------------------------------------------- ablation knobs
def test_marker_tiering_ignores_declared_sections() -> None:
    """Ablation arm A must stay reproducible now every scraper declares its tier."""
    from src.corpus.sections import claimable_of
    from src.models import Section

    declared = ContentItem(
        id="abl:1", source_type=SourceType.V2EX, title="t",
        url="https://e.com/abl", published_at=NOW, fetched_at=NOW,
        sections=[
            Section(tier="primary", text="作者正文"),
            Section(tier="community", text="网友说 Y", author="z"),
        ],
    )
    assert declared.content == "作者正文\n\n- @z: 网友说 Y"
    assert claimable_of(declared) == "作者正文"
    # with no marker left in the rendered body the legacy scan has nothing to
    # find, so it returns the whole thing: that is arm A on a migrated source
    assert claimable_of(declared, "marker") == declared.content

    legacy = ContentItem(
        id="abl:2", source_type=SourceType.V2EX, title="t",
        url="https://e.com/abl2", published_at=NOW, fetched_at=NOW,
        content="作者正文\n\n【评论区 Top】\n网友说 Y",
    )
    assert claimable_of(legacy) == "作者正文"
    assert claimable_of(legacy, "marker") == "作者正文"

    leaked = ContentItem(
        id="abl:3", source_type=SourceType.HACKERNEWS, title="t",
        url="https://e.com/abl3", published_at=NOW, fetched_at=NOW,
        content="作者正文\n\n--- Top Comments ---\n网友说 Y",
        sections=[Section(tier="community", text="网友说 Y")],
    )
    assert claimable_of(leaked) == ""                       # declared: all crowd
    assert "网友说 Y" in claimable_of(leaked, "marker")      # marker: cannot tell
    with pytest.raises(ValueError):
        claimable_of(leaked, "nonsense")


def test_store_honours_the_tiering_switch(corpus: Corpus) -> None:
    from src.models import Section

    def probe(idx: str) -> ContentItem:
        return ContentItem(
            id=f"abl:{idx}", source_type=SourceType.HACKERNEWS, title="t",
            url=f"https://e.com/abl{idx}", published_at=NOW, fetched_at=NOW,
            content="author body\n\n--- Top Comments ---\ncrowd words",
            sections=[Section(tier="community", text="crowd words")],
        )

    corpus.add_items([probe("4")], tiering="marker")
    corpus.add_items([probe("5")], tiering="sections")
    rows = {
        r["id"]: r["claimable"] for r in corpus._conn.execute(
            "SELECT id, claimable FROM items WHERE id IN ('abl:4','abl:5')"
        )
    }
    assert "crowd words" in rows["abl:4"]
    assert rows["abl:5"] == ""


# ------------------------------------------------- grade -> contradiction pair
def test_grading_records_the_sibling_it_called_a_conflict(corpus: Corpus) -> None:
    import asyncio

    store = ClaimStore(corpus)
    item = make_item(corpus, "g1", content="OpenForge shipped a parser rewrite in 2026.")
    store.upsert_claims([
        Claim(id="claim:a", item_id=item.id, text="OpenForge shipped the rewrite"),
        Claim(id="claim:b", item_id=item.id, text="OpenForge never shipped anything"),
    ])
    for cid in ("claim:a", "claim:b"):
        corpus._conn.execute(
            "INSERT INTO claim_evidence (claim_id,item_id,source_type) VALUES (?,?,'v2ex')",
            (cid, item.id),
        )
    store.recompute_independence()
    assert [c.id for c in store.sibling_claims("claim:a")] == ["claim:b"]

    class ConflictClient:
        def __init__(self) -> None:
            self.seen = ""

        async def complete(self, system, user, **kwargs):
            self.seen = user
            return '{"verdict": "contested", "confidence": 0.7, "conflicts": [1]}'

    client = ConflictClient()
    analyzer = ClaimAnalyzer(
        store=store, corpus=corpus, client=client, claimable_only=True
    )
    graded = asyncio.run(analyzer.grade_claim(store.get_claim("claim:a")))
    assert graded is not None and graded.verdict == "contested"
    assert "SIBLING CLAIMS" in client.seen
    assert store.contradictions_for("claim:a") == ["claim:b"]
    store.recompute_independence()
    assert store.get_claim("claim:a").verdict == "contested"
