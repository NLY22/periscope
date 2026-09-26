"""Claim-verdict agreement maths and the labeling sheet round trip.

The hand-computed case below is the guard rail for the number the project
currently cannot claim: how often the grader agrees with a human.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.analysis.agreement import independence_buckets, score_pairs
from src.analysis.claims import Claim, ClaimStore, EvidenceLink
from src.corpus.store import Corpus
from src.models import ContentItem, SourceType

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import eval_claims  # noqa: E402

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


# ------------------------------------------------------------------- metrics
def test_score_pairs_matches_hand_computed_values() -> None:
    pairs = [
        ("supported", "supported"),
        ("supported", "contested"),
        ("contested", "contested"),
        ("unsupported", "contested"),
    ]
    out = score_pairs(pairs)
    assert out.n == 4 and out.accuracy == pytest.approx(0.5)

    s = out.per_label["supported"]
    assert s["precision"] == pytest.approx(1.0) and s["recall"] == pytest.approx(0.5)
    assert s["f1"] == pytest.approx(2 / 3)
    c = out.per_label["contested"]
    assert c["precision"] == pytest.approx(1 / 3) and c["recall"] == pytest.approx(1.0)
    assert c["f1"] == pytest.approx(0.5)
    u = out.per_label["unsupported"]
    assert u["precision"] == 0.0 and u["recall"] == 0.0 and u["f1"] == 0.0
    assert out.macro_f1 == pytest.approx((2 / 3 + 0.5 + 0.0) / 3)
    assert out.confusion["supported"]["supported"] == 1


def test_unknown_or_missing_labels_are_excluded_not_guessed() -> None:
    out = score_pairs([("supported", "supported"), ("rumour", "supported"), (None, "contested")])
    assert out.n == 1 and out.unlabeled == 2 and out.accuracy == 1.0


def test_empty_input_reports_zeroes_without_division_errors() -> None:
    out = score_pairs([])
    assert out.n == 0 and out.macro_f1 == 0.0 and out.per_label["supported"]["f1"] == 0.0


def test_independence_buckets_show_the_trend_being_claimed() -> None:
    rows = [
        (1, "supported", "supported"),
        (1, "supported", "contested"),
        (2, "contested", "contested"),
        (5, "unsupported", "unsupported"),
        (3, "unsupported", "supported"),
    ]
    buckets = independence_buckets(rows)
    assert buckets["1"] == {"n": 2.0, "agreement": 0.5}
    assert buckets["2"] == {"n": 1.0, "agreement": 1.0}
    assert buckets["3+"] == {"n": 2.0, "agreement": 0.5}


# --------------------------------------------------------------------- sheet
@pytest.fixture()
def graded_corpus(tmp_path: Path) -> Corpus:
    corpus = Corpus(tmp_path / "corpus.db")
    corpus.add_items(
        [
            ContentItem(
                id="ag:origin",
                source_type=SourceType.RSS,
                title="OpenForge 完成 B 轮",
                url="https://ag.example.com/1",
                content="OpenForge 完成 B 轮，估值 30 亿。",
                author="tester",
                published_at=NOW,
                fetched_at=NOW,
            ),
            ContentItem(
                id="ag:second",
                source_type=SourceType.HACKERNEWS,
                title="OpenForge Series B",
                url="https://ag.example.com/2",
                content="OpenForge closed a Series B at $3 billion.",
                author="tester",
                published_at=NOW,
                fetched_at=NOW,
            ),
        ]
    )
    store = ClaimStore(corpus)
    store.upsert_claims(
        [
            Claim(
                id="claim:1", item_id="ag:origin", text="OpenForge 完成 B 轮融资",
                status="graded", verdict="supported", confidence=0.8, independent_sources=1,
            ),
            Claim(
                id="claim:2", item_id="ag:origin", text="估值为 30 亿美元",
                status="linked", verdict=None, independent_sources=0,
            ),
        ]
    )
    store.add_evidence(
        [
            EvidenceLink("claim:1", "ag:origin", None, "rss", 1.0),
            EvidenceLink("claim:1", "ag:second", None, "hackernews", 0.5),
        ]
    )
    store.recompute_independence()
    return corpus


def test_export_sheet_lists_linked_claims_with_evidence(graded_corpus: Corpus, tmp_path: Path) -> None:
    sheet = tmp_path / "labels.json"
    count = eval_claims.export_sheet(Path(graded_corpus.path), sheet)
    assert count == 2
    payload = json.loads(sheet.read_text(encoding="utf-8"))
    first = payload["labels"][0]
    assert first["human_verdict"] is None
    assert "instructions" in payload
    assert first["evidence"] and {"title", "url", "source_type"} <= set(first["evidence"][0])


def test_export_needs_a_corpus(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        eval_claims.export_sheet(tmp_path / "missing.db", tmp_path / "out.json")


def test_score_sheet_computes_and_writes(graded_corpus: Corpus, tmp_path: Path) -> None:
    sheet = tmp_path / "labels.json"
    eval_claims.export_sheet(Path(graded_corpus.path), sheet)
    payload = json.loads(sheet.read_text(encoding="utf-8"))
    payload["labels"][0]["human_verdict"] = "supported"
    payload["labels"][0]["machine_verdict"] = "supported"
    labeled = tmp_path / "labeled.json"
    labeled.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "results.json"
    report = eval_claims.score_sheet(labeled, out)
    assert "accuracy=1.000" in report
    assert "按独立信源数分桶" in report
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["n"] == 1 and written["accuracy"] == 1.0


def test_score_sheet_without_labels_says_so(tmp_path: Path) -> None:
    sheet = tmp_path / "empty.json"
    sheet.write_text(json.dumps({"labels": [{"claim_id": "x", "human_verdict": None}]}), encoding="utf-8")
    assert "无法计分" in eval_claims.score_sheet(sheet, None)
