"""检索能力压力测试：覆盖话题类型、查询写法、边界情况。

这是一份「回归测试」，每次改检索逻辑都应该跑它。
"""

from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import io
import sqlite3


from periscope.search import local as research  # noqa: E402

OUT = str(paths.REPORT_DIR / '.eval_stress.txt')

# 分四组，因为不同类型的查询失败方式不同
GROUPS = {
    "A. 单实体（专有名词）": [
        ("DeepSeek", ["DeepSeek"]),
        ("英伟达", ["英伟达", "NVIDIA", "Nvidia"]),
        ("宁德时代", ["宁德时代"]),
        ("OpenAI", ["OpenAI"]),
        ("宇树", ["宇树"]),
    ],
    "B. 概念（需要语义宽泛）": [
        ("人形机器人", ["人形机器人", "机器人"]),
        ("固态电池", ["固态电池"]),
        ("具身智能", ["具身"]),
        ("脑机接口", ["脑机"]),
        ("核聚变", ["核聚变", "聚变"]),
    ],
    "C. 中文多词（考验分词）": [
        ("大模型 幻觉", ["幻觉"]),
        ("机器人 量产", ["量产"]),
        ("芯片 出口管制", ["出口管制", "管制"]),
        ("车企 降价", ["降价"]),
        ("AI 面试", ["面试"]),
    ],
    "D. 边界情况": [
        ("AI", ["AI"]),                    # 2 字符，trigram 查不到
        ("VR", ["VR"]),                    # 2 字符
        ("不存在的词xyz123", []),           # 应当无结果
        ("人形机器人 固态电池", ["人形机器人", "固态电池"]),  # 跨话题
    ],
}


def main() -> int:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace")
    db = research.connect()

    L: list[str] = []
    L.append("=" * 92)
    L.append("检索能力压力测试")
    L.append("=" * 92)

    total_ok = 0
    total_n = 0
    body_ok = 0

    for group, cases in GROUPS.items():
        L.append("")
        L.append("#" * 92)
        L.append("# %s" % group)
        L.append("#" * 92)
        g_ok = 0
        for q, expect in cases:
            rows, note = research.search(db, q, 5)
            n = len(rows)
            total_n += 1

            if not expect:
                # 期望无结果
                verdict = "✓ 正确返回空" if n == 0 else "✗ 不该有结果却有 %d 条" % n
                ok = (n == 0)
            else:
                first_hit = 0
                for i, r in enumerate(rows, 1):
                    t = r["title"] or ""
                    s = r["snippet"] or ""
                    if any(e in t for e in expect) or any(e in s for e in expect):
                        first_hit = i
                        break
                ok = (first_hit == 1)
                if first_hit == 1:
                    verdict = "✓ 第1名相关"
                elif first_hit:
                    verdict = "~ 首个相关@%d" % first_hit
                elif n == 0:
                    verdict = "✗ 无结果"
                else:
                    verdict = "✗ 前5均不相关"

            wb = sum(1 for r in rows if (r["clen"] or 0) >= 400)
            if n:
                body_ok += 1 if wb >= 1 else 0
            if ok:
                g_ok += 1
                total_ok += 1

            L.append("  %-18s %-3d条 正文%d  %s" % (q, n, wb, verdict))
            if note:
                L.append("        (%s)" % note[:70])
            if not ok and n:
                L.append("        第1名: %s" % (rows[0]["title"] or "")[:62])
        L.append("  ── 本组: %d / %d ──" % (g_ok, len(cases)))

    L.append("")
    L.append("=" * 92)
    L.append("  总计: %d / %d 通过" % (total_ok, total_n))
    L.append("  （通过 = 第1名相关，或按要求返回空）")

    db.close()
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
