"""Derive uncompleted discussion targets from durable investigation evidence."""
from __future__ import annotations

import json
import time
from urllib.parse import urlsplit

from .reuse import _terms


_SUPPORTED = {"v2ex", "hackernews", "github", "stackoverflow", "csdn", "juejin", "zhihu", "weibo", "bilibili", "xhs", "douyin", "web_crawl"}
_ROOT_TYPES = {"topic", "story", "issue", "pull_request", "question", "article", "document", "answer", "post", "video", "note"}
_CATALOG_KEYS = {"github": "github_issues", "stackoverflow": "stackexchange_search",
                 "csdn": "csdn_search", "juejin": "juejin_search", "zhihu": "zhihu_search",
                 "weibo": "weibo_search", "bilibili": "bili_search"}


def pending_candidates(store, round_id: str, query: str, *, limit: int = 12) -> list[dict]:
    """Find unread or partially read objects from earlier rounds of this session."""
    current = store.get_round(round_id)
    if current is None:
        raise ValueError("round not found")
    seeds = current["options"].get("seeds") or []
    hosts = {(urlsplit(seed).hostname or "").lower().removeprefix("www.") for seed in seeds}
    hosts.discard("")
    if seeds and not hosts:
        return []
    allowed = _SUPPORTED
    if not seeds and current["options"].get("max_pages") is not None:
        allowed = {"v2ex", "hackernews"}
    rows = store.conn.execute('''WITH ranked AS (
        SELECT e.object_id, COALESCE(redirect.canonical_id, e.object_id) AS canonical_id,
               e.version_id, obj.source, obj.locator, v.snapshot_json,
               o.provenance_json, o.observed_at,
               ROW_NUMBER() OVER (PARTITION BY COALESCE(redirect.canonical_id, e.object_id),
                 COALESCE(json_extract(v.snapshot_json, '$.content_level'), 'full')
                 ORDER BY o.observed_at DESC, r.rowid DESC, o.batch_id DESC) AS position
        FROM investigation_rounds r
        JOIN investigation_round_evidence e ON e.round_id=r.id
        JOIN evidence_objects obj ON obj.id=e.object_id
        LEFT JOIN evidence_object_redirects redirect ON redirect.object_id=e.object_id
        JOIN evidence_versions v ON v.id=e.version_id
        JOIN investigation_batches b ON b.round_id=r.id
        JOIN evidence_observations o ON o.batch_id=b.batch_id AND o.object_id=e.object_id AND o.version_id=e.version_id
        WHERE r.investigation_id=? AND r.rowid < (SELECT rowid FROM investigation_rounds WHERE id=?)
          AND r.status != 'running'
        ) SELECT * FROM ranked WHERE position=1 ORDER BY observed_at DESC''',
        (current["investigation_id"], round_id)).fetchall()
    groups = {}
    for row in rows:
        source = row["source"]
        host = (urlsplit(row["locator"]).hostname or "").lower().removeprefix("www.")
        if source not in allowed or (hosts and host not in hosts):
            continue
        snapshot = json.loads(row["snapshot_json"])
        if source == "web_crawl" and not snapshot.get("type"):
            snapshot = {**snapshot, "type": "document", "native_id": row["locator"], "content_level": "full"}
        if snapshot.get("type") not in _ROOT_TYPES:
            continue
        coverage = json.loads(row["provenance_json"]).get("discussion_coverage") or {}
        group = groups.setdefault(row["canonical_id"], {})
        level = snapshot.get("content_level") or "full"
        group[level] = {"source": source, "object_id": row["object_id"], "canonical_id": row["canonical_id"], "version_id": row["version_id"],
                        "snapshot": snapshot, "coverage": coverage, "observed_at": row["observed_at"]}
    terms = _terms(query)
    ranked = []
    for versions in groups.values():
        full, card = versions.get("full"), versions.get("search_result")
        if full:
            coverage = full["coverage"]
            if coverage.get("status") not in {"partial", "not_attempted"}:
                continue
            old_limit = int(coverage.get("max_comments") or coverage.get("max_replies") or 0)
            if old_limit >= 60 and not coverage.get("errors"):
                continue
            item = dict(full)
            item["max_comments"] = min(60, max(20, old_limit * 2))
        elif card:
            if card["coverage"].get("status") == "unsupported":
                continue
            item = dict(card)
            item["max_comments"] = 20
        else:
            continue
        snapshot = item["snapshot"]
        if item["source"] == "weibo":
            aliases = store.conn.execute("SELECT alias FROM evidence_aliases WHERE source='weibo' AND kind='post' AND object_id=?",
                                         (item["canonical_id"],)).fetchall()
            numeric = [row[0] for row in aliases if row[0].isdigit()]
            if numeric:
                item["fetch_id"] = max(numeric, key=len)
        score = len(terms & _terms((snapshot.get("title") or "") + " " + (snapshot.get("text") or "")))
        if terms and not score:
            continue
        ranked.append((score, item))
    ranked.sort(key=lambda pair: (pair[0], pair[1]["observed_at"]), reverse=True)
    # Alternate sources so one long result page cannot consume the whole round.
    by_source = {}
    for _, item in ranked:
        by_source.setdefault(item["source"], []).append(item)
    out = []
    while by_source and len(out) < max(0, limit):
        for source in list(by_source):
            out.append(by_source[source].pop(0))
            if not by_source[source]:
                del by_source[source]
            if len(out) >= limit:
                break
    return out


