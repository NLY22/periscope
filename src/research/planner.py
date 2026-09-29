"""LLM-backed Planner: adapts an AIClient to the Planner protocol.

Prompts follow the house contract style (JSON-only outputs, validated,
one repair attempt). Parsing failures raise — ResearchSession already
treats planner exceptions as "keep the state, mark unanswered", which is
exactly the degradation we want on a flaky free tier.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ..ai.utils import parse_json_response

DECOMPOSE_SYSTEM = """\
You plan research. Given the MAIN QUESTION, output 3-6 atomic
SUB-QUESTIONS that, answered together, answer it. Cover: what happened,
who says so, what contradicts, what numbers/dates matter, what recent
developments changed the picture. Write sub-questions in the language of
the main question. Each must be answerable from collected evidence alone.

Return ONLY JSON: {"subquestions": ["...", "..."]}"""

ANSWER_SYSTEM = """\
Answer ONE sub-question using ONLY the numbered evidence items provided.
Cite inline with [n] matching those numbers; if evidence is thin, say so
explicitly instead of filling gaps. Keep it under 150 words, in the
language of the sub-question. Claim verdicts, when present, tell you what
independent-source checks already concluded — respect them.

Return ONLY JSON: {"answer": "..."}"""

REVISE_SYSTEM = """\
A user is steering an ongoing research session. Given the MAIN QUESTION,
the currently OPEN sub-questions, and the user's new message, decide how
the question tree changes: "add" new sub-questions the message raises,
"drop" existing sub-question texts the message makes irrelevant or
explicitly rejects. Use the EXACT wording of existing sub-questions for
drops. Prefer empty lists over invented scope.

Return ONLY JSON: {"add": ["..."], "drop": ["..."]}"""

DECIDE_SYSTEM = """\
A research sub-question returned too little evidence from a stored corpus.
Rewrite it as a SEARCH QUERY that would find the material instead:
- keep the entities, drop the grammar (question words, politeness);
- prefer the surface forms sources use: product/organisation names,
  acronyms, version numbers, amounts, dates;
- if the corpus has both Chinese and English items, pick the language the
  sources are written in, or mix the two most specific terms.
Return ONLY JSON: {"query": "..."}"""

NEXT_MOVE_SYSTEM = """\
You are the driver of one round of a long research session. Given the MAIN
QUESTION, the sub-question tree with its statuses and evidence counts, the
draft sections, any requests already waiting on the user, and the remaining
LLM budget, choose exactly one next move:

- "ask"      — you cannot proceed without something only the user has.
   kind: clarify (the question is ambiguous and the tree forks),
         confirm_claim (a claim is contested and needs a human ruling),
         choose_scope (evidence conflicts between two source families),
         supply_source (the corpus holds nothing relevant; ask for a link
                        or a file).
- "rescope"  — fold the user's latest message into the tree: add/drop
   sub-question texts.
- "deepen"   — gather more evidence for these sub-question ids (empty list
   means every open one).
- "finalize" — nothing further would change the answer; accept the draft.

Prefer deepen/finalize over ask. Asking the user is expensive for them: use
it when the evidence genuinely cannot settle the point, not when another
search round would. Respect the budget: if budget_left is 0, answer deepen or
finalize.

Return ONLY JSON: {"move": "ask"|"rescope"|"deepen"|"finalize",
                   "kind": "...", "question": "...", "options": ["..."],
                   "add": ["..."], "drop": ["..."],
                   "subquestion_ids": ["..."], "reason": "..."}
