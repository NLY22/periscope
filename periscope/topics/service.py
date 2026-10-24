"""Topic search orchestration, independent of CLI output and persistence."""

from __future__ import annotations

from collections.abc import Callable
import time

from . import layers
from .enrichment import _enrich_topk, _row_key
from .models import API_DEPTH, SPEED_FAST, TopicRequest, TopicResult
from .coverage import compare_sources
from periscope.search import api, merge


class TopicService:
    """Gather and rank evidence; callers decide how to display or save it."""

    def __init__(self, *, local_lookup: Callable | None = None,
                 api_lookup: Callable | None = None,
                 platform_lookup: Callable | None = None,
                 api_available: Callable | None = None,
                 enrich_lookup: Callable | None = None,
                 crawl_lookup: Callable | None = None):
        self.local_lookup = local_lookup or layers.local_lookup
        self.api_lookup = api_lookup or layers.api_lookup
        self.platform_lookup = platform_lookup or layers.platform_lookup
        self.api_available = api_available or api.available
        self.enrich_lookup = enrich_lookup or _enrich_topk
        self.crawl_lookup = crawl_lookup or layers.crawl_lookup

    def search(self, request: TopicRequest) -> TopicResult:
        started = time.monotonic()
        topic = request.topic.strip()
        result = TopicResult(topic=topic)
        breakdown = []
        if not request.no_local:
            result.local_rows, result.local_total = self.local_lookup(topic, request.limit * 3)
        breakdown.append("本地 %d 条" % result.local_total)

        if request.cached_only:
            result.merged = merge.merge([], result.local_rows)
            if request.save:
                result.notes.append("--save 在此路径下无事可做：本地行本来就在库里"
                                    "（用 --ingest-last 或去掉 --cached-only 才需要入库）")
        else:
            api_succeeded, platform_elapsed = self._gather(request, result, breakdown)
            crawl_seeds = request.crawl_seeds
            if request.crawl and not crawl_seeds:
                from periscope.collection.crawling.engine import select_seeds
                crawl_seeds = select_seeds(result.platform_rows + result.api_rows, limit=min(6, request.crawl_pages))
                if not crawl_seeds:
                    result.notes.append("本轮没有可跟进的链接，可使用 --crawl-seeds 指定站点入口")
            if crawl_seeds:
                result.crawl_rows, crawl_stat = self.crawl_lookup(topic, crawl_seeds, max_pages=request.crawl_pages,
                                                                 depth=request.crawl_depth, budget=request.crawl_budget)
                result.crawl_reports = crawl_stat.get("reports", [])
                breakdown.append("网页采集 %d 条/%.1fs" % (len(result.crawl_rows), crawl_stat["elapsed"]))
                crawl_states = {report["status"] for report in result.crawl_reports}
                has_failure = bool(crawl_states & {"failed", "blocked", "too_large", "unsupported", "needs_ocr"})
                incomplete = has_failure or bool(crawl_states & {"over_budget", "page_limit"})
                crawl_status = ("partial" if incomplete else "ok") if result.crawl_rows else "over_budget" if "over_budget" in crawl_states else "failed" if has_failure else "empty"
                result.source_reports.append({"key": "web_crawl", "mode": "crawl", "name": "站点爬虫",
                                              "status": crawl_status, "rows": len(result.crawl_rows)})
                if incomplete:
                    result.notes.append("网页采集部分页面未完成或无法读取，具体原因见 crawl_reports")
            result.coverage = compare_sources(result.api_rows, result.platform_rows + result.crawl_rows, api_succeeded=api_succeeded)
            result.merged = merge.merge(result.api_rows, result.local_rows + result.platform_rows + result.crawl_rows)
            if request.speed == "off" and not crawl_seeds:
                result.notes.append("平台采集已关闭（--speed off），只有本地与 API 两层")
            if request.enrich:
                remaining_budget = None if request.budget == 0 else max(0.0, request.budget - platform_elapsed)
                self._enrich(request, result, breakdown, budget_seconds=remaining_budget)

        result.elapsed = time.monotonic() - started
        result.breakdown = " · ".join(breakdown)
        return result

    def _gather(self, request: TopicRequest, result: TopicResult, breakdown: list[str]) -> tuple[bool, float]:
        api_succeeded = False
        if not request.no_api:
            if not self.api_available():
                result.notes.append("没有 DEEPSEEK_API_KEY，跳过 API 层")
            else:
                depth = request.api_depth or API_DEPTH.get(request.speed, 1)
                result.api_rows, elapsed, error = self.api_lookup(result.topic, request.api_count, depth)
                if error:
                    result.notes.append("API 失败: %s" % error[:80])
                else:
                    api_succeeded = True
                    breakdown.append("API %d 条/%d 问法/%.1fs" % (len(result.api_rows), max(depth, 1), elapsed))
        if request.speed == "off":
            return api_succeeded, 0.0
        options = {}
        if request.coverage != "focused" or request.sources or request.source_kinds or request.sources_file:
            options = {"coverage": request.coverage, "sources": request.sources,
                       "kinds": request.source_kinds, "sources_file": request.sources_file}
        result.platform_rows, stat = self.platform_lookup(result.topic, request.speed, request.budget, **options)
        platform_elapsed = float(stat.get("elapsed") or 0.0)
        result.source_reports = stat.get("source_reports", [])
        breakdown.append("平台 %d 条/%.1fs" % (len(result.platform_rows), stat["elapsed"]))
        if stat["failed"]:
            result.notes.append("平台失败: %s" % ", ".join(stat["failed"][:4]))
        if stat.get("over_budget"):
            result.notes.append("超出 %.0fs 预算未返回: %s（--budget 调大或换 --speed normal）"
                                % (request.budget, ", ".join(stat["over_budget"][:4])))
        if request.coverage == "focused" and request.speed == SPEED_FAST:
            result.notes.append("平台用快速模式（12 源 1 页）。"
                                "--normal 更全(26s)，--deep 含小红书/抖音(60s+)")
        return api_succeeded, platform_elapsed

    def _enrich(self, request: TopicRequest, result: TopicResult, breakdown: list[str], *,
                budget_seconds: float | None) -> None:
        if self.enrich_lookup is _enrich_topk:
            result.merged, stat, patches = self.enrich_lookup(
                result.merged, k=max(1, request.limit),
                budget_seconds=budget_seconds)
        else:
            # Preserve the original injected callback contract for embedders.
            result.merged, stat, patches = self.enrich_lookup(
                result.merged, k=max(1, request.limit))
        if stat["targets"]:
            breakdown.append("富化 %d/%d 条/%.1fs" % (stat["done"], stat["targets"], stat["elapsed"]))
        else:
            result.notes.append("富化：本次结果里没有可补采的平台详情或讨论")
        if stat.get("discussion_targets"):
            breakdown.append("HN 讨论树 %d/%d 条" % (
                stat.get("discussion_done", 0), stat["discussion_targets"]))
        if stat.get("discussion_partial"):
            result.notes.append("%d 条 HN 讨论树达到深度、条数或时间上限；覆盖详情随分片保存"
                                % stat["discussion_partial"])
        if stat.get("github_discussion_targets"):
            breakdown.append("GitHub 讨论 %d/%d 条" % (
                stat.get("github_discussion_done", 0), stat["github_discussion_targets"]))
        if stat.get("github_discussion_partial"):
            result.notes.append("%d 条 GitHub 讨论达到条数、分页或时间上限；覆盖详情随分片保存"
                                % stat["github_discussion_partial"])
        if stat.get("v2ex_discussion_targets"):
            breakdown.append("V2EX 回复 %d/%d 条" % (
                stat.get("v2ex_discussion_done", 0), stat["v2ex_discussion_targets"]))
        if stat.get("v2ex_discussion_partial"):
            result.notes.append("%d 条 V2EX 回复达到条数或时间上限；平台不提供回复间的父子关系"
                                % stat["v2ex_discussion_partial"])
        if stat["failed"]:
            result.notes.append("富化失败: %s" % ", ".join(stat["failed"][:3]))
        if stat["no_transcript"]:
            result.notes.append("%d 条无可用字幕（B站未登录时不返回轨道）" % stat["no_transcript"])
        for row in result.raw_rows:
            patch = patches.get(_row_key(row))
            if patch:
                row.update(patch)
