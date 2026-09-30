"""Importing user-exported content into the evidence corpus (spec §6.1 fallback).

The gated Chinese sources (小红书 / 贴吧 / login-walled forums) are not going to
be scraped — the spec excludes captcha, signing and account pools. So the
sanctioned path is: the user exports what their own account can already see,
and this repository ingests it **with declared tiers**. The tests below are
mostly about that one promise: an import must not be able to smuggle crowd text
into `claimable`, and it must say where the text came from.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.corpus.ingest import IngestError, import_payload, parse_import_payload  # noqa: E402
from src.corpus.sections import claimable_of  # noqa: E402
from src.corpus.store import Corpus  # noqa: E402
from src.corpus.trust import PROVENANCE_FACTOR, compute_features, trust_score  # noqa: E402
from src.models import Config, SourceType  # noqa: E402

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def note(**over):
    item = {
        "source_type": "rss",
        "title": "某笔记：定价对比",
        "locator": "xhs:note:abc123",
        "author": "作者甲",
        "published_at": "2026-09-20T00:00:00+00:00",
        "sections": [
            {"tier": "primary", "text": "官方定价是每百万 token 2 元。"},
            {"tier": "community", "author": "路人乙", "text": "我觉得明明是 5 元。"},
        ],
    }
    item.update(over)
    return item


def payload(*items):
    return {"items": list(items)}


# ------------------------------------------------------------------- parsing
def test_a_minimal_import_becomes_a_content_item_with_declared_tiers() -> None:
    items = parse_import_payload(payload(note()))
    assert len(items) == 1
    item = items[0]
    assert item.locator == "xhs:note:abc123"
    assert [s.tier for s in item.sections] == ["primary", "community"]
    assert item.source_type is SourceType.RSS


def test_imported_sections_are_marked_as_a_manual_export_not_author_fetch() -> None:
    """Provenance says *how the text got here*, and trust discounts it."""
    item = parse_import_payload(payload(note()))[0]
    assert {s.provenance for s in item.sections} == {"manual_export"}
    assert PROVENANCE_FACTOR["manual_export"] < PROVENANCE_FACTOR["author"]
    exported = trust_score(compute_features(
        source_type="rss", text="官方定价是每百万 token 2 元。", author="作者甲",
        provenance="manual_export", published_at=NOW, now=NOW))
    fetched = trust_score(compute_features(
        source_type="rss", text="官方定价是每百万 token 2 元。", author="作者甲",
        provenance="author", published_at=NOW, now=NOW))
    assert exported < fetched


def test_an_explicit_provenance_in_the_payload_is_respected() -> None:
    item = parse_import_payload(payload(note(sections=[
        {"tier": "primary", "text": "视频口述：我们测得 8% 下降。",
         "provenance": "transcript"},
    ])))[0]
    assert item.sections[0].provenance == "transcript"


def test_id_is_derived_from_the_locator_so_reimporting_is_idempotent() -> None:
    first = parse_import_payload(payload(note()))[0].id
    again = parse_import_payload(payload(note()))[0].id
    assert first == again
    assert first.startswith("import:")


def test_an_explicit_id_is_kept_as_given() -> None:
    item = parse_import_payload(payload(note(id="xhs:note:abc123")))[0]
    assert item.id == "xhs:note:abc123"


def test_an_item_needing_identity_raises_a_message_that_names_the_item() -> None:
    with pytest.raises(IngestError) as exc:
        parse_import_payload(payload(note(locator="", url=None)))
    assert "locator" in str(exc.value)
    assert "0" in str(exc.value)          # the index is the only way to find it


def test_an_unknown_source_type_lists_the_choices_instead_of_guessing() -> None:
    with pytest.raises(IngestError) as exc:
        parse_import_payload(payload(note(source_type="xiaohongshu")))
    assert "xiaohongshu" in str(exc.value)
    assert "rss" in str(exc.value)


def test_content_without_sections_is_imported_as_legacy_marker_text() -> None:
    """A plain export with no tiering at all still lands, but labelled as such."""
    item = parse_import_payload(payload(
        note(sections=[], content="整段导出文本，没有分层。"))) [0]
    assert item.sections and item.sections[0].provenance == "legacy_marker"
    assert item.content


def test_an_empty_text_is_rejected_rather_than_stored_blank() -> None:
    with pytest.raises(IngestError):
        parse_import_payload(payload(note(sections=[{"tier": "primary", "text": "  "}])))


def test_one_item_reports_every_problem_instead_of_one_per_round_trip() -> None:
    """Hand-edited exports fail in several ways at once; say them all."""
    with pytest.raises(IngestError) as exc:
        parse_import_payload({"items": [{"title": "缺东缺西"}]})
    message = str(exc.value)
    assert "source_type" in message and "locator" in message and "content" in message


# ---------------------------------------------------------------- tier safety
def test_crowd_text_in_an_import_can_never_reach_claimable() -> None:
    item = parse_import_payload(payload(note()))[0]
    claimable = claimable_of(item)
    assert "每百万 token 2 元" in claimable
    assert "我觉得明明是 5 元" not in claimable


def test_a_community_only_import_has_no_claimable_text_at_all() -> None:
    item = parse_import_payload(payload(note(sections=[
        {"tier": "community", "author": "路人", "text": "听说涨价了。"},
    ])))[0]
    assert claimable_of(item) == ""


# ------------------------------------------------------------------- storage
def test_importing_twice_reports_the_second_run_as_no_new_items(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    first = import_payload(corpus, payload(note()), now=NOW)
    second = import_payload(corpus, payload(note()), now=NOW)
    corpus.close()
    assert first["items_new"] == 1 and first["items_total_seen"] == 1
    assert second["items_new"] == 0 and second["items_total_seen"] == 1


def test_imported_items_are_searchable_and_their_crowd_text_is_not(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    import_payload(corpus, payload(note()), now=NOW)
    hits = corpus.search("每百万 token", tier="claimable", limit=5)
    crowd = corpus.search("我觉得明明是", tier="claimable", limit=5)
    everything = corpus.search("我觉得明明是", tier="all", limit=5)
    corpus.close()
    assert hits and not crowd
    assert everything, "community text stays reachable as a lead"


def test_a_bad_item_is_reported_without_losing_the_good_ones(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    result = import_payload(
        corpus, payload(note(), note(source_type="xiaohongshu")), now=NOW)
    corpus.close()
    assert result["items_new"] == 1
    assert len(result["rejected"]) == 1
    assert "1" in result["rejected"][0]["reason"] or "source_type" in result["rejected"][0]["reason"]


def test_a_oversized_dump_is_rejected_with_the_limit_in_the_message() -> None:
    from src.corpus.ingest import MAX_ITEM_CHARS

    with pytest.raises(IngestError) as exc:
        parse_import_payload(payload(note(sections=[
            {"tier": "primary", "text": "x" * (MAX_ITEM_CHARS + 1)},
        ])))
    assert str(MAX_ITEM_CHARS) in str(exc.value)


# ----------------------------------------------------------------- entry points
def test_the_mcp_tool_is_registered_and_counted() -> None:
    import re

    server = (REPO_ROOT / "src" / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "async def hz_corpus_import(" in server, (
        "the spec's degradation path promised an import entry; without the tool "
        "an import only works from the CLI"
    )
    assert len(re.findall(r"^(?:async )?def hz_\w+", server, re.M)) == 27


def test_the_mcp_exposes_the_import_verb() -> None:
    import inspect
    import re

    server = (REPO_ROOT / "src" / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "async def hz_corpus_import(" in server, (
        "the spec's degradation path promised an import entry; without the tool "
        "an import only works from the CLI"
    )
    assert len(re.findall(r"^(?:async )?def hz_\w+", server, re.M)) == 27

    from src.mcp.service import HorizonPipelineService as S

    assert callable(S.corpus_import)
    assert not inspect.iscoroutinefunction(S.corpus_import), (
        "import touches only SQLite; making it async would imply a network hop"
    )


def test_the_web_endpoint_imports_and_the_panel_can_find_it(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from src.orchestrator import HorizonOrchestrator
    from src.storage.manager import StorageManager
    from src.web.app import create_app

    cfg = json.loads((REPO_ROOT / "data" / "config.example.json").read_text(encoding="utf-8"))
    for value in cfg["sources"].values():
        if isinstance(value, dict) and "enabled" in value:
            value["enabled"] = False
    orch = HorizonOrchestrator(Config.model_validate(cfg), StorageManager(data_dir=str(tmp_path)))
    with TestClient(create_app(orch)) as client:
        result = client.post("/api/import", json=payload(note()))
        assert result.status_code == 200, result.text
        assert result.json()["items_new"] == 1
        hits = client.get("/api/search", params={"q": "每百万 token"}).json()["items"]
        assert hits and hits[0]["title"] == "某笔记：定价对比"
        bad = client.post("/api/import", json={"items": [{"title": "no identity"}]})
        assert bad.status_code == 400 and "locator" in bad.text


def test_the_cli_script_reports_counts_and_rejects_per_item(tmp_path: Path, capsys) -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import import_corpus

    # The CLI shares the panel's config lookup, so a data dir needs a config.
    cfg = json.loads((REPO_ROOT / "data" / "config.example.json").read_text(encoding="utf-8"))
    for value in cfg["sources"].values():
        if isinstance(value, dict) and "enabled" in value:
            value["enabled"] = False
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    export = tmp_path / "export.json"
    export.write_text(json.dumps(payload(note(), note(source_type="bogus"))), encoding="utf-8")
    code = import_corpus.main([
        "--file", str(export), "--data-dir", str(tmp_path), "--json",
    ])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["items_new"] == 1 and len(report["rejected"]) == 1

