"""关键对比：同一平台上，DeepSeek 索引到的 vs 我们抓到的。

决定「删还是留」的不是覆盖条数，是两件事：
  1. 新鲜度 —— 昨天的内容有没有
  2. 深度   —— 能不能拿到平台内的详情（播放量、评论、作者下的原帖）
"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths


import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx

K = paths.deepseek_key()
H = {"Authorization": "Bearer " + K, "Content-Type": "application/json"}
S = "https://api.deepseek.com/v1/tools/search"


def ds(q, count=20):
    try:
        r = httpx.post(S, headers=H, json={"query": q, "count": count,
                                          "need_content": True}, timeout=90)
        return r.json().get("results") or [] if r.status_code == 200 else []
    except Exception:
        return []


print("=" * 86)
print("1. 微博：DeepSeek 索引的日期分布")
print("=" * 86)
res = ds("人形机器人 site:weibo.com", 20)
dates = sorted((x.get("date") or "")[:10] for x in res if x.get("date"))
print("  %d 条，有日期 %d 条" % (len(res), len(dates)))
if dates:
    print("  最新 %s   最旧 %s" % (dates[-1], dates[0]))
    for x in res[:5]:
        print("    %s  %s" % ((x.get("date") or "?")[:10], (x.get("title") or "")[:52]))

print()
print("=" * 86)
print("2. 我们库里的微博：日期分布")
print("=" * 86)
import sqlite3
c = sqlite3.connect(str(paths.CORPUS), timeout=15)
c.execute("PRAGMA temp_store=MEMORY")
rows = c.execute("""select substr(published_at,1,10) d, title from items
    where source_type='twitter' and (title like '%人形机器人%')
    order by published_at desc limit 8""").fetchall()
print("  最近 8 条：")
for d, t in rows:
    print("    %s  %s" % (d or "?", (t or "")[:52]))
n = c.execute("""select count(*) from items where source_type='twitter'
  and published_at >= date('now','-2 days')""").fetchone()[0]
print("  近 2 天的微博条数:", n)
c.close()

print()
print("=" * 86)
print("3. 抖音：DeepSeek 给的是什么")
print("=" * 86)
res = ds("人形机器人 site:douyin.com", 20)
for x in res[:5]:
    print("    date=%s title=%s" % ((x.get("date") or "?")[:10], (x.get("title") or "")[:50]))
    ct = (x.get("content") or "")[:80].replace("\n", " ")
    print("          content: %s" % ct)

print()
print("=" * 86)
print("4. 知乎：DeepSeek 能否拿到问题的回答内容")
print("=" * 86)
res = ds("人形机器人 为什么需要人形 site:zhihu.com", 10)
for x in res[:3]:
    print("    title: %s" % (x.get("title") or "")[:56])
    ct = (x.get("content") or "")
    print("    content 长度 %d  开头: %s" % (len(ct), " ".join(ct.split())[:110]))

print()
print("=" * 86)
print("5. 最新内容：昨天的新闻能不能搜到")
print("=" * 86)
res = ds("人形机器人 2026年10月17日", 20)
dates = [(x.get("date") or "")[:10] for x in res if x.get("date")]
from collections import Counter
print("  日期分布:", dict(Counter(d[:7] for d in dates).most_common(6)))
print("  有 2026-10-17 的:", sum(1 for d in dates if d == "2026-10-17"))
