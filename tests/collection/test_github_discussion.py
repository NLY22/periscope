"""GitHub issue and pull request discussions must preserve source relationships."""

from __future__ import annotations

import asyncio

import httpx


def test_issue_detail_retrieves_body_beyond_search_excerpt():
    from periscope.collection.platforms import github

    fetch_detail = getattr(github, "fetch_issue_detail", None)
    assert callable(fetch_detail), "GitHub issue detail retrieval is missing"
    body = "Long issue body. " * 150

    async def route(request):
        assert request.url.path == "/repos/org/project/issues/7"
        return httpx.Response(200, json={"number": 7, "body": body,
                                        "user": {"login": "author"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_detail("org", "project", 7, client=client)

    detail, coverage = asyncio.run(run())
    assert detail["text"] == body
    assert detail["author"] == "author"
    assert coverage["status"] == "complete"


def test_issue_detail_reports_http_failure_without_fabricating_body():
    from periscope.collection.platforms import github

    fetch_detail = getattr(github, "fetch_issue_detail", None)
    assert callable(fetch_detail), "GitHub issue detail retrieval is missing"

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(403))) as client:
            return await fetch_detail("org", "project", 7, client=client)

    detail, coverage = asyncio.run(run())
    assert detail == {}
    assert coverage["status"] == "partial"
    assert "detail:http_403" in coverage["errors"]


def test_issue_detail_marks_body_truncation():
    from periscope.collection.platforms.github import fetch_issue_detail

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"body": "x" * 30001}))) as client:
            return await fetch_issue_detail("org", "project", 7, client=client)

    detail, coverage = asyncio.run(run())
    assert len(detail["text"]) == 30000
    assert coverage["status"] == "partial"
    assert "body_limit" in coverage["truncated_by"]


def test_github_search_rows_keep_native_identity_for_discussion_enrichment():
    import json

    from periscope.collection.registry import SourceRegistry
    from periscope.collection.transport.parsing import extract_rows

    source = SourceRegistry().get("github_issues").source
    rows = extract_rows(source, json.dumps({"items": [{
        "number": 7,
        "repository_url": "https://api.github.com/repos/org/project",
        "pull_request": {"url": "https://api.github.com/repos/org/project/pulls/7"},
        "title": "A useful issue", "html_url": "https://github.com/org/project/pull/7",
        "body": "Description", "comments": 3,
    }]}), "application/json")

    assert rows[0]["ids"] == {
        "number": 7,
        "repository_url": "https://api.github.com/repos/org/project",
        "pull_request.url": "https://api.github.com/repos/org/project/pulls/7",
    }


def test_github_pr_discussion_collects_issue_comments_and_review_reply_chains():
    from periscope.collection.platforms.github import fetch_discussion

    calls = []

    async def route(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/issues/7/comments"):
            return httpx.Response(200, json=[{
                "id": 101, "user": {"login": "reader"}, "body": "Issue discussion",
                "created_at": "2026-10-24T00:00:00Z",
                "html_url": "https://github.com/org/project/issues/7#issuecomment-101",
            }])
        if request.url.path.endswith("/pulls/7/comments"):
            return httpx.Response(200, json=[
                {"id": 201, "user": {"login": "reviewer"}, "body": "Inline concern",
                 "created_at": "2026-10-24T00:01:00Z", "path": "src/main.py", "line": 12,
                 "html_url": "https://github.com/org/project/pull/7#discussion_r201"},
                {"id": 202, "in_reply_to_id": 201, "user": {"login": "author"},
                 "body": "Addressed this", "created_at": "2026-10-24T00:02:00Z",
                 "path": "src/main.py", "line": 12,
                 "html_url": "https://github.com/org/project/pull/7#discussion_r202"},
            ])
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("org", "project", 7, is_pull_request=True,
                                          client=client)

    comments, coverage = asyncio.run(run())

    issue = next(comment for comment in comments if comment["discussion_type"] == "issue")
    review = next(comment for comment in comments if comment["discussion_type"] == "review")
    assert issue["text"] == "Issue discussion"
    assert issue["url"].endswith("issuecomment-101")
    assert review["text"] == "Inline concern"
    assert review["replies"][0]["text"] == "Addressed this"
    assert review["replies"][0]["parent_id"] == "201"
    assert review["path"] == "src/main.py"
    assert coverage["status"] == "complete"
    assert coverage["fetched_comments"] == 3
    assert sorted(calls) == [
        "/repos/org/project/issues/7/comments", "/repos/org/project/pulls/7/comments"]


def test_github_discussion_keeps_results_returned_before_budget_expires():
    from periscope.collection.platforms.github import fetch_discussion

    async def route(request):
        if request.url.path.endswith("/issues/9/comments"):
            return httpx.Response(200, json=[{
                "id": 901, "user": {"login": "reader"}, "body": "Saved before timeout",
                "html_url": "https://github.com/org/project/issues/9#issuecomment-901",
            }])
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=[])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion("org", "project", 9, is_pull_request=True,
                                          budget_seconds=0.05, client=client)

    comments, coverage = asyncio.run(run())

    assert any(comment["text"] == "Saved before timeout" for comment in comments)
    assert coverage["status"] == "partial"
    assert "time_budget" in coverage["truncated_by"]


