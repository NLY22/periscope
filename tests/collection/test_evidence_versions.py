from src.corpus.store import Corpus


def test_crawl_versions_keep_original_text_and_observations(scratch_path):
    from periscope.evidence.journal import save_batch, replay_directory
    from periscope.evidence.versions import EvidenceVersions

    corpus = Corpus(scratch_path / "corpus.db")
    row = {"source": "web_crawl", "kind": "media", "title": "robot admissions",
           "url": "https://example.org/admissions", "text": "robot score 365",
           "document": {"format": "html", "sha256": "a"},
           "provenance": {"observed_at": "2026-01-01"}}
    first = save_batch(corpus, [row])["versions"][0]
    backups = list((scratch_path / "backups").glob("before-evidence-*.db"))
    assert len(backups) == 1
    import sqlite3
    with sqlite3.connect(backups[0]) as old:
        assert old.execute("SELECT count(*) FROM items").fetchone()[0] == 1
        assert not old.execute("SELECT 1 FROM sqlite_master WHERE name='evidence_versions'").fetchone()
    again = save_batch(corpus, [{**row, "provenance": {"observed_at": "2026-01-02"},
                                 "document": {"format": "html", "sha256": "b"}}])["versions"][0]
    changed = save_batch(corpus, [{**row, "text": "robot score 366"}])["versions"][0]
    assert first["object_id"] == again["object_id"] == changed["object_id"]
    assert first["version_id"] == again["version_id"]
    assert first["version_id"] != changed["version_id"]
    store = EvidenceVersions(corpus)
    assert store.get_version(first["object_id"], first["version_id"])["text"] == "robot score 365"
    assert store.get_version(changed["object_id"], changed["version_id"])["text"] == "robot score 366"
    assert len(store.observations(first["object_id"])) == 3
    replay_directory(corpus, scratch_path / "evidence-journal")
    assert len(store.observations(first["object_id"])) == 3
    corpus.close()

    corpus = Corpus(scratch_path / "corpus.db")
    assert EvidenceVersions(corpus).get_version(first["object_id"], first["version_id"])["text"] == "robot score 365"
    corpus.close()


def test_bulk_ingest_keeps_changed_web_text_without_document_hash(scratch_path, monkeypatch):
    import json
    import sys
    from periscope.operations import ingest

    row = {"source": "web_crawl", "kind": "media", "title": "robot",
           "url": "https://example.org/", "text": "robot first"}
    source = scratch_path / "http_items.versions.json"
    source.write_text(json.dumps([row, {**row, "text": "robot second"}]), encoding="utf-8")
    target = scratch_path / "ingest.db"
    monkeypatch.setattr(ingest, "CORPUS", target)
    monkeypatch.setattr(ingest, "fulltext_cache", lambda: scratch_path / "missing.jsonl")
    monkeypatch.setattr(sys, "argv", ["ingest", "--only", str(source)])
    assert ingest.main() == 0
    corpus = Corpus(target)
    assert corpus.search("robot first") and corpus.search("robot second")
    corpus.close()
