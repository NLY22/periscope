"""Versioned investigation evidence is retained for newly connected sources."""

from src.corpus.store import Corpus


def test_generic_discussion_keeps_versions_relationships_and_search(scratch_path):
    from periscope.evidence.journal import replay_directory, save_batch
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.store import InvestigationStore

    corpus = Corpus(scratch_path / "generic.db")
    store = InvestigationStore(corpus)
    investigation = store.create("python bug")
    round_id = store.start_round(investigation, "python bug", "first")
    row = {
        "source": "github_issues", "kind": "community", "_origin": "platform",
        "title": "Python bug", "url": "https://github.com/example/repo/issues/12",
        "text": "search excerpt", "author": "maintainer",
        "evidence": {"source": "github", "type": "issue", "native_id": "example/repo#12", "content_level": "search_result"},
        "provenance": {"method": "github_search", "content_role": "community"},
    }
    first = save_batch(corpus, [row], round_id=round_id)
    root_ref = first["versions"][0]
    full = {**row, "text": "full python bug description",
        "evidence": {**row["evidence"], "content_level": "full"},
        "comments": [{"id": "301", "text": "python workaround one", "user": "reader",
                      "discussion_type": "issue", "replies": [
                          {"id": "302", "parent_id": "301", "text": "nested explanation",
                           "discussion_type": "issue"}]}],
        "discussion_coverage": {"status": "partial", "truncated_by": ["page_limit"]}}
    second = save_batch(corpus, [full], round_id=round_id)
    versions = EvidenceVersions(corpus)
    snapshots = [versions.get_version(**ref) for ref in second["versions"]]
    root = next(item for item in snapshots if item["type"] == "issue")
    reply = next(item for item in snapshots if item.get("native_id") == "302")
    assert root["text"] == "full python bug description"
    assert reply["parent_native_id"] == "301" and reply["text"] == "nested explanation"
    full_ref = next(ref for ref in second["versions"] if versions.get_version(**ref)["type"] == "issue")
    assert root_ref["object_id"] == full_ref["object_id"]
    assert root_ref["version_id"] != full_ref["version_id"]
    assert versions.get_version(**root_ref)["text"] == "search excerpt"
    assert corpus.search("nested explanation")
    assert not corpus.search("nested explanation", tier="claimable")
    assert corpus.search("full python bug description")
    assert len(store.references(round_id)) == 4
    replay_directory(corpus, scratch_path / "evidence-journal")
    assert len(store.references(round_id)) == 4
    assert len(versions.observations(root_ref["object_id"])) == 2
    corpus.close()


def test_generic_discussion_identical_reobservation_and_distinct_reply_kinds(scratch_path):
    from periscope.evidence.journal import save_batch
    from periscope.evidence.versions import EvidenceVersions

    corpus = Corpus(scratch_path / "generic-reuse.db")
    row = {
        "source": "github_issues", "kind": "community", "_origin": "platform",
        "title": "review", "url": "https://github.com/example/repo/pull/5",
        "text": "discussion body",
        "evidence": {"source": "github", "type": "pull_request", "native_id": "example/repo#5"},
        "comments": [{"id": "1", "text": "issue comment", "discussion_type": "issue"},
                     {"id": "1", "text": "review comment", "discussion_type": "review"}],
        "discussion_coverage": {"status": "partial", "collected_at": "2026-10-28"},
    }
    first = save_batch(corpus, [row])
    again = save_batch(corpus, [{**row, "discussion_coverage": {"status": "complete", "collected_at": "2026-10-29"}}])
    assert len(first["versions"]) == 3
    assert first["versions"] == again["versions"]
    replies = [ref for ref in first["versions"] if EvidenceVersions(corpus).get_version(**ref)["type"] == "reply"]
    assert len(replies) == 2 and replies[0]["object_id"] != replies[1]["object_id"]
    corpus.close()
def test_weibo_search_and_direct_link_use_the_same_post_identity():
    from periscope.investigations.catalog import _target

    url = "https://weibo.com/detail/AbC123"
    search = _target("weibo_search", url, ids={"id": "987654321"})
    direct = _target("weibo_search", url)
    assert search[0]["native_id"] == direct[0]["native_id"]
    assert search[1][1] == "987654321"


