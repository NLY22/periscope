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


# ------------------------------------------------------- entry points and CI
def test_every_console_script_is_documented() -> None:
    """Six commands are the whole user-facing surface; an undocumented one is invisible."""
    scripts = dict(
        re.findall(r"^([a-z][a-z-]+)\s*=\s*\"([\w.]+:[\w]+)\"",
                   (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    )
    assert scripts, "no [project.scripts] found — the guard would pass vacuously"
    readme = README.read_text(encoding="utf-8")
    missing = [name for name in scripts if name not in readme]
    assert not missing, f"README never mentions the commands: {missing}"


def test_github_workflow_mentions_say_they_do_not_run_here() -> None:
    """AtomGit executes no GitHub-syntax workflows, so a doc must not imply otherwise.

    The unit of judgement is the paragraph, not a character window: a mention is
    fine when *its own* paragraph says it is GitHub-only. "disabled" is not an
    acceptable caveat either — it is part of the filename, and telling someone to
    rename a file that will never run here is exactly the advice this catches.
    """
    mentions = ("daily-summary.yml", "deploy-docs.yml", "workflows/tests.yml",
                ".github/workflows")
    caveat = re.compile(
        r"不生效|不执行|不跑|check_tasks_num|GitHub[ -]syntax|no GitHub|does not run"
        r"|never runs|only on GitHub|GitHub 语法",
        re.IGNORECASE,
    )
    offenders = []
    for path in (README, CONFIG_DOC, REPO_ROOT / "docs" / "retrieval.md"):
        text = path.read_text(encoding="utf-8")
        paragraphs = text.split("\n\n")
        for mention in mentions:
            for number, paragraph in enumerate(paragraphs):
                if mention in paragraph and not caveat.search(paragraph):
                    offenders.append((path.name, mention, number))
    assert not offenders, f"workflow mentions whose own paragraph lacks the caveat: {offenders}"


def test_the_image_ships_the_harnesses_the_docs_tell_people_to_run() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts" in dockerfile, (
        "docs/evaluation.md shows a container command for the eval harnesses; "
        "without scripts/ in the image that command cannot work"
    )


# ------------------------------------------------------------------- changelog
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def test_changelog_does_not_invent_releases() -> None:
    """No tag exists and pyproject still says 0.1.0, so nothing may be 'released'."""
    text = CHANGELOG.read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', (REPO_ROOT / "pyproject.toml")
                        .read_text(encoding="utf-8"), re.M).group(1)
    assert version in text, f"CHANGELOG never mentions the current version {version}"
    assert "git tag" in text, "it must state that no tag exists yet"
    for forbidden in ("## 1.0.0", "Released", "已发布 1.", "Stable release"):
        assert forbidden not in text, f"CHANGELOG claims a release: {forbidden}"


def test_changelog_facts_match_the_code() -> None:
    """The numbers a reader would quote from it are checked against their sources."""
    from src.corpus.store import SCHEMA_VERSION

    text = CHANGELOG.read_text(encoding="utf-8")
    assert f"schema v{SCHEMA_VERSION}" in text, (
        f"corpus schema is v{SCHEMA_VERSION}; the changelog must not lag behind"
    )
    assert f"{len(_server_tools())}" in text, "the MCP tool count in the changelog is stale"
    for pull in ("#3", "#4", "#5", "#6"):
        assert pull in text, f"open merge request {pull} is missing from the changelog"


def test_changelog_keeps_the_uncalibrated_threshold_caveat() -> None:
    """The one 'capability built, effect not claimed' block must stay labelled.

    A changelog is the document people quote in a README or a slide; if the
    caveat lives only in docs/evaluation.md it gets lost on the way there.
    """
    text = CHANGELOG.read_text(encoding="utf-8")
    assert "50–100" in text and "默认" in text and "关闭" in text
    assert "triage_min_trust" in text


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


