# GitHub Issue and Pull Request Discussion Enrichment

## Intent

Add the discussion text missing from the existing GitHub Issues / PR search source so an investigation can inspect issue comments and pull request review threads, then keep those materials in the existing run-shard and community corpus path.

## Chosen approach

Use GitHub's REST API from the existing optional `--enrich` stage. Search rows retain their native issue number, repository identity, and pull request URL. The adapter reads issue comments and, for pull requests, inline review comments; it preserves review reply parent IDs, code path, and line. This uses the project's current HTTP stack and optional `GITHUB_TOKEN` configuration without requiring a browser session.

The alternative was to browse rendered GitHub pages, which adds browser startup and page parsing for data already exposed by the API. A second alternative was to collect only issue comments; that omits the review threads that distinguish pull requests. The REST endpoints with bounded pagination are the selected path.

## Constraints

- Up to 60 normalized discussion entries per issue / pull request.
- Up to two pages per endpoint, 30 entries per page, subject to the caller's time budget.
- A PR can include issue conversation comments and inline review comments; inline replies preserve `in_reply_to_id` as `parent_id`.
- Keep partial results and report endpoint errors, page limits, comment limits, or time limits.
- Store discussion text as community evidence. Keep the original permalink and review location.
- Optional `GITHUB_TOKEN` may raise API limits. Anonymous public access remains supported where the endpoint allows it.
- No claim of complete coverage or comment-version history.

## Acceptance

Tests cover native search identity, issue comments, nested review replies, review file locations, timeouts with partial results, and SQLite persistence/retrieval. The platform matrix distinguishes isolated simulated evidence from live verification.
