"""Bounded lexical discovery of earlier versioned evidence."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

_GENERIC = {"还有呢", "还有吗", "继续", "再看看", "详细说说", "what", "about", "more", "the", "and", "this", "that"}


def _terms(text: str) -> set[str]:
    for phrase in ("还有哪些", "还有什么", "有哪些", "哪些", "还有", "怎么样", "详细说说", "请问", "继续"):
        text = text.replace(phrase, " ")
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower())
    terms = set()
    for word in words:
        if word in _GENERIC:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
        elif len(word) >= 2:
            terms.add(word)
    return terms


def find_history(store, round_id: str, message: str, *, limit: int = 8) -> list[dict]:
    record = store.get_round(round_id)
    context = store.context(round_id)
    terms = _terms(message) or _terms(context["main_question"])
    if not terms:
        return []
    options = record["options"]
    seeds = options.get("seeds")
    hosts = {(urlsplit(url).hostname or "").lower().removeprefix("www.") for url in (seeds or [])}
    hosts.discard("")
    if seeds and not hosts:
        return []
    v2ex_only = "seeds" in options and not seeds
    scope, params = "", [round_id]
    if v2ex_only:
        scope += " AND obj.source='v2ex'"
    if hosts:
        patterns = [f"{scheme}://{prefix}{host}{suffix}" for host in hosts for scheme in ("http", "https")
                    for prefix in ("", "www.") for suffix in ("/%", ":%")]
        scope += " AND (" + " OR ".join("lower(obj.locator) LIKE ?" for _ in patterns) + ")"
        params.extend(patterns)
    selected_terms = sorted(terms, key=lambda term: (-len(term), term))[:40]
    term_filter = " OR ".join("lower(snapshot_json) LIKE ?" for _ in selected_terms)
    params.extend("%" + term + "%" for term in selected_terms)
    rows = store.conn.execute('''WITH ranked AS (SELECT e.object_id, e.version_id, r.id AS origin_round_id,
        o.observed_at, o.provenance_json, v.snapshot_json, obj.source, obj.locator,
        ROW_NUMBER() OVER (PARTITION BY e.object_id, COALESCE(json_extract(v.snapshot_json, '$.content_level'), 'full')
                           ORDER BY o.observed_at DESC, r.rowid DESC) AS position
        FROM investigation_rounds r JOIN investigation_round_evidence e ON e.round_id=r.id
        JOIN evidence_objects obj ON obj.id=e.object_id JOIN evidence_versions v ON v.id=e.version_id
        JOIN investigation_batches b ON b.round_id=r.id
        JOIN evidence_observations o ON o.batch_id=b.batch_id AND o.object_id=e.object_id AND o.version_id=e.version_id
        WHERE r.rowid < (SELECT rowid FROM investigation_rounds WHERE id=?) AND r.status != 'running' ''' + scope + ''')
        SELECT * FROM ranked WHERE position=1 AND (''' + term_filter + ''')
        ORDER BY observed_at DESC LIMIT 2000''', params).fetchall()
    candidates, seen = [], set()
    for row in rows:
        obj = row["object_id"]
        host = (urlsplit(row["locator"]).hostname or "").lower().removeprefix("www.")
        if v2ex_only and row["source"] != "v2ex":
            continue
        if hosts and host not in hosts:
            continue
        snapshot = json.loads(row["snapshot_json"])
        identity = (obj, snapshot.get("content_level") or "full")
        if identity in seen:
            continue
        seen.add(identity)
        available = _terms((snapshot.get("title") or "") + " " + (snapshot.get("text") or ""))
        score = len(terms & available)
        if score:
            candidates.append((score, {"object_id": obj, "version_id": row["version_id"],
                "round_id": row["origin_round_id"], "observed_at": row["observed_at"],
                "coverage": json.loads(row["provenance_json"]).get("discussion_coverage") or {}}))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in candidates[:limit]]
