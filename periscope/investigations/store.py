"""Investigation rounds and fixed evidence references."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from periscope.evidence.versions import EvidenceVersions, backup_corpus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InvestigationStore:
    def __init__(self, corpus):
        EvidenceVersions(corpus)
        self.conn = corpus._conn
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigations'").fetchone():
            backup_corpus(corpus)
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS investigations (
                id TEXT PRIMARY KEY, question TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS investigation_rounds (
                id TEXT PRIMARY KEY, investigation_id TEXT NOT NULL REFERENCES investigations(id),
                message TEXT NOT NULL, request_key TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(investigation_id, request_key)
            );
            CREATE TABLE IF NOT EXISTS investigation_round_evidence (
                round_id TEXT NOT NULL REFERENCES investigation_rounds(id),
                object_id TEXT NOT NULL REFERENCES evidence_objects(id),
                version_id TEXT NOT NULL REFERENCES evidence_versions(id),
                PRIMARY KEY(round_id, object_id, version_id)
            );
        ''')

    def create(self, question: str) -> str:
        if not question.strip():
            raise ValueError("question must not be empty")
        identifier = "inv_" + uuid4().hex
        with self.conn:
            self.conn.execute("INSERT INTO investigations VALUES (?, ?, ?)", (identifier, question.strip(), _now()))
        return identifier

    def start_round(self, investigation_id: str, message: str, request_key: str) -> str:
        if not message.strip() or not request_key.strip():
            raise ValueError("message and request key are required")
        with self.conn:
            locked = self.conn.execute("UPDATE investigations SET question=question WHERE id=?", (investigation_id,))
            if not locked.rowcount:
                raise ValueError("investigation not found")
            existing = self.conn.execute("SELECT id, message FROM investigation_rounds WHERE investigation_id=? AND request_key=?",
                                         (investigation_id, request_key)).fetchone()
            if existing:
                if existing["message"] != message.strip():
                    raise ValueError("request key already used for a different message")
                return existing["id"]
            self.conn.execute("UPDATE investigation_rounds SET status='cancelled' WHERE investigation_id=? AND status='running'",
                              (investigation_id,))
            identifier = "round_" + uuid4().hex
            self.conn.execute("INSERT INTO investigation_rounds VALUES (?, ?, ?, ?, ?, ?)",
                              (identifier, investigation_id, message.strip(), request_key, "running", _now()))
        return identifier

    def get_round(self, round_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM investigation_rounds WHERE id=?", (round_id,)).fetchone()
        return dict(row) if row else None

    def attach(self, round_id: str, *, object_id: str, version_id: str) -> None:
        with self.conn:
            cursor = self.conn.execute('''
                INSERT OR IGNORE INTO investigation_round_evidence (round_id, object_id, version_id)
                SELECT r.id, v.object_id, v.id FROM investigation_rounds r, evidence_versions v
                WHERE r.id=? AND r.status='running' AND v.id=? AND v.object_id=?
            ''', (round_id, version_id, object_id))
            if cursor.rowcount:
                return
            if not self.conn.execute("SELECT 1 FROM investigation_rounds WHERE id=? AND status='running'", (round_id,)).fetchone():
                raise ValueError("round is not active")
            if not self.conn.execute("SELECT 1 FROM evidence_versions WHERE id=? AND object_id=?", (version_id, object_id)).fetchone():
                raise ValueError("evidence version not found")

    def references(self, round_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT object_id, version_id FROM investigation_round_evidence WHERE round_id=? ORDER BY rowid",
                                 (round_id,)).fetchall()
        return [dict(row) for row in rows]
