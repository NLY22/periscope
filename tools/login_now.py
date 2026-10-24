"""登录工具 v3：持续保存 cookie，不靠关窗时机，不靠错误的登录判定。

前两次失败的三个原因，逐个修：
1. emoji 打印触发 GBK UnicodeEncodeError，脚本崩溃 → 全部改为 ASCII 输出，且写文件
2. 我用 Stop-Process 提前杀掉了浏览器 → 本版不主动杀，只在用户关窗时退出
3. SUB 这种游客也有 → 不再用「有会话 cookie」判定登录，改为持续覆盖保存 + 提交后验证

用法：
    python login_now.py xhs
    python login_now.py xhs douyin kuaishou smzdm

窗口打开后，你在里面正常登录。cookie 每 3 秒自动保存一次（覆盖），
所以无论你什么时候关窗，最后一次的状态都留下了。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paths  # noqa: E402
from core.console import force_utf8_output  # noqa: E402

force_utf8_output()

from playwright.sync_api import sync_playwright  # noqa: E402

PROFILES = paths.PROFILES
COOKIE_DIR = paths.COOKIES
LOG = paths.REPORT_DIR / ".login_now.txt"

SITES = {
    "xhs": {
        "label": "xiaohongshu",
        "url": "https://www.xiaohongshu.com/",
        "domain": "xiaohongshu.com",
    },
    "douyin": {
        "label": "douyin",
        "url": "https://www.douyin.com/",
        "domain": "douyin.com",
    },
    "kuaishou": {
        "label": "kuaishou",
        "url": "https://www.kuaishou.com/",
        "domain": "kuaishou.com",
    },
    "smzdm": {
        "label": "smzdm",
        "url": "https://www.smzdm.com/",
        "domain": "smzdm.com",
    },
    "weibo": {
        "label": "weibo",
        "url": "https://weibo.com/",
        "domain": "weibo.com",
    },
    "xueqiu": {
        "label": "xueqiu",
        "url": "https://xueqiu.com/",
        "domain": "xueqiu.com",
    },
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

MAX_WAIT_SECONDS = 600  # 10 分钟上限


def log(msg: str) -> None:
    """只写文件，不 print —— 避免 GBK 控制台把脚本搞崩。"""
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def main() -> int:
    names = [a for a in sys.argv[1:] if a in SITES]
    if not names:
        print("usage: python login_now.py %s" % "|".join(SITES))
        return 2

    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    LOG.write_text("", encoding="utf-8")

    for name in names:
        spec = SITES[name]
        prof = PROFILES / name
        prof.mkdir(parents=True, exist_ok=True)
        for junk in ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"):
            p = prof / junk
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

        log("")
        log("=" * 60)
        log("SITE: %s" % spec["label"])
        log("=" * 60)

        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=str(prof), channel="chrome",
                    headless=False, user_agent=UA, locale="zh-CN",
                    viewport={"width": 1280, "height": 900},
                    args=["--disable-blink-features=AutomationControlled",
                          "--start-maximized"],
                )
            except Exception as exc:  # noqa: BLE001
                log("LAUNCH_FAIL: %s: %s" % (type(exc).__name__, str(exc)[:150]))
                continue

            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(spec["url"], wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:  # noqa: BLE001
                log("GOTO_SLOW: %s" % type(exc).__name__)
            try:
                page.bring_to_front()
            except Exception:
                pass

            out_path = COOKIE_DIR / ("%s.json" % name)
            started = time.time()
            last_count = -1
            saves = 0

            # 持续保存：每 3 秒覆盖一次。用户什么时候关窗都不影响结果。
            while time.time() - started < MAX_WAIT_SECONDS:
                time.sleep(3)

                # 用户关窗 -> 退出
                try:
                    if not ctx.pages:
                        log("WINDOW_CLOSED by user")
                        break
                except Exception:
                    log("BROWSER_EXITED")
                    break

                try:
                    jar = [c for c in ctx.cookies()
                           if spec["domain"] in (c.get("domain") or "")]
                except Exception:
                    continue

                if len(jar) != last_count:
                    last_count = len(jar)
                    names_now = sorted({c["name"] for c in jar})
                    log("cookies=%d  %s" % (len(jar), ",".join(names_now[:18])))

                if jar:
                    try:
                        state = ctx.storage_state()
                        out_path.write_text(
                            json.dumps(state, ensure_ascii=False, indent=2),
                            encoding="utf-8")
                        saves += 1
                        if saves % 10 == 0:
                            log("saved %d times, %d cookies" % (saves, len(jar)))
                    except Exception as exc:  # noqa: BLE001
                        log("SAVE_FAIL: %s" % type(exc).__name__)

            log("RESULT: %s cookies=%d saves=%d -> %s" % (
                name, last_count if last_count > 0 else 0, saves, out_path.name))

            try:
                ctx.close()
            except Exception:
                pass

    log("")
    log("ALL_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
