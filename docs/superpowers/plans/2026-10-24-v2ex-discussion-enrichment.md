# V2EX Discussion Enrichment Implementation Plan

**Goal:** Enrich V2EX search results with full topic text and a bounded set of public replies.

**Architecture:** Parse the existing V2EX topic permalink, call the existing public topic and replies endpoints with one shared per-topic deadline, then patch the visible and raw investigation rows so the established shard and community corpus paths retain the content and coverage.

**Spec:** `docs/superpowers/specs/2026-10-24-v2ex-discussion-enrichment-design.md`

## Tasks

### 1. Add bounded public API adapter

- [x] Fetch topic detail and replies concurrently; preserve useful partial data on timeout.
- [x] Limit stored replies to 60 and report the reply count, truncation, and errors.
- [x] Preserve topic membership without inventing reply-to links.
- [x] Add tests for topic normalization, reply cap, and timeout.

### 2. Connect investigation and persistence

- [x] Route V2EX search hits through `--enrich`.
- [x] Use only collection budget remaining after the platform search stage.
- [x] Preserve topic detail metadata, comments, and coverage through run shards and SQLite.
- [x] Surface V2EX reply coverage in investigation notes.

### 3. Verify and document

- [x] Run anonymous topic 1 probe capped at 5 replies: body 1,619 characters, API returned 187 entries, 5 saved, `comment_limit`, 2.063 seconds, no errors. Output contains counts only.
- [x] Update the platform matrix and collection-pipeline documentation.
- [x] Run focused V2EX, GitHub, HN, Stack Overflow, and persistence regressions: 40 tests passed.
- [x] Full project suite after final edits: `pytest -q` reached 100%, exit code 0.
- [x] Run `py_compile` and `git diff --check`.
