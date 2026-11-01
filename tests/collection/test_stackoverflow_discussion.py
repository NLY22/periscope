"""Stack Overflow enrichment must preserve answers and post-level comments."""

from __future__ import annotations

import asyncio
import json

import httpx


def test_stackoverflow_search_hit_keeps_question_id():
    from periscope.collection.registry import SourceRegistry
    from periscope.collection.transport.parsing import extract_rows

    source = SourceRegistry().get("stackexchange_search").source
    rows = extract_rows(source, json.dumps({"items": [{
        "question_id": 123, "title": "A question", "link": "https://stackoverflow.com/questions/123/a-question",
        "body": "Question text", "owner": {"display_name": "writer"},
    }]}), "application/json")

    assert rows[0]["ids"] == {"question_id": 123}


def test_stackoverflow_fetches_answer_bodies_and_groups_comments_by_post():
    from periscope.collection.platforms.stackoverflow import fetch_discussion

    calls = []

    async def route(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/questions/123"):
            return httpx.Response(200, json={"items": [{
                "question_id": 123, "title": "Question", "body": "<p>Full question body</p>",
                "owner": {"display_name": "asker"}, "answer_count": 1,
                "accepted_answer_id": 201, "tags": ["python"],
            }], "has_more": False})
        if request.url.path.endswith("/questions/123/answers"):
            return httpx.Response(200, json={"items": [{
                "answer_id": 201, "question_id": 123, "body": "<p>Answer body</p>",
                "owner": {"display_name": "solver"}, "score": 8,
                "link": "https://stackoverflow.com/a/201",
            }], "has_more": False})
        if request.url.path.endswith("/posts/123;201/comments"):
            return httpx.Response(200, json={"items": [
                {"comment_id": 301, "post_id": 123, "post_type": "question",
                 "body": "Question comment", "owner": {"display_name": "reader"},
                 "link": "https://stackoverflow.com/questions/123#comment301_123"},
                {"comment_id": 302, "post_id": 201, "post_type": "answer",
                 "body": "Answer clarification", "owner": {"display_name": "reviewer"},
                 "reply_to_user": {"display_name": "solver"},
                 "link": "https://stackoverflow.com/questions/123#comment302_201"},
            ], "has_more": False})
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion(123, client=client)

    question, answers, comments, coverage = asyncio.run(run())

    assert question["text"] == "Full question body"
    assert question["author"] == "asker"
    assert question["detail"]["accepted_answer_id"] == 201
    assert answers[0]["text"] == "Answer body"
    assert answers[0]["parent_id"] == "123"
    assert answers[0]["replies"][0]["text"] == "Answer clarification"
    assert answers[0]["replies"][0]["parent_id"] == "201"
    assert answers[0]["replies"][0]["reply_to_user"] == "solver"
    assert comments[0]["text"] == "Question comment"
    assert comments[0]["parent_id"] == "123"
    assert coverage["status"] == "complete"
    assert coverage["fetched_answers"] == 1
    assert coverage["fetched_comments"] == 2
    assert "/2.3/questions/123" in calls
    assert "/2.3/questions/123/answers" in calls
    assert "/2.3/posts/123;201/comments" in calls


def test_stackoverflow_keeps_partial_answers_when_comments_exceed_budget():
    from periscope.collection.platforms.stackoverflow import fetch_discussion

    async def route(request):
        if request.url.path.endswith("/questions/456"):
            return httpx.Response(200, json={"items": [{"question_id": 456,
                "body": "Question", "answer_count": 1}], "has_more": False})
        if request.url.path.endswith("/questions/456/answers"):
            return httpx.Response(200, json={"items": [{"answer_id": 460,
                "question_id": 456, "body": "Answer", "owner": {}}], "has_more": False})
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"items": [], "has_more": False})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion(456, budget_seconds=0.05, client=client)

    question, answers, comments, coverage = asyncio.run(run())

    assert question["text"] == "Question"
    assert answers[0]["text"] == "Answer"
    assert comments == []
    assert coverage["status"] == "partial"
    assert "time_budget" in coverage["truncated_by"]


def test_stackoverflow_missing_question_is_not_reported_as_complete():
    from periscope.collection.platforms.stackoverflow import fetch_discussion

    async def route(_request):
        return httpx.Response(200, json={"items": [], "has_more": False})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await fetch_discussion(999999999, client=client)

    question, answers, comments, coverage = asyncio.run(run())

    assert question == {} and answers == [] and comments == []
    assert coverage["status"] == "partial"
    assert "question:missing" in coverage["errors"]


def test_topic_service_routes_stackoverflow_hit_through_enrichment(monkeypatch):
    from periscope.collection.platforms import stackoverflow
    from periscope.topics.models import TopicRequest
    from periscope.topics.service import TopicService

    observed = {}

    async def fetch_discussion(question_id, *, max_answers=10, max_comments=60,
                               budget_seconds=None):
        observed.update(question_id=question_id, budget_seconds=budget_seconds)
        return ({"text": "Full question", "author": "asker",
                 "detail": {"accepted_answer_id": 201}},
                [{"id": "answer-1", "user": "solver", "text": "Answer text",
                  "parent_id": "123", "replies": []}],
                [{"id": "comment-1", "user": "reader", "text": "Question comment",
                  "parent_id": "123"}],
                {"status": "complete", "fetched_answers": 1, "fetched_comments": 1,
                 "truncated_by": [], "errors": []})

    monkeypatch.setattr(stackoverflow, "fetch_discussion", fetch_discussion)
    question = {"title": "Stack question", "url": "https://stackoverflow.com/questions/123/test",
                "source": "stackexchange_search", "kind": "community",
                "ids": {"question_id": 123}, "text": "Search body", "_origin": "platform"}
    service = TopicService(
        local_lookup=lambda *_: ([], 0), api_lookup=lambda *_: ([], 0.0, ""),
        platform_lookup=lambda *_args, **_kwargs: ([question], {"elapsed": 2.0,
            "failed": [], "source_reports": []}), api_available=lambda: False)

    result = service.search(TopicRequest(topic="question", no_local=True, no_api=True,
                                         speed="normal", enrich=True, budget=7))

    assert observed == {"question_id": "123", "budget_seconds": 5}
    assert result.platform_rows[0]["answers"][0]["text"] == "Answer text"
    assert result.raw_rows[0]["discussion_coverage"]["status"] == "complete"
    assert result.raw_rows[0]["question_detail"]["accepted_answer_id"] == 201
