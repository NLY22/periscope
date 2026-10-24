"""Collect from platforms by intercepting the API calls the page makes itself.

Why this exists, and what was wrong before: 抖音, 快手 and 小红书 sign their API
requests with values computed by JavaScript (X-Bogus, `__ac_signature`,
`xsec_token`). I treated that as a wall and wrote the platforms off as
"requires a browser" or "unavailable". Both framings were wrong in the same way -
they assume I have to *forge* the request.

I do not. The page already signs every request it makes, correctly, because that
is how it works. So the collector navigates to the search page, listens to the
network, and keeps the JSON the page received. No signature is computed here, no
obfuscated JavaScript is reversed, and the request shapes stay whatever the site
decided they should be this week.

That also makes it robust in a way forging is not: when the signature scheme
changes, this keeps working, because it never knew the scheme.

Usage:
    python collect_ugc_intercept.py douyin kuaishou xhs smzdm
    python collect_ugc_intercept.py --all --limit-queries 3
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
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import quote


from playwright.sync_api import sync_playwright

ROOT = paths.ROOT
PROFILES = ROOT / ".pwprofiles"
OUT = paths.COLLECT_DIR / "ugc_intercept.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

QUERIES = (
    "大模型", "AI agent", "英伟达", "DeepSeek", "开源模型",
    "AI 编程", "算力", "智能体", "AI 创业", "微调",
)


@dataclass(frozen=True)
class Target:
    """One platform's search page, and how to be a convincing visitor."""

    key: str
    name: str
    kind: str
    url_template: str        # {q}
    profile: str = ""
    wait_ms: int = 7000
    max_scrolls: int = 8
    scroll_wait_ms: int = 2500
    # URL fragments that mark a response as "this is the item list", when the
    # generic detector needs help. Empty = detect generically.
    api_hints: tuple = ()
    limit: int = 200
    # Some sites do not run their search when they detect a headless browser.
    # 抖音 measured: headless issued nine JSON requests with no search among
    # them; headed issued the search API with ten records. A visible window is
    # the difference between the two, so it is a per-platform setting rather
    # than a global one.
    headed: bool = False


TARGETS: List[Target] = [
    # 抖音: X-Bogus / a_bogus are computed in the page, and the page also refuses
    # to run its search at all when it detects a headless browser - measured:
    # headless issued nine JSON requests with no search among them and rendered
    # 552 characters of nav and footer; headed mode issued
    # `/aweme/v1/web/general/search/single/` with `data: list[10]`. So this
    # platform needs headless=False, which is why Target carries the flag.
    Target("douyin", "抖音搜索", "短视频",
           "https://www.douyin.com/search/{q}?type=general",
           profile="douyin", wait_ms=15000, max_scrolls=10, headed=True,
           api_hints=("/aweme/v1/web/general/search/",)),
    # 快手: GraphQL with a signed payload, plus a login wall for search results.
    Target("kuaishou", "快手搜索", "短视频",
           "https://www.kuaishou.com/search/video?searchKey={q}",
           wait_ms=12000, max_scrolls=10, headed=True,
           api_hints=("/graphql", "/rest/"),
           ),
    # 小红书: xsec_token is in the URL the page itself builds. Measured endpoint:
    # /api/sns/web/v2/search/notes, 22 records on the first query.
    Target("xhs", "小红书搜索", "图文",
           "https://www.xiaohongshu.com/search_result?keyword={q}&type=51",
           profile="xhs", wait_ms=9000, max_scrolls=10,
           api_hints=("/api/sns/web/v2/search/notes",
                      "/api/sns/web/v1/search/notes")),
    # 什么值得买: the search page fills itself from a list API.
    Target("smzdm", "什么值得买搜索", "消费",
           "https://search.smzdm.com/?c=home&s={q}",
           wait_ms=8000, max_scrolls=8,
           api_hints=("/api/", "/ajax", "search")),
    # 微博: search needs a session.
    Target("weibo", "微博搜索", "社交",
           "https://s.weibo.com/weibo?q={q}",
           profile="weibo", wait_ms=8000, max_scrolls=6,
           api_hints=("/ajax/", "/api/")),
    # B站 search page also uses an API internally; interception is a cross-check
    # on the direct HTTP path that already works.
    Target("bili", "B站搜索", "短视频",
           "https://search.bilibili.com/all?keyword={q}",
           wait_ms=6000, max_scrolls=6,
           api_hints=("/x/web-interface/search/", "/x/web-gateway/")),
]


