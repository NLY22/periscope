"""User follow-ups should influence the actual searches, not only summaries."""

import asyncio
import json

from src.corpus.store import Corpus


def test_continue_searches_the_previous_instruction_and_main_subject(scratch_path):
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        seen = []
        corpus = Corpus(scratch_path / "followup.db")

        async def collector(query):
            seen.append(query)
            if False:
                yield []

        runner = InvestigationRunner(corpus, collector)
        inv = runner.store.create("计算机考研")
        first = runner.submit(inv, "计算机考研 择校", "one")
        await runner.wait(first)
        second = runner.submit(inv, "继续", "two")
        await runner.wait(second)
        assert seen == ["计算机考研 择校", "计算机考研 择校"]
        assert runner.store.get_round(second)["message"] == "继续"
        corpus.close()
    asyncio.run(scenario())


def test_search_plan_is_persisted_with_the_round_and_survives_restart(scratch_path):
    from periscope.investigations.planning import QueryPlan
    from periscope.investigations.runner import InvestigationRunner
    from periscope.investigations.store import InvestigationStore

    class Planner:
        async def plan(self, message, context):
            return QueryPlan("python memory", ("python leak", "python allocation"))

    async def scenario():
        database = scratch_path / "plan.db"
        corpus = Corpus(database)

        async def collect(query):
            assert query == "python memory"
            if False:
                yield []

        async def collect_plan(query, variants):
            assert query == "python memory" and variants == ("python leak", "python allocation")
            if False:
                yield []

        runner = InvestigationRunner(corpus, collect, planner=Planner())
        rid = runner.submit(runner.store.create("python"), "memory", "one", plan_collector=collect_plan)
        await runner.wait(rid)
        assert runner.store.query_plan(rid) == {"query": "python memory", "variants": ["python leak", "python allocation"]}
        corpus.close()
        reopened = Corpus(database)
        assert InvestigationStore(reopened).query_plan(rid)["query"] == "python memory"
        reopened.close()
    asyncio.run(scenario())


def test_model_query_plan_uses_context_and_filters_invalid_variants():
    from periscope.investigations.planning import QueryPlanner

    class Client:
        async def complete(self, **kwargs):
            payload = json.loads(kwargs["user"])
            assert payload["main_question"] == "计算机考研择校"
            assert payload["previous_messages"] == ["对比学校"]
            assert payload["message"] == "看看2025年录取情况"
            return json.dumps({"query": "2025 计算机考研 录取", "variants": [
                "2025 计算机考研 招生", "2025 计算机考研 招生", "x" * 300]}, ensure_ascii=False)

    async def scenario():
        result = await QueryPlanner(Client()).plan("看看2025年录取情况", {
            "main_question": "计算机考研择校", "previous_messages": ["对比学校"]})
        assert result.query == "2025 计算机考研 录取"
        assert result.variants == ("2025 计算机考研 招生",)
    asyncio.run(scenario())


def test_model_failure_keeps_a_meaningful_query_and_does_not_invent_a_year():
    from periscope.investigations.planning import QueryPlanner

    class Client:
        async def complete(self, **kwargs):
            return '{"query":"2030 unrelated","variants":["2030 unrelated"]}'

    async def scenario():
        result = await QueryPlanner(Client()).plan("2025 考研 录取", {"main_question": "考研", "previous_messages": []})
        assert "2025" in result.query and "2030" not in result.query
    asyncio.run(scenario())


def test_unconfigured_model_extracts_searchable_chinese_terms_without_losing_year():
    from periscope.investigations.planning import QueryPlanner

    async def scenario():
        result = await QueryPlanner().plan("请帮我分析2025年计算机考研哪些学校的录取情况", {
            "main_question": "计算机考研", "previous_messages": []})
        assert "2025" in result.query and "计算机考研" in result.query
        assert "帮我" not in result.query and "哪些" not in result.query
        assert result.variants and all("2025" in variant for variant in result.variants)
    asyncio.run(scenario())


def test_referential_followup_retains_the_main_subject_without_a_model():
    from periscope.investigations.planning import QueryPlanner

    async def scenario():
        result = await QueryPlanner().plan("看看大家的评论", {
            "main_question": "计算机考研", "previous_messages": ["录取讨论"]})
        assert "计算机考研" in result.query and "评论" in result.query
    asyncio.run(scenario())


