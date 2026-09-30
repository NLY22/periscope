# Horizon MCP

The MCP server exposes the fetch → score → digest pipeline as staged tools, the evidence corpus and claim layer as read tools, and the research session as **per-round verbs**. 26 tools in total.

The MCP layer does not reimplement business logic. It reuses the existing fetch, score, filter, enrich, and summarize modules, and the same `corpus.db` that the CLI and the web panel share.

## Tools

| Tool | Description |
| --- | --- |
| `hz_validate_config` | Validate Horizon config and required environment variables |
| `hz_fetch_items` | Fetch and deduplicate content into the `raw` stage |
| `hz_score_items` | Score items from a stage into `scored` |
| `hz_filter_items` | Filter scored items into `filtered` |
| `hz_enrich_items` | Enrich filtered items into `enriched` |
| `hz_generate_summary` | Generate markdown from a stage |
| `hz_run_pipeline` | Run fetch -> score -> filter -> enrich -> summarize |
| `hz_list_runs` | List recent run artifacts |
| `hz_get_run_meta` | Read metadata for a run |
| `hz_get_run_stage` | Read items from a run stage |
| `hz_get_run_summary` | Read a generated summary |
| `hz_get_metrics` | Read in-memory server metrics |
| `hz_corpus_stats` | Evidence-corpus overview (items, clusters, claims, sessions) |
| `hz_corpus_search` | Full-text search over everything ever collected (CJK-aware) |
| `hz_corpus_recent` | Most recent corpus items, optionally one source |
| `hz_list_claims` | Claims by pipeline status with verdicts, trust, `ungraded_reason` and independent-source counts |
| `hz_get_claim` | One claim with its linked evidence rows and recorded contradictions |
| `hz_research_start` | Open a long-session research task → cited report |
| `hz_research_followup` | Macro: advance the session until it finalises or has to ask |
| `hz_research_step` | One round: decide a verb, recompute only the named branches, return the delta |
| `hz_research_draft` | Read the versioned draft (revision, locked/stale sections, per-section evidence) |
| `hz_research_edit` | Edit one section — creates a `user_edit` revision and locks that section |
| `hz_research_answer` | Answer or skip a request the system raised, resuming a parked session |
| `hz_research_status` | Session state: sub-question tree, turns, widening actions, current report |
| `hz_research_list` | Recent research sessions, including `awaiting_user` ones |
| `hz_send_webhook` | Deliver a summary to the configured channel |

### The research verbs, in one round

`hz_research_step` returns a `TurnResult`: `revision`, `move` (`askuser` / `rescope` / `deepen` / `finalize`), `changed_sections`, `new_evidence`, `verdict_changes` and `pending_request`. When the move is `askuser` the session lands in `awaiting_user` and a `research_requests` row opens; `hz_research_answer` closes it (answered or skipped) and continues the same session. A section the user edited or locked is never overwritten by a later recompute — it is marked `stale` and kept. See [docs/retrieval.md](../../docs/retrieval.md) for the layering and trust rules behind those numbers.

## Resources

- `horizon://server/info`
- `horizon://metrics`
- `horizon://runs`
- `horizon://runs/{run_id}/meta`
- `horizon://runs/{run_id}/items/{stage}`
- `horizon://runs/{run_id}/summary/{language}`
- `horizon://config/effective`

## Install and Start

```bash
uv sync
uv run periscope-mcp
```

| Option | Default | Description |
|--------|---------|-------------|
| `-l`, `--log-level LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR/CRITICAL) |

The server runs over stdio and is intended to be launched by an MCP client. Stdout is reserved for the MCP protocol; progress, logs, warnings, and errors are written to stderr through one shared Rich console.

## Run Artifacts

Each run writes artifacts under `data/mcp-runs/<run_id>/`:

- `meta.json`
- `raw_items.json`
- `scored_items.json`
- `filtered_items.json`
- `enriched_items.json`
- `summary-<lang>.md`

## Design Principles

1. Keep Horizon as the single source of business logic.
2. Preserve staged re-entry so a run can continue from intermediate artifacts.
3. Default to no extra side effects unless explicitly requested.
4. Periscope evidence tools (`hz_corpus_*`, `hz_list_claims`, `hz_get_claim`, `hz_research_*`) act on the shared `corpus.db`, not the per-run artifacts — sessions and evidence survive server restarts and are visible to the CLI and web panel alike.

## Client Setup

See [integration.md](integration.md).
