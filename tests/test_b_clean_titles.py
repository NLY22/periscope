"""The maintenance pass that repairs titles already in the corpus.

`collector._clean` fixes titles on the way in, but a fix on ingest does not
touch what is already stored. This repository's corpus held 445 rows whose
titles carried markup when the ingest fix landed (114 escaped tags, 167 `&amp;`,
95 `&quot;`, 73 `&#39;`), and `topic.py` prints `row["title"]` verbatim.

These tests run the tool against a throwaway database, never the real corpus:
a test that rewrites 189k rows to prove a point is worse than the bug.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "clean_titles.py"


def _make_db(path: Path) -> None:
    """A corpus-shaped table with the columns the tool touches.

    Only `items.id` and `items.title` matter to this tool, and building the
    real schema here would couple the test to the store's migrations. The FTS
    triggers are exercised by the store's own tests.
    """
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE items (id TEXT PRIMARY KEY, title TEXT, content TEXT)")
    conn.executemany(
        "INSERT INTO items (id, title, content) VALUES (?, ?, ?)",
        [
            # A literal `&amp;` - the entity text, which is what the corpus
            # actually held. Escaping the ampersand in the fixture instead
            # would produce `\u0026amp;` and test nothing.
            ("a", "掌握 GPU 并行编程（硬件 &amp; 软件）", "body a"),
            ("b", "单引号 &#39; 与 &quot; 引号", "body b"),
            ("c", r"\u003cem\u003e大模型\u003c/em\u003e-视频", "body c"),
            ("d", "已经干净的标题", "body d"),
        ],
    )
    conn.commit()
    conn.close()


def _run(db: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--db", str(db), *args],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )


def _titles(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    rows = dict(conn.execute("SELECT id, title FROM items").fetchall())
    conn.close()
    return rows


def test_a_dry_run_reports_without_writing(scratch_path):
    """Rewriting a corpus because someone typed the script name is not OK."""
    db = scratch_path / "corpus.db"
    _make_db(db)
    before = _titles(db)

    proc = _run(db)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "只报告" in proc.stdout
    assert "需要改写: 3" in proc.stdout, proc.stdout[-600:]
    assert _titles(db) == before, "a dry run modified the database"


def test_apply_cleans_the_marked_rows_and_leaves_clean_ones_alone(scratch_path):
    db = scratch_path / "corpus.db"
    _make_db(db)

    proc = _run(db, "--apply")
    assert proc.returncode == 0, proc.stderr[-600:]

    titles = _titles(db)
    assert titles["a"] == "掌握 GPU 并行编程（硬件 & 软件）"
    assert titles["b"] == "单引号 ' 与 \" 引号"
    assert titles["c"] == "大模型 -视频"
    assert titles["d"] == "已经干净的标题", "a clean title must not be rewritten"


def test_apply_is_idempotent(scratch_path):
    """Second run must be a no-op, or a maintenance loop keeps rewriting rows."""
    db = scratch_path / "corpus.db"
    _make_db(db)

    assert _run(db, "--apply").returncode == 0
    first = _titles(db)

    second = _run(db, "--apply")
    assert second.returncode == 0, second.stderr[-600:]
    assert "无需改动" in second.stdout or "需要改写: 0" in second.stdout
    assert _titles(db) == first


def test_a_missing_corpus_is_reported_not_crashed(scratch_path):
    proc = _run(scratch_path / "nope.db")
    assert proc.returncode == 2
    assert "不存在" in proc.stdout


def test_the_tool_reuses_the_collectors_cleaner():
    """A second cleaning implementation would disagree with the first.

    Asserted structurally because that is the actual invariant: the tool must
    not grow its own regexes. If it did, a title the collector considers clean
    could be rewritten here, and the two would oscillate.
    """
    source = TOOL.read_text(encoding="utf-8")
    assert "from collectors.collector import _clean" in source
    # The only regexes allowed here are the LIKE patterns used to *find*
    # candidates; cleaning itself must not be implemented locally.
    assert "html.unescape" not in source
    assert "_decode_escapes" not in source


def test_the_limit_flag_bounds_the_work(scratch_path):
    """`--limit 1` must rewrite one row, not all of them.

    Counted directly: after the run, exactly two of the three dirty rows must
    still be dirty.
    """
    db = scratch_path / "corpus.db"
    _make_db(db)
    proc = _run(db, "--apply", "--limit", "1")
    assert proc.returncode == 0, proc.stderr[-600:]
    titles = _titles(db)
    still_dirty = [k for k, v in titles.items()
                   if "&amp;" in v or "&#39;" in v or "\\u003c" in v]
    assert len(still_dirty) == 2, (
        "expected exactly one row cleaned, %d still dirty: %s"
        % (len(still_dirty), still_dirty))
