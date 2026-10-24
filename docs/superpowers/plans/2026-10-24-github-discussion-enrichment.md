# GitHub Discussion Enrichment Implementation Plan

**Goal:** Attach bounded GitHub issue and pull request discussion trees to the existing investigation enrichment and persistence flow.

**Architecture:** Preserve search identity fields, fetch issue and review comments with the GitHub REST API under bounded pagination and time, then write comments and coverage metadata through TopicService's existing row patching and corpus ingestion paths.

**Spec:** `docs/superpowers/specs/2026-10-24-github-discussion-enrichment-design.md`

## Tasks

### 1. Preserve native search identity

- [x] Add a regression test for issue number, repository URL, and PR identity.
- [x] Declare those identity fields on `github_issues`.

### 2. Build bounded comment and review collection

- [x] Add REST collection with a 60-entry cap, two 30-entry pages per endpoint, and the caller's aggregate time budget.
- [x] Preserve issue comment permalinks and review reply parent IDs, path, and line.
- [x] Keep completed comments and report partial status when a request, page, or comment cap is reached.
- [x] Add behavior tests for nested replies and timeout partial results.

### 3. Connect the investigation and save path

- [x] Route GitHub issue / PR rows through `--enrich` and patch both visible rows and raw run rows.
- [x] Report GitHub discussion coverage in the round summary.
- [x] Preserve review metadata in community sections after SQLite round-trip.

### 4. Verify and document

- [x] Focused collection and persistence suite: 30 tests passed.
- [x] Full test suite after final code changes: `pytest -q` reached 100%, exit code 0.
- [x] `py_compile` for changed collection, enrichment, service, and persistence modules.
- [x] `git diff --check`.
- [x] Run one anonymous public PR probe capped at 3 saved comments / one page: 11 API comments returned, 3 kept, `comment_limit`, 2.015 seconds, no errors. Output recorded as counts only.
- [x] Update the platform matrix with the bounded live observation and its limits.
