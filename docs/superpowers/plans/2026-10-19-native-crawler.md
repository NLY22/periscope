# Native site crawling

The user explicitly wants to compete with general web-search APIs through broader retrieval using crawlers and native platform techniques. Extend the existing collection-to-topic-to-corpus flow with a bounded site crawler. Previous requests authorize implementing source expansion; proceed without another approval loop.

This increment follows supplied seed URLs through site listings, article links, sitemaps and PDF attachments. It extracts searchable content, records discovery paths and content hashes, and joins the existing topic output/save flow without requiring the DeepSeek API. Robots policies, host boundaries, pacing, page/byte/depth limits and total deadlines are part of the fetcher, not UI approval steps. Search ranking and legacy commands stay compatible.

- [x] Inspect existing collection, body extraction, URL policy, topic and persistence seams; snapshot sources/configuration.
- [x] Write failing tests for discovery, HTML/PDF extraction, page provenance, budgets, failure isolation and retrieval/persistence integration.
- [x] Implement focused document extraction and bounded same-site crawl modules, retaining completed results on timeout.
- [x] Connect seed crawling to topic requests, CLI, source comparison, raw run storage and explicit corpus ingestion.
- [x] Preserve extracted document metadata and changed content versions in the existing append-only corpus.
- [x] Verify selective public crawling, installation outside source cwd and full regression tests; obtain independent review.
- [x] Document working commands, actual results and limits (OCR, spreadsheet semantics, interactive platforms, coverage proof).

Acceptance: A seeded listing discovers a relevant article and an attached PDF, extracts independently searchable text with parent URLs and PDF page offsets, saves both, and retrieves them from an isolated corpus. An unrelated page, denied URL, redirect outside scope, oversized body or slow request must be reported without wiping successful records. No search API is required. Public tests are bounded and do not modify live site or login state.

Dependencies: reuse HTTPX, BeautifulSoup, Trafilatura and the existing public-URL validator; add pypdf for text-bearing PDFs. Scanned pages are marked as needing OCR rather than invented as readable text. All verification data stays under scratch.

## Execution evidence

- Initial 14 acceptance tests failed before implementation; all now pass. Subsequent regression cases for redirect robots, failure summaries, incremental versions, worker cancellation, host-balanced auto seeds and robots sitemap discovery were also observed failing before their implementations. Total new tests: 22.
- `--crawl` selects up to six host-balanced native/API result URLs, preferring direct sources; `--crawl-seeds` provides explicit entries. API-off works in both paths.
- Implemented streamed byte limits and URL policy for all redirect hops; policy and per-host pacing apply to final paths. XML maps are discovery-only. HTML/PDF parsing uses cancellable subprocesses.
- Kept existing append-only corpus behavior; crawler IDs include content hash, and both incremental/full ingestion deduplication preserve distinct versions. Document metadata survives run storage, ingestion and ID-based local hydration.
- Public probe: arXiv abstract HTML produced 2,255 characters. A two-seed run retained that page when its 20-second budget elapsed before PDF completion. Separate PDF probe succeeded in 19.03 seconds, extracting 39,613 characters from `https://arxiv.org/pdf/1706.03762` with page locators. No paid API request was made.
- Independent reviewer re-ran 22 crawler tests, checked the final auto-seed/map changes, and reported no blocking issues. Added the noted sitemapindex depth requirement to docs.
- Full regression: **1341 passed in 96.40 seconds**, with 8 pre-existing HTTPX per-request-cookie deprecation warnings. Existing six-configuration fixture retrieval evaluation remains unchanged.
- Built and installed wheel outside source cwd. Verified actual console help and real local HTTP listing -> HTML/PDF workers -> CLI save subprocess -> corpus retrieval with parent/page attribution -> changed-version persistence. Relative data/scratch/cookie overrides resolve correctly.
- Dependency lock preserves the original mirror and existing package versions; only 11 lines of pypdf metadata added.
- Limits explicitly documented: no OCR/Excel semantics/general browser login, no complete raw-file archival, no guarantee of saving an interrupted Ctrl+C run, and no claim that a fetched public page is unreachable by competitors.
