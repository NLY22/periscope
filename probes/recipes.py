"""A platform search recipe: record once, replay forever.

The problem this replaces
-------------------------
Every platform so far needed me to discover its internals: the API path, the
signature scheme, the result container. That knowledge went into Python source,
one hand-tuned entry per platform, spread across four collectors and 59 places.
When 抖音 changed its endpoint or refused a headless client, the fix was an edit
and another probe round. That is not a maintenance cost, it is a treadmill.

What a recipe is
----------------
A small JSON file describing *what a person does*, not what the site does
internally:

    {
      "key": "douyin",
      "search_url": "https://www.douyin.com/search/{q}",
      "results": ["div[data-e2e='scroll-list'] li", "..."],
      "title_from": "innerText",
      "profile": "douyin",
      "headed": true
    }

No API path. No signature. No request shape. The browser performs the same
navigation and the same scrolling a visitor would, and the recipe only says where
to read the result list from.

Why that survives change
------------------------
Signing, endpoints and anti-bot heuristics can be rewritten freely; a recipe that
never mentioned them keeps working. When the *page layout* changes, the recipe
needs one new selector - and `learn` can find it again by recording what a real
search looks like, instead of me reading a spec.

Commands
--------
    python recipes.py --list                 # what is recorded
    python recipes.py --learn douyin         # open a window, record a search
    python recipes.py --verify douyin        # replay and report item counts
    python recipes.py --verify --all
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import argparse
import io
import json
import time

from src._cli import force_utf8_output

force_utf8_output()

from playwright.sync_api import sync_playwright

ROOT = paths.ROOT
PROFILES = ROOT / ".pwprofiles"
RECIPES = ROOT / "recipes"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Seeds, so a platform can be replayed before it has been re-learned. Each is the
# minimum: where to search, and what a result looks like. Everything else is
# discovered on replay.
SEEDS = {
    "douyin": {
        "name": "抖音", "kind": "短视频",
        "search_url": "https://www.douyin.com/search/{q}",
        "profile": "douyin", "headed": True,
        "results": ["li[data-e2e='scroll-list-item']",
                    "div[data-e2e='scroll-list'] li",
                    "ul[data-e2e='scroll-list'] li",
                    "a[href*='/video/']"],
        "title_from": "innerText",
    },
    "kuaishou": {
        "name": "快手", "kind": "短视频",
        "search_url": "https://www.kuaishou.com/search/video?searchKey={q}",
        "headed": True,
        "results": ["div[class*='search-result'] div[class*='item']",
                    "a[href*='/short-video/']"],
        "title_from": "innerText", "title_from_href": r"/short-video/(.+?)(?:\?|$)",
    },
    "xhs": {
        "name": "小红书", "kind": "图文",
        "search_url": "https://www.xiaohongshu.com/search_result?keyword={q}&type=51",
        "profile": "xhs",
        "results": ["section[class*='note-item']", "a[href*='/explore/']"],
        "title_from": "innerText",
    },
    "weibo": {
        "name": "微博", "kind": "社交",
        "search_url": "https://s.weibo.com/weibo?q={q}",
        "profile": "weibo",
        "results": ["div[action-type='feed_list_item']", "p.txt", "div.card-wrap"],
        "title_from": "innerText",
    },
    "smzdm": {
        "name": "什么值得买", "kind": "消费",
        "search_url": "https://search.smzdm.com/?c=home&s={q}",
        "results": ["article", "div[class*='feed'] article", "li[class*='feed']"],
        "title_from": "innerText",
    },
    "zhihu": {
        "name": "知乎", "kind": "问答",
        "search_url": "https://www.zhihu.com/search?q={q}&type=content",
        "profile": "zhihu",
        "results": ["div[class*='SearchResult'] a[href*='/question/']",
                    "a[href*='/question/']"],
        "title_from": "innerText",
    },
    "bili": {
        "name": "B站", "kind": "短视频",
        "search_url": "https://search.bilibili.com/all?keyword={q}",
        "results": ["div[class*='video-list'] a", "a[href*='/video/BV']"],
        "title_from": "innerText",
    },
    "tieba": {
        "name": "贴吧", "kind": "论坛",
        "search_url": "https://tieba.baidu.com/f/search/res?qw={q}",
        "profile": "tieba",
        "results": ["a[href*='/p/']"],
        "title_from": "attribute:title", "title_from_parent": True,
    },
    "hupu": {
        "name": "虎扑", "kind": "论坛",
        "search_url": "https://bbs.hupu.com/search?q={q}",
        "results": ["a[href*='bbs.hupu.com/']"],
        "title_from": "innerText",
    },
    "juejin": {
        "name": "掘金", "kind": "技术社区",
        "search_url": "https://juejin.cn/search?query={q}",
        "results": ["div[class*='entry'] a", "a[href*='/post/']"],
        "title_from": "innerText",
    },
    "csdn": {
        "name": "CSDN", "kind": "技术社区",
        "search_url": "https://so.csdn.net/so/search?q={q}&t=blog",
        "results": ["div[class*='search-list'] a", "a[href*='blog.csdn.net']"],
        "title_from": "innerText",
    },
    "douban": {
        "name": "豆瓣", "kind": "兴趣社区",
        "search_url": "https://www.douban.com/search?q={q}",
        "results": ["div.result", "div[class*='result'] a"],
        "title_from": "innerText",
    },
}


def recipe_path(key: str) -> Path:
    return RECIPES / ("%s.json" % key)


def load_recipe(key: str) -> dict:
    """Recorded recipe if present, else the seed, merged so a learned file wins."""
    base = dict(SEEDS.get(key, {}))
    base.setdefault("key", key)
    path = recipe_path(key)
    if path.exists():
        try:
            learned = json.loads(path.read_text(encoding="utf-8"))
            base.update(learned)
            base["_learned"] = True
        except Exception:
            pass
    return base


def save_recipe(key: str, data: dict) -> None:
    RECIPES.mkdir(parents=True, exist_ok=True)
    recipe_path(key).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract(page, recipe: dict, limit: int = 200):
    """Read the result list using the recipe's selectors, first match wins."""
    for sel in recipe.get("results", []):
        rows = []
        seen = set()
        try:
            loc = page.locator(sel)
            n = min(loc.count(), limit * 3)
        except Exception:
            continue
        if n == 0:
            continue
        for i in range(n):
            node = loc.nth(i)
            text = ""
            href = ""
            how = recipe.get("title_from", "innerText")
            try:
                if how.startswith("attribute:"):
                    text = (node.get_attribute(how.split(":", 1)[1]) or "").strip()
                else:
                    text = " ".join(node.inner_text(timeout=2000).split())
            except Exception:
                text = ""
            try:
                href = node.get_attribute("href") or ""
                if not href:
                    inner = node.locator("a[href]").first
                    if inner.count():
                        href = inner.get_attribute("href") or ""
            except Exception:
                pass
            # Some rows carry the headline only on the parent element.
            if not text and recipe.get("title_from_parent"):
                try:
                    text = " ".join(
                        node.locator("xpath=..").inner_text(timeout=1500).split())
                except Exception:
                    text = ""
            # And some encode it in the URL.
            pattern = recipe.get("title_from_href")
            if not text and pattern and href:
                import re as _re
                m = _re.search(pattern, href)
                if m:
                    text = m.group(1)
            text = text.strip()[:200]
            if len(text) < 6 or text in seen:
                continue
            seen.add(text)
            rows.append({"title": text, "url": href})
            if len(rows) >= limit:
                break
        if len(rows) >= 3:
            return rows
    return []


