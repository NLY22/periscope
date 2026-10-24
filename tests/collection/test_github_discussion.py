"""GitHub issue and pull request discussions must preserve source relationships."""

from __future__ import annotations

import asyncio

import httpx


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

    monkeypatch.setattr(github, "fetch_discussion", fetch_discussion)
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
    assert result.raw_rows[0]["discussion_coverage"]["status"] == "complete"
