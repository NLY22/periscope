"""检索能力深度测试：混淆、多样性、时间、长尾。

压力测试（19/19）只验证了「第 1 名相关」。这里补四类更难的：

  E. 易混淆话题 —— 相近概念是否互相污染
  F. 来源多样性 —— 是否被单一来源霸屏
  G. 时间覆盖   —— 是否只能拿到最新内容
  H. 长尾查询   —— 冷门话题是否还有结果
"""

from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import io


from periscope.search import local as research  # noqa: E402

OUT = str(paths.REPORT_DIR / '.eval_deep.txt')

# E. 易混淆：每组两个话题，检索 A 的结果不应被 B 完全占据
CONFUSION = [
    ("人形机器人", "工业机器人"),
    ("大模型", "小模型"),
    ("固态电池", "锂电池"),
    ("自动驾驶", "辅助驾驶"),
    ("芯片", "显卡"),
]

# H. 长尾：冷门但真实存在的词
LONGTAIL = [
    "光刻机", "稀土", "量子计算", "碳化硅", "氢能源",
    "数字孪生", "边缘计算", "存算一体", "Chiplet", "RISC-V",
]


def main() -> int:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace")
    db = research.connect()

    L: list[str] = []
    L.append("=" * 92)
    L.append("检索能力深度测试")
    L.append("=" * 92)

    # ---------- E. 易混淆 ----------
    L.append("")
    L.append("#" * 92)
    L.append("# E. 易混淆话题：检索 A 的结果里有多少真的在讲 A")
    L.append("#" * 92)
    for a, b in CONFUSION:
        rows, _ = research.search(db, a, 10)
        if not rows:
            L.append("  %-12s vs %-12s  ✗ 无结果" % (a, b))
            continue
        only_a = only_b = both = neither = 0
        for r in rows:
            blob = (r["title"] or "") + " " + (r["snippet"] or "")
            ha, hb = a in blob, b in blob
            if ha and hb:
                both += 1
            elif ha:
                only_a += 1
            elif hb:
                only_b += 1
            else:
                neither += 1
        L.append("  %-12s / %-12s  纯A %d  纯B %d  都有 %d  都无 %d"
                 % (a, b, only_a, only_b, both, neither))
        if only_b > only_a:
            L.append("      ⚠ 讲 %s 的比讲 %s 的还多，可能混淆" % (b, a))
            for r in rows[:3]:
                blob = (r["title"] or "") + " " + (r["snippet"] or "")
                if b in blob and a not in blob:
                    L.append("        · %s" % (r["title"] or "")[:60])

    # ---------- F. 来源多样性 ----------
    L.append("")
    L.append("#" * 92)
    L.append("# F. 来源多样性：前 10 条来自几个独立发布者")
    L.append("#" * 92)
    for q in ["人形机器人", "大模型", "DeepSeek", "英伟达", "固态电池"]:
        rows, _ = research.search(db, q, 10)
        pubs = [(r["publisher"] or "")[:36] for r in rows]
        uniq = len(set(pubs))
        from collections import Counter
        top = Counter(pubs).most_common(3)
        L.append("  %-12s %2d 条 → %d 个发布者   最多: %s"
                 % (q, len(rows), uniq,
                    ", ".join("%s×%d" % (p or "?", n) for p, n in top)))
        if uniq <= 2 and len(rows) >= 8:
            L.append("      ⚠ 来源过于集中")

    # ---------- G. 时间覆盖 ----------
    L.append("")
    L.append("#" * 92)
    L.append("# G. 时间覆盖：前 10 条的日期跨度")
    L.append("#" * 92)
    for q in ["人形机器人", "固态电池", "核聚变"]:
        rows, _ = research.search(db, q, 10)
        dates = sorted((r["published_at"] or "")[:10] for r in rows if r["published_at"])
        if dates:
            L.append("  %-12s %s ~ %s  （%d 个日期）"
                     % (q, dates[0], dates[-1], len(set(dates))))
        else:
            L.append("  %-12s 无日期信息" % q)

    # ---------- H. 长尾 ----------
    L.append("")
    L.append("#" * 92)
    L.append("# H. 长尾话题：冷门词是否还有结果")
    L.append("#" * 92)
    tail_ok = 0
    for q in LONGTAIL:
        rows, note = research.search(db, q, 5)
        n = len(rows)
        rel = 0
        for r in rows:
            blob = (r["title"] or "") + " " + (r["snippet"] or "")
            if q in blob:
                rel += 1
        if n and rel:
            tail_ok += 1
        L.append("  %-12s %2d 条  相关 %d  %s" % (
            q, n, rel, "✓" if (n and rel) else "✗"))
        if n and rel < n:
            for r in rows:
                blob = (r["title"] or "") + " " + (r["snippet"] or "")
                if q not in blob:
                    L.append("      不相关: %s" % (r["title"] or "")[:56])
                    break

    L.append("")
    L.append("=" * 92)
    L.append("  长尾有结果: %d / %d" % (tail_ok, len(LONGTAIL)))

    db.close()
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
