
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import io
import re
from collections import Counter

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

K = paths.deepseek_key()
H = {"Authorization": "Bearer " + K, "Content-Type": "application/json"}
S = "https://api.deepseek.com/v1/tools/search"


def call(query, **kw):
    body = {"query": query}
    body.update(kw)
    r = httpx.post(S, headers=H, json=body, timeout=120)
    if r.status_code != 200:
        return []
    return r.json().get("results") or []


print("=" * 84)
print("A. 内容新鲜度：最近一天的内容能不能搜到")
print("=" * 84)
res = call("人形机器人", count=50, need_content=True)
dates = [(x.get("date") or "")[:10] for x in res]
have = [d for d in dates if d]
print("  50 条中有日期的:", len(have))
if have:
    print("  最新:", max(have), "  最旧:", min(have))
    yms = Counter(d[:7] for d in have)
    print("  按月:", ", ".join("%s×%d" % (k, v) for k, v in sorted(yms.items(), reverse=True)[:8]))

print()
print("=" * 84)
print("B. 正文质量：content 字段到底是什么")
print("=" * 84)
lens = [len(x.get("content") or "") for x in res]
nz = sorted(x for x in lens if x > 0)
print("  50 条里有正文:", len(nz), " 中位:", nz[len(nz) // 2] if nz else 0)
# 看一条完整的
for x in res:
    c = x.get("content") or ""
    if len(c) > 2000:
        print("  样本 title:", (x.get("title") or "")[:50])
        print("  content 长度:", len(c))
        print("  开头 300 字:")
        print("   ", " ".join(c.split())[:300])
        print("  结尾 200 字:")
        print("   ", " ".join(c.split())[-200:])
        # 检测是否被截断
        if c.rstrip().endswith(("...", "…")):
            print("  ⚠ 疑似截断")
        break

print()
print("=" * 84)
print("C. summary 与 content 的关系")
print("=" * 84)
for x in res[:3]:
    sm = x.get("summary") or ""
    ct = x.get("content") or ""
    print("  %-40s summary %4d  content %5d" % (
        (x.get("title") or "")[:40], len(sm), len(ct)))

print()
print("=" * 84)
print("D. 重复检测：同一话题 50 条里有几个独立站点")
print("=" * 84)
sites = Counter((x.get("site_name") or "?") for x in res)
print("  独立站点:", len(sites), "/ 50 条")
for s, n in sites.most_common(10):
    print("    %-30s %d" % (s[:30], n))

print()
print("=" * 84)
print("E. 同一条新闻被多家转载算几条")
print("=" * 84)
titles = [(x.get("title") or "")[:40] for x in res]
tcount = Counter(titles)
dup = [(t, n) for t, n in tcount.items() if n > 1]
print("  完全同名的:", len(dup))
for t, n in dup[:5]:
    print("    %s ×%d" % (t, n))
# 近似重复：取标题前 15 字
pref = Counter((x.get("title") or "")[:15] for x in res)
near = [(t, n) for t, n in pref.items() if n > 1]
print("  前15字相同的:", len(near))
for t, n in near[:5]:
    print("    %s… ×%d" % (t, n))
