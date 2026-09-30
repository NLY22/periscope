"""Import user-exported content into the evidence corpus, tiers included.

Why this exists instead of another scraper: the spec draws a hard line at
captcha solving, request signing and account pools (design spec §11), which is
exactly what 小红书 / 贴吧 / login-walled forums would require. The sanctioned
path is the user's own export — what their account can already see — handed to
Periscope as JSON.

The one thing this module must not get wrong is the tier. An import is not
trusted because it arrived through a new door: crowd text stays a lead unless
the payload declares it `primary`, and text with no tiering at all is stored
under `legacy_marker`, which `corpus.trust` discounts. Both are covered by
`tests/test_corpus_import.py`.

Payload shape (only `title` plus an identity are mandatory):

    {
      "items": [
        {
          "source_type": "rss",                  # one of SourceType's values
          "title": "某笔记：定价对比",
          "locator": "xhs:note:abc123",           # stable identity; url also works
          "url": "https://...",                   # optional, display only
          "author": "作者甲",
          "published_at": "2026-09-20T00:00:00+00:00",
          "time_basis": "published",              # published | crawled | unknown
          "metadata": {"source_label": "xiaohongshu"},
          "sections": [
            {"tier": "primary",   "text": "官方定价是每百万 token 2 元。"},
            {"tier": "community", "author": "路人乙", "text": "我觉得明明是 5 元。"}
          ]
        }
      ]
    }

`source_type` is enum-constrained on purpose: it selects the credibility prior
and the source family used by independence counting, so an import must pick the
closest real family (`rss` for article-like, `discourse`/`reddit` for forum-like,
`bilibili`/`youtube` for video) and put the human name in `metadata.source_label`.
Guessing a new family per import would let an import declare its own prior.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import ValidationError

from ..models import ContentItem, Section, SourceType
from .sections import claimable_of

# An import arrives as one JSON blob; a single item larger than this is almost
# certainly a whole page dump, and storing it would distort both clustering and
# the excerpt budget of every claim that later links to it.
MAX_ITEM_CHARS = 200_000

IMPORT_ID_PREFIX = "import"


class IngestError(ValueError):
    """The payload was rejected, with the reason a reader can act on."""


SOURCE_TYPE_CHOICES = tuple(member.value for member in SourceType)


def _items_of(payload: Union[Dict[str, Any], List[Any]]) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return items
        if payload.get("source_type") or payload.get("sections") or payload.get("content"):
            return [payload]          # a single item handed over bare
    raise IngestError(
        "payload must be {\"items\": [...]} or a list of items; got "
        f"{type(payload).__name__}"
    )


def _parse_time(raw: Any) -> Optional[datetime]:
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise IngestError(f"published_at is not ISO-8601: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _sections_of(raw: Dict[str, Any], index: int) -> List[Section]:
    declared = raw.get("sections") or []
    if not isinstance(declared, list):
        raise IngestError(f"item {index}: sections must be a list")
    sections: List[Section] = []
    for position, entry in enumerate(declared):
        if not isinstance(entry, dict):
            raise IngestError(f"item {index}: sections[{position}] must be an object")
        text = str(entry.get("text") or "").strip()
        if not text:
            raise IngestError(f"item {index}: sections[{position}] has empty text")
        tier = entry.get("tier")
        if tier not in ("primary", "community"):
            raise IngestError(
                f"item {index}: sections[{position}].tier must be 'primary' or "
                f"'community', got {tier!r}"
            )
        body = dict(entry)
        body["text"] = text
        # How the text got here is not the exporter's call to make casually:
        # `author` means "I took this from the author's own page".
        body.setdefault("provenance", "manual_export")
        try:
            sections.append(Section.model_validate(body))
        except ValidationError as exc:
            raise IngestError(
                f"item {index}: sections[{position}] rejected: {exc.errors()[0]['msg']}"
            ) from exc
    if sections:
        return sections
    raise IngestError(f"item {index}: sections[] has no usable entry")


def _item_at(raw: Any, index: int) -> ContentItem:
    if not isinstance(raw, dict):
        raise IngestError(f"item {index}: must be an object")
    # Report every problem in this item at once. Someone editing a thirty-item
    # export by hand should not have to round-trip once per mistake.
    problems: List[str] = []
    source_type = str(raw.get("source_type") or "")
    if source_type not in SOURCE_TYPE_CHOICES:
        problems.append(
            f"unknown source_type {source_type!r}; choose from "
            + ", ".join(SOURCE_TYPE_CHOICES)
        )
    locator = str(raw.get("locator") or "").strip()
    url = raw.get("url") or None
    if not locator and not url:
        problems.append(
            "needs either url or locator (a stable identity, otherwise "
            "re-importing the same export creates a second copy)"
        )

    sections: List[Section] = []
    declared = raw.get("sections") or []
    content = str(raw.get("content") or "").strip()
    if declared:
        sections = _sections_of(raw, index)
    elif content:
        # Untiered dump: keep it, but say so. `legacy_marker` is the value the
        # trust model already discounts, because the tier was inferred, not declared.
        sections = [Section(tier="primary", text=content, provenance="legacy_marker")]
    else:
        problems.append("needs sections or content")

    total = sum(len(s.text) for s in sections)
    if total > MAX_ITEM_CHARS:
        problems.append(
            f"{total} chars exceeds the {MAX_ITEM_CHARS} limit; split it into "
            "the pieces an author actually published"
        )
    if problems:
        raise IngestError(f"item {index}: " + "; ".join(problems))

    identity = locator or str(url)
    item_id = str(raw.get("id") or "").strip() or (
        f"{IMPORT_ID_PREFIX}:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"
    )
    metadata = dict(raw.get("metadata") or {})
    metadata["imported"] = True
    if raw.get("source_label"):
        metadata.setdefault("source_label", str(raw["source_label"]))
    body: Dict[str, Any] = {
        "id": item_id,
        "source_type": source_type,
        "title": str(raw.get("title") or "").strip() or identity,
        "locator": identity,
        "sections": [s.model_dump() for s in sections],
        "author": raw.get("author"),
        "published_at": _parse_time(raw.get("published_at")),
        "time_basis": raw.get("time_basis") or "published",
        "metadata": metadata,
    }
    if url:
        body["url"] = str(url)
    try:
        return ContentItem.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise IngestError(
            f"item {index}: {first['loc']} rejected: {first['msg']}"
        ) from exc


def parse_import_payload(
    payload: Union[Dict[str, Any], List[Any]],
) -> List[ContentItem]:
    """Turn a validated payload into `ContentItem`s, tiers preserved.

    Raises `IngestError` naming the item index on the first problem, so a
    hand-edited JSON file fails with a sentence rather than a stack trace.
    Stricter than the import path, which rejects per item and keeps going --
    callers that want to preview an import must use `import_payload(dry_run=True)`
    instead, or the preview disagrees with what the write does.
    """
    raw_items = _items_of(payload)
    return [_item_at(raw, index) for index, raw in enumerate(raw_items)]


def prepare_import(
    payload: Union[Dict[str, Any], List[Any], str],
) -> Tuple[List[ContentItem], List[Dict[str, str]]]:
    """The one validation path both preview and write share.

    Splitting this out is what makes a dry run trustworthy: preview and import
    cannot drift, because they are the same loop over the same `_item_at`.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise IngestError(f"payload is not valid JSON: {exc}") from exc

    items: List[ContentItem] = []
    rejected: List[Dict[str, str]] = []
    for index, raw in enumerate(_items_of(payload)):
        try:
            items.append(_item_at(raw, index))
        except IngestError as exc:
            rejected.append({"index": str(index), "reason": str(exc)})
    return items, rejected


