"""抖音搜索采集器（浏览器拦截响应包）。

页面负责正常的搜索请求与平台校验，本适配读取页面收到的结果。
默认使用独立的无头浏览器和持久化会话；按结果到达继续处理，不固定等待。

用法：
    python collect_douyin.py --queries 人形机器人 固态电池 --scrolls 6
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import argparse
import asyncio
import json
import re
import sys
import urllib.parse
from urllib.parse import urlsplit

from periscope.collection.platforms.browser import browser_session, ensure_env, run_collector, wait_for_items, write_checkpoint
from periscope.runtime import paths


ROOT = paths.ROOT

PROFILES = paths.PROFILES
OUT_DEFAULT = paths.COLLECT_DIR / "ugc_search.douyin.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

MAX_DISCUSSION_COMMENTS = 60
MAX_COMMENT_PAGES = 4
MAX_REPLY_THREADS = 5
MAX_REPLY_PAGES = 3
COMMENT_LIST_HINT = "/aweme/v1/web/comment/list/"
COMMENT_REPLY_HINT = "/aweme/v1/web/comment/list/reply/"
MORE_REPLIES_PATTERN = re.compile(r"(?:展开\s*)?更多回复|查看更多回复")
VIDEO_ID_PATH = re.compile(r"^/video/(\d+)/?$")


def _aweme_id(video_url: str) -> str:
    """Return a Douyin video ID only for a canonical video URL."""
    try:
        parsed = urlsplit(str(video_url or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"https", "http"} or (parsed.hostname or "").lower() not in {
            "douyin.com", "www.douyin.com"}:
        return ""
    match = VIDEO_ID_PATH.fullmatch(parsed.path)
    return match.group(1) if match else ""


def _safe_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _comment_record(comment: dict, *, aweme_id: str, parent_id: str = "") -> dict:
    user = comment.get("user") or {}
    record = {
        "id": str(comment.get("cid") or comment.get("comment_id") or ""),
        "aweme_id": str(comment.get("aweme_id") or aweme_id),
        "text": str(comment.get("text") or "").strip(),
        "author": str(user.get("nickname") or ""),
        "likes": _safe_int(comment.get("digg_count")),
        "created_at": str(comment.get("create_time") or ""),
        "url": "https://www.douyin.com/video/%s" % aweme_id,
    }
    if parent_id:
        record["parent_id"] = parent_id
    return record


def _parse_comment_pages(payloads: list[dict], *, aweme_id: str,
                         max_comments: int = MAX_DISCUSSION_COMMENTS,
                         reply_payloads: list[dict] | None = None):
    """Normalize root and expanded reply pages with explicit coverage."""
    roots = []
    roots_by_id: dict[str, dict] = {}
    seen: set[str] = set()
    reported_replies_by_id: dict[str, int] = {}
    reply_has_more: set[str] = set()
    reply_page_counts: dict[str, int] = {}
    accepted_reply_threads: set[str] = set()
    truncated_by: list[str] = []
    errors: list[str] = []
    reported_comments = 0
    has_more = False
    pages_attempted = 0

    def add_reply(parent: dict, item: dict) -> None:
        reply_id = str(item.get("cid") or item.get("comment_id") or "")
        if not reply_id or reply_id in seen:
            return
        seen.add(reply_id)
        parent["replies"].append(_comment_record(item, aweme_id=aweme_id,
                                                  parent_id=parent["id"]))

    for payload in payloads:
        if not isinstance(payload, dict) or not isinstance(payload.get("comments"), list):
            if "comments:invalid_payload" not in errors:
                errors.append("comments:invalid_payload")
            continue
        pages_attempted += 1
        has_more = bool(payload.get("has_more"))
        reported_comments = max(reported_comments, _safe_int(payload.get("total")))
        for item in payload["comments"]:
            if not isinstance(item, dict):
                continue
            comment_id = str(item.get("cid") or item.get("comment_id") or "")
            if not comment_id:
                continue
            reported_replies_by_id[comment_id] = max(
                reported_replies_by_id.get(comment_id, 0),
                _safe_int(item.get("reply_comment_total")))
            if comment_id in seen:
                continue
            seen.add(comment_id)
            root = _comment_record(item, aweme_id=aweme_id)
            root["replies"] = []
            roots_by_id[comment_id] = root
            roots.append(root)
            inline_items = item.get("reply_comment")
            if isinstance(inline_items, list):
                for reply in inline_items:
                    if not isinstance(reply, dict):
                        continue
                    add_reply(root, reply)
            if len(seen) >= max_comments:
                truncated_by.append("comment_limit")
                break
        if len(seen) >= max_comments:
            break

    for entry in reply_payloads or []:
        payload = entry.get("payload") or {}
        items = payload.get("comments") if isinstance(payload, dict) else None
        parent_id = str(entry.get("comment_id") or "")
        if not parent_id and isinstance(items, list):
            parent_id = next((str(item.get("root_comment_id") or "")
                              for item in items if isinstance(item, dict)), "")
        if parent_id not in roots_by_id or not isinstance(items, list):
            if "replies:invalid_payload" not in errors:
                errors.append("replies:invalid_payload")
            continue
        if parent_id not in accepted_reply_threads:
            if len(accepted_reply_threads) >= MAX_REPLY_THREADS:
                truncated_by.append("reply_thread_limit")
                continue
            accepted_reply_threads.add(parent_id)
        if reply_page_counts.get(parent_id, 0) >= MAX_REPLY_PAGES:
            truncated_by.append("reply_page_limit")
            continue
        reply_page_counts[parent_id] = reply_page_counts.get(parent_id, 0) + 1
        if bool(payload.get("has_more")):
            reply_has_more.add(parent_id)
        else:
            reply_has_more.discard(parent_id)
        root = roots_by_id[parent_id]
        for item in items:
            if not isinstance(item, dict):
                continue
            reply_id = str(item.get("cid") or item.get("comment_id") or "")
            if not reply_id or reply_id in seen:
                continue
            if len(seen) >= max_comments:
                truncated_by.append("comment_limit")
                break
            seen.add(reply_id)
            reply_parent = str(item.get("reply_to_reply_id") or parent_id)
            root["replies"].append(_comment_record(
                item, aweme_id=aweme_id, parent_id=reply_parent))
        if len(seen) >= max_comments:
            break

    reported_replies = sum(reported_replies_by_id.values())
    fetched_replies = sum(len(root["replies"]) for root in roots)
    if reported_replies > fetched_replies:
        for root_id in reported_replies_by_id:
            fetched_for_root = len(roots_by_id[root_id]["replies"])
            if reported_replies_by_id[root_id] <= fetched_for_root:
                continue
            if reply_page_counts.get(root_id, 0):
                truncated_by.append("reply_thread_incomplete")
            else:
                truncated_by.append("reply_threads_not_fetched")
    if reply_has_more:
        truncated_by.append("reply_page_limit")
    if has_more:
        truncated_by.append("comment_page_limit")
    fetched_roots = len(roots)
    coverage = {
        "platform": "douyin", "aweme_id": aweme_id,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "status": "partial" if truncated_by or errors or not pages_attempted else "complete",
        "reported_comments": reported_comments,
        "reported_replies": reported_replies,
        "fetched_comments": fetched_roots + fetched_replies,
        "fetched_roots": fetched_roots, "fetched_replies": fetched_replies,
        "pages_attempted": pages_attempted,
        "reply_pages_attempted": sum(reply_page_counts.values()),
        "max_comments": max_comments,
        "truncated_by": list(dict.fromkeys(truncated_by)), "errors": errors,
    }
    return {}, roots, coverage

DEFAULT_QUERIES = [
    "人形机器人", "具身智能", "大模型", "AI 应用", "智能体",
    "算力", "芯片", "英伟达", "DeepSeek", "通义千问",
    "豆包", "Kimi", "文心一言", "智谱", "MiniMax",
    "AI 副业", "AI 失业", "本地部署", "提示词", "AI 翻车",
]

# 抖音搜索响应里，能确定是内容列表的 URL 特征
SEARCH_HINTS = (
    "/aweme/v1/web/general/search/single/",
    "/aweme/v1/web/search/item/",
    "/aweme/v1/web/general/search/stream/",
)


def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace("&amp;", "&").replace("&nbsp;", " ")
    return " ".join(s.split())


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", nargs="*", default=None)
    ap.add_argument("--limit-queries", type=int, default=0)
    ap.add_argument("--scrolls", type=int, default=6)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--headed", action="store_true", default=False,
                    help="显示浏览器窗口（默认不显示）")
    return ap.parse_args()


def _fetch_many_sync(items: list[dict], *, max_comments: int,
                     budget_seconds: float | None) -> dict:
    from time import monotonic

    deadline = None if budget_seconds is None else monotonic() + max(0.0, budget_seconds)
    results = {}
    targets = []
    for item in items:
        url = str(item.get("url") or "")
        aweme_id = str(item.get("aweme_id") or _aweme_id(url))
        if aweme_id.isdigit() and urlsplit(url).hostname in {"douyin.com", "www.douyin.com"}:
            targets.append((url, aweme_id))
        else:
            results[url] = ({}, [], {
                "platform": "douyin", "status": "partial", "aweme_id": "",
                "fetched_comments": 0, "fetched_roots": 0, "fetched_replies": 0,
                "pages_attempted": 0, "max_comments": max_comments,
                "truncated_by": [], "errors": ["invalid_video_url"],
            })
    if not targets:
        return results

    def remaining() -> float | None:
        return None if deadline is None else max(0.0, deadline - monotonic())

    try:
        with browser_session(PROFILES / "douyin", headed=False, user_agent=UA,
                             prepare=ensure_env,
                             viewport={"width": 1440, "height": 950}) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for video_url, aweme_id in targets:
                left = remaining()
                if left is not None and left <= 0:
                    results[video_url] = ({}, [], {
                        "platform": "douyin", "aweme_id": aweme_id, "status": "partial",
                        "fetched_comments": 0, "fetched_roots": 0, "fetched_replies": 0,
                        "pages_attempted": 0, "max_comments": max_comments,
                        "truncated_by": ["time_budget"], "errors": [],
                    })
                    continue

                payloads: list[dict] = []
                reply_payloads: list[dict] = []
                root_request_count = [0]

                def on_response(response) -> None:
                    response_path = urlsplit(response.url).path.rstrip("/")
                    is_reply = response_path.endswith(COMMENT_REPLY_HINT.rstrip("/"))
                    if not is_reply and not response_path.endswith(COMMENT_LIST_HINT.rstrip("/")):
                        return
                    try:
                        payload = response.json()
                    except Exception:
                        return
                    if isinstance(payload, dict):
                        if is_reply:
                            query = urllib.parse.parse_qs(urlsplit(response.url).query)
                            reply_payloads.append({
                                "comment_id": query.get("comment_id", [""])[0],
                                "payload": payload,
                            })
                        else:
                            payloads.append(payload)

                page.on("response", on_response)
                errors: list[str] = []
                truncated_by: list[str] = []

                def limit_root_comment_requests(route) -> None:
                    request_path = urlsplit(route.request.url).path.rstrip("/")
                    if request_path.endswith(COMMENT_LIST_HINT.rstrip("/")):
                        root_request_count[0] += 1
                        if root_request_count[0] > MAX_COMMENT_PAGES:
                            route.abort()
                            return
                    route.continue_()

                page.route("**/aweme/v1/web/comment/list/**", limit_root_comment_requests)
                try:
                    nav_ms = int(min(20000, max(1000, (left or 20) * 1000)))
                    page.goto(video_url, wait_until="domcontentloaded", timeout=nav_ms)
                    wait_ms = int(min(3500, max(0, (remaining() or 0) * 1000)))
                    wait_until = monotonic() + wait_ms / 1000
                    while not payloads and monotonic() < wait_until:
                        page.wait_for_timeout(100)
                    if payloads and remaining() != 0:
                        page.wait_for_timeout(min(350, int((remaining() or 0.35) * 1000)))

                    while len(payloads) < MAX_COMMENT_PAGES:
                        left = remaining()
                        if left is not None and left <= 0:
                            truncated_by.append("time_budget")
                            break
                        latest = payloads[-1] if payloads else {}
                        if not latest.get("has_more"):
                            break
                        before = len(payloads)
                        page.mouse.move(1250, 500)
                        page.mouse.wheel(0, 1400)
                        wait_until = monotonic() + min(1.5, left or 1.5)
                        while len(payloads) == before and monotonic() < wait_until:
                            page.wait_for_timeout(100)
                        if len(payloads) == before:
                            truncated_by.append("pagination_stalled")
                            break

                    reply_controls = page.get_by_text(re.compile(r"\d+\s*条回复"))
                    threads_to_expand = min(reply_controls.count(), MAX_REPLY_THREADS)
                    for control_index in range(threads_to_expand):
                        left = remaining()
                        if left is not None and left <= 0:
                            truncated_by.append("time_budget")
                            break
                        before = len(reply_payloads)
                        try:
                            reply_controls.nth(control_index).click(
                                timeout=int(min(1200, max(100, (left or 1) * 1000))))
                        except Exception:
                            continue
                        wait_until = monotonic() + min(1.2, remaining() or 1.2)
                        while len(reply_payloads) == before and monotonic() < wait_until:
                            page.wait_for_timeout(100)
                        if len(reply_payloads) == before:
                            continue
                        comment_id = reply_payloads[-1].get("comment_id")
                        for _ in range(1, MAX_REPLY_PAGES):
                            if not comment_id or remaining() == 0:
                                break
                            latest = next((entry["payload"] for entry in reversed(reply_payloads)
                                           if entry.get("comment_id") == comment_id), {})
                            if not latest.get("has_more"):
                                break
                            more_controls = page.get_by_text(MORE_REPLIES_PATTERN)
                            if more_controls.count() == 0:
                                break
                            before = len(reply_payloads)
                            try:
                                more_controls.last.click(
                                    timeout=int(min(1200, max(100, (remaining() or 1) * 1000))))
                            except Exception:
                                break
                            wait_until = monotonic() + min(1.2, remaining() or 1.2)
                            while len(reply_payloads) == before and monotonic() < wait_until:
                                page.wait_for_timeout(100)
                            if (len(reply_payloads) == before
                                    or not any(entry.get("comment_id") == comment_id
                                               for entry in reply_payloads[before:])):
                                break

                    if root_request_count[0] > MAX_COMMENT_PAGES:
                        truncated_by.append("comment_page_limit")

                    if not payloads:
                        try:
                            page_text = page.locator("body").inner_text(timeout=1000)
                        except Exception:
                            page_text = ""
                        if any(marker in page_text for marker in ("登录后查看", "请先登录", "扫码登录")):
                            errors.append("login_required")
                        else:
                            errors.append("comments:response_missing")
                except Exception as exc:  # noqa: BLE001 - keep other videos usable
                    errors.append("page:%s" % ("time_budget" if remaining() == 0
                                               else type(exc).__name__))
                finally:
                    try:
                        page.remove_listener("response", on_response)
                    except Exception:
                        pass
                    try:
                        page.unroute("**/aweme/v1/web/comment/list/**", limit_root_comment_requests)
                    except Exception:
                        pass

                _, comments, coverage = _parse_comment_pages(
                    payloads, aweme_id=aweme_id, max_comments=max_comments,
                    reply_payloads=reply_payloads)
                coverage["errors"].extend(errors)
                coverage["truncated_by"].extend(truncated_by)
                coverage["truncated_by"] = list(dict.fromkeys(coverage["truncated_by"]))
                if errors or coverage["truncated_by"]:
                    coverage["status"] = "partial"
                results[video_url] = ({}, comments, coverage)
    except Exception as exc:  # noqa: BLE001 - browser/profile failure is source-local
        for video_url, aweme_id in targets:
            results.setdefault(video_url, ({}, [], {
                "platform": "douyin", "aweme_id": aweme_id, "status": "partial",
                "fetched_comments": 0, "fetched_roots": 0, "fetched_replies": 0,
                "pages_attempted": 0, "max_comments": max_comments,
                "truncated_by": [], "errors": ["browser:%s" % type(exc).__name__],
            }))
    return results


async def fetch_many(items: list[dict], *, max_comments: int = MAX_DISCUSSION_COMMENTS,
                     budget_seconds: float | None = 20.0) -> dict:
    """Read bounded Douyin comment pages by driving the existing detail UI."""
    bounded_comments = max(1, min(int(max_comments), MAX_DISCUSSION_COMMENTS))
    return await asyncio.to_thread(_fetch_many_sync, items,
                                   max_comments=bounded_comments,
                                   budget_seconds=budget_seconds)


def walk_awemes(obj, out: list) -> None:
    """在任意嵌套里找 aweme_info / aweme_list 里的条目。"""
    if isinstance(obj, dict):
        # 单个 aweme
        if "aweme_id" in obj and ("desc" in obj or "aweme_id" in obj):
            out.append(obj)
            return
        for k, v in obj.items():
            if k in ("aweme_info", "aweme_detail") and isinstance(v, dict):
                out.append(v)
            elif isinstance(v, (dict, list)):
                walk_awemes(v, out)
    elif isinstance(obj, list):
        for it in obj:
            walk_awemes(it, out)


def main() -> int:
    args = parse_args()
    out = Path(getattr(args, "out", OUT_DEFAULT))

    queries = args.queries or DEFAULT_QUERIES
    if args.limit_queries:
        queries = queries[: args.limit_queries]

    prof = PROFILES / "douyin"
    rows: list[dict] = []
    seen: set[str] = set()

    with browser_session(prof, headed=getattr(args, "headed", False), user_agent=UA, prepare=ensure_env,
                         viewport={"width": 1440, "height": 950}) as ctx:
        write_checkpoint(out, [])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        bucket: list[dict] = []

        def on_resp(r) -> None:
            u = r.url
            if not any(h in u for h in SEARCH_HINTS):
                return
            ct = (r.headers or {}).get("content-type", "")
            if "json" not in ct:
                return
            try:
                body = r.json()
            except Exception:
                return
            found: list[dict] = []
            walk_awemes(body, found)
            bucket.extend(found)

        page.on("response", on_resp)

        for qi, q in enumerate(queries, 1):
            bucket.clear()
            url = ("https://www.douyin.com/search/"
                   + urllib.parse.quote(q) + "?type=general")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=50000)
            except Exception:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=40000)
                except Exception:
                    pass
            wait_for_items(page, lambda: len(bucket), timeout_ms=8000)

            before = len(rows)
            stall = 0
            for scroll in range(max(0, args.scrolls) + 1):
                if scroll:
                    before_response = len(bucket)
                    page.mouse.wheel(0, 3000)
                    wait_for_items(page, lambda: len(bucket), previous=before_response, timeout_ms=2800)

                added = 0
                for a in list(bucket):
                    aid = str(a.get("aweme_id") or "")
                    desc = clean(a.get("desc") or "")
                    if not aid or not desc or len(desc) < 4:
                        continue
                    if aid in seen:
                        continue
                    seen.add(aid)
                    author = ((a.get("author") or {}).get("nickname") or "")
                    stats = a.get("statistics") or {}
                    ct_epoch = a.get("create_time")
                    rows.append({
                        "platform": "douyin",
                        "platform_name": "抖音搜索",
                        "kind": "视频",
                        "title": desc[:120],
                        "url": "https://www.douyin.com/video/%s" % aid,
                        "text": desc[:400],
                        "author": clean(author),
                        "date": str(ct_epoch or ""),
                        "likes": str(stats.get("digg_count") or ""),
                        "query": q,
                    })
                    added += 1

                if added:
                    write_checkpoint(out, rows)
                if added == 0:
                    stall += 1
                    if stall >= 3:
                        break
                else:
                    stall = 0

            print("  [%d/%d] %-14s +%-4d 累计 %d" % (
                qi, len(queries), q, len(rows) - before, len(rows)), flush=True)

    write_checkpoint(out, rows)
    print()
    print("queries: %d   items: %d   -> %s" % (len(queries), len(rows), out.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_collector(main))
