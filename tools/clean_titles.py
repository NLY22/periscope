"""Clean markup out of titles already stored in the corpus.

Why this exists separately from the collector fix: the collector now cleans
titles on the way in, but everything collected before that fix is already in
`corpus.db`. Measured on this repository's own corpus when the fix landed:

    titles containing the literal `\\u003c`   114
    titles containing `&amp;`                 167

Both are user-visible. `topic.py` prints `row["title"]` verbatim, and the local
layer copies the stored title straight into the result row. So a reader of a
"GPU 并行编程" query saw half the list rendered as
`掌握 GPU 并行编程（硬件 &amp; 软件）`.

It is also a retrieval bug, not only a cosmetic one. The score gives a bonus
when a query term appears in the *title*; a title carrying escaped markup still
contains the term, so it keeps that bonus - and meanwhile the same story from
another outlet normalises to a different fingerprint, so one story is counted
as two independent sources.

Design notes:

- **Dry run by default.** Rewriting 100k rows because someone typed the script
  name is not an acceptable side effect.
- **Reuse `collector._clean`.** A second cleaning implementation would drift
  from the one the collector uses, and the two would disagree about which
  titles are already clean.
- **Only rewrite what changes.** A blanket `UPDATE` over every row would touch
  the FTS index for the whole corpus; this touches the rows that need it.
- **Idempotent.** Running it twice changes nothing the second time, which is
  what makes it safe to leave in a maintenance loop.

Usage:
    python tools/clean_titles.py                # report only
    python tools/clean_titles.py --apply        # rewrite the rows that differ
    python tools/clean_titles.py --apply --limit 500
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paths  # noqa: E402
from core.console import force_utf8_output  # noqa: E402

force_utf8_output()

from collectors.collector import _clean  # noqa: E402

# The shapes that indicate markup survived into storage. Kept in sync with
# `test_retrieval_quality.test_no_title_carries_raw_markup`, which asserts the
# corpus holds none of them after this script has run.
MARKERS = (
    (r"%\u003c%", "escaped tag"),
    (r"%&amp;%", "entity &amp;"),
    (r"%&quot;%", "entity &quot;"),
    (r"%&#39;%", "entity &#39;"),
    (r"%<em>%", "raw <em>"),
    (r"%</em>%", "raw </em>"),
)


def _count(conn: sqlite3.Connection, pattern: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM items WHERE title LIKE ?", (pattern,)).fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="清理语料库里标题的残留标记")
    ap.add_argument("--apply", action="store_true",
                    help="真的写库（默认只报告）")
    ap.add_argument("--limit", type=int, default=0,
                    help="最多处理多少行（0 = 全部）")
    ap.add_argument("--db", default="", help="语料库路径（默认 core.paths）")
    args = ap.parse_args()

    corpus_path = Path(args.db) if args.db else paths.CORPUS
    if not corpus_path.exists():
        print("语料库不存在: %s" % corpus_path)
        return 2

    conn = sqlite3.connect(str(corpus_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")

    print("=" * 78)
    print("标题标记清理")
    print("=" * 78)
    print("  语料库: %s" % corpus_path)
    print("  模式  : %s" % ("写入" if args.apply else "只报告（加 --apply 才写库）"))
    print()

    before = {label: _count(conn, pat) for pat, label in MARKERS}
    total_rows = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    print("  语料总行数: %d" % total_rows)
    for label, n in before.items():
        print("    %-14s %6d" % (label, n))

    # Candidate rows: anything matching any marker.
    where = " OR ".join("title LIKE ?" for _ in MARKERS)
    params = [pat for pat, _label in MARKERS]
    sql = "SELECT id, title FROM items WHERE %s" % where
    if args.limit:
        sql += " LIMIT ?"
        params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()

    changes = []
    for row in rows:
        old = row["title"] or ""
        new = _clean(old)
        if new and new != old:
            changes.append((new, row["id"], old))

    print()
    print("  需要改写: %d / %d 行" % (len(changes), len(rows)))
    for new, _rid, old in changes[:5]:
        print("    - %r" % old[:64])
        print("    + %r" % new[:64])

    if not args.apply:
        print()
        print("  未写入。确认无误后加 --apply。")
        conn.close()
        return 0

    if not changes:
        print()
        print("  无需改动。")
        conn.close()
        return 0

    # The FTS index is maintained by the store's triggers; a plain UPDATE on
    # `items` is what keeps it in step. Doing it in one transaction means a
    # failure leaves the corpus exactly as it was rather than half-cleaned.
    try:
        conn.execute("BEGIN")
        conn.executemany(
            "UPDATE items SET title=? WHERE id=?", [(n, i) for n, i, _o in changes])
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print("  写库失败，已回滚: %s: %s" % (type(exc).__name__, exc))
        conn.close()
        return 1

    after = {label: _count(conn, pat) for pat, label in MARKERS}
    print()
    print("  已改写 %d 行" % len(changes))
    for label in before:
        print("    %-14s %6d -> %6d" % (label, before[label], after[label]))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