# ------------------------------------------------------------- architecture.md
# docs/architecture.md is spec §8's structural diagrams. A picture is easier to
# admire and easier to let rot than a table of config keys, so the guard is
# bidirectional: everything the code calls a status / verb / table has to be
# drawn, and nothing may be drawn that the code does not have a name for.
ARCH_DOC = REPO_ROOT / "docs" / "architecture.md"
SESSION_SRC = REPO_ROOT / "src" / "research" / "session.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code_vocab() -> set[str]:
    """Every identifier-shaped token that appears in shipped source.

    Broad on purpose: it cannot tell a live symbol from one that only survives
    in a docstring, but it does reject a name that was never in the code at all
    — which is the mistake this guard was written for.
    """
    files = (
        list((REPO_ROOT / "src").rglob("*.py"))
        + list((REPO_ROOT / "scripts").rglob("*.py"))
        + [REPO_ROOT / "src" / "web" / "static" / "index.html"]
    )
    vocab: set[str] = set()
    for path in files:
        vocab.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _read(path)))
    return vocab


def _doc_backticked(text: str) -> list[str]:
    return [t.strip() for t in re.findall(r"`([^`\n]+)`", text)]


def _doc_identifiers(text: str) -> list[str]:
    return [
        t for t in _doc_backticked(text)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", t) and "..." not in t
        # `UC...` / `past_...` are elisions readers are meant to complete, not symbols.
    ]


def _dataclass_field_values(class_name: str, field_name: str) -> list[str]:
    """Read the `# a | b | c` comment that documents a status-like string field.

    Scoped to one dataclass body because three of them carry a `status` field
    with a different vocabulary each.
    """
    text = _read(SESSION_SRC)
    marker = f"class {class_name}:"
    assert marker in text, f"{marker} is gone from session.py"
    body = text.split(marker, 1)[1].split("@dataclass", 1)[0]
    match = re.search(rf'{field_name}: str(?: = "[\w]*")?\s*#\s*([\w |]+)', body)
    assert match, f"{class_name}.{field_name} lost the comment that enumerates it"
    return [v.strip() for v in match.group(1).split("|") if v.strip()]


def _session_status_values() -> list[str]:
    return _dataclass_field_values("Session", "status")


def _subquestion_status_values() -> list[str]:
    return _dataclass_field_values("SubQuestion", "status")


def _turn_roles() -> list[str]:
    return _dataclass_field_values("Turn", "role")


