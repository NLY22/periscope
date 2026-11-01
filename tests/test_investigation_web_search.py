"""Public and configured web discovery must feed saved evidence safely."""

import asyncio

from src.corpus.store import Corpus


def test_configured_search_api_saves_returned_content_and_provenance(monkeypatch, scratch_path):
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.web_search import stream_search_api
    from periscope.search import api

    async def scenario():
        async def search(query, **kwargs):
            assert query == "admissions" and kwargs["need_content"] is True
            return api.SearchResponse(results=[api.Result(title="Admissions", url="https://example.org/admissions",
                                                          content="official admission text")])
        monkeypatch.setattr(api, "available", lambda: True)
        monkeypatch.setattr(api, "search_async", search)
        corpus = Corpus(scratch_path / "api-search.db")
        runner = InvestigationRunner(corpus, lambda message: stream_search_api(message, budget=30))
        rid = runner.submit(runner.store.create("admissions"), "admissions", "one")
        await runner.wait(rid)
        ref = runner.store.references(rid)[0]
        snap = EvidenceVersions(corpus).get_version(**ref)
        assert snap["text"] == "official admission text" and snap["content_level"] == "api_content"
        assert corpus.search("official admission text")
        corpus.close()
    asyncio.run(scenario())


def test_search_api_normalizes_non_string_fields_before_journaling(monkeypatch, scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.web_search import stream_search_api
    from periscope.search import api

    async def scenario():
        async def search(query, **kwargs):
            return api.SearchResponse(results=[api.Result(title={"unexpected": "object"}, url="https://example.org/admissions",
                                                          content="admission evidence", date=1700000000, site_name=["unexpected"])])

        monkeypatch.setattr(api, "available", lambda: True)
        monkeypatch.setattr(api, "search_async", search)
        corpus = Corpus(scratch_path / "api-fields.db")
        runner = InvestigationRunner(corpus, lambda query: stream_search_api(query, budget=30))
        rid = runner.submit(runner.store.create("admissions"), "admissions", "one")
        await runner.wait(rid)
        assert runner.store.get_round(rid)["status"] == "completed" and corpus.search("admission evidence")
        restarted = InvestigationRunner(corpus, lambda query: stream_search_api(query, budget=30))
        assert restarted.recovery and not restarted.recovery["failed"]
        corpus.close()
    asyncio.run(scenario())


def test_public_search_saves_candidates_and_reads_selected_page(monkeypatch, scratch_path):
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations import web_search
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def discover(query, budget):
            return [{"title": "Admissions", "href": "https://example.org/admissions", "body": "search excerpt"}]

        async def crawl(topic, options):
            assert options.include_seed and options.seeds == ("https://example.org/admissions",)
            yield [{"source": "web_crawl", "kind": "media", "title": "Admissions",
                    "url": "https://example.org/admissions", "text": "full admission details"}]

        monkeypatch.setattr(web_search, "_run_public_search", discover)
        monkeypatch.setattr(web_search, "stream_crawl", crawl)
        corpus = Corpus(scratch_path / "public-search.db")
        runner = InvestigationRunner(corpus, lambda message: web_search.stream_public_search(message, budget=30, detail_limit=1))
        rid = runner.submit(runner.store.create("admissions"), "admissions", "one")
        await runner.wait(rid)
        snaps = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(rid)]
        assert {item["text"] for item in snaps} == {"search excerpt", "full admission details"}
        corpus.close()
    asyncio.run(scenario())


def test_public_search_and_platform_detail_share_identity_and_community_tier(monkeypatch, scratch_path):
    from periscope.collection.platforms import github
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations import web_search
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def discover(query, budget):
            return [{"title": "Issue", "href": "https://github.com/example/repo/issues/12", "body": "personalclaim42"}]

        async def detail(*args, **kwargs):
            return {"text": "personalclaim42 full discussion"}, {"status": "complete"}

        async def comments(*args, **kwargs):
            return [], {"status": "complete"}

        monkeypatch.setattr(web_search, "_run_public_search", discover)
        monkeypatch.setattr(github, "fetch_issue_detail", detail)
        monkeypatch.setattr(github, "fetch_discussion", comments)
        corpus = Corpus(scratch_path / "public-platform.db")
        runner = InvestigationRunner(corpus, lambda query: web_search.stream_public_search(query, budget=30, detail_limit=1))
        rid = runner.submit(runner.store.create("python"), "python", "one")
        await runner.wait(rid)
        refs = runner.store.references(rid)
        assert len({ref["object_id"] for ref in refs}) == 1
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in refs]
        assert any(item.get("content_level") == "full" for item in snapshots)
        assert corpus.search("personalclaim42") and not corpus.search("personalclaim42", tier="claimable")
        corpus.close()
    asyncio.run(scenario())


def test_search_api_community_result_is_not_a_primary_claim(monkeypatch, scratch_path):
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.web_search import stream_search_api
    from periscope.search import api

    async def scenario():
        async def search(query, **kwargs):
            return api.SearchResponse(results=[api.Result(title="Question", url="https://stackoverflow.com/questions/10/example",
                                                          content="personalclaim42")])
        monkeypatch.setattr(api, "available", lambda: True)
        monkeypatch.setattr(api, "search_async", search)
        corpus = Corpus(scratch_path / "api-community.db")
        runner = InvestigationRunner(corpus, lambda query: stream_search_api(query, budget=30))
        rid = runner.submit(runner.store.create("python"), "python", "one")
        await runner.wait(rid)
        assert corpus.search("personalclaim42") and not corpus.search("personalclaim42", tier="claimable")
        corpus.close()
    asyncio.run(scenario())