def run_recipe(p, recipe: dict, queries, limit=200) -> tuple:
    """Replay a recipe: navigate, scroll, read. No API knowledge involved."""
    started = time.monotonic()
    rows, seen = [], set()
    error = ""
    ctx = None
    closer = None
    profile = recipe.get("profile") or ""
    try:
        headed = bool(recipe.get("headed"))
        args = ["--disable-blink-features=AutomationControlled"]
        if profile and (PROFILES / profile).exists():
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILES / profile), channel="chrome",
                headless=not headed, user_agent=UA, locale="zh-CN",
                viewport={"width": 1440, "height": 900}, args=args)
            closer = ctx.close
        else:
            browser = p.chromium.launch(channel="chrome", headless=not headed,
                                        args=args)
            ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                      viewport={"width": 1440, "height": 900})
            def closer(b=browser, c=ctx):
                c.close(); b.close()

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for query in queries:
            if len(rows) >= limit:
                break
            url = recipe["search_url"].replace("{q}", query)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                error = "%s: %s" % (type(exc).__name__, str(exc)[:40])
                continue
            page.wait_for_timeout(recipe.get("wait_ms", 8000))
            best, stalls = 0, 0
            for _ in range(recipe.get("max_scrolls", 8)):
                try:
                    page.mouse.wheel(0, 3000)
                    page.wait_for_timeout(2200)
                except Exception:
                    break
                got = len(extract(page, recipe, limit))
                if got > best:
                    best, stalls = got, 0
                else:
                    stalls += 1
                    if stalls >= 2:
                        break
            for row in extract(page, recipe, limit):
                if row["title"] in seen:
                    continue
                seen.add(row["title"])
                rows.append({"platform": recipe["key"],
                             "platform_name": recipe.get("name", recipe["key"]),
                             "kind": recipe.get("kind", "社区"),
                             "title": row["title"], "url": row["url"],
                             "text": row["title"], "query": query})
    except Exception as exc:
        error = "%s: %s" % (type(exc).__name__, str(exc)[:60])
    finally:
        if closer:
            try:
                closer()
            except Exception:
                pass
    return rows, round(time.monotonic() - started, 1), error


