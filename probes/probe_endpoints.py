"""Which JSON endpoint on a search page actually carries the results?

The interception approach works but was picking the wrong endpoint: 抖音 returned
30 rows of its hot list rather than search results, because the hot-list API
answered first and the generic detector accepted it.

Guessing the path is what produced that. So this reports every JSON response a
search page makes, with its path and how many item-shaped records it holds. The
endpoint with a large count tied to the query is the search endpoint; the one
with a small fixed count is a sidebar.

Run once per platform, then wire the winning path into collect_ugc_intercept.py
as a precise filter.
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import io
import json
from urllib.parse import quote, urlsplit

from src._cli import force_utf8_output

force_utf8_output()

from playwright.sync_api import sync_playwright

ROOT = paths.ROOT
PROFILES = ROOT / ".pwprofiles"
OUT = paths.REPORT_DIR / "endpoint_report.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

QUERY = "大模型"

TARGETS = {
    "douyin": ("https://www.douyin.com/search/%s?type=general" % quote(QUERY), ""),
    "kuaishou": ("https://www.kuaishou.com/search/video?searchKey=%s" % quote(QUERY), ""),
    "xhs": ("https://www.xiaohongshu.com/search_result?keyword=%s&type=51" % quote(QUERY), "xhs"),
    "weibo": ("https://s.weibo.com/weibo?q=%s" % quote(QUERY), ""),
    "smzdm": ("https://search.smzdm.com/?c=home&s=%s" % quote(QUERY), ""),
    "bili": ("https://search.bilibili.com/all?keyword=%s" % quote(QUERY), ""),
    "zhihu": ("https://www.zhihu.com/search?q=%s&type=content" % quote(QUERY), "zhihu"),
    "tieba": ("https://tieba.baidu.com/f/search/res?qw=%s" % quote(QUERY), "tieba"),
}

TITLE_KEYS = ("title", "desc", "word", "name", "article_title", "content",
              "caption", "text", "note_title", "display_title")


def count_records(node, depth=0):
    """Largest array-of-records found in a JSON body."""
    if depth > 8:
        return 0
    best = 0
    if isinstance(node, list):
        if node and isinstance(node[0], dict):
            best = max(best, len(node))
        for item in node[:4]:
            best = max(best, count_records(item, depth + 1))
    elif isinstance(node, dict):
        for v in list(node.values())[:40]:
            best = max(best, count_records(v, depth + 1))
    return best


def has_title(node, depth=0):
    """Does the body contain any record with a title-like string?"""
    if depth > 8:
        return ""
    if isinstance(node, list):
        for item in node[:6]:
            got = has_title(item, depth + 1)
            if got:
                return got
    elif isinstance(node, dict):
        for k in TITLE_KEYS:
            v = node.get(k)
            if isinstance(v, str) and 8 <= len(v) <= 200:
                return v[:60]
        for v in list(node.values())[:40]:
            got = has_title(v, depth + 1)
            if got:
                return got
    return ""


def main() -> int:
    only = sys.argv[1:] or list(TARGETS)
    report = {}
    with sync_playwright() as p:
        for key in only:
            if key not in TARGETS:
                continue
            url, profile = TARGETS[key]
            seen = {}
            ctx = None
            closer = None
            try:
                if profile and (PROFILES / profile).exists():
                    ctx = p.chromium.launch_persistent_context(
                        user_data_dir=str(PROFILES / profile), channel="chrome",
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

                def on_resp(resp, seen=seen):
                    ctype = (resp.headers or {}).get("content-type", "")
                    if "json" not in ctype.lower():
                        return
                    path = urlsplit(resp.url).path
                    if path in seen:
                        return
                    try:
                        body = resp.json()
                    except Exception:
                        return
                    n = count_records(body)
                    if n < 2:
                        return
                    seen[path] = {"count": n, "sample": has_title(body),
                                  "full_url": resp.url[:160]}

                page.on("response", on_resp)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass
                page.wait_for_timeout(9000)
                for _ in range(6):
                    try:
                        page.mouse.wheel(0, 4000)
                        page.wait_for_timeout(2200)
                    except Exception:
                        break
                report[key] = {"url": url, "title": page.title(),
                               "endpoints": seen}
            except Exception as exc:
                report[key] = {"url": url, "fatal": "%s: %s"
                               % (type(exc).__name__, str(exc)[:60])}
            finally:
                if closer:
                    try:
                        closer()
                    except Exception:
                        pass

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 96)
    print("JSON endpoints on each platform's search page")
    print("=" * 96)
    for key, e in report.items():
        print()
        print("[%s]  %s" % (key, e.get("title", "")[:60]))
        if e.get("fatal"):
            print("   FATAL %s" % e["fatal"])
            continue
        eps = sorted(e.get("endpoints", {}).items(),
                     key=lambda kv: -kv[1]["count"])
        for path, info in eps[:10]:
            print("   %-58s %4d  %s"
                  % (path[:58], info["count"], (info["sample"] or "")[:34]))
        if not eps:
            print("   (no JSON endpoint with >=2 records)")
    print()
    print("wrote %s" % OUT.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
