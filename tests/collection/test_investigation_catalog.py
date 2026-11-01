"""Existing source adapters must provide durable investigation evidence."""

import asyncio

import httpx
import pytest

from src.corpus.store import Corpus


def test_github_search_saves_candidates_then_issue_and_discussion(monkeypatch, scratch_path):
    from periscope.collection.platforms import github
    from periscope.collection.strategies import broad
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.catalog import stream_catalog_source
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "github.db")
        calls = []

        async def search(topic, plan, **kwargs):
            assert topic == "python" and [entry.key for entry in plan.selected] == ["github_issues"]
            return [{"title": "Python issue", "url": "https://github.com/example/repo/issues/12",
                     "text": "search excerpt", "author": "maintainer"}], {"source_reports": [{"status": "ok"}]}

        async def detail(owner, repo, number, **kwargs):
            assert (owner, repo, number) == ("example", "repo", "12")
            assert corpus.search("search excerpt")
            calls.append("detail")
            return {"text": "full python issue", "author": "maintainer"}, {"status": "complete", "errors": [], "truncated_by": []}

        async def comments(owner, repo, number, **kwargs):
            assert corpus.search("full python issue")
            calls.append("comments")
            return [{"id": "23", "text": "python workaround", "user": "reader", "discussion_type": "issue"}], {
                "status": "complete", "errors": [], "truncated_by": []}

        monkeypatch.setattr(broad, "collect_async", search)
        monkeypatch.setattr(github, "fetch_issue_detail", detail)
        monkeypatch.setattr(github, "fetch_discussion", comments)
        runner = InvestigationRunner(corpus, lambda message: stream_catalog_source(message, "github_issues", budget=30))
        round_id = runner.submit(runner.store.create("python"), "python", "one")
        await runner.wait(round_id)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(round_id)]
        assert calls == ["detail", "comments"]
        assert runner.store.get_round(round_id)["status"] == "completed"
        assert {item.get("content_level") for item in snapshots if item["type"] == "issue"} == {"full", "search_result"}
        reply = next(item for item in snapshots if item["type"] == "reply")
        assert reply["text"] == "python workaround" and reply["parent_native_id"] == "example/repo#12"
        corpus.close()

    asyncio.run(scenario())


def test_catalog_follows_most_relevant_lead_not_just_first_search_result(monkeypatch):
    from periscope.collection.platforms import github
    from periscope.collection.strategies import broad
    from periscope.investigations.catalog import stream_catalog_source
    from periscope.investigations.crawl import CollectionIncomplete

    async def scenario():
        followed = []

        async def search(topic, plan, **kwargs):
            return [{"title": "Unrelated", "text": "old discussion", "url": "https://github.com/a/b/issues/1"},
                    {"title": "Python memory leak", "text": "python memory leak diagnosis", "url": "https://github.com/a/b/issues/2"}], {
                        "source_reports": [{"status": "ok"}]}

        async def detail(owner, repo, number, **kwargs):
            followed.append(number)
            return {"text": "details"}, {"status": "complete"}

        async def comments(*args, **kwargs):
            return [], {"status": "complete"}

        monkeypatch.setattr(broad, "collect_async", search)
        monkeypatch.setattr(github, "fetch_issue_detail", detail)
        monkeypatch.setattr(github, "fetch_discussion", comments)
        try:
            async for _ in stream_catalog_source("python memory leak", "github_issues", budget=30, detail_limit=1):
                pass
        except CollectionIncomplete:
            pass
        assert followed == ["2"]
    asyncio.run(scenario())


