"""Convert captured discussions into attributed, searchable community sections."""
from __future__ import annotations

import hashlib
from urllib.parse import quote

from src.models import Section
from src.corpus.sections import marker_sections_to_model


_METADATA_FIELDS = ("provenance", "metrics", "ids", "published_text", "body_source_url", "sources", "document")
_CHILD_FIELDS = ("comments", "replies", "children", "answers")


def body_sections(text: str, tier: str) -> list[Section]:
    """Honor old discussion markers; new unmarked prose retains declared authorship."""
    sections = marker_sections_to_model(text)
    if not any(section.locator for section in sections):
        return [Section(tier=tier, text=text)]
    if tier == "community":
        sections = [section.model_copy(update={"tier": "community"}) for section in sections]
    return sections


def captured_metadata(row: dict) -> dict:
    """Keep declared source fields, not browser credentials or process helpers."""
    metadata = {name: row[name] for name in _METADATA_FIELDS if row.get(name)}
    source = row.get("source") or row.get("platform")
    if source:
        metadata["source_key"] = source
    if row.get("kind"):
        metadata["source_kind"] = row["kind"]
    return metadata


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _author(comment: dict) -> str:
    value = comment.get("author") or comment.get("user") or comment.get("member")
    if isinstance(value, dict):
        value = value.get("name") or value.get("nickname") or value.get("uname") or value.get("display_name")
    return _text(value)


def discussion_sections(row: dict, locator: str) -> list[Section]:
    """Preserve comments and nested replies without asserting them as author prose."""
    stack = []
    for field in reversed(_CHILD_FIELDS):
        values = row.get(field)
        if isinstance(values, list):
            stack.extend((value, None, field) for value in reversed(values))
    sections, seen, visited = [], set(), set()
    while stack:
        comment, inherited_parent, relation = stack.pop()
        if not isinstance(comment, dict) or id(comment) in visited:
            continue
        visited.add(id(comment))
        body = _text(comment.get("text")) or _text(comment.get("body")) or _text(comment.get("content"))
        if not body and isinstance(comment.get("content"), dict):
            body = _text(comment["content"].get("message"))
        author = _author(comment)
        native_id = next((str(comment[key]) for key in ("id", "rpid", "comment_id", "reply_id")
                          if isinstance(comment.get(key), (str, int)) and str(comment[key]).strip()), "")
        identity = native_id or "text-" + hashlib.sha256((author + "\0" + body).encode("utf-8")).hexdigest()[:20]
        parent = comment.get("parent_id") or comment.get("reply_to") or inherited_parent
        if not isinstance(parent, (str, int)):
            parent = inherited_parent
        parent = str(parent) if parent not in (None, "", 0, "0") else None
        for field in reversed(_CHILD_FIELDS):
            values = comment.get(field)
            if isinstance(values, list):
                stack.extend((value, identity, field) for value in reversed(values))
        if not body or identity in seen:
            continue
        seen.add(identity)
        metadata = {"id": identity, "relation": relation}
        if parent:
            metadata["parent_id"] = parent
        for field in ("likes", "like", "score", "date", "created_at", "published_at"):
            if field in comment and isinstance(comment[field], (str, int, float)):
                metadata[field] = comment[field]
        own_url = _text(comment.get("url"))
        section_locator = own_url if own_url.startswith(("http://", "https://")) else (
            locator.split("#", 1)[0] + "#comment-" + quote(identity, safe=""))
        sections.append(Section(tier="community", text=body, author=author or None,
                                locator=section_locator, meta=metadata))
    return sections
