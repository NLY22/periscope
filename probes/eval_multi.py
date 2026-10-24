"""检索质量多角度评估。

三个维度分开测，因为一个数字看不出问题在哪：
  A. 相关性   —— 第一名是否真的在讲这个话题
  B. 内容量   —— 前 5 条里有多少是「有正文的文章」而不是标题/标签
  C. 多样性   —— 前 5 条来自几个不同的发布者（避免同一来源霸屏）

对照三套排序方案：
  1. trust     —— 按可信度（research.py 原来用的）
  2. bm25      —— 项目自带（Corpus.search）
  3. hybrid    —— 相关性 × 内容量 × 可信度（本脚本要验证的）
"""

from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths

import io
import re
import sqlite3


OUT = str(paths.REPORT_DIR / '.eval_multi.txt')
CORPUS = str(paths.CORPUS)

CASES = [
    ("人形机器人", ["人形机器人"]),
    ("固态电池", ["固态电池"]),
    ("DeepSeek", ["DeepSeek"]),
    ("英伟达", ["英伟达", "NVIDIA", "Nvidia"]),
    ("宁德时代", ["宁德时代"]),
    ("具身智能", ["具身"]),
    ("自动驾驶", ["自动驾驶", "无人驾驶"]),
    ("脑机接口", ["脑机"]),
    ("核聚变", ["核聚变", "聚变"]),
    ("AI 芯片", ["AI 芯片", "AI芯片"]),
    ("大模型 幻觉", ["幻觉"]),
    ("机器人 量产", ["量产"]),
]


