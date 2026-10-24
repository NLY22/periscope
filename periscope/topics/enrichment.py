"""Enrich selected video results and return patches for raw run records."""

from __future__ import annotations

import asyncio
import re
import time


def _enrich_topk(merged: list[dict], k: int = 5) -> tuple[list[dict], dict, dict]:
    """Fill in counters/comments/transcripts for the top-K *enrichable* rows.

    "Enrichable" rather than plain top-K, because the two sets do not overlap
    in practice: on a news topic the top results are articles (they have body
    text, which the ranking rewards), and an article has no B站 video to
    enrich. Selecting `merged[:k]` there found zero targets and reported
    "enriched 0 rows", which looks like a broken feature and is really a
    selection bug. So the scan skips rows without a video id and enriches the
    first `k` that have one.

    Returns `(merged, stat, patches)` where `patches` is keyed by URL. The patch
    map exists because the merge **copies** each row into its cluster head: a
    dict updated on `merged` never reaches the original `plat_rows` list, and
    the run shard is written from those originals. Patching both is what makes
    the enrichment visible in the shard and not only on screen.
    """
    stat = {"done": 0, "elapsed": 0.0, "failed": [],
            "no_transcript": 0, "targets": 0}
    targets = []
    for row in merged:
        if len(targets) >= k:
            break
        ids = row.get("ids") or {}
        bvid = ids.get("bvid")
        if not bvid and (row.get("url") or ""):
            match = re.search(r"(BV[0-9A-Za-z]{10})", row["url"])
            bvid = match.group(1) if match else None
        if bvid:
            targets.append((row, bvid, _row_key(row)))
    if not targets:
        return merged, stat, {}

    stat["targets"] = len(targets)
    t0 = time.monotonic()
    try:
        from periscope.collection.platforms import bilibili as bilibili_enrich
    except Exception as exc:  # noqa: BLE001
        stat["failed"].append("import:%s" % type(exc).__name__)
        stat["elapsed"] = time.monotonic() - t0
        return merged, stat, {}

    try:
        got = asyncio.run(bilibili_enrich.enrich_many(
            [bvid for _row, bvid, _key in targets],
            concurrency=4, with_comments=True))
    except Exception as exc:  # noqa: BLE001
        stat["failed"].append("run:%s" % type(exc).__name__)
        stat["elapsed"] = time.monotonic() - t0
        return merged, stat, {}

    patches: dict = {}
    for row, bvid, key in targets:
        result = got.get(bvid)
        if result is None:
            stat["failed"].append(bvid)
            continue
        patch = result.as_patch()
        row.update(patch)
        if key:
            patches[key] = patch
        if result.metrics or result.has_transcript:
            stat["done"] += 1
        if not result.has_transcript:
            stat["no_transcript"] += 1
        if result.errors:
            stat["failed"].extend("%s:%s" % (bvid, e) for e in result.errors[:1])

    stat["elapsed"] = time.monotonic() - t0
    return merged, stat, patches


def _row_key(row: dict) -> str:
    """Stable identity for matching a merged row back to its original."""
    return (row.get("url") or "").strip() or (row.get("title") or "").strip()
