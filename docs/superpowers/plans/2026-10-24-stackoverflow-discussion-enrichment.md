# Stack Overflow Discussion Enrichment Implementation Plan

**Goal:** Enrich Stack Overflow search rows with full question text, top answers, and comments tied to their source post.

**Architecture:** Preserve `question_id`, read question and vote-sorted answers through Stack Exchange API, then fetch comments for the question plus selected answer IDs. Patch both merged and raw rows, and preserve the resulting community evidence and coverage through the common run and corpus paths.

**Spec:** `docs/superpowers/specs/2026-10-24-stackoverflow-discussion-enrichment-design.md`

## Tasks

### 1. Add bounded API collection

- [x] Preserve question IDs on search results.
- [x] Fetch question details and vote-sorted answer bodies.
- [x] Fetch comments for the question and selected answers; retain user-directed reply information without fabricating reply parents.
- [x] Add request, answer, comment, and per-round time caps with partial coverage.
- [x] Add failure tests for missing questions and timeouts.

### 2. Connect investigation and persistence

- [x] Route Stack Overflow rows through `--enrich` and use the remaining round budget.
- [x] Preserve answer/comment structure and question metadata through shards and SQLite.
- [x] Report answer/comment coverage in the investigation summary.

### 3. Verify and document

- [x] Run anonymous question 231767 probe, capped at 2 answers / 3 comments: 961-character body, 51 answers and 22 comments returned, 2 answers and 3 comments saved, `answer_limit` / `comment_limit`, 1.750 seconds, no errors. Output recorded as counts only.
- [x] Update matrix, pipeline docs, and official API references.
- [x] Run focused platform and persistence regressions: 40 tests passed.
- [x] Full project suite after Stack Overflow integration: `pytest -q` reached 100%, exit code 0.
- [x] Run `py_compile` and `git diff --check`.