def test_model_cannot_invent_a_year_when_the_user_did_not_give_one():
    from periscope.investigations.planning import QueryPlanner

    class Client:
        async def complete(self, **kwargs):
            return '{"query":"2030 admissions","variants":[]}'

    async def scenario():
        result = await QueryPlanner(Client()).plan("admissions", {"main_question": "university admissions"})
        assert "2030" not in result.query
    asyncio.run(scenario())


def test_chinese_relevance_uses_entities_and_preserves_explicit_year():
    from periscope.collection.crawling.engine import _matches

    query = "2025 计算机考研 录取"
    assert _matches("2025年计算机专业硕士拟录取名单", query)
    assert not _matches("2024年计算机专业硕士拟录取名单", query)
    assert not _matches("2025年学校体育活动", query)


def test_variants_fan_out_without_repeating_deep_collection(monkeypatch):
    from periscope.investigations import browser_sources, catalog, crawl, web_search

    async def scenario():
        observed = []

        async def legacy(topic, **kwargs):
            observed.append(("legacy", topic, kwargs["depth"]))
            if False:
                yield []

        async def extra(topic, source_key, **kwargs):
            observed.append((source_key, topic, kwargs["depth"]))
            if False:
                yield []

        async def public(topic, **kwargs):
            observed.append(("public_web", topic, kwargs["depth"]))
            if False:
                yield []

        async def api(topic, **kwargs):
            observed.append(("search_api", topic, kwargs["depth"]))
            if False:
                yield []

        async def browser(topic, source, **kwargs):
            observed.append((source, topic, kwargs["depth"]))
            if False:
                yield []

        monkeypatch.setattr(crawl, "stream_v2ex_search", legacy)
        monkeypatch.setattr(crawl, "stream_hn_search", legacy)
        monkeypatch.setattr(catalog, "stream_catalog_source", extra)
        monkeypatch.setattr(web_search, "stream_public_search", public)
        monkeypatch.setattr(web_search, "stream_search_api", api)
        monkeypatch.setattr(browser_sources, "stream_browser_source", browser)
        async for _ in crawl.stream_auto_search("main", budget=30, variants=("variant one", "variant two")):
            pass
        assert sum(1 for _, query, depth in observed if query == "main" and depth == 2) == 17
        assert {query for _, query, _ in observed} == {"main", "variant one", "variant two"}
        assert all(depth == 0 for _, query, depth in observed if query != "main")
        assert len(observed) == 21
    asyncio.run(scenario())


def test_continue_deepens_unread_candidates_from_the_same_investigation(scratch_path):
    from periscope.evidence.versions import EvidenceVersions
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "continue.db")
        rows = [{"source": "github_issues", "kind": "community", "_origin": "platform",
                 "title": "python issue " + str(number), "text": "python search excerpt",
                 "url": "https://github.com/example/repo/issues/" + str(number),
                 "evidence": {"source": "github", "type": "issue", "native_id": "example/repo#" + str(number),
                              "content_level": "search_result"}}
                for number in (1, 2)]
        seen = []
        first = True

        async def collector(query):
            nonlocal first
            if first:
                first = False
                yield rows
                yield [{**rows[0], "text": "already read", "evidence": {**rows[0]["evidence"], "content_level": "full"},
                        "discussion_coverage": {"status": "complete"}}]

        async def resume(query, candidates, budget):
            seen.extend(candidates)
            assert [candidate["snapshot"]["native_id"] for candidate in candidates] == ["example/repo#2"]
            yield [{**rows[1], "text": "newly read full text",
                    "evidence": {**rows[1]["evidence"], "content_level": "full"},
                    "discussion_coverage": {"status": "complete"}}]

        runner = InvestigationRunner(corpus, collector)
        inv = runner.store.create("python")
        await runner.wait(runner.submit(inv, "python", "first", options={"seeds": [], "max_pages": None}))
        rid = runner.submit(inv, "继续", "second", options={"seeds": [], "max_pages": None}, resume_collector=resume)
        await runner.wait(rid)
        snapshots = [EvidenceVersions(corpus).get_version(**ref) for ref in runner.store.references(rid)]
        assert seen and any(item["text"] == "newly read full text" for item in snapshots)
        corpus.close()
    asyncio.run(scenario())


