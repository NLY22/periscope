"""深度验证：所谓“可替代”的平台，DeepSeek 拿到的到底是真内容还是二手转述。

用一个更严的指标：结果里的网址**是不是平台自己的域名**。
搜索引擎常常返回“关于微博的报道”而不是“微博上的帖子”。
"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths


import io
from urllib.parse import urlparse

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

K = paths.deepseek_key()
H = {"Authorization": "Bearer " + K, "Content-Type": "application/json"}
S = "https://api.deepseek.com/v1/tools/search"

TOPIC = "人形机器人"

CASES = [
    ("微博", "weibo.com", ["weibo.com", "weibo.cn"]),
    ("快手", "kuaishou.com", ["kuaishou.com", "kwaicdn.com"]),
    ("知乎", "zhihu.com", ["zhihu.com"]),
    ("虎扑", "hupu.com", ["hupu.com"]),
    ("贴吧", "tieba.baidu.com", ["tieba.baidu.com"]),
    ("抖音", "douyin.com", ["douyin.com", "iesdouyin.com"]),
    ("B站", "bilibili.com", ["bilibili.com", "b23.tv"]),
    ("小红书", "xiaohongshu.com", ["xiaohongshu.com", "xhslink.com"]),
]

for name, domain, owns in CASES:
    q = "%s site:%s" % (TOPIC, domain)
    try:
        r = httpx.post(S, headers=H, json={
            "query": q, "count": 10, "need_content": True}, timeout=90)
        res = r.json().get("results") or [] if r.status_code == 200 else []
    except Exception:
        res = []
    if not res:
        print("=" * 84)
        print("%s: 0 条（搜不到）" % name)
        continue

    own = 0
    foreign = []
    for x in res:
        u = x.get("url") or ""
        host = urlparse(u).netloc.lower()
        if any(host.endswith(o) or o in host for o in owns):
            own += 1
        else:
            foreign.append((host, (x.get("title") or "")[:40]))

    print("=" * 84)
    print("%s: %d 条，其中 %d 条确实来自 %s 自己的域名" % (name, len(res), own, domain))
    if foreign:
        print("  以下 %d 条来自其他站点（说明搜索结果不是平台原帖）:" % len(foreign))
        for host, t in foreign[:5]:
            print("    [%-26s] %s" % (host[:26], t))
