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
        answers_exist = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_answers'").fetchone()
        batches_exist = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_batches'").fetchone()
        results_exist = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_collection_results'").fetchone()
        reuse_exists = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_reuse'").fetchone()
        refresh_exists = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_refresh_events'").fetchone()
        initial_exists = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_initial_events'").fetchone()
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(investigation_rounds)")}
        if not existing or not events_exist or not answers_exist or not batches_exist or not results_exist or not reuse_exists or not refresh_exists or not initial_exists or not {"error", "options_json"} <= columns:
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
            CREATE TABLE IF NOT EXISTS investigation_answers (
                round_id TEXT PRIMARY KEY REFERENCES investigation_rounds(id), result_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS investigation_batches (
                round_id TEXT NOT NULL REFERENCES investigation_rounds(id), batch_id TEXT NOT NULL,
                PRIMARY KEY(round_id, batch_id)
            );
            CREATE TABLE IF NOT EXISTS investigation_collection_results (
                round_id TEXT PRIMARY KEY REFERENCES investigation_rounds(id), status TEXT NOT NULL, reason TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS investigation_reuse (
                round_id TEXT NOT NULL REFERENCES investigation_rounds(id), object_id TEXT NOT NULL,
                version_id TEXT NOT NULL, origin_round_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                coverage_json TEXT NOT NULL, PRIMARY KEY(round_id, object_id, version_id)
            );
            CREATE TABLE IF NOT EXISTS investigation_refresh_events (
                seq INTEGER PRIMARY KEY REFERENCES investigation_events(seq), round_id TEXT NOT NULL,
                batch_id TEXT NOT NULL, object_id TEXT NOT NULL, version_id TEXT NOT NULL,
                UNIQUE(round_id, batch_id, object_id, version_id)
            );
            CREATE TABLE IF NOT EXISTS investigation_initial_events (
                seq INTEGER PRIMARY KEY REFERENCES investigation_events(seq), batch_id TEXT NOT NULL
            );
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

    def attach(self, round_id: str, *, object_id: str, version_id: str, recover: bool = False, batch_id: str | None = None) -> None:
        with self.conn:
            statuses = "('running', 'interrupted', 'failed')" if recover else "('running')"
            cursor = self.conn.execute('''
                INSERT OR IGNORE INTO investigation_round_evidence (round_id, object_id, version_id)
                SELECT r.id, v.object_id, v.id FROM investigation_rounds r, evidence_versions v
                WHERE r.id=? AND r.status IN ''' + statuses + ''' AND v.id=? AND v.object_id=?
            ''', (round_id, version_id, object_id))
            if cursor.rowcount:
                event = self.conn.execute("INSERT INTO investigation_events (round_id, object_id, version_id, created_at) VALUES (?, ?, ?, ?)",
                                          (round_id, object_id, version_id, _now()))
                if batch_id:
                    self.conn.execute("INSERT INTO investigation_initial_events VALUES (?, ?)", (event.lastrowid, batch_id))
                return
            if not self.conn.execute("SELECT 1 FROM investigation_rounds WHERE id=? AND status IN " + statuses, (round_id,)).fetchone():
                raise ValueError("round is not active")
            if not self.conn.execute("SELECT 1 FROM evidence_versions WHERE id=? AND object_id=?", (version_id, object_id)).fetchone():
                raise ValueError("evidence version not found")
            if batch_id and not self.conn.execute('''SELECT 1 FROM investigation_initial_events i
                JOIN investigation_events e ON e.seq=i.seq WHERE e.round_id=? AND i.batch_id=? AND e.object_id=? AND e.version_id=?''',
                (round_id, batch_id, object_id, version_id)).fetchone() and not self.conn.execute('''
                SELECT 1 FROM investigation_refresh_events WHERE round_id=? AND batch_id=? AND object_id=? AND version_id=?''',
                (round_id, batch_id, object_id, version_id)).fetchone():
                event = self.conn.execute("INSERT INTO investigation_events (round_id, object_id, version_id, created_at) VALUES (?, ?, ?, ?)",
                                          (round_id, object_id, version_id, _now()))
                self.conn.execute("INSERT INTO investigation_refresh_events VALUES (?, ?, ?, ?, ?)",
                                  (event.lastrowid, round_id, batch_id, object_id, version_id))

    def references(self, round_id: str) -> list[dict]:
        rows = self.conn.execute('''SELECT r.object_id, r.version_id FROM investigation_round_evidence r
            WHERE r.round_id=? ORDER BY COALESCE((SELECT MAX(o.observed_at) FROM evidence_observations o
                JOIN investigation_batches b ON b.batch_id=o.batch_id
                WHERE b.round_id=r.round_id AND o.object_id=r.object_id AND o.version_id=r.version_id), ''), r.rowid''',
            (round_id,)).fetchall()
        return [dict(row) for row in rows]

    def save_answer(self, round_id: str, answer: dict) -> bool:
        with self.conn:
            cursor = self.conn.execute('''INSERT OR REPLACE INTO investigation_answers(round_id, result_json)
                SELECT id, ? FROM investigation_rounds WHERE id=? AND status='running' ''',
                (json.dumps(answer, ensure_ascii=False), round_id))
        return bool(cursor.rowcount)

    def save_collection_result(self, round_id: str, status: str, reason: str) -> None:
        with self.conn:
            self.conn.execute('''INSERT OR REPLACE INTO investigation_collection_results(round_id, status, reason)
                SELECT id, ?, ? FROM investigation_rounds WHERE id=? AND status='running' ''', (status, reason, round_id))

    def collection_result(self, round_id: str) -> dict | None:
        row = self.conn.execute("SELECT status, reason FROM investigation_collection_results WHERE round_id=?", (round_id,)).fetchone()
        return dict(row) if row else None

    def attach_batch(self, round_id: str, batch_id: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO investigation_batches VALUES (?, ?)", (round_id, batch_id))

    def coverage(self, round_id: str, reference: dict) -> dict:
        row = self.conn.execute('''SELECT o.provenance_json FROM evidence_observations o
            JOIN investigation_batches b ON b.batch_id=o.batch_id
            WHERE b.round_id=? AND o.object_id=? AND o.version_id=?
            ORDER BY o.observed_at DESC, o.batch_id DESC LIMIT 1''',
            (round_id, reference["object_id"], reference["version_id"])).fetchone()
        if row:
            return json.loads(row[0]).get("discussion_coverage") or {}
        row = self.conn.execute('''SELECT coverage_json FROM investigation_reuse
            WHERE round_id=? AND object_id=? AND version_id=?''',
            (round_id, reference["object_id"], reference["version_id"])).fetchone()
        return json.loads(row[0]) if row else {}

    def attach_reused(self, round_id: str, item: dict) -> None:
        with self.conn:
            active = self.conn.execute("SELECT 1 FROM investigation_rounds WHERE id=? AND status='running'", (round_id,)).fetchone()
            if not active:
                return
            self.conn.execute("INSERT OR IGNORE INTO investigation_reuse VALUES (?, ?, ?, ?, ?, ?)",
                (round_id, item["object_id"], item["version_id"], item["round_id"], item["observed_at"],
                 json.dumps(item["coverage"], ensure_ascii=False)))
            cursor = self.conn.execute("INSERT OR IGNORE INTO investigation_round_evidence VALUES (?, ?, ?)",
                (round_id, item["object_id"], item["version_id"]))
            if cursor.rowcount:
                self.conn.execute("INSERT INTO investigation_events (round_id, object_id, version_id, created_at) VALUES (?, ?, ?, ?)",
                    (round_id, item["object_id"], item["version_id"], _now()))

    def origin(self, round_id: str, reference: dict) -> dict:
        current = self.conn.execute('''SELECT MAX(o.observed_at) FROM evidence_observations o
            JOIN investigation_batches b ON b.batch_id=o.batch_id WHERE b.round_id=? AND o.object_id=? AND o.version_id=?''',
            (round_id, reference["object_id"], reference["version_id"])).fetchone()[0]
        if current:
            investigation = self.get_round(round_id)["investigation_id"]
            return {"kind": "collected", "investigation_id": investigation, "round_id": round_id, "observed_at": current}
        row = self.conn.execute('''SELECT u.origin_round_id, u.observed_at, r.investigation_id FROM investigation_reuse u
            JOIN investigation_rounds r ON r.id=u.origin_round_id
            WHERE u.round_id=? AND u.object_id=? AND u.version_id=?''',
            (round_id, reference["object_id"], reference["version_id"])).fetchone()
        return {"kind": "reused", "investigation_id": row[2], "round_id": row[0], "observed_at": row[1]} if row else {"kind": "unknown"}

    def answer(self, round_id: str) -> dict | None:
        row = self.conn.execute("SELECT result_json FROM investigation_answers WHERE round_id=?", (round_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def context(self, round_id: str) -> dict:
        row = self.conn.execute('''SELECT i.question, r.investigation_id FROM investigation_rounds r
            JOIN investigations i ON i.id=r.investigation_id WHERE r.id=?''', (round_id,)).fetchone()
        if row is None:
            raise ValueError("round not found")
        messages = self.conn.execute('''SELECT message FROM investigation_rounds
            WHERE investigation_id=? AND rowid < (SELECT rowid FROM investigation_rounds WHERE id=?)
            ORDER BY rowid DESC''', (row["investigation_id"], round_id)).fetchall()
        return {"main_question": row["question"], "previous_messages": [m["message"] for m in reversed(messages)]}

    def events(self, round_id: str, *, after: int = 0, limit: int = 100) -> list[dict]:
        rows = self.conn.execute('''SELECT seq, object_id, version_id, created_at FROM investigation_events
                                    WHERE round_id=? AND seq>? ORDER BY seq LIMIT ?''',
                                 (round_id, max(0, after), max(1, min(limit, 500)))).fetchall()
        events = []
        for row in rows:
            reference = {"object_id": row["object_id"], "version_id": row["version_id"]}
            reused = self.conn.execute('''SELECT u.origin_round_id, u.observed_at, r.investigation_id FROM investigation_reuse u
                JOIN investigation_rounds r ON r.id=u.origin_round_id
                WHERE u.round_id=? AND u.object_id=? AND u.version_id=?''',
                (round_id, reference["object_id"], reference["version_id"])).fetchone()
            refresh = self.conn.execute('''SELECT o.observed_at FROM investigation_refresh_events f
                JOIN evidence_observations o ON o.batch_id=f.batch_id AND o.object_id=f.object_id AND o.version_id=f.version_id
                WHERE f.seq=?''', (row["seq"],)).fetchone()
            initial = self.conn.execute('''SELECT o.observed_at FROM investigation_initial_events i
                JOIN evidence_observations o ON o.batch_id=i.batch_id AND o.object_id=? AND o.version_id=?
                WHERE i.seq=?''', (reference["object_id"], reference["version_id"], row["seq"])).fetchone()
            event_origin = ({"kind": "collected", "investigation_id": self.get_round(round_id)["investigation_id"],
                             "round_id": round_id, "observed_at": refresh[0]} if refresh else
                            {"kind": "collected", "investigation_id": self.get_round(round_id)["investigation_id"],
                             "round_id": round_id, "observed_at": initial[0]} if initial else
                            {"kind": "reused", "investigation_id": reused[2], "round_id": reused[0], "observed_at": reused[1]}
                            if reused else self.origin(round_id, reference))
            events.append({"seq": row["seq"], "type": "evidence_refreshed" if refresh else "evidence", "created_at": row["created_at"],
                           "references": [reference], "origin": event_origin})
        return events