def _corpus_tables() -> tuple[set[str], set[str]]:
    """(plain tables, fts5 virtual tables) created anywhere under src/.

    `llm_cache` is dropped: ResponseCache opens its own file, so it is not part
    of the corpus database the diagram describes.
    """
    plain: set[str] = set()
    virtual: set[str] = set()
    for path in (REPO_ROOT / "src").rglob("*.py"):
        text = _read(path)
        plain.update(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", text))
        virtual.update(re.findall(r"CREATE VIRTUAL TABLE IF NOT EXISTS (\w+)", text))
    return plain - {"llm_cache"}, virtual


def _ladder_actions() -> list[str]:
    """The widening steps, read off the lines that build the ladder list."""
    found: list[str] = []
    for line in _read(SESSION_SRC).splitlines():
        if "ladder" in line:
            found += re.findall(r'"([a-z_]+)"', line)
    deduped = list(dict.fromkeys(found))
    assert deduped, "the widen ladder moved out of the lines this guard reads"
    return deduped


def test_architecture_doc_exists_and_declares_its_three_diagrams() -> None:
    text = _read(ARCH_DOC)
    assert text.count("```mermaid") == 3, "spec §8 charts 1-3 are one diagram each"
    for kind in ("flowchart", "stateDiagram-v2", "sequenceDiagram"):
        assert kind in text, f"{kind} is missing from the architecture doc"


def test_architecture_doc_draws_every_session_status() -> None:
    text = _read(ARCH_DOC)
    statuses = _session_status_values()
    assert "awaiting_user" in statuses, "the parked state is load-bearing"
    for status in statuses:
        assert status in text, f"session status {status!r} is not drawn"
    for role in _turn_roles():
        assert role in text, f"turn role {role!r} is not named"
    for status in _subquestion_status_values():
        assert status in text, f"sub-question status {status!r} is not named"
    from src.research.drafts import REQUEST_STATUSES

    for status in REQUEST_STATUSES:
        assert status in text, f"request status {status!r} is not named"


def test_architecture_doc_draws_every_research_verb() -> None:
    from typing import get_args

    from src.research.moves import Move

    text = _read(ARCH_DOC)
    for verb in get_args(Move):
        assert verb.__name__ in text, f"move {verb.__name__} is not drawn"


def test_architecture_doc_table_list_matches_the_schema_exactly() -> None:
    text = _read(ARCH_DOC)
    plain, virtual = _corpus_tables()
    rows = re.findall(r"^\| `(\w+)` \| (普通|FTS5 虚表) \|", text, re.M)
    named = {name for name, _ in rows}
    assert named == plain | virtual, (
        f"doc lists {sorted(named)} but the schema creates {sorted(plain | virtual)}"
    )
    for table in plain | virtual:
        assert table in text, f"table {table} never appears in the doc"
    assert len([k for _, k in rows if k == "普通"]) == len(plain) == 12
    assert len([k for _, k in rows if k == "FTS5 虚表"]) == len(virtual) == 2


def test_architecture_doc_names_the_probe_verdicts_and_the_widen_ladder() -> None:
    from src.sources.reachability import VERDICTS

    text = _read(ARCH_DOC)
    for verdict in VERDICTS:
        assert verdict in text, f"reachability verdict {verdict!r} is missing"
    for action in _ladder_actions():
        assert action in text, f"widen-ladder step {action!r} is missing"


def test_architecture_doc_cites_a_real_source_family_count() -> None:
    from src.models import SOURCE_SPECS

    text = _read(ARCH_DOC)
    assert f"{len(SOURCE_SPECS)} 个已注册源族" in text, (
        "SOURCE_SPECS changed size; the diagram's family count is stale"
    )


def _unknown_doc_tokens(text: str, vocab: set[str]) -> list[str]:
    """Code-shaped backticked tokens the shipped source has no name for.

    A dotted token passes only when every part exists, so `Corpus.add_items` is
    checked as a pair while `Session.statusq` would be reported.
    """
    unknown = []
    for token in _doc_identifiers(text):
        if token in vocab or all(part in vocab for part in token.split(".")):
            continue
        unknown.append(token)
    return unknown


def test_architecture_doc_backticks_only_names_that_exist_in_code() -> None:
    unknown = _unknown_doc_tokens(_read(ARCH_DOC), _code_vocab())
    assert not unknown, f"docs/architecture.md names symbols the code does not have: {unknown}"


def test_the_architecture_guard_rejects_a_made_up_symbol() -> None:
    """The vocabulary check has to bite, not just pass.

    It was written because a diagram cited `SOURCE_REGISTRY` as if it were a
    guess; that name is real (src/models.py derives it from SOURCE_SPECS), so
    the check is pinned here against names that are genuinely absent.
    """
    vocab = _code_vocab()
    for real in ("SOURCE_SPECS", "SOURCE_REGISTRY", "SCRAPER_BINDINGS"):
        assert real in vocab, f"{real} should be found in the shipped source"
    probe = (
        "the panel reads `ResearchPanel` from `Corpus.not_a_column` "
        "and `SOURCE_REGISTRY_V2`"
    )
    assert _unknown_doc_tokens(probe, vocab) == [
        "ResearchPanel",
        "Corpus.not_a_column",
        "SOURCE_REGISTRY_V2",
    ]


def test_architecture_doc_paths_point_at_real_files_and_tests() -> None:
    text = _read(ARCH_DOC)
    for token in _doc_backticked(text):
        for path_part in re.findall(r"[A-Za-z0-9_./-]+\.py", token):
            target = REPO_ROOT / path_part
            assert target.exists(), f"{path_part} referenced by the doc does not exist"
        if "::" in token:
            file_part, _, test_name = token.partition("::")
            target = REPO_ROOT / file_part
            assert target.exists(), f"{file_part} does not exist"
            assert f"def {test_name}" in _read(target), f"{test_name} is not in {file_part}"


def test_readme_links_every_top_level_doc() -> None:
    """A guide nobody can reach from the front page is not documentation.

    `docs/index.md` is exempt: it is the GitHub Pages site home, and this
    platform never builds it (see the workflow guards above).
    """
    readme = _read(README)
    unlinked = [
        path.name for path in sorted((REPO_ROOT / "docs").glob("*.md"))
        if path.name != "index.md" and path.name not in readme
    ]
    assert not unlinked, f"docs not linked from the README: {unlinked}"


def test_site_index_links_the_same_pages_in_both_languages() -> None:
    """docs/index.md carries a Chinese and an English doc list.

    Editing one and forgetting the other is the obvious failure, and nothing
    but a test would notice -- the site is not built on this platform.
    """
    text = _read(REPO_ROOT / "docs" / "index.md")

    def links_after(heading: str) -> set[str]:
        block = text.split(heading, 1)[1]
        block = block.split("\n## ", 1)[0]
        return set(re.findall(r"\]\(([^)\s]+)\)", block))

    zh, en = links_after("## 文档"), links_after("## Documentation")
    assert zh == en, f"zh-only={sorted(zh - en)} en-only={sorted(en - zh)}"
    assert "architecture" in zh, "the diagram page has to be reachable from the site home"


def test_every_image_referenced_in_docs_exists() -> None:
    """A missing asset is the quietest documentation bug: the page still renders.

    Both spellings are checked because the README uses `<img>` for its sized
    pictures while the guides use Markdown image syntax.
    """
    targets = [README, *sorted((REPO_ROOT / "docs").glob("*.md"))]
    broken = []
    for path in targets:
        text = _read(path)
        refs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text)
        refs += re.findall(r'<img src="([^"]+)"', text)
        for ref in refs:
            if ref.startswith(("http://", "https://", "data:", "#")):
                continue
            base = REPO_ROOT if ref.startswith("/") else path.parent
            if not (base / ref.lstrip("/")).exists():
                broken.append(f"{path.name} -> {ref}")
    assert not broken, f"embedded images that are not in the repository: {broken}"


