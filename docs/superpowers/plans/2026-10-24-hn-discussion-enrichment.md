# HN Discussion Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich HN story search hits with a bounded, persistable comment tree.

**Architecture:** Preserve Algolia story IDs in the current source model, fetch descendant items from the official Firebase API with node/depth/time limits, and attach the normalized tree plus coverage metadata to the existing topic result and ingest paths.

**Tech Stack:** Python 3.11+, httpx.AsyncClient, existing TopicService, SQLite corpus/FTS5.

**Spec:** `docs/superpowers/specs/2026-10-24-hn-discussion-enrichment-design.md`

## Global Constraints

- At most 60 comment nodes per story and depth 4.
- At most 8 concurrent Firebase requests; stop at the caller's time budget.
- Preserve comment text as community evidence, with native IDs and parent relationships.
- Keep partial results and report why the tree is partial.
- Do not access login sessions or claim full coverage.

## Review Focus

- Missing/deleted Firebase nodes: keep traversing available descendants and do not invent text.
- Cyclic or duplicate child IDs: visit each item ID at most once.
- Deadline reached mid-tree: return completed nodes and mark coverage partial.
- Algolia row without a usable story ID: skip enrichment with an explicit reason.
- Nested text persistence: preserve parent IDs and community-only classification through SQLite round-trip.

---

### Task 1: Preserve HN story IDs in search rows

**Files:**
- Modify: `periscope/collection/catalog/community.py`

**Interfaces:**
- Produces `row["ids"]["objectID"]` for HN story search entries.

- [x] Add `objectID` to the declared IDs for `hn_ai`, `hn_ai_monthly`, and `hn_cn`.
- [x] Keep `hn_comments` identity behavior unchanged.

### Task 2: Add bounded public HN tree retrieval

**Files:**
- Create: `periscope/collection/platforms/hackernews.py`

**Interfaces:**
- `async def fetch_discussion(story_id: str, *, max_comments: int = 60, max_depth: int = 4, concurrency: int = 8, budget_seconds: float | None = 20.0, client: httpx.AsyncClient | None = None) -> tuple[list[dict], dict]`
- Returns normalized nested `comments` and coverage metadata.

- [x] Fetch each Firebase item once, cap nodes/depth/concurrency, and normalize comment text/IDs/parent fields.
- [x] Keep useful partial nodes on timeout or individual item failure.

### Task 3: Connect HN tree enrichment and persistence

**Files:**
- Modify: `periscope/topics/enrichment.py`
- Modify: `periscope/topics/runs.py`
- Modify: `periscope/operations/materials.py`

**Interfaces:**
- `--enrich` dispatches HN story rows to the HN adapter and attaches `comments` plus `discussion_coverage`.
- Run shards and corpus metadata retain `discussion_coverage`.

- [x] Pass the existing per-query enrichment limit and budget into the HN adapter.
- [x] Record partial status in the query notes without dropping successful nodes.
- [x] Add coverage to the explicit run-shard allowlist and persisted metadata.

### Task 4: Verify implementation

- [x] Run focused adapter, TopicService, GitHub/V2EX/Stack Overflow integration, and persistence tests: 40 passed.
- [x] Run the full test suite after platform enrichment changes: `pytest -q` reached 100%, exit code 0.
- [x] Run one anonymous public probe on HN story 8863, capped at 5 nodes; 5/71 reported comments fetched in 2.594 seconds, `comment_limit`, no errors. Output recorded as counts only.

Implementation note: HN syntax compilation, `git diff --check`, focused regressions, and the full suite have passed. The live result is a single public snapshot, not a coverage or reliability guarantee.
