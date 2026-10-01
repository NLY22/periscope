"""The labeling loop, end to end, on a real corpus - with synthetic labels.

Every piece of this pipeline was tested in isolation, which is exactly how
2026-09-30 got away with claiming the tools were ready: `roc_thresholds()` had a
unit test while `--score` never called it, so a completed 100-row batch would
still have produced no theta. Nothing exercised export -> label -> score ->
apply. This file does, against a real SQLite corpus with claims, evidence links
and a graded verdict, and it asserts the two facts that make the loop worth
running at all: a suggestion appears, and configuring it silences the warning.

Labels here are synthetic and are labelled as such - they prove the plumbing,
not the accuracy. The human batch is still the maintainer's to do.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_claims  # noqa: E402

from src.analysis.claims import Claim, ClaimAnalyzer, ClaimStore, EvidenceLink  # noqa: E402
from src.corpus.store import Corpus  # noqa: E402
from src.corpus.trust import Thresholds, thresholds_from, unused_calibration  # noqa: E402
from src.models import AnalysisConfig, ContentItem, SourceType  # noqa: E402

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

CLAIMS = [
    ("claim:ship", "DeepSeek-V4 shipped in 2026-09."),
    ("claim:price", "DeepSeek-V4 costs 2 CNY per 1M tokens."),
    ("claim:rank", "DeepSeek-V4 ranks first on the 2026-09 leaderboard."),
]

# Synthetic gold labels, three classes so the metric and the fit both have
# something to average over - the gap the docs call out.
GOLD = {"claim:ship": "supported", "claim:price": "contested", "claim:rank": "unsupported"}


class _Client:
    """The grader says `supported` for everything; only the labels disagree."""

    async def complete(self, system: str, user: str, **kwargs) -> str:
        return json.dumps({"verdict": "supported", "confidence": 0.8,
                           "reason": "excerpts agree", "conflicts": []})


def _item(corpus: Corpus, key: str, source: SourceType, publisher: str) -> ContentItem:
    item = ContentItem(
        id=f"loop:{key}", source_type=source, title=f"title {key}",
        url=f"https://e.com/{key}",
        content="DeepSeek-V4 shipped in 2026-09 at 2 CNY per 1M tokens and leads the leaderboard.",
        author=publisher, published_at=NOW, fetched_at=NOW,
    )
    corpus.add_items([item], now=NOW)
    return item


def _graded_corpus(tmp_path: Path) -> tuple[Corpus, ClaimStore]:
    corpus = Corpus(tmp_path / "loop.db")
    store = ClaimStore(corpus)
    rss = _item(corpus, "rss", SourceType.RSS, "vendor-blog")
    news = _item(corpus, "hn", SourceType.HACKERNEWS, "forum-thread")

    for claim_id, text in CLAIMS:
        store.upsert_claims([Claim(id=claim_id, item_id=rss.id, text=text, status="linked")])
        store.add_evidence([
            EvidenceLink(claim_id, rss.id, "cl-rss", "rss", 1.0),
            EvidenceLink(claim_id, news.id, "cl-hn", "hackernews", 0.9),
        ])
    store.recompute_independence()

    analyzer = ClaimAnalyzer(
        store=store, corpus=corpus, client=_Client(),
        # Breadth exists (two families); this keeps the test about the loop
        # rather than about where the hand prior happens to sit.
        thresholds=Thresholds(supported=0.0),
    )
    for claim_id, _ in CLAIMS:
        asyncio.run(analyzer.grade_claim(store.get_claim(claim_id)))
    return corpus, store


def _label(sheet: Path) -> None:
    payload = json.loads(sheet.read_text(encoding="utf-8"))
    for row in payload["labels"]:
        row["human_verdict"] = GOLD[row["claim_id"]]
    sheet.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_export_then_score_produces_a_usable_threshold_suggestion(tmp_path: Path) -> None:
    _graded_corpus(tmp_path)
    sheet = tmp_path / "labels.json"

    assert eval_claims.export_sheet(tmp_path / "loop.db", sheet) == len(CLAIMS)
    payload = json.loads(sheet.read_text(encoding="utf-8"))
    assert payload["tiering"] == "sections" and payload["blind"] is False
    for row in payload["labels"]:
        assert row["machine_trust"] is not None, "theta is defined over T"
        assert row["machine_verdict"] == "supported", "the model uniform, the labels split"
        assert row["machine_verdict_source"] == "llm"
        assert row["evidence"][0]["claimable_excerpt"], "excerpts, not just titles"

    _label(sheet)
    out = tmp_path / "results.json"
    text = eval_claims.score_sheet(sheet, out, None, "sections")
    report = json.loads(out.read_text(encoding="utf-8"))

    assert report["n"] == len(CLAIMS)
    assert report["class_coverage"] == {"supported": 1, "contested": 1, "unsupported": 1}
    assert report["macro_f1_interpretable"] is True, text
    suggested = report["thresholds_suggested"]
    assert suggested is not None, text
    assert 0.0 < suggested["triage"] <= suggested["supported"] <= 1.0
    assert report["accuracy"] == round(1 / 3, 4), "one of three matched the model"
    assert report["by_verdict_source"]["llm"] == {"n": 3.0, "agreement": round(1 / 3, 4)}


def test_the_blind_loop_reaches_the_same_suggestion(tmp_path: Path) -> None:
    """Blind is the shape the number should be quoted from, so it has to be able
    to reach the same place the visible one does."""
    _graded_corpus(tmp_path)
    sheet = tmp_path / "blind.json"

    eval_claims.export_sheet(tmp_path / "loop.db", sheet, blind=True)
    assert "machine_verdict" not in sheet.read_text(encoding="utf-8").split('"labels"')[1]

    _label(sheet)
    out = tmp_path / "blind_results.json"
    text = eval_claims.score_sheet(sheet, out, None, "sections")
    report = json.loads(out.read_text(encoding="utf-8"))

    assert report["blind"] is True, text
    assert "非盲标" not in text and "由副表合入" in text, text
    assert report["thresholds_suggested"] is not None
    assert report["class_coverage"]["contested"] == 1


def test_applying_the_suggestion_is_what_silences_the_warning(tmp_path: Path) -> None:
    """The loop's last mile, in both directions: warn while it is unused, and go
    quiet once the configured number matches it."""
    _graded_corpus(tmp_path)
    sheet = tmp_path / "labels.json"
    eval_claims.export_sheet(tmp_path / "loop.db", sheet)
    _label(sheet)
    out = tmp_path / "results.json"
    eval_claims.score_sheet(sheet, out, None, "sections")
    report = json.loads(out.read_text(encoding="utf-8"))
    suggested = report["thresholds_suggested"]

    assert unused_calibration(AnalysisConfig(), report) is not None
    applied = AnalysisConfig(
        supported_min_trust=suggested["supported"], triage_min_trust=suggested["triage"]
    )
    assert unused_calibration(applied, report) is None

    in_use = thresholds_from(applied)
    assert in_use.supported == suggested["supported"]
    assert in_use.same_family_publishers == Thresholds().same_family_publishers, (
        "applying theta must not silently move the breadth rule"
    )


def test_the_export_refuses_to_score_a_different_arm_than_the_sheet(tmp_path: Path) -> None:
    _graded_corpus(tmp_path)
    sheet = tmp_path / "labels.json"
    eval_claims.export_sheet(tmp_path / "loop.db", sheet, tiering="marker")
    _label(sheet)

    text = eval_claims.score_sheet(sheet, tmp_path / "r.json", None, "sections")
    assert "两档不能共用同一份 ground truth" in text
    # and the marker arm really does show the crowd text this corpus declares
    excerpt = json.loads(sheet.read_text(encoding="utf-8"))["labels"][0]["evidence"][0]
    assert excerpt["claimable_excerpt"], excerpt
