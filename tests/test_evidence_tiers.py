"""Evidence tiering: comments/replies are leads, never claims' raw material.

The scrapers append crowd text to an item body under markers such as
【评论区 Top】. These tests pin down that the corpus stores that split, that
claim linking/grading and research evidence see only the author-written layer,
and that a database written before tiering is upgraded rather than orphaned.
"""

from datetime import datetime, timezone
from pathlib import Path

import sqlite3

import pytest

from src.analysis.claims import Claim, ClaimAnalyzer, ClaimStore
from src.corpus.sections import (
    TIER_COMMUNITY,
    TIER_PRIMARY,
    claimable_text,
    community_text,
    split_sections,
)
from src.corpus.store import Corpus
from src.models import ContentItem, SourceType
from src.research.session import ResearchSession, ResearchStore

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)

AUTHOR_BODY = "OpenForge shipped a release notes page describing the parser rewrite."
COMMENT_BLOCK = "【评论区 Top】\npineapplepizza says the rewrite broke everything again."
REPLY_BLOCK = "【回复精选】\nsandwichshop mentioned pineapplepizza in passing."


def make_item(
    idx: str,
    content: str,
    title: str = "A title",
    source: SourceType = SourceType.V2EX,
) -> ContentItem:
    return ContentItem(
        id=f"tier-test:{idx}",
        source_type=source,
        title=title,
        url=f"https://example.com/{idx}",
        content=content,
        author="tester",
        published_at=NOW,
        fetched_at=NOW,
    )


# ------------------------------------------------------------------ splitting
def test_split_sections_tags_author_and_crowd_layers() -> None:
    sections = split_sections(f"{AUTHOR_BODY}\n\n{COMMENT_BLOCK}")
    assert [s.tier for s in sections] == [TIER_PRIMARY, TIER_COMMUNITY]
    assert sections[1].marker == "【评论区 Top】"


def test_transcript_stays_claimable_but_comments_do_not() -> None:
    text = "desc line\n\n【视频字幕节选】\ncreator spoken words\n\n【评论区 Top】\ncrowd words"
    assert "creator spoken words" in claimable_text(text)
    assert "crowd words" not in claimable_text(text)
    assert community_text(text) == "crowd words"


def test_marker_lookalike_inside_prose_does_not_split() -> None:
    text = "A line about 【评论区 Top】 being used inline as prose."
    sections = split_sections(text)
    assert len(sections) == 1
    assert sections[0].tier == TIER_PRIMARY


# --------------------------------------------------------------------- store
@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    c = Corpus(tmp_path / "corpus.db")
    c.add_items(
        [
            make_item("author", AUTHOR_BODY, title="Parser rewrite lands"),
            make_item("crowd", f"{AUTHOR_BODY}\n\n{COMMENT_BLOCK}\n\n{REPLY_BLOCK}"),
            make_item("only_crowd", f"{COMMENT_BLOCK}\n\n{REPLY_BLOCK}"),
        ]
    )
    return c


def test_add_items_stores_the_claimable_layer(corpus: Corpus) -> None:
    rows = corpus._conn.execute("SELECT id, claimable FROM items").fetchall()
    by_id = {r["id"]: r["claimable"] for r in rows}
    assert "pineapplepizza" not in by_id["tier-test:author"]
    assert "pineapplepizza" not in by_id["tier-test:crowd"]
    assert "broke everything" in by_id["tier-test:crowd"] or "rewrite" in by_id["tier-test:crowd"]
    assert by_id["tier-test:only_crowd"] == ""


def test_claimable_search_ignores_crowd_only_matches(corpus: Corpus) -> None:
    hits = {row["id"] for row in corpus.search("pineapplepizza", limit=10, tier="claimable")}
    assert hits == set()
    hits_all = {row["id"] for row in corpus.search("pineapplepizza", limit=10)}
    assert len(hits_all) == 2  # panel keeps full-corpus recall


def test_all_tier_search_still_finds_author_text(corpus: Corpus) -> None:
    assert corpus.search("parser", limit=5, tier="claimable")


def test_migration_backfills_claimable_for_a_pre_tiering_database(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
CREATE TABLE items (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE, source_type TEXT NOT NULL, title TEXT NOT NULL,
    url TEXT NOT NULL, author TEXT, published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}', fingerprint INTEGER NOT NULL,
    cluster_id TEXT, run_id INTEGER
);
CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
    finished_at TEXT, since TEXT NOT NULL, items_new INTEGER NOT NULL DEFAULT 0,
    items_total_seen INTEGER NOT NULL DEFAULT 0, note TEXT);
