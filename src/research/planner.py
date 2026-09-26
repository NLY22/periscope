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
