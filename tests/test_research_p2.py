"""P2: the research document as a versioned artefact with a per-round verb.

Covers spec §4 and the three P2 acceptance clauses in spec §10:
(a) a session can run >=3 rounds and the system can ask the user on its own;
(b) a section the user edited is never overwritten, only marked stale;
(c) a round costs fewer LLM calls than re-running the whole tree.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.analysis.claims import Claim, ClaimStore
from src.corpus.store import Corpus
from src.models import ContentItem, SourceType
from src.research.drafts import DraftSection, DraftStore, body_hash
from src.research.moves import AskUser, Deepen, Finalize, MoveContext, Rescope
from src.research.session import ResearchSession, ResearchStore

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def make_item(idx: str, title: str, content: str) -> ContentItem:
    return ContentItem(
        id=f"p2:{idx}",
        source_type=SourceType.V2EX,
        title=title,
        url=f"https://e.com/{idx}",
        content=content,
        author="tester",
        published_at=NOW,
        fetched_at=NOW,
    )


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    c = Corpus(tmp_path / "corpus.db")
    c.add_items([
        make_item("1", "DeepSeek-V4 发布", "DeepSeek-V4 模型 9 月上线，支持百万上下文。"),
        make_item("2", "发布说明", "DeepSeek-V4 定价每百万 token 2 元。"),
        make_item("3", "评测", "第三方评测称 V4 代码能力领先。"),
    ])
    return c


class ScriptedPlanner:
    """A planner whose next_move answers come from a script, call by call."""

    def __init__(self, moves, subs=None, answer="模型给出的回答"):
        self.moves = list(moves)
        self.subs = subs or ["V4 的能力到底如何", "定价是多少"]
        self.answer_text = answer
        self.calls = 0
        self.asks = 0

    async def decompose(self, question, prior):
        self.calls += 1
        return list(self.subs)

    async def answer(self, question, subquestion, evidence):
        self.calls += 1
        return self.answer_text if evidence else ""

    async def revise(self, question, user_message, open_subquestions):
        self.calls += 1
        return {"add": [], "drop": []}

    async def next_move(self, ctx):
        self.calls += 1
        if not self.moves:
            return Finalize("script exhausted")
        move = self.moves.pop(0)
        if isinstance(move, AskUser):
            self.asks += 1
        return move


def build_session(corpus, planner=None, budget=12) -> ResearchSession:
    return ResearchSession(
        store=ResearchStore(corpus),
        corpus=corpus,
        planner=planner,
        evidence_per_question=5,
        planner_budget_per_invocation=budget,
    )


# --------------------------------------------------------------------- drafts
def test_draft_revisions_increase_and_are_readable(corpus) -> None:
    rs = build_session(corpus, planner=None)
    started = asyncio.run(rs.start("DeepSeek 研究"))
    store = rs.drafts
    draft = store.latest(started.session_id)
    assert draft is not None and draft.revision >= 1
    assert draft.origin == "render"
    assert store.revisions(started.session_id)[0] == 1


def test_edit_section_creates_a_user_edit_revision_and_locks_it(corpus) -> None:
    rs = build_session(corpus, planner=None)
    report = asyncio.run(rs.start("DeepSeek 研究"))
    draft = rs.drafts.latest(report.session_id)
    target = draft.sections[-1]
    edited = rs.edit_section(report.session_id, target.id, "我自己写的结论")
    assert edited.origin == "user_edit"
    assert edited.revision > draft.revision
    section = edited.section(target.id)
    assert section.body == "我自己写的结论"
    assert section.locked is True
    assert section.hash == body_hash("我自己写的结论")


def test_a_locked_section_is_never_overwritten_only_marked_stale(corpus) -> None:
    rs = build_session(corpus, planner=None)
    report = asyncio.run(rs.start("DeepSeek 研究"))
    draft = rs.drafts.latest(report.session_id)
    target = draft.sections[-1]
    rs.edit_section(report.session_id, target.id, "用户自己的话")

    # new evidence arrives, then the loop re-renders into a fresh revision
    corpus.add_items([make_item("9", "补充", "DeepSeek-V4 支持百万上下文，补充说明。")])
    asyncio.run(rs.step(report.session_id, "再查一次"))

    after = rs.drafts.latest(report.session_id)
    kept = after.section(target.id)
    assert kept.body == "用户自己的话"          # not replaced
    assert kept.locked is True
    assert kept.stale is True                   # and the reader is told


def test_stale_flag_is_false_when_the_recomputed_text_matches(corpus) -> None:
    rs = build_session(corpus, planner=None)
    report = asyncio.run(rs.start("DeepSeek 研究"))
    draft = rs.drafts.latest(report.session_id)
    target = draft.sections[-1]
    rs.edit_section(report.session_id, target.id, target.body)  # "edit" to the same text
    corpus.add_items([make_item("10", "无关", "与本题无关的另一件事。")])
    asyncio.run(rs.step(report.session_id, "继续"))
    after = rs.drafts.latest(report.session_id)
    assert after.section(target.id).stale is False


# ---------------------------------------------------------------------- step
def test_step_can_ask_the_user_and_parks_the_session(corpus) -> None:
    planner = ScriptedPlanner([
        Deepen(),
        AskUser("supply_source", "语料里没有你司的内部数据，能给我一个链接或文件吗？"),
    ])
    rs = build_session(corpus, planner)
    started = asyncio.run(rs.start("我们公司的市场调研"))
    status = rs.store.get_session(started.session_id).status
    assert status == "reported"

    result = asyncio.run(rs.step(started.session_id, "先只看竞品"))
    assert result.move in ("rescope", "deepen") or result.pending_request is None

    second = asyncio.run(rs.step(started.session_id))
    assert second.move == "askuser"
    assert second.pending_request is not None
    assert second.pending_request.kind == "supply_source"
    assert rs.store.get_session(started.session_id).status == "awaiting_user"
    roles = [t.role for t in rs.store.turns(started.session_id)]
    assert "assistant_question" in roles


def test_answering_a_request_resumes_the_session(corpus) -> None:
    planner = ScriptedPlanner([
        AskUser("clarify", "你说的是竞品还是价格？", ("竞品", "价格")),
        Finalize("够了"),
    ])
    rs = build_session(corpus, planner)
    started = asyncio.run(rs.start("市场调研"))
    first = asyncio.run(rs.step(started.session_id, "市场调研"))
    request = first.pending_request
    assert request is not None

    resumed = asyncio.run(rs.answer_request(started.session_id, request.id, "价格"))
    assert resumed.pending_request is None
    pending = rs.drafts.pending_requests(started.session_id)
    assert pending == []


def test_a_request_can_be_skipped_without_answering(corpus) -> None:
    planner = ScriptedPlanner([AskUser("choose_scope", "要哪一面？")])
    rs = build_session(corpus, planner)
    started = asyncio.run(rs.start("矛盾的证据"))
    first = asyncio.run(rs.step(started.session_id))
    ok = rs.drafts.answer_request(first.pending_request.id, "", skip=True)
    assert ok is True
    assert rs.drafts.pending_requests(started.session_id) == []


def test_answering_an_unknown_or_closed_request_raises(corpus) -> None:
    rs = build_session(corpus, planner=None)
    started = asyncio.run(rs.start("DeepSeek 研究"))
    with pytest.raises(KeyError):
        asyncio.run(rs.answer_request(started.session_id, "req_nope", "x"))


def test_deepening_one_branch_costs_fewer_calls_than_deepening_all(corpus) -> None:
    """The local-recompute claim, measured rather than asserted in prose."""
    subs = [f"子问题{i}" for i in range(4)]

    planner = ScriptedPlanner([], subs=subs)
    rs = build_session(corpus, planner, budget=60)
    started = asyncio.run(rs.start("宽题研究"))
    assert planner.calls == 1 + len(subs)          # decompose + one answer each
    after_start = planner.calls
    ids = [s.id for s in rs.store.subquestions(started.session_id)]

    asyncio.run(rs.step(started.session_id))       # scripted list empty -> Finalize
    finalize_only = planner.calls - after_start

    after = planner.calls
    planner.moves = [Deepen(tuple(ids[:1]))]
    asyncio.run(rs.step(started.session_id))
    one_branch = planner.calls - after

    after = planner.calls
    planner.moves = [Deepen(tuple(ids))]
    asyncio.run(rs.step(started.session_id))
    all_branches = planner.calls - after

    assert one_branch < all_branches
    assert one_branch == 2         # next_move + the single answer
    assert all_branches == 1 + len(ids)


def test_a_step_costs_no_more_than_a_full_rerun(corpus) -> None:
    subs = [f"子问题{i}" for i in range(6)]
    planner = ScriptedPlanner([], subs=subs)
    rs = build_session(corpus, planner, budget=60)
    started = asyncio.run(rs.start("宽题研究"))
    ids = [s.id for s in rs.store.subquestions(started.session_id)]

    whole_tree = build_session(corpus, ScriptedPlanner([], subs=subs), budget=60)
    asyncio.run(whole_tree.start("宽题研究"))
    full_rerun = whole_tree.planner_calls

    before = planner.calls
    planner.moves = [Deepen(tuple(ids[:2]))]
    asyncio.run(rs.step(started.session_id))
    step_round = planner.calls - before

    assert step_round < full_rerun


def test_no_planner_still_finishes_rather_than_hanging(corpus) -> None:
    rs = build_session(corpus, planner=None)
    started = asyncio.run(rs.start("DeepSeek 研究"))
    result = asyncio.run(rs.step(started.session_id, "继续"))
    assert result.move == "deepen"
    assert result.revision >= 1
    assert result.markdown


def test_budget_exhausted_falls_back_to_the_deterministic_move(corpus) -> None:
    planner = ScriptedPlanner([Deepen()])
    rs = build_session(corpus, planner, budget=1)
    started = asyncio.run(rs.start("DeepSeek 研究"))
    # start() already spent the one-call budget; the loop must still choose
    result = asyncio.run(rs.step(started.session_id, "再来一轮"))
    assert result.move == "deepen"
    assert result.revision >= 1


# ------------------------------------------------------------------ context
def test_move_context_reports_open_and_thin_branches() -> None:
    ctx = MoveContext(
        session_id="s", question="q",
        subquestions=[
            {"id": "a", "text": "a", "status": "open", "evidence_ids": []},
            {"id": "b", "text": "b", "status": "answered", "evidence_ids": ["x"]},
            {"id": "c", "text": "c", "status": "open", "evidence_ids": ["y"]},
        ],
        budget_left=3,
    )
    assert [s["id"] for s in ctx.open_subquestions] == ["a", "c"]
    assert [s["id"] for s in ctx.thin_subquestions] == ["a"]
    assert ctx.budget_left == 3


def test_a_non_move_from_the_planner_falls_back(corpus) -> None:
    """A planner that answers with a bare string must not wedge the session."""

    class BadPlanner(ScriptedPlanner):
        async def next_move(self, ctx):
            return "deepen"      # a string, not a Move instance

    # answer="" keeps every branch open, so the deterministic policy's answer
    # to "what next" is unambiguously deepen rather than finalize.
    rs = build_session(corpus, BadPlanner([None], answer=""), budget=6)
    started = asyncio.run(rs.start("DeepSeek 研究"))
    assert all(s.status == "open" for s in rs.store.subquestions(started.session_id))

    result = asyncio.run(rs.step(started.session_id, "继续"))
    assert result.move == "deepen"
    assert result.revision >= 1


# --------------------------------------------------------------- entry points
class FakeOrchestrator:
    """Exposes exactly what the MCP service and the panel reach for."""

    def __init__(self, session):
        self._session = session

    def get_research_session(self):
        return self._session


def test_mcp_service_exposes_the_four_new_verbs() -> None:
    import inspect

    from src.mcp.service import HorizonPipelineService as S

    for name in ("research_step", "research_draft", "research_edit", "research_answer"):
        assert callable(getattr(S, name)), name
    # step and answer advance the loop, so they await; draft and edit do not
    assert inspect.iscoroutinefunction(S.research_step)
    assert inspect.iscoroutinefunction(S.research_answer)
    assert not inspect.iscoroutinefunction(S.research_draft)
    assert not inspect.iscoroutinefunction(S.research_edit)


def test_mcp_tool_list_includes_the_round_verbs() -> None:
    import asyncio

    from src.mcp import server

    async def names() -> set:
        return {t.name for t in await server.mcp.list_tools()}

    tools = asyncio.run(names())
    assert {
        "hz_research_step", "hz_research_draft", "hz_research_edit",
        "hz_research_answer", "hz_research_start", "hz_research_followup",
    } <= tools


def test_panel_step_draft_edit_and_answer_round_trip(tmp_path, corpus) -> None:
    from fastapi.testclient import TestClient

    from src.web.app import create_app

    rs = build_session(corpus, planner=None)
    orchestrator = FakeOrchestrator(rs)
    client = TestClient(create_app(orchestrator))

    started = client.post("/api/research/start", json={"question": "DeepSeek 研究"})
    assert started.status_code == 200
    session_id = started.json()["session_id"]

    status = client.get(f"/api/research/{session_id}").json()
    assert status["draft"]["revision"] >= 1
    section_id = status["draft"]["sections"][-1]["id"]

    edited = client.patch(
        f"/api/research/{session_id}/draft/sections/{section_id}",
        json={"section_id": section_id, "body": "我自己的结论"},
    )
    assert edited.status_code == 200
    assert edited.json()["origin"] == "user_edit"
    assert edited.json()["section"]["locked"] is True

    draft = client.get(f"/api/research/{session_id}/draft").json()
    assert draft["revision"] == edited.json()["revision"]
    assert draft["revisions"] == sorted(set(draft["revisions"]))

    stepped = client.post(f"/api/research/{session_id}/step", json={"message": "继续"})
    assert stepped.status_code == 200
    body = stepped.json()
    assert body["move"] in ("deepen", "finalize", "rescope")
    assert body["revision"] >= draft["revision"]
    target = next(s for s in draft["sections"] if s["id"] == section_id)
    assert target["body"] == "我自己的结论"

    missing = client.post(
        "/api/research/ses_nope/step", json={"message": "x"}
    )
    assert missing.status_code == 404
    unknown_section = client.patch(
        f"/api/research/{session_id}/draft/sections/s_nope",
        json={"section_id": "s_nope", "body": "x"},
    )
    assert unknown_section.status_code == 404


def test_spec_acceptance_three_rounds_with_one_system_question(corpus) -> None:
    """spec §10 P2 (a): >=3 rounds, at least one initiated by the system."""
    planner = ScriptedPlanner([
        AskUser("clarify", "你关心的是竞品还是价格？", ("竞品", "价格")),
        Deepen(),
        Finalize("证据够了"),
    ], subs=["V4 能力如何", "定价多少"])
    rs = build_session(corpus, planner, budget=30)
    started = asyncio.run(rs.start("DeepSeek-V4 到底怎么样"))

    moves = []
    for message in ["先看价格", "", ""]:
        result = asyncio.run(rs.step(started.session_id, message))
        moves.append(result.move)
        if result.pending_request is not None:
            asyncio.run(rs.answer_request(
                started.session_id, result.pending_request.id, "价格"
            ))
    assert len(moves) == 3
    assert "askuser" in moves
    # the session neither hung nor stayed mid-investigation
    assert rs.store.get_session(started.session_id).status in (
        "reported", "drafting", "awaiting_user"
    )
    assert len(rs.drafts.revisions(started.session_id)) >= 2


def test_a_parked_session_can_still_be_listed_and_resumed(corpus) -> None:
    """spec §9.4: `awaiting_user` must be visible, not a dead session."""
    planner = ScriptedPlanner([AskUser("supply_source", "有内部数据吗？")])
    rs = build_session(corpus, planner, budget=10)
    started = asyncio.run(rs.start("内部数据"))
    asyncio.run(rs.step(started.session_id, "开始"))
    assert rs.store.get_session(started.session_id).status == "awaiting_user"

    listed = rs.store.list_sessions()
    assert any(s.id == started.session_id and s.status == "awaiting_user" for s in listed)

    pending = rs.drafts.pending_requests(started.session_id)
    assert len(pending) == 1
    resumed = asyncio.run(rs.answer_request(started.session_id, pending[0].id, "没有"))
    assert resumed.pending_request is None
    assert rs.store.get_session(started.session_id).status != "awaiting_user"


# -------------------------------------------------------------------- lineage
def test_a_section_carries_the_evidence_of_the_branch_it_came_from(corpus) -> None:
    """`section -> subquestion -> evidence` has to be filled, not merely declared.

    The panel renders a per-section evidence count and the planner is shown the
    disagreements; both read these fields, so an empty one is a silent UI bug.
    """
    rs = build_session(corpus, planner=None)
    started = asyncio.run(rs.start("DeepSeek 研究"))
    draft = rs.drafts.latest(started.session_id)
    by_text = {s.text: s for s in rs.store.subquestions(started.session_id)}

    matched = [s for s in draft.sections if s.title in by_text]
    assert matched, "the default report renders one section per sub-question"
    for section in matched:
        sq = by_text[section.title]
        assert sq.evidence_ids, "the fixture must reach the corpus at all"
        assert section.subquestion_id == sq.id
        assert section.evidence_ids == list(sq.evidence_ids)


def test_a_template_section_gathers_every_subheading_under_it(corpus) -> None:
    """A template section holds several `###` branches; its evidence is the union.

    The single `subquestion_id` pointer is left empty rather than pointed at the
    first member — one branch out of two would claim a lineage that is not there.
    """
    rs = build_session(corpus, ScriptedPlanner([]))
    started = asyncio.run(rs.start("DeepSeek 研究"))
    subs = rs.store.subquestions(started.session_id)
    assert len(subs) >= 2
    markdown = "\n".join([
        "## 市场调研",
        f"### {subs[0].text}",
        "正文一。",
        "",
        f"### {subs[1].text}",
        "正文二。",
    ])

    draft = rs.commit_draft(started.session_id, markdown)
    section = draft.section(ResearchSession.section_id("市场调研"))
    assert section is not None
    assert set(section.evidence_ids) == set(subs[0].evidence_ids) | set(subs[1].evidence_ids)
    assert section.subquestion_id is None


def test_a_contested_claim_becomes_visible_to_the_next_move(corpus) -> None:
    """spec §4.2: AskUser/Deepen should be able to see a disagreement exists."""
    rs = build_session(corpus, planner=None)
    started = asyncio.run(rs.start("DeepSeek 研究"))
    draft = rs.drafts.latest(started.session_id)
    cited = [s for s in draft.sections if s.evidence_ids]
    assert cited, "fixture must produce at least one section with evidence"

    ClaimStore(corpus).upsert_claims([
        Claim(
            id="cl_contested", item_id=cited[0].evidence_ids[0],
            text="V4 的定价有两种说法", status="graded", verdict="contested",
        )
    ])
    rs.commit_draft(started.session_id, rs.render_report(started.session_id))

    ctx = rs.move_context(started.session_id)
    assert ctx.contested_claims, (
        "the planner cannot ask about a contradiction it is never shown"
    )
    assert ctx.contested_claims[0]["id"] == cited[0].id
