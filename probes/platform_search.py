"""Search any platform for any keyword, without knowing the platform.

The ask this answers: not "collect from 抖音" but "be able to search arbitrary
content on arbitrary platforms, from now on". Every approach before this one
failed that test in the same way - it encoded per-site knowledge (an API path, a
signature trick, a CSS selector) in Python, so each new platform or layout change
cost another round of probing.

How this works instead
----------------------
A search results page has one property that does not depend on which company
built it: **the results are a group of structurally identical siblings, and each
one contains a link and a run of text.** A nav bar is not a sibling group. A
footer is not. A sidebar of adverts has no links with article-length text. So the
result list can be *found* by measurement rather than by being told.

The detector, run inside the page:

  for every element with >= 5 children
    group the children by (tag, first two classes)
    keep a group where >= 60% of members contain a link
      and >= 60% contain 8-300 characters of text
    score it: members x link-ratio x text-ratio

The highest-scoring group is the result list. No selector is written by hand, and
no platform is named anywhere in this file.

What is remembered
------------------
The *structural signature* of the winning group (parent tag+class, child
tag+class), so a repeat run can go straight to it. If that signature later stops
matching - because the site was redesigned - the detector runs again and the
signature is rewritten. That is the self-healing part: a layout change costs one
rediscovery, not a code edit.

Usage
-----
    python platform_search.py --url "https://www.douyin.com/search/{q}" --q 大模型
    python platform_search.py --site douyin --q 大模型 --q 英伟达
    python platform_search.py --learn douyin          # record a new site
    python platform_search.py --sites                 # what is known
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
from urllib.parse import quote

from src._cli import force_utf8_output

force_utf8_output()

from playwright.sync_api import sync_playwright

ROOT = paths.ROOT
PROFILES = ROOT / ".pwprofiles"
KNOWN = paths.REPORT_DIR / "search_sites.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Finding the result list needs no site knowledge, but reaching the page does:
# where the search box lives, and whether the site runs its search at all when it
# thinks the browser is automated. Those two facts are all a "site" is now.
SITES = {
    "douyin": {"name": "抖音", "kind": "短视频",
               "url": "https://www.douyin.com/search/{q}",
               "profile": "douyin", "headed": True},
    "kuaishou": {"name": "快手", "kind": "短视频",
                 "url": "https://www.kuaishou.com/search/video?searchKey={q}",
                 "headed": True},
    "xhs": {"name": "小红书", "kind": "图文",
            "url": "https://www.xiaohongshu.com/search_result?keyword={q}&type=51",
            "profile": "xhs"},
    "weibo": {"name": "微博", "kind": "社交",
              "url": "https://s.weibo.com/weibo?q={q}", "profile": "weibo"},
    "bili": {"name": "B站", "kind": "短视频",
             "url": "https://search.bilibili.com/all?keyword={q}"},
    "zhihu": {"name": "知乎", "kind": "问答",
              "url": "https://www.zhihu.com/search?q={q}&type=content",
              "profile": "zhihu"},
    "tieba": {"name": "贴吧", "kind": "论坛",
              "url": "https://tieba.baidu.com/f/search/res?qw={q}",
              "profile": "tieba"},
    "hupu": {"name": "虎扑", "kind": "论坛",
             "url": "https://bbs.hupu.com/search?q={q}"},
    "juejin": {"name": "掘金", "kind": "技术社区",
               "url": "https://juejin.cn/search?query={q}"},
    "csdn": {"name": "CSDN", "kind": "技术社区",
             "url": "https://so.csdn.net/so/search?q={q}&t=blog"},
    "smzdm": {"name": "什么值得买", "kind": "消费",
              "url": "https://search.smzdm.com/?c=home&s={q}"},
    "douban": {"name": "豆瓣", "kind": "兴趣社区",
               "url": "https://www.douban.com/search?q={q}"},
    "producthunt": {"name": "Product Hunt", "kind": "产品",
                    "url": "https://www.producthunt.com/search?q={q}"},
}

# The structural detector. Written as one JS function so the grouping happens
# where the DOM is, rather than shipping thousands of nodes to Python.
DISCOVER_JS = r"""
() => {
  const TEXT_MIN = 8, TEXT_MAX = 300;
  const MIN_GROUP = 5;

  const sig = (el) => {
    let c = '';
    if (el.className && typeof el.className === 'string') {
      c = el.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
    }
    return el.tagName.toLowerCase() + (c ? '.' + c : '');
  };
  const len = (el) => (el.innerText || '').trim().length;

  const groups = [];
  for (const parent of document.querySelectorAll('*')) {
    const kids = parent.children;
    if (!kids || kids.length < MIN_GROUP) continue;
    const bySig = new Map();
    for (const k of kids) {
      const s = sig(k);
      if (!bySig.has(s)) bySig.set(s, []);
      bySig.get(s).push(k);
    }
    for (const [s, members] of bySig) {
      const n = members.length;
      if (n < MIN_GROUP) continue;
      let link = 0, text = 0;
      for (const m of members) {
        if (m.querySelector('a[href]')) link++;
        const t = len(m);
        if (t >= TEXT_MIN && t <= TEXT_MAX) text++;
      }
      const linkR = link / n, textR = text / n;
      if (linkR < 0.6 || textR < 0.6) continue;
      // Prefer bigger groups, complete ones, and a parent that is itself not
      // the whole body (a single wrapper containing everything scores badly).
      const score = n * linkR * textR;
      groups.push({
        parentSig: sig(parent),
        childSig: s,
        count: n,
        linkRatio: linkR,
        textRatio: textR,
        score: score
      });
    }
  }
  groups.sort((a, b) => b.score - a.score);
  // De-duplicate: keep the largest group per child signature.
  const seen = new Set(), out = [];
  for (const g of groups) {
    if (seen.has(g.childSig)) continue;
    seen.add(g.childSig);
    out.push(g);
    if (out.length >= 6) break;
  }
  return out;
}
"""

# Extract using a discovered signature, and fall back to "any sibling that has a
# link and text" if the signature no longer matches.
EXTRACT_JS = r"""
(cfg) => {
  const {parentSig, childSig, limit} = cfg;
  const sig = (el) => {
    let c = '';
    if (el.className && typeof el.className === 'string') {
      c = el.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
    }
    return el.tagName.toLowerCase() + (c ? '.' + c : '');
  };

  let members = [];
  if (parentSig && childSig) {
    for (const parent of document.querySelectorAll('*')) {
      if (sig(parent) !== parentSig) continue;
      const hit = Array.from(parent.children).filter(k => sig(k) === childSig);
      if (hit.length >= 5) { members = hit; break; }
    }
  }

  const out = [];
  const seen = new Set();
  for (const m of members) {
    let title = (m.innerText || '').trim().replace(/\s+/g, ' ');
    let href = '';
    const a = m.querySelector('a[href]');
    if (a) href = a.getAttribute('href') || '';
    if (!href) {
      const self = m.getAttribute && m.getAttribute('href');
      if (self) href = self;
    }
    // Some sites put the headline on `title=`, or only in the URL.
    if (title.length < 8) {
      for (const attr of ['title', 'aria-label', 'data-title']) {
        const v = m.getAttribute && m.getAttribute(attr);
        if (v && v.trim().length >= 8) { title = v.trim(); break; }
      }
    }
    if (title.length < 8 && href) {
      try {
        const u = decodeURIComponent(href);
        const m2 = u.match(/(?:video|note|post|question|p)\/([^/?#]{6,})/);
        if (m2) title = m2[1];
      } catch (e) {}
    }
    if (!title || title.length > 300) continue;
    if (seen.has(title)) continue;
    seen.add(title);
    out.push({title: title, url: href});
    if (out.length >= limit) break;
  }
  return out;
}
"""


def load_known() -> dict:
    if KNOWN.exists():
        try:
            return json.loads(KNOWN.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_known(data: dict) -> None:
    KNOWN.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def search(p, site: dict, queries, limit: int = 200,
           discovery: dict | None = None) -> tuple:
    """Search one site for each query and return the items found.

    `discovery` carries a previously learned structural signature. When it is
    given and still matches, extraction is direct; when it does not match, the
    detector runs and the new signature is returned so the caller can save it.
    """
    started = time.monotonic()
    rows, seen = [], set()
    rediscovered = None
    error = ""
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
            if len(rows) >= limit:
                break
            url = site["url"].replace("{q}", quote(query))
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                error = "%s: %s" % (type(exc).__name__, str(exc)[:40])
                continue
            page.wait_for_timeout(site.get("wait_ms", 9000))
            for _ in range(site.get("max_scrolls", 8)):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(2000)

            got = []
            if discovery and discovery.get("childSig"):
                try:
                    got = page.evaluate(
                        EXTRACT_JS, {"parentSig": discovery.get("parentSig"),
                                     "childSig": discovery["childSig"],
                                     "limit": limit})
                except Exception:
                    got = []
            if len(got) < 3:
                # Signature stale, or never learned: find the list again.
                try:
                    found = page.evaluate(DISCOVER_JS)
                except Exception:
                    found = []
                if found:
                    best = found[0]
                    rediscovered = {"parentSig": best["parentSig"],
                                    "childSig": best["childSig"],
                                    "count": best["count"],
                                    "score": round(best["score"], 1)}
                    discovery = rediscovered
                    try:
                        got = page.evaluate(
                            EXTRACT_JS,
                            {"parentSig": best["parentSig"],
                             "childSig": best["childSig"], "limit": limit})
                    except Exception:
                        got = []

            for item in got or []:
                title = (item.get("title") or "").strip()
                if len(title) < 8 or title in seen:
                    continue
                seen.add(title)
                href = item.get("url") or ""
                if href.startswith("/"):
                    from urllib.parse import urljoin
                    href = urljoin(url, href)
                rows.append({"platform": site.get("key", ""),
                             "platform_name": site.get("name", ""),
                             "kind": site.get("kind", "社区"),
                             "title": title[:200], "url": href,
                             "text": title[:1000], "query": query})
    except Exception as exc:
        error = "%s: %s" % (type(exc).__name__, str(exc)[:60])
    finally:
        if closer:
            try:
                closer()
            except Exception:
                pass
    return rows, round(time.monotonic() - started, 1), error, rediscovered


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", help="search URL template containing {q}")
    ap.add_argument("--site", help="a known site key")
    ap.add_argument("--q", action="append", default=[], help="query (repeatable)")
    ap.add_argument("--profile", default="")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--sites", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--out", default="ugc_generic.json")
    args = ap.parse_args()

    known = load_known()

    if args.sites:
        print("%-14s %-10s %-6s %s" % ("key", "name", "learned", "signature"))
        for key, site in SITES.items():
            d = known.get(key, {})
            print("%-14s %-10s %-6s %s"
                  % (key, site["name"][:10], "yes" if d.get("childSig") else "no",
                     (d.get("childSig") or "-")[:44]))
        return 0

    queries = args.q or ["大模型"]

    if args.url:
        site = {"key": "custom", "name": "custom", "kind": "社区",
                "url": args.url, "profile": args.profile,
                "headed": args.headed}
        targets = [("custom", site)]
    elif args.all:
        targets = [(k, dict(v, key=k)) for k, v in SITES.items()]
    elif args.site:
        if args.site not in SITES:
            print("unknown site %r; try --sites" % args.site)
            return 2
        targets = [(args.site, dict(SITES[args.site], key=args.site))]
    else:
        ap.print_help()
        return 2

    print("generic platform search: %d site(s), %d queries"
          % (len(targets), len(queries)))
    print("=" * 88)
    all_rows = []
    summary = []
    with sync_playwright() as p:
        for key, site in targets:
            rows, secs, err, re_disc = search(
                p, site, queries, args.limit, known.get(key))
            all_rows.extend(rows)
            summary.append((key, site.get("name", ""), len(rows), secs, err, re_disc))
            flag = " (re-learned)" if re_disc else ""
            print("%-13s %-12s %5d items %6.1fs  %s%s"
                  % (key, site.get("name", "")[:12], len(rows), secs, err, flag))
            if re_disc:
                known[key] = re_disc

    if known:
        save_known(known)
    out = paths.COLLECT_DIR / args.out
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("=" * 88)
    print("items: %d -> %s" % (len(all_rows), out.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
