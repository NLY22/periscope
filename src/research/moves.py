"""The decision the research loop makes between rounds.

Before P2 the loop had one top-level behaviour: `investigate()` walks every
open sub-question and returns a whole report. That cannot ask the user for a
missing input, and it cannot re-do only the part that changed. `Move` is the
vocabulary for those choices; the four `AskUser` kinds each correspond to a
real gap in the evidence, not to an interaction for its own sake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

AskUserKind = Literal["clarify", "confirm_claim", "choose_scope", "supply_source"]


@dataclass(frozen=True)
class AskUser:
    """Stop and request an input only the human can supply."""

    kind: AskUserKind
    question: str
    options: tuple = ()


@dataclass(frozen=True)
class Rescope:
    """Fold a scope change into the sub-question tree."""

    add: tuple = ()
    drop: tuple = ()


@dataclass(frozen=True)
class Deepen:
    """Gather more evidence for these sub-questions; empty means every open one."""

    subquestion_ids: tuple = ()


@dataclass(frozen=True)
class Finalize:
    """Nothing more to gain: accept the current draft as the report."""

    reason: str = ""


Move = Union[AskUser, Rescope, Deepen, Finalize]


@dataclass
class MoveContext:
    """Everything a planner needs to choose the next move.

    Deliberately a plain snapshot rather than live handles to the store: the
    deterministic fallback in `ResearchSession` builds one of these without an
    LLM, and a session that cannot reach a model must still be able to decide.
    """

    session_id: str
    question: str
    subquestions: List[Dict[str, Any]] = field(default_factory=list)
    draft_sections: List[Dict[str, Any]] = field(default_factory=list)
    pending_requests: List[Dict[str, Any]] = field(default_factory=list)
    user_message: str = ""
    budget_left: int = 0

    @property
    def open_subquestions(self) -> List[Dict[str, Any]]:
        return [s for s in self.subquestions if s.get("status") == "open"]

    @property
    def thin_subquestions(self) -> List[Dict[str, Any]]:
        """Open branches whose evidence came up short — the honest ask signal."""
        return [
            s
            for s in self.subquestions
            if s.get("status") == "open" and not s.get("evidence_ids")
        ]

    @property
    def contested_claims(self) -> List[Dict[str, Any]]:
        return [
            s
            for s in self.draft_sections
            if any(v == "contested" for v in (s.get("verdicts") or {}).values())
        ]
