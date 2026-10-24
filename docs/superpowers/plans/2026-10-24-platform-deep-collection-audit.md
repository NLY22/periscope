# Platform Deep Collection Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish an evidence-backed, reproducible depth baseline for the 12 agreed platforms, separating search, author text, comments, replies, pagination, and save/retrieval behavior.

**Architecture:** Keep this as an audit batch over existing adapters. Record code-path, historical-artifact, and current anonymous-probe evidence separately; use temporary output and an isolated SQLite corpus for persistence checks. Do not add or imply new platform capabilities.

**Tech Stack:** Python 3.12, existing `SourceRegistry` / collection adapters, SQLite FTS5, pytest, Markdown audit record.

**Spec:** `docs/superpowers/specs/2026-10-24-platform-deep-collection-audit-design.md`

## Global Constraints

- Cover exactly: XHS, Zhihu, Weibo, Bilibili, Douyin, Kuaishou, V2EX, GitHub, Stack Overflow, Hacker News, CSDN, and Juejin.
- An existing source configuration is not evidence that body, comments, replies, or updates work.
- Live probes use one bounded public query per platform; stop on login, CAPTCHA, or access restriction.
- Never read personal browser profiles, cookies, or secrets; never write the real corpus.
- Missing evidence is `unknown` or `not verified`, not a claim that the platform has no content.
- Do not claim API superiority or full pagination from this audit.

## Review Focus

- A search card is mistaken for a full post: independently record the source field that contains author text and whether a detail request exists.
- A comment count is mistaken for comment content: require captured comment text and relation fields as evidence.
- Partial pages are mistaken for complete coverage: record page parameters, page cap, and actual observed pages.
- Empty or blocked live response is mistaken for unsupported content: preserve status and reason.
- A collected row is mistaken for persistent retrieval: round-trip only through an isolated corpus and verify source URL and section relations.

---

### Task 1: Build the code and history baseline

**Files:**
- Create: `docs/platform-deep-collection-matrix.md`
- Read: `periscope/collection/catalog/community.py`
- Read: `periscope/collection/extensions.py`
- Read: `periscope/collection/strategies/`
- Read: `periscope/collection/platforms/`
- Read: `.periscope-scratch/` historical platform artifacts, if present

**Interfaces:**
- Produces a 12-row matrix with separate columns for search, author text, comments, replies, pagination/coverage, update behavior, save/retrieval, and evidence class.
- Status values are limited to `present`, `partial`, `missing`, and `unknown`; every non-unknown claim cites a code path or artifact.

- [x] **Step 1: Record each platform's actual source keys and detail/comment entry points**

- [x] **Step 2: Inspect historical artifacts without aggregating duplicate query rows as unique content**

- [x] **Step 3: Write the matrix and mark unsupported dimensions explicitly**

- [x] **Step 4: Review every populated cell against its cited evidence**

### Task 2: Verify persistence independently of live platform access

**Files:**
- Test: `tests/collection/test_material_persistence.py`
- Read: `periscope/operations/materials.py`
- Read: `periscope/operations/ingest.py`
- Read: `periscope/operations/fulltext.py`

**Interfaces:**
- Uses an isolated temporary corpus and the existing conversion/persistence contract.
- Verifies that captured post text, comments/replies (when present), provenance, native IDs, parent relations, and source locators survive save and retrieval.

- [x] **Step 1: Map existing persistence tests to the audit's required fields**

- [x] **Step 2: Extend the round-trip case to close and reopen the corpus, then assert stored sections and provenance fields.**

- [x] **Step 3: `tests/collection/test_material_persistence.py` → 16 passed.**

### Task 3: Run bounded anonymous probes and finalize findings

**Files:**
- Modify: `docs/platform-deep-collection-matrix.md`
- Create: `.periscope-scratch/platform-depth-audit-20261024/` probe outputs (ignored scratch data)
- Read: existing source and browser strategy entry points

**Interfaces:**
- At most one short, public query per platform; no personal login profile or credentials.
- Each observation records UTC/local timestamp, query, mode, elapsed time, row count, observed content layers, and status/error.
- If safe isolated probing is unavailable for an adapter, mark the probe as unrun and retain the static/history baseline.

- [x] **Step 1: Confirm how each target maps to its existing anonymous adapter and cap each probe**

- [x] **Step 2: Run one bounded HTTP request per source; run each browser source once with a fresh profile and zero scrolls.**

- [x] **Step 3: Inspect field names and text lengths only; invalidate any run whose source configuration could load a saved session.**

- [x] **Step 4: Add all 12 live statuses, invalidate cookie-configured first results, rerun those two with empty `cookies_file`, and preserve empty/unknown distinctions in the matrix.**

- [x] **Step 5: Persistence + docs guard → 65 passed; full suite → 1,398 passed, 8 warnings; `git diff --check` passed.**

## Self-Review

- Scope coverage: all 12 agreed platforms are required in Task 1; Tasks 2–3 establish persistence and live evidence without adding adapter behavior.
- Review-focus coverage: source distinction and pagination are recorded in Task 1; persistence fields in Task 2; blocked/empty behavior in Task 3.
- This plan deliberately does not implement missing adapters; gaps become the next ranked implementation batch.