def test_explicit_github_issue_uses_platform_detail_and_saves_replies(monkeypatch, scratch_path):
    from periscope.collection.crawling.engine import CrawlOptions
    from periscope.collection.platforms import github
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.crawl import stream_crawl
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        calls = []

        async def detail(owner, repo, number, **kwargs):
            calls.append((owner, repo, number))
            return {"text": "full issue body"}, {"status": "complete"}

        async def comments(owner, repo, number, **kwargs):
            assert kwargs["max_comments"] == 20
            return [{"id": "33", "text": "a useful reply"}], {"status": "complete"}

        monkeypatch.setattr(github, "fetch_issue_detail", detail)
        monkeypatch.setattr(github, "fetch_discussion", comments)
        corpus = Corpus(scratch_path / "explicit-issue.db")
        options = CrawlOptions(("https://github.com/example/repo/issues/12",), max_pages=4, depth=2, budget=30)
        runner = InvestigationRunner(corpus, lambda query: stream_crawl(query, options, reply_limit=20))
        rid = runner.submit(runner.store.create("python"), "深入这条讨论", "one")
        await runner.wait(rid)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(rid)]
        assert calls == [("example", "repo", "12")]
        assert any(item["type"] == "issue" and item["text"] == "full issue body" for item in snapshots)
        assert any(item["type"] == "reply" and item["text"] == "a useful reply" for item in snapshots)
        corpus.close()

    asyncio.run(scenario())


def test_multiple_explicit_platform_links_each_receive_detail_and_replies(monkeypatch, scratch_path):
    from periscope.collection.crawling.engine import CrawlOptions
    from periscope.collection.platforms import github
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.crawl import stream_crawl
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        called = []

        async def detail(owner, repo, number, **kwargs):
            called.append(number)
            return {"text": "issue " + number}, {"status": "complete"}

        async def comments(owner, repo, number, **kwargs):
            return [{"id": "reply-" + number, "text": "reply " + number}], {"status": "complete"}

        monkeypatch.setattr(github, "fetch_issue_detail", detail)
        monkeypatch.setattr(github, "fetch_discussion", comments)
        corpus = Corpus(scratch_path / "multiple-seeds.db")
        seeds = ("https://github.com/example/repo/issues/1", "https://github.com/example/repo/issues/2")
        options = CrawlOptions(seeds, max_pages=4, depth=2, budget=30)
        runner = InvestigationRunner(corpus, lambda query: stream_crawl(query, options))
        rid = runner.submit(runner.store.create("python"), "read both", "one")
        await runner.wait(rid)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(rid)]
        assert called == ["1", "2"]
        assert {item["text"] for item in snapshots if item.get("type") == "reply"} == {"reply 1", "reply 2"}
        corpus.close()
    asyncio.run(scenario())


def test_stackoverflow_question_answers_and_comments_are_separate(monkeypatch, scratch_path):
    from periscope.collection.platforms import stackoverflow
    from periscope.collection.strategies import broad
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.catalog import stream_catalog_source
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def search(topic, plan, **kwargs):
            return [{"title": "Python question", "url": "https://stackoverflow.com/questions/10/example",
                     "text": "search snippet"}], {"source_reports": [{"status": "ok"}]}

        async def detail(question_id, **kwargs):
            assert question_id == "10"
            return ({"text": "original question", "author": "asker"},
                    [{"id": "11", "text": "accepted answer", "discussion_type": "answer",
                      "replies": [{"id": "12", "text": "answer comment", "parent_id": "11"}]}],
                    [{"id": "13", "text": "question comment", "parent_id": "10"}],
                    {"status": "complete", "errors": [], "truncated_by": []})

        monkeypatch.setattr(broad, "collect_async", search)
        monkeypatch.setattr(stackoverflow, "fetch_discussion", detail)
        corpus = Corpus(scratch_path / "stack.db")
        runner = InvestigationRunner(corpus, lambda message: stream_catalog_source(message, "stackexchange_search", budget=30))
        rid = runner.submit(runner.store.create("python"), "python", "one")
        await runner.wait(rid)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(rid)]
        assert next(item for item in snapshots if item["type"] == "answer")["text"] == "accepted answer"
        assert next(item for item in snapshots if item.get("native_id") == "12")["parent_native_id"] == "11"
        assert next(item for item in snapshots if item.get("native_id") == "13")["parent_native_id"] == "10"
        corpus.close()

    asyncio.run(scenario())


