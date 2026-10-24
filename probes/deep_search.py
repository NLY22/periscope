"""Search every platform, for any topic, as deep as each platform allows.

The ask this answers: "each time I run it, deeply search for the information I
want, across platforms - and not just one fixed set of keywords."

So three things had to become general, and previously none were:

  the topic      any query at runtime, from the command line or a file. The old
                 collectors carried a hardcoded CN_TERMS/QUERIES tuple, which
                 meant the corpus could only ever answer questions about those
                 fifteen phrases.
  the depth      scrolling continues while new items keep appearing, then tries
                 the page's own "load more" / next-page controls, and only stops
                 when both are exhausted. Depth here means "until the platform
                 stops giving", not "the first screen".
  the platform   the result list is found by structure (a sibling group whose
                 members each hold a link and a run of text), so no per-site
                 selector is written. platform_search.py holds that detector;
                 this file drives it over every known site and merges the output.

Usage
-----
    python deep_search.py --q "人形机器人"
    python deep_search.py --q "固态电池" --q "氢能"
    python deep_search.py --queries-file topics.txt --platforms douyin xhs bili
    python deep_search.py --q "AI 芯片" --depth 3 --min-items 50

The output is one JSON file per run, plus a printed per-platform report so a
platform that returned nothing is visible rather than silently absent.
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
from urllib.parse import quote, urljoin

from src._cli import force_utf8_output

force_utf8_output()

from playwright.sync_api import sync_playwright

from platform_search import (DISCOVER_JS, EXTRACT_JS, PROFILES, SITES, UA,
                             load_known, save_known)

ROOT = paths.ROOT

# Controls that reveal more results without a URL change. Clicking these is what
# turns one screen into a deep search; the labels are the ones Chinese platforms
# actually use, and a miss costs nothing because scrolling still runs.
LOAD_MORE = (
    "加载更多", "查看更多", "展开更多", "更多结果", "点击加载",
    "Load more", "Show more", "See more", "More results",
)


def _extract(page, discovery, limit):
    """Read results using a learned signature, else discover one."""
    got = []
    if discovery and discovery.get("childSig"):
        try:
            got = page.evaluate(
                EXTRACT_JS, {"parentSig": discovery.get("parentSig"),
                             "childSig": discovery["childSig"], "limit": limit})
        except Exception:
            got = []
    if len(got) >= 3:
        return got, discovery
    try:
        found = page.evaluate(DISCOVER_JS)
    except Exception:
        found = []
    if not found:
        return got, discovery
    best = found[0]
    signature = {"parentSig": best["parentSig"], "childSig": best["childSig"],
                 "count": best["count"], "score": round(best["score"], 1)}
    try:
        got = page.evaluate(
            EXTRACT_JS, {"parentSig": best["parentSig"],
                         "childSig": best["childSig"], "limit": limit})
    except Exception:
        got = []
    return got, signature


def _click_load_more(page) -> bool:
    """Click any visible 'load more' control. Returns whether one was clicked."""
    for label in LOAD_MORE:
        for sel in ("button", "a", "div[role='button']", "span"):
            try:
                loc = page.get_by_text(label, exact=False)
                if loc.count() == 0:
                    continue
                first = loc.first
                if first.is_visible():
                    first.click(timeout=2500)
                    page.wait_for_timeout(2500)
                    return True
            except Exception:
                continue
    return False


def _next_page_url(page, current: str) -> str:
    """A real pagination link on the page, if the platform offers one."""
    try:
        cands = page.evaluate("""() => {
            const out = [];
            for (const a of document.querySelectorAll('a[href]')) {
                const t = (a.innerText || '').trim();
                const rel = (a.getAttribute('rel') || '').toLowerCase();
                const cls = (a.className && typeof a.className === 'string')
                    ? a.className.toLowerCase() : '';
                if (rel.includes('next') || cls.includes('next') ||
                    /^(下一页|下页|next|›|»)/i.test(t)) {
                    out.push(a.getAttribute('href'));
                }
            }
            return out.slice(0, 4);
        }""")
    except Exception:
        return ""
    for href in cands or []:
        if not href or href.startswith("#"):
            continue
        nxt = urljoin(current, href)
        if nxt != current:
            return nxt
    return ""


def deep_search_site(p, key: str, site: dict, queries, *,
                     depth: int = 2, min_items: int = 40,
                     max_items: int = 400) -> tuple:
    """Search one site for every query, going as deep as it will go.

    `depth` is how many extra pagination hops to follow after the scroll
    saturates. Scrolling is unbounded until two consecutive rounds add nothing;
    `depth` only governs following explicit next-page links, because those cost a
    fresh navigation and some platforms rate-limit them.
    """
    started = time.monotonic()
    rows, seen = [], set()
    errors = []
    signature = load_known().get(key)
    ctx = None
    closer = None
    profile = site.get("profile") or ""
    headed = bool(site.get("headed"))
    args = ["--disable-blink-features=AutomationControlled"]
    try:
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
            if len(rows) >= max_items:
                break
            url = site["url"].replace("{q}", quote(query))
            hops = 0
            while url and hops <= depth and len(rows) < max_items:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as exc:
                    errors.append("%s" % type(exc).__name__)
                    break
                page.wait_for_timeout(site.get("wait_ms", 9000))

                # Scroll to saturation: two rounds with no new titles ends it.
                best, stalls = 0, 0
                for _ in range(site.get("max_scrolls", 20)):
                    page.mouse.wheel(0, 3500)
                    page.wait_for_timeout(2000)
                    got, signature = _extract(page, signature, max_items)
                    if len(got) > best:
                        best, stalls = len(got), 0
                    else:
                        stalls += 1
                        if stalls >= 2:
                            break
                    if len(rows) + len(got) >= max_items:
                        break

                # A "load more" control can unlock another batch after the
                # scroll saturates; click it and keep scrolling.
                for _ in range(4):
                    if not _click_load_more(page):
                        break
                    for _ in range(6):
                        page.mouse.wheel(0, 3500)
                        page.wait_for_timeout(1800)
                    got, signature = _extract(page, signature, max_items)
                    if len(got) <= best:
                        break
                    best = len(got)

                got, signature = _extract(page, signature, max_items)
                for item in got or []:
                    title = (item.get("title") or "").strip()
                    if len(title) < 8 or title in seen:
                        continue
                    seen.add(title)
                    href = item.get("url") or ""
                    if href.startswith("/"):
                        href = urljoin(url, href)
                    rows.append({"platform": key,
                                 "platform_name": site.get("name", key),
                                 "kind": site.get("kind", "社区"),
                                 "title": title[:200], "url": href,
                                 "text": title[:1000], "query": query})
                    if len(rows) >= max_items:
                        break

                if len(rows) >= min_items and hops >= depth:
                    break
                nxt = _next_page_url(page, url)
                if not nxt or nxt == url:
                    break
                url = nxt
                hops += 1
    except Exception as exc:
        errors.append("%s: %s" % (type(exc).__name__, str(exc)[:60]))
    finally:
        if closer:
            try:
                closer()
            except Exception:
                pass

    if signature:
        known = load_known()
        known[key] = signature
        save_known(known)
    return rows, round(time.monotonic() - started, 1), "; ".join(errors[:2])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--q", action="append", default=[],
                    help="topic to search (repeatable)")
    ap.add_argument("--queries-file", help="one topic per line")
    ap.add_argument("--platforms", nargs="*", default=[],
                    help="site keys, or 'all' (default)")
    ap.add_argument("--depth", type=int, default=2,
                    help="extra next-page hops per query (default 2)")
    ap.add_argument("--min-items", type=int, default=40,
                    help="stop paginating a query once this many items exist")
    ap.add_argument("--max-items", type=int, default=400,
                    help="hard cap per platform")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    queries = list(args.q)
    if args.queries_file:
        try:
            for line in Path(args.queries_file).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    queries.append(line)
        except Exception as exc:
            print("cannot read %s: %s" % (args.queries_file, exc))
            return 2
    if not queries:
        print("no topic given. Example:")
        print('  python deep_search.py --q "人形机器人"')
        return 2

    wanted = args.platforms or ["all"]
    if "all" in wanted:
        targets = [(k, dict(v, key=k)) for k, v in SITES.items()]
    else:
        targets = []
        for k in wanted:
            if k not in SITES:
                print("unknown platform %r (try --sites via platform_search.py)" % k)
                continue
            targets.append((k, dict(SITES[k], key=k)))

    print("deep search: %d topic(s) x %d platform(s)"
          % (len(queries), len(targets)))
    print("  topics   : %s" % ", ".join(queries[:8]))
    print("  depth    : %d next-page hops, min %d items, cap %d"
          % (args.depth, args.min_items, args.max_items))
    print("=" * 92)

    all_rows = []
    summary = []
    with sync_playwright() as p:
        for key, site in targets:
            rows, secs, err = deep_search_site(
                p, key, site, queries, depth=args.depth,
                min_items=args.min_items, max_items=args.max_items)
            all_rows.extend(rows)
            summary.append((key, site.get("name", ""), len(rows), secs, err))
            per_query = {}
            for r in rows:
                per_query[r["query"]] = per_query.get(r["query"], 0) + 1
            detail = ", ".join("%s:%d" % (q, n) for q, n in per_query.items())
            print("%-13s %-12s %5d items %6.1fs  %s %s"
                  % (key, site.get("name", "")[:12], len(rows), secs,
                     detail[:44], err[:24]))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = paths.REPORT_DIR / (args.out or ("deep_search_%s.json" % stamp))
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("=" * 92)
    ok = sum(1 for s in summary if s[2])
    print("platforms with items: %d/%d   total items: %d"
          % (ok, len(summary), len(all_rows)))
    print("-> %s" % out.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
