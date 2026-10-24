"""The two write-only tables: engagement as a series, transcripts as a property.

P0 stores them and nothing reads them yet. These tests pin the *contract* the
analysis stage will rely on, so it can be built against a shape that is already
guaranteed:

- engagement appends, so history survives a second observation;
- an unmeasured metric stays absent rather than becoming zero;
- a transcript replaces, because a better extraction of the same media wins.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.corpus.store import Corpus


@pytest.fixture
def corpus(scratch_path):
    return Corpus(scratch_path / "c.db")


def _t(hour: int) -> datetime:
    return datetime(2026, 10, 19, hour, 0, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------- engagement
def test_engagement_is_a_series_not_a_column(corpus):
    assert corpus.record_engagement("i1", "bilibili", {"play": 100}, collected_at=_t(1))
    assert corpus.record_engagement("i1", "bilibili", {"play": 150}, collected_at=_t(2))

    rows = corpus._conn.execute(
        "SELECT metrics_json FROM engagement_snapshots WHERE item_id=? "
        "ORDER BY collected_at", ("i1",)).fetchall()
    assert len(rows) == 2, "the earlier observation must survive"
    assert corpus.latest_engagement("i1")["metrics"] == {"play": 150}


def test_the_same_observation_is_idempotent(corpus):
    first = corpus.record_engagement("i2", "weibo", {"likes": 5}, collected_at=_t(3))
    second = corpus.record_engagement("i2", "weibo", {"likes": 5}, collected_at=_t(3))
    assert first is True
    assert second is False, "re-reading the same post must not inflate history"


def test_empty_metrics_are_not_stored(corpus):
    assert corpus.record_engagement("i3", "weibo", {}) is False
    assert corpus.record_engagement("i3", "weibo", {"likes": None}) is False
    assert corpus._conn.execute(
        "SELECT COUNT(*) FROM engagement_snapshots").fetchone()[0] == 0


def test_absent_metric_stays_absent(corpus):
    """A platform that reports likes but not views must not store views=0."""
    corpus.record_engagement("i4", "xhs", {"liked": 9}, collected_at=_t(4))
    metrics = corpus.latest_engagement("i4")["metrics"]
    assert metrics == {"liked": 9}
    assert "views" not in metrics


def test_platforms_are_kept_apart(corpus):
    corpus.record_engagement("i5", "weibo", {"likes": 1}, collected_at=_t(5))
    corpus.record_engagement("i5", "bilibili", {"play": 2}, collected_at=_t(5))
    count = corpus._conn.execute(
        "SELECT COUNT(*) FROM engagement_snapshots WHERE item_id=?", ("i5",)
    ).fetchone()[0]
    assert count == 2, "one post mirrored on two platforms has two series"


# ----------------------------------------------------------------- transcripts
def test_transcript_stores_segments_and_text(corpus):
    assert corpus.record_transcript(
        "v1", "cc", [{"start": 0, "end": 1.5, "text": "第一句"},
                     {"start": 1.5, "end": 3, "text": "第二句"}],
        lang="zh", confidence=0.9)
    got = corpus.transcript_of("v1")
    assert got["method"] == "cc"
    assert got["text"] == "第一句 第二句"
    assert got["segments"][1]["text"] == "第二句"


def test_a_better_extraction_replaces_the_old_one(corpus):
    corpus.record_transcript("v2", "asr", [{"text": "机器识别的"}], lang="zh")
    corpus.record_transcript("v2", "cc", [{"text": "作者字幕"}], lang="zh")
    same_method = corpus._conn.execute(
        "SELECT COUNT(*) FROM transcripts WHERE item_id=?", ("v2",)).fetchone()[0]
    corpus.record_transcript("v2", "asr", [{"text": "更好的识别结果"}], lang="zh")
    assert same_method == 2, "different methods coexist"
    rows = corpus._conn.execute(
        "SELECT text FROM transcripts WHERE item_id=? AND method='asr'", ("v2",)
    ).fetchall()
    assert len(rows) == 1 and rows[0]["text"] == "更好的识别结果"


def test_method_is_preserved_because_it_changes_meaning(corpus):
    corpus.record_transcript("v3", "vlm", [{"text": "画面里有一台机器人"}])
    assert corpus.transcript_of("v3")["method"] == "vlm"
    # A VLM description is a guess about an image, not the creator's words, and
    # the claim layer has to be able to see that distinction.
    assert corpus.transcript_of("v3")["method"] != "cc"


def test_empty_transcript_is_not_stored(corpus):
    assert corpus.record_transcript("v4", "cc", []) is False
    assert corpus.transcript_of("v4") is None


def test_stats_report_the_new_tables(corpus):
    stats = corpus.stats()
    assert "engagement_snapshots" in stats
    assert "transcripts" in stats


def test_schema_is_additive_for_an_existing_database(scratch_path):
    """A corpus from before these tables must gain them, keeping its items.

    The pre-existing database is simulated by dropping the two new tables from
    a real one: that is exactly what an older file looks like to `_SCHEMA`, and
    it exercises the same `CREATE TABLE IF NOT EXISTS` path without inventing a
    hand-written schema that would drift from the real one.
    """
    path = scratch_path / "old.db"

    first = Corpus(path)
    first._conn.execute(
        "INSERT INTO items (id, source_type, title, url, published_at,"
        " fetched_at, fingerprint) VALUES ('x', 'rss', 't',"
        " 'https://a.example/x', '2026-01-01T00:00:00+00:00',"
        " '2026-01-01T00:00:00+00:00', 1)")
    first._conn.commit()
    first._conn.execute("DROP TABLE engagement_snapshots")
    first._conn.execute("DROP TABLE transcripts")
    first._conn.commit()
    first._conn.close()

    corpus = Corpus(path)
    assert corpus._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert corpus.record_transcript("x", "cc", [{"text": "hi"}]) is True
    assert corpus.record_engagement("x", "bilibili", {"play": 1}) is True
