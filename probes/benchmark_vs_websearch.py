"""能力对标：web-search 产品 vs 本系统。

DeepSeek 的 web-search 不通过 API 开放（/v1/web_search 返回 404，
tools 只支持 function，web_search:true 只是普通对话），所以无法做
API 到 API 的对比。这里改为按「web-search 产品对外承诺的能力」逐项
实测本系统，每一项都给出可复现的数字。

每一项的判定标准写死在代码里，避免事后调整标准凑结论。
"""

from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import io
import json
import math
import sqlite3
import time


from periscope.search import local as research  # noqa: E402

OUT = paths.REPORT_DIR / '.benchmark.txt'
CORPUS = paths.CORPUS

TOPICS = [
    ("人形机器人", ["人形机器人"]),
    ("固态电池", ["固态电池"]),
    ("DeepSeek", ["DeepSeek"]),
    ("英伟达", ["英伟达", "NVIDIA"]),
    ("宁德时代", ["宁德时代"]),
    ("具身智能", ["具身"]),
    ("自动驾驶", ["自动驾驶"]),
    ("脑机接口", ["脑机"]),
    ("核聚变", ["核聚变"]),
    ("光刻机", ["光刻机"]),
]


def main() -> int:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace")
    db = research.connect()

    L: list[str] = []
    L.append("=" * 94)
    L.append("能力对标：DeepSeek web-search  vs  本系统")
    L.append("=" * 94)
    L.append("")
    L.append("对标方式说明：")
    L.append("  DeepSeek 的 web-search 是产品端功能，开放平台未提供对应 API")
    L.append("  （/v1/web_search -> 404；tools 只接受 function；")
    L.append("   web_search:true 返回「我无法搜索实时新闻」）。")
    L.append("  因此无法做 API 调用的横向对比，改为对 web-search 的公开")
    L.append("  能力逐项实测本系统，每项给出可复现数字。")
    L.append("")

    # ---------- 指标 1：单次查询结果数 ----------
    L.append("#" * 94)
    L.append("# 1. 单次查询能拿回多少条结果")
    L.append("#" * 94)
    L.append("  web-search 公开行为：一次查询返回 10-20 条网页摘要")
    L.append("")
    L.append("  本系统实测：")
    total_hits = 0
    for q, _ in TOPICS:
        rows, _ = research.search(db, q, 20)
        terms = research.terms_of(q)
        match, _ = research.build_match(
            [t for t in terms if len(t) >= research.MIN_GRAM])
        n = 0
        if match:
            try:
                n = db.execute(
                    "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?",
                    (match,)).fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        total_hits += n
        shown = len(rows)
        wb = sum(1 for r in rows if (r["clen"] or 0) >= 400)
        L.append("    %-12s 命中 %6d 条   展示 %2d   其中带正文 %2d"
                 % (q, n, shown, wb))
    L.append("")
    L.append("    %d 个话题合计命中 %d 条，平均每话题 %.0f 条"
             % (len(TOPICS), total_hits, total_hits / len(TOPICS)))

    # ---------- 指标 2：正文深度 ----------
    L.append("")
    L.append("#" * 94)
    L.append("# 2. 每条结果能给出多少正文")
    L.append("#" * 94)
    L.append("  web-search 公开行为：给摘要（几百字），不保证全文")
    L.append("")
    rows_all = []
    for q, _ in TOPICS:
        rows, _ = research.search(db, q, 10)
        rows_all.extend(rows)
    lens = sorted((r["clen"] or 0) for r in rows_all if (r["clen"] or 0) >= 400)
    if lens:
        L.append("  本系统实测（%d 条带正文的结果）：" % len(lens))
        L.append("    中位正文 %d 字" % lens[len(lens) // 2])
        L.append("    平均正文 %d 字" % (sum(lens) // len(lens)))
        L.append("    最长正文 %d 字" % lens[-1])
        L.append("    超过 500 字的比例 %.0f%%"
                 % (100.0 * sum(1 for x in lens if x > 500) / len(lens)))
    # 全库正文统计
    tot = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    full = db.execute(
        "SELECT COUNT(*) FROM items WHERE LENGTH(COALESCE(content,''))>=400"
    ).fetchone()[0]
    chars = db.execute(
        "SELECT SUM(LENGTH(COALESCE(content,''))) FROM items"
    ).fetchone()[0] or 0
    L.append("")
    L.append("  全库：%d 条，其中 %d 条带正文（%.0f%%），正文合计 %.1f 亿字"
             % (tot, full, 100.0 * full / tot, chars / 1e8))

    # ---------- 指标 3：平台原生内容 ----------
    L.append("")
    L.append("#" * 94)
    L.append("# 3. 通用网页索引拿不到的平台内容")
    L.append("#" * 94)
    L.append("  web-search：只能检索通用网页索引收录的页面")
    L.append("")
    PLAT = {
        "bilibili": "B站", "v2ex": "V2EX", "twitter": "微博",
        "youtube": "YouTube", "reddit": "Reddit", "github": "GitHub",
    }
    for st, label in PLAT.items():
        n = db.execute(
            "SELECT COUNT(*) FROM items WHERE source_type=?", (st,)).fetchone()[0]
        nb = db.execute(
            "SELECT COUNT(*) FROM items WHERE source_type=? "
            "AND LENGTH(COALESCE(content,''))>=400", (st,)).fetchone()[0]
        L.append("    %-10s %7d 条  带正文 %6d" % (label, n, nb))
    # 小红书/抖音/快手（publisher 里带）
    for kw, label in (("小红书", "小红书"), ("抖音", "抖音"), ("快手", "快手")):
        n = db.execute(
            "SELECT COUNT(*) FROM items WHERE publisher LIKE ?",
            ("%" + kw + "%",)).fetchone()[0]
        if n:
            L.append("    %-10s %7d 条" % (label, n))

    # ---------- 指标 4：时间回溯 ----------
    L.append("")
    L.append("#" * 94)
    L.append("# 4. 时间维度")
    L.append("#" * 94)
    L.append("  web-search：检索当下索引，通常不给按时间段回溯的能力")
    L.append("")
    for q, _ in TOPICS[:5]:
        rows, _ = research.search(db, q, 10)
        dates = sorted((r["published_at"] or "")[:10]
                       for r in rows if r["published_at"])
        if dates:
            span = "%s ~ %s" % (dates[0], dates[-1])
            L.append("    %-12s 前10条跨 %s（%d 个不同日期）"
                     % (q, span, len(set(dates))))
    earliest = db.execute(
        "SELECT MIN(published_at) FROM items WHERE published_at IS NOT NULL"
    ).fetchone()[0]
    latest = db.execute(
        "SELECT MAX(published_at) FROM items WHERE published_at IS NOT NULL"
    ).fetchone()[0]
    L.append("")
    L.append("    全库时间范围：%s ~ %s" % (str(earliest)[:10], str(latest)[:10]))

    # ---------- 指标 5：速度 ----------
    L.append("")
    L.append("#" * 94)
    L.append("# 5. 检索延迟")
    L.append("#" * 94)
    L.append("  web-search：需要联网检索 + 抓取 + 合成，通常数秒")
    L.append("")
    times = []
    for q, _ in TOPICS:
        t0 = time.monotonic()
        research.search(db, q, 10)
        times.append((time.monotonic() - t0) * 1000)
    times.sort()
    L.append("    本系统本地检索：")
    L.append("      中位 %.1f ms" % times[len(times) // 2])
    L.append("      最快 %.1f ms   最慢 %.1f ms" % (times[0], times[-1]))
    L.append("      平均 %.1f ms" % (sum(times) / len(times)))

    # ---------- 指标 6：来源独立性 ----------
    L.append("")
    L.append("#" * 94)
    L.append("# 6. 结果里的独立来源数")
    L.append("#" * 94)
    L.append("  web-search：给出链接列表，不区分「独立信源」和「同一稿件的转载」")
    L.append("")
    for q, _ in TOPICS[:5]:
        rows, _ = research.search(db, q, 10)
        pubs = {(r["publisher"] or "?")[:40] for r in rows}
        L.append("    %-12s 前10条来自 %d 个独立发布者" % (q, len(pubs)))
    dp = db.execute(
        "SELECT COUNT(DISTINCT publisher) FROM items WHERE publisher IS NOT NULL"
    ).fetchone()[0]
    L.append("")
    L.append("    全库独立发布者：%d 个" % dp)

    # ---------- 指标 7：可复现性 ----------
    L.append("")
    L.append("#" * 94)
    L.append("# 7. 可复现性")
    L.append("#" * 94)
    L.append("  web-search：索引随时间变化，同一问题不同时刻可能得到不同来源")
    L.append("")
    q = "人形机器人"
    a, _ = research.search(db, q, 5)
    b, _ = research.search(db, q, 5)
    same = all((x["title"] or "") == (y["title"] or "") for x, y in zip(a, b))
    L.append("    同一查询连续两次结果一致：%s" % ("是" if same else "否"))
    L.append("    数据落盘位置：%s" % CORPUS)
    L.append("    可离线复跑：是（不依赖外部索引）")

    L.append("")
    L.append("=" * 94)
    db.close()
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
