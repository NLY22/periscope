"""登录窗口：打开 → 置顶 → 自动检测登录 → 自动保存。

与旧版的三个区别，每一个都对应一次失败：

1. 旧版只等「关窗」，用户没关就什么都没有。新版在后台轮询 cookie，
   一旦出现登录态就立即保存，不依赖关窗时机。
2. 旧版没有置顶，窗口可能被别的窗口盖住。新版把它带到前台。
3. 旧版一次跑两个站，第一个窗口关掉前看不到第二个。新版默认单站。

用法：
    python login_window.py weibo
    python login_window.py xhs kuaishou
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
LOG = paths.REPORT_DIR / ".login_progress.txt"

SITES = {
    "weibo": {
        "label": "微博",
        "url": "https://weibo.com/",
        "domain": "weibo.com",
        # SUB / SUBP 是微博的会话 cookie，出现即已登录
        "need": ["SUB", "SUBP"],
    },
    "xhs": {
        "label": "小红书",
        "url": "https://www.xiaohongshu.com/",
        "domain": "xiaohongshu.com",
        # web_session 是会话；a1 是设备指纹，两者都有才是真登录
        "need": ["web_session"],
        "also": ["a1"],
    },
    "kuaishou": {
        "label": "快手",
        "url": "https://www.kuaishou.com/",
        "domain": "kuaishou.com",
        "need": ["passToken", "userId", "kuaishou.web.cp.api_ph"],
    },
    "smzdm": {
        "label": "什么值得买",
        "url": "https://www.smzdm.com/",
        "domain": "smzdm.com",
        "need": ["sess", "smzdm_id", "user_login"],
    },
    "douyin": {
        "label": "抖音",
        "url": "https://www.douyin.com/",
        "domain": "douyin.com",
        "need": ["sessionid", "sessionid_ss", "sid_tt"],
    },
    "zhihu": {
        "label": "知乎",
        "url": "https://www.zhihu.com/",
        "domain": "zhihu.com",
        "need": ["z_c0"],
    },
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _log(lines: list[str], msg: str) -> None:
    """同时写屏幕与文件，这样即便控制台编码坏了也能读结果。"""
    print(msg, flush=True)
    lines.append(msg)
    try:
        LOG.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    names = [a for a in sys.argv[1:] if a in SITES]
    if not names:
        print("usage: python login_window.py %s" % "|".join(SITES))
        return 2

    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

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

        _log(lines, "")
        _log(lines, "=" * 70)
        _log(lines, "  %s  —— 浏览器窗口即将打开" % spec["label"])
        _log(lines, "=" * 70)
        _log(lines, "  1) 在弹出的 Chrome 窗口里登录 %s" % spec["label"])
        _log(lines, "  2) 登录成功后【什么都不用做】")
        _log(lines, "  3) 本脚本检测到登录态会自动保存并关闭窗口")
        _log(lines, "  4) 如果 5 分钟没登录，窗口会自动关闭")
        _log(lines, "")

        cookies_saved = False
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
                _log(lines, "  ✗ 浏览器启动失败: %s: %s" % (
                    type(exc).__name__, str(exc)[:120]))
                continue

            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(spec["url"], wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:  # noqa: BLE001
                _log(lines, "  (首页加载较慢: %s，继续等待)" % type(exc).__name__)

            # 把窗口带到前台。Playwright 没有直接的 bringToFront 跨窗口能力，
            # 但 page.bring_to_front() 能让 Chrome 自己激活这个标签页，从而把
            # 整个窗口提到最前。
            try:
                page.bring_to_front()
            except Exception:
                pass

            _log(lines, "  窗口已打开，等待登录……（最多等 5 分钟）")

            deadline = time.time() + 300
            last_report = 0
            while time.time() < deadline:
                time.sleep(2)
                try:
                    if not ctx.pages:
                        _log(lines, "  窗口被关闭了")
                        break
                except Exception:
                    _log(lines, "  浏览器已退出")
                    break

                # 检查登录态
                try:
                    jar = [c for c in ctx.cookies()
                           if spec["domain"] in (c.get("domain") or "")]
                except Exception:
                    jar = []
                got = {c["name"] for c in jar}
                hit = [k for k in spec["need"] if k in got]

                if hit:
                    # 有会话 cookie，落盘
                    try:
                        state = ctx.storage_state()
                        out = COOKIE_DIR / ("%s.json" % name)
                        out.write_text(
                            json.dumps(state, ensure_ascii=False, indent=2),
                            encoding="utf-8")
                        cookies_saved = True
                        _log(lines, "")
                        _log(lines, "  ✅ 检测到登录态 %s" % hit)
                        _log(lines, "     已保存 -> data/cookies/%s.json (%d 个 cookie)"
                             % (name, len(jar)))
                        also = spec.get("also") or []
                        miss = [a for a in also if a not in got]
                        if miss:
                            _log(lines, "     注意：缺少 %s，可能登录未完全完成"
                                 % miss)
                    except Exception as exc:  # noqa: BLE001
                        _log(lines, "  ✗ 保存失败: %s" % exc)
                    break

                # 每 30 秒报一次进度
                if time.time() - last_report > 30:
                    last_report = time.time()
                    left = int(deadline - time.time())
                    _log(lines, "  …… 还在等（剩 %d 秒），已看到 %d 个 %s cookie"
                         % (left, len(jar), spec["domain"]))

            try:
                ctx.close()
            except Exception:
                pass

        if not cookies_saved:
            _log(lines, "  ⚠️  未检测到登录态，什么也没保存")

    _log(lines, "")
    _log(lines, "完成。可读取 .login_progress.txt 查看详情。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
