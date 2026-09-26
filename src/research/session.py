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
from ..corpus.citations import audit_report
from ..corpus.store import Corpus
from .templates import ReportTemplate, build_skeleton, resolve_template

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

CREATE TABLE IF NOT EXISTS research_actions (
    id TEXT PRIMARY KEY,
    subquestion_id TEXT NOT NULL REFERENCES research_subquestions(id),
    round_number INTEGER NOT NULL,
    action TEXT NOT NULL,
    query TEXT NOT NULL,
    source_types TEXT,
    new_items INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ractions_sub ON research_actions(subquestion_id);
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
    def record_action(
        self,
        subquestion_id: str,
        round_number: int,
        action: str,
        query: str,
        source_types: Optional[List[str]] = None,
        new_items: int = 0,
    ) -> None:
        """Append one retrieval attempt, so "we looked" is auditable."""
        self._conn.execute(
            "INSERT INTO research_actions (id, subquestion_id, round_number, action,"
            " query, source_types, new_items, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                f"act_{uuid.uuid4().hex[:10]}",
                subquestion_id,
                round_number,
                action,
                query,
                json.dumps(source_types or [], ensure_ascii=False),
                new_items,
                _utc_now().isoformat(),
            ),
        )
        self._conn.commit()

    def actions_for(self, subquestion_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT round_number, action, query, source_types, new_items"
            " FROM research_actions WHERE subquestion_id=? ORDER BY round_number, created_at",
            (subquestion_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["source_types"] = json.loads(d.get("source_types") or "[]")
            except json.JSONDecodeError:
                d["source_types"] = []
            out.append(d)
        return out

    def actions_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT a.round_number, a.action, a.query, a.source_types, a.new_items,"
            " s.text AS subquestion FROM research_actions a"
            " JOIN research_subquestions s ON s.id = a.subquestion_id"
            " WHERE s.session_id=? ORDER BY s.id, a.round_number",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

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
        claimable_only: bool = True,
        retriever: Optional[Any] = None,
        max_retrieval_rounds: int = 3,
        min_evidence_for_answer: int = 3,
        collector: Optional[Any] = None,
        report_template: Optional[str] = None,
    ):
        self.store = store
        self.corpus = corpus
        self.planner = planner
        self.evidence_per_question = evidence_per_question
        self.max_evidence_chars = max_evidence_chars
        self.planner_budget = planner_budget_per_invocation
        # Evidence must rest on what an author asserted. With tiering on,
        # replies and comment blocks stay out of the sub-question's evidence
        # set (they are reachable through gather_leads instead).
        self.claimable_only = claimable_only
        # Optional HybridRetriever; None keeps the deterministic lexical path.
        self.retriever = retriever
        # Widening budget: how far the loop may go to reach evidence for one
        # sub-question before it declares the gap honestly.
        self.max_retrieval_rounds = max(1, max_retrieval_rounds)
        self.min_evidence_for_answer = max(1, min_evidence_for_answer)
        # async (question) -> int; wired by the orchestrator when a
        # keyword-driven source is configured, so a thin corpus can be widened.
        self.collector = collector
        # "auto" infers 背景调查/市场调研/方法探索 from the question; "flat" or
        # None keeps the original unanswered-question-shaped report.
        self.report_template = report_template
        self.planner_calls = 0

    @property
    def _search_tier(self) -> str:
        return "claimable" if self.claimable_only else "all"

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
        if self.retriever is not None:
            # Fill the vector index here rather than in the daily pipeline:
            # embeddings are only worth paying for when someone actually asks
            # a research question, and the corpus grows between questions.
            try:
                await self.retriever.index_pending()
            except Exception as exc:
                logger.warning("vector indexing skipped: %s", exc)
        for sq in self.store.open_subquestions(session_id):
            evidence = await self._gather_until_enough(session, sq)
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

    # ------------------------------------------------------------- widening
    def _retrieval_ladder(self) -> List[str]:
        """Widening order for one sub-question: cheapest and likeliest first.

        Free moves (looser terms, an untouched slice of the corpus) come before
        paid ones (a model-written rephrasing, a new collection pass), because
        the binding constraint is a rate-limited free tier, not compute.
        """
        ladder = ["baseline", "widen_terms", "switch_source_family"]
        if self.planner is not None:
            ladder.append("rewrite_query")
        if self.collector is not None:
            ladder.append("collect_keywords")
        return ladder

    async def _gather_until_enough(
        self, session: Session, sq: SubQuestion
    ) -> List[Dict[str, Any]]:
        """Evidence for one sub-question, widening the search as it comes up short.

        Every attempt is recorded, so a report can say what was tried instead
        of looking like the corpus simply had nothing.
        """
        found: Dict[str, Dict[str, Any]] = {}

        async def ask(query: str, term_budget: int, families: Optional[List[str]]) -> int:
            rows = await self.gather_evidence_async(
                query, term_budget=term_budget, source_types=families
            )
            new = 0
            for row in rows:
                if row["id"] not in found:
                    found[row["id"]] = row
                    new += 1
            return new

        first = await ask(sq.text, 4, None)
        self.store.record_action(sq.id, 1, "baseline", sq.text, None, first)

        round_number = 1
        for action in self._retrieval_ladder()[1:]:
            if len(found) >= self.min_evidence_for_answer:
                break
            if round_number >= self.max_retrieval_rounds:
                break
            round_number += 1

            if action == "widen_terms":
                new = await ask(sq.text, 2, None)
                self.store.record_action(sq.id, round_number, action, sq.text, None, new)
            elif action == "switch_source_family":
                families = self._untried_families(found)
                if not families:
                    self.store.record_action(sq.id, round_number, action, sq.text, [], 0)
                    continue
                new = await ask(sq.text, 3, families)
                self.store.record_action(sq.id, round_number, action, sq.text, families, new)
            elif action == "rewrite_query":
                rewritten = await self._rewrite(session, sq)
                if not rewritten:
                    self.store.record_action(sq.id, round_number, action, sq.text, [], 0)
                    continue
                new = await ask(rewritten, 3, None)
                self.store.record_action(sq.id, round_number, action, rewritten, None, new)
            elif action == "collect_keywords":
                collected = 0
                try:
                    collected = int(await self.collector(sq.text) or 0)
                except Exception as exc:
                    logger.warning("on-demand collection failed: %s", exc)
                new = await ask(sq.text, 3, None)
                self.store.record_action(sq.id, round_number, action, sq.text, None, new)

        return list(found.values())

    def _untried_families(self, found: Dict[str, Dict[str, Any]]) -> List[str]:
        used = {row.get("source_type") for row in found.values()}
        return [f for f in self.corpus.source_families() if f not in used][:4]

    async def _rewrite(self, session: Session, sq: SubQuestion) -> Optional[str]:
        """Ask the planner for a differently-worded query; None if unavailable.

        `decide` is intentionally optional on the Planner protocol: a host
        that supplies only decompose/answer/revise keeps the deterministic
        widening ladder.
        """
        decide = getattr(self.planner, "decide", None)
        if decide is None or self._budget_exhausted():
            return None
        self.planner_calls += 1
        try:
            data = await decide(
                session.question, sq.text, self.corpus.source_families()
            )
        except Exception as exc:
            logger.warning("query rewrite failed (%s); keeping original wording", exc)
            return None
        if not isinstance(data, dict):
            return None
        query = str(data.get("query", "")).strip()
        return query or None

    def _render_template(
        self,
        template: ReportTemplate,
        active: List[SubQuestion],
        marks_for,
        attempts,
    ) -> List[str]:
        """Render under the template's sections plus the three computed blocks.

        Sections are headings the reader expects; their content is still only
        what the session actually has. An empty section says 未覆盖 rather than
        being padded, and the timeline / disagreements / open questions are
        built from stored dates and verdicts, not from the model.
        """
        evidence_rows: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for sq in active:
            for item_id in sq.evidence_ids:
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                row = self.corpus._conn.execute(
                    "SELECT id, title, url, source_type, published_at, claimable"
                    " FROM items WHERE id=?",
                    (item_id,),
                ).fetchone()
                if row is None:
                    continue
                record = self.corpus._row_to_dict(row)
                record["claims"] = self._claims_for_item(item_id)
                evidence_rows.append(record)

        skeleton = build_skeleton(template, active, evidence_rows)
        lines: List[str] = [f"_报告骨架：{template.label}_", ""]

        for section in template.sections:
            lines.append(f"## {section.heading}")
            members = skeleton["sections"].get(section.heading, [])
            if not members:
                lines.append("_（未覆盖：本轮没有子问题落到这一节）_")
                lines.append("")
                continue
            for sq in members:
                lines.append(f"### {sq.text}")
                if sq.status == "answered" and sq.answer:
                    lines.append(sq.answer.strip())
                    marks = marks_for(sq)
                    if marks:
                        lines.append(f"证据来源：{marks}")
                else:
                    lines.append("_（尚未回答，等待下一轮迭代）_")
                    tried = attempts(sq)
                    if tried:
                        lines.append(tried)
                    marks = marks_for(sq)
                    if marks:
                        lines.append(f"已收集证据，待分析：{marks}")
                lines.append("")

        if skeleton["uncategorised"]:
            lines.append("## 其他")
            for sq in skeleton["uncategorised"]:
                lines.append(f"### {sq.text}")
                if sq.answer:
                    lines.append(sq.answer.strip())
                lines.append("")

        lines.append("## 时间线")
        if skeleton["timeline"]:
            for entry in skeleton["timeline"]:
                lines.append(f"- {entry['date']} — {entry['title']}")
        else:
            lines.append("_（证据中没有可用的发布日期）_")
        lines.append("")

        lines.append("## 分歧点")
        if skeleton["disagreements"]:
            for row in skeleton["disagreements"]:
                contested = [
                    c["text"] for c in row.get("claims", []) if c.get("verdict") == "contested"
                ]
                lines.append(f"- {row['title']}：" + ("；".join(contested) or "存在矛盾说法"))
        else:
            lines.append("_（核查层尚未发现相互矛盾的独立说法）_")
        lines.append("")

        lines.append("## 未决问题")
        if skeleton["open_questions"]:
            for sq in skeleton["open_questions"]:
                tried = attempts(sq)
                lines.append(f"- {sq.text}" + (f"（{tried.removeprefix('_取证尝试：_')}）" if tried else ""))
        else:
            lines.append("_（全部子问题已回答）_")
        lines.append("")
        return lines

    async def gather_evidence_async(
        self,
        query: str,
        term_budget: int = 4,
        source_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Evidence through the configured retriever, else the lexical core.

        Kept as a separate entry point so the no-key path stays the synchronous
        lexical one — a session must be able to run with every optional leg
        unavailable and still produce the same evidence set as before.
        """
        if self.retriever is None:
            return self.gather_evidence(query, term_budget, source_types)

        rows = await self.retriever.gather(
            query,
            limit=self.evidence_per_question,
            term_budget=term_budget,
            source_types=source_types,
        )
        evidence: List[Dict[str, Any]] = []
        for row in rows:
            snippet = re.sub(r"\s+", " ", self._author_text(row) or "")
            evidence.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "url": row["url"],
                    "source_type": row["source_type"],
                    "published_at": row["published_at"],
                    "snippet": snippet[: self.max_evidence_chars],
                    "claims": self._claims_for_item(row["id"]),
                    "retrieval": row.get("retrieval", {}),
                }
            )
        return evidence

    def gather_evidence(
        self,
        query: str,
        term_budget: int = 4,
        source_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Corpus items + their linked claim verdicts for one sub-question.

        A sub-question is not a search query: FTS phrases demand exact
        substrings, so we decompose into discriminating terms (same helper
        the claim linker uses) and merge per-term hits by accumulated BM25.
        """
        terms = discriminating_terms(query, max_terms=term_budget, corpus=self.corpus)
        if not terms:
            terms = [query]
        scores: Dict[str, float] = {}
        rows_by_id: Dict[str, Dict[str, Any]] = {}
        for term in terms:
            for rank, row in enumerate(
                self.corpus.search(
                    term,
                    limit=self.evidence_per_question,
                    tier=self._search_tier,
                    source_types=source_types,
                )
            ):
                scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (rank + 1)
                rows_by_id.setdefault(row["id"], row)
        ordered = sorted(scores, key=lambda i: -scores[i])[: self.evidence_per_question]
        evidence: List[Dict[str, Any]] = []
        for item_id in ordered:
            row = rows_by_id[item_id]
            snippet = re.sub(r"\s+", " ", self._author_text(row) or "")
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

    def _author_text(self, row: Dict[str, Any]) -> str:
        """The layer of a corpus row a report may quote as evidence."""
        if not self.claimable_only:
            return row.get("content") or ""
        return row.get("claimable") or ""

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

        def marks_for(sq) -> str:
            return " ".join(c for c in (cite(i) for i in sq.evidence_ids[:4]) if c)

        def attempts(sq) -> str:
            tried = self.store.actions_for(sq.id)
            if not tried:
                return ""
            return "_取证尝试：_" + "；".join(
                f"{a['action']}"
                + (f"→{','.join(a['source_types'])}" if a.get("source_types") else "")
                + f"(+{a['new_items']})"
                for a in tried
            )

        template = resolve_template(self.report_template, session.question)
        if template is not None:
            lines += self._render_template(template, active, marks_for, attempts)
        else:
            for sq in active:
                lines.append(f"## {sq.text}")
                if sq.status == "answered" and sq.answer:
                    lines.append(sq.answer.strip())
                    if sq.evidence_ids:
                        marks = marks_for(sq)
                        if marks:
                            lines.append(f"\n证据来源：{marks}")
                    else:
                        lines.append("_（本条回答暂无语料支撑，仅供参考）_")
                elif sq.status == "open":
                    lines.append("_（尚未回答：缺少模型调用预算或语料证据，等待下一轮迭代）_")
                    tried = attempts(sq)
                    if tried:
                        lines.append(tried)
                    if sq.evidence_ids:
                        marks = marks_for(sq)
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
            # Make the guarantee checkable rather than merely intended: a reader
            # (or the panel) can re-run the same audit on the finished text.
            lines.append("")
            lines.append(f"> {audit_report(chr(10).join(lines), self.corpus).summary()}")
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
