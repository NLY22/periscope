import asyncio

import httpx
from src.corpus.store import Corpus


def test_real_crawler_saves_first_page_before_fetching_next(monkeypatch, scratch_path):
    from periscope.collection.crawling import engine
    from periscope.investigations.crawl import stream_crawl
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "crawler.db")
        async def public(url):
            return url
        async def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /")
            if request.url.path == "/list":
                return httpx.Response(200, text='<main><h1>robot first</h1><p>robot evidence first</p><a href="/next">robot next</a></main>',
                                      headers={"content-type": "text/html"})
            assert corpus.search("robot evidence first")
            return httpx.Response(200, text='<main><h1>robot second</h1><p>robot evidence second</p></main>',
                                  headers={"content-type": "text/html"})

        client_type = httpx.AsyncClient
        monkeypatch.setattr(engine, "validate_public_http_url", public)
        monkeypatch.setattr(engine.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
        options = engine.CrawlOptions(("https://lab.example/list",), max_pages=2, depth=1, budget=10, delay=0)
        runner = InvestigationRunner(corpus, lambda message: stream_crawl(message, options))
        investigation = runner.store.create("robot")
        round_id = runner.submit(investigation, "robot", "key")
        await runner.wait(round_id)
        assert runner.store.get_round(round_id)["status"] == "completed"
        assert len(runner.store.references(round_id)) == 2
        assert corpus.search("robot evidence second")
        corpus.close()

    asyncio.run(scenario())


def test_failed_page_marks_round_partial_and_preserves_first(monkeypatch, scratch_path):
    from periscope.collection.crawling import engine
    from periscope.investigations.crawl import stream_crawl
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "partial.db")
        async def public(url):
            return url
        async def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /")
            if request.url.path == "/list":
                return httpx.Response(200, text='<main><h1>robot first</h1><a href="/next">robot next</a></main>',
                                      headers={"content-type": "text/html"})
            return httpx.Response(500)
        client_type = httpx.AsyncClient
        monkeypatch.setattr(engine, "validate_public_http_url", public)
        monkeypatch.setattr(engine.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
        options = engine.CrawlOptions(("https://lab.example/list",), max_pages=2, depth=1, budget=10, delay=0)
        runner = InvestigationRunner(corpus, lambda message: stream_crawl(message, options))
        investigation = runner.store.create("robot")
        round_id = runner.submit(investigation, "robot", "key")
        await runner.wait(round_id)
        assert runner.store.get_round(round_id)["status"] == "partial"
        assert len(runner.store.references(round_id)) == 1
        assert corpus.search("robot first")
        corpus.close()
    asyncio.run(scenario())
