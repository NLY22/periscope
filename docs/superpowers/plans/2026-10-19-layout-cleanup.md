# File layout cleanup

The user asks to improve file organization before further features. Scope is directory ownership and runtime artifact placement. Proceed within the existing authorization, preserving current code/data and legacy entry points.

Target collection layout: `transport/` owns HTTP fetching and response parsing; `strategies/` owns broad/focused/batch/browser dispatch; `crawling/` owns site traversal, document extraction and its worker; `catalog/` owns source definitions and `platforms/` owns site-specific adapters. Existing flat collection module paths remain thin aliases so imports, state injection and old scripts continue to work. Internal imports and subprocess targets use the new canonical paths.

Root output cleanup: complete the already-defined `COLLECT_DIR` switch, put diagnostic reports in `REPORT_DIR`, make readers/writers agree, and relocate existing known root artifacts using a checked, collision-preserving manifest. Do not move databases, dependency/browser caches, credentials or profiles. Legacy root data remains readable for other checkouts. Historical probe entry points should also default to centralized output locations.

- [x] Inspect ownership, path seams, dirty-workspace state and preserve a source snapshot.
- [x] Add regression checks for module aliases, import side effects, shared output paths, safe artifact relocation and ingestion from the new collection directory.
- [x] Move collection implementations into clear subpackages; keep legacy modules as compatibility aliases.
- [x] Unify collection/report writers and ingest readers; move the known existing root outputs with a manifest.
- [x] Add documentation navigation and update architecture/usage links.
- [x] Run the full suite, build/install checks and an independent regression review; record actual final layout and verification.

## Execution and verification

- Moved nine implementation modules into transport, strategies and crawling; the former flat modules and legacy collectors remain aliases to the same module objects. Runtime and worker imports now use canonical modules.
- Unified collection outputs under COLLECT_DIR and diagnostics under REPORT_DIR, including child-process environment anchoring. Full ingestion also reads legacy root paths and collision-preserved UGC files.
- Relocated 58 known artifacts (124,753,733 bytes); every destination was verified against the recorded size and SHA256, with no original root artifacts remaining. Manifest: `.periscope-scratch/reports/layout-migration-20261019-201959-536178.json`. Databases, credentials and browser profiles were not relocated.
- Grouped source/crawler tests under tests/collection and package/layout tests under tests/architecture. Added navigation for docs, tests, legacy imports and historical probes. All 24 historical probe files passed syntax inspection; they were not run against live services.
- Twenty layout regressions pass. Final complete suite: `python -m pytest -p no:cacheprovider -o addopts='' -q --tb=short` — **1361 passed**, 8 existing httpx cookie deprecation warnings, 110.67 seconds.
- Offline wheel build and isolated reinstall passed. Outside the source checkout, the installed console entry and real local HTTP fixture verified discovery → HTML/PDF worker → explicit save subprocess → local provenance → changed-document history, plus legacy module identity and relative collection/report paths across child processes. No paid API or live platform calls were needed.
- Independent layout review identified report-path mismatches and collision-preserved UGC discovery. Four behavioral regressions reproduced those failures before the fixes; the reviewer checked the final changes and reported no remaining blockers in scope.
- Local verification evidence: `.periscope-scratch/layout-verification-20261019.json` and `.periscope-scratch/layout-installed-result.json`; pre-change source snapshot: `.periscope-scratch/layout-20261019/`.
