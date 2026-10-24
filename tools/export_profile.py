"""Export a saved browser profile's cookies to a JSON file.

`login_window.py` exports on close, but the sync API has no context 'close'
event, so a window closed in a way the poll does not catch leaves the login
captured in the profile and not exported. The profile is the durable copy - it
has the cookies either way - so this reads from it directly rather than asking
for a second login.

    python export_profile.py douyin kuaishou smzdm
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

# What a logged-in session on each site must contain. Absence means the profile
# exists but the login did not take.
REQUIRED = {
    "douyin": ["sessionid", "sessionid_ss", "sid_tt", "passport_csrf_token",
               "ttwid", "UIFID"],
    "kuaishou": ["kuaishou.web.cp.api_ph", "kuaishou.web.cp.api_st", "userId",
                 "passToken", "did"],
    "smzdm": ["sess", "smzdm_id", "user_login", "smzdm_id"],
    "weibo": ["SUB"],
    "xhs": ["web_session"],
    "zhihu": ["z_c0"],
    "tieba": ["BDUSS"],
}


def main() -> int:
    names = sys.argv[1:] or list(REQUIRED)
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    rc = 0
    with sync_playwright() as p:
        for name in names:
            prof = PROFILES / name
            if not prof.exists():
                print("%-10s profile 不存在，跳过" % name)
                rc = 1
                continue
            # Clear a stale lock; the profile is not open by another process at
            # this point, and a leftover lock makes launch_persistent_context
            # refuse to start.
            for lock_name in ("lockfile", "SingletonLock"):
                lock = prof / lock_name
                if lock.exists():
                    try:
                        lock.unlink()
                    except Exception:
                        pass
            ctx = None
            try:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=str(prof), channel="chrome", headless=True,
                    user_agent=UA, locale="zh-CN",
                    viewport={"width": 1280, "height": 900})
                cookies = ctx.cookies()
                state = ctx.storage_state()
            except Exception as exc:
                print("%-10s 打开 profile 失败: %s: %s"
                      % (name, type(exc).__name__, str(exc)[:60]))
                rc = 1
                continue
            finally:
                if ctx is not None:
                    try:
                        ctx.close()
                    except Exception:
                        pass

            # Only this site's cookies; a profile can carry others.
            site_domain = {
                "douyin": "douyin.com", "kuaishou": "kuaishou.com",
                "smzdm": "smzdm.com", "weibo": "weibo.com",
                "xhs": "xiaohongshu.com", "zhihu": "zhihu.com",
                "tieba": "baidu.com",
            }.get(name, name)
            mine = [c for c in cookies if site_domain in (c.get("domain") or "")]
            found = {c["name"] for c in mine}
            need = [k for k in REQUIRED.get(name, []) if k not in found]
            hit = [k for k in REQUIRED.get(name, []) if k in found]

            print("%-10s %3d 个 %s cookie   命中: %s"
                  % (name, len(mine), site_domain, ", ".join(hit[:4]) or "无"))
            if hit:
                out = COOKIE_DIR / ("%s.json" % name)
                out.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                print("           -> 已写入 %s (%d KB)"
                      % (out.name, len(out.read_bytes()) // 1024))
            else:
                print("           ⚠️ 未发现登录 cookie（缺 %s）" % ", ".join(need[:4]))
                rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
