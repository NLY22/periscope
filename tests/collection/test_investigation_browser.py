"""Browser-backed leads must use the investigation evidence and stop cleanly."""

import asyncio

import pytest

from src.corpus.store import Corpus


def test_xhs_search_and_discussion_become_versioned_evidence(monkeypatch, scratch_path):
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations import browser_sources
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def search(topic, source, budget):
            yield {"title": "考研经历", "text": "摘要", "url": "https://www.xiaohongshu.com/explore/abcd1234"}

        async def detail(source, card, budget, max_comments):
            return ({"text": "完整经验分享", "author": "作者"},
                    [{"id": "7", "text": "考生回复", "author": "评论者", "parent_id": "abcd1234"}],
                    {"status": "complete", "errors": [], "truncated_by": []})

        monkeypatch.setattr(browser_sources, "_stream_search", search)
        monkeypatch.setattr(browser_sources, "_fetch_detail", detail)
        corpus = Corpus(scratch_path / "xhs-investigation.db")
        runner = InvestigationRunner(corpus, lambda query: browser_sources.stream_browser_source(query, "xhs_browser", budget=30))
        rid = runner.submit(runner.store.create("考研"), "考研", "one")
        await runner.wait(rid)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(rid)]
        assert any(item["type"] == "note" and item["text"] == "完整经验分享" for item in snapshots)
        assert any(item["type"] == "reply" and item["text"] == "考生回复" and item["author"] == "评论者" for item in snapshots)
        assert runner.store.get_round(rid)["status"] == "completed"
        corpus.close()
    asyncio.run(scenario())


def test_kuaishou_search_is_preserved_without_claiming_comment_support(monkeypatch, scratch_path):
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations import browser_sources
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def search(topic, source, budget):
            yield {"title": "视频讨论", "text": "视频描述", "url": "https://www.kuaishou.com/short-video/abc123"}

        monkeypatch.setattr(browser_sources, "_stream_search", search)
        corpus = Corpus(scratch_path / "kuaishou-investigation.db")
        runner = InvestigationRunner(corpus, lambda query: browser_sources.stream_browser_source(query, "kuaishou_browser", budget=30))
        rid = runner.submit(runner.store.create("讨论"), "讨论", "one")
        await runner.wait(rid)
        item = EvidenceVersions(corpus).get_version(**runner.store.references(rid)[0])
        assert item["content_level"] == "search_result" and item["text"] == "视频描述"
        assert runner.store.get_round(rid)["status"] == "partial"
        corpus.close()
    asyncio.run(scenario())


def test_closing_search_terminates_its_owned_browser_worker(monkeypatch, scratch_path):
    import json
    from pathlib import Path
    from periscope.investigations import browser_sources

    async def scenario():
        stopped = asyncio.Event()

        class Process:
            pid = 12345
            returncode = None

        async def launch(*command, **kwargs):
            assert Path(kwargs["env"]["TEMP"]).is_relative_to(scratch_path)
            out = Path(command[command.index("--out") + 1])
            out.write_text(json.dumps([{"url": "https://www.douyin.com/video/123", "title": "video"}]), encoding="utf-8")
            return Process()

        async def terminate(process):
            process.returncode = -1
            stopped.set()

        monkeypatch.setattr(browser_sources.asyncio, "create_subprocess_exec", launch)
        monkeypatch.setattr(browser_sources, "_terminate_process_tree", terminate)
        monkeypatch.setattr(browser_sources.paths, "SHARD_DIR", scratch_path)
        monkeypatch.setattr(browser_sources.paths, "SCRATCH", scratch_path)
        stream = browser_sources._stream_search("video", "douyin_browser", 30)
        assert (await asyncio.wait_for(anext(stream), 2))["title"] == "video"
        await stream.aclose()
        assert stopped.is_set()
    asyncio.run(scenario())


def test_stopping_after_a_lead_closes_the_underlying_search(monkeypatch):
    from periscope.investigations import browser_sources

    async def scenario():
        stopped = asyncio.Event()

        async def search(topic, source, budget):
            try:
                yield {"title": "video", "url": "https://www.douyin.com/video/123"}
                await asyncio.Future()
            finally:
                stopped.set()

        monkeypatch.setattr(browser_sources, "_stream_search", search)
        stream = browser_sources.stream_browser_source("video", "douyin_browser", budget=30)
        assert await anext(stream)
        await stream.aclose()
        assert stopped.is_set()
    asyncio.run(scenario())


