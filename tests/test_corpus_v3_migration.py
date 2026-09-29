"""Schema v3: locator/time_basis/sections_json, backfilled on old databases."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.corpus.store import SCHEMA_VERSION, Corpus
from src.models import ContentItem, Section, SourceType

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)

V2_SCHEMA = """
CREATE TABLE items (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE, source_type TEXT NOT NULL, title TEXT NOT NULL,
    url TEXT NOT NULL, author TEXT, published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
    claimable TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}', fingerprint INTEGER NOT NULL,
    cluster_id TEXT, run_id INTEGER
);
CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
    finished_at TEXT, since TEXT NOT NULL, items_new INTEGER NOT NULL DEFAULT 0,
    items_total_seen INTEGER NOT NULL DEFAULT 0, note TEXT);
"""


def make(idx: str, **overrides) -> ContentItem:
    base = dict(
        id=f"v3:{idx}",
        source_type=SourceType.DISCOURSE,
        title=f"title {idx}",
        url=f"https://forum.test/t/{idx}",
        content="author body",
        published_at=NOW,
        fetched_at=NOW,
    )
    base.update(overrides)
    return ContentItem(**base)


def test_schema_version_is_three(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        assert SCHEMA_VERSION == 3
        assert corpus._conn.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        corpus.close()


def test_new_columns_exist_on_a_fresh_database(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        cols = {r["name"] for r in corpus._conn.execute("PRAGMA table_info(items)")}
        assert {"locator", "time_basis", "sections_json"} <= cols
    finally:
        corpus.close()


def test_add_items_writes_locator_time_basis_and_sections(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        corpus.add_items([make("1", sections=[
            Section(tier="primary", text="the author says X"),
            Section(tier="community", text="a stranger says Y", author="s", locator="#2"),
        ])])
        row = corpus._conn.execute(
            "SELECT locator, time_basis, sections_json, claimable FROM items WHERE id='v3:1'"
        ).fetchone()
        assert row["locator"] == "https://forum.test/t/1"
        assert row["time_basis"] == "published"
        stored = json.loads(row["sections_json"])
        assert [s["tier"] for s in stored] == ["primary", "community"]
        assert stored[1]["locator"] == "#2"
        assert row["claimable"] == "the author says X"
    finally:
        corpus.close()


def test_url_less_item_is_stored_under_its_locator(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        corpus.add_items([make("nourl", url=None, locator="xhs:note:abc")])
        row = corpus._conn.execute(
            "SELECT url, locator FROM items WHERE id='v3:nourl'"
        ).fetchone()
        assert row["url"] == "xhs:note:abc"
        assert row["locator"] == "xhs:note:abc"
    finally:
        corpus.close()


def test_time_basis_unknown_is_persisted(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        corpus.add_items([make("notime", published_at=None)])
        row = corpus._conn.execute(
            "SELECT time_basis, published_at FROM items WHERE id='v3:notime'"
        ).fetchone()
        assert row["time_basis"] == "unknown"
        assert row["published_at"] == NOW.isoformat()
    finally:
        corpus.close()


def test_v2_database_is_backfilled_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(V2_SCHEMA)
    conn.execute(
        """INSERT INTO items (id, source_type, title, url, published_at, fetched_at,
                              content, claimable, fingerprint)
           VALUES ('legacy:1','v2ex','Old','https://e.com/1','2026-09-01T00:00:00+00:00',
                   '2026-09-01T00:00:00+00:00',?,?,0)""",
        ("author body\n\n【评论区 Top】\ncrowd body", "author body"),
    )
    conn.commit()
    conn.close()

    migrated = Corpus(path)
    try:
        row = migrated._conn.execute(
            "SELECT locator, time_basis, sections_json, claimable FROM items WHERE id='legacy:1'"
        ).fetchone()
        assert row["locator"] == "https://e.com/1"
        assert row["time_basis"] == "published"
        sections = json.loads(row["sections_json"])
        assert [(s["tier"], s["provenance"]) for s in sections] == [
            ("primary", "legacy_marker"),
            ("community", "legacy_marker"),
        ]
        assert row["claimable"] == "author body"
    finally:
        migrated.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(V2_SCHEMA)
    conn.execute(
        """INSERT INTO items (id, source_type, title, url, published_at, fetched_at,
                              content, claimable, fingerprint)
           VALUES ('legacy:2','v2ex','Old','https://e.com/2','2026-09-01T00:00:00+00:00',
                   '2026-09-01T00:00:00+00:00','body','body',0)"""
    )
    conn.commit()
    conn.close()

    first = Corpus(path)
    first.close()
    second = Corpus(path)
    try:
        assert second._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        assert second._conn.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        second.close()


def test_row_to_dict_exposes_parsed_sections(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        corpus.add_items([make("1", sections=[Section(tier="primary", text="x")])])
        rows = corpus.recent(limit=5)
        assert rows[0]["sections"][0]["text"] == "x"
        assert "sections_json" not in rows[0]
    finally:
        corpus.close()


def test_known_ids_reports_only_rows_already_stored(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        corpus.add_items([make("1")])
        assert corpus.known_ids(["v3:1", "v3:2"]) == {"v3:1"}
        assert corpus.known_ids([]) == set()
    finally:
        corpus.close()