def test_specific_followup_can_deepen_relevant_unread_candidates(scratch_path):
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "specific-followup.db")
        resumed = []

        async def collector(query):
            if query == "python":
                yield [{"source": "github_issues", "kind": "community", "title": "python issue",
                        "url": "https://github.com/example/repo/issues/1", "text": "python issue summary",
                        "evidence": {"source": "github", "type": "issue", "native_id": "example/repo#1", "content_level": "search_result"}}]

        async def resume(query, candidates, budget):
            resumed.extend(candidates)
            yield [{"source": "github_issues", "kind": "community", "title": "python issue",
                    "url": "https://github.com/example/repo/issues/1", "text": "python detailed reply",
                    "evidence": {"source": "github", "type": "issue", "native_id": "example/repo#1", "content_level": "full"},
                    "discussion_coverage": {"status": "complete"}}]

        runner = InvestigationRunner(corpus, collector)
        inv = runner.store.create("python")
        await runner.wait(runner.submit(inv, "python", "one"))
        second = runner.submit(inv, "python workaround", "two", resume_collector=resume)
        await runner.wait(second)
        assert resumed and corpus.search("python detailed reply")
        corpus.close()
    asyncio.run(scenario())


def test_pending_candidates_respect_the_current_site_restriction(scratch_path):
    from periscope.investigations.pending import pending_candidates
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "pending-scope.db")

        async def collector(query):
            yield [{"source": "github_issues", "kind": "community", "title": "python",
                    "url": "https://github.com/example/repo/issues/1", "text": "python",
                    "evidence": {"source": "github", "type": "issue", "native_id": "example/repo#1", "content_level": "search_result"}},
                   {"source": "csdn_search", "kind": "community", "title": "python",
                    "url": "https://blog.csdn.net/user/article/details/2", "text": "python",
                    "evidence": {"source": "csdn", "type": "article", "native_id": "2", "content_level": "search_result"}}]

        runner = InvestigationRunner(corpus, collector)
        inv = runner.store.create("python")
        await runner.wait(runner.submit(inv, "python", "first"))
        rid = runner.store.start_round(inv, "继续", "second", options={"seeds": ["https://github.com/"]})
        pending = pending_candidates(runner.store, rid, "python")
        assert [item["source"] for item in pending] == ["github"]
        corpus.close()
    asyncio.run(scenario())


def test_incomplete_resume_does_not_prevent_new_search(scratch_path):
    from periscope.investigations.crawl import CollectionIncomplete
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "pending-partial.db")
        count = 0

        async def collect(query):
            nonlocal count
            count += 1
            if count == 1:
                yield [{"source": "github_issues", "kind": "community", "title": "python",
                        "url": "https://github.com/example/repo/issues/1", "text": "python",
                        "evidence": {"source": "github", "type": "issue", "native_id": "example/repo#1", "content_level": "search_result"}}]
            else:
                yield [{"source": "web_crawl", "kind": "media", "title": "python",
                        "url": "https://example.org/python", "text": "new search evidence"}]

        async def resume(query, candidates, budget):
            if False:
                yield []
            raise CollectionIncomplete("sources:github:temporarily_unavailable")

        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("python")
        await runner.wait(runner.submit(inv, "python", "first"))
        rid = runner.submit(inv, "继续", "second", resume_collector=resume)
        await runner.wait(rid)
        assert count == 2 and runner.store.get_round(rid)["status"] == "partial"
        assert corpus.search("new search evidence")
        corpus.close()
    asyncio.run(scenario())


def test_slow_resume_saves_partial_results_and_leaves_time_for_new_search(monkeypatch, scratch_path):
    from periscope.investigations import runner as runner_module
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "resume-budget.db")
        stopped = asyncio.Event()
        searched = []

        async def collect(query):
            searched.append(query)
            yield [{"source": "web_crawl", "kind": "media", "title": "python",
                    "url": "https://example.org/new", "text": "new evidence"}]

        async def resume(query, candidates, budget):
            try:
                yield [{"source": "web_crawl", "kind": "media", "title": "python",
                        "url": "https://example.org/old", "text": "saved during resume"}]
                await asyncio.Future()
            finally:
                stopped.set()

        monkeypatch.setattr(runner_module, "_resume_budget", lambda remaining: 0.05, raising=False)
        monkeypatch.setattr(runner_module, "pending_candidates", lambda *args: [{"snapshot": {}}])
        runner = InvestigationRunner(corpus, collect, budget=30)
        inv = runner.store.create("python")
        rid = runner.submit(inv, "继续", "one", resume_collector=resume)
        await asyncio.wait_for(runner.wait(rid), 2)
        assert stopped.is_set() and searched
        assert corpus.search("saved during resume") and corpus.search("new evidence")
        assert runner.store.get_round(rid)["status"] == "partial"
        corpus.close()

    asyncio.run(scenario())


