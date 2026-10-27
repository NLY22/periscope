import pytest

from src.corpus.store import Corpus


ROW = {"source": "web_crawl", "kind": "media", "title": "robot admissions",
       "url": "https://example.org/admissions", "text": "robot score 365",
       "document": {"sha256": "v1"}, "provenance": {"method": "crawl"}}


def test_batch_can_be_replayed_after_database_failure(scratch_path, monkeypatch):
    from periscope.evidence.journal import save_batch, replay_batch

    corpus = Corpus(scratch_path / "corpus.db")
    original = corpus.add_items
    monkeypatch.setattr(corpus, "add_items", lambda items: (_ for _ in ()).throw(OSError("database unavailable")))
    with pytest.raises(OSError):
        save_batch(corpus, [ROW])
    batches = list((scratch_path / "evidence-journal").glob("*.jsonl"))
    assert len(batches) == 1
    monkeypatch.setattr(corpus, "add_items", original)
    assert replay_batch(corpus, batches[0])["new"] == 1
    assert replay_batch(corpus, batches[0])["new"] == 0
    corpus.close()
    corpus = Corpus(scratch_path / "corpus.db")
    assert any("robot score 365" in row["content"] for row in corpus.search("robot"))
    corpus.close()


def test_replay_rejects_incomplete_or_corrupt_batch_without_writes(scratch_path):
    from periscope.evidence.journal import replay_batch

    corpus = Corpus(scratch_path / "corpus.db")
    path = scratch_path / "bad.jsonl"
    path.write_text('{"version":1,"rows":', encoding="utf-8")
    with pytest.raises(ValueError):
        replay_batch(corpus, path)
    assert corpus.search("robot") == []
    corpus.close()


def test_directory_recovery_continues_after_corrupt_batch(scratch_path):
    from periscope.evidence.journal import save_batch, replay_directory

    first = Corpus(scratch_path / "original.db")
    save_batch(first, [ROW])
    first.close()
    directory = scratch_path / "evidence-journal"
    (directory / "invalid.jsonl").write_text("broken\n", encoding="utf-8")
    restored = Corpus(scratch_path / "restored.db")
    outcome = replay_directory(restored, directory)
    assert outcome["new"] == 1 and len(outcome["failed"]) == 1
    assert outcome["failed"][0]["batch"] == "invalid.jsonl"
    assert any("robot score 365" in row["content"] for row in restored.search("robot"))
    assert replay_directory(restored, directory)["new"] == 0
    restored.close()


def test_recovery_reports_records_it_cannot_convert(scratch_path):
    from periscope.evidence.journal import save_batch, replay_directory

    corpus = Corpus(scratch_path / "corpus.db")
    save_batch(corpus, [{"source": "unknown-source", "title": "unknown", "url": "https://example.org/"}])
    result = replay_directory(corpus, scratch_path / "evidence-journal")
    assert result["skipped"]["unmapped kind: "] == 1
    corpus.close()
