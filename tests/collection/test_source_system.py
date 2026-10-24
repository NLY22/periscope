"""Broad source retrieval must be useful, bounded and independently observable."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest


def test_registry_distinguishes_search_feeds_and_snapshots():
    from periscope.collection.registry import SourceRegistry
    registry = SourceRegistry()
    assert registry.get("arxiv_ai").mode == "search"
    assert registry.get("openai").mode == "feed"
    assert registry.get("litellm_prices").mode == "snapshot"
    assert registry.get("xhs_explore").mode == "browser"
    assert registry.get("github_issues").mode == "search"
    assert len(registry.entries()) > 179


def test_broad_plan_connects_existing_search_and_feed_sources():
    from periscope.collection.registry import SourceRegistry
    registry = SourceRegistry()
    plan = registry.plan("broad")
    keys = {entry.key for entry in plan.selected}
    assert {"weibo_search", "arxiv_ai", "openalex", "gh_search", "github_issues",
            "hn_ai", "openai", "google_ai", "crossref_works"} <= keys
    assert "litellm_prices" not in keys
    assert any(e.key == "xhs_explore" for e in plan.skipped)
    assert len(keys) == len(plan.selected)


def test_explicit_sources_and_kinds_are_validated():
    from periscope.collection.registry import SourceRegistry
    registry = SourceRegistry()
    plan = registry.plan("broad", sources=("arxiv_ai", "openalex"))
    assert {e.key for e in plan.selected} == {"arxiv_ai", "openalex"}
    assert all(e.kind == "academic" for e in registry.plan("broad", kinds=("academic",)).selected)
    with pytest.raises(ValueError, match="missing"):
        registry.plan("broad", sources=("missing",))
    with pytest.raises(ValueError):
        registry.plan("broad", kinds=("invented",))


def test_custom_feed_joins_the_same_plan(scratch_path):
    from periscope.collection.registry import SourceRegistry
    file = scratch_path / "sources.json"
    file.write_text(json.dumps({"sources": [{"key": "lab_notes", "name": "Lab notes",
        "url": "https://example.org/feed.xml", "kind": "academic", "fmt": "rss"}]}))
    registry = SourceRegistry.from_file(file)
    assert registry.get("lab_notes").mode == "feed"
    assert registry.plan("broad", sources=("lab_notes",)).selected[0].key == "lab_notes"
    file.write_text(json.dumps({"sources": [{"key": "openai", "name": "Duplicate",
        "url": "https://example.org/feed.xml", "kind": "official", "fmt": "rss"}]}))
    with pytest.raises(ValueError):
        SourceRegistry.from_file(file)


def test_topic_query_is_encoded_without_changing_its_meaning():
    from urllib.parse import parse_qs, urlsplit
    from periscope.collection.http import _page_url
    from periscope.collection.models import Source
    source = Source("test", "Test", "https://example.org/search?q={query}", "community",
                    queries=("A&B #中文",), query_param="query")
    query = parse_qs(urlsplit(_page_url(source, 1, "A&B #中文")).query)
    assert query == {"q": ["A&B #中文"]}


def test_youtube_results_keep_the_video_identity():
    from periscope.collection.parsing import extract_rows
    from periscope.collection.sources import CATALOG
    source = next(s for s in CATALOG if s.key == "youtube_cn")
    data = {"contents": {"videoRenderer": {"videoId": "test1234567",
        "title": {"runs": [{"text": "完整视频标题与研究讲解"}]},
        "ownerText": {"runs": [{"text": "Research Channel"}]},
        "descriptionSnippet": {"runs": [{"text": "讲解中的资料与方法"}]}}}}
    html = "<script>var ytInitialData = " + json.dumps(data) + ";</script>"
    rows = extract_rows(source, html, "text/html")
    assert rows[0]["url"] == "https://www.youtube.com/watch?v=test1234567"
    assert rows[0]["author"] == "Research Channel"
    assert rows[0]["ids"]["video_id"] == "test1234567"


def test_broad_collector_filters_feeds_and_keeps_source_outcomes(monkeypatch):
    from periscope.collection import broad, http
    from periscope.collection.models import FetchStats
    from periscope.collection.registry import SourceRegistry
    async def collect(client, source, since=None, **kwargs):
        assert source.max_pages <= 1
        if source.key == "openai":
            return [{"title": "机器人实验方法", "url": "https://example.org/match", "text": "结果"},
                    {"title": "其他无关新闻", "url": "https://example.org/other"}], FetchStats(source.key, source.name, source.kind)
        return [{"title": "机器人讨论记录", "url": "https://example.org/thread", "text": "实测"}], FetchStats(source.key, source.name, source.kind)
    monkeypatch.setattr(http, "collect", collect)
    plan = SourceRegistry().plan("broad", sources=("openai", "github_issues"))
    rows, stat = asyncio.run(broad.collect_async("机器人", plan, pages=1, budget=2))
    assert {r["url"] for r in rows} == {"https://example.org/match", "https://example.org/thread"}
    assert all(r["provenance"]["source_key"] == r["source"] for r in rows)
    assert len(stat["source_reports"]) == 2
    assert next(r for r in stat["source_reports"] if r["key"] == "openai")["filtered"] == 1


def test_one_source_failure_does_not_erase_other_results(monkeypatch):
    from periscope.collection import broad, http
    from periscope.collection.models import FetchStats
    from periscope.collection.registry import SourceRegistry
    async def collect(client, source, since=None, **kwargs):
        if source.key == "github_issues":
            raise httpx.ConnectError("unavailable")
        return [{"title": "机器人研究资料", "url": "https://example.org/ok"}], FetchStats(source.key, source.name, source.kind)
    monkeypatch.setattr(http, "collect", collect)
    rows, stat = asyncio.run(broad.collect_async("机器人", SourceRegistry().plan("broad", sources=("openai", "github_issues")), budget=2))
    assert len(rows) == 1
    assert "github_issues" in stat["failed"]
    assert {r["status"] for r in stat["source_reports"]} == {"ok", "failed"}


def test_budget_covers_queued_sources(monkeypatch):
    from periscope.collection import broad, http
    from periscope.collection.registry import SourceRegistry
    started = []
    async def slow(client, source, since=None, **kwargs):
        started.append(source.key)
        await asyncio.sleep(5)
    monkeypatch.setattr(http, "collect", slow)
    rows, stat = asyncio.run(broad.collect_async("robot", SourceRegistry().plan("broad", sources=("openai", "github_issues")), budget=0.05, concurrency=1))
    assert not rows
    assert len(started) == 1
    assert len(stat["over_budget"]) == 2
    assert len(stat["source_reports"]) == 2


def test_api_comparison_only_claims_this_runs_observed_url_difference():
    from periscope.topics.coverage import compare_sources
    api = [{"url": "https://example.org/same?utm_source=api"}]
    direct = [{"url": "https://example.org/same#section", "source": "feed"},
              {"url": "https://example.org/new", "source": "community"}]
    assert compare_sources(api, direct, api_succeeded=True)["direct_urls_not_in_api"] == 1
    assert compare_sources([], direct, api_succeeded=False)["direct_urls_not_in_api"] is None


def test_merged_body_retains_its_actual_source():
    from periscope.search.merge import merge
    rows = [{"title": "同一个研究主题的公开报告", "url": "https://a.example/a", "authority": 4, "rerank": 3, "body": "short"},
            {"title": "同一个研究主题的公开报告", "url": "https://b.example/b", "authority": 0, "rerank": 0.1, "body": "longer body " * 60}]
    row = merge(rows, [])[0]
    assert row["url"] == "https://a.example/a"
    assert row["body_source_url"] == "https://b.example/b"
    assert {member["url"] for member in row["sources"]} == {r["url"] for r in rows}


def test_ingest_and_local_retrieval_preserve_native_provenance(scratch_path, monkeypatch):
    from periscope.operations.ingest import build
    from periscope.topics import layers
    from src.corpus.store import Corpus
    row = {"source": "github_issues", "kind": "community", "title": "机器人实际部署问题",
           "url": "https://github.com/test/test/issues/1", "body": "机器人部署记录" * 100,
           "provenance": {"source_key": "github_issues", "method": "search"},
           "metrics": {"comments": 7}, "ids": {"issue": 1},
           "body_source_url": "https://example.org/original", "sources": [{"url": "https://example.org/original"}]}
    items, skipped = build([row], [])
    assert not skipped
    assert items[0].metadata["provenance"] == row["provenance"]
    assert items[0].content == row["body"]
    corpus = Corpus(scratch_path / "corpus.db")
    corpus.add_items(items)
    monkeypatch.setattr(layers.corpus_search, "CORPUS", scratch_path / "corpus.db")
    rows, _ = layers.local_lookup("机器人", 3)
    assert rows[0]["source"] == "github_issues"
    assert rows[0]["provenance"] == row["provenance"]
    assert rows[0]["metrics"] == {"comments": 7}
    assert rows[0]["body_source_url"] == row["body_source_url"]
    assert rows[0]["sources"] == row["sources"]
    corpus.close()


def test_comments_keep_community_tier_when_saved():
    from periscope.operations.ingest import build
    items, _ = build([{"source": "hn_comments", "kind": "community", "title": "A comment",
                       "url": "https://news.ycombinator.com/item?id=123",
                       "text": "A participant's observation",
                       "provenance": {"content_role": "community"}}], [])
    assert items[0].sections[0].tier == "community"


def test_run_conversion_does_not_consume_the_saved_payload():
    from periscope.topics.runs import ingestable_rows
    payload = {"rows": [{"_layer": "api", "title": "Result", "url": "https://example.org/1"}]}
    assert ingestable_rows(payload) == ingestable_rows(payload)
    assert payload["rows"][0]["_layer"] == "api"


@pytest.mark.parametrize("bad", [{"max_pages": -2}, {"min_interval": "slow"}, {"headers": "Bearer secret"}, {"queries": "term"}])
def test_invalid_source_settings_fail_before_collection(scratch_path, bad):
    from periscope.collection.registry import SourceRegistry
    file = scratch_path / "invalid.json"
    file.write_text(json.dumps({"sources": [{"key": "invalid", "name": "Invalid",
         "url": "https://example.org/search?q={query}", "kind": "academic", **bad}]}))
    with pytest.raises(ValueError):
        SourceRegistry.from_file(file)


def test_explicit_monthly_search_never_leaves_time_placeholders():
    from periscope.collection.broad import _prepare
    from periscope.collection.http import _date_windows, _page_url
    from periscope.collection.registry import SourceRegistry
    source = _prepare(SourceRegistry().get("hn_ai_monthly"), "robot", 1)
    assert "{" not in _page_url(source, 0, "robot", _date_windows(source)[0])


def test_deep_source_filter_does_not_launch_unselected_browsers(monkeypatch):
    from periscope.collection import broad
    from periscope.topics import layers
    monkeypatch.setattr(broad, "collect_topic", lambda *a, **k: ([], {"source_reports": [], "per": []}))
    calls = []
    monkeypatch.setattr(layers.subprocess, "run", lambda *a, **k: calls.append(a))
    _, stat = layers.platform_lookup("robot", "deep", 1, coverage="broad", sources=("arxiv_ai",))
    assert calls == []
    assert not stat["failed"]


def test_native_resources_do_not_merge_on_a_shared_topic_title():
    from periscope.collection.broad import _linked_row
    from periscope.collection.registry import SourceRegistry
    from periscope.search.merge import merge
    registry = SourceRegistry()
    rows = [_linked_row(registry.get(key), {"title": title, "url": url, "text": body}, "now")
            for key, title, url, body in (
                ("github_issues", "DeepSeek harness added", "https://github.com/a/b/pull/1", "Added a harness"),
                ("github_issues", "DeepSeek conversation limit", "https://github.com/a/b/issues/2", "Unrelated feature request" * 50),
                ("crossref_works", "DeepSeek", "https://doi.org/10.123/1", "A paper"))]
    combined = merge([], rows)
    assert len(combined) == 3
    assert all(len(row["sources"]) == 1 for row in combined)


def test_same_native_url_deduplicates_across_api_and_direct_retrieval():
    from periscope.collection.broad import _linked_row
    from periscope.collection.registry import SourceRegistry
    from periscope.search.merge import merge
    direct = _linked_row(SourceRegistry().get("github_issues"),
                         {"title": "Issue title", "url": "https://github.com/a/b/issues/1", "text": "Details"}, "now")
    api = {"title": "Rewritten issue title", "url": "https://github.com/a/b/issues/1?utm_source=search"}
    assert len(merge([api], [direct])) == 1


def test_api_titles_cannot_premerge_distinct_native_resources():
    from periscope.search.merge import merge
    first, second = "https://github.com/a/b/issues/1", "https://github.com/a/b/issues/2"
    api = [{"title": "Robot issue", "url": first, "authority": 4},
           {"title": "Robot issue", "url": second, "body": "Unrelated details" * 100}]
    direct = {"title": "Robot issue", "url": first, "text": "Original description",
              "provenance": {"identity": "url"}}
    rows = merge(api, [direct])
    assert len(rows) == 2
    assert all(len({member["url"] for member in row["sources"]}) == 1 for row in rows)


@pytest.mark.parametrize("fmt,body", [("json", "<html>Sign in</html>"), ("rss", "<html>Access denied</html>"),
                                    ("rss", "not xml"), ("json", '{"error":"quota exceeded"}')])
def test_invalid_responses_are_failures_instead_of_empty_searches(fmt, body):
    from periscope.collection.http import collect
    from periscope.collection.models import Source
    async def exercise():
        source = Source("test", "Test", "https://example.org/results", "academic", fmt=fmt,
                        items_path="items" if fmt == "json" else "", min_interval=0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=body))) as client:
            rows, stats = await collect(client, source)
        assert not rows
        assert stats.error
    asyncio.run(exercise())


def test_valid_empty_search_is_still_empty_not_failed():
    from periscope.collection.http import collect
    from periscope.collection.models import Source
    async def exercise():
        source = Source("test", "Test", "https://example.org/results", "academic", items_path="items", min_interval=0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"items": []}))) as client:
            rows, stats = await collect(client, source)
        assert not rows and not stats.error
    asyncio.run(exercise())


def test_cli_defaults_to_broad_search_and_emits_source_reports(monkeypatch, scratch_path):
    from periscope.cli import topic
    captured = {}
    def platform(query, speed, budget, **kwargs):
        captured.update(kwargs)
        return [], {"elapsed": 0, "failed": [], "source_reports": [{"key": "openai", "status": "empty", "rows": 0}]}
    monkeypatch.setattr(topic, "platform_lookup", platform)
    monkeypatch.setattr(topic, "emit", lambda *args: None)
    target = scratch_path / "results.json"
    assert topic.main(["--topic", "robot", "--no-local", "--no-api", "--no-shard", "--json", str(target)]) == 0
    assert captured["coverage"] == "broad"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["source_reports"][0]["status"] == "empty"
    assert payload["coverage"]["direct_urls_not_in_api"] is None


def test_local_record_without_web_url_still_searches(scratch_path, monkeypatch):
    from periscope.topics import layers
    from src.corpus.store import Corpus
    from src.models import ContentItem, SourceType
    corpus = Corpus(scratch_path / "local.db")
    corpus.add_items([ContentItem(id="local:1", source_type=SourceType.RSS, title="机器人研究报告",
                                 locator="local:1", content="机器人研究方法" * 100)])
    monkeypatch.setattr(layers.corpus_search, "CORPUS", scratch_path / "local.db")
    rows, _ = layers.local_lookup("机器人", 3)
    assert rows[0]["title"] == "机器人研究报告"
    corpus.close()


def test_hydration_keeps_each_records_metadata_on_a_shared_url():
    import sqlite3
    from periscope.search.local import hydrate_bodies
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE items(id TEXT, url TEXT, content TEXT, metadata_json TEXT)")
    for key in ("a", "b"):
        db.execute("INSERT INTO items VALUES(?,?,?,?)", (key, "https://example.org/shared", key * 500,
                                                      json.dumps({"source_key": key})))
    rows = hydrate_bodies(db, [{"id": "a", "url": "https://example.org/shared"},
                               {"id": "b", "url": "https://example.org/shared"}])
    assert [row["source_key"] for row in rows] == ["a", "b"]
    assert [row["body"] for row in rows] == ["a" * 500, "b" * 500]
    db.close()


def test_custom_source_with_unmapped_kind_fails_early(scratch_path):
    from periscope.collection.registry import SourceRegistry
    file = scratch_path / "unsupported.json"
    file.write_text(json.dumps({"sources": [{"key": "custom", "name": "Custom", "fmt": "rss",
         "url": "https://example.org/feed.xml", "kind": "unmapped_custom_kind"}]}))
    with pytest.raises(ValueError, match="kind"):
        SourceRegistry.from_file(file)


def test_successful_last_page_does_not_wait_for_another_request():
    from periscope.collection.http import collect
    from periscope.collection.models import Source
    async def exercise():
        source = Source("test", "Test", "https://example.org/results", "academic", max_pages=1, min_interval=6.5)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[{"title": "robot", "url": "https://example.org/1"}]))) as client:
            rows, _ = await asyncio.wait_for(collect(client, source), 0.3)
        assert len(rows) == 1
    asyncio.run(exercise())


def test_completed_pages_survive_the_overall_deadline(monkeypatch):
    from periscope.collection import broad, http
    from periscope.collection.registry import SourceRegistry
    real_collect = http.collect
    async def collect(client, source, since=None, **kwargs):
        async def response(request):
            if request.url.params.get("page") == "2":
                await asyncio.sleep(5)
            return httpx.Response(200, json={"items": [{"title": "robot", "html_url": "https://github.com/a/b/issues/1"}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as fake:
            from dataclasses import replace
            return await real_collect(fake, replace(source, min_interval=0), **kwargs)
    monkeypatch.setattr(http, "collect", collect)
    rows, stat = asyncio.run(broad.collect_async("robot", SourceRegistry().plan(sources=("github_issues",)),
                                               pages=2, budget=0.2))
    assert len(rows) == 1
    assert stat["source_reports"][0]["status"] == "partial"


def test_extension_and_custom_kinds_use_the_ingest_credibility_family():
    from periscope.collection.credibility import prior_for
    from periscope.collection.families import KIND_TO_SOURCE
    from src.corpus.trust import SOURCE_PRIORS
    for key in ("crossref_works", "custom_academic_feed"):
        assert prior_for(key, "academic") == SOURCE_PRIORS[KIND_TO_SOURCE["academic"].value]


def test_shared_host_spacing_survives_source_boundaries():
    from periscope.collection.broad import _PacedClient
    import time
    stamps = []
    class Client:
        async def get(self, url):
            stamps.append(time.monotonic())
    async def exercise():
        last = {}
        await _PacedClient(Client(), "same", 0.05, last).get("https://example.org/1")
        await _PacedClient(Client(), "same", 0, last).get("https://example.org/2")
    asyncio.run(exercise())
    assert stamps[1] - stamps[0] >= 0.04
