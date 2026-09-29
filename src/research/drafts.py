"""The research document as a versioned artefact, not a live projection.

`render_report()` is a pure function of session state, so any change to the
evidence silently rewrites the whole document and a user's own edits have
nowhere to live. A draft is stored per revision instead:

  origin="render"     produced by the system
  origin="user_edit"  produced by the user; the system never overwrites it
  origin="merge"      the system folded a user edit into a fresh render

When an upstream dependency changes but the section is locked or user-edited,
the section is marked `stale` and left alone. That is the non-destructive
counterpart of `RunStore.invalidate_after`, which deletes downstream files.

Citation numbers are per-revision and may be re-ordered; `item_id` is the
stable handle, which is why the mapping lives on the section, not in the text.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ORIGINS = ("render", "user_edit", "merge")
REQUEST_KINDS = ("clarify", "confirm_claim", "choose_scope", "supply_source")
REQUEST_STATUSES = ("open", "answered", "skipped")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def body_hash(body: str) -> str:
    """Content fingerprint used to detect a section that moved on its own."""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()[:16]


@dataclass
class DraftSection:
    id: str
    title: str
    body: str
    evidence_ids: List[str] = field(default_factory=list)
    subquestion_id: Optional[str] = None
    locked: bool = False
    hash: str = ""
    stale: bool = False
    verdicts: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.hash:
            self.hash = body_hash(self.body)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "evidence_ids": list(self.evidence_ids),
            "subquestion_id": self.subquestion_id,
            "locked": self.locked,
            "hash": self.hash,
            "stale": self.stale,
            "verdicts": dict(self.verdicts),
        }

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "DraftSection":
        return DraftSection(
            id=raw["id"],
            title=raw.get("title", ""),
            body=raw.get("body", ""),
            evidence_ids=list(raw.get("evidence_ids") or []),
            subquestion_id=raw.get("subquestion_id"),
            locked=bool(raw.get("locked", False)),
            hash=raw.get("hash", ""),
            stale=bool(raw.get("stale", False)),
            verdicts=dict(raw.get("verdicts") or {}),
        )


@dataclass
class Draft:
    id: str
    session_id: str
    revision: int
    origin: str
    sections: List[DraftSection]
    created_at: datetime

    def section(self, section_id: str) -> Optional[DraftSection]:
        return next((s for s in self.sections if s.id == section_id), None)

    def markdown(self) -> str:
        parts: List[str] = []
        for section in self.sections:
            marker = " _(stale: 上游证据已变化，未自动覆盖)_" if section.stale else ""
            parts.append(f"## {section.title}{marker}\n\n{section.body}".strip())
        return "\n\n".join(parts).strip()


@dataclass
class ResearchRequest:
    id: str
    session_id: str
    kind: str
    payload: Dict[str, Any]
    turn_id: Optional[str] = None
    status: str = "open"
    answer: Optional[str] = None
    created_at: datetime = field(default_factory=_utc_now)
    answered_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "kind": self.kind,
            "payload": dict(self.payload),
            "status": self.status,
            "answer": self.answer,
            "created_at": self.created_at.isoformat(),
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_drafts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    revision INTEGER NOT NULL,
    origin TEXT NOT NULL,
    sections_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_rdrafts_session
    ON research_drafts(session_id, revision DESC);

CREATE TABLE IF NOT EXISTS research_requests (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    turn_id TEXT,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'open',
    answer TEXT,
    created_at TEXT NOT NULL,
    answered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rreq_session
    ON research_requests(session_id, status);
"""