async def stream_pending_candidates(query: str, candidates: list[dict], budget: float):
    """Revisit saved candidates within a bounded budget, retaining partial results."""
    from periscope.collection.crawling.engine import CrawlOptions
    from .browser_sources import stream_browser_detail
    from .catalog import stream_catalog_detail
    from .crawl import CollectionIncomplete, stream_crawl

    deadline = time.monotonic() + budget
    remaining_nodes = 300
    incomplete = []
    for candidate in candidates[:12]:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or remaining_nodes <= 0:
            incomplete.append("time_or_comment_budget")
            break
        snapshot = candidate["snapshot"]
        source = candidate["source"]
        url = snapshot.get("url") or ""
        comment_limit = min(remaining_nodes, int(candidate.get("max_comments") or 20))
        remaining_nodes -= comment_limit
        if source == "web_crawl":
            options = CrawlOptions((url,), max_pages=1, depth=0, budget=remaining, include_seed=True)
            batches = stream_crawl(query, options)
        elif source in {"v2ex", "hackernews"}:
            pages = 2 if source == "v2ex" else comment_limit + 1
            options = CrawlOptions((url,), max_pages=pages, depth=min(4, max(0, int(candidate.get("max_depth", 2)))), budget=remaining)
            batches = stream_crawl(query, options, reply_limit=comment_limit)
        elif source in _CATALOG_KEYS or source in {"xhs", "douyin"}:
            source_key = _CATALOG_KEYS[source] if source in _CATALOG_KEYS else source + "_browser"
            card = {"source": source_key, "kind": "community", "_origin": "platform",
                    "title": snapshot.get("title") or "", "text": snapshot.get("text") or "",
                    "author": snapshot.get("author") or "", "url": url,
                    "date": str(snapshot.get("published_at") or ""),
                    "evidence": {"source": source, "type": snapshot.get("type"),
                                 "native_id": snapshot.get("native_id"),
                                 "content_level": snapshot.get("content_level") or "full"},
                    "provenance": {"method": "followup", "content_role": "community"}}
            if source == "weibo" and candidate.get("fetch_id"):
                card["ids"] = {"id": candidate["fetch_id"]}
            batches = (stream_browser_detail(card, budget=remaining, max_comments=comment_limit)
                       if source in {"xhs", "douyin"} else
                       stream_catalog_detail(card, budget=remaining, max_comments=comment_limit))
        else:
            continue
        try:
            async for batch in batches:
                yield batch
        except CollectionIncomplete as exc:
            incomplete.append(str(exc))
        except Exception as exc:
            incomplete.append(source + ":" + type(exc).__name__)
    if incomplete:
        raise CollectionIncomplete(("sources:" + ",".join(incomplete))[:200])
