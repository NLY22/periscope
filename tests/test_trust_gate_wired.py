"""The `supported` gates must run in the product, not only in the harness.

`classify()` and `Thresholds` existed, and every document described them as the
mechanism behind a `supported` verdict - but nothing on the grading path called
them. A model that only reads excerpts could call a same-family repost pile
"supported", the row would store that, and the report's "可信度不足" wording then
implied an aggregate objection that had never been made. Meanwhile the docs told
the maintainer to write a calibrated theta into a `trust` config that did not
exist, so the one quantity the labeling pipeline produces had nowhere to land.

These tests pin the two things that fix it: the gate demoting an un-backable
`supported`, and the configured cut points actually reaching the analyzer.
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

from src.analysis.claims import Claim, ClaimAnalyzer, ClaimStore
from src.corpus.store import Corpus
from src.corpus.trust import Thresholds, thresholds_from
from src.models import AnalysisConfig, ContentItem, SourceType

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

SUPPORTED = json.dumps(
    {"verdict": "supported", "confidence": 0.9, "reason": "the excerpts agree", "conflicts": []}
)
UNSUPPORTED = json.dumps(
    {"verdict": "unsupported", "confidence": 0.8, "reason": "too vague", "conflicts": []}
)


class _Client:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def complete(self, system: str, user: str, **kwargs) -> str:
        return self.reply


def _corpus(tmp_path) -> Corpus:
    return Corpus(tmp_path / "gate.db")


def _item(corpus: Corpus, idx: str, source: SourceType, author: str) -> ContentItem:
    item = ContentItem(
        id=f"g:{idx}", source_type=source, title=f"t {idx}",
        url=f"https://e.com/{idx}",
        content="DeepSeek-V4 shipped in 2026-09 at 2 CNY per million tokens.",
        author=author, published_at=NOW, fetched_at=NOW,
    )
    corpus.add_items([item], now=NOW)
    return item


def _claim_with_evidence(corpus: Corpus, store: ClaimStore, pairs) -> ClaimStore:
    claim = Claim(id="claim:x", item_id=pairs[0][0].id, text="DeepSeek-V4 shipped in 2026-09.")
    store.upsert_claims([claim])
    for item, cluster in pairs:
        corpus._conn.execute(
            "INSERT INTO claim_evidence (claim_id, item_id, cluster_id, source_type, score)"
            " VALUES (?,?,?,?,1.0)",
            (claim.id, item.id, cluster, item.source_type.value),
        )
    corpus._conn.commit()
    store.recompute_independence()
    return store


def _row(corpus: Corpus, claim_id: str):
    return corpus._conn.execute(
        "SELECT verdict, verdict_source, trust, independent_sources FROM claims WHERE id=?",
        (claim_id,),
    ).fetchone()


def test_two_publishers_in_one_family_cannot_be_graded_supported(tmp_path) -> None:
    """The breadth rule, exercised through `grade_claim` rather than `classify`.

    Two distinct publishers inside one source family is the shape the fork must
    still be able to support - but only at `same_family_publishers` depth, which
    this is not. Before the wiring, the stored verdict here was whatever the
    model said.
    """
    corpus, store = _corpus(tmp_path), None
    store = ClaimStore(corpus)
    a = _item(corpus, "a", SourceType.HACKERNEWS, "writer-a")
    b = _item(corpus, "b", SourceType.HACKERNEWS, "writer-b")
    _claim_with_evidence(corpus, store, [(a, "c1"), (b, "c2")])

    analyzer = ClaimAnalyzer(store=store, corpus=corpus, client=_Client(SUPPORTED))
    graded = asyncio.run(analyzer.grade_claim(store.get_claim("claim:x")))

    assert graded is not None
    row = _row(corpus, "claim:x")
    assert graded.verdict == "unsupported" == row["verdict"]
    assert row["verdict_source"] == "trust_gate", (
        "a demotion has to be attributable, or the model silently loses a label"
    )


def test_the_gate_never_promotes_a_model_that_said_unsupported(tmp_path) -> None:
    """Whether the excerpts confirm the claim is the model's call; the gate only
    withholds the `supported` the breadth arithmetic cannot earn."""
    corpus = _corpus(tmp_path)
    store = ClaimStore(corpus)
    a = _item(corpus, "a", SourceType.RSS, "writer-a")
    b = _item(corpus, "b", SourceType.HACKERNEWS, "writer-b")
    _claim_with_evidence(corpus, store, [(a, "c1"), (b, "c2")])

    asyncio.run(ClaimAnalyzer(store=store, corpus=corpus, client=_Client(UNSUPPORTED))
                .grade_claim(store.get_claim("claim:x")))

    row = _row(corpus, "claim:x")
    assert row["verdict"] == "unsupported" and row["verdict_source"] == "llm"


def test_configured_cut_points_reach_the_gate(tmp_path) -> None:
    """The same evidence shape, with depth lowered to 2, must earn `supported`.

    If the analyzer rebuilt its own `Thresholds()` at the call site, every
    document would stay correct while the configured values did nothing.
    """
    corpus = _corpus(tmp_path)
    store = ClaimStore(corpus)
    a = _item(corpus, "a", SourceType.HACKERNEWS, "writer-a")
    b = _item(corpus, "b", SourceType.HACKERNEWS, "writer-b")
    _claim_with_evidence(corpus, store, [(a, "c1"), (b, "c2")])

    analyzer = ClaimAnalyzer(
        store=store, corpus=corpus, client=_Client(SUPPORTED),
        thresholds=Thresholds(supported=0.0, same_family_publishers=2),
    )
    asyncio.run(analyzer.grade_claim(store.get_claim("claim:x")))

    row = _row(corpus, "claim:x")
    assert row["verdict"] == "supported", row["verdict_source"]
    assert row["verdict_source"] == "llm"


def test_configuring_nothing_keeps_the_hand_priors_and_says_so() -> None:
    default = thresholds_from(AnalysisConfig())
    assert default == Thresholds(), "an unset field must not read as a fitted value"
    assert default.supported == 0.55 and default.same_family_publishers == 3

    mixed = thresholds_from(AnalysisConfig(supported_min_trust=0.42, same_family_publishers=5))
    assert (mixed.supported, mixed.same_family_publishers) == (0.42, 5)
    assert mixed.triage == Thresholds().triage, "unset fields stay at the prior"
    assert thresholds_from(AnalysisConfig(supported_min_trust=0.0)).supported == 0.0


def test_the_report_labels_a_gate_demotion_differently_than_a_models_no() -> None:
    """`可信度不足` and `模型原判 supported，被门否决` are different findings; the
    second is the only one that tells the reader the model disagreed."""
    text = (Path(__file__).resolve().parents[1] / "src" / "research" / "session.py").read_text(
        encoding="utf-8")
    assert "未通过可信度门" in text and "模型原判 supported" in text
    assert "verdict_source" in text, "the renderer stopped reading who decided it"
    assert "trust_gate" in text


def test_an_old_claims_table_gains_the_column_without_losing_rows(tmp_path) -> None:
    """Old rows keep a NULL source, which is the honest answer: nobody knows
    whether a pre-gate `supported` would have passed the breadth rule."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE claims (id TEXT PRIMARY KEY, item_id TEXT NOT NULL, text TEXT NOT NULL,
           claim_type TEXT NOT NULL DEFAULT 'fact', time_scope TEXT,
           status TEXT NOT NULL DEFAULT 'extracted', verdict TEXT, confidence REAL,
           independent_sources INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO claims VALUES ('claim:old','g:a','t','fact',NULL,'graded','supported',"
        "0.9,2,'2026-09-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    corpus = Corpus(path)
    ClaimStore(corpus)
    columns = {r["name"] for r in corpus._conn.execute("PRAGMA table_info(claims)")}
    assert {"trust", "ungraded_reason", "verdict_source"} <= columns

    row = corpus._conn.execute(
        "SELECT verdict, verdict_source FROM claims WHERE id='claim:old'"
    ).fetchone()
    assert row["verdict"] == "supported" and row["verdict_source"] is None


# ------------------------------------------------------------ the source travels
# A veto that lives only in a column nobody selects is invisible exactly where
# it matters: the store read, the JSON consumers, and the labeling sheet.

def _demoted(tmp_path):
    corpus = _corpus(tmp_path)
    store = ClaimStore(corpus)
    a = _item(corpus, "a", SourceType.HACKERNEWS, "writer-a")
    b = _item(corpus, "b", SourceType.HACKERNEWS, "writer-b")
    _claim_with_evidence(corpus, store, [(a, "c1"), (b, "c2")])
    asyncio.run(ClaimAnalyzer(store=store, corpus=corpus, client=_Client(SUPPORTED))
                .grade_claim(store.get_claim("claim:x")))
    return corpus


def _score(path: Path, out: Path):
    text = eval_claims.score_sheet(path, out)
    return text, json.loads(out.read_text(encoding="utf-8"))


def test_the_source_survives_a_read_through_the_store(tmp_path) -> None:
    _demoted(tmp_path)
    claim = ClaimStore(Corpus(tmp_path / "gate.db")).get_claim("claim:x")

    assert claim is not None and claim.verdict == "unsupported"
    assert claim.verdict_source == "trust_gate", "the read path dropped the column"
    assert claim.to_dict()["verdict_source"] == "trust_gate", "the JSON shape lost it"


def test_the_labeling_sheet_says_who_made_the_machine_verdict(tmp_path) -> None:
    _demoted(tmp_path)
    sheet = tmp_path / "sheet.json"
    assert eval_claims.export_sheet(tmp_path / "gate.db", sheet) >= 1

    row = json.loads(sheet.read_text(encoding="utf-8"))["labels"][0]
    assert row["machine_verdict"] == "unsupported"
    assert row["machine_verdict_source"] == "trust_gate", (
        "without this the eval measures the model and the gate as one thing"
    )


def test_blind_export_holds_the_source_back_with_the_other_machine_columns(
    tmp_path,
) -> None:
    _demoted(tmp_path)
    sheet = tmp_path / "blind_sheet.json"
    eval_claims.export_sheet(tmp_path / "gate.db", sheet, blind=True)

    payload = json.loads(sheet.read_text(encoding="utf-8"))
    assert all(
        "machine_verdict_source" not in r and "machine_trust" not in r
        for r in payload["labels"]
    ), "the rows, not the instructions text, are what must be held back"
    sidecar = json.loads(
        eval_claims.sidecar_path(sheet).read_text(encoding="utf-8"))["machine"]
    assert any(v.get("machine_verdict_source") == "trust_gate" for v in sidecar.values())


def _mixed_source_sheet() -> dict:
    return {
        "labels": [
            {"claim_id": "a", "machine_verdict": "unsupported",
             "machine_verdict_source": "trust_gate", "human_verdict": "supported",
             "machine_trust": 0.60, "independent_sources": 2},
            {"claim_id": "b", "machine_verdict": "supported",
             "machine_verdict_source": "llm", "human_verdict": "supported",
             "machine_trust": 0.80, "independent_sources": 3},
            {"claim_id": "c", "machine_verdict": "unsupported",
             "machine_verdict_source": "llm", "human_verdict": "unsupported",
             "machine_trust": 0.10, "independent_sources": 2},
        ]
    }


def test_scoring_splits_agreement_between_the_model_and_the_gate(tmp_path) -> None:
    """Row `a` is the interesting one: human and model agreed, and the gate
    overruled. One agreement number buries that under "the system is 67% right".
    """
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps(_mixed_source_sheet(), ensure_ascii=False), encoding="utf-8")
    text, report = _score(path, tmp_path / "r.json")

    assert "按判定来源拆分" in text, text
    assert "trust_gate: n=1, agreement=0.000" in text, text
    assert "llm: n=2, agreement=1.000" in text, text
    assert report["by_verdict_source"]["trust_gate"]["n"] == 1
    assert report["accuracy"] == round(2 / 3, 4), "the overall number stays as it was"


def test_sheets_without_the_column_get_no_split_and_no_complaint(tmp_path) -> None:
    rows = [
        {k: v for k, v in r.items() if k != "machine_verdict_source"}
        for r in _mixed_source_sheet()["labels"]
    ]
    path = tmp_path / "plain.json"
    path.write_text(json.dumps({"labels": rows}, ensure_ascii=False), encoding="utf-8")
    text, report = _score(path, tmp_path / "r.json")

    assert "按判定来源拆分" not in text, text
    assert report["by_verdict_source"] == {}