"""
    )
    conn.execute(
        """INSERT INTO items (id, source_type, title, url, published_at, fetched_at,
                              content, fingerprint)
           VALUES ('legacy:1','v2ex','Old','https://e.com/1','2026-09-01T00:00:00+00:00',
                   '2026-09-01T00:00:00+00:00',?,0)""",
        (f"{AUTHOR_BODY}\n\n{COMMENT_BLOCK}",),
    )
    conn.commit()
    conn.close()

    migrated = Corpus(path)
    try:
        row = migrated._conn.execute("SELECT claimable FROM items WHERE id='legacy:1'").fetchone()
        assert "parser rewrite" in row["claimable"]
        assert "pineapplepizza" not in row["claimable"]
        assert migrated.search("pineapplepizza", limit=5, tier="claimable") == []
        assert migrated.search("pineapplepizza", limit=5) != []
    finally:
        migrated.close()


# -------------------------------------------------------------------- claims
def _analyzer(corpus: Corpus, claimable_only: bool) -> ClaimAnalyzer:
    return ClaimAnalyzer(
        store=ClaimStore(corpus),
        corpus=corpus,
        client=None,
        claimable_only=claimable_only,
    )


def _crowd_only_claim() -> Claim:
    return Claim(
        id="claim:test:crowd",
        item_id="tier-test:author",
        text="Users claim pineapplepizza broke everything",
    )


def test_claim_linking_never_rests_on_comment_text(corpus: Corpus) -> None:
    linked = _analyzer(corpus, claimable_only=True).link_evidence(_crowd_only_claim())
    assert [l.item_id for l in linked] == ["tier-test:author"]  # origin only, no crowd votes


def test_ablation_off_restores_the_old_behaviour(corpus: Corpus) -> None:
    linked = _analyzer(corpus, claimable_only=False).link_evidence(_crowd_only_claim())
    assert {l.item_id for l in linked} >= {"tier-test:crowd", "tier-test:only_crowd"}


def test_independence_count_excludes_crowd_backed_items(corpus: Corpus) -> None:
    analyzer = _analyzer(corpus, claimable_only=True)
    claim = Claim(
        id="claim:test:author",
        item_id="tier-test:author",
        text="OpenForge shipped a release notes page",
    )
    analyzer.store.upsert_claims([claim])
    analyzer.link_evidence(claim)
    analyzer.store.recompute_independence()
    stored = analyzer.store.get_claim(claim.id)
    assert stored is not None and stored.independent_sources >= 1
    # crowd-only items were never linked, so they cannot inflate the count
    rows = corpus._conn.execute(
        "SELECT COUNT(*) FROM claim_evidence WHERE claim_id=? AND item_id='tier-test:only_crowd'",
        (claim.id,),
    ).fetchone()[0]
    assert rows == 0


def test_extraction_input_is_the_author_layer(corpus: Corpus) -> None:
    analyzer = _analyzer(corpus, claimable_only=True)
    item = make_item("extract", f"{AUTHOR_BODY}\n\n{COMMENT_BLOCK}")
    author_text = analyzer._author_text(item.content)
    assert "parser rewrite" in author_text
    assert "pineapplepizza" not in author_text


# ------------------------------------------------------------------- research
def _session(corpus: Corpus, claimable_only: bool) -> ResearchSession:
    return ResearchSession(
        store=ResearchStore(corpus),
        corpus=corpus,
        planner=None,
        evidence_per_question=5,
        claimable_only=claimable_only,
    )


def test_research_evidence_snippets_exclude_crowd_text(corpus: Corpus) -> None:
    evidence = _session(corpus, True).gather_evidence("parser rewrite release")
    assert evidence
    assert all("pineapplepizza" not in e["snippet"] for e in evidence)


def test_research_evidence_tier_is_ablatable(corpus: Corpus) -> None:
    crowd_hits = _session(corpus, False).gather_evidence("pineapplepizza")
    assert crowd_hits
    assert any("pineapplepizza" in e["snippet"] for e in crowd_hits)
    assert _session(corpus, True).gather_evidence("pineapplepizza") == []
