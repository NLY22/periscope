"""重叠度分析：我们的每个源，DeepSeek Search 能不能替代。

对每个源做同一话题的检索，看 DeepSeek 能不能给出相同的东西。
判定标准写死在代码里，避免事后调整。
"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths


import io
import time
from collections import Counter

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

K = paths.deepseek_key()
H = {"Authorization": "Bearer " + K, "Content-Type": "application/json"}
S = "https://api.deepseek.com/v1/tools/search"


def ds(query, **kw):
    body = {"query": query, "count": 10}
    body.update(kw)
    try:
        r = httpx.post(S, headers=H, json=body, timeout=90)
        if r.status_code != 200:
            return []
        return r.json().get("results") or []
    except Exception:
        return []


TOPIC = "人形机器人"

# (我们的源, 对应 site: 限定, 说明)
CASES = [
    ("微博", f"{TOPIC} site:weibo.com", "社交舆情"),
    ("小红书", f"{TOPIC} site:xiaohongshu.com", "消费体验"),
    ("抖音", f"{TOPIC} site:douyin.com", "短视频"),
    ("快手", f"{TOPIC} site:kuaishou.com", "短视频"),
    ("B站", f"{TOPIC} site:bilibili.com", "视频"),
    ("知乎", f"{TOPIC} site:zhihu.com", "问答"),
    ("CSDN", f"{TOPIC} site:blog.csdn.net", "技术博客"),
    ("掘金", f"{TOPIC} site:juejin.cn", "技术社区"),
    ("V2EX", f"{TOPIC} site:v2ex.com", "技术论坛"),
    ("虎扑", f"{TOPIC} site:hupu.com", "体育论坛"),
    ("头条", f"{TOPIC} site:toutiao.com", "资讯"),
    ("公众号", f"{TOPIC} site:mp.weixin.qq.com", "公众号"),
    ("YouTube", f"{TOPIC} site:youtube.com", "视频"),
    ("豆瓣", f"{TOPIC} site:douban.com", "书影音"),
    ("雪球", f"{TOPIC} site:xueqiu.com", "投资"),
]

print("=" * 88)
print("DeepSeek Search 对各平台的覆盖（site: 限定）")
print("=" * 88)
print()
print("%-10s %-6s %-8s %s" % ("平台", "条数", "有正文", "样例标题"))
print("-" * 88)

rows = []
for name, q, note in CASES:
    res = ds(q, count=10, need_content=True)
    n = len(res)
    wb = sum(1 for x in res if (x.get("content") or ""))
    sample = (res[0].get("title") or "")[:38] if res else "-"
    rows.append((name, n, wb, sample, res))
    print("%-10s %-6d %-8d %s" % (name, n, wb, sample))
    time.sleep(0.25)

print()
print("=" * 88)
print("判定")
print("=" * 88)
covered = [r[0] for r in rows if r[1] >= 5]
weak = [r[0] for r in rows if 0 < r[1] < 5]
missing = [r[0] for r in rows if r[1] == 0]

print("  DeepSeek 覆盖良好（>=5 条）: %s" % ("、".join(covered) or "无"))
print("  DeepSeek 覆盖很弱（1-4 条）: %s" % ("、".join(weak) or "无"))
print("  DeepSeek 完全搜不到      : %s" % ("、".join(missing) or "无"))

print()
print("=" * 88)
print("平台内容深度对比：DeepSeek 给的 vs 我们能拿到的")
print("=" * 88)
for name, n, wb, sample, res in rows:
    if n == 0:
        continue
    # 看 DeepSeek 给的是否只是壳层
    shells = 0
    for x in res:
        t = (x.get("title") or "")
        if t.startswith("相关视频") or t.endswith("_bilibili") or len(t) < 12:
            shells += 1
    print("  %-8s %2d 条  疑似壳层 %d  例: %s" % (name, n, shells, sample[:44]))
