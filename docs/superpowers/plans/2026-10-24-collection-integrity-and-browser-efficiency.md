# Collection integrity and browser efficiency implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan inline. One read-only review at the end; no implementer agents.

**Goal:** Make captured discussions searchable without promoting comments into author evidence, preserve provenance during body recovery, and avoid unnecessary browser waiting or losing partial results.

**Architecture:** Keep the HTTP-first, explicit deep-browser path. Centralize discussion normalization and recovered-body updates in operations helpers; keep browser runtime/progress primitives in the platform package. Reuse existing SQLite/FTS triggers and model sections. Do not replace the research architecture or global append-only item semantics in this batch.

**Tech stack:** Python 3.11+, SQLite/FTS5, Pydantic sections, optional Playwright, pytest.

**Spec:** Existing source goals in `docs/source-system.md`, verified issues in `docs/project-audit-2026-10-22.md`, and the user's current request to improve resource use and the collection chain.

## Global constraints

- Do not operate on the real corpus, login state, accounts or live platforms during tests.
- Preserve compatibility imports, source filters, partial-failure reporting, and source ownership/tier boundaries.
- Browser execution stays headless by default and restricted to explicitly selected/deep-mode adapters.
- No new dependencies, browser/signature bypass, multi-account system, blanket automatic browser fallback or unmeasured performance claims.
- Keep unrelated working-tree changes; no reset, commit or database migration in this batch.

## Review focus

- A word appearing only in a nested reply must survive run save → ingest → all-tier search, with author and parent relation retained.
- Community-only or unasserted sections must stay excluded from claimable text after body recovery.
- Recovery success and failure must preserve original provenance, metrics, sections and searchable derivatives.
- A timed-out browser worker must retain only its own complete checkpoint, including empty, malformed or mixed rows safely.
- Fast results must avoid fixed multi-second sleep; busy profiles must not be unlocked by deleting browser locks.

## Task 1: Discussion preservation

- [x] Add failing isolated round-trip tests for HTTP/UGC comments and nested replies, attribution, empty/malformed records, and author-only indexing.
- [x] Add `periscope/operations/materials.py` to normalize comments/replies into community sections with stable locators/parent links and preserve captured fields.
- [x] Wire the helper into both ingestion branches, query-run preservation and topic platform pass-through where captured discussion was dropped.
- [x] Run ingestion, evidence-tier, run-store and source-system checks.

## Task 2: Body recovery integrity

- [x] Add failing tests exercising the real fulltext-only and full ingest paths, metadata preservation, community/unasserted tiers, indexes and malformed cache rows.
- [x] Add `periscope/operations/fulltext.py` for one transactional update implementation and streaming cache application; route ingest and backfill through it.
- [x] Recalculate content, claimable, fingerprint and trust together; preserve existing discussion and original classification. Merge failure markers instead of replacing metadata.
- [x] Verify repeat application and recoverable failures without touching the real corpus.

## Task 3: Browser efficiency and partial results

- [x] Add failing tests for early result readiness, bounded empty waits, atomic checkpoints, per-profile exclusive leases and partial recovery after a worker timeout.
- [x] Share runtime/session/wait/checkpoint helpers across the three platform adapters. Replace initial and scrolling fixed waits with result-driven waits; checkpoint each completed batch.
- [x] Use per-invocation output paths in the browser strategy, recover valid checkpoint rows after timeout, and distinguish partial, timeout, busy-profile and failed results.
- [x] Preserve sequential browser limits and HTTP-only fast/normal paths.

## Task 4: Integration and handoff

- [x] Run relevant tests, then the full suite if targeted checks pass.
- [x] Perform one read-only review of this batch and fix supported findings.
- [x] Update collection docs with the actual chain, verified improvements and remaining gaps: global browser pooling, complete platform depth, general object versioning, and the newer collection → Web/MCP investigation bridge.

## Execution notes

- Existing code already runs HTTP concurrently and invokes browser adapters only in deep mode; do not describe this as a new optimization.
- Existing adapters reuse one persistent context for multiple keywords in a worker; this batch adds safe exclusive profile use and adaptive waits, not a long-running browser pool.
- Old reference IDs remain unchanged. General item versioning is a separate design boundary; preserving it avoids silently replacing evidence used by previous answers.


## Verification ledger

- Initial isolated tests reproduced nine material/fulltext failures; browser tests reproduced missing adaptive waits, discarded checkpoints and shared-output/profile issues. Worker tests also reproduced the failure to collect the initial page when zero scrolls were requested.
- Body normalization, both cache merge paths and direct backfill now share preservation rules. The independently reviewed legacy-marker case and A → B → A cache replay case were each observed failing, then passing.
- Owned-worker timeout cleanup was checked with a real local subprocess and its child, without opening a platform or user browser.
- Full suite: 1,392 passed, one documentation-index failure, eight existing HTTPX per-request-cookie deprecation warnings (239.48s). Both omitted README documentation links were corrected, and that check passed on replay. Raw report: `.periscope-scratch/reports/collection-integrity-20261024.xml`.
- Final verification after those fixes and unchanged-cache fingerprint optimization: **147 passed** in20.70s across the affected material, browser, worker, enrichment, source-body, run-store, evidence-tier, architecture, boundary and documentation tests. Report: `.periscope-scratch/reports/collection-integrity-final-20261024.xml`.
- No live source-success, memory or wall-clock speed improvement percentage is claimed. No actual login session or production corpus was used by the new tests.

Ruling: General source-object versioning, platform pagination and the newer collection-to-Web investigation bridge remain separate work; the append-only corpus contract was preserved rather than silently replacing previously cited objects.
Ruling: Browser timeout cleanup also stops owned descendants because terminating just the Python worker can leave a browser/driver alive. Cleanup has a short separate grace period and reports failure explicitly.
Ruling: Unchanged cache replay checks its content digest and coherent stored fields before expensive fingerprint work. This avoids repeated SimHash computation without weakening the first corrective write for older cache records.