Omit keys you do not need."""


class LLMPlanner:
    """Turns any AIClient-compatible object (complete(system, user) -> str)."""

    def __init__(self, client: Any):
        self.client = client

    async def _json(
        self,
        system: str,
        user: str,
        validate=lambda data: isinstance(data, dict),
        contract: str = "Return ONLY the JSON object.",
    ) -> Dict[str, Any]:
        """One call, one repair retry, then raise.

        `validate` checks the full contract (shape AND required fields), so
        a well-formed JSON missing "answer" also earns exactly one retry —
        important for a small model that drifts from the schema.
        """
        raw = await self.client.complete(system=system, user=user, temperature=0)
        parsed = parse_json_response(raw)
        if isinstance(parsed, dict) and validate(parsed):
            return parsed
        raw = await self.client.complete(
            system=system,
            user=user + "\n\nPrevious reply did not satisfy the output contract. "
            + contract,
            temperature=0,
        )
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict) or not validate(parsed):
            raise ValueError(f"planner LLM response failed contract: {contract}")
        return parsed

    async def decompose(self, question: str, prior: List[str]) -> List[str]:
        user = f"MAIN QUESTION: {question}"
        if prior:
            user += "\nAlready asked: " + "; ".join(prior)
        data = await self._json(
            DECOMPOSE_SYSTEM,
            user,
            validate=lambda d: isinstance(d.get("subquestions"), list),
            contract='Return ONLY {"subquestions": ["...", "..."]}',
        )
        return [str(s) for s in data["subquestions"] if str(s).strip()]

    async def answer(
        self, question: str, subquestion: str, evidence: List[Dict[str, Any]]
    ) -> str:
        lines = []
        for n, ev in enumerate(evidence, 1):
            claims = ""
            verdicts = [c for c in ev.get("claims", []) if c.get("verdict")]
            if verdicts:
                claims = " | 核查: " + "; ".join(
                    f"{c['verdict']}({c['independent_sources']}源)" for c in verdicts[:3]
                )
            published = (ev.get("published_at") or "")[:10]
            lines.append(
                f"[{n}] ({ev['source_type']}, {published}) "
                f"{ev['title']}: {ev['snippet']}{claims}"
            )
        user = (
            f"MAIN QUESTION: {question}\nSUB-QUESTION: {subquestion}\n\n"
            "证据:\n" + ("\n".join(lines) or "（无）")
        )
        data = await self._json(
            ANSWER_SYSTEM,
            user,
            validate=lambda d: isinstance(d.get("answer"), str) and bool(d["answer"].strip()),
            contract='Return ONLY {"answer": "..."}',
        )
        return data["answer"].strip()

    async def decide(
        self, question: str, subquestion: str, corpus_families: List[str]
    ) -> Dict[str, Any]:
        """Rewrite an under-evidenced sub-question into a better search query."""
        user = (
            f"MAIN QUESTION: {question}\nSUB-QUESTION: {subquestion}\n"
            "CORPUS SOURCE FAMILIES: "
            + ", ".join(corpus_families or ["(unknown)"])
        )
        data = await self._json(
            DECIDE_SYSTEM,
            user,
            validate=lambda d: isinstance(d.get("query"), str) and bool(d["query"].strip()),
            contract='Return ONLY {"query": "..."}',
        )
        return {"query": data["query"].strip()}

    async def revise(
        self, question: str, user_message: str, open_subquestions: List[str]
    ) -> Dict[str, List[str]]:
        user = (
            f"MAIN QUESTION: {question}\n"
            "OPEN SUB-QUESTIONS: " + json.dumps(open_subquestions, ensure_ascii=False)
            + f"\nUSER MESSAGE: {user_message}"
        )
        data = await self._json(REVISE_SYSTEM, user)
        return {
            "add": [str(x) for x in data.get("add", []) if str(x).strip()],
            "drop": [str(x) for x in data.get("drop", []) if str(x).strip()],
        }

    async def next_move(self, ctx: Any) -> Any:
        """Choose this round's verb from a snapshot of the session state."""
        from .moves import AskUser, Deepen, Finalize, Rescope

        payload = {
            "MAIN QUESTION": ctx.question,
            "USER MESSAGE": ctx.user_message or None,
            "SUB-QUESTIONS": [
                {
                    "id": s["id"], "text": s["text"], "status": s["status"],
                    "evidence": len(s.get("evidence_ids") or []),
                }
                for s in ctx.subquestions
            ],
            "DRAFT_SECTIONS": [
                {
                    "id": s["id"], "title": s["title"], "locked": s["locked"],
                    "stale": s["stale"], "verdicts": s.get("verdicts") or {},
                }
                for s in ctx.draft_sections
            ],
            "PENDING_REQUESTS": [
                {"id": r["id"], "kind": r["kind"]} for r in ctx.pending_requests
            ],
            "budget_left": ctx.budget_left,
        }
        data = await self._json(
            NEXT_MOVE_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
            validate=lambda d: d.get("move") in {"ask", "rescope", "deepen", "finalize"},
            contract='Return ONLY {"move": "deepen", "subquestion_ids": ["..."]}',
        )
        move = data["move"]
        if move == "ask":
            kind = data.get("kind") or "clarify"
            if kind not in {"clarify", "confirm_claim", "choose_scope", "supply_source"}:
                kind = "clarify"
            return AskUser(
                kind=kind,
                question=str(data.get("question") or "需要你补充一点信息才能继续。"),
                options=tuple(str(o) for o in data.get("options") or ()),
            )
        if move == "rescope":
            return Rescope(
                add=tuple(str(x) for x in data.get("add") or [] if str(x).strip()),
                drop=tuple(str(x) for x in data.get("drop") or [] if str(x).strip()),
            )
        if move == "deepen":
            return Deepen(tuple(str(x) for x in data.get("subquestion_ids") or []))
        return Finalize(str(data.get("reason") or ""))
