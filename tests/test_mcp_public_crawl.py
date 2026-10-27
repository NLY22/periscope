import asyncio

import httpx


def test_public_crawl_is_registered_as_mcp_tool():
    from src.mcp.server import mcp

    assert "ps_crawl_public" in {tool.name for tool in asyncio.run(mcp.list_tools())}


def test_mcp_crawls_public_form_and_returns_text_and_provenance(monkeypatch):
    from periscope.collection.crawling import engine
    from src.mcp.server import ps_crawl_public

    async def public(url):
        return url

    async def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.path == "/form":
            return httpx.Response(200, text='<form action="/result"><input name="year"></form>',
                                  headers={"content-type": "text/html"})
        assert request.url.params["year"] == "2026"
        return httpx.Response(200, text="<main><h1>robot admission</h1><p>robot score 365</p></main>",
                              headers={"content-type": "text/html"})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(engine, "validate_public_http_url", public)
    monkeypatch.setattr(engine.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
    result = asyncio.run(ps_crawl_public(topic="robot", seeds=["https://lab.example/form"],
                                         form_fields={"year": "2026"}, max_pages=2, depth=1, budget=10))
    assert result["ok"] is True
    row = next(row for row in result["data"]["rows"] if "/result?" in row["url"])
    assert "robot score 365" in (row.get("body") or row.get("text"))
    assert row["provenance"]["query_fields"] == {"year": "2026"}
    assert any(report["status"] == "ok" for report in result["data"]["reports"])


def test_mcp_crawl_rejects_unbounded_or_invalid_requests():
    from src.mcp.server import ps_crawl_public

    for kwargs in ({"topic": " ", "seeds": ["https://example.org"]},
                   {"topic": "robot", "seeds": []},
                   {"topic": "robot", "seeds": ["https://example.org"], "budget": 0},
                   {"topic": "robot", "seeds": ["https://example.org"], "max_pages": 10000},
                   {"topic": "robot", "seeds": ["https://example.org"], "depth": 0,
                    "form_fields": {"year": "2026"}}):
        result = asyncio.run(ps_crawl_public(**kwargs))
        assert result["ok"] is False
        assert result["error"]["code"] == "PS_INVALID_INPUT"
