"""Captured discussion and recovered bodies must survive the actual ingest path."""
from datetime import datetime, timezone
import asyncio
import json

import pytest

from periscope.operations import ingest
from periscope.topics import runs
from src.corpus.store import Corpus
from src.models import ContentItem, Section, SourceType


def test_query_round_trip_keeps_comment_text_author_and_reply_parent(scratch_path, monkeypatch):
    topic_dir = scratch_path / "topics"
    topic_dir.mkdir()
    monkeypatch.setattr(runs.paths, "TOPIC_DIR", topic_dir)
    row = {"_origin": "platform", "source": "bili_search", "kind": "community",
           "title": "A video description", "url": "https://www.bilibili.com/video/BV1234567890",
           "text": "Creator description",
           "discussion_url": "https://news.ycombinator.com/item?id=123",
           "discussion_coverage": {"status": "partial", "attempted_ids": 1,
                                   "reported_descendants": 12, "truncated_by": ["comment_limit"]},
           "provenance": {"source_key": "bili_search", "method": "http",
                          "observed_at": "2026-10-24T00:00:00+00:00"},
           "comments": [
               {"rpid": 21, "user": "Alice", "text": "parentcommentneedle", "likes": 3,
                "url": "https://news.ycombinator.com/item?id=21",
                "replies": [{"rpid": 22, "user": "Bob", "text": "deepreplyneedle",
                             "url": "https://news.ycombinator.com/item?id=22"}]},
               {"id": "31", "user": "Reviewer", "text": "inline reviewneedle",
                "discussion_type": "review", "path": "src/main.py", "line": 12,
                "url": "https://github.com/org/project/pull/7#discussion_r31"}]}
    path = runs.write("investigation", [row])
    converted = runs.ingestable_rows(runs.read(path))
    items, skipped = ingest.build(converted, [])
    assert not skipped
    corpus_path = scratch_path / "corpus.db"
    with_corpus = Corpus(corpus_path)
    with_corpus.add_items(items)
    with_corpus.close()

    # Reopen the database so relation assertions prove the persisted record,
    # not only the in-memory ContentItem produced before ingestion.
    with_corpus = Corpus(corpus_path)
    try:
        assert len(with_corpus.search("deepreplyneedle")) == 1
        assert not with_corpus.search("deepreplyneedle", tier="claimable")
        stored = with_corpus._conn.execute(
            "SELECT sections_json, metadata_json FROM items WHERE url=?",
            ("https://www.bilibili.com/video/BV1234567890",),
        ).fetchone()
        sections = json.loads(stored["sections_json"])
        metadata = json.loads(stored["metadata_json"])
        reply = next(s for s in sections if "deepreplyneedle" in s["text"])
        assert reply["tier"] == "community" and reply["author"] == "Bob"
        assert str(reply["meta"]["parent_id"]) == "21"
        assert reply["locator"] == "https://news.ycombinator.com/item?id=22"
        parent = next(s for s in sections if "parentcommentneedle" in s["text"])
        assert parent["author"] == "Alice" and parent["meta"]["likes"] == 3
        review = next(s for s in sections if "inline reviewneedle" in s["text"])
        assert review["meta"]["discussion_type"] == "review"
        assert review["meta"]["path"] == "src/main.py" and review["meta"]["line"] == 12
        assert metadata["source_key"] == "bili_search"
        assert metadata["provenance"]["source_key"] == "bili_search"
        assert metadata["discussion_url"] == "https://news.ycombinator.com/item?id=123"
        assert metadata["discussion_coverage"]["status"] == "partial"
    finally:
        with_corpus.close()


def test_browser_shard_comments_remain_community(scratch_path):
    row = {"platform": "xhs", "title": "Note title", "url": "https://www.xiaohongshu.com/explore/abc",
           "author": "Creator", "text": "note body", "provenance": {"method": "browser"},
           "comments": [None, {}, {"id": "r1", "author": "Reader", "text": "browserreplyneedle"}]}
    items, _ = ingest.build([], [row])
    assert "browserreplyneedle" in items[0].content
    assert all(s.tier == "community" for s in items[0].sections)
    assert items[0].metadata["provenance"]["method"] == "browser"


