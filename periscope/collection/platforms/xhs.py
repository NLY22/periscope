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
import argparse
import json
import re
import sys
import urllib.parse

from periscope.collection.platforms.browser import browser_session, ensure_env, run_collector, wait_for_items, write_checkpoint
from periscope.runtime import paths


ROOT = paths.ROOT

PROFILES = paths.PROFILES
# Standalone runs use the collection directory; topic lookup can name a shard explicitly.
OUT_DEFAULT = paths.COLLECT_DIR / "ugc_search.xhs.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

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