def test_topic_service_routes_github_pr_through_enrichment_and_keeps_raw_patch(monkeypatch):
    from periscope.collection.platforms import github
    from periscope.topics.models import TopicRequest
    from periscope.topics.service import TopicService

    observed = {}

    async def fetch_discussion(owner, repo, number, *, is_pull_request,
                               max_comments=60, max_pages=2, budget_seconds=None):
        observed.update(owner=owner, repo=repo, number=number,
                        is_pull_request=is_pull_request, budget_seconds=budget_seconds)
        return ([{"id": "88", "user": "reviewer", "text": "Review detail",
                  "parent_id": "", "url": "https://github.com/org/project/pull/7#discussion_r88",
                  "discussion_type": "review"}],
                {"status": "complete", "fetched_comments": 1,
                 "truncated_by": [], "errors": []})

    async def fetch_issue_detail(owner, repo, number, *, budget_seconds=None):
        return ({"text": "Complete original text " * 100, "author": "issue-author"},
                {"status": "complete", "truncated_by": [], "errors": []})

    monkeypatch.setattr(github, "fetch_discussion", fetch_discussion)
    monkeypatch.setattr(github, "fetch_issue_detail", fetch_issue_detail)
    pull = {"title": "Review request", "url": "https://github.com/org/project/pull/7",
            "source": "github_issues", "kind": "community",
            "ids": {"number": 7, "repository_url": "https://api.github.com/repos/org/project",
                    "pull_request.url": "https://api.github.com/repos/org/project/pulls/7"},
            "text": "Proposal", "_origin": "platform"}
    service = TopicService(
        local_lookup=lambda *_: ([], 0),
        api_lookup=lambda *_: ([], 0.0, ""),
        platform_lookup=lambda *_args, **_kwargs: ([pull], {"elapsed": 0.0,
                                                            "failed": [], "source_reports": []}),
        api_available=lambda: False,
    )

    result = service.search(TopicRequest(topic="review", no_local=True, no_api=True,
                                         speed="normal", enrich=True, budget=7))

    assert observed == {"owner": "org", "repo": "project", "number": "7",
                        "is_pull_request": True, "budget_seconds": 7}
    assert result.platform_rows[0]["comments"][0]["text"] == "Review detail"
    assert result.platform_rows[0]["text"] == "Complete original text " * 100
    assert result.raw_rows[0]["text"] == "Complete original text " * 100
    assert result.raw_rows[0]["author"] == "issue-author"
    assert result.raw_rows[0]["discussion_coverage"]["status"] == "complete"
    assert result.raw_rows[0]["discussion_coverage"]["detail"]["status"] == "complete"


def test_github_detail_failure_keeps_search_text_and_discussion(monkeypatch):
    from periscope.collection.platforms import github
    from periscope.topics.enrichment import _enrich_topk

    async def fetch_discussion(*args, **kwargs):
        return ([{"id": "1", "text": "Helpful comment"}],
                {"status": "complete", "fetched_comments": 1,
                 "truncated_by": [], "errors": []})

    async def fetch_issue_detail(*args, **kwargs):
        return ({}, {"status": "partial", "truncated_by": [],
                     "errors": ["detail:http_403"]})

    monkeypatch.setattr(github, "fetch_discussion", fetch_discussion)
    monkeypatch.setattr(github, "fetch_issue_detail", fetch_issue_detail)
    row = {"title": "Issue", "url": "https://github.com/org/project/issues/7",
           "source": "github_issues", "text": "Search excerpt"}
    rows, _, patches = _enrich_topk([row], budget_seconds=5)
    assert rows[0]["text"] == "Search excerpt"
    assert rows[0]["comments"][0]["text"] == "Helpful comment"
    assert patches[row["url"]]["discussion_coverage"]["status"] == "partial"
    assert "detail:http_403" in rows[0]["discussion_coverage"]["errors"]


def test_github_full_body_and_coverage_survive_persistence(scratch_path, monkeypatch):
    import json
    from periscope.collection.platforms import github
    from periscope.operations import ingest
    from periscope.topics import runs
    from periscope.topics.enrichment import _enrich_topk
    from src.corpus.store import Corpus

    async def fetch_detail(*args, **kwargs):
        return ({"text": "intro " * 300 + "githubfullbodyneedle", "author": "Alice"},
                {"status": "complete", "truncated_by": [], "errors": []})

    async def fetch_discussion(*args, **kwargs):
        return ([{"id": "1", "text": "githubcommentneedle", "user": "Bob"}],
                {"status": "complete", "fetched_comments": 1,
                 "truncated_by": [], "errors": []})

    monkeypatch.setattr(github, "fetch_issue_detail", fetch_detail)
    monkeypatch.setattr(github, "fetch_discussion", fetch_discussion)
    topic_dir = scratch_path / "topics"
    topic_dir.mkdir()
    monkeypatch.setattr(runs.paths, "TOPIC_DIR", topic_dir)
    row = {"_origin": "platform", "source": "github_issues", "kind": "community",
           "title": "Issue", "url": "https://github.com/org/project/issues/7",
           "text": "Search excerpt"}
    rows, _, _ = _enrich_topk([row], budget_seconds=5)
    shard = runs.write("github issue", rows)
    items, skipped = ingest.build(runs.ingestable_rows(runs.read(shard)), [])
    assert not skipped
    db_path = scratch_path / "github.db"
    corpus = Corpus(db_path)
    corpus.add_items(items)
    corpus.close()
    corpus = Corpus(db_path)
    try:
        assert corpus.search("githubfullbodyneedle")
        assert corpus.search("githubcommentneedle")
        stored = corpus._conn.execute("SELECT metadata_json FROM items WHERE url=?",
                                     (row["url"],)).fetchone()
        assert json.loads(stored["metadata_json"])["discussion_coverage"]["detail"]["status"] == "complete"
    finally:
        corpus.close()