def test_default_search_runs_only_one_browser_source_at_a_time(monkeypatch):
    from periscope.investigations import browser_sources, catalog, crawl, web_search

    async def scenario():
        active, peak, seen = 0, 0, set()

        async def browser(topic, source, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
                seen.add(source)
                yield [{"source": source, "text": topic}]
            finally:
                active -= 1

        async def empty(*args, **kwargs):
            if False:
                yield []

        monkeypatch.setattr(browser_sources, "stream_browser_source", browser)
        monkeypatch.setattr(catalog, "stream_catalog_source", empty)
        monkeypatch.setattr(crawl, "stream_v2ex_search", empty)
        monkeypatch.setattr(crawl, "stream_hn_search", empty)
        monkeypatch.setattr(web_search, "stream_public_search", empty)
        monkeypatch.setattr(web_search, "stream_search_api", empty)
        async for _ in crawl.stream_auto_search("topic", budget=30):
            pass
        assert peak == 1 and seen == {"xhs_browser", "douyin_browser", "kuaishou_browser"}
    asyncio.run(scenario())


def test_separate_investigations_share_the_browser_limit(monkeypatch):
    from periscope.investigations import browser_sources

    async def scenario():
        active, peak = 0, 0

        async def raw_search(topic, source, budget):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
                yield {"url": "https://www.douyin.com/video/123"}
            finally:
                active -= 1

        monkeypatch.setattr(browser_sources, "_run_search", raw_search, raising=False)
        async def unexpected_launch(*args, **kwargs):
            raise AssertionError("test must not start a real browser")
        monkeypatch.setattr(browser_sources.asyncio, "create_subprocess_exec", unexpected_launch)

        async def consume():
            async for _ in browser_sources._stream_search("topic", "douyin_browser", 30):
                pass

        await asyncio.gather(consume(), consume())
        assert peak == 1
    asyncio.run(scenario())


def test_explicit_xhs_link_uses_browser_detail(monkeypatch, scratch_path):
    from periscope.collection.crawling.engine import CrawlOptions
    from periscope.investigations import browser_sources
    from periscope.investigations.crawl import stream_crawl
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        called = []

        async def detail(source, card, budget, max_comments):
            called.append((source, card["url"]))
            return ({"text": "详细经验"}, [{"id": "8", "text": "一条回复"}], {"status": "complete"})

        monkeypatch.setattr(browser_sources, "_fetch_detail", detail)
        corpus = Corpus(scratch_path / "xhs-link.db")
        url = "https://www.xiaohongshu.com/explore/abcd1234"
        options = CrawlOptions((url,), max_pages=3, depth=2, budget=30)
        runner = InvestigationRunner(corpus, lambda query: stream_crawl(query, options))
        rid = runner.submit(runner.store.create("经验"), "深入这条", "one")
        await runner.wait(rid)
        assert called == [("xhs_browser", url)] and corpus.search("详细经验")
        corpus.close()
    asyncio.run(scenario())


def test_previously_discovered_xhs_lead_can_be_selected_for_followup(scratch_path):
    from periscope.investigations.pending import pending_candidates
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "xhs-pending.db")

        async def collect(query):
            yield [{"source": "xhs_browser", "kind": "community", "title": "考研经验", "text": "考研摘要",
                    "url": "https://www.xiaohongshu.com/explore/abcd1234",
                    "evidence": {"source": "xhs", "type": "note", "native_id": "abcd1234", "content_level": "search_result"},
                    "discussion_coverage": {"status": "not_attempted"}}]

        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("考研")
        await runner.wait(runner.submit(inv, "考研", "one"))
        rid = runner.store.start_round(inv, "考研评论", "two", options={"seeds": [], "max_pages": None})
        assert [item["source"] for item in pending_candidates(runner.store, rid, "考研")] == ["xhs"]
        corpus.close()
    asyncio.run(scenario())


def test_browser_profile_directory_can_be_isolated_for_probes(scratch_path):
    import os
    import subprocess
    import sys

    isolated = scratch_path / "isolated-profiles"
    env = {**os.environ, "PERISCOPE_BROWSER_PROFILE_DIR": str(isolated)}
    result = subprocess.run([sys.executable, "-c", "from periscope.runtime.paths import PROFILES, subprocess_env; "
                             "print(PROFILES); print(subprocess_env().get('PERISCOPE_BROWSER_PROFILE_DIR', ''))"],
                            env=env, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == [str(isolated), str(isolated)]


@pytest.mark.parametrize("source", ["xhs_browser", "douyin_browser", "kuaishou_browser"])
def test_browser_sources_are_not_classified_as_hacker_news(source):
    from periscope.operations.ingest import build
    from src.models import SourceType

    rows = [{"source": source, "kind": "community", "title": "test", "text": "discussion",
             "url": "https://example.org/post", "provenance": {"content_role": "community"}}]
    items, _ = build(rows, [])
    assert items[0].source_type == SourceType.BILIBILI


def test_browser_detail_worker_uses_remaining_deadline_after_startup(monkeypatch):
    import io
    import json
    import sys
    from types import SimpleNamespace
    from periscope.collection.platforms import xhs
    from periscope.investigations import browser_detail_worker

    url = "https://www.xiaohongshu.com/explore/abcd1234"
    received = []

    def fetch(items, *, max_comments, budget_seconds):
        received.append(budget_seconds)
        return {url: ({"text": "captured text"}, [{"id": "1", "text": "captured comment"}], {"status": "partial"})}

    monkeypatch.setattr(xhs, "_fetch_many_sync", fetch)
    monkeypatch.setattr(browser_detail_worker, "time", SimpleNamespace(monotonic=lambda: 100.0), raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"source": "xhs_browser", "url": url,
                                                              "budget": 30, "deadline": 105, "max_comments": 10})))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    assert browser_detail_worker.main() == 0
    assert received == [5.0] and json.loads(output.getvalue())["comments"][0]["text"] == "captured comment"
