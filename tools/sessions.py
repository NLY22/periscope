"""Summarise what sessions we hold and what each is good for. No cookie values."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.console import force_utf8_output  # noqa: E402
from core import paths  # noqa: E402

force_utf8_output()

COOKIES = paths.COOKIES
PROFILES = paths.PROFILES

# what a session actually unlocks
VALUE = {
    "xhs": "小红书：笔记正文、搜索、话题、评论",
    "zhihu": "知乎：问题、回答、专栏、热榜",
    "tieba": "百度贴吧：帖子正文、楼层回复",
    "weibo": "微博：时间线、搜索、评论",
}
SESSION_KEYS = {
    "xhs": ["web_session", "a1", "id_token"],
    "zhihu": ["z_c0", "d_c0"],
    "tieba": ["BDUSS", "STOKEN"],
    "weibo": ["SUB", "SUBP"],
}

print("=" * 84)
print("已保存的登录会话")
print("=" * 84)
found = []
for name, desc in VALUE.items():
    f = COOKIES / ("%s.json" % name)
    prof = PROFILES / name
    if not f.exists():
        state = "无 cookie 文件"
        if prof.exists():
            state += "（profile 存在，可能未登录成功）"
        print("\n[%s] %s" % (name, state))
        print("     %s" % desc)
        continue
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception as exc:
        print("\n[%s] cookie 文件读取失败: %s" % (name, exc))
        continue
    cookies = data.get("cookies") or []
    names = {c.get("name") for c in cookies}
    hit = [k for k in SESSION_KEYS[name] if k in names]
    print("\n[%s] %d 个 cookie  会话键: %s" % (name, len(cookies), hit or "无"))
    print("     %s" % desc)
    print("     文件: %s" % f)
    if hit:
        found.append(name)

print()
print("=" * 84)
print("已登录: %s" % (found or "无"))
print("=" * 84)
