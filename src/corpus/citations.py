"""Citation audit for research reports: no ghost references, no crowd-only proof.

`render_report` already refuses to invent reference numbers — a citation is
only emitted when the item exists in the corpus. This module closes the other
half of the loop and makes the guarantee checkable from the outside: parse a
finished report and verify that every numbered reference resolves to a real
stored item, that it has author-written text behind it (not just a comment),
and that nothing in the reference list is never cited.

It is deliberately read-only: the panel and the eval harness can call it
without any chance of rewriting a report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .store import Corpus

_INLINE = re.compile(r"\[(\d+)\]")
_REF_LINE = re.compile(r"^\[(\d+)\]\s+`?([^`]*)`?\s*(.*)$", re.MULTILINE)
_REFERENCE_HEADING = "## 引用"


@dataclass
class CitationAudit:
    """What an outside reader can prove about one report."""

    cited: List[int] = field(default_factory=list)
    resolved: List[int] = field(default_factory=list)
    ghosts: List[int] = field(default_factory=list)  # [n] in body, no such reference
    dangling: List[int] = field(default_factory=list)  # reference never cited
    missing_items: List[int] = field(default_factory=list)  # reference not in corpus
    crowd_only: List[int] = field(default_factory=list)  # cited item has no author text

    @property
    def ok(self) -> bool:
        return not (self.ghosts or self.missing_items or self.crowd_only)

    def summary(self) -> str:
        parts = [f"引用核验：{len(self.resolved)}/{len(self.cited)} 条内联标记可解析"]
        if self.ghosts:
            parts.append("幽灵引用 " + ",".join(f"[{n}]" for n in self.ghosts))
        if self.missing_items:
            parts.append("语料中不存在 " + ",".join(f"[{n}]" for n in self.missing_items))
        if self.crowd_only:
            parts.append("仅人群发言支撑 " + ",".join(f"[{n}]" for n in self.crowd_only))
        if self.dangling:
            parts.append("列出但未被引用 " + ",".join(f"[{n}]" for n in self.dangling))
        if self.ok and not self.dangling:
            parts.append("每条引用都指向有作者亲写文本的存储条目")
        return "；".join(parts) + "。"


def audit_report(markdown: str, corpus: Corpus) -> CitationAudit:
    """Check a rendered report against the corpus it claims to rest on."""
    body, _, references_block = markdown.partition(_REFERENCE_HEADING)
    cited = [int(n) for n in _INLINE.findall(body)]

    table: Dict[int, Dict[str, str]] = {}
    if references_block:
        for number, source_type, remainder in _REF_LINE.findall(references_block):
            table[int(number)] = {"source_type": source_type, "text": remainder.strip()}

    audit = CitationAudit(cited=sorted(set(cited)))
    for number in sorted(set(cited)):
        entry = table.get(number)
        if entry is None:
            audit.ghosts.append(number)
            continue
        row = _lookup(corpus, entry["text"])
        if row is None:
            audit.missing_items.append(number)
            continue
        audit.resolved.append(number)
        if not (row["claimable"] or "").strip():
            audit.crowd_only.append(number)

    audit.dangling = sorted(number for number in table if number not in set(cited))
    return audit


def _lookup(corpus: Corpus, reference_text: str) -> Optional[Dict[str, Any]]:
    """Resolve a reference line back to a stored item, url first then title."""
    url_match = re.search(r"https?://\S+", reference_text or "")
    if url_match:
        row = corpus._conn.execute(
            "SELECT id, claimable FROM items WHERE url=?", (url_match.group(0),)
        ).fetchone()
        if row is not None:
            return dict(row)
    title = re.sub(r"^`[^`]*`", "", reference_text).strip()
    if title:
        row = corpus._conn.execute(
            "SELECT id, claimable FROM items WHERE title=?", (title.split(" — ")[0].strip(),)
        ).fetchone()
        if row is not None:
            return dict(row)
    return None
