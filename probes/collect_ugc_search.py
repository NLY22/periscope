"""Collect from platform *search*, not just their front pages.

The measurement that motivates this: on the HTTP side, three single-term HN
sources returned 1,055 items while the same endpoint driven by a keyword matrix
returned 31,063. A hot list is one page of one question; a search is as many
pages of as many questions as you ask.

The platform collectors only ever read hot lists, which is why twelve platforms
produced 867 items. Probe results say why search was not already doing this: via
HTTP only 知乎 answers (and pages - 19 new results on page 2), while B站 returns
412, 贴吧 times out, and 小红书/掘金/CSDN return shapes that need the real DOM.
So this drives a browser, reusing the proven selector approach.

Usage:
    python collect_ugc_search.py                      # every platform, default queries
    python collect_ugc_search.py zhihu bili           # named platforms
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import io
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List
from urllib.parse import quote, urljoin


from playwright.sync_api import sync_playwright

ROOT = paths.ROOT
PROFILES = ROOT / ".pwprofiles"
OUT = paths.COLLECT_DIR / "ugc_search.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# The keyword matrix. Same principle as the HTTP side: one platform asked forty
# questions covers forty topics, and a term that returns nothing costs one page
# load. Kept to the subjects a market researcher needs - the models, the money,
# the hardware, the failures - rather than generic tech terms.
QUERIES = (
    # Technical subjects: what a market researcher needs to see - the models,
    # the money, the hardware.
    "大模型", "AI agent", "开源模型", "模型发布", "AI 编程",
    "算力", "英伟达", "推理优化", "微调", "多模态",
    "AI 创业", "融资", "AI 安全", "智能体", "具身智能",
    # Question-shaped terms. A platform is where people say what they think, and
    # thoughts are phrased as questions and complaints far more often than as
    # product names. These surface the discussions that a keyword like "大模型"
    # ranks below press coverage.
    "AI 好用吗", "AI 替代", "AI 离职", "AI 裁员", "AI 失业",
    "ChatGPT 体验", "DeepSeek 使用", "大模型 幻觉", "AI 翻车",
    "提示词", "本地部署", "显存 够吗", "AI 副业", "AI 接单",
    # Vendors people actually argue about on Chinese platforms.
    "OpenAI", "Claude", "DeepSeek", "通义千问", "豆包", "Kimi",
    "文心一言", "智谱", "月之暗面", "MiniMax", "Qwen",
)


@dataclass(frozen=True)
class SearchPlatform:
    """One platform's search surface, and how to read it."""

    key: str
    name: str
    kind: str
    url_template: str            # {q} is the URL-encoded query
    selectors: tuple
    profile: str = ""
    wait_ms: int = 6000
    max_scrolls: int = 6
    scroll_wait_ms: int = 2200
    stall_limit: int = 2
    limit: int = 120
    # Some platforms answer a search with JSON, not a rendered page. When set,
    # the collector fetches the URL with the browser's request context and reads
    # `json_path` rather than running DOM selectors - 豆瓣's rexxar API is like
    # this, and it returns its list under `subject_collection_items`, not
    # `items`, which is why `total=29, items=[]` read as an empty result.
    json_path: tuple = ()
    json_title: str = "title"
    json_url: str = "url"
    # Some platforms need a click or a tab switch after landing.
    post_goto: str = ""          # css selector to click, if any


