"""Web panel endpoint tests — thin API surface over the real orchestrator."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.models import Config, ContentItem, SourceType
from src.orchestrator import HorizonOrchestrator
from src.storage.manager import StorageManager
from src.web.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path):
    cfg = json.loads((REPO_ROOT / "data" / "config.example.json").read_text(encoding="utf-8"))
    for value in cfg["sources"].values():
        if isinstance(value, dict) and "enabled" in value:
            value["enabled"] = False
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    entry["enabled"] = False
    config = Config.model_validate(cfg)
    orch = HorizonOrchestrator(config, StorageManager(data_dir=str(tmp_path)))
    now = datetime.now(timezone.utc)
    orch.get_corpus().add_items([
        ContentItem(
            id="t1", source_type=SourceType.V2EX, title="开源大模型周报",
            url="https://example.com/1", author="a", published_at=now,
            fetched_at=now, content="本周开源大模型领域热闹，多家发布新模型。",
            metadata={},
        ),
        ContentItem(
            id="t2", source_type=SourceType.DISCOURSE, title="Rust code review thread",
            url="https://example.com/2", author="b", published_at=now,
            fetched_at=now, content="A code review on a multithreaded server in Rust.",
            metadata={},
        ),
    ])
    app = create_app(orch)
    with TestClient(app) as c:
        yield c


def test_index_serves_panel(client) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Periscope" in r.text and "潜望镜" in r.text  # the UI shell itself


def test_stats_band(client) -> None:
    data = client.get("/api/stats").json()
    assert data["corpus"]["items"] == 2
    assert data["ai_available"] is False  # no key in test env — honest badge
    assert "claims" in data and "research" in data


def test_items_strip_content_but_keep_snippets(client) -> None:
    data = client.get("/api/items?limit=10").json()
    assert data["count"] == 2
    for item in data["items"]:
        assert "content" not in item
        assert isinstance(item["snippet"], str)


def test_search_is_cjk_aware(client) -> None:
    assert client.get("/api/search?q=大模型").json()["count"] == 1
    assert client.get("/api/search?q=multithreaded").json()["count"] == 1


def test_search_rejects_blank(client) -> None:
    assert client.get("/api/search?q=++").status_code == 400


def test_claims_empty_gracefully(client) -> None:
    data = client.get("/api/claims").json()
    assert data["claims"] == []


def test_unknown_claim_404(client) -> None:
    assert client.get("/api/claims/nope").status_code == 404


def test_research_roundtrip(client) -> None:
    r = client.post("/api/research/start", json={"question": "开源大模型有哪些发布？"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert "研究报告" in r.json()["markdown"]

    status = client.get(f"/api/research/{sid}").json()
    assert status["session"]["id"] == sid
    assert len(status["subquestions"]) >= 1

    fu = client.post(f"/api/research/{sid}/followup", json={"message": "只看中国的"})
    assert fu.status_code == 200
    assert fu.json()["session_id"] == sid

    listing = client.get("/api/research").json()
    assert listing["count"] >= 1
    assert sid in {s["id"] for s in listing["sessions"]}


def test_unknown_session_404(client) -> None:
    assert client.get("/api/research/ses_deadbeef").status_code == 404
    assert client.get("/api/research/ses_deadbeef").json() == {"detail": "session not found"}


def test_start_rejects_empty_question(client) -> None:
    assert client.post("/api/research/start", json={"question": "  "}).status_code == 400


def test_panel_repaints_round_state_after_it_changes_the_session() -> None:
    """A text guard on the panel script, found by actually clicking it.

    Starting a session used to paint only the report, so the round timeline,
    the editable sections and the pending-request card stayed empty until the
    page was reloaded — the P2 surface was invisible in the one place it is
    meant to be used. Verified in a browser against a seeded corpus: after the
    fix, a start renders the draft, a round appends to the timeline, and an
    edited section keeps its text and picks up 「上游已变 · 未覆盖」.
    """
    script = (REPO_ROOT / "src" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    ask_block = script.split("async function ask(kind){")[1].split("$('#askBtn').onclick")[0]
    step_block = script.split("async function stepRound(msg){")[1].split("async function ask(")[0]

    assert "await showSession(r.session_id)" in ask_block, (
        "starting or following up must reload the whole round state, not just the report"
    )
    assert "await showSession(current)" in step_block, (
        "a round can open a request; without a repaint the user never sees it"
    )
    assert "· active" not in script, (
        "the status line must come from the server, not a hardcoded 'active'"
    )


# ------------------------------------------------------- the import affordance
import re

PANEL_HTML = (
    Path(__file__).resolve().parents[1] / "src" / "web" / "static" / "index.html"
)


def test_panel_script_parses_when_handed_to_node(tmp_path) -> None:
    """The panel is one `<script>`; a syntax error blanks the whole page.

    There is no browser here to catch it, so the cheapest real parser wins:
    `node --check`. Skipped when node is absent rather than pretending coverage.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    html = PANEL_HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts, "the panel lost its script block"
    target = tmp_path / "panel.js"
    target.write_text("\n".join(scripts), encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(target)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_import_panel_reads_only_fields_the_endpoint_returns(client) -> None:
    """A UI that reads `r.items_added` against a server returning `items_new`
    shows an empty result forever, and every endpoint test still passes."""
    html = PANEL_HTML.read_text(encoding="utf-8")
    block = html.split("async function doImport")[1].split("$('#importCheck')")[0]
    used = set(re.findall(r"\br\.([a-z_]+)", block))
    assert used, "the import handler stopped reading the response at all"

    response = client.post("/api/import", json={
        "items": [{
            "title": "面板自检用的一条", "url": "https://example.com/panel-check",
            "content": "作者写的正文里有一个可核验数字", "source_type": "rss",
            "locator": "rss:example:panel-check",
        }],
        "dry_run": True,
    })
    assert response.status_code == 200, response.text
    returned = set(response.json())
    assert used <= returned, f"panel reads fields the endpoint does not return: {sorted(used - returned)}"