def test_stackoverflow_answer_comment_keeps_its_own_type_even_when_ids_match(monkeypatch, scratch_path):
    from periscope.collection.platforms import stackoverflow
    from periscope.collection.strategies import broad
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.catalog import stream_catalog_source
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def search(topic, plan, **kwargs):
            return [{"title": "Python question", "url": "https://stackoverflow.com/questions/10/example"}], {
                "source_reports": [{"status": "ok"}]}

        async def detail(question_id, **kwargs):
            return ({"text": "question"},
                    [{"id": "11", "text": "the answer", "discussion_type": "answer",
                      "replies": [{"id": "11", "text": "comment on answer", "discussion_type": "answer", "parent_id": "11"}]}],
                    [], {"status": "complete"})

        monkeypatch.setattr(broad, "collect_async", search)
        monkeypatch.setattr(stackoverflow, "fetch_discussion", detail)
        corpus = Corpus(scratch_path / "stack-comment-id.db")
        runner = InvestigationRunner(corpus, lambda query: stream_catalog_source(query, "stackexchange_search", budget=30))
        rid = runner.submit(runner.store.create("python"), "python", "one")
        await runner.wait(rid)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(rid)]
        assert any(item.get("type") == "answer" and item["text"] == "the answer" for item in snapshots)
        assert any(item.get("type") == "reply" and item["text"] == "comment on answer" for item in snapshots)
        corpus.close()
    asyncio.run(scenario())


def test_stackoverflow_zero_depth_does_not_request_answers_or_comments():
    from periscope.collection.platforms import stackoverflow

    async def scenario():
        paths = []

        async def handler(request):
            paths.append(request.url.path)
            return httpx.Response(200, json={"items": [{"question_id": 10, "title": "question", "body": "body", "answer_count": 1}], "has_more": False})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await stackoverflow.fetch_discussion("10", max_answers=0, max_comments=0, client=client)
        assert paths == ["/2.3/questions/10"]
    asyncio.run(scenario())


@pytest.mark.parametrize("source,module,url,root_type,native_id", [
    ("zhihu_search", "zhihu", "https://www.zhihu.com/question/45/answer/123", "answer", "123"),
    ("weibo_search", "weibo", "https://weibo.com/detail/abc123", "post", "abc123"),
    ("bili_search", "bilibili", "https://www.bilibili.com/video/BV1234567890", "video", "BV1234567890"),
])
def test_chinese_source_can_save_body_and_discussion(monkeypatch, scratch_path, source, module, url, root_type, native_id):
    from importlib import import_module
    from periscope.collection.strategies import broad
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.catalog import stream_catalog_source
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def search(topic, plan, **kwargs):
            return [{"title": "考研讨论", "url": url, "text": "搜索摘要"}], {"source_reports": [{"status": "ok"}]}

        async def detail(*args, **kwargs):
            return ({"text": "完整的考研讨论", "author": "作者"},
                    [{"id": "777", "text": "考生的评论", "parent_id": native_id}],
                    {"status": "complete", "errors": [], "truncated_by": []})

        monkeypatch.setattr(broad, "collect_async", search)
        platform = import_module("periscope.collection.platforms." + module)
        monkeypatch.setattr(platform, "fetch_video_discussion" if module == "bilibili" else "fetch_discussion", detail, raising=False)
        corpus = Corpus(scratch_path / (module + ".db"))
        runner = InvestigationRunner(corpus, lambda query: stream_catalog_source(query, source, budget=30))
        rid = runner.submit(runner.store.create("考研"), "考研", "one")
        await runner.wait(rid)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(rid)]
        assert any(item["type"] == root_type and item["text"] == "完整的考研讨论" for item in snapshots)
        assert next(item for item in snapshots if item["type"] == "reply")["parent_native_id"] == native_id
        assert runner.store.get_round(rid)["status"] == "completed"
        corpus.close()
    asyncio.run(scenario())


