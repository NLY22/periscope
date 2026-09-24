"""Long-session research orchestration (Periscope Phase D).

The endgame feature: a user asks a question, Periscope decomposes it into
sub-questions, investigates each against the evidence corpus (items +
claim verdicts), writes a cited report, and keeps iterating as the user
pushes back — across days, across restarts. The persisted state machine
is what makes "长时交互" real instead of just a long prompt.

Layering (same discipline as the claim pipeline):

- ResearchStore: pure SQLite state (sessions, subquestions, turns),
  co-located with the corpus DB. Every transition is one committed write,
  so a crash mid-run resumes from the last sub-question, not zero.
- Planner protocol: the ONLY LLM-shaped seam (decompose / answer /
  revise). Tests use a deterministic fake; Agnes plugs in later through
  exactly this interface.
- ResearchSession: the loop. Evidence gathering is deterministic (corpus
  FTS + linked claims); when the planner is absent or its budget is
  spent, sub-questions stay `open` and the report says so honestly
  instead of hallucinating closure.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

from ..analysis.claims import discriminating_terms
from ..corpus.store import Corpus

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SubQuestion:
    id: str
    session_id: str
    text: str
    parent_id: Optional[str] = None
    status: str = "open"  # open | answered | dropped
    answer: Optional[str] = None
    evidence_ids: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)


@dataclass
class Turn:
    id: str
    session_id: str
    role: str  # user | report
    content: str
    created_at: datetime = field(default_factory=_utc_now)


@dataclass
class Session:
    id: str
    question: str
    status: str = "created"  # created | investigating | reported | closed
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)


class ResearchStore:
    """SQLite persistence for research sessions, inside the corpus database."""

    _SCHEMA = """
