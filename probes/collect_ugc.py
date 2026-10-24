"""Collect from UGC platforms through a real browser.

Why a separate collector from `collector.py`: those sources are HTTP endpoints
with a schema. These are JavaScript applications: a plain GET returns an
application shell that does not vary by URL (two different 抖音 URLs returned
byte-identical bodies in the HTTP probe), and the content only exists after the
page runs.

Every selector here was measured by `probe_dom.py` and is recorded in
`dom_report.json` - none are guesses. Where a platform needed a session, the
persistent Chrome profile from the login step is reused, so the account that was
signed in by hand is the account these requests come from.

Usage:
    python collect_ugc.py                 # every platform
    python collect_ugc.py weibo_hot xhs   # named ones
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import io
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

ROOT = paths.ROOT
PROFILES = ROOT / ".pwprofiles"
OUT = paths.COLLECT_DIR / "ugc_items.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


@dataclass(frozen=True)
class Platform:
    """One UGC platform, and the DOM facts needed to read it.

    `selectors` are tried in order; the first that yields rows wins. That order
    matters: the specific selector is measured first, the generic one only as a
    fallback, because a generic `a[href*='/']` matches the nav bar (516 matches
    on 虎扑, none of them a post).
    """

    key: str
    name: str
    kind: str                      # 短视频 | 图文 | 问答 | 论坛 | 社交 | 产品 | 消费
    url: str
    selectors: tuple
    profile: str = ""              # persistent Chrome profile holding the session
    wait_ms: int = 6000
    scroll: bool = True
    # how many items are worth keeping per fetch
    limit: int = 40
    # Infinite-scroll depth. `max_scrolls` bounds the walk; `stall_limit` stops
    # early once this many consecutive scrolls add nothing, which is what an
    # exhausted feed looks like.
    max_scrolls: int = 12
    scroll_wait_ms: int = 2200
    stall_limit: int = 2
    # When a card renders no text but its link encodes the subject - 快手 writes
    # /short-video/<title> - the title has to be decoded out of the href. This is
    # a regex with one capture group, applied to the href.
    title_from_href: str = ""


PLATFORMS: List[Platform] = [
    Platform("douyin_hot", "抖音热点榜", "短视频", "https://www.douyin.com/hot",
             ("a[href*='/video/']",), limit=200),
    Platform("kuaishou_hot", "快手热榜", "短视频",
             "https://www.kuaishou.com/brilliant",
             # The card's inner_text is empty for video tiles, but the href
             # carries the title verbatim: /short-video/听总书记讲长征. So the
             # title has to come from the URL, not from the element's text.
             ("a[href*='/short-video/']",),
             title_from_href=r"/short-video/(.+?)(?:\?|$)", limit=200),
    Platform("bili_popular", "B站排行榜", "短视频",
             "https://www.bilibili.com/v/popular/rank/all",
             ("li.rank-item",), limit=100),
    Platform("weibo_hot", "微博热搜", "社交", "https://s.weibo.com/top/summary",
             ("#pl_top_realtimehot table tbody tr", "table tbody tr"), limit=53),
    Platform("douban_explore", "豆瓣讨论精选", "论坛",
             "https://www.douban.com/group/explore",
             ("div[class*='topic'] a", "a[href*='/group/topic/']"), limit=200),
    # My first three guesses here were all navigation (516 anchors, every one a
    # nav item). A DOM walk found the real container: li.bbs-sl-web-post-body,
    # fifty of them, each with an a.p-title.
    Platform("hupu", "虎扑步行街", "论坛", "https://bbs.hupu.com/bxj",
             ("li.bbs-sl-web-post-body", "a.p-title"), limit=200),
    Platform("smzdm", "什么值得买", "消费", "https://www.smzdm.com/",
             ("article", "a[href*='/p/']"), limit=200),
    Platform("producthunt", "Product Hunt", "产品",
             "https://www.producthunt.com/",
             ("div[class*='item'] a", "a[class*='text']"), limit=200),
    Platform("threads", "Threads", "社交", "https://www.threads.net/",
             ("div[data-pressable-container]",), limit=100),
    Platform("xhs_explore", "小红书", "图文",
             "https://www.xiaohongshu.com/explore",
             ("a[href*='/explore/']", "section[class*='note-item']"),
             profile="xhs", limit=200),
    Platform("zhihu_hot", "知乎热榜", "问答", "https://www.zhihu.com/hot",
             ("a[href*='/question/']", "div[class*='HotItem']"),
             profile="zhihu", limit=200),
    Platform("tieba_python", "贴吧 python 吧", "论坛",
             "https://tieba.baidu.com/f?kw=python",
             ("a[href*='/p/']",), profile="tieba", limit=150),
]

# nav-bar and boilerplate text that a generic selector drags in
NOISE = re.compile(
    r"^(首页|我的|登录|注册|下载|客户端|消息|反馈|更多|展开|收起|搜索|关注|推荐|"
    r"手机|加入|导航|全部|热门|排行榜|Sign|Log|Home|More|Menu)$", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _is_item(title: str, url: str) -> bool:
    """Reject nav links and empty labels; keep anything that reads like a post."""
    if not title or len(title) < 6:
        return False
    if NOISE.match(title):
        return False
    if not url or url.rstrip("/") in ("", "https:/"):
        return False
    return True


def extract(page, platform: Platform) -> List[Dict[str, Any]]:
    """Try each selector in order; return the first that yields real items."""
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
            href = ""
            try:
                href = node.get_attribute("href") or ""
                if not href:
                    inner = node.locator("a[href]").first
                    if inner.count():
                        href = inner.get_attribute("href") or ""
            except Exception:
                pass

            try:
                text = _clean(node.inner_text(timeout=2500))
            except Exception:
                text = ""

            # Some tiles carry no text at all; the subject is in the URL.
            if platform.title_from_href and href:
                m = re.search(platform.title_from_href, href)
                if m:
                    decoded = _clean(m.group(1))
                    try:
                        from urllib.parse import unquote
                        decoded = _clean(unquote(decoded))
                    except Exception:
                        pass
                    text = decoded if not text else "%s %s" % (decoded, text)

            if not _is_item(text, href or "x"):
                continue
            title = text[:200]
            if title in seen:
                continue
            seen.add(title)
            rows.append({
                "platform": platform.key,
                "platform_name": platform.name,
                "kind": platform.kind,
                "title": title,
                "url": urljoin(platform.url, href) if href else "",
                "text": text[:1000],
            })
            if len(rows) >= platform.limit:
                break
        if len(rows) >= 3:
            return rows
    return []


def collect_one(p, platform: Platform) -> tuple:
    started = time.monotonic()
    error = ""
    rows: List[Dict[str, Any]] = []
    ctx = None
    closer = None
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
        try:
            page.goto(platform.url, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            error = "goto:%s" % type(exc).__name__
        page.wait_for_timeout(platform.wait_ms)

        # These are infinite-scroll applications: one wheel event loads a few
        # more cards and no more. A single scroll returned 40 items from every
        # platform that had them, which is the size of the first screen, not the
        # size of the feed. So scroll until the extracted count stops growing -
        # the natural stopping point, since it means the feed has either ended or
        # stopped serving new content to this session.
        if platform.scroll:
            best = 0
            stalls = 0
            for _ in range(platform.max_scrolls):
                try:
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(platform.scroll_wait_ms)
                except Exception:
                    break
                got = len(extract(page, platform))
                if got > best:
                    best = got
                    stalls = 0
                else:
                    stalls += 1
                    if stalls >= platform.stall_limit:
                        break

        rows = extract(page, platform)
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
    wanted = sys.argv[1:] or None
    targets = [pf for pf in PLATFORMS if not wanted or pf.key in wanted]
    print("platforms: %d" % len(targets))
    print("=" * 84)

    all_rows: List[Dict[str, Any]] = []
    summary = []
    with sync_playwright() as p:
        for pf in targets:
            rows, secs, err = collect_one(p, pf)
            all_rows.extend(rows)
            summary.append((pf.key, pf.name, pf.kind, len(rows), secs, err))
            print("%-16s %-14s %-6s %4d items  %5.1fs  %s"
                  % (pf.key, pf.name, pf.kind, len(rows), secs, err))

    # A filtered run writes to its own file. Running `collect_ugc.py zhihu_hot`
    # over `ugc_items.json` replaced a 462-row collection with that one
    # platform's 31 rows - the same trap the HTTP collector had. Only an
    # unfiltered run owns the canonical filename.
    out = OUT if not wanted else OUT.with_name(
        "ugc_items.%s.json" % "-".join(sorted(wanted))[:40])
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("=" * 84)
    ok = sum(1 for s in summary if s[3])
    print("platforms with items: %d/%d   items: %d   -> %s"
          % (ok, len(summary), len(all_rows), out.name))
    from collections import Counter
    print("by kind:", dict(Counter(r["kind"] for r in all_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
