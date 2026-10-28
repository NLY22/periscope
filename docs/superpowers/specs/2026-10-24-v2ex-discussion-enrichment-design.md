# V2EX Topic and Reply Enrichment

## Intent

Turn V2EX search hits into full topic text plus bounded replies in the same investigation round, while preserving the original topic and reply links in the shared corpus.

## Chosen approach

Use the existing public V1 `topics/show.json` and `replies/show.json` endpoints. The topic ID comes from the existing V2EX topic permalink. This reuses the existing anonymous search workflow and avoids requiring a personal token or launching a browser.

The newer API 2.0 provides topic and replies routes but uses a Personal Access Token. Reusing the older existing scraper would return only a fixed number of replies and truncate reply text to 300 characters. The selected adapter requests the full topic and reply list, then applies a local per-round cap and time budget.

## Constraints

- Save at most 60 replies per topic and obey the remaining platform collection budget.
- Keep completed topic/reply data on timeout and record a partial coverage reason.
- Preserve reply author, native reply ID, timestamp, permalink, and topic ID.
- The V1 reply response is flat and has no reply-to field; do not infer a nested thread.
- Store platform node and topic identity as source metadata.
- No automatic reply-version history or full-coverage claim.

## Acceptance

Tests cover detail plus replies, local comment cap, timeout partial results, TopicService routing, remaining-budget propagation, and shard / SQLite round-trip. A bounded anonymous probe records only lengths and counts.

## Interface evidence

- The V2EX site documents the legacy `/api/topics/show.json` endpoint and states it requires no authentication: <https://www.v2ex.com/t/2241>.
- The current API 2.0 help lists `topics/:topic_id` and `topics/:topic_id/replies` under the newer token-based API: <https://www.v2ex.com/help/api>.