def test_bilibili_av_and_bv_aliases_share_an_evidence_object(scratch_path):
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.catalog import _target
    from src.corpus.store import Corpus

    corpus = Corpus(scratch_path / "bili-alias.db")
    store = EvidenceVersions(corpus)
    av_url = "https://www.bilibili.com/video/av123"
    bv_url = "https://www.bilibili.com/video/BV1234567890"
    identities = [_target("bili_search", av_url, ids={"bvid": "BV1234567890", "aid": 123})[0],
                  _target("bili_search", av_url)[0], _target("bili_search", bv_url)[0]]
    refs = []
    for index, (identity, url) in enumerate(zip(identities, (av_url, av_url, bv_url))):
        refs.extend(store.record_batch({"batch_id": str(index), "created_at": "2026-10-29T00:00:00Z", "rows": [{
            "source": "bili_search", "url": url, "text": "description", "evidence": identity}]}))
    assert len({ref["object_id"] for ref in refs}) == 1
    corpus.close()


def test_existing_bilibili_alias_objects_are_linked_without_breaking_old_versions(scratch_path):
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.catalog import _target
    from src.corpus.store import Corpus

    corpus = Corpus(scratch_path / "bili-alias-conflict.db")
    store = EvidenceVersions(corpus)
    av = "https://www.bilibili.com/video/av123"
    bv = "https://www.bilibili.com/video/BV1234567890"

    def record(index, url, ids=None):
        identity = _target("bili_search", url, ids=ids)[0]
        return store.record_batch({"batch_id": str(index), "created_at": "2026-10-29T00:00:00Z", "rows": [{
            "source": "bili_search", "url": url, "text": "description", "evidence": identity}]})[0]

    av_ref = record(1, av)
    bv_ref = record(2, bv)
    assert av_ref["object_id"] != bv_ref["object_id"]
    linked = record(3, av, {"bvid": "BV1234567890", "aid": 123})
    assert store.canonical_object_id(av_ref["object_id"]) == store.canonical_object_id(bv_ref["object_id"])
    assert store.canonical_object_id(linked["object_id"]) == store.canonical_object_id(av_ref["object_id"])
    assert store.get_version(**av_ref)["text"] == "description"
    assert store.get_version(**bv_ref)["text"] == "description"
    corpus.close()


def test_weibo_numeric_and_short_id_share_an_evidence_object(scratch_path):
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.catalog import _target
    from src.corpus.store import Corpus

    corpus = Corpus(scratch_path / "weibo-alias.db")
    store = EvidenceVersions(corpus)
    urls = ("https://weibo.com/detail/AbC123", "https://weibo.com/detail/987654321")
    identities = (_target("weibo_search", urls[0], ids={"id": "987654321"})[0], _target("weibo_search", urls[1])[0])
    refs = []
    for index, (url, identity) in enumerate(zip(urls, identities)):
        refs.extend(store.record_batch({"batch_id": str(index), "created_at": "2026-10-29T00:00:00Z", "rows": [{
            "source": "weibo_search", "url": url, "text": "post", "evidence": identity}]}))
    assert len({ref["object_id"] for ref in refs}) == 1
    corpus.close()


def test_linked_legacy_aliases_do_not_duplicate_pending_or_answer_evidence(scratch_path):
    import asyncio
    import json
    from periscope.investigations.answer import EvidenceAnswerer
    from periscope.investigations.catalog import _target
    from periscope.investigations.pending import pending_candidates
    from periscope.investigations.runner import InvestigationRunner
    from src.corpus.store import Corpus

    async def scenario():
        corpus = Corpus(scratch_path / "alias-followup.db")
        av = "https://www.bilibili.com/video/av123"
        bv = "https://www.bilibili.com/video/BV1234567890"

        async def collect(query):
            for url, ids in ((av, None), (bv, None), (av, {"bvid": "BV1234567890", "aid": 123})):
                yield [{"source": "bili_search", "kind": "community", "title": "python video", "text": "python discussion",
                        "url": url, "evidence": {**_target("bili_search", url, ids=ids)[0], "content_level": "search_result"},
                        "discussion_coverage": {"status": "not_attempted"}}]

        class Client:
            async def complete(self, **kwargs):
                items = json.loads(kwargs["user"])["evidence"]
                assert len(items) == 1
                return '{"answer":"Result [1]"}'

        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("python")
        first = runner.submit(inv, "python", "one")
        await runner.wait(first)
        result = await EvidenceAnswerer(Client()).answer(corpus, "python", runner.store.references(first))
        assert result["status"] == "completed"
        second = runner.store.start_round(inv, "继续", "two", options={"seeds": [], "max_pages": None})
        assert len(pending_candidates(runner.store, second, "python")) == 1
        corpus.close()
    asyncio.run(scenario())