def preview_payload(payload: Union[Dict[str, Any], List[Any], str]) -> Dict[str, Any]:
    """What an import would do, without a database and without writing.

    Same `prepare_import` loop as the write, so the counts match -- the CLI's
    old `--dry-run` used the strict parser and would abort on a file the real
    import accepts 29/30 of. `items_new` is 0 here by definition, and the
    `dry_run` flag is what tells a caller which zero it is reading.
    """
    items, rejected = prepare_import(payload)
    return {
        "items_new": 0,
        "items_total_seen": len(items),
        "rejected": rejected,
        "claimable_nonempty": sum(1 for i in items if claimable_of(i)),
        "dry_run": True,
    }


def import_payload(
    corpus,
    payload: Union[Dict[str, Any], List[Any], str],
    *,
    tiering: str = "sections",
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Store an export and report what actually landed.

    A bad item is rejected and reported, not silently dropped and not fatal: a
    thirty-note export with one typo should still bring in twenty-nine.

    `dry_run=True` runs the identical validation and touches nothing -- no rows,
    no cluster pass -- so the panel can show a user what their pasted export
    would do before it does it.
    """
    if dry_run:
        return preview_payload(payload)

    items, rejected = prepare_import(payload)

    new_count = corpus.add_items(items, tiering=tiering, now=now) if items else 0
    corpus.recompute_clusters()
    return {
        "items_new": new_count,
        "items_total_seen": len(items),
        "rejected": rejected,
        "claimable_nonempty": sum(1 for i in items if claimable_of(i)),
        "dry_run": False,
    }