# ------------------------------------------------------------- the ablation table
EVAL_DOC = REPO_ROOT / "docs" / "evaluation.md"
RESULTS_JSON = REPO_ROOT / "data" / "eval" / "results.json"
_METRIC_KEYS = {
    "recall@5": "recall@5",
    "recall@10": "recall@10",
    "precision@5": "precision@5",
    "nDCG@10": "ndcg@10",
    "MRR": "mrr",
}


def _ablation_table(text: str) -> tuple[list[str], list[list[str]]]:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| 配置 |"))
    header = [c.strip() for c in lines[start].strip("|").split("|")]
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return header, rows


def _table_mismatches(rows: list[list[str]], configs: list[dict]) -> list[str]:
    """Compare the doc's ablation rows against the stored run, cell by cell."""
    problems: list[str] = []
    for cells, config in zip(rows, configs):
        letter = cells[0].split()[0]
        if not config["name"].startswith(letter):
            problems.append(f"{config['name']!r} is not row {letter}")
        for column, printed in zip(_METRIC_KEYS, cells[1:]):
            actual = f"{config['metrics'][_METRIC_KEYS[column]]:.3f}"
            if actual != printed.replace("**", ""):
                problems.append(
                    f"row {letter}, {column}: docs/evaluation.md says {printed}, "
                    f"results.json says {actual}"
                )
    return problems


def test_ablation_table_matches_the_stored_eval_run() -> None:
    """The headline numbers were, until now, checked only by eye.

    They are this project's most-quoted table, and `data/eval/results.json`
    sits in the repository right next to it -- so a mismatch is a bug someone
    can actually catch, not a judgement call.
    """
    text = _read(EVAL_DOC)
    header, rows = _ablation_table(text)
    assert header == ["配置", *_METRIC_KEYS], "the ablation table changed shape"

    stored = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    configs = stored["configs"]
    assert len(rows) == len(configs), f"doc has {len(rows)} rows, results.json has {len(configs)}"

    assert not _table_mismatches(rows, configs), "; ".join(_table_mismatches(rows, configs))


def test_ablation_table_names_the_tiering_the_run_recorded() -> None:
    """Every quoted number has to be traceable to a tiering arm.

    `--tiering=marker` reproduces the A/B rows; if the committed run is from the
    other arm, the reproduction command in the doc is wrong and must be fixed
    rather than left to read plausibly.
    """
    stored = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    assert f'--tiering={stored["tiering"]}' in _read(EVAL_DOC)


def test_the_ablation_guard_rejects_a_stale_number() -> None:
    """The table check has to bite: a rounded digit off by one is what rot looks like."""
    stored = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    rows = _ablation_table(_read(EVAL_DOC))[1]
    assert not _table_mismatches(rows, stored["configs"])

    tampered = [row[:] for row in rows]
    tampered[1][4] = "0.851"          # B's nDCG@10, one unit in the last place
    tampered[3][2] = "**1.00**"        # D's recall@10, right value, wrong precision
    problems = _table_mismatches(tampered, stored["configs"])
    assert len(problems) == 2, problems
    assert "row B, nDCG@10" in problems[0] and "row D, recall@10" in problems[1]


