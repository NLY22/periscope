"""CLI output paths and the labeling entry point must survive real locations.

`Path.relative_to` raises when a path is outside the base, so printing an
argument that way turned a finished run into a traceback - and on Windows two
drives make that the common case, not the corner. Same family: exporting a
labeling sheet from a corpus written before claim analysis used to surface
`sqlite3.OperationalError: no such table: claims`.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_claims  # noqa: E402

from src._cli import display_path  # noqa: E402
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
