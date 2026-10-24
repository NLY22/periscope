"""DeepSeek Search API 能力边界系统测试。

从六个维度测：
  1. 时效边界   —— 最新能到什么时候
  2. 数量边界   —— count 上限与去重后的有效条数
  3. 平台边界   —— 哪些平台有、哪些没有
  4. 查询语法   —— 官方支持的操作符实际是否生效
  5. 语言边界   —— 中英文覆盖
  6. 稳定性     —— 同样的查询多次结果是否一致
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
    t0 = time.monotonic()
    try:
        r = httpx.post(S, headers=H, json=body, timeout=120)
        el = time.monotonic() - t0
        if r.status_code != 200:
            return [], el, r.status_code
        return (r.json().get("results") or []), el, 200
    except Exception as exc:
        return [], time.monotonic() - t0, type(exc).__name__


def dates_of(res):
    return sorted((x.get("date") or "")[:10] for x in res if x.get("date"))


# ============================================================
print("=" * 88)
print("1. 时效边界：after: 能否把结果限定在最近")
print("=" * 88)
for label, extra in [
    ("最近一月", "after:2026-09-18"),
    ("最近一周", "after:2026-10-11"),
    ("最近三天", "after:2026-10-15"),
]:
    res, el, code = ds("人形机器人 %s" % extra, count=20)
    d = dates_of(res)
    newest = d[-1] if d else "-"
    print("  %-8s %2d 条  %.2fs  最新日期 %s  有日期的 %d/%d"
          % (label, len(res), el, newest, len(d), len(res)))
    time.sleep(0.25)

print()
print("=" * 88)
print("2. 数量边界：count 上限")
print("=" * 88)
for c in (10, 50, 100):
    res, el, code = ds("人形机器人", count=c)
    print("  count=%-4d -> %2d 条  HTTP %s  %.2fs" % (c, len(res), code, el))
    time.sleep(0.25)

# 去重后的有效条数
res, el, code = ds("人形机器人", count=50)
sites = Counter((x.get("site_name") or "?") for x in res)
titles = Counter((x.get("title") or "")[:20] for x in res)
print("  count=50 的结果：")
print("    独立站点 %d 个" % len(sites))
print("    标题前20字重复的组: %d" % sum(1 for t, n in titles.items() if n > 1))
print("    站点最集中: %s" % (sites.most_common(3)))

print()
print("=" * 88)
print("3. 语言边界")
print("=" * 88)
for label, q in [
    ("中文", "人形机器人 量产"),
    ("英文", "humanoid robot mass production"),
    ("日文", "ヒューマノイドロボット 量産"),
    ("混合", "humanoid 人形 robot 机器人"),
]:
    res, el, code = ds(q, count=10)
    lang_cn = sum(1 for x in res if any('\u4e00' <= c <= '\u9fff'
                                       for c in (x.get("title") or "")))
    print("  %-6s %2d 条  中文标题 %d  %.2fs" % (label, len(res), lang_cn, el))
    time.sleep(0.25)

print()
print("=" * 88)
print("4. 查询语法：官方文档列出的操作符实际是否生效")
print("=" * 88)
base, _, _ = ds("人形机器人 宇树", count=20)
base_sites = {(x.get("site_name") or "?") for x in base}
print("  基线（无操作符）: %d 条，%d 个站点" % (len(base), len(base_sites)))

tests = [
    ("site: 限定", "人形机器人 site:qbitai.com", 10),
    ("-site: 排除", "人形机器人 -site:qbitai.com", 20),
    ("精确短语", '"人形机器人"', 20),
    ("排除词", "人形机器人 -宇树", 20),
    ("lang: 限定", "人形机器人 lang:en", 20),
]
for label, q, c in tests:
    res, el, code = ds(q, count=c)
    names = Counter((x.get("site_name") or "?") for x in res)
    print("  %-12s %2d 条  站点: %s"
          % (label, len(res), ", ".join(s for s, _ in names.most_common(3))))
    time.sleep(0.25)

print()
print("=" * 88)
print("5. 稳定性：同一查询连续 3 次，结果是否一致")
print("=" * 88)
sets = []
for i in range(3):
    res, el, code = ds("人形机器人 量产", count=10)
    urls = [x.get("url") for x in res]
    sets.append(set(urls))
    print("  第%d次: %d 条  %.2fs" % (i + 1, len(res), el))
    time.sleep(0.3)
print("  三次结果完全一致: %s" % (sets[0] == sets[1] == sets[2]))
print("  交集大小: %d" % len(sets[0] & sets[1] & sets[2]))

print()
print("=" * 88)
print("6. 冷门/低资源话题")
print("=" * 88)
for q in ["硼中子俘获治疗 临床", "深海采矿 锰结核", "钙钛矿光伏 量产",
          "光热发电 熔盐储热", "工业母机 五轴联动"]:
    res, el, code = ds(q, count=10)
    auth = [x.get("authority") for x in res]
    good = sum(1 for a in auth if a is not None and a >= 3)
    print("  %-22s %2d 条  authority>=3 的 %d 条  %.2fs"
          % (q, len(res), good, el))
    time.sleep(0.25)
