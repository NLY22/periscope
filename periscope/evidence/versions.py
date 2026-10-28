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


def backup_corpus(corpus) -> None:
    folder = Path(corpus.path).resolve().parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    backup = sqlite3.connect(str(folder / ("before-evidence-" + uuid4().hex + ".db")))
    try:
        corpus._conn.backup(backup)
    finally:
        backup.close()


class EvidenceVersions:
    """Additive version tables alongside the existing searchable corpus."""

    def __init__(self, corpus):
        self.conn = corpus._conn
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_objects'").fetchone():
            backup_corpus(corpus)
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
            def record(source, kind, identity, locator, snapshot, provenance, original_hash=""):
                object_id = _id("obj_", source + "\0" + kind + "\0" + identity)
                encoded = _json(snapshot)
                version_id = _id("ver_", object_id + "\0" + encoded)
                self.conn.execute("INSERT OR IGNORE INTO evidence_objects VALUES (?, ?, ?, ?)",
                                  (object_id, source, kind, locator))
                self.conn.execute("INSERT OR IGNORE INTO evidence_versions VALUES (?, ?, ?)",
                                  (version_id, object_id, encoded))
                self.conn.execute("INSERT OR IGNORE INTO evidence_observations VALUES (?, ?, ?, ?, ?, ?)",
                                  (batch["batch_id"], object_id, version_id, batch["created_at"],
                                   _json(provenance), original_hash))
                references.append({"object_id": object_id, "version_id": version_id})

            for row in batch["rows"]:
                locator = canonical_url(row.get("url") or "")
                if not locator:
                    continue
                provenance = row.get("provenance") or {}
                if row.get("source") == "web_crawl":
                    document = dict(row.get("document") or {})
                    original_hash = document.pop("sha256", "")
                    snapshot = {"title": row.get("title") or "", "text": row.get("text") or row.get("body") or "",
                                "author": row.get("author") or "", "published_at": row.get("date") or "",
                                "url": locator, "document": document}
                    record("web_crawl", "document", locator, locator, snapshot, provenance, original_hash)
                elif row.get("source") == "v2ex_search" and (row.get("topic_detail") or {}).get("topic_id"):
                    provenance = {**provenance, "discussion_coverage": row.get("discussion_coverage") or {}}
                    topic_id = str(row["topic_detail"]["topic_id"])
                    snapshot = {"type": "topic", "native_id": topic_id, "url": locator,
                                "title": row.get("title") or "", "text": row.get("text") or "",
                                "author": row.get("author") or "", "published_at": row["topic_detail"].get("created_at"),
                                "content_level": row["topic_detail"].get("content_level") or "full"}
                    if row["topic_detail"].get("available", True):
                        record("v2ex", "topic", topic_id, locator, snapshot, provenance)
                    for comment in row.get("comments") or []:
                        if not isinstance(comment, dict) or not comment.get("id") or not comment.get("text"):
                            continue
                        reply_id = str(comment["id"])
                        reply = {"type": "reply", "native_id": reply_id, "parent_native_id": topic_id,
                                 "text": comment["text"], "author": comment.get("user") or "",
                                 "published_at": comment.get("created_at"), "url": comment.get("url") or locator}
                        record("v2ex", "reply", reply_id, reply["url"], reply, provenance)
                elif row.get("source") == "hn_ai" and (row.get("topic_detail") or {}).get("story_id"):
                    provenance = {**provenance, "discussion_coverage": row.get("discussion_coverage") or {}}
                    story_id = str(row["topic_detail"]["story_id"])
                    snapshot = {"type": "story", "native_id": story_id, "url": locator,
                                "title": row.get("title") or "", "text": row.get("text") or "",
                                "author": row.get("author") or "", "published_at": row.get("date") or "",
                                "external_url": row["topic_detail"].get("external_url") or "",
                                "content_level": row["topic_detail"].get("content_level") or "full"}
                    record("hackernews", "story", story_id, locator, snapshot, provenance)
                    pending = list(row.get("comments") or [])
                    while pending:
                        comment = pending.pop()
                        if not isinstance(comment, dict):
                            continue
                        pending.extend(comment.get("replies") or [])
                        if not comment.get("id") or not comment.get("text"):
                            continue
                        reply_id = str(comment["id"])
                        reply = {"type": "reply", "native_id": reply_id,
                                 "parent_native_id": str(comment.get("parent_id") or story_id),
                                 "text": comment["text"], "author": comment.get("user") or "",
                                 "published_at": comment.get("created_at"), "url": comment.get("url") or locator}
                        record("hackernews", "reply", reply_id, reply["url"], reply, provenance)
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
