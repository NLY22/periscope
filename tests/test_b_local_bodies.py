"""The local layer must not answer with less text than it has stored.

The corpus stores whole articles - this repository's own database holds one row
with 22,680 characters - but the ranking reads `SUBSTR(content,1,400)` on
purpose, because it scores hundreds of candidates and the full column is large.
That is a reasonable trade for ranking and an unacceptable one for output:
`topic.py` handed `snippet` to the caller as `body`, so a 22,680-character
article reached the reader as 400 characters with no ellipsis and no length,
which reads as "the article is short" rather than "we truncated it".

`core.search.hydrate_bodies` fetches the real text for the rows that survive
ranking. These tests use a small in-memory corpus so they do not depend on the
1.5 GB database being present.
"""

from __future__ import annotations

import sqlite3

import pytest

from core import search as research


@pytest.fixture
def tiny_db():
    """A corpus-shaped table with the columns `hydrate_bodies` reads."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE items (id TEXT PRIMARY KEY, url TEXT, title TEXT, "
        "content TEXT)")
    long_body = "长" * 1200
    db.executemany(
        "INSERT INTO items (id, url, title, content) VALUES (?, ?, ?, ?)",
        [
            ("a", "https://x/1", "长文", long_body),
            ("b", "https://x/2", "短文", "短" * 50),
            ("c", "https://x/3", "空文", ""),
        ],
    )
    db.commit()
    return db


def test_a_long_article_is_not_truncated_to_the_snippet(tiny_db):
    rows = [
        {"url": "https://x/1", "title": "长文", "clen": 1200, "snippet": "长" * 400},
    ]
    research.hydrate_bodies(tiny_db, rows)
    assert len(rows[0]["body"]) == 1200, (
        "the body should be the stored content, not the 400-char snippet")


def test_a_short_row_keeps_its_whole_body(tiny_db):
    rows = [{"url": "https://x/2", "title": "短文", "clen": 50, "snippet": "短" * 50}]
    research.hydrate_bodies(tiny_db, rows)
    assert rows[0]["body"] == "短" * 50


def test_an_empty_body_is_not_invented(tiny_db):
    rows = [{"url": "https://x/3", "title": "空文", "clen": 0, "snippet": ""}]
    research.hydrate_bodies(tiny_db, rows)
    assert "body" not in rows[0] or rows[0]["body"] == ""


def test_the_cap_bounds_one_pathological_document(tiny_db):
    """A 22 MB row must not be copied into the result verbatim."""
    tiny_db.execute(
        "INSERT INTO items (id, url, title, content) VALUES (?, ?, ?, ?)",
        ("big", "https://x/big", "巨文", "巨" * 50000))
    tiny_db.commit()
    rows = [{"url": "https://x/big", "title": "巨文", "clen": 50000, "snippet": ""}]
    research.hydrate_bodies(tiny_db, rows, max_chars=4000)
    assert len(rows[0]["body"]) == 4000


def test_rows_without_a_url_are_left_alone(tiny_db):
    """Platform rows often have no URL; they must not crash the hydration."""
    rows = [{"url": "", "title": "无链接", "clen": 500, "snippet": "x"}]
    research.hydrate_bodies(tiny_db, rows)
    assert rows[0]["title"] == "无链接"
    assert "body" not in rows[0]


def test_a_sqlite_row_is_accepted_not_crashed():
    """The first version assigned into `r["body"]` and raised TypeError.

    `search()` returns `sqlite3.Row`, which is immutable. The hydration has to
    copy it to a dict; this pins that, because the failure was only visible on
    a real query against a real corpus.
    """
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE items (url TEXT, content TEXT)")
    db.execute("INSERT INTO items VALUES (?, ?)", ("https://x/9", "正" * 800))
    db.commit()
    rows = db.execute("SELECT url, '' AS snippet FROM items").fetchall()
    out = research.hydrate_bodies(db, rows)
    assert len(out[0]["body"]) == 800
    assert out[0]["body"] == "正" * 800


def test_hydration_does_not_change_the_ranking_pool_contract():
    """Bite proof: `search()` must still return `snippet`, not full bodies.

    If `search()` started returning whole documents, every query would read
    megabytes for rows that are then discarded.
    """
    import inspect

    src = inspect.getsource(research.search)
    assert "SUBSTR" in src.upper(), (
        "search() no longer truncates its candidate pool; a query would now "
        "read whole article bodies for hundreds of rows")
