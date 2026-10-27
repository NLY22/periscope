"""Versioned snapshots and observations for collected public web documents."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from periscope.search.identity import canonical_url


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()


class EvidenceVersions:
    """Additive version tables alongside the existing searchable corpus."""

    def __init__(self, corpus):
        self.conn = corpus._conn
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_objects'").fetchone():
            folder = Path(corpus.path).resolve().parent / "backups"
            folder.mkdir(parents=True, exist_ok=True)
            backup = sqlite3.connect(str(folder / ("before-evidence-" + uuid4().hex + ".db")))
            try:
                self.conn.backup(backup)
            finally:
                backup.close()
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS evidence_objects (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, locator TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_versions (
                id TEXT PRIMARY KEY, object_id TEXT NOT NULL REFERENCES evidence_objects(id),
                snapshot_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_observations (
                batch_id TEXT NOT NULL, object_id TEXT NOT NULL REFERENCES evidence_objects(id),
                version_id TEXT NOT NULL REFERENCES evidence_versions(id), observed_at TEXT NOT NULL,
                provenance_json TEXT NOT NULL, document_sha256 TEXT NOT NULL,
                PRIMARY KEY(batch_id, object_id, version_id)
            );
        ''')

    def record_batch(self, batch: dict) -> list[dict]:
        references = []
        with self.conn:
            for row in batch["rows"]:
                if row.get("source") != "web_crawl":
                    continue
                locator = canonical_url(row.get("url") or "")
                if not locator:
                    continue
                object_id = _id("obj_", "web_crawl\0document\0" + locator)
                document = dict(row.get("document") or {})
                original_hash = document.pop("sha256", "")
                snapshot = {"title": row.get("title") or "", "text": row.get("text") or row.get("body") or "",
                            "author": row.get("author") or "", "published_at": row.get("date") or "",
                            "url": locator, "document": document}
                encoded = _json(snapshot)
                version_id = _id("ver_", object_id + "\0" + encoded)
                self.conn.execute("INSERT OR IGNORE INTO evidence_objects VALUES (?, ?, ?, ?)",
                                  (object_id, "web_crawl", "document", locator))
                self.conn.execute("INSERT OR IGNORE INTO evidence_versions VALUES (?, ?, ?)",
                                  (version_id, object_id, encoded))
                self.conn.execute("INSERT OR IGNORE INTO evidence_observations VALUES (?, ?, ?, ?, ?, ?)",
                                  (batch["batch_id"], object_id, version_id, batch["created_at"],
                                   _json(row.get("provenance") or {}), original_hash))
                references.append({"object_id": object_id, "version_id": version_id})
        return references

    def get_version(self, object_id: str, version_id: str) -> dict | None:
        row = self.conn.execute("SELECT snapshot_json FROM evidence_versions WHERE object_id=? AND id=?",
                                (object_id, version_id)).fetchone()
        return json.loads(row[0]) if row else None

    def observations(self, object_id: str, version_id: str | None = None, limit: int = 50) -> list[dict]:
        condition = " AND version_id=?" if version_id is not None else ""
        params = (object_id, version_id, limit) if version_id is not None else (object_id, limit)
        rows = self.conn.execute("SELECT * FROM evidence_observations WHERE object_id=?" + condition +
                                 " ORDER BY observed_at DESC, batch_id DESC LIMIT ?", params).fetchall()
        result = []
        for row in rows:
            observation = dict(row)
            observation["provenance"] = json.loads(observation.pop("provenance_json"))
            result.append(observation)
        return result
