"""The active package borrows only the legacy evidence kernel.

The canonical implementation lives in `periscope/`; the old `core/`,
`collectors/` and `topic.py` paths are compatibility entry points.
Collection and operations may borrow only A's evidence kernel
(models, corpus store, trust/sections/simhash, extractors, url security,
the CLI helper). Anything from `src.analysis`, `src.ai`, `src.research`,
`src.web`, `src.mcp`, `src.services`, `src.setup`, `src.processing` or
`src.orchestrator` is legacy product surface, not a B dependency.

`tools/` is deliberately not scanned: the claim diagnostics there are legacy
ops scripts that legitimately use `src.analysis`.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KERNEL = (
    "src._cli",
    "src.models",
    "src.corpus.store",
    "src.corpus.trust",
    "src.corpus.sections",
    "src.corpus.simhash",
    "src.extractors",
    "src.url_security",
    "src._scratch",
)


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.lineno, node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name


def _b_runtime_files():
    files = list((ROOT / "periscope").rglob("*.py"))
    return [p for p in files if p.name != "__init__.py"]


def _offenders(allowed):
    out = []
    for path in _b_runtime_files():
        for line, module in _imports(path):
            if not module.startswith("src"):
                continue
            if not any(module == a or module.startswith(a + ".") for a in allowed):
                out.append("%s:%d imports %s" % (path.relative_to(ROOT), line, module))
    return out


def test_core_and_topic_do_not_import_a_at_all():
    files = [path for folder in ("runtime", "search", "topics", "cli")
             for path in (ROOT / "periscope" / folder).rglob("*.py")]
    for path in files:
        if path.name == "__init__.py":
            continue
        bad = [(line, m) for line, m in _imports(path) if m.startswith("src")]
        assert not bad, "%s imports A: %s" % (path.name, bad)


def test_collectors_import_only_the_kernel():
    offenders = _offenders(KERNEL)
    assert not offenders, "collectors/ reached into legacy A:\n" + "\n".join(offenders)


def test_the_boundary_is_not_vacuous():
    # If the scanner sees no src imports at all, the rule could pass for the
    # wrong reason. `backfill_fulltext.py` must still import the kernel.
    seen = {
        module
        for path in _b_runtime_files()
        for _line, module in _imports(path)
        if module.startswith("src")
    }
    assert "src.corpus.store" in seen
    assert "src.models" in seen


def test_legacy_product_dependency_would_be_rejected(scratch_path, monkeypatch):
    forbidden = scratch_path / "forbidden.py"
    forbidden.write_text("from src.orchestrator import Orchestrator\n", encoding="utf-8")
    monkeypatch.setitem(_offenders.__globals__, "_b_runtime_files", lambda: [forbidden])
    assert any("src.orchestrator" in entry for entry in _offenders(KERNEL))