def test_legacy_marked_replies_stay_searchable_without_becoming_author_evidence(scratch_path):
    row = {"source": "v2ex_search", "kind": "community", "title": "Existing export",
           "url": "https://example.org/legacy-marked", "text": "Author opening\n\n【回复精选】\nLegacyReplyNeedle"}
    items, _ = ingest.build([row], [])
    corpus = Corpus(scratch_path / "legacy.db")
    try:
        corpus.add_items(items)
        assert corpus.search("LegacyReplyNeedle")
        assert not corpus.search("LegacyReplyNeedle", tier="claimable")
    finally:
        corpus.close()


@pytest.mark.parametrize("tier,asserted", [("primary", True), ("community", True), ("primary", False)])
@pytest.mark.parametrize("mode", ["fulltext-only", "ingest"])
def test_body_recovery_preserves_metadata_tiers_discussion_and_indexes(scratch_path, monkeypatch, tier, asserted, mode):
    db = scratch_path / "corpus.db"
    cache = scratch_path / "fulltext.jsonl"
    item = ContentItem(id="recovered", source_type=SourceType.V2EX, title="Saved title",
                       url="https://example.org/topic", author="Original author",
                       published_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
                       sections=[Section(tier=tier, text="oldsnippetneedle", asserted=asserted),
                                 Section(tier="community", text="retaineddiscussionneedle", author="Reader")],
                       metadata={"source_key": "v2ex_search", "source_kind": "community",
                                 "provenance": {"method": "http", "content_role": tier},
                                 "metrics": {"likes": 7}})
    corpus = Corpus(db)
    corpus.add_items([item])
    old_fp = corpus._conn.execute("SELECT fingerprint FROM items WHERE id=?", (item.id,)).fetchone()[0]
    corpus.close()
    text = "recoveredbodyneedle " * 32
    cache.write_text(json.dumps({"id": item.id, "url": str(item.url), "text": text}), encoding="utf-8")
    monkeypatch.setattr(ingest, "CORPUS", db)
    monkeypatch.setattr(ingest, "fulltext_cache", lambda: cache)
    if mode == "fulltext-only":
        assert ingest.merge_fulltext_only() == 0
    else:
        shard = scratch_path / "http_items.empty.json"
        shard.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(ingest.sys, "argv", ["ingest", "--only", str(shard)])
        assert ingest.main() == 0
    corpus = Corpus(db)
    try:
        stored = dict(corpus._conn.execute("SELECT * FROM items WHERE id=?", (item.id,)).fetchone())
        metadata = json.loads(stored["metadata_json"])
        assert metadata["source_key"] == "v2ex_search"
        assert metadata["metrics"] == {"likes": 7}
        assert metadata["provenance"]["content_role"] == tier
        assert "retaineddiscussionneedle" in stored["content"]
        assert stored["fingerprint"] != old_fp
        sections = json.loads(stored["sections_json"])
        assert sections[0]["tier"] == tier and sections[0]["asserted"] is asserted
        assert "recoveredbodyneedle" in sections[0]["text"]
        assert len(corpus.search("recoveredbodyneedle")) == 1
        assert bool(corpus.search("recoveredbodyneedle", tier="claimable")) == (tier == "primary" and asserted)
        assert "retaineddiscussionneedle" not in stored["claimable"]
    finally:
        corpus.close()


def test_fulltext_cache_skips_malformed_records_and_unknown_ids(scratch_path, monkeypatch):
    db = scratch_path / "corpus.db"
    corpus = Corpus(db)
    corpus.add_items([ContentItem(id="valid", source_type=SourceType.RSS, title="Article",
                                  url="https://example.org/a", sections=[Section(tier="primary", text="Teaser")])])
    corpus.close()
    cache = scratch_path / "cache.jsonl"
    cache.write_text('\n'.join(["broken json", "[]", "null", json.dumps({"id": "valid", "text": {"not": "text"}}),
                      json.dumps({"id": "missing", "text": "x" * 450}),
                      json.dumps({"id": "valid", "text": "validrecoverneedle " * 30})]), encoding="utf-8")
    monkeypatch.setattr(ingest, "CORPUS", db)
    monkeypatch.setattr(ingest, "fulltext_cache", lambda: cache)
    assert ingest.merge_fulltext_only() == 0
    corpus = Corpus(db)
    try:
        assert len(corpus.search("validrecoverneedle")) == 1
    finally:
        corpus.close()


