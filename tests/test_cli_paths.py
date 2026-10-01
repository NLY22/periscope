"""CLI output paths, the console code page, and the labeling entry point.

Two crashes found the same way: `Path.relative_to` raises when a printed path
is outside the repository (repo on `D:`, temp on `C:` - the ordinary case here),
and printing a warning glyph that cp936 cannot encode killed
`eval_claims.py --score` *after* it had written its results, on the one command
the docs tell the maintainer to run once the labels exist.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_claims  # noqa: E402

from src._cli import display_path, force_utf8_output  # noqa: E402
from src.analysis.claims import ClaimStore  # noqa: E402
from src.corpus.store import Corpus  # noqa: E402


def test_display_path_is_relative_only_when_it_really_is_inside() -> None:
    assert display_path(REPO_ROOT / "data" / "x.json", REPO_ROOT) == str(
        Path("data") / "x.json"
    )
    # The real-world case: on this machine the repo is on D: and the temp
    # directory is on C:, so `relative_to` raises rather than being merely ugly.
    outside = Path(tempfile.gettempdir()).resolve() / "labels.json"
    assert not str(outside).lower().startswith(str(REPO_ROOT).lower())
    assert display_path(outside, REPO_ROOT) == str(outside)
    assert display_path(outside) == str(outside)


def test_exporting_to_a_path_outside_the_repo_does_not_crash(tmp_path, monkeypatch, capsys):
    """The bug was in the success message: the export had already finished."""
    db = tmp_path / "corpus.db"
    ClaimStore(Corpus(db))  # creates the claims table, no rows yet
    sheet = tmp_path / "labels.json"

    monkeypatch.setattr(sys, "argv", ["eval_claims.py", "--export", str(db),
                                      "--sheet", str(sheet), "--blind"])
    assert eval_claims.main() == 0

    printed = capsys.readouterr().out
    assert str(sheet) in printed and str(db) not in printed
    assert eval_claims.sidecar_path(sheet).exists(), "blind export still wrote its sidecar"


def test_exporting_from_a_corpus_without_claims_says_what_to_run(tmp_path) -> None:
    db = tmp_path / "bare.db"
    Corpus(db)  # items layer only - no claims table

    try:
        eval_claims.export_sheet(db, tmp_path / "labels.json")
    except SystemExit as raised:
        message = str(raised)
        assert "claims" in message and "periscope" in message, message
    else:
        raise AssertionError("a bare corpus must not fall through to a SQL error")


def test_no_script_prints_paths_with_relative_to() -> None:
    offenders = [
        path.name for path in (REPO_ROOT / "scripts").glob("*.py")
        if "relative_to(REPO_ROOT)" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"use `_cli.display_path`, which tolerates outside paths: {offenders}"


def test_every_printing_script_declares_its_output_encoding() -> None:
    """A new script that prints user text inherits the same crash otherwise."""
    unguarded = [
        path.name for path in sorted((REPO_ROOT / "scripts").glob("*.py"))
        if "print(" in path.read_text(encoding="utf-8")
        and "force_utf8_output" not in path.read_text(encoding="utf-8")
    ]
    assert not unguarded, f"scripts printing without a code-page guard: {unguarded}"


LABELLED = {
    "labels": [
        {"claim_id": "a", "machine_verdict": "supported", "human_verdict": "supported",
         "machine_trust": 0.72, "independent_sources": 2},
        {"claim_id": "b", "machine_verdict": "unsupported", "human_verdict": "contested",
         "machine_trust": 0.31, "independent_sources": 1},
    ]
}


def test_scoring_survives_a_chinese_windows_console(tmp_path) -> None:
    """End to end, in a real subprocess with the console encoding pinned."""
    import os
    import subprocess

    sheet = tmp_path / "labels.json"
    sheet.write_text(json.dumps(LABELLED, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "results.json"

    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "eval_claims.py"),
         "--score", str(sheet), "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "gbk"}, cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    assert "macro-F1" in proc.stdout, proc.stdout[-500:]
    assert out.exists(), "the result file is the point of the run"