def fts_phrase(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def terms_of(q: str) -> list[str]:
    return [t for t in re.split(r"\s+", q.strip()) if t]


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(CORPUS)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA temp_store=MEMORY")
    return db


# ---------------------------------------------------------------- 三种排序
def rank_trust(db, q: str, limit: int):
    expr = fts_phrase(q)
    try:
        return db.execute("""
            SELECT i.title, i.publisher, i.source_type, i.published_at,
                   i.trust, LENGTH(COALESCE(i.content,'')) clen,
                   SUBSTR(COALESCE(i.content,''),1,400) snippet
            FROM items i JOIN items_fts f ON i.rowid=f.rowid
            WHERE items_fts MATCH ?
            ORDER BY i.trust DESC, clen DESC LIMIT ?
        """, (expr, limit)).fetchall()
    except sqlite3.OperationalError:
        return []


def rank_bm25(db, q: str, limit: int):
    ts = terms_of(q)
    fts = [t for t in ts if len(t) >= 3]
    out = []
    if fts:
        match = " AND ".join(fts_phrase(t) for t in fts)
        try:
            out = db.execute("""
                SELECT i.title, i.publisher, i.source_type, i.published_at,
                       i.trust, LENGTH(COALESCE(i.content,'')) clen,
                       SUBSTR(COALESCE(i.content,''),1,400) snippet
                FROM items i JOIN items_fts f ON i.rowid=f.rowid
                WHERE items_fts MATCH ?
                ORDER BY bm25(items_fts) LIMIT ?
            """, (match, limit)).fetchall()
        except sqlite3.OperationalError:
            out = []
    if not out:
        for t in ts:
            like = "%" + t + "%"
            out = db.execute("""
                SELECT title, publisher, source_type, published_at, trust,
                       LENGTH(COALESCE(content,'')) clen,
                       SUBSTR(COALESCE(content,''),1,400) snippet
                FROM items WHERE title LIKE ? OR content LIKE ?
                ORDER BY LENGTH(COALESCE(content,'')) DESC LIMIT ?
            """, (like, like, limit)).fetchall()
            if out:
                break
    return out


def rank_hybrid(db, q: str, limit: int):
    """相关性候选池 → 用「相关性 + 内容量 + 可信度」重排。

    候选池取 BM25 的前 N 条（N 远大于 limit），这样重排有材料可选。
    打分刻意保持可解释：
      rel   查询词在标题里的命中比例（标题命中比正文命中强得多）
      body  正文长度，取对数并封顶（5,000 字以上的收益递减）
      trust 发布者可信度
    """
    ts = terms_of(q)
    fts = [t for t in ts if len(t) >= 3]
    if not fts:
        return []
    match = " AND ".join(fts_phrase(t) for t in fts)
    pool_size = max(limit * 20, 100)
    try:
        rows = db.execute("""
            SELECT i.title, i.publisher, i.source_type, i.published_at,
                   i.trust, LENGTH(COALESCE(i.content,'')) clen,
                   SUBSTR(COALESCE(i.content,''),1,400) snippet,
                   bm25(items_fts) bm
            FROM items i JOIN items_fts f ON i.rowid=f.rowid
            WHERE items_fts MATCH ?
            ORDER BY bm25(items_fts) LIMIT ?
        """, (match, pool_size)).fetchall()
    except sqlite3.OperationalError:
        return []
    if not rows:
        return []

    import math

    scored = []
    for r in rows:
        title = r["title"] or ""
        # 标题命中比例
        hit = sum(1 for t in ts if t in title)
        rel = hit / max(len(ts), 1)
        # 正文量：对数封顶
        clen = r["clen"] or 0
        body = min(math.log1p(clen) / math.log1p(5000), 1.0)
        trust = r["trust"] or 0.5
        # bm25 是负值，越负越相关；归一化成 0..1
        bm = r["bm"] or 0
        score = rel * 0.55 + body * 0.25 + trust * 0.20
        scored.append((score, rel, body, bm, r))

    scored.sort(key=lambda x: -x[0])
    return [s[4] for s in scored[:limit]]


# ---------------------------------------------------------------- 评估
def evaluate(rows, expect) -> tuple[bool, int, int, int]:
    """返回 (第一名相关, 首个相关位次, 有正文条数, 独立发布者数)。"""
    if not rows:
        return False, 0, 0, 0
    first_rank = 0
    with_body = 0
    pubs = set()
    for i, r in enumerate(rows, 1):
        t = r["title"] or ""
        snip = r["snippet"] or ""
        if (r["clen"] or 0) >= 400:
            with_body += 1
        pubs.add((r["publisher"] or "")[:40])
        if not first_rank and (any(e in t for e in expect) or any(e in snip for e in expect)):
            first_rank = i
    return (first_rank == 1), first_rank, with_body, len(pubs)


def main() -> int:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace")
    db = connect()

    L: list[str] = []
    L.append("=" * 92)
    L.append("检索质量多角度评估")
    L.append("=" * 92)

    rankers = [
        ("trust", rank_trust),
        ("bm25", rank_bm25),
        ("hybrid", rank_hybrid),
    ]
    summary = {}

    for name, fn in rankers:
        top1 = body5 = uniq5 = firstsum = 0
        L.append("")
        L.append("#" * 92)
        L.append("# 排序方案: %s" % name)
        L.append("#" * 92)
        for q, expect in CASES:
            rows = fn(db, q, 5)
            ok1, first, wb, up = evaluate(rows, expect)
            top1 += 1 if ok1 else 0
            body5 += wb
            uniq5 += up
            firstsum += first if first else 99
            L.append("  %-14s %s  首个相关@%-3s 有正文 %d/5  发布者 %d/5"
                     % (q, "✓" if ok1 else "·", first or "-", wb, up))
            if not ok1 and rows:
                L.append("      第1名: %s" % (rows[0]["title"] or "")[:60])
        n = len(CASES)
        summary[name] = (top1, body5 / n, uniq5 / n, firstsum / n)
        L.append("")
        L.append("  ── %s 小结 ──" % name)
        L.append("    第一名相关   : %d / %d" % (top1, n))
        L.append("    前5条平均有正文: %.1f / 5" % (body5 / n))
        L.append("    前5条平均发布者: %.1f / 5" % (uniq5 / n))
        L.append("    首个相关平均位次: %.1f" % (firstsum / n))

    L.append("")
    L.append("=" * 92)
    L.append("三方案对比")
    L.append("=" * 92)
    L.append("  %-8s %-12s %-14s %-14s %s" % (
        "方案", "第1名相关", "前5有正文均", "前5发布者均", "首个相关位次均"))
    for name, (t1, b5, u5, fs) in summary.items():
        L.append("  %-8s %-12s %-14s %-14s %.1f" % (
            name, "%d/%d" % (t1, len(CASES)), "%.1f" % b5, "%.1f" % u5, fs))

    db.close()
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