# --------------------------------------------------- inherited text vs fork policy
COOKIE_DOC = REPO_ROOT / "docs" / "twitter-cookies.md"
HUB_DOC = REPO_ROOT / "docs" / "horizon-hub-design.md"


def test_cookie_guide_states_the_collection_boundary() -> None:
    """Upstream wrote this guide as a multi-account "防封" recipe.

    SECURITY.md in the same repository excludes account pools, so the two
    cannot both stay true. The guide now separates what the code does from what
    this fork will support, and still warns about the failure a stale second
    export causes even for one account.
    """
    text = _read(COOKIE_DOC)
    assert "防封" not in text, "cookie guide sells evasion as a feature"
    assert "多账号轮询" not in text, "the pool-as-stability section is back"
    assert "SECURITY.md" in text, "the boundary has to be cited, not implied"
    assert "一个账号" in text
    assert "warm-up failed" in text, "the single-account pitfall must stay documented"


def test_unimplemented_upstream_proposal_is_labelled_everywhere_it_is_linked() -> None:
    """A proposal that reads like architecture is the most misleading doc type."""
    banner = "\n".join(_read(HUB_DOC).splitlines()[:8])
    assert "not implemented" in banner.lower(), "HorizonHub must be labelled a proposal up top"
    row = next(line for line in _read(README).splitlines() if "horizon-hub-design.md" in line)
    assert "未实现" in row and "没有对应代码" in row, f"README row: {row}"


# ------------------------------------------------- repo-wide symbol existence net
# The same check that caught a symbol in docs/architecture.md is worth applying to
# every guide. It needs two honest extensions, because the docs legitimately name
# things that do not live in `src/`:
#   - configuration files and repository files (env var names, compose keys, .md paths)
#   - **external** identifiers -- Twitter cookie keys, OpenBB provider names, pydantic
#     API, git refs, cron. Those go in a stated list, each with its reason, so the day
#     someone removes `ct0` from the code the doc stops being excused for it.
# Suffix shorthands (`hz_research_step` / `_draft` / `_edit`) are accepted only when
# some real identifier ends with them, which is a rule rather than an allowance.
_EXTERNAL_IDENTIFIERS = {
    "auth_token", "ct0", "twid",   # Twitter cookie names the user exports
    "benzinga", "yfinance",        # OpenBB provider names, chosen by the user
    "model_fields", "model_validator",  # pydantic API
    "HEAD", "HEAD~1", "main",      # git refs quoted in contribution docs
    "cron",                        # the scheduler this platform can actually offer
    "check_tasks_num",             # an AtomGit API response field we quote
    "MyExtractor", "MyExtractorConfig",  # placeholder class in the extractor how-to
    "F12",                         # a keyboard key, not code
    "periscope1123.top",           # the demo domain mentioned in the README
    "llama3.1",                    # a model name a user would type
    "past_7_days",                 # an OSSInsight period value the guide calls out as broken
    "x_cookies_stale.json",        # hypothetical stale export in the cookie guide
    "hz_claims",                   # named in the changelog precisely because it never existed
    "SOURCE_REGISTRY_V2",          # named in the changelog as the invented token a guard rejects
}

# Scoped on purpose: the guides and the contributor-facing files describe the
# repository as it is, so every symbol they quote must exist. `docs/superpowers/`
# is excluded -- the plans name interfaces before they are written (that is what a
# plan is), and the spec quotes planned thresholds and ablation arms. Checking
# those would either fail by design or need an allowlist wide enough to be a lie.
_SYMBOL_DOC_TARGETS = (
    README,
    CONFIG_DOC,
    REPO_ROOT / "docs" / "retrieval.md",
    REPO_ROOT / "docs" / "scrapers.md",
    REPO_ROOT / "docs" / "evaluation.md",
    REPO_ROOT / "docs" / "scoring.md",
    REPO_ROOT / "docs" / "profiles.md",
    REPO_ROOT / "docs" / "extractors.md",
    REPO_ROOT / "docs" / "twitter-cookies.md",
    REPO_ROOT / "docs" / "horizon-hub-design.md",
    REPO_ROOT / "docs" / "architecture.md",
    MCP_DOC,
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "SECURITY.md",
    REPO_ROOT / "CHANGELOG.md",
)


