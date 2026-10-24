"""关键验证：after: 到底有没有生效，还是被忽略了。"""

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


def ds(q, count=20):
    try:
        r = httpx.post(S, headers=H, json={"query": q, "count": count},
                       timeout=120)
        return r.json().get("results") or [] if r.status_code == 200 else []
    except Exception:
        return []


print("=" * 86)
print("after: 是否真的生效")
print("=" * 86)
for label, q in [
    ("无限制", "人形机器人"),
    ("after:2026-01-01", "人形机器人 after:2026-01-01"),
    ("after:2026-06-01", "人形机器人 after:2026-06-01"),
    ("after:2026-09-01", "人形机器人 after:2026-09-01"),
    ("after:2026-10-01", "人形机器人 after:2026-10-01"),
    ("after:2027-01-01(未来)", "人形机器人 after:2027-01-01"),
]:
    res = ds(q, 20)
    d = sorted((x.get("date") or "")[:10] for x in res if x.get("date"))
    span = ("%s ~ %s" % (d[0], d[-1])) if d else "无日期"
    before = sum(1 for x in d if x < "2026-09-01")
    print("  %-24s %2d 条  跨度 %s  9月前的 %d 条" % (label, len(res), span, before))
    time.sleep(0.25)

print()
print("=" * 86)
print("after: 限定下，结果里最旧的日期是什么")
print("=" * 86)
res = ds("人形机器人 after:2026-10-01", 50)
d = sorted((x.get("date") or "")[:10] for x in res if x.get("date"))
print("  50 条里 earliest = %s   latest = %s" % (d[0] if d else "-", d[-1] if d else "-"))
months = Counter(x[:7] for x in d)
print("  按月:", dict(sorted(months.items())))

print()
print("=" * 86)
print("before: 是否生效（限历史）")
print("=" * 86)
for label, q in [
    ("before:2025-01-01", "人形机器人 before:2025-01-01"),
    ("before:2020-01-01", "人形机器人 before:2020-01-01"),
]:
    res = ds(q, 20)
    d = sorted((x.get("date") or "")[:10] for x in res if x.get("date"))
    span = ("%s ~ %s" % (d[0], d[-1])) if d else "无日期"
    print("  %-20s %2d 条  跨度 %s" % (label, len(res), span))
    time.sleep(0.25)