SEARCHES: List[SearchPlatform] = [
    SearchPlatform("zhihu", "知乎搜索", "问答",
                   "https://www.zhihu.com/search?q={q}&type=content",
                   ("div[class*='SearchResult'] a[href*='/question/']",
                    "a[href*='/question/']", "div[class*='Card'] a"),
                   profile="zhihu", limit=150),
    SearchPlatform("bili", "B站搜索", "短视频",
                   "https://search.bilibili.com/all?keyword={q}",
                   ("div[class*='video-list'] a", "a[href*='/video/BV']",
                    "div[class*='bili-video-card'] a"),
                   limit=150),
    SearchPlatform("xhs", "小红书搜索", "图文",
                   "https://www.xiaohongshu.com/search_result?keyword={q}",
                   ("section[class*='note-item']", "a[href*='/explore/']",
                    "div[class*='note-item']"),
                   profile="xhs", limit=150),
    SearchPlatform("juejin", "掘金搜索", "技术社区",
                   "https://juejin.cn/search?query={q}",
                   ("div[class*='entry'] a", "a[href*='/post/']",
                    "li[class*='item'] a"),
                   limit=120),
    SearchPlatform("csdn", "CSDN 搜索", "技术社区",
                   "https://so.csdn.net/so/search?q={q}&t=blog",
                   ("div[class*='search-list'] a", "a[href*='blog.csdn.net']",
                    "div[class*='list-item'] a"),
                   limit=120),
    # These five were configured from guesses and returned 0 items each time.
    # Probed properly (probe_search_dom.py), and the reasons are all different:
    #
    #   贴吧   30 results on `a[href*='/p/']` with EMPTY link text - the headline
    #          is on the title attribute / the enclosing row. Fixed by the
    #          attribute fallback in `extract`, and it now works.
    #   微博   redirects to passport.weibo.com/sso/signin: login required.
    #   豆瓣   `douban.com/search?q=` answers "Page not found" - that endpoint is
    #          gone, not blocked.
    #   虎扑   search.hupu.com does not resolve at all (DNS).
    #   smzdm  renders an empty document to a headless client.
    #
    # So 贴吧 stays; the other four are removed rather than left in the list
    # silently contributing nothing on every run. Re-adding them needs a session
    # (微博, smzdm) or a different endpoint (豆瓣, 虎扑), which is separate work.
    SearchPlatform("tieba", "贴吧搜索", "论坛",
                   "https://tieba.baidu.com/f/search/res?qw={q}",
                   ("a[href*='/p/']",),
                   profile="tieba", limit=150, max_scrolls=8),
    # 虎扑: the search page answers 200 with 123 KB of HTML and twenty topic
    # links, but the links are absolute (`https://bbs.hupu.com/N.html`) and the
    # earlier probe only matched relative ones - so a working platform looked
    # dead. It was a parser bug, not a block.
    SearchPlatform("hupu", "虎扑搜索", "论坛",
                   "https://bbs.hupu.com/search?q={q}&page=1",
                   ("a[href*='bbs.hupu.com/']", "a[href$='.html']"),
                   limit=150, max_scrolls=6),
    # 豆瓣: the rexxar API returns its list under `subject_collection_items`, not
    # `items`, which is why `total=29, items=[]` looked like an empty result.
    # It is not a search endpoint - it lists what is showing - so it takes no
    # query and contributes one page per run.
    SearchPlatform("douban", "豆瓣", "兴趣社区",
                   "https://m.douban.com/rexxar/api/v2/subject_collection/"
                   "movie_showing/items?start=0&count=20",
                   ("div[class*='item'] a",),
                   limit=60, max_scrolls=1,
                   json_path=("subject_collection_items",),
                   json_title="title", json_url="url"),
    SearchPlatform("producthunt", "Product Hunt 搜索", "产品",
                   "https://www.producthunt.com/search?q={q}",
                   ("a[href*='/posts/']", "div[class*='item'] a"),
                   limit=100),
]

# boilerplate that generic selectors drag in
import re  # noqa: E402