CREATE TABLE IF NOT EXISTS research_sessions (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_subquestions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    parent_id TEXT,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    answer TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rsubq_session
    ON research_subquestions(session_id);
CREATE INDEX IF NOT EXISTS idx_rsubq_status
    ON research_subquestions(session_id, status);

CREATE TABLE IF NOT EXISTS research_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rturns_session ON research_turns(session_id);
"""

    def __init__(self, corpus: Corpus):
        self._conn = corpus._conn
        self._conn.executescript(self._SCHEMA)
        self._conn.commit()

    # sessions ---------------------------------------------------------------
    def create_session(self, question: str) -> Session:
        session = Session(id=f"ses_{uuid.uuid4().hex[:10]}", question=question.strip())
        self._conn.execute(
            "INSERT INTO research_sessions VALUES (?,?,?,?,?)",
            (session.id, session.question, session.status,
             session.created_at.isoformat(), session.updated_at.isoformat()),
        )
        self._conn.commit()
        return session

    def set_session_status(self, session_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE research_sessions SET status=?, updated_at=? WHERE id=?",
            (status, _utc_now().isoformat(), session_id),
        )
        self._conn.commit()

    def get_session(self, session_id: str) -> Optional[Session]:
        row = self._conn.execute(
            "SELECT * FROM research_sessions WHERE id=?", (session_id,)
        ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, limit: int = 50) -> List[Session]:
        rows = self._conn.execute(
            "SELECT * FROM research_sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    @staticmethod
    def _row_to_session(row) -> Session:
        return Session(
            id=row["id"], question=row["question"], status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # sub-questions ----------------------------------------------------------
    def add_subquestion(
        self, session_id: str, text: str, parent_id: Optional[str] = None
    ) -> SubQuestion:
        sq = SubQuestion(
            id=f"q_{uuid.uuid4().hex[:10]}", session_id=session_id,
            text=text.strip(), parent_id=parent_id,
        )
        self._conn.execute(
            """INSERT INTO research_subquestions
               (id, session_id, parent_id, text, status, answer, evidence_json,
                created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (sq.id, sq.session_id, sq.parent_id, sq.text, sq.status, None,
             "[]", sq.created_at.isoformat(), sq.updated_at.isoformat()),
        )
        self._conn.commit()
        return sq

    def resolve_subquestion(
        self,
        subquestion_id: str,
        status: str,
        answer: Optional[str] = None,
        evidence_ids: Optional[List[str]] = None,
    ) -> None:
        row = self._conn.execute(
            "SELECT * FROM research_subquestions WHERE id=?", (subquestion_id,)
        ).fetchone()
        if row is None:
            return
        evidence = (
            evidence_ids if evidence_ids is not None
            else json.loads(row["evidence_json"] or "[]")
        )
        self._conn.execute(
            """UPDATE research_subquestions
               SET status=?, answer=?, evidence_json=?, updated_at=? WHERE id=?""",
            (status,
             answer if answer is not None else row["answer"],
             json.dumps(evidence, ensure_ascii=False),
             _utc_now().isoformat(), subquestion_id),
        )
        self._conn.commit()

    def subquestions(self, session_id: str) -> List[SubQuestion]:
        rows = self._conn.execute(
            "SELECT * FROM research_subquestions WHERE session_id=? "
            "ORDER BY created_at, id",
            (session_id,),
        ).fetchall()
        return [self._row_to_subq(r) for r in rows]

    def open_subquestions(self, session_id: str) -> List[SubQuestion]:
        return [s for s in self.subquestions(session_id) if s.status == "open"]

    @staticmethod
    def _row_to_subq(row) -> SubQuestion:
        return SubQuestion(
            id=row["id"], session_id=row["session_id"], parent_id=row["parent_id"],
            text=row["text"], status=row["status"], answer=row["answer"],
            evidence_ids=json.loads(row["evidence_json"] or "[]"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # turns ------------------------------------------------------------------
    def add_turn(self, session_id: str, role: str, content: str) -> Turn:
        turn = Turn(
            id=f"t_{uuid.uuid4().hex[:10]}", session_id=session_id,
            role=role, content=content,
        )
        self._conn.execute(
            "INSERT INTO research_turns VALUES (?,?,?,?,?)",
            (turn.id, session_id, role, content, turn.created_at.isoformat()),
        )
        self._conn.commit()
        return turn

    def turns(self, session_id: str, limit: int = 200) -> List[Turn]:
        rows = self._conn.execute(
            "SELECT * FROM research_turns WHERE session_id=? "
            "ORDER BY created_at, id LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            Turn(id=r["id"], session_id=r["session_id"], role=r["role"],
                 content=r["content"], created_at=datetime.fromisoformat(r["created_at"]))
            for r in rows
        ]

    def stats(self) -> Dict[str, Any]:
        return {
            "sessions": self._conn.execute(
                "SELECT COUNT(*) FROM research_sessions"
            ).fetchone()[0],
            "subquestions": dict(
                self._conn.execute(
                    "SELECT status, COUNT(*) FROM research_subquestions GROUP BY status"
                ).fetchall()
            ),
            "turns": self._conn.execute(
                "SELECT COUNT(*) FROM research_turns"
            ).fetchone()[0],
        }


class Planner(Protocol):
    """The LLM seam. Everything else in this module is deterministic."""

    async def decompose(self, question: str, prior: List[str]) -> List[str]:
        """Sub-questions that together answer the main question."""

    async def answer(
        self, question: str, subquestion: str, evidence: List[Dict[str, Any]]
    ) -> str:
        """Grounded prose answer to one sub-question."""

    async def revise(
        self, question: str, user_message: str, open_subquestions: List[str]
    ) -> Dict[str, List[str]]:
        """Tree delta after a follow-up: {"add": [...], "drop": [...]}."""


class ResearchSession:
    """The stateful loop over one research question."""

    def __init__(
        self,
        store: ResearchStore,
        corpus: Corpus,
        planner: Optional[Planner] = None,
        evidence_per_question: int = 8,
        max_evidence_chars: int = 700,
        planner_budget_per_invocation: int = 12,
    ):
        self.store = store
        self.corpus = corpus
        self.planner = planner
        self.evidence_per_question = evidence_per_question
        self.max_evidence_chars = max_evidence_chars
        self.planner_budget = planner_budget_per_invocation
        self.planner_calls = 0

    @property
    def llm_available(self) -> bool:
        return self.planner is not None

    def reset_budget(self) -> None:
        self.planner_calls = 0

    # ------------------------------------------------------------- lifecycle
    async def start(self, question: str) -> "SessionReport":
        """Create the session, decompose, investigate; return the report."""
        session = self.store.create_session(question)
        self.store.add_turn(session.id, "user", question)
        for text in await self._decompose(question):
            self.store.add_subquestion(session.id, text)
        report = await self.investigate(session.id)
        return SessionReport(session_id=session.id, markdown=report)

    async def _decompose(self, question: str) -> List[str]:
        if self.planner is None or self._budget_exhausted():
            return [question]  # single-node tree beats no session at all
        self.planner_calls += 1
        try:
            subs = await self.planner.decompose(question, [])
        except Exception as exc:
            logger.warning("decompose failed (%s); using raw question", exc)
            return [question]
        subs = [s.strip() for s in subs if s and s.strip()][:12]
        return subs or [question]

    def _budget_exhausted(self) -> bool:
        return self.planner_calls >= self.planner_budget

    # ------------------------------------------------------------ investigate
    async def investigate(self, session_id: str) -> str:
        """Answer every open sub-question, then render and store the report."""
        self.store.set_session_status(session_id, "investigating")
        session = self.store.get_session(session_id)
        for sq in self.store.open_subquestions(session_id):
            evidence = self.gather_evidence(sq.text)
            answer: Optional[str] = None
            if self.planner is not None and not self._budget_exhausted():
                self.planner_calls += 1
                try:
                    answer = await self.planner.answer(session.question, sq.text, evidence)
                except Exception as exc:
                    logger.warning("answer failed for %s: %s", sq.id, exc)
            self.store.resolve_subquestion(
                sq.id,
                # staying open with evidence attached is the checkpoint that
                # lets the next run (or a new day of corpus) answer it
                status="answered" if answer else "open",
                answer=answer,
                evidence_ids=[e["id"] for e in evidence],
            )
        report = self.render_report(session_id)
        self.store.add_turn(session_id, "report", report)
        self.store.set_session_status(session_id, "reported")
        return report

    def gather_evidence(self, query: str) -> List[Dict[str, Any]]:
        """Corpus items + their linked claim verdicts for one sub-question.

        A sub-question is not a search query: FTS phrases demand exact
        substrings, so we decompose into discriminating terms (same helper
        the claim linker uses) and merge per-term hits by accumulated BM25.
        """
        terms = discriminating_terms(query, max_terms=4, corpus=self.corpus)
        if not terms:
            terms = [query]
        scores: Dict[str, float] = {}
        rows_by_id: Dict[str, Dict[str, Any]] = {}
        for term in terms:
            for rank, row in enumerate(self.corpus.search(term, limit=self.evidence_per_question)):
                scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (rank + 1)
                rows_by_id.setdefault(row["id"], row)
        ordered = sorted(scores, key=lambda i: -scores[i])[: self.evidence_per_question]
        evidence: List[Dict[str, Any]] = []
        for item_id in ordered:
            row = rows_by_id[item_id]
            snippet = re.sub(r"\s+", " ", row.get("content") or "")
            evidence.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "url": row["url"],
                    "source_type": row["source_type"],
                    "published_at": row["published_at"],
                    "snippet": snippet[: self.max_evidence_chars],
                    "claims": self._claims_for_item(row["id"]),
                }
            )
        return evidence

    def _claims_for_item(self, item_id: str) -> List[Dict[str, Any]]:
        try:
            rows = self.corpus._conn.execute(
                """SELECT text, status, verdict, independent_sources
                   FROM claims WHERE item_id=?""",
                (item_id,),
            ).fetchall()
        except Exception:
            return []  # claim tables may not exist in a fresh corpus
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- report
    def render_report(self, session_id: str) -> str:
        """Markdown report with numbered citation anchors.

        Numbering is assigned in order of first appearance across
        sub-questions, and the reference list is derived from ids that
        actually exist in the corpus — a report can never cite a ghost.
        """
        session = self.store.get_session(session_id)
        subs = self.store.subquestions(session_id)
        refs: List[Dict[str, Any]] = []
        ref_numbers: Dict[str, int] = {}

        def cite(item_id: str) -> Optional[str]:
            if item_id in ref_numbers:
                return f"[{ref_numbers[item_id]}]"
            row = self.corpus._conn.execute(
                "SELECT title, url, source_type FROM items WHERE id=?", (item_id,)
            ).fetchone()
            if row is None:
                return None
            ref_numbers[item_id] = len(refs) + 1
            refs.append({"id": item_id, **dict(row)})
            return f"[{ref_numbers[item_id]}]"

        lines = [f"# 研究报告：{session.question}", ""]
        active = [s for s in subs if s.status != "dropped"]
        answered = sum(1 for s in active if s.status == "answered")
        status_line = f"> 进度：{answered}/{len(active)} 个子问题已回答"
        if answered < len(active):
            status_line += "（未回答的子问题将在后续迭代或语料扩充后处理）"
        lines += [status_line, ""]

        for sq in active:
            lines.append(f"## {sq.text}")
            if sq.status == "answered" and sq.answer:
                lines.append(sq.answer.strip())
                if sq.evidence_ids:
                    marks = " ".join(
                        c for c in (cite(i) for i in sq.evidence_ids[:4]) if c
                    )
                    if marks:
                        lines.append(f"\n证据来源：{marks}")
                else:
                    lines.append("_（本条回答暂无语料支撑，仅供参考）_")
            elif sq.status == "open":
                lines.append("_（尚未回答：缺少模型调用预算或语料证据，等待下一轮迭代）_")
                if sq.evidence_ids:
                    marks = " ".join(
                        c for c in (cite(i) for i in sq.evidence_ids[:4]) if c
                    )
                    if marks:
                        lines.append(f"已收集证据，待分析：{marks}")
            lines.append("")

        # verdict digest: what the correctness layer already knows
        verdict_rows = self._session_claim_verdicts(subs)
        if verdict_rows:
            lines.append("## 声明核查摘要")
            for v in verdict_rows:
                tag = {"supported": "✅ 多源支持", "contested": "⚠️ 存在矛盾",
                       "unsupported": "❓ 证据不足"}.get(v["verdict"], "🔗 已关联证据")
                lines.append(
                    f"- {tag}（独立信源 {v['independent_sources']}）：{v['text']}"
                )
            lines.append("")

        if refs:
            lines.append("## 引用")
            for r in refs:
                n = ref_numbers[r["id"]]
                lines.append(f"[{n}] `{r['source_type']}` {r['title']} — {r['url']}")
        return "\n".join(lines).strip()

    def _session_claim_verdicts(self, subs: List[SubQuestion]) -> List[Dict[str, Any]]:
        item_ids = {i for s in subs for i in s.evidence_ids}
        if not item_ids:
            return []
        placeholders = ",".join("?" * len(item_ids))
        try:
            rows = self.corpus._conn.execute(
                f"""SELECT text, verdict, independent_sources FROM claims
                    WHERE item_id IN ({placeholders})
                      AND (status='graded' OR independent_sources >= 2)
                    ORDER BY independent_sources DESC LIMIT 12""",
                tuple(item_ids),
            ).fetchall()
        except Exception:
            return []
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- follow-up
    async def followup(self, session_id: str, user_message: str) -> SessionReport:
        """Iterate: fold the user's pushback into the tree, re-investigate."""
        session = self.store.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown research session: {session_id}")
        self.reset_budget()
        self.store.add_turn(session_id, "user", user_message)
        if self.planner is not None:
            open_texts = [s.text for s in self.store.open_subquestions(session_id)]
            try:
                self.planner_calls += 1
                delta = await self.planner.revise(session.question, user_message, open_texts)
            except Exception as exc:
                logger.warning("revise failed: %s", exc)
                delta = {}
            current = {s.text: s for s in self.store.subquestions(session_id)}
            for text in delta.get("add", []):
                text = text.strip()
                if text and text not in current:
                    self.store.add_subquestion(session_id, text)
            for text in delta.get("drop", []):
                sq = current.get(text.strip())
                if sq is not None and sq.status != "dropped":
                    # a user's scope cut can retire an answered branch too —
                    # the drop only hides it from future reports, history stays
                    self.store.resolve_subquestion(sq.id, status="dropped")
        report = await self.investigate(session_id)
        return SessionReport(session_id=session_id, markdown=report)


@dataclass
class SessionReport:
    session_id: str
    markdown: str
