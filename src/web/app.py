"""Periscope web panel (Phase E): browse, ask, verify — in a browser.

Deliberately thin: every endpoint delegates to the same orchestrator
objects the CLI and MCP server use. No new business logic lives here; the
panel is a window onto the evidence loop, not a second implementation.

Threading note: all endpoints are async so SQLite work stays on the event
loop thread (Corpus/claims/research connections are created lazily by the
orchestrator and are not shared across threads). The corpus is a personal
collection — reads take microseconds, and the only heavy operation
(collection) is network-bound and awaited.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class StartRequest(BaseModel):
    question: str


class FollowupRequest(BaseModel):
    message: str


class StepRequest(BaseModel):
    message: Optional[str] = None


class SectionEditRequest(BaseModel):
    section_id: str
    body: str


class AnswerRequest(BaseModel):
    request_id: str
    answer: str = ""
    skip: bool = False


class CollectRequest(BaseModel):
    hours: Optional[int] = None


class PanelState:
    """Mutable app state: one orchestrator, one in-flight collection slot."""

    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator
        self.ai_available: Optional[bool] = None  # probed lazily, silently
        self.collect_task: Optional[asyncio.Task] = None
        self.collect_result: dict[str, Any] = {"state": "idle"}

    def probe_ai(self) -> bool:
        if self.ai_available is None:
            try:
                self.ai_available = self.orchestrator._get_optional_ai_client() is not None
            except Exception:
                self.ai_available = False
        return self.ai_available

    async def start_collect(self, hours: int) -> None:
        if self.collect_task is not None and not self.collect_task.done():
            raise HTTPException(status_code=409, detail="collection already running")
        self.collect_result = {"state": "running", "started_at": _now()}
        self.collect_task = asyncio.create_task(self._collect(hours))

    async def _collect(self, hours: int) -> None:
        orch = self.orchestrator
        try:
            since = datetime.now(timezone.utc) - timedelta(hours=hours)
            items = await orch.fetch_all_sources(since)
            await orch.analyze_claims(items)
            self.collect_result = {
                "state": "done",
                "finished_at": _now(),
                "fetched": len(items),
            }
        except Exception as exc:
            logger.exception("panel collection failed")
            self.collect_result = {"state": "error", "error": str(exc), "finished_at": _now()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_app(orchestrator: Any) -> FastAPI:
    app = FastAPI(title="Periscope", docs_url="/api/docs", redoc_url=None)
    state = PanelState(orchestrator)
    app.state.panel = state

    # ------------------------------------------------------------------ ui
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # --------------------------------------------------------------- stats
    @app.get("/api/stats")
    async def stats() -> dict[str, Any]:
        orch = state.orchestrator
        corpus = orch.get_corpus()
        if corpus is None:
            raise HTTPException(status_code=503, detail="corpus is disabled in config")
        from ..research.session import ResearchStore

        claim_store = orch.get_claim_store()
        return {
            "corpus": corpus.stats(),
            "claims": claim_store.stats() if claim_store else {"claims": 0},
            "research": ResearchStore(corpus).stats(),
            "ai_available": state.probe_ai(),
            "collect": state.collect_result,
        }

    @app.get("/api/search")
    async def search(q: str, limit: int = 20) -> dict[str, Any]:
        if not q.strip():
            raise HTTPException(status_code=400, detail="q must not be empty")
        corpus = state.orchestrator.get_corpus()
        if corpus is None:
            raise HTTPException(status_code=503, detail="corpus is disabled in config")
        rows = corpus.search(q, limit=max(1, min(limit, 100)))
        for row in rows:
            body = row.get("content") or ""
            row["snippet"] = body[:280]
            row.pop("content", None)
            row.pop("metadata", None)
        return {"query": q, "count": len(rows), "items": rows}

    @app.get("/api/items")
    async def items(limit: int = 30, source: str | None = None) -> dict[str, Any]:
        corpus = state.orchestrator.get_corpus()
        if corpus is None:
            raise HTTPException(status_code=503, detail="corpus is disabled in config")
        rows = corpus.recent(limit=max(1, min(limit, 200)), source_type=source)
        for row in rows:
            body = row.get("content") or ""
            row["snippet"] = body[:280]
            row.pop("content", None)
            row.pop("metadata", None)
        return {"count": len(rows), "items": rows}

    @app.post("/api/import")
    async def import_export(payload: dict[str, Any]) -> dict[str, Any]:
        """Ingest a user export with declared tiers (spec §6.1 fallback path).

        Nothing here reaches the network: the point is that a gated source is
        handed over as text the user's own account can already see. If every
        item was rejected the request is a 400, because a wholly invalid file
        must not look like a successful import.
        """
        from ..corpus.ingest import IngestError, import_payload

        corpus = state.orchestrator.get_corpus()
        if corpus is None:
            raise HTTPException(status_code=503, detail="corpus is disabled in config")
        try:
            report = import_payload(corpus, payload)
        except IngestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not report["items_total_seen"] and report["rejected"]:
            raise HTTPException(status_code=400, detail=report["rejected"][0]["reason"])
        return report

    # -------------------------------------------------------------- claims
    @app.get("/api/claims")
    async def claims(status: str = "graded", limit: int = 50) -> dict[str, Any]:
        store = state.orchestrator.get_claim_store()
        if store is None:
            return {"count": 0, "claims": [], "stats": {"claims": 0}}
        rows = store.claims_by_status(status, limit=max(1, min(limit, 200)))
        return {
            "status": status,
            "count": len(rows),
            "claims": [c.to_dict() for c in rows],
            "stats": store.stats(),
        }

    @app.get("/api/claims/{claim_id}")
    async def claim(claim_id: str) -> dict[str, Any]:
        store = state.orchestrator.get_claim_store()
        if store is None:
            raise HTTPException(status_code=503, detail="analysis is disabled in config")
        found = store.get_claim(claim_id)
        if found is None:
            raise HTTPException(status_code=404, detail="claim not found")
        return {"claim": found.to_dict(), "evidence": store.evidence_for(claim_id)}

    # ------------------------------------------------------------ research
    @app.get("/api/research")
    async def research_list(limit: int = 30) -> dict[str, Any]:
        session = state.orchestrator.get_research_session()
        if session is None:
            raise HTTPException(status_code=503, detail="research is disabled in config")
        sessions = session.store.list_sessions(limit=max(1, min(limit, 100)))
        return {
            "count": len(sessions),
            "sessions": [
                {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in vars(s).items()}
                for s in sessions
            ],
        }

    def _session_or_404(session_id: str):
        session = state.orchestrator.get_research_session()
        if session is None:
            raise HTTPException(status_code=503, detail="research is disabled in config")
        if session.store.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session

    @app.get("/api/research/{session_id}")
    async def research_status(session_id: str) -> dict[str, Any]:
        session = _session_or_404(session_id)

        def iso(value: Any) -> Any:
            return value.isoformat() if isinstance(value, datetime) else value

        record = session.store.get_session(session_id)
        return {
            "session": {k: iso(v) for k, v in vars(record).items()},
            "subquestions": [
                {k: iso(v) for k, v in vars(s).items()}
                for s in session.store.subquestions(session_id)
            ],
            "turns": [
                {k: iso(v) for k, v in vars(t).items()}
                for t in session.store.turns(session_id, limit=200)
            ],
            "report": session.render_report(session_id),
            "draft": _draft_payload(session, session_id),
            "pending_requests": [
                r.to_dict() for r in session.drafts.pending_requests(session_id)
            ],
        }

    def _draft_payload(session, session_id: str) -> dict[str, Any]:
        draft = session.drafts.latest(session_id)
        if draft is None:
            return {"revision": 0, "origin": None, "sections": []}
        return {
            "revision": draft.revision,
            "origin": draft.origin,
            "markdown": draft.markdown(),
            "sections": [s.to_dict() for s in draft.sections],
        }

    @app.post("/api/research/start")
    async def research_start(req: StartRequest) -> dict[str, Any]:
        session = state.orchestrator.get_research_session()
        if session is None:
            raise HTTPException(status_code=503, detail="research is disabled in config")
        if not req.question.strip():
            raise HTTPException(status_code=400, detail="question must not be empty")
        report = await session.start(req.question.strip())
        return {"session_id": report.session_id, "markdown": report.markdown}

    @app.post("/api/research/{session_id}/followup")
    async def research_followup(session_id: str, req: FollowupRequest) -> dict[str, Any]:
        session = _session_or_404(session_id)
        if not req.message.strip():
            raise HTTPException(status_code=400, detail="message must not be empty")
        report = await session.followup(session_id, req.message.strip())
        return {"session_id": report.session_id, "markdown": report.markdown}

    # -------------------------------------------------- P2: per-round drafting
    @app.post("/api/research/{session_id}/step")
    async def research_step(session_id: str, req: StepRequest) -> dict[str, Any]:
        """Advance one round. Returns what moved, not just a whole report."""
        session = _session_or_404(session_id)
        result = await session.step(session_id, (req.message or "").strip())
        return result.to_dict()

    @app.get("/api/research/{session_id}/draft")
    async def research_draft(session_id: str, revision: int = 0) -> dict[str, Any]:
        session = _session_or_404(session_id)
        payload = _draft_payload(session, session_id)
        if revision:
            draft = session.drafts.get(session_id, revision)
            if draft is None:
                raise HTTPException(status_code=404, detail="unknown draft revision")
            payload = {
                "revision": draft.revision,
                "origin": draft.origin,
                "markdown": draft.markdown(),
                "sections": [s.to_dict() for s in draft.sections],
            }
        payload["revisions"] = session.drafts.revisions(session_id)
        return payload

    @app.patch("/api/research/{session_id}/draft/sections/{section_id}")
    async def research_section_edit(
        session_id: str, section_id: str, req: SectionEditRequest
    ) -> dict[str, Any]:
        """A user edit becomes its own revision and locks that section."""
        session = _session_or_404(session_id)
        try:
            draft = session.edit_section(session_id, section_id, req.body)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "revision": draft.revision,
            "origin": draft.origin,
            "section": draft.section(section_id).to_dict(),
        }

    @app.post("/api/research/{session_id}/requests/{request_id}")
    async def research_request_answer(
        session_id: str, request_id: str, req: AnswerRequest
    ) -> dict[str, Any]:
        """Reply to (or skip) something the session asked, then continue."""
        session = _session_or_404(session_id)
        try:
            result = await session.answer_request(
                session_id, request_id, req.answer, skip=req.skip
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result.to_dict()

    # ------------------------------------------------------------- collect
    @app.post("/api/collect")
    async def collect(req: CollectRequest) -> dict[str, Any]:
        hours = req.hours or state.orchestrator.config.collection.time_window_hours
        await state.start_collect(max(1, hours))
        return {"started": True, "hours": hours}

    @app.get("/api/collect/status")
    async def collect_status() -> dict[str, Any]:
        return state.collect_result

    # ------------------------------------------------------------ fallback
    @app.exception_handler(HTTPException)
    async def http_error(request, exc: HTTPException):  # noqa: ANN001
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app