NOISE = re.compile(
    r"^(首页|我的|登录|注册|下载|客户端|消息|反馈|更多|展开|收起|搜索|关注|推荐|"
    r"手机|加入|导航|全部|热门|排行榜|Sign|Log|Home|More|Menu|"
    r"上一页|下一页|相关搜索|大家都在搜|搜索历史|清除)$", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _is_item(title: str, url: str) -> bool:
    if not title or len(title) < 6:
        return False
    if NOISE.match(title):
        return False
    return bool(url and url.rstrip("/") not in ("", "https:/"))


def extract(page, platform: SearchPlatform) -> List[Dict[str, Any]]:
    """First selector that yields real items wins, as in the hot-list collector."""
    # Relative hrefs must resolve against the page that was actually loaded, not
    # against a template: a search page can redirect, and resolving against the
    # wrong base is what produced `https://search.bilibili.com//space.bilibili...`
    page_url = ""
    try:
        page_url = page.url or ""
    except Exception:
        pass
    for sel in platform.selectors:
        rows: List[Dict[str, Any]] = []
        seen = set()
        try:
            loc = page.locator(sel)
            n = min(loc.count(), platform.limit * 3)
        except Exception:
            continue
        if n == 0:
            continue
        for i in range(n):
            node = loc.nth(i)
            try:
                text = _clean(node.inner_text(timeout=2000))
            except Exception:
                text = ""
            href = ""
            try:
                href = node.get_attribute("href") or ""
                if not href:
                    inner = node.locator("a[href]").first
                    if inner.count():
                        href = inner.get_attribute("href") or ""
            except Exception:
                pass

            # Some sites put the headline in an attribute or in the row, not in
            # the anchor's text. 贴吧's search results are 30 `a[href*='/p/']`
            # links with empty inner text and the title on `title=`, and ignoring
            # that made the platform look like it returned nothing.
            if not text:
                for attr in ("title", "aria-label", "data-title"):
                    try:
                        candidate = _clean(node.get_attribute(attr) or "")
                    except Exception:
                        candidate = ""
                    if candidate:
                        text = candidate
                        break
            if not text:
                # Last resort: the enclosing row, which holds the headline plus
                # author/date. Long enough to be a title, and the noise filter
                # and 200-char cap keep it usable.
                try:
                    text = _clean(node.locator("xpath=..").inner_text(timeout=1500))
                except Exception:
                    text = ""

            if not _is_item(text, href or "x"):
                continue
            title = text[:200]
            if title in seen:
                continue
            seen.add(title)
            # Resolve relative hrefs properly. The previous version glues
            # "https://" + host + href for paths starting with "/", which
            # produces `https://search.bilibili.com//space.bilibili.com/6946`
            # whenever the site writes a scheme-relative URL - a form that looks
            # like a path but already names a host.
            resolved = urljoin(page_url or platform.url_template, href) if href else ""
            rows.append({"platform": platform.key, "platform_name": platform.name,
                         "kind": platform.kind, "title": title,
                         "url": resolved,
                         "text": text[:1000]})
            if len(rows) >= platform.limit:
                break
        if len(rows) >= 3:
            return rows
    return []


def extract_json(page, platform: SearchPlatform) -> List[Dict[str, Any]]:
    """Read a JSON search response from a page the browser already fetched.

    Uses the browser as the transport (cookies, TLS fingerprint, headers all
    handled) but parses the body as data. This is how 豆瓣's rexxar API is
    reached: the endpoint answers JSON and refuses a bare HTTP client, but the
    logged-in browser can load it.
    """
    try:
        body = page.content() if hasattr(page, "content") else b""
    except Exception:
        return []
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    start = text.find("{")
    if start < 0:
        return []
    try:
        data = json.loads(text[start:])
    except Exception:
        return []

    items = None
    for key in platform.json_path:
        if isinstance(data, dict) and isinstance(data.get(key), list):
            items = data[key]
            break
        if isinstance(data, list):
            items = data
            break
    if not isinstance(items, list):
        return []

    rows: List[Dict[str, Any]] = []
    seen = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        title = _clean(str(it.get(platform.json_title) or ""))
        if not title or title in seen:
            continue
        seen.add(title)
        rows.append({"platform": platform.key, "platform_name": platform.name,
                     "kind": platform.kind, "title": title[:200],
                     "url": str(it.get(platform.json_url) or ""),
                     "text": _clean(str(it.get("description") or title))[:1000]})
    return rows


def collect_query(page, platform: SearchPlatform, query: str):
    """Load one search URL, scroll until the result count stops growing."""
    url = platform.url_template.replace("{q}", quote(query))
    # A JSON endpoint takes no query, so every "query" would fetch the same page.
    if platform.json_path:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        return extract_json(page, platform)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    page.wait_for_timeout(platform.wait_ms)
    if platform.post_goto:
        try:
            page.click(platform.post_goto, timeout=3000)
            page.wait_for_timeout(1500)
        except Exception:
            pass
    best, stalls = 0, 0
    for _ in range(platform.max_scrolls):
        try:
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(platform.scroll_wait_ms)
        except Exception:
            break
        got = len(extract(page, platform))
        if got > best:
            best, stalls = got, 0
        else:
            stalls += 1
            if stalls >= platform.stall_limit:
                break
    return extract(page, platform)


def collect_platform(p, platform: SearchPlatform, queries):
    started = time.monotonic()
    ctx = None
    closer = None
    rows: List[Dict[str, Any]] = []
    seen = set()
    error = ""
    try:
        if platform.profile and (PROFILES / platform.profile).exists():
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILES / platform.profile), channel="chrome",
                headless=True, user_agent=UA, locale="zh-CN",
                viewport={"width": 1500, "height": 950})
            closer = ctx.close
        else:
            browser = p.chromium.launch(channel="chrome", headless=True)
            ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                      viewport={"width": 1500, "height": 950})
            def closer(b=browser, c=ctx):
                c.close(); b.close()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # A JSON endpoint ignores the query, so running the whole keyword matrix
        # against it fetches the same page forty times. One fetch is enough.
        run_queries = queries[:1] if platform.json_path else queries
        for query in run_queries:
            try:
                got = collect_query(page, platform, query)
            except Exception as exc:
                error = "%s: %s" % (type(exc).__name__, str(exc)[:40])
                continue
            for row in got:
                key = row["title"]
                if key in seen:
                    continue
                seen.add(key)
                row["query"] = query
                rows.append(row)
    except Exception as exc:
        error = "%s: %s" % (type(exc).__name__, str(exc)[:60])
    finally:
        if closer:
            try:
                closer()
            except Exception:
                pass
    return rows, round(time.monotonic() - started, 1), error


def main() -> int:
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")]
    only = None
    if "--limit-queries" in sys.argv:
        only = int(sys.argv[sys.argv.index("--limit-queries") + 1])
    targets = [s for s in SEARCHES if not wanted or s.key in wanted]
    queries = QUERIES[:only] if only else QUERIES
    print("platforms: %d   queries per platform: %d" % (len(targets), len(queries)))
    print("=" * 84)

    all_rows: List[Dict[str, Any]] = []
    summary = []
    with sync_playwright() as p:
        for platform in targets:
            rows, secs, err = collect_platform(p, platform, queries)
            all_rows.extend(rows)
            summary.append((platform.key, platform.name, len(rows), secs, err))
            print("%-14s %-18s %5d items  %6.1fs  %s"
                  % (platform.key, platform.name, len(rows), secs, err))

    out = OUT if not wanted else OUT.with_name(
        "ugc_search.%s.json" % "-".join(sorted(wanted))[:40])
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("=" * 84)
    ok = sum(1 for s in summary if s[2])
    print("platforms with items: %d/%d   items: %d   -> %s"
          % (ok, len(summary), len(all_rows), out.name))
    from collections import Counter
    print("by kind:", dict(Counter(r["kind"] for r in all_rows)))
    print("by query (top 10):",
          dict(Counter(r.get("query") for r in all_rows).most_common(10)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
