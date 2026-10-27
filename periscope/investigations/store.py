"""Investigation rounds and fixed evidence references."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import uuid4

from periscope.evidence.versions import EvidenceVersions, backup_corpus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InvestigationStore:
    def __init__(self, corpus):
        EvidenceVersions(corpus)
        self.conn = corpus._conn
        existing = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigations'").fetchone()
        events_exist = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_events'").fetchone()
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(investigation_rounds)")}
        if not existing or not events_exist or not {"error", "options_json"} <= columns:
            backup_corpus(corpus)
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS investigations (
                id TEXT PRIMARY KEY, question TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS investigation_rounds (
                id TEXT PRIMARY KEY, investigation_id TEXT NOT NULL REFERENCES investigations(id),
                message TEXT NOT NULL, request_key TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', options_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(investigation_id, request_key)
            );
            CREATE TABLE IF NOT EXISTS investigation_round_evidence (
                round_id TEXT NOT NULL REFERENCES investigation_rounds(id),
                object_id TEXT NOT NULL REFERENCES evidence_objects(id),
                version_id TEXT NOT NULL REFERENCES evidence_versions(id),
                PRIMARY KEY(round_id, object_id, version_id)
            );
            CREATE TABLE IF NOT EXISTS investigation_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, round_id TEXT NOT NULL REFERENCES investigation_rounds(id),
                object_id TEXT NOT NULL, version_id TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_investigation_events_round ON investigation_events(round_id, seq);
        ''')
        current_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(investigation_rounds)")}
        if "error" not in current_columns:
            self.conn.execute("ALTER TABLE investigation_rounds ADD COLUMN error TEXT NOT NULL DEFAULT ''")
        if "options_json" not in current_columns:
            self.conn.execute("ALTER TABLE investigation_rounds ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'")
        self.conn.commit()

    def create(self, question: str) -> str:
        if not question.strip():
            raise ValueError("question must not be empty")
        identifier = "inv_" + uuid4().hex
        with self.conn:
            self.conn.execute("INSERT INTO investigations VALUES (?, ?, ?)", (identifier, question.strip(), _now()))
        return identifier

    def list_investigations(self, limit: int = 30) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM investigations ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                 (max(1, min(limit, 100)),)).fetchall()
        return [dict(row) for row in rows]

    def investigation(self, investigation_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM investigations WHERE id=?", (investigation_id,)).fetchone()
        if row is None:
            return None
        rounds = self.conn.execute("SELECT id FROM investigation_rounds WHERE investigation_id=? ORDER BY created_at, rowid LIMIT 100",
                                   (investigation_id,)).fetchall()
        return {"investigation": dict(row), "rounds": [self.get_round(item["id"]) for item in rounds]}

    def start_round(self, investigation_id: str, message: str, request_key: str, options: dict | None = None) -> str:
        if not message.strip() or not request_key.strip():
            raise ValueError("message and request key are required")
        options_json = json.dumps(options or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self.conn:
            locked = self.conn.execute("UPDATE investigations SET question=question WHERE id=?", (investigation_id,))
            if not locked.rowcount:
                raise ValueError("investigation not found")
            existing = self.conn.execute("SELECT id, message, options_json FROM investigation_rounds WHERE investigation_id=? AND request_key=?",
                                         (investigation_id, request_key)).fetchone()
            if existing:
                if existing["message"] != message.strip() or existing["options_json"] != options_json:
                    raise ValueError("request key already used for a different message")
                return existing["id"]
            self.conn.execute("UPDATE investigation_rounds SET status='cancelled' WHERE investigation_id=? AND status='running'",
                              (investigation_id,))
            identifier = "round_" + uuid4().hex
            self.conn.execute("INSERT INTO investigation_rounds (id, investigation_id, message, request_key, status, created_at, options_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (identifier, investigation_id, message.strip(), request_key, "running", _now(), options_json))
        return identifier

    def get_round(self, round_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM investigation_rounds WHERE id=?", (round_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["options"] = json.loads(result.pop("options_json"))
        return result

    def finish_round(self, round_id: str, status: str, error: str = "") -> bool:
        if status not in {"completed", "partial", "failed", "cancelled"}:
            raise ValueError("invalid final status")
        with self.conn:
            cursor = self.conn.execute("UPDATE investigation_rounds SET status=?, error=? WHERE id=? AND status='running'",
                                       (status, error, round_id))
        return bool(cursor.rowcount)

    def interrupt_abandoned_rounds(self) -> int:
        with self.conn:
            cursor = self.conn.execute("UPDATE investigation_rounds SET status='interrupted', error='backend_restart' WHERE status='running'")
        return cursor.rowcount

    def attach(self, round_id: str, *, object_id: str, version_id: str, recover: bool = False) -> None:
        with self.conn:
            statuses = "('running', 'interrupted', 'failed')" if recover else "('running')"
            cursor = self.conn.execute('''
                INSERT OR IGNORE INTO investigation_round_evidence (round_id, object_id, version_id)
                SELECT r.id, v.object_id, v.id FROM investigation_rounds r, evidence_versions v
                WHERE r.id=? AND r.status IN ''' + statuses + ''' AND v.id=? AND v.object_id=?
            ''', (round_id, version_id, object_id))
            if cursor.rowcount:
                self.conn.execute("INSERT INTO investigation_events (round_id, object_id, version_id, created_at) VALUES (?, ?, ?, ?)",
                                  (round_id, object_id, version_id, _now()))
                return
            if not self.conn.execute("SELECT 1 FROM investigation_rounds WHERE id=? AND status IN " + statuses, (round_id,)).fetchone():
                raise ValueError("round is not active")
            if not self.conn.execute("SELECT 1 FROM evidence_versions WHERE id=? AND object_id=?", (version_id, object_id)).fetchone():
                raise ValueError("evidence version not found")

    def references(self, round_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT object_id, version_id FROM investigation_round_evidence WHERE round_id=? ORDER BY rowid",
                                 (round_id,)).fetchall()
        return [dict(row) for row in rows]

    def events(self, round_id: str, *, after: int = 0, limit: int = 100) -> list[dict]:
        rows = self.conn.execute('''SELECT seq, object_id, version_id, created_at FROM investigation_events
                                    WHERE round_id=? AND seq>? ORDER BY seq LIMIT ?''',
                                 (round_id, max(0, after), max(1, min(limit, 500)))).fetchall()
        return [{"seq": row["seq"], "type": "evidence", "created_at": row["created_at"],
                 "references": [{"object_id": row["object_id"], "version_id": row["version_id"]}]}
                for row in rows]