def test_explicit_limits_and_seeds_do_not_resume_other_saved_candidates(monkeypatch, scratch_path):
    from periscope.investigations import runner as runner_module
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "limited-resume.db")
        resumed = []

        async def collect(query):
            if False:
                yield []

        async def resume(query, candidates, budget):
            resumed.append(True)
            if False:
                yield []

        monkeypatch.setattr(runner_module, "pending_candidates", lambda *args: [{"snapshot": {}}])
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("python")
        for index, options in enumerate(({"depth": 0, "max_pages": None, "seeds": []},
                                         {"depth": 2, "max_pages": 1, "seeds": []},
                                         {"depth": 2, "max_pages": None, "seeds": ["https://github.com/a/b/issues/1"]})):
            await runner.wait(runner.submit(inv, "python comments", str(index), options=options, resume_collector=resume))
        assert not resumed
        corpus.close()
    asyncio.run(scenario())


def test_pending_hn_followup_uses_current_depth_limit(monkeypatch):
    from periscope.investigations import crawl, pending

    async def scenario():
        observed = []

        async def collect(query, options, **kwargs):
            observed.append(options.depth)
            if False:
                yield []

        monkeypatch.setattr(crawl, "stream_crawl", collect)
        candidates = [{"source": "hackernews", "snapshot": {"url": "https://news.ycombinator.com/item?id=123"},
                       "max_comments": 20, "max_depth": 1}]
        async for _ in pending.stream_pending_candidates("python", candidates, 30):
            pass
        assert observed == [1]
    asyncio.run(scenario())


def test_failed_direct_link_does_not_hide_previous_relevant_lead(monkeypatch, scratch_path):
    from periscope.collection.crawling.engine import CrawlOptions
    from periscope.collection.platforms import github
    from periscope.investigations.crawl import stream_crawl
    from periscope.investigations.pending import pending_candidates
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "direct-empty-card.db")
        url = "https://github.com/example/repo/issues/12"

        async def search(query):
            yield [{"source": "github_issues", "kind": "community", "title": "python issue", "url": url,
                    "text": "python summary", "evidence": {"source": "github", "type": "issue",
                    "native_id": "example/repo#12", "content_level": "search_result"}}]

        async def detail(*args, **kwargs):
            return {}, {"status": "failed"}

        async def comments(*args, **kwargs):
            return [], {"status": "failed"}

        monkeypatch.setattr(github, "fetch_issue_detail", detail)
        monkeypatch.setattr(github, "fetch_discussion", comments)
        runner = InvestigationRunner(corpus, search)
        inv = runner.store.create("python")
        await runner.wait(runner.submit(inv, "python", "one"))
        options = CrawlOptions((url,), max_pages=2, depth=1, budget=30)
        await runner.wait(runner.submit(inv, "深入这条", "two", collector=lambda query: stream_crawl(query, options),
                                        options={"seeds": [url], "max_pages": None, "depth": 1}))
        third = runner.store.start_round(inv, "继续", "three", options={"seeds": [], "max_pages": None})
        pending = pending_candidates(runner.store, third, "python")
        assert pending and pending[0]["snapshot"]["text"] == "python summary"
        corpus.close()
    asyncio.run(scenario())


def test_weibo_followup_uses_previously_observed_numeric_id(monkeypatch, scratch_path):
    from periscope.collection.platforms import weibo
    from periscope.investigations.catalog import _target
    from periscope.investigations.pending import pending_candidates, stream_pending_candidates
    from periscope.investigations.runner import InvestigationRunner

    async def scenario():
        corpus = Corpus(scratch_path / "weibo-followup.db")
        url = "https://weibo.com/detail/AbC123"
        observed = []

        async def collect(query):
            yield [{"source": "weibo_search", "kind": "community", "title": "python post", "url": url,
                    "text": "python summary", "evidence": {**_target("weibo_search", url, ids={"id": "987654321"})[0],
                                                               "content_level": "search_result"}}]

        async def detail(url, *, post_id, **kwargs):
            observed.append(post_id)
            return {"text": "post"}, [], {"status": "complete"}

        monkeypatch.setattr(weibo, "fetch_discussion", detail)
        runner = InvestigationRunner(corpus, collect)
        inv = runner.store.create("python")
        await runner.wait(runner.submit(inv, "python", "one"))
        rid = runner.store.start_round(inv, "继续", "two", options={"seeds": [], "max_pages": None})
        candidates = pending_candidates(runner.store, rid, "python")
        async for _ in stream_pending_candidates("python", candidates, 30):
            pass
        assert observed == ["987654321"]
        corpus.close()
    asyncio.run(scenario())
