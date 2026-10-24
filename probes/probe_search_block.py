"""Why does the search page never call a search endpoint?

抖音's search page loads and titles itself "抖音搜索", yet it issues nine JSON
requests and none of them is a search call. 快手 issues exactly one. Something
either blocks the search request or replaces the result list with a different UI.

Candidates, in order of likelihood:
  - a login wall rendered over the results
  - a captcha / verification interstitial
  - the results arriving as server-rendered HTML rather than JSON
  - the page needing a deliberate interaction before searching

This looks at what is actually on the page, and at whether the DOM already holds
the results, so the next step is chosen from evidence.
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import io
from urllib.parse import quote

from src._cli import force_utf8_output

force_utf8_output()

from playwright.sync_api import sync_playwright

ROOT = paths.ROOT
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

Q = quote("大模型")
TARGETS = {
    "douyin": "https://www.douyin.com/search/%s?type=general" % Q,
    "kuaishou": "https://www.kuaishou.com/search/video?searchKey=%s" % Q,
}


def main() -> int:
    with sync_playwright() as p:
        for key, url in TARGETS.items():
            print("=" * 88)
            print("[%s] %s" % (key, url))
            print("=" * 88)
            browser = p.chromium.launch(channel="chrome", headless=True)
            ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                      viewport={"width": 1500, "height": 950})
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(9000)
                for _ in range(4):
                    page.mouse.wheel(0, 3500)
                    page.wait_for_timeout(2000)

                print("  title: %s" % page.title()[:60])
                print("  url  : %s" % page.url[:80])

                # Is there a login prompt or captcha on screen?
                text = page.evaluate("() => document.body.innerText || ''")[:3000]
                for marker in ("登录", "验证", "扫码", "拖动", "安全", "captcha",
                               "slide", "verify"):
                    if marker in text:
                        print("  on-page marker: %r" % marker)

                # Do the results already exist in the DOM?
                counts = page.evaluate("""() => {
                    const out = {};
                    const sels = [
                      'a[href*="/video/"]', 'a[href*="/note/"]',
                      'a[href*="/short-video/"]', 'div[class*="card"]',
                      'div[class*="item"]', 'li[class*="item"]',
                      'ul li', 'div[data-e2e]', '[data-e2e]'
                    ];
                    for (const s of sels) {
                      try { out[s] = document.querySelectorAll(s).length; }
                      catch (e) { out[s] = -1; }
                    }
                    return out;
                }""")
                for sel, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                    if n > 0:
                        print("    %-30s %d" % (sel, n))

                # Show a slice of visible text so the page state is legible.
                visible = " ".join(text.split())[:400]
                print()
                print("  visible text: %s" % visible)
            except Exception as exc:
                print("  FAILED %s: %s" % (type(exc).__name__, str(exc)[:70]))
            finally:
                try:
                    ctx.close(); browser.close()
                except Exception:
                    pass
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
