"""The labelling path has to end in a threshold, not just an agreement number.

`roc_thresholds()` existed and was unit-tested as a function, but nothing wired
it to the command the docs tell the maintainer to run after labelling -- and the
exported sheet did not even carry `T`, the quantity the thresholds are defined
over. So "tools are in place, only the labels are missing" was overstated: with
100 labels in hand the run would have produced agreement and still left θ hand
set. These tests cover the whole path through `scripts/eval_claims.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import eval_claims  # noqa: E402


def sheet(rows):
    return {"instructions": "…", "labels": rows}


def labelled(n_supported=6, n_unsupported=6):
    rows = []
    for i in range(n_supported):
        rows.append({
            "claim_id": f"s{i}", "machine_verdict": "supported",
            "human_verdict": "supported", "machine_trust": 0.70 + 0.04 * i,
            "independent_sources": 3,
        })
    for i in range(n_unsupported):
        rows.append({
            "claim_id": f"u{i}", "machine_verdict": "unsupported",
            "human_verdict": "unsupported", "machine_trust": 0.05 + 0.03 * i,
            "independent_sources": 1,
        })
    return sheet(rows)


def write(tmp_path: Path, payload) -> Path:
    path = tmp_path / "claims_labels.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_scoring_labelled_claims_also_suggests_thresholds(tmp_path: Path) -> None:
    out = tmp_path / "claims_results.json"
    text = eval_claims.score_sheet(write(tmp_path, labelled()), out)

    assert "θ 校准建议" in text, text
    report = json.loads(out.read_text(encoding="utf-8"))
    suggested = report["thresholds_suggested"]
    assert report["thresholds_suggested"] is not None and report["threshold_pairs"] == 12
    assert 0.0 < suggested["triage"] <= suggested["supported"] <= 1.0
    # Perfect agreement, yet macro-F1 is only 2/3: the metric averages over the
    # fixed three-label set, and this fixture contains no `contested` rows, so
    # that class scores 0. Documented in docs/evaluation.md -- a label batch
    # without contested examples must not be read as mediocre accuracy.
    assert report["accuracy"] == 1.0
    assert report["macro_f1"] == pytest.approx(2 / 3, abs=1e-3)


def test_a_sheet_without_T_values_says_so_instead_of_inventing_a_threshold(tmp_path: Path) -> None:
    rows = [
        {"claim_id": f"c{i}", "machine_verdict": v, "human_verdict": v, "independent_sources": 2}
        for i, v in enumerate(["supported", "unsupported"] * 4)
    ]
    out = tmp_path / "claims_results.json"
    text = eval_claims.score_sheet(write(tmp_path, sheet(rows)), out)

    assert "θ 校准：跳过" in text
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["thresholds_suggested"] is None and report["threshold_pairs"] == 0
    assert report["n"] == 8, "agreement still works without trust values"


def test_a_single_class_label_set_cannot_set_a_threshold(tmp_path: Path) -> None:
    """Fitting on one class would silently produce a confident, meaningless cut."""
    rows = [
        {"claim_id": f"o{i}", "machine_verdict": "supported", "human_verdict": "supported",
         "machine_trust": 0.9 - 0.01 * i, "independent_sources": 3}
        for i in range(5)
    ]
    text = eval_claims.score_sheet(write(tmp_path, sheet(rows)), tmp_path / "r.json")
    assert "θ 校准：跳过" in text


def test_the_export_queries_the_column_the_threshold_is_defined_over() -> None:
    """Guards the specific regression: `c.trust` dropped out of the SELECT."""
    source = (REPO_ROOT / "scripts" / "eval_claims.py").read_text(encoding="utf-8")
    export = source.split("def export_sheet")[1].split("def score_sheet")[0]
    assert "c.trust" in export, "the sheet query stopped selecting the claim T value"
    assert '"machine_trust": row["trust"]' in export

    score = source.split("def score_sheet")[1].split("def main")[0]
    assert "roc_thresholds(" in score, "scoring no longer fits thresholds"