# Fields that name an item, in rough order of how often they are the title.
TITLE_KEYS = ("title", "desc", "word", "name", "article_title", "content",
              "caption", "text", "note_title", "display_title", "share_info")
URL_KEYS = ("url", "share_url", "jump_url", "link", "target_url", "web_url",
            "note_url", "note_id", "aweme_id", "id")

# What a *result* has and a telemetry blob or an autocomplete list does not.
#
# The first generic version accepted any array of dicts carrying a title-like
# string, and duly harvested `js_error`, `performance`, `resource_error` from
# 抖音's monitoring endpoint, `<em class="suggest_high_light">大模型</em>` from
# B站's autocomplete, and 快手's "添加快捷方式到桌面" UI copy - 167 rows of which
# almost none were posts.
#
# A real result carries an identity a suggestion cannot: an item id, an author,
# engagement counts, or a media object. Requiring at least one of those is what
# separates a feed from a word list.
ITEM_SIGNALS = ("aweme_id", "note_id", "bvid", "aid", "item_id", "vid",
                "author", "nickname", "user_info", "author_info", "user",
                "digg_count", "like_count", "liked_count", "view_count",
                "play_count", "comment_count", "collect_count",
                "note_card", "video", "cover", "images", "image_list")

# A title full of markup is an autocomplete entry, not a post.
MARKUP = re.compile(r"</?[a-z][^>]*>", re.I)

# Bodies that are telemetry rather than content.
TELEMETRY_KEYS = {"js_error", "performance", "resource_error", "resource",
                  "monitor", "track", "metric", "sentry", "timestamp"}


