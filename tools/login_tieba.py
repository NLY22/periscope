"""Open a tieba login window that saves on close, with no timeout.

The earlier attempt used a 20-minute poll loop, which is worse than useless for
this site: Baidu's login flow includes a puzzle captcha that the human has to
solve, and the loop gave up on a wall clock rather than on the window closing.
It also saved whatever was in the profile at the moment it stopped, so a
half-finished login looked like a finished one.

This waits for the window to close, however long that takes, and reports what
the profile actually contains. The only signal that matters is BDUSS - Baidu's
session cookie - which is absent when logged out and present when logged in.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paths  # noqa: E402
from core.console import force_utf8_output  # noqa: E402

force_utf8_output()

from playwright.sync_api import sync_playwright  # noqa: E402

PROFILE = paths.PROFILES / "tieba"
COOKIE_OUT = paths.COOKIES / "tieba.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def main() -> int:
    PROFILE.mkdir(parents=True, exist_ok=True)
    COOKIE_OUT.parent.mkdir(parents=True, exist_ok=True)

    lock = PROFILE / "lockfile"
    if lock.exists():
        try:
            lock.unlink()
            print("已清理上次残留的 lockfile")
        except Exception:
            pass

    print("=" * 74)
    print("  百度贴吧 登录")
    print("=" * 74)
    print("  浏览器即将打开，地址是贴吧首页。")
    print()
    print("  1. 点右上角「登录」，扫码或账号密码登录")
    print("  2. 如果弹出【拼图验证】，手动滑一下")
    print("  3. 看到自己的头像 / 用户名 之后，【关闭浏览器窗口】")
    print()
    print("  ★ 没有超时。窗口不关，脚本就一直等。")
    print()

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), channel="chrome", headless=False,
            user_agent=UA, locale="zh-CN",
            viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://tieba.baidu.com/", wait_until="domcontentloaded",
                      timeout=60000)
        except Exception as exc:
            print("(首页加载慢: %s —— 仍可手动点登录)" % type(exc).__name__)

        print("  等待你关闭窗口…")
        try:
            # Wait for the context to die. `pages` empties when the last tab
            # closes, which is the reliable signal here (the page object often
            # dies first, so waiting on the page would hang forever).
            while True:
                try:
                    if not ctx.pages:
                        break
                except Exception:
                    break
                page.wait_for_timeout(2000)
        except Exception:
            pass

        cookies = []
        state = None
        try:
            cookies = [c for c in ctx.cookies()
                       if "baidu.com" in (c.get("domain") or "")]
            if cookies:
                state = ctx.storage_state()
        except Exception as exc:
            print("  读取会话失败: %s" % exc)
        try:
            ctx.close()
        except Exception:
            pass

    names = [c["name"] for c in cookies]
    print()
    print("=" * 74)
    print("  结果")
    print("=" * 74)
    print("  baidu.com 域 cookie: %d 个" % len(cookies))
    for c in cookies[:14]:
        print("     %-24s len=%d" % (c["name"][:24], len(c.get("value") or "")))

    has_bduss = any(n.upper() == "BDUSS" for n in names)
    print()
    if has_bduss and state is not None:
        COOKIE_OUT.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        print("  ✅ 有 BDUSS —— 登录成功，已保存 -> data/cookies/tieba.json")
        return 0
    print("  ❌ 没有 BDUSS —— 未登录成功")
    print("     找到的 cookie: %s" % (names[:12] or "无"))
    print("     （说明：BDUSS 是百度会话票据，未登录时不存在）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