def test_bilibili_text_discussion_does_not_fetch_subtitles(monkeypatch):
    from periscope.collection.platforms import bilibili

    async def scenario():
        calls = []

        async def get(client, url, params):
            calls.append(url)
            assert url == bilibili.API_VIEW
            return {"data": {"aid": 123, "title": "考研", "desc": "文字简介", "owner": {"name": "作者"}}}

        async def comments(client, aid, limit, **kwargs):
            assert aid == 123 and limit == 10
            return [{"id": "44", "text": "考生评论"}], {"status": "complete", "errors": [], "truncated_by": []}

        monkeypatch.setattr(bilibili, "_json", get)
        monkeypatch.setattr(bilibili, "_comments", comments)
        async with httpx.AsyncClient() as client:
            detail, nodes, coverage = await bilibili.fetch_video_discussion("BV1234567890", max_comments=10, client=client)
        assert detail["text"] == "文字简介" and nodes[0]["text"] == "考生评论"
        assert coverage["status"] == "complete" and calls == [bilibili.API_VIEW]
    asyncio.run(scenario())


def test_bilibili_search_av_url_uses_bvid_for_followup(monkeypatch, scratch_path):
    from periscope.collection.strategies import broad
    from periscope.collection.platforms import bilibili
    from periscope.investigations.catalog import stream_catalog_source
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        seen = []

        async def search(topic, plan, **kwargs):
            return [{"title": "考研讨论", "url": "http://www.bilibili.com/video/av123",
                     "text": "摘要", "ids": {"bvid": "BV1234567890", "aid": 123}}], {"source_reports": [{"status": "ok"}]}

        async def detail(video_id, **kwargs):
            seen.append(video_id)
            return {"text": "视频简介"}, [{"id": "456", "text": "评论"}], {"status": "complete"}

        monkeypatch.setattr(broad, "collect_async", search)
        monkeypatch.setattr(bilibili, "fetch_video_discussion", detail)
        corpus = Corpus(scratch_path / "bili-av.db")
        runner = InvestigationRunner(corpus, lambda query: stream_catalog_source(query, "bili_search", budget=30))
        rid = runner.submit(runner.store.create("考研"), "考研", "one")
        await runner.wait(rid)
        assert seen == ["BV1234567890"] and corpus.search("视频简介")
        corpus.close()
    asyncio.run(scenario())


def test_bilibili_can_read_a_direct_av_link_without_subtitles(monkeypatch):
    from periscope.collection.platforms import bilibili

    async def scenario():
        async def get(client, url, params):
            assert url == bilibili.API_VIEW and params == {"aid": 123}
            return {"data": {"aid": 123, "title": "video", "desc": "description"}}

        async def comments(client, aid, limit, **kwargs):
            return [], {"status": "complete", "errors": [], "truncated_by": []}

        monkeypatch.setattr(bilibili, "_json", get)
        monkeypatch.setattr(bilibili, "_comments", comments)
        async with httpx.AsyncClient() as client:
            detail, _, coverage = await bilibili.fetch_video_discussion("av123", client=client)
        assert detail["text"] == "description" and coverage["status"] == "complete"
    asyncio.run(scenario())


def test_bilibili_detail_reports_http_status_without_response_body():
    from periscope.collection.platforms import bilibili

    async def scenario():
        async def handler(request):
            return httpx.Response(412, text="private diagnostic details")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            detail, comments, coverage = await bilibili.fetch_video_discussion("BV1234567890", client=client)
        assert not detail and not comments and coverage["errors"] == ["video:http_412"]
    asyncio.run(scenario())


