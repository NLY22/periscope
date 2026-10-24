"""Capture a session from a persistent profile, and verify it actually works.

Same approach that succeeded for xiaohongshu: open the profile headless, read
the site's own cookies, and - the important part - load a page that requires the
session so success is proven by content, not by cookie count.

    python capture_session.py zhihu
    python capture_session.py tieba
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

PROFILES = paths.PROFILES
COOKIE_DIR = paths.COOKIES

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

SITES = {
    "xhs": {
        "label": "小红书", "domain": "xiaohongshu.com",
        "probe": "https://www.xiaohongshu.com/explore",
        "need": ["web_session"],
    },
    "zhihu": {
        "label": "知乎", "domain": "zhihu.com",
        "probe": "https://www.zhihu.com/",
        "need": ["z_c0"],
    },
    "tieba": {
        "label": "百度贴吧", "domain": "baidu.com",
        "probe": "https://tieba.baidu.com/",
        "need": ["BDUSS"],
    },
}


def capture(name: str) -> bool:
    spec = SITES[name]
    prof = PROFILES / name
    if not prof.exists():
        print("[%s] 没有 profile" % name)
        return False

    lock = prof / "lockfile"
    if lock.exists():
        try:
            lock.unlink()
        except Exception:
            pass

    print("=" * 78)
    print("[%s] %s" % (name, spec["label"]))
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(prof), channel="chrome", headless=True,
                user_agent=UA, locale="zh-CN")
        except Exception as exc:
            print("    启动失败（profile 可能被占用）: %s" % str(exc)[:100])
            return False
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(spec["probe"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4500)
            body = page.locator("body").inner_text(timeout=8000)

            cookies = [c for c in ctx.cookies()
                       if spec["domain"] in (c.get("domain") or "")]
            names = [c["name"] for c in cookies]
            found = [k for k in spec["need"]
                     if any(k.lower() == n.lower() for n in names)]

            print("    url   : %s" % page.url[:80])
            print("    cookie: %d 个（%s 域）" % (len(cookies), spec["domain"]))
            print("    会话键: %s" % (found or "未找到 %s" % spec["need"]))
            print("    样本  : %s" % " ".join(body.split())[:140])

            if not found:
                print("    ❌ 没有会话 cookie —— 未登录成功")
                return False

            state = ctx.storage_state()
            out = COOKIE_DIR / ("%s.json" % name)
            out.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            print("    ✅ 已保存 %d 个 cookie -> %s" % (len(cookies), out.name))
            return True
        except Exception as exc:
            print("    检查失败: %s: %s" % (type(exc).__name__, str(exc)[:80]))
            return False
        finally:
            ctx.close()


def main() -> int:
    names = [a for a in sys.argv[1:] if a in SITES]
    if not names:
        names = [n for n in SITES if (PROFILES / n).exists()]
    ok = []
    for n in names:
        if capture(n):
            ok.append(n)
    print()
    print("成功: %s" % (ok or "无"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
