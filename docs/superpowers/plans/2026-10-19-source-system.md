# Information Source System Refactor

**Goal:** Make broad source coverage the product's organizing principle and expose what direct sources add beyond the search API in each run.

**User direction:** Organize and refactor the project, expand its information sources sufficiently, and address search API limitations. Proceed in the current workspace; preserve existing work and real data.

**Design:** A shared registry describes source retrieval modes, capabilities and limits. A bounded collector searches native endpoints and filters relevant feed entries. Topic orchestration remains separate; it records per-source outcomes and run-local API comparison. Existing focused collection and source imports remain compatible.

## Constraints

- Preserve the original 179 catalog definitions; register additional sources alongside them.
- Default CLI coverage becomes broad; `--coverage focused` restores the previous platform route. Existing low-level calls retain their defaults.
- Sources are categorized as search, feed, page, browser or snapshot. Fixed feeds are not advertised as historical search; browser/snapshot limitations are explicit.
- Add direct GitHub issue search, Stack Exchange search, Hugging Face model/dataset search and Crossref metadata retrieval using their public endpoint contracts.
- Respect overall deadlines, concurrency, per-source limits and source pacing; no login bypass or automatic account operations.
- Preserve source kinds and provenance through collection, output, run storage and ingestion. Body origin must remain identifiable when merged results choose another row's text.
- Compare direct linked URLs against API URLs only when that API request succeeded; report differences for this run, never claim universal exclusivity.
- Use isolated data for validation. Do not migrate the existing databases or browser sessions.

## Tasks

- [x] Snapshot active source code/configuration and verify the 1283-test baseline.
- [x] Add tests for registry classification, coverage selection, configuration, bounded collection, relevance/URL filtering and truthful per-source outcomes.
- [x] Implement registry, extension sources and the broad collector; fix query encoding and YouTube video identity extraction with regression tests.
- [x] Integrate CLI source listing/selection, broad mode, source reports and API comparison into topic requests/results and replayable runs.
- [x] Preserve source provenance through merging and ingestion; test body attribution and no-data/API-failure cases.
- [x] Verify public endpoints selectively, run the full suite and retrieval evaluation, inspect a rebuilt wheel, and obtain independent regression review.
- [x] Update the product description around source breadth, document limitations and report exact validation.

## Evidence and decisions

- Prior product code already separates runtime, search, topics, collection and operations. This round extends those boundaries rather than introducing another product namespace.
- Broadness means connected and observable retrieval paths. Catalog counts and successful HTTP status alone do not demonstrate useful coverage.
- Source contracts checked against GitHub REST search, Hugging Face Hub API, Stack Exchange advanced search and Crossref REST documentation. Existing historical site probes are treated as observations about those queries, not permanent coverage guarantees.

## Execution evidence

- Snapshot: 159 active source/config files under `.periscope-scratch/source-system-20261019`; baseline 1283 tests passed.
- Inventory: 184 configurations; default HTTP plan selects 25 native/search endpoints and 84 feeds. Remaining entries retain explicit browser/page/snapshot limitations or duplicate-recipe reasons.
- Added five native searches. Six-source public `DeepSeek` check (including HN comments) returned 250 raw records; a separate nine-source check returned 445 records from seven sources, with two correctly recorded filtered-empty feeds. No paid search API was invoked or superiority claim inferred.
- Source details remain attached to raw runs, output, ingestion and local hydration. Local hydration uses record IDs to avoid sharing metadata across sources on the same URL.
- Independent review: repaired local row compatibility, source-kind validation, missing body attribution, cancellation discarding completed pages and exact-resource clustering being polluted by API title clusters. New regression cases were observed failing and then passing. Reviewer rechecked the final mixed-API case and reported no remaining blockers within scope.
- Timing: source collection reports completed pages on progress; partial results survive source/overall timeouts. Request pacing applies before requests and across source boundaries sharing a host.
- Installation: offline wheel rebuilt and inspected; isolated install outside source cwd passed seven module help entries, console command, relative-path `--save`/`--ingest-last`, and custom RSS over real local HTTP through CLI, save subprocess and local provenance retrieval.
- Evaluation: existing six-configuration fixture retrieval evaluation completed. Stub-based semantic/expansion numbers remain fixture checks, not measured real-world search quality.
- Artifacts: inventory, live results, wheel and verification JSON are under `.periscope-scratch/source-system-*`. Existing databases and browser sessions were not migrated.
- Final verification: **1319 tests passed in 79.43 seconds**, 36 tests added beyond the 1283-test baseline. Eight HTTPX per-request-cookie deprecation warnings remain; no failing checks.