def learn(key: str) -> int:
    """Open a visible window and record which selectors hold the results.

    Records by observation rather than by asking: the page is loaded, then every
    candidate selector in the seed is scored by how many *item-like* nodes it
    matches. The winner is written to the recipe file, so the next replay needs
    no manual step.
    """
    recipe = load_recipe(key)
    if not recipe.get("search_url"):
        print("no seed for %r" % key)
        return 1
    query = "大模型"
    url = recipe["search_url"].replace("{q}", query)
    print("=" * 76)
    print("learning %s" % key)
    print("=" * 76)
    print("  url: %s" % url)
    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled"]
        profile = recipe.get("profile") or ""
        if profile and (PROFILES / profile).exists():
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILES / profile), channel="chrome",
                headless=False, user_agent=UA, locale="zh-CN",
                viewport={"width": 1440, "height": 900}, args=args)
        else:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILES / ("_learn_" + key)), channel="chrome",
                headless=False, user_agent=UA, locale="zh-CN",
                viewport={"width": 1440, "height": 900}, args=args)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        page.wait_for_timeout(12000)
        for _ in range(4):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(2000)

        scored = []
        for sel in recipe.get("results", []):
            try:
                n = page.locator(sel).count()
            except Exception:
                n = 0
            sample = ""
            if n:
                try:
                    sample = " ".join(
                        page.locator(sel).first.inner_text(timeout=2000).split())[:60]
                except Exception:
                    pass
            scored.append((n, sel, sample))

        scored.sort(key=lambda x: -x[0])
        print()
        for n, sel, sample in scored:
            mark = "OK " if n >= 5 else "   "
            print("  %s%-46s %4d  %s" % (mark, sel[:46], n, sample))
        best = next((s for s in scored if s[0] >= 5), None)
        if not best:
            print()
            print("  no selector matched >=5 items. The page may need a login, or")
            print("  the seed selectors are stale. Recipe left unchanged.")
            try:
                ctx.close()
            except Exception:
                pass
            return 1

        # Put the winning selector first so replay uses it immediately.
        n, sel, _ = best
        order = [sel] + [s for s in recipe.get("results", []) if s != sel]
        recipe["results"] = order
        recipe["learned_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        recipe["learned_count"] = n
        save_recipe(key, recipe)
        print()
        print("  recorded: %s  (%d items)" % (sel, n))
        try:
            ctx.close()
        except Exception:
            pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--learn", nargs="*", default=[])
    ap.add_argument("--verify", nargs="*", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--queries", type=int, default=2)
    ap.add_argument("--out", default="ugc_recipe.json")
    args = ap.parse_args()

    if args.list:
        print("%-12s %-8s %-6s %s" % ("key", "learned", "headed", "first selector"))
        for key in SEEDS:
            r = load_recipe(key)
            print("%-12s %-8s %-6s %s"
                  % (key, "yes" if r.get("_learned") else "seed",
                     r.get("headed", False),
                     (r.get("results") or [""])[0][:44]))
        return 0

    if args.learn:
        rc = 0
        for key in args.learn:
            rc |= learn(key)
        return rc

    keys = list(SEEDS) if (args.all or not args.verify) else args.verify
    queries = ("大模型", "AI agent", "英伟达")[:args.queries]
    print("replaying %d recipes, %d queries each" % (len(keys), len(queries)))
    print("=" * 84)
    all_rows = []
    with sync_playwright() as p:
        for key in keys:
            recipe = load_recipe(key)
            rows, secs, err = run_recipe(p, recipe, queries)
            all_rows.extend(rows)
            print("%-11s %-12s %5d items  %6.1fs  %s"
                  % (key, recipe.get("name", "")[:12], len(rows), secs, err))
    out = paths.COLLECT_DIR / args.out
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("=" * 84)
    ok = sum(1 for r in all_rows)
    print("items: %d -> %s" % (ok, out.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
