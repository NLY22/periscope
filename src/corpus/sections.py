"""Split a scraped item body into a claimable layer and a discussion layer.

Why this exists: the wide sources Periscope collects (video comments, forum
floors, V2EX replies) are appended into one `ContentItem.content` string by
the scrapers, marked with headings like 【评论区 Top】. The claim pipeline and
the research evidence gatherer both read that whole string, so a stranger's
opinion in a comment thread can become (a) the source of an extracted claim,
(b) an "independent source" for grading, and (c) a quoted snippet in a report.
Widening sources is the point of this fork; separating what the *author*
asserted from what the *crowd* said is what keeps that width analysable.

Tiers
-----
primary    written by the item's own author: article body, RSS full text,
           video description, creator-authored CC transcript, the opening
           post of a thread. This is the only tier claims may be distilled
           from, evidence may link to, and reports may quote.
community  replies, comment blocks, follow-up floors. Kept in the corpus and
           searchable as leads, never counted as an independent source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

TIER_PRIMARY = "primary"
TIER_COMMUNITY = "community"

# Marker -> tier, as written by the scrapers. Matched as a whole line so a
# stray 【 in prose does not split anything.
_MARKERS = {
    "【视频字幕节选】": TIER_PRIMARY,
    "【评论区 Top】": TIER_COMMUNITY,
    "【评论区】": TIER_COMMUNITY,
    "【回复精选】": TIER_COMMUNITY,
    "【楼层讨论】": TIER_COMMUNITY,
}

_MARKER_LINE = re.compile(
    r"^[ \t]*(" + "|".join(re.escape(m) for m in _MARKERS) + r")[ \t]*$",
    re.MULTILINE,
)


@dataclass
class Section:
    """One contiguous block of an item body, tagged with its authorship tier."""

    tier: str
    marker: str | None
    text: str


def split_sections(content: str | None) -> List[Section]:
    """Break an item body into sections at scraper-inserted marker lines.

    Text before the first marker is the author's own body (`primary`); a CC
    transcript block is creator-written and therefore also `primary`, while
    comment/reply/floor blocks are `community`.
    """
    body = (content or "").strip("\n")
    if not body:
        return []

    out: List[Section] = []
    head = body[: _first_marker_pos(body)]
    if head.strip():
        out.append(Section(TIER_PRIMARY, None, head.strip()))

    for match in _MARKER_LINE.finditer(body):
        marker = match.group(1)
        start = match.end()
        nxt = _MARKER_LINE.search(body, start)
        end = nxt.start() if nxt else len(body)
        text = body[start:end].strip()
        if text:
            out.append(Section(_MARKERS[marker], marker, text))
    return out


def _first_marker_pos(body: str) -> int:
    match = _MARKER_LINE.search(body)
    return match.start() if match else len(body)


def tiered_text(content: str | None, tier: str) -> str:
    """Concatenate the sections of one tier, in document order."""
    return "\n\n".join(s.text for s in split_sections(content) if s.tier == tier).strip()


def claimable_text(content: str | None) -> str:
    """The author-written part of an item — the only text evidence may rest on."""
    text = tiered_text(content, TIER_PRIMARY)
    return text if text else ""


def community_text(content: str | None) -> str:
    """The crowd-written part of an item (replies, comments, later floors)."""
    return tiered_text(content, TIER_COMMUNITY)


def has_community(content: str | None) -> bool:
    return bool(community_text(content))