def test_bilibili_real_comment_format_is_saved_with_reply_relationship(monkeypatch, scratch_path):
    from periscope.collection.strategies import broad
    from periscope.collection.platforms import bilibili
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.catalog import stream_catalog_source
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def search(topic, plan, **kwargs):
            return [{"title": "video", "url": "https://www.bilibili.com/video/BV1234567890"}], {
                "source_reports": [{"status": "ok"}]}

        async def get(client, url, params):
            if url == bilibili.API_VIEW:
                return {"data": {"aid": 123, "title": "video", "desc": "description"}}
            assert url == bilibili.API_REPLY
            return {"data": {"page": {"count": 1}, "replies": [
                {"rpid": 77, "content": {"message": "root comment"}, "member": {"uname": "reader"}, "rcount": 1,
                 "replies": [{"rpid": 78, "parent": 77, "content": {"message": "nested reply"}, "member": {"uname": "writer"}}]}
            ]}}

        monkeypatch.setattr(broad, "collect_async", search)
        monkeypatch.setattr(bilibili, "_json", get)
        corpus = Corpus(scratch_path / "bili-native-comments.db")
        runner = InvestigationRunner(corpus, lambda query: stream_catalog_source(query, "bili_search", budget=30))
        rid = runner.submit(runner.store.create("video"), "video", "one")
        await runner.wait(rid)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(rid)]
        replies = {item["native_id"]: item for item in snapshots if item.get("type") == "reply"}
        assert replies["77"]["text"] == "root comment" and replies["77"]["author"] == "reader"
        assert replies["78"]["parent_native_id"] == "77" and replies["78"]["author"] == "writer"
        corpus.close()
    asyncio.run(scenario())


def test_catalog_search_records_academic_metadata_without_fabricating_full_text(monkeypatch, scratch_path):
    from periscope.collection.strategies import broad
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.catalog import stream_catalog_source
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def search(topic, plan, **kwargs):
            return [{"title": "A paper", "url": "https://arxiv.org/abs/1234.5678", "text": "abstract"}], {
                "source_reports": [{"status": "ok"}]}
        monkeypatch.setattr(broad, "collect_async", search)
        corpus = Corpus(scratch_path / "academic.db")
        runner = InvestigationRunner(corpus, lambda message: stream_catalog_source(message, "arxiv_ai", budget=30))
        rid = runner.submit(runner.store.create("paper"), "paper", "one")
        await runner.wait(rid)
        item = EvidenceVersions(corpus).get_version(**runner.store.references(rid)[0])
        assert item["type"] == "paper" and item["text"] == "abstract"
        assert item["content_level"] == "search_result"
        corpus.close()
    asyncio.run(scenario())


def test_unhandled_zhihu_result_remains_visible_as_a_search_lead(monkeypatch, scratch_path):
    from periscope.collection.strategies import broad
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.catalog import stream_catalog_source
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        async def search(topic, plan, **kwargs):
            return [{"title": "考研问题", "url": "https://www.zhihu.com/question/123", "text": "相关讨论摘要"},
                    {"title": "untrusted", "url": "https://example.org/question/123", "text": "wrong host"}], {
                        "source_reports": [{"status": "ok"}]}

        monkeypatch.setattr(broad, "collect_async", search)
        corpus = Corpus(scratch_path / "unhandled.db")
        runner = InvestigationRunner(corpus, lambda query: stream_catalog_source(query, "zhihu_search", budget=30))
        rid = runner.submit(runner.store.create("考研"), "考研", "one")
        await runner.wait(rid)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(rid)]
        assert len(snapshots) == 1 and snapshots[0]["text"] == "相关讨论摘要"
        assert snapshots[0]["content_level"] == "search_result"
        assert runner.store.get_round(rid)["status"] == "partial"
        corpus.close()
    asyncio.run(scenario())


def test_source_failure_is_not_reported_as_an_empty_or_partial_success(monkeypatch):
    from periscope.collection.strategies import broad
    from periscope.investigations import browser_sources, catalog, crawl, web_search
    from periscope.investigations.events import SourceProgress

    async def scenario():
        async def search(topic, plan, **kwargs):
            return [], {"source_reports": [{"status": "failed", "error": "HTTP 429 (throttled)"}]}

        async def empty(topic, **kwargs):
            if False:
                yield []

        monkeypatch.setattr(broad, "collect_async", search)
        monkeypatch.setattr(crawl, "stream_v2ex_search", empty)
        monkeypatch.setattr(crawl, "stream_hn_search", empty)
        monkeypatch.setattr(web_search, "stream_public_search", empty)
        monkeypatch.setattr(web_search, "stream_search_api", empty)
        monkeypatch.setattr(browser_sources, "stream_browser_source", empty)
        events = []
        try:
            async for item in crawl.stream_auto_search("python", budget=30, emit_progress=True):
                if isinstance(item, SourceProgress):
                    events.append(item)
        except crawl.CollectionIncomplete:
            pass
        final = [event for event in events if event.source == "github_issues"][-1]
        assert final.status == "failed" and "search_http_429" in final.reason
    asyncio.run(scenario())


