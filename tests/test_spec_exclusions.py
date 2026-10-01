"""spec §11's exclusions, checked in code instead of asserted in prose.

"This fork does not do X" is the promise most likely to be broken by someone
who is not trying to break it: a streaming PR to make the panel feel nicer, a
captcha-solving dependency added to unblock a source, a signature reverse-
engineered because the endpoint is right there. Each would make the project a
different project, and no test currently noticed. The same file pins the two
deliberate non-changes (nullable columns, the extra-key strictness split) so a
well-meaning tightening does not quietly break existing configs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.models import (  # noqa: E402
    AnalysisConfig,
    ContentItem,
    CorpusConfig,
    ResearchConfig,
    RetrievalConfig,
    Section,
)

CONFIG_DOC = REPO_ROOT / "docs" / "configuration.md"


def _sources(*folders: str) -> str:
    parts = []
    for folder in folders:
        for path in (REPO_ROOT / folder).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_no_streaming_transport_was_added() -> None:
    """§11: request/response plus revision polling is enough; streaming is YAGNI."""
    code = _sources("src/web", "src/mcp")
    for pattern in ("websocket", "WebSocket", "text/event-stream", "StreamingResponse"):
        assert pattern not in code, f"{pattern} appeared - the SSE/WebSocket exclusion is now untrue"


def test_no_captcha_solving_and_no_request_signing() -> None:
    """§11 excludes captcha solving and signature reverse engineering outright.

    `blocked_captcha` in reachability.py is a *verdict* - detecting that a
    source is gated - which is the sanctioned alternative, so the word captcha
    alone must not be the trigger. What is forbidden is sending a challenge to
    a solving service or reproducing a platform's request signature.
    """
    code = _sources("src/scrapers", "src/sources", "src/ai", "src/corpus")
    forbidden = (
        r"anticaptcha", r"2captcha", r"capmonster", r"capture[-_]?solve", r"solve_captcha",
        r"manualcaptcha", r"captcha[-_]?api[-_]?key",
        r"hashlib\.md5\([^)]*(appkey|secret|salt|sign)",
    )
    hits = [p for p in forbidden if re.search(p, code, re.I)]
    assert not hits, f"anti-scraping exclusions broken by: {hits}"

    # ...and the pattern is not vacuously satisfied by clean code: run it
    # against the two things §11 forbids, and it must match.
    hypothetical = (
        "from io import anticaptcha; "
        "sign = hashlib.md5(appkey + ts).hexdigest()"
    )
    assert any(re.search(p, hypothetical, re.I) for p in forbidden[:3])
    assert re.search(forbidden[7], hypothetical)


def test_gated_platforms_have_no_scraper_module() -> None:
    """§9.15 / §11: no tieba or xiaohongshu fetching code before the probe passes."""
    files = {p.name for p in (REPO_ROOT / "src" / "scrapers").glob("*.py")}
    files |= {p.name for p in (REPO_ROOT / "src" / "sources").glob("*.py")}
    offenders = [f for f in sorted(files) if re.search(r"tieba|xhs|xiaohongshu|baidu", f, re.I)]
    assert not offenders, (
        f"scraper modules appeared for sources that never passed the probe: {offenders}"
    )


def test_semantic_retrieval_stays_off_until_a_model_is_named() -> None:
    """§11: vector search is never default-on; no model ships in the image."""
    assert RetrievalConfig().embedding_model == ""
    code = _sources("src")
    assert re.search(r"retrieval\.semantic and retrieval\.embedding_model", code), (
        "the semantic leg lost one of its two guards, so it can turn on by default"
    )


def test_items_keep_their_not_null_identity_columns() -> None:
    """§11 said we would not relax these; the whole citation and sort path leans on them."""
    ddl = (REPO_ROOT / "src" / "corpus" / "store.py").read_text(encoding="utf-8")
    assert "url TEXT NOT NULL" in ddl and "published_at TEXT NOT NULL" in ddl


def test_the_extra_key_strictness_split_is_still_the_documented_one() -> None:
    """Primitives reject typos; the evidence blocks do not - on purpose.

    Flipping either side is a behaviour change for real users: forbid on the
    four blocks makes an existing config with a stray key fail to load, and
    loosening Section / ContentItem lets a mis-typed tier field through.
    """
    for cls in (CorpusConfig, AnalysisConfig, ResearchConfig, RetrievalConfig):
        assert cls.model_config.get("extra") != "forbid", f"{cls.__name__} was tightened"
    for cls in (Section, ContentItem):
        assert cls.model_config.get("extra") == "forbid", f"{cls.__name__} lost its strictness"
    assert "**not** forbid" in _read_config_doc(), (
        "configuration.md no longer states the split, so the difference reads like an oversight"
    )


def _read_config_doc() -> str:
    return CONFIG_DOC.read_text(encoding="utf-8")
