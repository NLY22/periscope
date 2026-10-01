"""The three import entries must be the same feature, not three near-copies.

spec §6.1 promises one fallback path reached three ways: MCP `hz_corpus_import`,
`POST /api/import` (the panel), and `scripts/import_corpus.py`. Every time that
path grew a knob, the entries drifted -- `--dry-run` landed on the CLI first with
a stricter parser than the write used, then on the panel, and the MCP tool was
left behind again. These checks make that drift loud rather than silent: they
assert the knobs match and that nobody re-implements the validation on the side.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MCP_SERVER = (REPO_ROOT / "src" / "mcp" / "server.py").read_text(encoding="utf-8")
MCP_SERVICE = (REPO_ROOT / "src" / "mcp" / "service.py").read_text(encoding="utf-8")
WEB_APP = (REPO_ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
PANEL_JS = (REPO_ROOT / "src" / "web" / "static" / "index.html").read_text(encoding="utf-8")
CLI = (REPO_ROOT / "scripts" / "import_corpus.py").read_text(encoding="utf-8")
MCP_GUIDE = (REPO_ROOT / "src" / "mcp" / "README.md").read_text(encoding="utf-8")


def _block(text: str, start: str) -> str:
    """The definition body that begins at `start`, up to the next top-level def."""
    index = text.index(start)
    tail = text[index + len(start):]
    stop = re.search(r"\n(?:async )?def |\n@|\nclass ", tail)
    return tail[: stop.start()] if stop else tail


def _signature(text: str, start: str) -> str:
    """Just the parameter list of a definition, never its body.

    Matching the whole body lets a docstring that still says `dry_run` keep the
    defence after the parameter is deleted -- the exact false green this file
    exists to prevent. Found this by trying to break my own guard: stripping
    the parameter left the string `dry_run` in the docstring, so the first
    version of the check did not bite.
    """
    open_at = text.index(start) + len(start)
    depth, end = 1, open_at
    while end < len(text) and depth:
        depth += text[end] == "(" or 0
        depth -= text[end] == ")" or 0
        end += 1
    return text[open_at:end]


def test_every_entry_can_preview_and_the_reproducers_keep_the_tier_knob() -> None:
    previews = {
        "hz_corpus_import": _signature(MCP_SERVER, "async def hz_corpus_import("),
        "service.corpus_import": _signature(MCP_SERVICE, "def corpus_import("),
        "POST /api/import": _block(WEB_APP, "async def import_export("),
        "periscope-import": CLI,
        "panel import button": _block(PANEL_JS, "async function doImport("),
    }
    for name, body in previews.items():
        assert "dry_run" in body or "--dry-run" in body, (
            f"{name} cannot preview an import; the entries have drifted apart again"
        )

    # `tiering` stays out of the panel on purpose: `marker` is the ablation arm
    # that reproduces the pre-P0 numbers, and offering it where a user uploads
    # evidence invites importing under the weaker rule.
    for name in ("hz_corpus_import", "service.corpus_import"):
        assert "tiering" in previews[name], f"{name} lost the tiering knob"
    assert "--tiering" in CLI, "the CLI is how the ablation arms are reproduced"


def test_the_preview_goes_through_the_shared_validation() -> None:
    """Three entry points, one validator -- a second implementation is a second truth."""
    assert "dry_run=dry_run" in _block(MCP_SERVICE, "def corpus_import("), (
        "the MCP service no longer delegates the preview to ingest.import_payload"
    )
    assert "dry_run=dry_run" in _block(WEB_APP, "async def import_export(")
    assert "preview_payload(" in CLI

    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in list((REPO_ROOT / "src").rglob("*.py")) + list((REPO_ROOT / "scripts").rglob("*.py"))
        if "_item_at(" in path.read_text(encoding="utf-8")
        and path.name != "ingest.py"
    ]
    assert not offenders, f"import validation happens outside src/corpus/ingest.py: {offenders}"


def test_mcp_guide_documents_the_preview() -> None:
    """An option a client cannot discover from the guide is an option it will not use."""
    section = MCP_GUIDE.split("hz_corpus_import")
    assert len(section) > 1, "the MCP guide stopped documenting the import tool"
    around = section[0] + section[1]
    assert "dry_run" in around, "hz_corpus_import's preview is undocumented in the MCP guide"