def test_github_and_stackoverflow_cancel_their_owned_requests():
    from periscope.collection.platforms import github, stackoverflow

    async def check(fetch):
        started, stopped = [], []
        ready = asyncio.Event()

        async def handler(request):
            started.append(str(request.url))
            if len(started) >= 2:
                ready.set()
            try:
                await asyncio.Future()
            finally:
                stopped.append(str(request.url))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(fetch(client))
            await asyncio.wait_for(ready.wait(), 2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0)
            assert sorted(started) == sorted(stopped)

    async def scenario():
        await check(lambda client: github.fetch_discussion("owner", "repo", "1", is_pull_request=True, client=client))
        await check(lambda client: stackoverflow.fetch_discussion("1", client=client))
    asyncio.run(scenario())


def test_default_discovery_runs_catalog_sources_in_addition_to_existing_communities(monkeypatch):
    from periscope.investigations import browser_sources, catalog, crawl, web_search

    async def legacy(topic, **kwargs):
        yield [{"source": "legacy", "text": topic}]

    async def extra(topic, source_key, **kwargs):
        yield [{"source": source_key, "text": topic}]

    async def public(topic, **kwargs):
        yield [{"source": "public_web", "text": topic}]

    async def api(topic, **kwargs):
        yield [{"source": "search_api", "text": topic}]

    async def browser(topic, source, **kwargs):
        yield [{"source": source, "text": topic}]

    monkeypatch.setattr(crawl, "stream_v2ex_search", legacy)
    monkeypatch.setattr(crawl, "stream_hn_search", legacy)
    monkeypatch.setattr(catalog, "stream_catalog_source", extra)
    monkeypatch.setattr(web_search, "stream_public_search", public)
    monkeypatch.setattr(web_search, "stream_search_api", api)
    monkeypatch.setattr(browser_sources, "stream_browser_source", browser)

    async def scenario():
        rows = []
        async for batch in crawl.stream_auto_search("python", budget=30):
            rows.extend(batch)
        assert {row["source"] for row in rows} == {"legacy", "public_web", "search_api", *catalog.CATALOG_SOURCES,
                                                   "xhs_browser", "douyin_browser", "kuaishou_browser"}
        assert len(rows) == len(catalog.CATALOG_SOURCES) + 7
    asyncio.run(scenario())


