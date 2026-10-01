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


def three_class_rows():
    """The same fixture plus `contested` examples - the class a labeler forgets."""
    rows = labelled()["labels"]
    rows += [
        {"claim_id": f"v{i}", "machine_verdict": "contested", "human_verdict": "contested",
         "machine_trust": 0.5, "independent_sources": 2}
        for i in range(3)
    ]
    return sheet(rows)


def test_a_missing_verdict_class_is_said_before_the_number_it_confuses(tmp_path: Path) -> None:
    """macro-F1 averages over three fixed labels, so an absent class is not a
    low score - it is an unreadable one, and the sheet above the fold looks
    like a mediocre result."""
    out = tmp_path / "r.json"
    text = eval_claims.score_sheet(write(tmp_path, labelled()), out)

    assert "不可解读" in text, text
    assert "contested" in text.splitlines()[0]
    assert "0.667" in text, "the ceiling it would hit at perfect agreement is the useful part"
    assert text.index("不可解读") < text.index("macro-F1="), "the caveat must precede the number"

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["macro_f1_interpretable"] is False
    assert report["class_coverage"] == {"supported": 6, "contested": 0, "unsupported": 6}


def test_a_class_covering_label_batch_is_not_warned_about(tmp_path: Path) -> None:
    """Prove the check is about coverage, not about the tool being pessimistic."""
    out = tmp_path / "r.json"
    text = eval_claims.score_sheet(write(tmp_path, three_class_rows()), out)

    assert "不可解读" not in text, text
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["macro_f1_interpretable"] is True
    assert all(v > 0 for v in report["class_coverage"].values())
    assert report["macro_f1"] == pytest.approx(1.0, abs=1e-3), "perfect agreement on all three"


def test_the_export_tells_the_labeler_about_class_coverage_up_front() -> None:
    """The warning is useless if it only appears after 100 rows are labelled."""
    source = (REPO_ROOT / "scripts" / "eval_claims.py").read_text(encoding="utf-8")
    export = source.split("def export_sheet")[1].split("def score_sheet")[0]
    assert "instructions += COVERAGE_NOTE" in export, (
        "the sheet stopped carrying the coverage requirement"
    )
    assert export.index("instructions += COVERAGE_NOTE") > export.index("if blind:"), (
        "the note must be appended after the blind/non-blind branch, so both get it"
    )
    assert "三个类别都要标到样本" in eval_claims.COVERAGE_NOTE
    assert "0.667" in eval_claims.COVERAGE_NOTE, "the reason belongs on the sheet, not only in the docs"


# --------------------------------------------------------------- blind labelling
# The non-blind shape was a documented caveat ("一致率因此偏乐观") plus an
# instruction to blank two columns by hand. Anchored agreement is the number a
# report would quote, so the supported path has to be the unanchored one.

def score_file(path: Path, out: Path):
    text = eval_claims.score_sheet(path, out)
    return text, json.loads(out.read_text(encoding="utf-8"))


def blind_payload(rows):
    return {"instructions": "盲标", "blind": True, "labels": rows}


def split_into_blind_pair(tmp_path: Path, payload, name: str = "blind.json"):
    """Write what `--blind` would write: a sheet without machine columns."""
    rows = [dict(r) for r in payload["labels"]]
    machine = eval_claims.split_machine_columns(rows)
    sheet_path = tmp_path / name
    sheet_path.write_text(json.dumps(blind_payload(rows), ensure_ascii=False), encoding="utf-8")
    sidecar = eval_claims.sidecar_path(sheet_path)
    sidecar.write_text(json.dumps({"machine": machine}, ensure_ascii=False), encoding="utf-8")
    return sheet_path, rows, machine


def test_a_blind_sheet_carries_no_machine_columns_at_all(tmp_path: Path) -> None:
    _, rows, machine = split_into_blind_pair(tmp_path, three_class_rows())
    assert all("machine_verdict" not in r and "machine_trust" not in r for r in rows)
    assert not any("machine_" in json.dumps(r, ensure_ascii=False) for r in rows), rows
    assert machine and all("machine_verdict" in v for v in machine.values())


def test_blind_scoring_reproduces_the_visible_numbers(tmp_path: Path) -> None:
    """The sidecar must restore exactly what it took out - or the blind path is
    quietly a different measurement, and the two are not comparable."""
    visible_path = tmp_path / "visible.json"
    visible_path.write_text(json.dumps(three_class_rows(), ensure_ascii=False), encoding="utf-8")
    visible_text, visible = score_file(visible_path, tmp_path / "visible.out.json")
    sheet_path, _, _ = split_into_blind_pair(tmp_path, three_class_rows())
    blind_text, blind = score_file(sheet_path, tmp_path / "blind.out.json")

    for key in ("n", "accuracy", "macro_f1", "class_coverage", "threshold_pairs", "thresholds_suggested"):
        assert blind[key] == visible[key], key
    assert blind["blind"] is True and visible["blind"] is False
    assert "盲标（machine_* 由副表合入）" in blind_text, blind_text
    assert "非盲标" in visible_text


def test_a_blind_sheet_without_its_sidecar_fails_loudly(tmp_path: Path) -> None:
    sheet_path, rows, _ = split_into_blind_pair(tmp_path, three_class_rows())
    eval_claims.sidecar_path(sheet_path).unlink()

    with pytest.raises(SystemExit) as raised:
        eval_claims.score_sheet(sheet_path, tmp_path / "r.json")
    assert "--machine" in str(raised.value)


def test_an_unlabelled_sheet_is_not_mistaken_for_a_blind_one(tmp_path: Path) -> None:
    """Both shapes lack `machine_verdict`, and only one of them has a sidecar.

    Guessing from the columns alone turned "nobody has labelled this yet" into a
    crash with a message about the wrong thing.
    """
    rows = [{"claim_id": f"c{i}", "human_verdict": None} for i in range(3)]
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(sheet(rows), ensure_ascii=False), encoding="utf-8")

    text = eval_claims.score_sheet(path, tmp_path / "r.json")
    assert "无法计分" in text and "--machine" not in text, text


def test_the_blind_option_is_reachable_from_the_command_the_docs_give() -> None:
    source = (REPO_ROOT / "scripts" / "eval_claims.py").read_text(encoding="utf-8")
    export = source.split("def export_sheet")[1].split("def score_sheet")[0]
    main = source.split("def main")[1]
    assert '"--blind"' in main and "blind=args.blind" in main
    assert '"--machine"' in main and "args.machine" in main
    assert "--blind" in export, "the non-blind instructions must point at the blind option"
