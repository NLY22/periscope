"""Terminal and JSON representations of topic results."""

from __future__ import annotations

from pathlib import Path
import os

from periscope.search import merge


ORIGIN_LABEL = {"local": "本地", "api": "API", "platform": "平台", "crawl": "网页采集"}

def emit(text: str, out_path: str = "") -> None:
    """Write UTF-8 output and an optional file, preserving best-effort behavior."""
    if out_path:
        try:
            Path(out_path).write_text(text, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    try:
        os.write(1, (text + "\n").encode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        pass


def render(topic: str, rows: list[dict], info: dict) -> str:
    L = ["=" * 94, "话题：%s" % topic, "=" * 94]

    st = merge.stats(rows)
    L.append("  共 %d 条（%d 条带正文，%d 条多家独立报道）"
             % (st["total"], st["with_body"], st["multi_source"]))
    L.append("  耗时 %.1fs   %s"
             % (info.get("elapsed", 0), info.get("breakdown", "")))
    coverage = info.get("coverage") or {}
    if coverage.get("comparison_available"):
        L.append("  本轮 API 未返回的直采链接：%d / %d（仅限本轮查询对照）"
                 % (coverage["direct_urls_not_in_api"], coverage["direct_linked_urls"]))
    reports = info.get("source_reports") or []
    if reports:
        active = [report for report in reports if report["status"] != "skipped"]
        L.append("  来源：%d 个纳入计划，%d 个返回资料，%d 个未赶上预算"
                 % (len(active), sum(report.get("rows", 0) > 0 for report in active),
                    sum(report["status"] == "over_budget" for report in active)))
    if info.get("notes"):
        for n in info["notes"]:
            L.append("  注意: %s" % n)
    L.append("")

    if not rows:
        L.append("  （没有找到相关内容。）")
        return "\n".join(L)

    for i, r in enumerate(rows, 1):
        title = (r.get("title") or "(无标题)")[:78]
        L.append("-" * 94)
        L.append("[%d] %s" % (i, title))

        ind = r.get("_independent") or 1
        src = " · ".join((r.get("_publishers") or [r.get("author") or "?"])[:3])
        L.append("    来源: %-40s 日期: %s" % (src[:40], r.get("date") or "?"))
        body = r.get("body") or ""
        flag = "[多家独立报道]" if ind > 1 else ""
        L.append("    正文: %-5d 字  %s" % (len(body), flag))
        document = r.get("document") or {}
        if document.get("format") == "pdf":
            L.append("    文档: PDF，已读取 %d/%d 页%s%s" % (
                document.get("pages_read", 0), document.get("pages_total", 0),
                "，文本已截断" if document.get("text_truncated") else "",
                "，部分页面需要 OCR" if document.get("needs_ocr") else ""))
        # Enrichment, when present. Counters are shown with the platform's own
        # meaning, and a transcript is labelled as such - it is the视频's speech,
        # not the article body, and conflating them is how a summary starts
        # quoting a video it never watched.
        metrics = r.get("metrics") or {}
        if metrics:
            L.append("    互动: %s" % " · ".join(
                "%s=%s" % (k, v) for k, v in list(metrics.items())[:6]))
        transcript = r.get("transcript") or {}
        ttext = (transcript.get("text") or "").strip()
        if ttext:
            L.append("    字幕: %d 字（%s）%s"
                     % (len(ttext), transcript.get("method") or "?",
                        " ".join(ttext.split())[:120] + "..."))
        comments = r.get("comments") or []
        if comments:
            L.append("    评论: %d 条，首条 %s"
                     % (len(comments), (comments[0].get("text") or "")[:60]))
        if r.get("url"):
            L.append("    链接: %s" % (r["url"] or "")[:88])
        if r.get("body_source_url") and r["body_source_url"] != r.get("url"):
            L.append("    正文出处: %s" % r["body_source_url"])
        if body:
            L.append("    节选: %s..." % " ".join(body.split())[:300])
        L.append("")
    return "\n".join(L)


def json_payload(topic: str, rows: list[dict], info: dict, limit: int) -> dict:
    """Preserve the existing --json interchange format."""
    return {
        "topic": topic,
        "elapsed": round(info["elapsed"], 2),
        "breakdown": info["breakdown"],
        "notes": info["notes"],
        "stats": merge.stats(rows),
        "source_reports": info.get("source_reports", []),
        "coverage": info.get("coverage", {}),
        "crawl_reports": info.get("crawl_reports", []),
        "results": [
            {"title": r.get("title"), "url": r.get("url"),
             "source": r.get("source"), "kind": r.get("kind"),
             "metrics": r.get("metrics", {}), "ids": r.get("ids", {}),
             "document": r.get("document", {}),
             "publishers": r.get("_publishers"), "independent": r.get("_independent"),
             "date": r.get("date"), "score": round(r.get("_score") or 0, 3),
             "body_chars": len(r.get("body") or ""),
             "body_source_url": r.get("body_source_url") or r.get("url"),
             "sources": r.get("sources", []), "provenance": r.get("provenance", {}),
             "snippet": (r.get("body") or r.get("text") or "")[:400]}
            for r in rows[:limit]
        ],
    }