def test_slow_discussion_does_not_block_other_sources_from_starting(monkeypatch):
    from periscope.investigations import browser_sources, catalog, crawl, web_search

    async def scenario():
        started = set()
        ninth_started = asyncio.Event()
        slow_details = asyncio.Event()

        async def collect(name):
            started.add(name)
            if len(started) >= 9:
                ninth_started.set()
            yield [{"source": name, "text": "a search result"}]
            await slow_details.wait()

        def legacy(name):
            def worker(topic, **kwargs):
                return collect(name)
            return worker

        def extra(topic, source_key, **kwargs):
            return collect(source_key)

        monkeypatch.setattr(crawl, "stream_v2ex_search", legacy("v2ex"))
        monkeypatch.setattr(crawl, "stream_hn_search", legacy("hn"))
        monkeypatch.setattr(catalog, "stream_catalog_source", extra)
        monkeypatch.setattr(web_search, "stream_public_search", legacy("public_web"))
        monkeypatch.setattr(web_search, "stream_search_api", legacy("search_api"))
        monkeypatch.setattr(browser_sources, "stream_browser_source", lambda topic, source, **kwargs: collect(source))

        async def consume():
            async for _ in crawl.stream_auto_search("python", budget=30):
                pass

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(ninth_started.wait(), 1)
            assert len(started) >= 9
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_cancelling_broad_search_cleans_its_source_task(monkeypatch):
    from periscope.collection.registry import SourcePlan, SourceRegistry
    from periscope.collection.strategies import broad
    from periscope.collection.transport import client as transport

    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()

        async def collect(*args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        monkeypatch.setattr(transport, "collect", collect)
        plan = SourcePlan((SourceRegistry().get("github_issues"),))
        task = asyncio.create_task(broad.collect_async("python", plan, budget=30))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert stopped.is_set()
    asyncio.run(scenario())


def test_broad_search_uses_real_request_build_and_send(monkeypatch):
    from periscope.collection.registry import SourcePlan, SourceRegistry
    from periscope.collection.strategies import broad

    client_type = httpx.AsyncClient
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [{"title": "Python issue",
            "html_url": "https://github.com/example/repo/issues/12", "body": "the issue",
            "number": 12, "user": {"login": "author"}}]})

    monkeypatch.setattr(broad.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(handler), **kwargs))

    async def scenario():
        plan = SourcePlan((SourceRegistry().get("github_issues"),))
        rows, stats = await broad.collect_async("python", plan, budget=5)
        assert len(requests) == 1 and requests[0].method == "GET"
        assert len(rows) == 1 and rows[0]["url"] == "https://github.com/example/repo/issues/12"
        assert stats["source_reports"][0]["status"] == "ok"
    asyncio.run(scenario())


@pytest.mark.parametrize("source,payload,expected", [
    ("weibo_search", {"statuses": [{"mblogid": "AbC123", "id": "123456789", "text_raw": "考研讨论"}]},
     "https://weibo.com/detail/AbC123"),
    ("juejin_search", {"data": [{"result_model": {"article_info": {"title": "Python article", "article_id": "1234567890123456789", "brief_content": "article"}}}]},
     "https://juejin.cn/post/1234567890123456789"),
    ("zhihu_search", {"data": [{"object": {"title": "考研回答", "url": "https://api.zhihu.com/answers/123", "excerpt": "answer"}}]},
     "https://www.zhihu.com/answer/123"),
])
def test_broad_search_turns_real_platform_identifiers_into_detail_urls(monkeypatch, source, payload, expected):
    from periscope.collection.registry import SourcePlan, SourceRegistry
    from periscope.collection.strategies import broad
    from periscope.investigations.catalog import _target

    client_type = httpx.AsyncClient

    async def handler(request):
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(broad.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(handler), **kwargs))

    async def scenario():
        plan = SourcePlan((SourceRegistry().get(source),))
        rows, stats = await broad.collect_async("python", plan, budget=5)
        assert len(rows) == 1 and rows[0]["url"] == expected
        assert _target(source, rows[0]["url"], ids=rows[0].get("ids"))
        assert stats["source_reports"][0]["status"] == "ok"
    asyncio.run(scenario())


def test_expanded_history_can_reuse_a_previously_captured_platform(scratch_path):
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "expanded-history.db")

        async def collect(message):
            if message == "first":
                yield [{"source": "github_issues", "kind": "community", "_origin": "platform",
                        "title": "python issue", "text": "python workaround",
                        "url": "https://github.com/example/repo/issues/12",
                        "evidence": {"source": "github", "type": "issue", "native_id": "example/repo#12"}}]

        runner = InvestigationRunner(corpus, collect)
        investigation = runner.store.create("python")
        await runner.wait(runner.submit(investigation, "first", "one"))
        second = runner.submit(investigation, "python", "two", options={"seeds": [], "max_pages": None})
        await runner.wait(second)
        refs = runner.store.references(second)
        assert len(refs) == 1 and EvidenceVersions(corpus).get_version(**refs[0])["text"] == "python workaround"
        assert runner.store.origin(second, refs[0])["kind"] == "reused"
        corpus.close()
    asyncio.run(scenario())
