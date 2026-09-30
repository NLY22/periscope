"""Guard rails that keep the documentation from drifting away from the code.

The docs in this repo make checkable claims: which config keys exist, what they
default to, how many MCP tools there are, and which tools are listed. Before
these tests, three of those claims were false at the same time (README said 22
tools, the MCP guide listed 21 of 26 and named a tool that does not exist,
`docs/configuration.md` documented none of the fork's four evidence blocks).
A prose fix without a test would rot again.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.models import (  # noqa: E402
    AnalysisConfig,
    CorpusConfig,
    ResearchConfig,
    RetrievalConfig,
)

CONFIG_DOC = REPO_ROOT / "docs" / "configuration.md"
MCP_DOC = REPO_ROOT / "src" / "mcp" / "README.md"
README = REPO_ROOT / "README.md"
SERVER = REPO_ROOT / "src" / "mcp" / "server.py"

EVIDENCE_BLOCKS = {
    "corpus": CorpusConfig,
    "analysis": AnalysisConfig,
    "research": ResearchConfig,
    "retrieval": RetrievalConfig,
}


def _section(doc: str, heading: str) -> str:
    r"""Text between `### \`heading\`` and the next heading of any level."""
    start = doc.index(f"### `{heading}`")
    tail = doc[start + len(f"### `{heading}`"):]
    stop = re.search(r"^#{2,3} ", tail, re.MULTILINE)
    return tail[: stop.start()] if stop else tail


def _rendered(default) -> str:
    """How a default should appear in the guide's table."""
    if isinstance(default, bool):
        return "`true`" if default else "`false`"
    if isinstance(default, str):
        return f"`\"{default}\"`" if default else '`""`'
    return f"`{default}`"


def _server_tools() -> set:
    return set(re.findall(r"^(?:async )?def (hz_\w+)", SERVER.read_text(encoding="utf-8"), re.M))


# ---------------------------------------------------------------- config blocks
@pytest.mark.parametrize("block", sorted(EVIDENCE_BLOCKS))
def test_every_evidence_config_field_is_documented(block: str) -> None:
    """A key that exists in the model but not in the guide is an invisible knob."""
    section = _section(CONFIG_DOC.read_text(encoding="utf-8"), block)
    missing = [name for name in EVIDENCE_BLOCKS[block].model_fields if f"`{name}`" not in section]
    assert not missing, f"docs/configuration.md §`{block}` does not document: {missing}"


def test_documented_defaults_match_the_models_exactly() -> None:
    """The defaults table must not be one release behind the models."""
    doc = CONFIG_DOC.read_text(encoding="utf-8")
    for block, cls in EVIDENCE_BLOCKS.items():
        section = _section(doc, block)
        for name, field in cls.model_fields.items():
            if field.is_required() or field.default is None:
                continue          # required or None-defaulted: no cell to match
            expected = _rendered(field.default)
            assert expected in section, (
                f"{block}.{name}: guide does not show default {expected}"
            )


def test_config_example_covers_the_evidence_blocks() -> None:
    """The example file is what users copy; a missing key there is a missing knob."""
    example = json.loads((REPO_ROOT / "data" / "config.example.json").read_text(encoding="utf-8"))
    for block in ("corpus", "analysis", "research"):
        assert block in example, f"data/config.example.json has no `{block}` block"
    assert "triage_min_trust" in example["analysis"], (
        "the P1 gate must be discoverable in the example config, not only in code"
    )


# -------------------------------------------------------------------- MCP tools
def test_mcp_guide_lists_every_registered_tool() -> None:
    tools = _server_tools()
    listed = set(re.findall(r"`(hz_\w+)`", MCP_DOC.read_text(encoding="utf-8")))
    assert not tools - listed, f"undocumented tools: {sorted(tools - listed)}"


def test_mcp_guide_invents_no_tool() -> None:
    """The guide once named `hz_claims`, which has never existed."""
    tools = _server_tools()
    listed = set(re.findall(r"`(hz_\w+)`", MCP_DOC.read_text(encoding="utf-8")))
    assert not listed - tools, f"non-existent tools in the guide: {sorted(listed - tools)}"


def test_readme_tool_count_matches_the_server() -> None:
    """README quoted 22 while the server registered 26."""
    tools = _server_tools()
    readme = README.read_text(encoding="utf-8")
    quoted = {int(n) for n in re.findall(r"(\d+)\s*个工具", readme)}
    assert quoted == {len(tools)}, f"README says {sorted(quoted)}; server registers {len(tools)}"


# ---------------------------------------------------------------- fork identity
_UPSTREAM_CONTACTS = ("thysrael@gmail.com", "thysrael@163.com")
_FORK_DOC = re.compile(r"本 fork|this fork", re.IGNORECASE)


@pytest.mark.parametrize("name", ["SECURITY.md", "CODE_OF_CONDUCT.md"])
def test_fork_contact_banner_comes_before_the_upstream_address(name: str) -> None:
    """Both files were pure upstream, so every report went to a stranger's inbox.

    The rule is not "never mention the upstream address" — it is that a reader
    must be told whose address it is before they can act on it.
    """
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    first_contact = min(
        (text.index(c) for c in _UPSTREAM_CONTACTS if c in text),
        default=None,
    )
    assert first_contact is not None, f"{name} no longer carries the upstream contact"
    header = text[:first_contact]
    assert _FORK_DOC.search(header), (
        f"{name}: the fork/upstream distinction must appear before any contact address"
    )


def test_contributing_guide_is_not_the_upstream_one() -> None:
    """CONTRIBUTING.md used to be upstream verbatim: no test command, no invariant,
    and it sent source suggestions to a site this fork does not run."""
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "uv run pytest" in text, "contributor duties must include the actual test command"
    assert "test_tier_guard" in text, "the marker ban is the fork's core invariant; it must be documented"
    assert "没有 CI" in text or "不执行" in text, (
        "this platform does not run GitHub workflows; contributors must be told to verify locally"
    )


# ------------------------------------------------------------------- stale text
_HISTORICAL_CONTEXT = (
    "banned", "deleted", "legacy", "not a text marker", "反解", "早期", "不再", "P0 之前", "复现",
)


def test_no_doc_teaches_the_deleted_marker_protocol() -> None:
    """`--- Top Comments ---` is banned in `src/scrapers/` by a guard test.

    The guides may still name it while explaining what was replaced; they may
    not tell anyone to write it. So the rule is: every mention has to sit in a
    line that says, in words, that this is the old or forbidden way.
    """
    for path in (README, CONFIG_DOC, REPO_ROOT / "docs" / "scrapers.md",
                 REPO_ROOT / "docs" / "retrieval.md"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "--- Top Comments ---" in line:
                assert any(hint in line.lower() for hint in _HISTORICAL_CONTEXT), (
                    f"{path.name} names the deleted marker without calling it legacy: "
                    f"{line.strip()[:90]}"
                )