def _repo_vocab() -> set[str]:
    vocab = _code_vocab()
    sources = [
        REPO_ROOT / ".env.example",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "docker-compose.yml",
        REPO_ROOT / "Dockerfile",
        *sorted((REPO_ROOT / "tests").glob("*.py")),
        *sorted((REPO_ROOT / "data").glob("*.json")),
        *sorted((REPO_ROOT / "data" / "eval").glob("*.json")),
    ]
    for path in sources:
        if path.exists():
            vocab.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _read(path)))
    vocab.update(path.name for path in REPO_ROOT.rglob("*") if path.is_file())
    # tests/*.py is part of the vocabulary (the changelog names test functions),
    # but this file's own allowlist would otherwise make every exempted name
    # "real" and quietly disarm the check.
    return vocab - _EXTERNAL_IDENTIFIERS


def _suffix_is_real(token: str, vocab: set[str]) -> bool:
    """`_draft` counts as real only because `hz_research_draft` ends with it."""
    if not (token.startswith("_") or token.islower()):
        return False
    return any(name.endswith(token) for name in vocab)


def _unknown_symbols(text: str, vocab: set[str]) -> list[str]:
    unknown = []
    envish = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text))
    for token in _doc_identifiers(text):
        if token in envish:
            continue          # a ${VAR} name is chosen by the user, not by this repo
        if token in _EXTERNAL_IDENTIFIERS:
            continue
        if token in vocab or all(part in vocab for part in token.split(".")):
            continue
        if _suffix_is_real(token, vocab):
            continue
        unknown.append(token)
    return sorted(set(unknown))


def test_every_doc_quoted_symbol_exists_somewhere() -> None:
    offenders = {}
    vocab = _repo_vocab()
    for path in _SYMBOL_DOC_TARGETS:
        missing = _unknown_symbols(_read(path), vocab)
        if missing:
            offenders[path.name] = missing
    assert not offenders, f"docs name identifiers that exist nowhere in the repo: {offenders}"


def test_the_symbol_net_is_not_toothless() -> None:
    vocab = _repo_vocab()
    assert "SOURCE_SPECS" in vocab and "auth_token" not in vocab
    # Built at runtime on purpose: this file is part of the vocabulary, so a
    # literal fake name would become "real" by being written down here.
    fake_class = f"SOURCE_REGISTRY_V{9}"
    fake_tool = "hz_research_" + "nothere"
    assert _unknown_symbols(f"`{fake_class}` and `{fake_tool}` and `_draft`", vocab) == sorted(
        [fake_class, fake_tool]
    )


# ------------------------------------------------------------ documented CLI surface
_SCRIPTS_TABLE = re.compile(r'^(periscope[a-z-]*)\s*=\s*"([\w.]+):', re.M)


def _entry_points() -> dict[str, str]:
    return dict(_SCRIPTS_TABLE.findall(_read(REPO_ROOT / "pyproject.toml")))


def _options_in(mod_rel: str) -> set[str]:
    path = REPO_ROOT / mod_rel
    if not path.exists():
        return set()
    text = _read(path)
    # Match any `"--flag"` literal in the module: argparse calls wrap, and several
    # declare the short form first (`"-d", "--data-dir"`), which an
    # `add_argument("--x"` anchored pattern silently misses.
    flags = set(re.findall(r'"--([a-z][a-z0-9-]*)"', text))
    # Entry points share one parser helper: src/_cli.py contributes --data-dir and
    # --config to whichever CLI imports it. Without this the docs looked like they
    # advertised three flags that do not exist -- they do, they are just declared
    # one file over. Absence claims need the whole search surface.
    if "_cli" in text:
        flags |= _options_in("src/_cli.py")
    return flags


# Flags of the programs that *wrap* ours. A line like
# "uv run periscope --hours 24` 或 `docker compose run --rm periscope-collect"
# puts a compose flag between two of our commands, and crediting `--rm` to the
# collector would report a documentation defect that does not exist.
_ORCHESTRATOR_FLAGS = {"rm", "entrypoint", "build", "no-cache", "it", "detach", "d"}


