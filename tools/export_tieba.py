"""Export the tieba session that is already sitting in its profile.

`login_tieba.py` died on its last print statement - a UnicodeEncodeError from an
emoji, on a console using the GBK code page - which happened *after* the login
succeeded but *before* the cookie file was written. So the session is on disk and
only the export step is missing.

Re-exporting is cheap and does not need the human: open the profile headless,
read the cookies, write them out. The check is the same one the login window
used - BDUSS present means signed in - because BDUSS is Baidu's session ticket
and does not exist for a logged-out browser.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Root on sys.path before anything from this project is imported: launched as
# `python tools/export_tieba.py`, sys.path[0] is tools/, not the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paths  # noqa: E402
from core.console import force_utf8_output  # noqa: E402

# The crash in login_tieba.py was an encoding failure, so make the console safe
# before printing anything, not after.
force_utf8_output()

from playwright.sync_api import sync_playwright  # noqa: E402

PROFILE = paths.PROFILES / "tieba"
OUT = paths.COOKIES / "tieba.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def main() -> int:
    if not PROFILE.exists():
        print("[tieba] no profile")
        return 1

    lock = PROFILE / "lockfile"
    if lock.exists():
        try:
            lock.unlink()
            print("cleared stale lockfile")
        except Exception:
            pass

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), channel="chrome", headless=True,
            user_agent=UA, locale="zh-CN")
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto("https://tieba.baidu.com/",
                          wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3500)
            except Exception as exc:
                print("page load: %s" % type(exc).__name__)

            cookies = [c for c in ctx.cookies()
                       if "baidu.com" in (c.get("domain") or "")]
            names = [c["name"] for c in cookies]
            has_bduss = any(n.upper() == "BDUSS" for n in names)

            print("baidu.com cookies: %d" % len(cookies))
            for c in cookies:
                if c["name"].upper() in ("BDUSS", "STOKEN", "PTOKEN", "TIEBA_SID"):
                    print("   %-12s len=%d" % (c["name"], len(c.get("value") or "")))

            if not has_bduss:
                print("RESULT: no BDUSS - not signed in")
                return 1

            state = ctx.storage_state()
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            print("RESULT: signed in, %d cookies saved -> %s"
                  % (len(cookies), OUT.name))

            # prove it is really a session, not just a cookie: load a page that
            # renders account UI only for a signed-in visitor
            try:
                page.goto("https://tieba.baidu.com/index.html",
                          wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
                body = page.locator("body").inner_text(timeout=8000)
                signed = [m for m in ("个人中心", "我的贴吧", "退出", "我的收藏")
                          if m in body]
                logged_out = [m for m in ("登录探索更多内容", "立即登录", "百度安全验证")
                              if m in body]
                print("signed-in markers : %s" % (signed or "none"))
                print("logged-out markers: %s" % (logged_out or "none"))
                print("sample: %s" % " ".join(body.split())[:180])
            except Exception as exc:
                print("verify load failed: %s" % type(exc).__name__)
            return 0
        finally:
            ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
