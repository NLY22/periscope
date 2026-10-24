"""Apply recovered article text without losing provenance or discussion tiers."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from src.corpus.sections import claimable_from_sections, marker_sections_to_model
from src.corpus.simhash import fingerprint
from src.corpus.trust import compute_features, trust_score
from src.models import Section, sections_to_content


def _object(raw: str) -> dict:
    value = json.loads(raw or "{}")
    if not isinstance(value, dict):
        raise ValueError("Expected stored metadata object")
    return value


def mark_fulltext_failure(conn: sqlite3.Connection, item_id: str, details: dict) -> bool:
    row = conn.execute("SELECT metadata_json FROM items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        return False
    metadata = _object(row[0])
    metadata["fulltext_failed"] = details
    conn.execute("UPDATE items SET metadata_json=? WHERE id=?",
                 (json.dumps(metadata, ensure_ascii=False), item_id))
    return True


def recover_body(conn: sqlite3.Connection, record: dict) -> bool:
    """Update one existing item; the caller owns the transaction and commit."""
    if not isinstance(record, dict):
        return False
    item_id, text = record.get("id"), record.get("text")
    if not isinstance(item_id, str) or not isinstance(text, str) or not 400 <= len(text.strip()) <= 60000:
        return False
    text = text.strip()
    cursor = conn.execute("SELECT * FROM items WHERE id=?", (item_id,))
    stored = cursor.fetchone()
    if stored is None:
        return False
    row = dict(zip((col[0] for col in cursor.description), stored))
    try:
        metadata = _object(row["metadata_json"])
        raw_sections = json.loads(row["sections_json"] or "[]")
        sections = [Section.model_validate(value) for value in raw_sections]
        if not sections:
            sections = marker_sections_to_model(row["content"])
            provenance = metadata.get("provenance")
            if isinstance(provenance, dict) and provenance.get("content_role") == "community":
                sections = [section.model_copy(update={"tier": "community"}) for section in sections]
        published = datetime.fromisoformat(row["published_at"])
    except (ValueError, TypeError):
        # An invalid existing tier must not silently become asserted primary text.
        return False
    if sections and sections[0].provenance in ("author", "manual_export", "legacy_marker") and not sections[0].meta.get("relation"):
        sections[0] = sections[0].model_copy(update={"text": text})
    else:
        # Keep transcripts/OCR/inferred sections as declared. Newly recovered prose
        # is only a lead when its author role cannot be established from the item.
        sections.insert(0, Section(tier="community", text=text, meta={"recovered_body": True}))
    content = sections_to_content(sections)
    claimable = claimable_from_sections(sections)
    metadata.pop("fulltext_failed", None)
    url = record.get("url")
    metadata["fulltext"] = {"chars": len(text), "fetched_from": url if isinstance(url, str) else "",
                            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    section_values = [section.model_dump() for section in sections]
    if (row["content"] == content and row["claimable"] == claimable and
            raw_sections == section_values and _object(row["metadata_json"]) == metadata):
        return False
    fp = fingerprint(row["title"] + "\n" + content)
    signed_fp = fp if fp < (1 << 63) else fp - (1 << 64)
    features = compute_features(source_type=row["source_type"], text=claimable, author=row["author"],
                                provenance=sections[0].provenance, confidence=sections[0].confidence,
                                published_at=published, time_basis=row["time_basis"], meta=metadata)
    conn.execute("""UPDATE items SET content=?, claimable=?, sections_json=?, metadata_json=?,
                    fingerprint=?, cluster_id=NULL, trust=?, trust_features_json=? WHERE id=?""",
                 (content, claimable, json.dumps(section_values, ensure_ascii=False),
                  json.dumps(metadata, ensure_ascii=False), signed_fp, trust_score(features),
                  json.dumps(features.to_dict(), ensure_ascii=False), item_id))
    return True


def apply_cache(corpus, path: Path) -> int:
    """Stream complete JSONL records; a bad cache line cannot hide later records."""
    count = 0
    with Path(path).open("r", encoding="utf-8-sig") as stream, corpus._conn:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            count += int(recover_body(corpus._conn, record))
    return count
