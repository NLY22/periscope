"""The panel's own example payload has to survive the panel's own API.

Found by driving the page in a browser: the import textarea suggested
`"source_type": "forum"`, which the endpoint rejects - `forum` is a *kind* in
`SourceSpec`, not a source type, and the two vocabularies are disjoint. A
placeholder is documentation the user copies, so a wrong one is a defect with a
guaranteed victim.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.models import SOURCE_REGISTRY  # noqa: E402

HTML = (REPO_ROOT / "src" / "web" / "static" / "index.html").read_text(encoding="utf-8")

SOURCE_KINDS = {"official", "forum", "ugc_social", "aggregator", "search_engine"}


def _placeholder_payloads() -> list[dict]:
    found = re.findall(r"placeholder='(\{.*?\})'", HTML)
    assert found, "the import textarea lost its example - keep one, and keep it valid"
    return [json.loads(text) for text in found]


def test_the_kinds_and_the_source_types_never_overlap() -> None:
    """Why the placeholder was easy to get wrong, and why it must stay hard."""
    assert not SOURCE_KINDS & set(SOURCE_REGISTRY)


def test_the_placeholder_names_a_real_source_type() -> None:
    """The one thing in the example that must be exact.

    A placeholder legitimately sketches values (`"url": "…"` cannot import), but
    a vocabulary word in it is not a sketch: `forum` is a `SourceSpec.kind`, and
    the endpoint only accepts the 14 source types. Users copy placeholders.
    """
    for payload in _placeholder_payloads():
        items = payload.get("items", [])
        assert items, "an example with no items teaches nothing"
        for item in items:
            assert item["source_type"] in SOURCE_REGISTRY, (
                f"the panel suggests {item['source_type']!r}, which is not a source type; "
                f"the kinds are {sorted(SOURCE_KINDS)} and the types are {sorted(SOURCE_REGISTRY)}"
            )


def test_the_runnable_example_still_imports() -> None:
    """The repo's copyable file, as opposed to the sketch above.

    Covered by `tests/test_corpus_import.py` too; repeated here because the
    placeholder and the example are the two ways a user learns the shape, and
    they must not disagree.
    """
    from src.corpus.ingest import parse_import_payload

    payload = json.loads((REPO_ROOT / "data" / "export.example.json").read_text(encoding="utf-8"))
    items = parse_import_payload(payload)
    assert items and all(item.source_type in SOURCE_REGISTRY for item in items)


def test_the_claims_pane_says_who_made_a_vetoed_verdict() -> None:
    """Found by reading the rendered pane in a browser, not the endpoint.

    `/api/claims` carried `verdict_source` all along, and the template ignored
    it: a gate-demoted claim showed exactly what a model's own `unsupported`
    shows, including the model's confidence percentage - crediting a label to
    the thing that was overruled.
    """
    assert "verdict_source" in HTML, "the pane stopped distinguishing who decided"
    assert "可信度门否决" in HTML and "模型原判 supported" in HTML
    assert "c.verdict_source!=='trust_gate'" in HTML, (
        "the model's confidence is printed for a verdict it no longer owns"
    )


def test_the_stats_keys_the_panel_prints_exist_in_the_payload(tmp_path: Path) -> None:
    """`meta.claims ?? 0` is a silent-zero machine.

    A server-side rename would not error; it would print 0 声明 · 0 多源 and look
    like an empty corpus. Checked against the real `ClaimStore.stats()` keys
    rather than against a list kept in two places.
    """
    from src.analysis.claims import ClaimStore
    from src.corpus.store import Corpus

    used = set(re.findall(r"\bmeta\.([a-z_]+)", HTML))
    assert used, "the panel stopped reading stats - delete this test, do not pass it"
    provided = set(ClaimStore(Corpus(tmp_path / "stats.db")).stats())
    assert used <= provided, f"panel would render a confident 0 for {sorted(used - provided)}"

