"""Command-line adapter for topic search."""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import math
import sys

from periscope.runtime import paths
from periscope.search import api as dsapi, merge, local as corpus_search
from periscope.topics import runs as runstore
from periscope.topics.enrichment import _enrich_topk, _row_key
from periscope.topics.layers import local_lookup, api_lookup, platform_lookup, _platform_trust, _fmt_date
from periscope.topics.models import (
    TopicRequest, SPEED_FAST, SPEED_NORMAL, SPEED_DEEP, API_DEPTH, FAST_SOURCES, BROWSER_SCRIPTS,
)
from periscope.topics.persistence import _display, _ingest, _ingest_last, _materialise, ingest_run
from periscope.topics.render import emit, render, json_payload
from periscope.topics.service import TopicService
from periscope.collection.registry import SourceRegistry


ROOT = paths.ROOT


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="任意话题检索",
        epilog="默认广泛覆盖：本地 + API + 原生搜索与相关订阅源。--coverage focused 恢复原有平台范围。")
    ap.add_argument("--topic", default="",
                    help="话题；--ingest-last 时不需要")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--cached-only", action="store_true",
                    help="只查本地语料，不联网")
    ap.add_argument("--no-api", action="store_true",
                    help="不调 DeepSeek Search API")
    ap.add_argument("--no-local", action="store_true", help="不查询历史语料")
    ap.add_argument("--coverage", choices=("broad", "focused"), default="broad",
                    help="直采范围：broad 接入原生搜索和相关订阅源；focused 保留原有平台范围")
    ap.add_argument("--sources", nargs="+", default=(), metavar="KEY", help="只选择指定直采源")
    ap.add_argument("--source-kinds", nargs="+", default=(), metavar="KIND", help="筛选直采源类别")
    ap.add_argument("--sources-file", default="", help="加载自定义信息源 JSON 配置")
    ap.add_argument("--crawl", action="store_true", help="自动跟进本轮检索发现的页面和 PDF 附件")
    ap.add_argument("--crawl-seeds", nargs="+", default=(), metavar="URL", help="从指定站点入口跟进页面、站点地图和 PDF 附件")
    ap.add_argument("--crawl-pages", type=int, default=12, help="爬虫最多尝试的页面数（包含未命中与失败页面）")
    ap.add_argument("--crawl-depth", type=int, default=2, help="爬虫最大跟进层数，0 只读入口")
    ap.add_argument("--crawl-budget", type=float, default=30.0, help="爬虫独立时间预算（秒），0 表示没有总时限")
    ap.add_argument("--list-sources", action="store_true", help="列出已配置来源与检索方式，不联网")
    ap.add_argument("--speed", choices=["fast", "normal", "deep", "off"],
                    default="fast",
                    help="直采深度：fast 一页 / normal 两页 / deep 三页及浏览器适配 / off 不采")
    # 兼容旧参数
    ap.add_argument("--platform", action="store_true", help="等于 --speed normal")
    ap.add_argument("--deep", action="store_true", help="等于 --speed deep")
    ap.add_argument("--which", nargs="*", default=None, help="（已废弃）")
    ap.add_argument("--api-count", type=int, default=50,
                    help="单次查询取多少条（接口上限 50）")
    ap.add_argument("--api-depth", type=int, default=0,
                    help="用几种问法查 API（0 = 按 --speed 自动：fast 3 / "
                         "normal 5 / deep 7；1 = 旧的单次查询）")
    ap.add_argument("--budget", type=float, default=30.0,
                    help="直采总预算（秒）；平台详情与讨论补采使用剩余预算，0 表示不限")
    ap.add_argument("--json", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--save", action="store_true",
                    help="采集后立即入库（默认只落分片，不写库）")
    ap.add_argument("--enrich", action="store_true",
                    help="对前 --limit 条补平台详情（B站、HN、GitHub、V2EX、Stack Overflow）")
    ap.add_argument("--ingest-last", action="store_true",
                    help="把最近一次分片入库，不重新查询")
    ap.add_argument("--no-shard", action="store_true",
                    help="不落分片（调试用）")
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not math.isfinite(args.budget) or args.budget < 0:
        parser.error("--budget 必须是非负有限数值")
    if args.crawl_seeds or args.crawl:
        from periscope.collection.crawling.engine import CrawlOptions
        try:
            CrawlOptions(tuple(args.crawl_seeds), max_pages=args.crawl_pages, depth=args.crawl_depth, budget=args.crawl_budget)
        except ValueError as exc:
            parser.error(str(exc))
    try:
        registry = SourceRegistry.from_file(args.sources_file) if args.sources_file else SourceRegistry()
        registry.plan(args.coverage, sources=tuple(args.sources), kinds=tuple(args.source_kinds))
    except ValueError as exc:
        parser.error(str(exc))
    if args.list_sources:
        entries = [{**entry.describe(), "enabled": entry.key not in registry.disabled} for entry in registry.entries()
                   if (not args.sources or entry.key in args.sources)
                   and (not args.source_kinds or entry.kind in args.source_kinds)]
        payload = {"status": "configured_not_health_checked", "sources": entries}
        if args.json:
            Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        emit("已配置 %d 个来源（登记不代表在线可用）：\n%s" % (len(entries), "\n".join(
            "%-24s %-10s %-12s %s%s" % (entry["key"], entry["mode"], entry["kind"], entry["name"],
                                         " · " + entry["limitation"] if entry["limitation"] else "") for entry in entries)), args.out)
        return 0
    if args.ingest_last:
        return _ingest_last()
    if not args.topic.strip():
        parser.error("--topic 是必需的（除非使用 --ingest-last）")
    if args.deep:
        args.speed = SPEED_DEEP
    elif args.platform:
        args.speed = SPEED_NORMAL
    request = TopicRequest(**{name: getattr(args, name) for name in TopicRequest.__dataclass_fields__})
    service = TopicService(local_lookup=local_lookup, api_lookup=api_lookup,
                           platform_lookup=platform_lookup, api_available=dsapi.available,
                           enrich_lookup=_enrich_topk)
    result = service.search(request)
    info = result.info
    text = render(result.topic, result.merged[:args.limit], info)
    shard_path = None
    if not args.no_shard:
        try:
            shard_path = runstore.write(result.topic, result.raw_rows,
                                        argv=list(sys.argv[1:] if argv is None else argv),
                                        elapsed=result.elapsed, stats=result.stats, notes=result.notes)
        except Exception as exc:
            result.notes.append("分片落盘失败: %s" % type(exc).__name__)
    if shard_path is not None:
        text += "\n  分片: %s" % _display(shard_path)
        if not args.cached_only:
            text += "\n  入库: python topic.py --ingest-last"
    # Preserve the current cached-only output contract.
    if args.json:
        Path(args.json).write_text(json.dumps(
            json_payload(result.topic, result.merged, info, args.limit),
            ensure_ascii=False, indent=2), encoding="utf-8")
    emit(text, args.out)
    if args.save and not args.cached_only and shard_path is not None:
        code = ingest_run(shard_path)
        emit("  入库完成（exit=%d）" % code)
        return 0 if code == 0 else code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