def _command_lines(path: Path) -> list[str]:
    """Only code spans and fenced blocks, one line at a time.

    Scanning prose makes a changelog that *discusses* `--flag` look like a
    tutorial that advertises it. Commands in this repository are always inside
    code spans or fenced blocks, so that is where they get checked.
    """
    text = _read(path)
    chunks = re.findall(r"`([^`\n]+)`", text)
    chunks += re.findall(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    lines: list[str] = []
    for chunk in chunks:
        lines.extend(chunk.splitlines())
    return lines


def _documented_targets() -> dict[str, set[str]]:
    """Every --flag the docs print for one of our commands.

    A flag belongs to the command it follows *up to the next command on the
    same line*, because the compose idiom stacks two: `docker compose run --rm
    --entrypoint uv periscope-collect run periscope-wechat test --lang zh`
    advertises `--lang` for the wechat CLI, not the collector, and `--rm` is
    docker's. Matching by whole line made both look like documentation defects.
    """
    pattern = re.compile(r"(?:uv run )?(?:python )?(scripts/\w+\.py|periscope-[a-z-]+|periscope)")
    found: dict[str, set[str]] = {}
    sources = [
        README, MCP_DOC, REPO_ROOT / "CONTRIBUTING.md", REPO_ROOT / "SECURITY.md",
        REPO_ROOT / "CHANGELOG.md", *sorted((REPO_ROOT / "docs").glob("*.md")),
    ]
    for path in sources:
        for line in _command_lines(path):
            matches = list(pattern.finditer(line))
            for index, match in enumerate(matches):
                stop = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                segment = line[match.end():stop]
                found.setdefault(match.group(1), set()).update(
                    flag for flag in re.findall(r"--([a-z][a-z0-9-]+)", segment)
                    if flag not in _ORCHESTRATOR_FLAGS
                )
    return found


def _resolve_module(target: str) -> str:
    if target.startswith("scripts/"):
        return target
    if target == "periscope-collect":        # docker-compose service -> image entrypoint
        return "src/main.py"
    return _entry_points().get(target, "").replace(".", "/") + ".py"


def test_every_documented_command_flag_exists() -> None:
    """A flag copied out of the docs has to be accepted by the program.

    Command-line twin of the route-reachability guard: the docs promise a
    surface, and an unchecked promise turns into `unrecognized arguments` on a
    reader's machine while every test stays green.
    """
    unknown = {}
    for target, flags in _documented_targets().items():
        module = _resolve_module(target)
        assert module, f"{target} is documented but is neither an entry point nor a script"
        available = _options_in(module)
        missing = sorted(flag for flag in flags if flag not in available)
        if missing:
            unknown[target] = missing
    assert not unknown, f"docs print flags the programs do not accept: {unknown}"


def test_the_reachability_probe_is_documented_as_a_runnable_command() -> None:
    """A tool named only in prose is a tool nobody runs.

    The probe is the thing that turns "can this source be fetched" from an
    opinion into a measurement, and the whole S1/P3 chain waits on it -- yet
    the README used to say "one command does it" without printing the command.
    """
    mentions = [
        line
        for path in (README, REPO_ROOT / "CONTRIBUTING.md", *sorted((REPO_ROOT / "docs").glob("*.md")))
        for line in _command_lines(path)
        if "spike_sources.py" in line
    ]
    assert mentions, "scripts/spike_sources.py is never shown as a command"
    # A bare `scripts/spike_sources.py` inside prose is a mention, not a
    # runnable example -- what has to exist is at least one line a reader can
    # copy, which means it names the required --source and the --online switch
    # that is the whole point of the guard.
    invocations = [line for line in mentions if "--source" in line]
    assert invocations, (
        "the probe is only ever mentioned; no documented line can be run as-is"
    )
    assert any("--online" in line for line in invocations), (
        "no example shows the only flag that makes the probe actually fetch"
    )


def test_the_flag_guard_resolves_shared_and_invented_options() -> None:
    assert "hours" in _options_in("src/main.py")
    assert "data-dir" in _options_in("src/main.py"), "the shared parser must count"
    for made_up in ("summarise-everything", "force-refresh-all"):
        assert made_up not in _options_in("src/main.py")
        assert made_up not in _options_in("scripts/eval_retrieval.py")
    assert {"tiering", "check"} <= {  # the harnesses the docs tell people to run
        f for f in _options_in("scripts/eval_retrieval.py")
    } | _options_in("scripts/render_eval_charts.py")

    # Bite proof: the same comparison the guard runs, fed an invented flag.
    synthetic = {"periscope": {"hours", "totally-made-up"}}
    missing = {
        target: sorted(flag for flag in flags if flag not in _options_in(_resolve_module(target)))
        for target, flags in synthetic.items()
    }
    assert missing == {"periscope": ["totally-made-up"]}


def test_every_env_var_the_code_reads_is_documented() -> None:
    """An env var the software tells you to set has to appear in a guide.

    Found this by auditing the deployment surface: `HORIZON_PATH` is named in an
    MCP error message ("Pass horizon_path or set HORIZON_PATH") while no document
    mentioned it -- the same class of dead end as the RESEND_API_KEY case, where
    the guide pointed at a variable .env.example did not carry.
    """
    read: set[str] = set()
    for path in list((REPO_ROOT / "src").rglob("*.py")) + list((REPO_ROOT / "scripts").rglob("*.py")):
        read.update(
            re.findall(r'(?:getenv|environ\.get)\(\s*["\']([A-Z][A-Z0-9_]{2,})', _read(path))
        )
    documented = set(re.findall(
        r"^([A-Z][A-Z0-9_]+)=", _read(REPO_ROOT / ".env.example"), re.M
    )) | set(re.findall(r"`([A-Z][A-Z0-9_]{2,})`", _read(CONFIG_DOC)))
    # api_key_env / password_env style names are config fields, not fixed vars.
    undoc = sorted(read - documented - {"OPENAI_API_KEY"})
    assert not undoc, f"env vars read by the code but documented nowhere: {undoc}"


# ---------------------------------------------- deployment + asset pack surfaces
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
_COMPOSE_SERVICES = set(re.findall(r"^  ([a-z][a-z0-9-]+):\n", COMPOSE, re.M))


def test_documented_compose_commands_name_real_services() -> None:
    """`docker compose up -d periscope-web` is only a recipe if that service exists.

    Nothing was wrong here when first checked -- which is the point of pinning
    it: renaming a service would otherwise leave five doc lines quietly broken.

    Only the name before the container's own `run <script>` counts as a service.
    `docker compose run --rm --entrypoint uv periscope-collect run
    periscope-wechat test` names one service and one console script, and reading
    both as services is the same misattribution the flag check had to learn.
    """
    assert _COMPOSE_SERVICES == {"periscope-web", "periscope-collect"}
    unknown = {}
    for path in (README, CONFIG_DOC, CHANGELOG, REPO_ROOT / "docs" / "evaluation.md"):
        for cmd in re.findall(r"docker compose [^\n`]{0,80}", _read(path)):
            head = re.split(r"\s+run\s+periscope-", cmd)[0]
            for service in re.findall(r"\bperiscope-[a-z][a-z0-9-]*\b", head):
                if service not in _COMPOSE_SERVICES:
                    unknown.setdefault(path.name, []).append(f"{service} in {cmd[:44]}")
    assert not unknown, f"compose commands in docs name services the file lacks: {unknown}"


def test_every_built_in_profile_is_listed_in_the_profile_guide() -> None:
    """The guide's roster table is what a reader believes about what ships."""
    on_disk = sorted(p.name for p in (REPO_ROOT / "profiles").iterdir() if p.is_dir())
    guide = _read(REPO_ROOT / "docs" / "profiles.md")
    lines = guide.split("## Built-in Profiles", 1)[1].splitlines()
    listed: list[str] = []
    for line in lines:
        row = re.match(r"^\| `([a-z0-9-]+)` \|", line)
        if row:
            listed.append(row.group(1))
        elif listed and line.strip() and not line.startswith("|"):
            break  # the roster table ended; later tables are not the roster
    assert sorted(listed) == on_disk, (
        f"profiles guide lists {sorted(listed)}, repository ships {on_disk}"
    )

    for profile in on_disk:
        files = {p.name for p in (REPO_ROOT / "profiles" / profile).iterdir()}
        assert {"profile.json", "match.md", "analysis.md"} <= files, f"{profile} lacks a core file"
    # The guide promises a four-file layout; it holds for every profile shipped.
    assert "four-file layout" in guide
    assert all(
        (REPO_ROOT / "profiles" / p / "enrichment.md").exists() for p in on_disk
    ), "a profile broke the documented layout, or the guide's wording needs the same care as this line"
