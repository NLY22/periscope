# Existing Code Organization Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to execute this plan in this session. The user has instructed us to proceed with organizing the existing code.

**Goal:** Give the active topic-search code one installable home, focused modules and compatible existing entry points.

**Architecture:** Move active implementations to `periscope`, retain old paths as forwarding entry points, and separate topic orchestration, presentation, collection definitions and HTTP parsing. Keep existing evidence and legacy product modules in `src`.

**Tech Stack:** Python >=3.11, existing dependencies, pytest, Hatchling.

**Spec:** `docs/superpowers/specs/2026-10-19-project-structure-refactor-design.md`.

## Global Constraints

- Preserve current workspace changes. Work in place because the active implementation is predominantly untracked; snapshot source files before modifying them.
- Preserve CLI arguments, defaults, outputs, source definitions, catalog order, data locations and explicit save behavior.
- Do not merge, move or rewrite real databases, cookies or browser sessions.
- No retrieval-policy changes, research-feature changes or new product dependencies.
- Use existing tests for moved behavior; add meaningful compatibility, service, installation and persistence checks.
- Do not stage or commit unrelated existing work.

## Review Focus

- Old and new imports must share module state, including monkeypatched paths.
- Direct old scripts must still run; installed operation must not require repository scripts.
- Catalog entries, order, pagination and parse behavior must survive extraction exactly.
- Querying with substitutes must produce replayable raw rows and explicit save must work in an isolated database.
- Installed defaults must use writable user data directories and builds must exclude credentials and runtime data.

## Task 1: Baseline and migration contracts

- [x] Snapshot the source files being reorganized in ignored scratch storage and record their hashes.
- [x] Run the existing full test suite and record its summary.
- [x] Add `tests/architecture/test_package_structure.py` covering shared imports, catalog equivalence, installed defaults and official module entry points.
- [x] Run the new tests and confirm that the new package interfaces are absent.

## Task 2: Runtime, search and collection package

- [x] Create the formal package with runtime paths/console, local/API/merge search and source model/catalog/parsing/HTTP modules.
- [x] Move remaining platform implementations under `periscope.collection.platforms`; maintain old script entry points and preserve source settings/order.
- [x] Replace old runtime modules with aliases of their canonical implementations, including private names required by existing callers.
- [x] Adapt architecture tests to inspect canonical implementation paths rather than only compatibility wrappers.
- [x] Run existing collection, path, trust, merge and retrieval tests plus the new migration contracts.

## Task 3: Topic service, presentation and persistence

- [x] Add `TopicRequest`, `TopicResult` and `TopicService.search(request)` to separate query orchestration from CLI/output side effects.
- [x] Extract text/JSON presentation, enrichment and persistence helpers; make CLI parsing a separate module.
- [x] Retain `topic` import and direct execution compatibility, including current test injection points.
- [x] Migrate ingest/backfill implementations so subprocess execution uses `-m` module entry points and is available after installation.
- [x] Verify substituted collection through shard persistence and an isolated corpus round trip; verify cached-only and explicit-save behavior.

## Task 4: Build and documentation

- [x] Include canonical package and compatibility modules in the wheel and add `periscope-topic` without changing old commands.
- [x] Update Docker source inputs and exclusions; keep its default command.
- [x] Update README, architecture and foundation docs to explain canonical ownership and compatibility.
- [x] Build a wheel and inspect its contents for required files and excluded runtime data.
- [x] Verify installed CLI help and isolated operation outside the checkout.

## Task 5: Final verification and review

- [x] Run the full test suite and existing retrieval evaluation.
- [x] Review migration changes against the source snapshot, especially source definitions, default values, import effects and subprocess invocations.
- [x] Fix regressions, rerun affected tests, then rerun the suite if code changed.
- [x] Record completed work and exact validation; report any unavailable Docker validation without claiming it ran.

## Execution record

- Authorization: user asked to organize the existing code after the scope was explained; proceed with reversible implementation rather than request permission again.
- Workspace: preserve the current checkout, existing untracked implementation and user modifications; source snapshots provide a local recovery/reference point.
- Baseline: 1254 tests passed. Snapshot contains 249 source/configuration files; no databases or credentials were copied.
- Implemented: canonical runtime/search/collection/operations package, categorized catalog, topic request/result/service, render/enrichment/persistence modules and compatible old imports/commands.
- Verified: catalog dataclass SHA256 unchanged (`e34ed2823db9093c8bfecbb4ac2157b355ccc07f6db60828a8a428ee29fc07ae`); API and merge function/class ASTs unchanged.
- Independent review: identified relative installed path drift. Fixed by resolving paths and anchoring overrides in child environments; the regression test was observed failing and then passing.
- Installed verification: wheel built and installed into an isolated target; seven module help entry points, actual `periscope-topic` console command, `--ingest-last`, `--save` and retrieval round trip passed outside the source working directory with relative data/scratch/cookie overrides.
- Necessary compatibility repair: installed verification reproduced the existing `--save` run-dict/list mismatch. Both save paths now share `ingest_run`; the dedicated regression test was observed failing and then passing.
- Existing retrieval evaluation completed for six configurations with results isolated under scratch.
- Docker: source inputs and runtime-data exclusions updated; Docker executable is unavailable, so no image build was claimed.
- Git: changes remain in the user's working tree; no unrelated changes were staged or committed.

- Final verification: 1283 tests passed in 83.53 seconds; 29 checks added beyond the 1254-test baseline.
