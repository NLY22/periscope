"""A run must not write into the repository's own tree.

Inherited code copied every generated summary into `docs/_posts/` with Jekyll
front matter, because the project it came from published a GitHub Pages site
from the repo itself. On this platform nothing builds that site, so the effect
was a worktree that turns dirty after every ordinary run -- which contradicts
what `docs/selftest.md` 档 0 tells a reader to expect (`git status -s` empty).

The obvious guard would be a test that runs the pipeline and looks at the
filesystem, but no harness here drives the summary stage without a model and a
config, so the check is structural: no source file may open or create a path
under the repo's own documentation or source tree. It is paired with a synthetic
snippet proving the pattern is not decorative.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Writes into a tracked directory: an open(..., "w") / mkdir / write_text whose
# target starts from the repo's own docs/, src/ or scripts/.
REPO_TREE_WRITE = re.compile(
    r"""(?:open|Path)\(\s*[f]?["'](?:docs|src|scripts)/"""
    r"""|(?:docs|src|scripts)/[\w\-/{}]*["']\s*\)?\s*\.mkdir"""
)


def _sources() -> list[Path]:
    return [p for d in ("src", "scripts") for p in (REPO_ROOT / d).rglob("*.py")]


def _offenders(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if REPO_TREE_WRITE.search(line)]


def test_no_source_file_writes_into_the_repo_tree() -> None:
    offenders = {
        str(path.relative_to(REPO_ROOT)): found
        for path in _sources()
        if (found := _offenders(path.read_text(encoding="utf-8", errors="replace")))
    }
    assert not offenders, (
        "generated output belongs under the storage data dir, not the repository: "
        f"{offenders}"
    )


def test_the_pattern_rejects_a_planted_write() -> None:
    """Bite proof: the same line the deleted feature used has to be caught."""
    planted = 'posts_dir = Path("docs/_posts")\nposts_dir.mkdir(parents=True, exist_ok=True)\n'
    assert _offenders(planted), "the repo-tree-write pattern stopped matching"
    assert _offenders('summary_path = self.storage.save_daily_summary(today, summary)\n') == []