def test_one_malformed_journal_batch_does_not_block_other_recovery(scratch_path):
    import json
    from periscope.evidence.journal import replay_directory, save_batch
    from src.corpus.store import Corpus

    corpus = Corpus(scratch_path / "journal-isolation.db")
    directory = scratch_path / "evidence-journal"
    directory.mkdir()
    bad = {"version": 1, "batch_id": "bad", "created_at": "2026-10-29T00:00:00Z", "rows": [{
        "source": "web_crawl", "kind": "media", "url": "https://example.org/bad", "title": {"invalid": "type"}, "text": "bad"}]}
    (directory / "bad.jsonl").write_text(json.dumps(bad) + "\n", encoding="utf-8")
    save_batch(corpus, [{"source": "web_crawl", "kind": "media", "url": "https://example.org/good",
                         "title": "good", "text": "recoverable evidence"}])
    result = replay_directory(corpus, scratch_path / "evidence-journal")
    assert result["batches"] == 2 and result["skipped"]["invalid_row"] == 1
    assert corpus.search("recoverable evidence")
    corpus.close()


def test_malformed_source_row_is_isolated_and_other_evidence_survives(scratch_path):
    import asyncio
    from periscope.investigations.runner import InvestigationRunner
    from src.corpus.store import Corpus

    async def scenario():
        corpus = Corpus(scratch_path / "malformed-row.db")

        async def collect(query):
            yield [{"source": "web_crawl", "kind": "media", "url": "https://example.org/bad", "title": {"bad": "type"}},
                   {"source": "web_crawl", "kind": "media", "url": "https://example.org/good", "title": "good", "text": "valid independent evidence"}]

        runner = InvestigationRunner(corpus, collect)
        rid = runner.submit(runner.store.create("topic"), "topic", "one")
        await runner.wait(rid)
        assert runner.store.get_round(rid)["status"] == "partial"
        assert corpus.search("valid independent evidence") and len(runner.store.references(rid)) == 1
        restarted = InvestigationRunner(corpus, collect)
        assert not restarted.recovery["failed"] and restarted.recovery["skipped"]["invalid_row"] == 1
        corpus.close()
    asyncio.run(scenario())


def test_missing_source_is_reported_and_other_rows_are_kept(scratch_path):
    import asyncio
    from periscope.investigations.runner import InvestigationRunner
    from src.corpus.store import Corpus

    async def scenario():
        corpus = Corpus(scratch_path / "missing-source.db")

        async def collect(query):
            yield [{"url": "https://example.org/bad", "title": "missing source"},
                   {"source": "web_crawl", "url": "https://example.org/good", "text": "valid result"}]

        runner = InvestigationRunner(corpus, collect)
        rid = runner.submit(runner.store.create("topic"), "topic", "one")
        await runner.wait(rid)
        assert runner.store.get_round(rid)["status"] == "partial"
        assert corpus.search("valid result")
        corpus.close()
    asyncio.run(scenario())


def test_malformed_reply_children_do_not_discard_valid_post_or_comments(scratch_path):
    from periscope.evidence.journal import save_batch
    from periscope.evidence.versions import EvidenceVersions
    from src.corpus.store import Corpus

    corpus = Corpus(scratch_path / "malformed-children.db")
    result = save_batch(corpus, [{"source": "github_issues", "kind": "community", "url": "https://github.com/a/b/issues/1",
        "title": "issue", "text": "valid root", "evidence": {"source": "github", "type": "issue", "native_id": "a/b#1"},
        "comments": [{"id": "1", "text": "good reply"}, {"id": "2", "text": "other reply", "replies": 1}]}])
    versions = EvidenceVersions(corpus)
    snapshots = [versions.get_version(**ref) for ref in result["versions"]]
    assert {item["text"] for item in snapshots} == {"valid root", "good reply", "other reply"}
    corpus.close()


