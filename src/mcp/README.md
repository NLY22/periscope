# Horizon MCP

The MCP server exposes the fetch → score → digest pipeline as staged tools, the evidence corpus and claim layer as read tools, and the research session as **per-round verbs**. 27 tools in total.

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
| `hz_corpus_import` | Ingest a user export (`{"items": [...]}`) with declared tiers; `dry_run` previews without storing |
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

### Importing a user export

`hz_corpus_import` is the sanctioned path for sources Periscope must not scrape — the design excludes captcha solving, request signing and account pools, so a gated platform arrives as text the user's own account can already see. It reaches no network.

```json
{
  "items": [
    {
      "source_type": "rss",
      "title": "某笔记：定价对比",
      "locator": "xhs:note:abc123",
      "author": "作者甲",
      "published_at": "2026-09-20T00:00:00+00:00",
      "metadata": {"source_label": "xiaohongshu"},
      "sections": [
        {"tier": "primary", "text": "官方定价是每百万 token 2 元。"},
        {"tier": "community", "author": "路人乙", "text": "我觉得明明是 5 元。"}
      ]
    }
  ]
}
```

Rules that matter:

- **Tiers are declared, never inferred.** `community` text from an export stays a lead: it is searchable in the full-text index but cannot enter `claimable`, claim extraction, or the independent-source count. An item with no `sections` at all is stored as one `legacy_marker` section, which the trust model already discounts.
- **Provenance defaults to `manual_export`** (factor 0.85) because Periscope did not fetch the page and cannot confirm it said this. A payload may declare `transcript` / `ocr` / `vlm` / `author` per section instead.
- **`source_type` must be one of the 14 registered families**, because it selects the credibility prior and the source family used by independence counting. Put the human name in `metadata.source_label`. An import may not invent its own prior.
- **`locator` (or `url`) is the identity**; without one the item is rejected, since re-importing the same export would otherwise create a second copy. Ids are derived from the locator, so importing the same file twice reports `items_new: 0`.
- Each item is validated independently: one bad item is returned in `rejected` with every problem it has, the rest still land.

The same file ships as [`data/export.example.json`](../../data/export.example.json), and the test suite parses it, so the example cannot drift from the implementation.

Same payload on the CLI (`uv run python scripts/import_corpus.py --file export.json --data-dir data`, plus `--dry-run` to validate without opening a database) and on the panel, which has a section for it (`POST /api/import`, answering 400 only when nothing at all could be imported). The panel's 只校验 button sends the same body with `"dry_run": true`: it runs the identical validation loop and writes nothing, so what the preview counts is what the import would accept.

**All three entries are one feature, checked as such** (`tests/test_mcp_parity.py`): each can preview (`dry_run` / `--dry-run` / 只校验), each delegates validation to `src/corpus/ingest.py` rather than re-implementing it, and `tiering` is exposed by the MCP tool and the CLI — the two places that reproduce ablation arms — and deliberately *not* by the panel, where offering `marker` would invite importing evidence under the weaker layering rule.

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