class DraftStore:
    """Persistence for draft revisions and for requests addressed to the user."""

    def __init__(self, corpus) -> None:
        self._conn: sqlite3.Connection = corpus._conn
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --------------------------------------------------------------- drafts
    def save(
        self,
        session_id: str,
        sections: List[DraftSection],
        origin: str = "render",
    ) -> Draft:
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")
        row = self._conn.execute(
            "SELECT MAX(revision) AS r FROM research_drafts WHERE session_id=?",
            (session_id,),
        ).fetchone()
        revision = int(row["r"] or 0) + 1
        created = _utc_now()
        draft = Draft(
            id=f"drf_{uuid.uuid4().hex[:10]}",
            session_id=session_id,
            revision=revision,
            origin=origin,
            sections=sections,
            created_at=created,
        )
        self._conn.execute(
            "INSERT INTO research_drafts VALUES (?,?,?,?,?,?)",
            (
                draft.id,
                session_id,
                revision,
                origin,
                json.dumps([s.to_dict() for s in sections], ensure_ascii=False),
                created.isoformat(),
            ),
        )
        self._conn.commit()
        return draft

    def latest(self, session_id: str) -> Optional[Draft]:
        row = self._conn.execute(
            "SELECT * FROM research_drafts WHERE session_id=?"
            " ORDER BY revision DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return self._row_to_draft(row) if row else None

    def get(self, session_id: str, revision: Optional[int] = None) -> Optional[Draft]:
        if revision is None:
            return self.latest(session_id)
        row = self._conn.execute(
            "SELECT * FROM research_drafts WHERE session_id=? AND revision=?",
            (session_id, revision),
        ).fetchone()
        return self._row_to_draft(row) if row else None

    def revisions(self, session_id: str) -> List[int]:
        rows = self._conn.execute(
            "SELECT revision FROM research_drafts WHERE session_id=?"
            " ORDER BY revision",
            (session_id,),
        ).fetchall()
        return [int(r["revision"]) for r in rows]

    @staticmethod
    def _row_to_draft(row) -> Draft:
        try:
            raw = json.loads(row["sections_json"])
        except (json.JSONDecodeError, TypeError):
            raw = []
        return Draft(
            id=row["id"],
            session_id=row["session_id"],
            revision=int(row["revision"]),
            origin=row["origin"],
            sections=[DraftSection.from_dict(s) for s in raw],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # --------------------------------------------------------------- requests
    def open_request(
        self,
        session_id: str,
        kind: str,
        payload: Dict[str, Any],
        turn_id: Optional[str] = None,
    ) -> ResearchRequest:
        if kind not in REQUEST_KINDS:
            raise ValueError(f"kind must be one of {REQUEST_KINDS}, got {kind!r}")
        request = ResearchRequest(
            id=f"req_{uuid.uuid4().hex[:10]}",
            session_id=session_id,
            kind=kind,
            payload=dict(payload),
            turn_id=turn_id,
        )
        self._conn.execute(
            "INSERT INTO research_requests"
            " (id, session_id, turn_id, kind, payload_json, status, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                request.id,
                session_id,
                turn_id,
                kind,
                json.dumps(payload, ensure_ascii=False),
                "open",
                request.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return request

    def answer_request(self, request_id: str, answer: str, *, skip: bool = False) -> bool:
        """Resolve one open request. Returns False when it was not openable."""
        row = self._conn.execute(
            "SELECT * FROM research_requests WHERE id=? AND status='open'", (request_id,)
        ).fetchone()
        if row is None:
            return False
        self._conn.execute(
            "UPDATE research_requests SET status=?, answer=?, answered_at=?"
            " WHERE id=?",
            ("skipped" if skip else "answered", None if skip else answer,
             _utc_now().isoformat(), request_id),
        )
        self._conn.commit()
        return True

    def pending_requests(self, session_id: str) -> List[ResearchRequest]:
        rows = self._conn.execute(
            "SELECT * FROM research_requests WHERE session_id=? AND status='open'"
            " ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [self._row_to_request(r) for r in rows]

    def has_open_request(self, session_id: str, kind: Optional[str] = None) -> bool:
        placeholders = " AND status='open'"
        params: List[Any] = [session_id]
        if kind:
            placeholders += " AND kind=?"
            params.append(kind)
        return bool(
            self._conn.execute(
                f"SELECT 1 FROM research_requests WHERE session_id=?{placeholders} LIMIT 1",
                tuple(params),
            ).fetchone()
        )

    @staticmethod
    def _row_to_request(row) -> ResearchRequest:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        return ResearchRequest(
            id=row["id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            kind=row["kind"],
            payload=payload,
            status=row["status"],
            answer=row["answer"],
            created_at=datetime.fromisoformat(row["created_at"]),
            answered_at=(
                datetime.fromisoformat(row["answered_at"]) if row["answered_at"] else None
            ),
        )