@pytest.mark.parametrize("outcome", ["success", "error", "too_long", "short"])
def test_direct_backfill_keeps_source_metadata_and_only_claims_declared_author_text(scratch_path, monkeypatch, outcome):
    from periscope.operations import backfill
    db = scratch_path / "corpus.db"
    corpus = Corpus(db)
    corpus.add_items([ContentItem(id="direct", source_type=SourceType.V2EX, title="Discussion",
                     url="https://example.org/direct", author="Reader",
                     sections=[Section(tier="community", text="A teaser")],
                     metadata={"source_key": "v2ex_search", "metrics": {"likes": 7}})])
    corpus.close()
    urls = scratch_path / "urls.txt"
    urls.write_text("https://example.org/direct", encoding="utf-8")
    if outcome == "success":
        history = [{"id": "direct", "text": "directrecoveryneedle " * 32},
                   {"id": "direct", "text": "oldcachedneedle " * 32}]
        (scratch_path / "cache.jsonl").write_text("\n".join(json.dumps(row) for row in history) + "\n", encoding="utf-8")
    monkeypatch.setattr(backfill, "CORPUS", db)
    monkeypatch.setattr(backfill.paths, "fulltext_cache", lambda: scratch_path / "cache.jsonl")
    monkeypatch.setattr(backfill, "report", lambda *_: None)
    monkeypatch.setattr(backfill.sys, "argv", ["backfill", "--urls-file", str(urls)])
    class Extractor:
        async def extract(self, *args):
            if outcome == "error":
                raise ValueError("Example extractor failure")
            return "x" * 61000 if outcome == "too_long" else "short" if outcome == "short" else "directrecoveryneedle " * 32
    monkeypatch.setattr(backfill, "TrafilaturaExtractor", lambda *_: Extractor())
    assert asyncio.run(backfill.main()) == 0
    corpus = Corpus(db)
    try:
        stored = dict(corpus._conn.execute("SELECT * FROM items WHERE id='direct'").fetchone())
        assert json.loads(stored["metadata_json"])["source_key"] == "v2ex_search"
        assert json.loads(stored["metadata_json"])["metrics"]["likes"] == 7
        assert stored["claimable"] == ""
        if outcome == "success":
            assert corpus.search("directrecoveryneedle")
            cached = [json.loads(line) for line in (scratch_path / "cache.jsonl").read_text(encoding="utf-8").splitlines()]
            assert "directrecoveryneedle" in cached[-1]["text"]
    finally:
        corpus.close()


def test_recovery_is_repeatable_and_legacy_community_text_is_not_promoted(scratch_path):
    from periscope.operations.fulltext import recover_body
    corpus = Corpus(scratch_path / "corpus.db")
    try:
        corpus.add_items([ContentItem(id="legacy", source_type=SourceType.V2EX, title="Old item",
                                       url="https://example.org/legacy", content="Old snippet",
                                       metadata={"provenance": {"content_role": "community"}})])
        record = {"id": "legacy", "text": "legacyrecoveryneedle " * 30, "url": "https://example.org/legacy"}
        assert recover_body(corpus._conn, record)
        assert not recover_body(corpus._conn, record)
        row = corpus._conn.execute("SELECT claimable,sections_json FROM items WHERE id='legacy'").fetchone()
        assert row[0] == "" and json.loads(row[1])[0]["tier"] == "community"
    finally:
        corpus.close()


def test_replaying_an_unchanged_body_avoids_recomputing_its_fingerprint(scratch_path, monkeypatch):
    from periscope.operations import fulltext
    corpus = Corpus(scratch_path / "corpus.db")
    try:
        corpus.add_items([ContentItem(id="repeat", source_type=SourceType.RSS, title="Article",
                                       url="https://example.org/repeat", sections=[Section(tier="primary", text="Teaser")])])
        record = {"id": "repeat", "url": "https://example.org/repeat", "text": "unchangedbodyneedle " * 100}
        assert fulltext.recover_body(corpus._conn, record)
        def unnecessary(*args):
            pytest.fail("Unchanged cached text should not repeat expensive SimHash work")
        monkeypatch.setattr(fulltext, "fingerprint", unnecessary)
        assert not fulltext.recover_body(corpus._conn, record)
    finally:
        corpus.close()