def test_failed_platform_detail_does_not_replace_relevant_search_lead_with_blank_card(monkeypatch, scratch_path):
    from periscope.collection.platforms import github
    from periscope.investigations import web_search
    from periscope.investigations.pending import pending_candidates
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def discover(query, budget):
            return [{"title": "python issue", "href": "https://github.com/example/repo/issues/12", "body": "python summary"}]

        async def detail(*args, **kwargs):
            return {}, {"status": "failed"}

        async def comments(*args, **kwargs):
            return [], {"status": "failed"}

        monkeypatch.setattr(web_search, "_run_public_search", discover)
        monkeypatch.setattr(github, "fetch_issue_detail", detail)
        monkeypatch.setattr(github, "fetch_discussion", comments)
        corpus = Corpus(scratch_path / "public-pending.db")
        runner = InvestigationRunner(corpus, lambda query: web_search.stream_public_search(query, budget=30, detail_limit=1))
        inv = runner.store.create("python")
        await runner.wait(runner.submit(inv, "python", "one"))
        rid = runner.store.start_round(inv, "继续", "two", options={"seeds": [], "max_pages": None})
        pending = pending_candidates(runner.store, rid, "python")
        assert len(pending) == 1 and pending[0]["snapshot"]["text"] == "python summary"
        corpus.close()
    asyncio.run(scenario())


def test_generic_crawled_community_page_is_not_promoted_to_primary(monkeypatch, scratch_path):
    import httpx
    from periscope.collection.crawling import engine
    from periscope.investigations import web_search
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def discover(query, budget):
            return [{"title": "discussion", "href": "https://www.reddit.com/r/test/comments/123/example", "body": "search excerpt"}]

        async def public(url):
            return url

        async def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /")
            return httpx.Response(200, text="<main><h1>discussion</h1><p>personalclaim99</p></main>", headers={"content-type": "text/html"})

        client_type = httpx.AsyncClient
        monkeypatch.setattr(web_search, "_run_public_search", discover)
        monkeypatch.setattr(engine, "validate_public_http_url", public)
        monkeypatch.setattr(engine.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
        corpus = Corpus(scratch_path / "community-page.db")
        runner = InvestigationRunner(corpus, lambda query: web_search.stream_public_search(query, budget=30, detail_limit=1))
        rid = runner.submit(runner.store.create("discussion"), "discussion", "one")
        await runner.wait(rid)
        results = corpus.search("personalclaim99")
        assert results and results[0]["source_type"] == "reddit"
        assert not corpus.search("personalclaim99", tier="claimable")
        corpus.close()
    asyncio.run(scenario())


def test_github_official_readme_is_not_classified_as_community():
    from periscope.search.identity import community_source_key

    assert not community_source_key("https://github.com/owner/project/blob/main/README.md")
    assert not community_source_key("https://github.com/owner/project/releases/tag/v1")
    assert community_source_key("https://github.com/owner/project/issues/1")


def test_cancelling_public_search_reaps_its_worker(monkeypatch):
    from periscope.investigations import web_search

    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()

        class Worker:
            returncode = None
            async def communicate(self, data):
                started.set()
                await asyncio.Future()
            def kill(self):
                self.returncode = -1
                stopped.set()
            async def wait(self):
                return self.returncode

        async def launch(*args, **kwargs):
            return Worker()

        monkeypatch.setattr(web_search.asyncio, "create_subprocess_exec", launch)
        task = asyncio.create_task(web_search._run_public_search("admissions", 30))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert stopped.is_set()
    asyncio.run(scenario())


def test_public_search_worker_emits_portable_json_for_chinese(monkeypatch):
    import io
    import json
    import sys
    from types import SimpleNamespace
    from periscope.investigations import web_worker

    class Search:
        def text(self, query, **kwargs):
            return [{"title": "计算机考研", "href": "https://example.org/", "body": "招生信息"}]

    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"query":"admissions"}\n'))
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=Search))
    assert web_worker.main() == 0
    assert output.getvalue().isascii()
    assert json.loads(output.getvalue())["results"][0]["title"] == "计算机考研"


def test_explicit_search_result_can_be_saved_when_exact_question_is_absent(monkeypatch, scratch_path):
    import httpx
    from periscope.collection.crawling import engine
    from periscope.investigations.crawl import stream_crawl
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def public(url):
            return url
        async def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /")
            return httpx.Response(200, text="<main><h1>2025 招生录取</h1><p>计算机专业录取名单</p></main>",
                                  headers={"content-type": "text/html"})
        client_type = httpx.AsyncClient
        monkeypatch.setattr(engine, "validate_public_http_url", public)
        monkeypatch.setattr(engine.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
        corpus = Corpus(scratch_path / "seed.db")
        options = engine.CrawlOptions(("https://lab.example/admissions",), max_pages=1, depth=0, budget=30, include_seed=True)
        runner = InvestigationRunner(corpus, lambda message: stream_crawl(message, options))
        rid = runner.submit(runner.store.create("考研"), "请帮我分析计算机考研哪些学校录取率比较高", "one")
        await runner.wait(rid)
        assert runner.store.references(rid) and corpus.search("计算机")
        corpus.close()
    asyncio.run(scenario())