def _is_telemetry(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    keys = {str(k).lower() for k in body.keys()}
    if keys & TELEMETRY_KEYS and "data" not in keys:
        return True
    return bool(body.get("js_error") or body.get("performance"))


def _has_item_signal(item: Dict[str, Any]) -> bool:
    """Does this record look like a post rather than a suggested phrase?"""
    for k in ITEM_SIGNALS:
        if k in item and item[k]:
            return True
    return False


def _first_str(obj: Dict[str, Any], keys) -> str:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and 6 <= len(v) <= 300:
            return v
        # nested one level: share_info.title, statistics.x
        if isinstance(v, dict):
            inner = _first_str(v, ("title", "desc", "name", "url"))
            if inner:
                return inner
    return ""


def _walk_lists(node, depth=0, out=None):
    """Every list of dicts in a JSON body, largest first."""
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(node, list):
        if node and isinstance(node[0], dict):
            out.append(node)
        for item in node[:4]:
            _walk_lists(item, depth + 1, out)
    elif isinstance(node, dict):
        for v in list(node.values())[:40]:
            _walk_lists(v, depth + 1, out)
    return out


def harvest(body: Any, platform: Target) -> List[Dict[str, Any]]:
    """Pull item-shaped records out of one intercepted JSON body.

    The detector is generic about *where* the list is - a platform's payload
    nests its items at a different depth every time, and a per-platform path
    would need editing whenever the site changes. It is strict about *what*
    counts as an item, because being loose is what produced 167 rows of
    telemetry keys and autocomplete phrases.

    Any array of records is accepted only if the records carry an item signal
    (id, author, engagement count, media object) and their title is prose rather
    than markup.
    """
    if _is_telemetry(body):
        return []

    for arr in _walk_lists(body):
        if len(arr) < 2:
            continue
        # Is this a result list or a word list? Decide on the array, not per row,
        # so one odd record cannot let a whole autocomplete response through.
        signal = sum(1 for it in arr[:12]
                     if isinstance(it, dict) and _has_item_signal(it))
        if signal < max(2, len(arr[:12]) // 3):
            continue

        rows: List[Dict[str, Any]] = []
        seen = set()
        for item in arr:
            if not isinstance(item, dict) or not _has_item_signal(item):
                continue
            title = _first_str(item, TITLE_KEYS)
            if not title:
                continue
            title = title.replace("\n", " ").strip()
            if MARKUP.search(title) or len(title) < 8:
                continue          # an autocomplete suggestion, not a post
            title = title[:200]
            if title in seen:
                continue
            seen.add(title)
            url = _first_str(item, URL_KEYS)
            if url and not url.startswith("http"):
                url = ""
            rows.append({"platform": platform.key, "platform_name": platform.name,
                         "kind": platform.kind, "title": title, "url": url,
                         "text": title})
            if len(rows) >= platform.limit:
                return rows
        if len(rows) >= 3:
            return rows
    return []


def collect(p, target: Target, queries) -> tuple:
    started = time.monotonic()
    rows: List[Dict[str, Any]] = []
    seen = set()
    captured = {"responses": 0, "json": 0, "useful": 0}
    error = ""
    ctx = None
    closer = None
    try:
        if target.profile and (PROFILES / target.profile).exists():
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILES / target.profile), channel="chrome",
                headless=not target.headed, user_agent=UA, locale="zh-CN",
                viewport={"width": 1500, "height": 950})
            closer = ctx.close
        else:
            browser = p.chromium.launch(channel="chrome",
                                        headless=not target.headed,
                                        args=["--disable-blink-features=AutomationControlled"])
            ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                      viewport={"width": 1500, "height": 950})
            def closer(b=browser, c=ctx):
                c.close(); b.close()

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(resp):
            nonlocal rows
            captured["responses"] += 1
            url = resp.url
            if target.api_hints and not any(h in url for h in target.api_hints):
                return
            ctype = (resp.headers or {}).get("content-type", "")
            if "json" not in ctype.lower():
                return
            captured["json"] += 1
            try:
                body = resp.json()
            except Exception:
                return
            got = harvest(body, target)
            if not got:
                return
            captured["useful"] += 1
            for row in got:
                if row["title"] in seen:
                    continue
                seen.add(row["title"])
                rows.append(row)

        page.on("response", on_response)

        for query in queries:
            if len(rows) >= target.limit:
                break
            try:
                page.goto(target.url_template.replace("{q}", quote(query)),
                          wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                error = "%s: %s" % (type(exc).__name__, str(exc)[:40])
                continue
            page.wait_for_timeout(target.wait_ms)
            for _ in range(target.max_scrolls):
                if len(rows) >= target.limit:
                    break
                try:
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(target.scroll_wait_ms)
                except Exception:
                    break
    except Exception as exc:
        error = "%s: %s" % (type(exc).__name__, str(exc)[:60])
    finally:
        if closer:
            try:
                closer()
            except Exception:
                pass
    return rows, round(time.monotonic() - started, 1), error, captured


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    limit_q = None
    if "--limit-queries" in sys.argv:
        limit_q = int(sys.argv[sys.argv.index("--limit-queries") + 1])
    if "--all" in sys.argv or not args:
        targets = TARGETS
    else:
        targets = [t for t in TARGETS if t.key in args]
    queries = QUERIES[:limit_q] if limit_q else QUERIES

    print("platforms: %d   queries: %d   mode: response interception"
          % (len(targets), len(queries)))
    print("=" * 92)

    all_rows: List[Dict[str, Any]] = []
    summary = []
    with sync_playwright() as p:
        for target in targets:
            rows, secs, err, cap = collect(p, target, queries)
            all_rows.extend(rows)
            summary.append((target.key, target.name, len(rows), secs, err, cap))
            print("%-11s %-14s %5d items  %6.1fs  seen %d resp (%d json, %d useful)  %s"
                  % (target.key, target.name, len(rows), secs,
                     cap["responses"], cap["json"], cap["useful"], err))

    out = OUT if len(targets) == len(TARGETS) else OUT.with_name(
        "ugc_intercept.%s.json" % "-".join(sorted(t.key for t in targets))[:40])
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("=" * 92)
    ok = sum(1 for s in summary if s[2])
    print("platforms with items: %d/%d   items: %d   -> %s"
          % (ok, len(summary), len(all_rows), out.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
