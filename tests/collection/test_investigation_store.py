import pytest
from src.corpus.store import Corpus


def test_sessions_share_evidence_but_rounds_pin_their_version(scratch_path):
    from periscope.evidence.journal import save_batch
    from periscope.investigations.store import InvestigationStore

    corpus = Corpus(scratch_path / "corpus.db")
    row = {"source": "web_crawl", "kind": "media", "title": "robot",
           "url": "https://example.org/", "text": "robot first version"}
    first = save_batch(corpus, [row])["versions"][0]
    store = InvestigationStore(corpus)
    a = store.create("first investigation")
    b = store.create("second investigation")
    ar = store.start_round(a, "find details", "request-a")
    store.attach(ar, **first)
    store.attach(ar, **first)
    with pytest.raises(ValueError):
        store.attach(ar, object_id=first["object_id"], version_id="wrong-version")
    second = save_batch(corpus, [{**row, "text": "robot updated version"}])["versions"][0]
    br = store.start_round(b, "find updates", "request-b")
    store.attach(br, **second)
    assert store.references(ar) == [first]
    assert store.references(br) == [second]
    assert store.start_round(a, "find details", "request-a") == ar
    corpus.close()
    corpus = Corpus(scratch_path / "corpus.db")
    assert InvestigationStore(corpus).references(ar) == [first]
    corpus.close()


def test_new_round_cancels_old_round_and_rejects_late_results(scratch_path):
    from periscope.evidence.journal import save_batch
    from periscope.investigations.store import InvestigationStore

    corpus = Corpus(scratch_path / "corpus.db")
    row = {"source": "web_crawl", "kind": "media", "title": "robot",
           "url": "https://example.org/", "text": "robot result"}
    reference = save_batch(corpus, [row])["versions"][0]
    store = InvestigationStore(corpus)
    session = store.create("robot")
    old = store.start_round(session, "initial", "request-1")
    store.attach(old, **reference)
    new = store.start_round(session, "follow up", "request-2")
    assert store.get_round(old)["status"] == "cancelled"
    assert store.get_round(new)["status"] == "running"
    with pytest.raises(ValueError):
        store.attach(old, **reference)
    assert store.references(old) == [reference]
    corpus.close()
