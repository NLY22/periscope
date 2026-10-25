"""小红书搜索采集器（浏览器 DOM 提取）。

API 直连返回 300011（账号风控），但浏览器内 DOM 有真实结果：
  section.note-item
    data-note-id = 笔记 ID
    innerText 逐行 = 标题 / 作者 / 日期 / 点赞
    a[href*='/search_result/{id}?xsec_token=...'] = 可访问链接

用法：
    python collect_xhs.py --queries 人形机器人 固态电池
    python collect_xhs.py --limit-queries 3 --scrolls 12
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import argparse
import asyncio
import json
import re
import sys
import time
import urllib.parse
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from periscope.collection.platforms.browser import browser_session, ensure_env, run_collector, wait_for_items, write_checkpoint
from periscope.runtime import paths


ROOT = paths.ROOT

PROFILES = paths.PROFILES
# Standalone runs use the collection directory; topic lookup can name a shard explicitly.
OUT_DEFAULT = paths.COLLECT_DIR / "ugc_search.xhs.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

MAX_DISCUSSION_COMMENTS = 60
MAX_COMMENT_PAGES = 4
MAX_REPLY_THREADS = 5
MAX_REPLY_PAGES = 3
MAX_NOTE_BODY_CHARS = 12000
COMMENT_API_HINT = "/api/sns/web/v2/comment/page"
COMMENT_SUB_API_HINT = "/api/sns/web/v2/comment/sub/page"
_NOTE_ID = re.compile(r"^/(?:explore|search_result)/([A-Za-z0-9]+)(?:/)?$")


def _note_id(note_url: str) -> str:
    """Return a native note ID only for a Xiaohongshu note URL."""
    try:
        parsed = urlsplit(str(note_url or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in {
            "xiaohongshu.com", "www.xiaohongshu.com"}:
        return ""
    match = _NOTE_ID.fullmatch(parsed.path)
    return match.group(1) if match else ""


def _plain_text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return "\n".join(part.strip() for part in
                     BeautifulSoup(value, "html.parser").get_text("\n").splitlines()
                     if part.strip())


def _canonical_note_url(note_id: str) -> str:
    return "https://www.xiaohongshu.com/explore/%s" % note_id


def _comment_record(comment: dict, *, note_id: str, note_url: str,
                    parent_id: str = "") -> dict:
    user = comment.get("user_info") or {}
    record = {
        "id": str(comment.get("id") or ""),
        "note_id": str(comment.get("note_id") or note_id),
        "text": _plain_text(comment.get("content") or ""),
        "author": str(user.get("nickname") or ""),
        "likes": _as_int(comment.get("like_count")),
        "created_at": str(comment.get("create_time") or ""),
        "url": _canonical_note_url(note_id),
    }
    if parent_id:
        record["parent_id"] = parent_id
    return record


def _parse_comment_pages(payloads: list[dict], *, note_id: str, note_url: str,
                         max_comments: int = MAX_DISCUSSION_COMMENTS,
                         sub_payloads: list[dict] | None = None):
    """Normalize root and expanded sub-comment pages with explicit coverage."""
    roots = []
    roots_by_id: dict[str, dict] = {}
    expected_replies: dict[str, int] = {}
    reply_has_more: set[str] = set()
    reply_page_counts: dict[str, int] = {}
    seen: set[str] = set()
    truncated_by: list[str] = []
    errors: list[str] = []
    reported_comments = 0
    has_more = False
    pages_attempted = 0
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            if "comments:invalid_payload" not in errors:
                errors.append("comments:invalid_payload")
            continue
        if "comments" not in data:
            if "comments:invalid_payload" not in errors:
                errors.append("comments:invalid_payload")
            continue
        pages_attempted += 1
        has_more = bool(data.get("has_more"))
        reported_comments = max(reported_comments, _as_int(
            data.get("total") or data.get("total_count") or data.get("comments_count")))
        for item in data.get("comments") or []:
            if not isinstance(item, dict):
                continue
            comment_id = str(item.get("id") or "")
            if not comment_id or comment_id in seen:
                continue
            seen.add(comment_id)
            record = _comment_record(item, note_id=note_id, note_url=note_url)
            replies = []
            for reply in item.get("sub_comments") or []:
                if not isinstance(reply, dict):
                    continue
                reply_id = str(reply.get("id") or "")
                if not reply_id or reply_id in seen:
                    continue
                seen.add(reply_id)
                replies.append(_comment_record(reply, note_id=note_id,
                                               note_url=note_url, parent_id=comment_id))
            record["replies"] = replies
            reply_count = _as_int(item.get("sub_comment_count"))
            roots_by_id[comment_id] = record
            expected_replies[comment_id] = reply_count
            if bool(item.get("sub_comment_has_more")):
                reply_has_more.add(comment_id)
            roots.append(record)
            if len(seen) >= max_comments:
                truncated_by.append("comment_limit")
                break
        if len(seen) >= max_comments:
            break

    for entry in sub_payloads or []:
        parent_id = str(entry.get("root_comment_id") or "")
        payload = entry.get("payload") or {}
        data = payload.get("data") if isinstance(payload, dict) else None
        if parent_id not in roots_by_id or not isinstance(data, dict) or not isinstance(data.get("comments"), list):
            if "reply:invalid_payload" not in errors:
                errors.append("reply:invalid_payload")
            continue
        reply_page_counts[parent_id] = reply_page_counts.get(parent_id, 0) + 1
        if data.get("has_more"):
            reply_has_more.add(parent_id)
        root = roots_by_id[parent_id]
        for reply in data["comments"]:
            if not isinstance(reply, dict):
                continue
            reply_id = str(reply.get("id") or "")
            if not reply_id or reply_id in seen:
                continue
            if len(seen) >= max_comments:
                truncated_by.append("comment_limit")
                break
            seen.add(reply_id)
            root["replies"].append(_comment_record(
                reply, note_id=note_id, note_url=note_url, parent_id=parent_id))
        if len(seen) >= max_comments:
            break

    for root_id, expected in expected_replies.items():
        fetched = len(roots_by_id[root_id]["replies"])
        if expected > fetched or root_id in reply_has_more:
            if root_id in reply_page_counts:
                truncated_by.append("reply_page_limit" if root_id in reply_has_more
                                    else "reply_thread_incomplete")
            else:
                truncated_by.append("reply_preview_only")
    if has_more:
        truncated_by.append("comment_page_limit")
    fetched_roots = len(roots)
    fetched_replies = sum(len(root["replies"]) for root in roots)
    coverage = {
        "platform": "xhs", "note_id": note_id,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "status": "partial" if truncated_by or errors or not pages_attempted else "complete",
        "reported_comments": reported_comments,
        "fetched_comments": fetched_roots + fetched_replies,
        "fetched_roots": fetched_roots, "fetched_replies": fetched_replies,
        "pages_attempted": pages_attempted, "max_comments": max_comments,
        "reported_replies": sum(expected_replies.values()),
        "reply_pages_attempted": sum(reply_page_counts.values()),
        "truncated_by": list(dict.fromkeys(truncated_by)), "errors": errors,
    }
    return {}, roots, coverage


def _as_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0

# 默认查询词：沿用项目的中文词表
DEFAULT_QUERIES = [
    "人形机器人", "具身智能", "大模型", "AI 应用", "智能体",
    "算力", "芯片", "英伟达", "DeepSeek", "通义千问",
    "豆包", "Kimi", "文心一言", "智谱", "MiniMax",
    "AI 副业", "AI 失业", "本地部署", "提示词", "AI 翻车",
]

EXTRACT_JS = """() => {
    const out = [];
    for (const el of document.querySelectorAll('section.note-item')) {
        const raw = (el.innerText || '').trim();
        if (!raw) continue;
        const lines = raw.split('\\n').map(s => s.trim()).filter(Boolean);
        if (!lines.length) continue;
        // 链接：优先带 xsec_token 的 search_result 链接
        let href = '';
        for (const a of el.querySelectorAll('a')) {
            const h = a.getAttribute('href') || '';
            if (h.includes('/search_result/') && h.includes('xsec_token')) { href = h; break; }
        }
        if (!href) {
            for (const a of el.querySelectorAll('a')) {
                const h = a.getAttribute('href') || '';
                if (h.includes('/explore/')) { href = h; break; }
            }
        }
        out.push({
            note_id: el.getAttribute('data-note-id') || '',
            lines: lines.slice(0, 6),
            href: href,
            raw: raw.slice(0, 220)
        });
    }
    return out;
}"""

# 小红书的日期行有多种写法，行序也不固定（有的标题在前、有的日期在前），
# 所以不能按下标取，要按模式识别再挑剩下的最长行当标题。
DATE_PATTERNS = [
    re.compile(r"^\d{1,2}-\d{1,2}$"),                      # 08-30
    re.compile(r"^\d{2,4}-\d{1,2}-\d{1,2}$"),              # 2026-08-30
    re.compile(r"^(昨天|今天|前天)\s*\d{0,2}:?\d{0,2}$"),  # 昨天 20:25
    re.compile(r"^\d+\s*(天|小时|分钟|秒)前$"),             # 3天前
    re.compile(r"^\d{1,2}:\d{2}$"),                        # 20:25
    re.compile(r"^\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}$"),      # 08-30 20:25
]

# 混在结果里的推荐块 / 提示行，不是笔记
NOISE = (
    "大家都在搜", "大家都在问", "相关搜索", "猜你喜欢", "为你推荐",
    "查看更多", "展开", "收起", "赞", "收藏", "评论",
)


def is_date_line(s: str) -> bool:
    return any(p.match(s) for p in DATE_PATTERNS)


def is_noise(s: str) -> bool:
    return any(n in s for n in NOISE) or s in ("赞", "收藏", "评论", "分享")


def pick_fields(lines: list[str]) -> tuple[str, str, str, str]:
    """从若干行里挑出 (标题, 作者, 日期, 点赞)，与行序无关。"""
    date = ""
    rest: list[str] = []
    likes = ""
    for s in lines:
        if not date and is_date_line(s):
            date = s
            continue
        rest.append(s)
    # 点赞数：纯数字且在末位
    if rest and re.fullmatch(r"\d{1,7}(\.\d)?[万wW]?", rest[-1]):
        likes = rest.pop()
    # 标题 = 剩下最长的、不像作者名的行
    candidates = [s for s in rest if not is_noise(s) and len(s) >= 3]
    if not candidates:
        return ("", "", date, likes)
    title = max(candidates, key=len)
    others = [s for s in candidates if s != title]
    author = others[0] if others else ""
    return (title, author, date, likes)


def clean(s: str) -> str:
    return " ".join((s or "").split())


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", nargs="*", default=None)
    ap.add_argument("--limit-queries", type=int, default=0)
    ap.add_argument("--scrolls", type=int, default=10,
                    help="每个查询滚动次数（决定深度）")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    # 默认无头：不弹窗、不占用屏幕。实测无头模式能拿到完整 30 条结果。
    # 需要看界面调试时才加 --headed。
    ap.add_argument("--headed", action="store_true", default=False,
                    help="显示浏览器窗口（默认不显示）")
    return ap.parse_args()


def _wait_for_notes(page, known: set[str], timeout_ms: int) -> bool:
    from playwright.sync_api import TimeoutError as BrowserTimeout
    condition = "known => (" + EXTRACT_JS + ")().some(note => !known.includes(note.note_id || note.href || note.raw))"
    try:
        page.wait_for_function(condition, arg=list(known), timeout=timeout_ms, polling=150).dispose()
        return True
    except BrowserTimeout:
        return False


def _fetch_many_sync(items: list[dict], *, max_comments: int,
                     budget_seconds: float | None) -> dict:
    from time import monotonic

    deadline = None if budget_seconds is None else monotonic() + max(0.0, budget_seconds)
    results = {}
    targets = []
    for item in items:
        url = str(item.get("url") or "")
        note_id = _note_id(url)
        if note_id:
            targets.append((url, note_id))
        else:
            results[url] = ({}, [], {
                "platform": "xhs", "status": "partial", "note_id": "",
                "fetched_comments": 0, "fetched_roots": 0, "fetched_replies": 0,
                "pages_attempted": 0, "max_comments": max_comments,
                "truncated_by": [], "errors": ["invalid_note_url"],
            })
    if not targets:
        return results

    def remaining() -> float | None:
        return None if deadline is None else max(0.0, deadline - monotonic())

    try:
        with browser_session(PROFILES / "xhs", headed=False, user_agent=UA,
                             prepare=ensure_env,
                             viewport={"width": 1440, "height": 950}) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for note_url, note_id in targets:
                left = remaining()
                if left is not None and left <= 0:
                    results[note_url] = ({}, [], {
                        "platform": "xhs", "note_id": note_id, "status": "partial",
                        "fetched_comments": 0, "fetched_roots": 0, "fetched_replies": 0,
                        "pages_attempted": 0, "max_comments": max_comments,
                        "truncated_by": ["time_budget"], "errors": [],
                    })
                    continue

                payloads: list[dict] = []
                sub_payloads: list[dict] = []

                def on_response(response) -> None:
                    response_path = urlsplit(response.url).path
                    is_sub_comment = COMMENT_SUB_API_HINT in response_path
                    if not is_sub_comment and COMMENT_API_HINT not in response_path:
                        return
                    try:
                        payload = response.json()
                    except Exception:
                        return
                    if isinstance(payload, dict):
                        if is_sub_comment:
                            root_comment_id = (parse_qs(urlsplit(response.url).query)
                                               .get("root_comment_id", [""])[0])
                            sub_payloads.append({"root_comment_id": root_comment_id,
                                                 "payload": payload})
                        else:
                            payloads.append(payload)

                page.on("response", on_response)
                errors: list[str] = []
                detail = {"note_id": note_id, "url": _canonical_note_url(note_id), "text": ""}
                try:
                    nav_ms = int(min(15000, max(1000, (left or 15) * 1000)))
                    page.goto(note_url, wait_until="domcontentloaded", timeout=nav_ms)
                    left = remaining()
                    page.wait_for_timeout(int(min(1400, max(0, (left or 1) * 1000))))
                    for selector, field in (("#detail-title", "title"),
                                            (".note-content", "text"),
                                            ("#user-nickname", "author")):
                        left = remaining()
                        if left is not None and left <= 0:
                            break
                        try:
                            locator = page.locator(selector).first
                            if locator.count():
                                value = locator.inner_text(timeout=1200).strip()
                                if value:
                                    detail[field] = value[:MAX_NOTE_BODY_CHARS]
                        except Exception:
                            continue

                    page_text = ""
                    try:
                        page_text = page.locator("body").inner_text(timeout=1000)
                    except Exception:
                        pass
                    if any(marker in page_text for marker in ("扫码登录", "登录后查看", "请先登录")):
                        errors.append("login_required")

                    for page_index in range(MAX_COMMENT_PAGES):
                        left = remaining()
                        if left is not None and left <= 0:
                            break
                        latest = payloads[-1].get("data") if payloads else {}
                        if page_index and not (latest or {}).get("has_more"):
                            break
                        before = len(payloads)
                        if page_index:
                            page.mouse.wheel(0, 1100)
                        wait_ms = int(min(1100, max(0, (remaining() or 0) * 1000)))
                        if wait_ms:
                            page.wait_for_timeout(wait_ms)
                        if len(payloads) == before and page_index == 0:
                            continue

                    reply_controls = page.get_by_text(re.compile(r"\d+\s*条回复"))
                    controls_to_expand = min(reply_controls.count(), MAX_REPLY_THREADS)
                    for control_index in range(controls_to_expand):
                        left = remaining()
                        if left is not None and left <= 0:
                            break
                        before = len(sub_payloads)
                        try:
                            reply_controls.nth(control_index).click(
                                timeout=int(min(1000, max(100, (left or 1) * 1000))))
                        except Exception:
                            continue
                        wait_until = monotonic() + min(1.5, remaining() or 1.5)
                        while len(sub_payloads) == before and monotonic() < wait_until:
                            page.wait_for_timeout(100)
                        if len(sub_payloads) == before:
                            continue
                        parent_id = sub_payloads[-1].get("root_comment_id")
                        reply_page_index = 1
                        while (parent_id and reply_page_index < MAX_REPLY_PAGES
                               and remaining() != 0):
                            latest = next((entry["payload"].get("data") or {}
                                           for entry in reversed(sub_payloads)
                                           if entry.get("root_comment_id") == parent_id), {})
                            if not latest.get("has_more"):
                                break
                            before = len(sub_payloads)
                            page.mouse.move(1250, 500)
                            page.mouse.wheel(0, 900)
                            wait_until = monotonic() + min(1.2, remaining() or 1.2)
                            while len(sub_payloads) == before and monotonic() < wait_until:
                                page.wait_for_timeout(100)
                            if len(sub_payloads) == before:
                                break
                            reply_page_index += 1

                    if not payloads and not errors:
                        errors.append("comments:response_missing")
                except Exception as exc:  # noqa: BLE001 - keep per-note failures isolated
                    label = "time_budget" if remaining() == 0 else type(exc).__name__
                    errors.append("page:%s" % label)
                finally:
                    try:
                        page.remove_listener("response", on_response)
                    except Exception:
                        pass

                _, comments, coverage = _parse_comment_pages(
                    payloads, note_id=note_id, note_url=note_url,
                    max_comments=max_comments, sub_payloads=sub_payloads)
                coverage["errors"].extend(errors)
                if remaining() == 0 and "time_budget" not in coverage["truncated_by"]:
                    coverage["truncated_by"].append("time_budget")
                if not payloads or errors or coverage["truncated_by"]:
                    coverage["status"] = "partial"
                if len(detail.get("text") or "") >= MAX_NOTE_BODY_CHARS:
                    coverage["truncated_by"].append("body_limit")
                    coverage["status"] = "partial"
                results[note_url] = (detail, comments, coverage)
    except Exception as exc:  # noqa: BLE001 - profile/browser errors are source-local
        for note_url, note_id in targets:
            results.setdefault(note_url, ({}, [], {
                "platform": "xhs", "note_id": note_id, "status": "partial",
                "fetched_comments": 0, "fetched_roots": 0, "fetched_replies": 0,
                "pages_attempted": 0, "max_comments": max_comments,
                "truncated_by": [], "errors": ["browser:%s" % type(exc).__name__],
            }))
    return results


async def fetch_many(items: list[dict], *, max_comments: int = MAX_DISCUSSION_COMMENTS,
                     budget_seconds: float | None = 20.0) -> dict:
    """Enrich note details and observed discussion in one reused browser session."""
    bounded_comments = max(1, min(int(max_comments), MAX_DISCUSSION_COMMENTS))
    return await asyncio.to_thread(_fetch_many_sync, items,
                                   max_comments=bounded_comments,
                                   budget_seconds=budget_seconds)


def main() -> int:
    args = parse_args()
    out = Path(getattr(args, "out", OUT_DEFAULT))

    queries = args.queries or DEFAULT_QUERIES
    if args.limit_queries:
        queries = queries[: args.limit_queries]

    prof = PROFILES / "xhs"
    rows: list[dict] = []
    seen: set[str] = set()
    stats: list[tuple[str, int, int]] = []
    known: set[str] = set()

    with browser_session(prof, headed=getattr(args, "headed", False), user_agent=UA, prepare=ensure_env,
                         viewport={"width": 1440, "height": 950}) as ctx:
        write_checkpoint(out, [])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for qi, q in enumerate(queries, 1):
            url = ("https://www.xiaohongshu.com/search_result?keyword="
                   + urllib.parse.quote(q) + "&type=51")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=50000)
            except Exception:
                page.reload(wait_until="domcontentloaded")
            known.clear()
            _wait_for_notes(page, known, 8000)

            before = len(rows)
            stall = 0
            for scroll in range(max(0, args.scrolls) + 1):
                if scroll:
                    page.mouse.wheel(0, 2800)
                    _wait_for_notes(page, known, 2200)
                try:
                    items = page.evaluate(EXTRACT_JS)
                except Exception:
                    items = []
                known.update(str(it.get("note_id") or it.get("href") or it.get("raw") or "") for it in items)
                added = 0
                for it in items:
                    nid = clean(it.get("note_id"))
                    lines = [clean(x) for x in (it.get("lines") or []) if clean(x)]
                    title, author, date, likes = pick_fields(lines)
                    if not title or len(title) < 4:
                        continue
                    key = nid or title
                    if key in seen:
                        continue
                    seen.add(key)
                    href = it.get("href") or ""
                    if href.startswith("/"):
                        href = "https://www.xiaohongshu.com" + href
                    rows.append({
                        "platform": "xhs",
                        "platform_name": "小红书搜索",
                        "kind": "笔记",
                        "title": title,
                        "url": href,
                        "text": title,
                        "author": author,
                        "date": date,
                        "likes": likes,
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

            got = len(rows) - before
            stats.append((q, got, len(rows)))
            print("  [%d/%d] %-14s +%-4d 累计 %d" % (qi, len(queries), q, got, len(rows)),
                  flush=True)

    write_checkpoint(out, rows)
    print()
    print("queries: %d   items: %d   -> %s" % (len(queries), len(rows), out.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_collector(main))