def test_legacy_community_rows_are_backed_up_and_removed_from_claimable_index(scratch_path):
    import json
    from periscope.operations.ingest import build
    from src.corpus.semantic import EmbeddingIndex
    from src.corpus.store import Corpus

    database = scratch_path / "legacy-community.db"
    corpus = Corpus(database)
    old = {"source": "public_web", "kind": "media", "url": "https://example.org/post",
           "title": "personalclaimlegacy", "text": "personalclaimlegacy",
           "provenance": {"content_role": "source_text"}}
    official = {"source": "web_crawl", "kind": "media", "url": "https://school.example/official",
                "title": "officialmarker", "text": "officialmarker", "provenance": {"content_role": "source_text"}}
    github_readme = {"source": "public_web", "kind": "media", "url": "https://example.org/readme",
                     "title": "projectofficial", "text": "projectofficial", "provenance": {"content_role": "source_text"}}
    items, _ = build([old, official, github_readme], [])
    items[0].url = "https://www.reddit.com/r/test/comments/123/post"
    items[0].locator = str(items[0].url)
    items[2].url = "https://github.com/owner/project/blob/main/README.md"
    items[2].locator = str(items[2].url)
    corpus.add_items(items)
    EmbeddingIndex(corpus, "test").upsert(items[0].id, [1.0, 0.0])
    assert corpus.search("personalclaimlegacy", tier="claimable")
    corpus._conn.execute("DROP TABLE IF EXISTS corpus_migrations")
    corpus._conn.commit()
    corpus.close()

    reopened = Corpus(database)
    assert reopened.search("personalclaimlegacy") and not reopened.search("personalclaimlegacy", tier="claimable")
    assert reopened.search("officialmarker", tier="claimable")
    assert reopened.search("projectofficial", tier="claimable")
    assert not EmbeddingIndex(reopened, "test").search([1.0, 0.0])
    row = reopened._conn.execute("SELECT sections_json, source_type FROM items WHERE id=?", (items[0].id,)).fetchone()
    assert all(section["tier"] == "community" for section in json.loads(row[0]))
    assert row["source_type"] == "reddit"
    assert list((scratch_path / "backups").glob("*.db"))
    reopened.close()


def test_replayed_old_community_batch_cannot_restore_primary_content(scratch_path):
    from periscope.evidence.journal import save_batch
    from src.corpus.store import Corpus

    corpus = Corpus(scratch_path / "old-community-journal.db")
    save_batch(corpus, [{"source": "web_crawl", "kind": "media", "url": "https://www.reddit.com/r/test/comments/123/post",
                         "title": "oldcommunitymarker", "text": "oldcommunitymarker",
                         "provenance": {"content_role": "source_text"}}])
    assert corpus.search("oldcommunitymarker") and not corpus.search("oldcommunitymarker", tier="claimable")
    corpus.close()


def test_community_migration_does_not_modify_data_when_backup_fails(monkeypatch, scratch_path):
    import sqlite3
    import pytest
    from periscope.evidence import versions
    from periscope.operations.ingest import build
    from src.corpus.store import Corpus

    database = scratch_path / "backup-failure.db"
    corpus = Corpus(database)
    items, _ = build([{"source": "public_web", "kind": "media", "url": "https://example.org/post",
                        "title": "old", "text": "old primary", "provenance": {"content_role": "source_text"}}], [])
    items[0].url = "https://www.reddit.com/r/test/comments/123/post"
    items[0].locator = str(items[0].url)
    corpus.add_items(items)
    corpus._conn.execute("DROP TABLE corpus_migrations")
    corpus._conn.commit()
    corpus.close()

    def fail_backup(_corpus):
        raise OSError("backup unavailable")

    monkeypatch.setattr(versions, "backup_corpus", fail_backup)
    with pytest.raises(OSError, match="backup unavailable"):
        Corpus(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT claimable FROM items WHERE id=?", (items[0].id,)).fetchone()[0] == "old primary"
        assert not connection.execute("SELECT 1 FROM corpus_migrations WHERE name='community_search_results_v1'").fetchone()


def test_concurrent_corpus_opens_serialize_community_migration(monkeypatch, scratch_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from periscope.evidence import versions
    from periscope.operations.ingest import build
    from src.corpus.store import Corpus

    database = scratch_path / "concurrent-migration.db"
    corpus = Corpus(database)
    items, _ = build([{"source": "public_web", "kind": "media", "url": "https://example.org/post",
                        "title": "old", "text": "old primary", "provenance": {"content_role": "source_text"}}], [])
    items[0].url = "https://www.reddit.com/r/test/comments/123/post"
    items[0].locator = str(items[0].url)
    corpus.add_items(items)
    corpus._conn.execute("DROP TABLE corpus_migrations")
    corpus._conn.commit()
    corpus.close()

    original = versions.backup_corpus
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow_backup(target):
        calls.append(1)
        started.set()
        assert release.wait(3)
        original(target)

    def open_and_close():
        instance = Corpus(database)
        instance.close()

    monkeypatch.setattr(versions, "backup_corpus", slow_backup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(open_and_close)
        assert started.wait(3)
        second = pool.submit(open_and_close)
        threading.Event().wait(0.3)
        release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    assert len(calls) == 1
