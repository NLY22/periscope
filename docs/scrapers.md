---
layout: default
title: Source Scrapers
---

# Source Scrapers

Horizon fetches content from multiple source types. All scrapers inherit from `BaseScraper`, share an async HTTP client, and implement a `fetch(since)` method that returns a list of `ContentItem` objects. Sources are fetched concurrently via `asyncio.gather`.

## 本 fork 加的抓取基础设施（P0）

三处与上游不同，读下面的逐源清单前先知道：

1. **源列表由注册表驱动，不再是一串 `if`。** `SOURCE_SPECS`（`src/models.py`，纯元数据：key / label / `credibility_prior` / `login_required` / `config_field` / `item_fields`）派生出 `SOURCE_REGISTRY`；`src/sources/registry.py` 持有 `key → 工厂函数`。用工厂而不是类，是因为 RSS 需要第三个参数 `ExtractorRegistry`，而 Twitter 按 `mode` 在两个类之间二选一（Playwright 版不接受 client）。加一个源的同步点从 5 处降到 2 处，由 `tests/test_source_registry.py`（28 条）钉住注册表 ↔ enum ↔ `SourcesConfig` ↔ bindings 的一致性，并在 `MockTransport` 下断言抓取循环真的到达每一个已启用源。
2. **限速与鉴权是可注入的共享件。** `src/scrapers/throttle.py` 做 per-host 令牌桶 + 抖动 + `429` 重试一次（clock / sleeper / rng 全部可注入，测试不打挂钟）；`src/scrapers/auth.py` 提供 env-token 与 cookie-file 两种 provider，带过期检测——哪条 cookie 坏了会点名，而不是下游显示 "found 0 items"。`BaseScraper` 的这两个接缝是可选注入，默认值等于现行为，所以 14 个 scraper 一行没改也能跑。顺带修掉一个真实缺陷：`Retry-After` 按 RFC 9110 可以是 HTTP-date，原先 `int(headers["Retry-After"])` 会抛 `ValueError`，把限速升级成抓取失败。
3. **人群文本是声明出来的，不是靠标记反解的。** 各源把评论/回复/楼层放进 `ContentItem.sections`（`tier="community"`，带 `author` 与 `locator`），`content` 由 sections 拼回。`src/scrapers/` 里**不允许再出现任何分层标记字面量**，由 `tests/test_tier_guard.py` 守护。详见 [docs/retrieval.md](retrieval.md)。

因此下面每个源的 "Extracted data" 里，凡是提到评论区的地方，都指 community 层：它进全文检索与日报，不进声明蒸馏与独立信源计数。

## Hacker News

**File**: `src/scrapers/hackernews.py`

Uses the [Firebase HN API](https://hacker-news.firebaseio.com/v0):

- `GET /topstories.json` — fetches top story IDs
- `GET /item/{id}.json` — fetches story/comment details

Stories and their comments are fetched concurrently. For each story, the top 5 comments are included (deleted/dead comments excluded, HTML stripped, truncated at 500 chars).

**Config** (`sources.hackernews`):

```json
{
  "enabled": true,
  "fetch_top_stories": 30,
  "min_score": 100,
  "category": "tech"
}
```

- `fetch_top_stories` — number of top story IDs to fetch
- `min_score` — minimum HN points to include a story
- `category` — optional tag for balanced digest grouping

**Extracted data**: title, URL (falls back to HN discussion URL), author, score, comment count, top comment text, and category.

## GitHub

**File**: `src/scrapers/github.py`

Uses the [GitHub REST API](https://api.github.com):

- `GET /users/{username}/events/public` — user activity events
- `GET /repos/{owner}/{repo}/releases` — repository releases

Two source types are supported:

- **`user_events`** — tracks push, create, release, public, and watch events for a user
- **`repo_releases`** — tracks new releases for a specific repository

**Config** (`sources.github`, list of entries):

```json
{
  "type": "user_events",
  "username": "torvalds",
  "enabled": true,
  "category": "oss"
}
```

```json
{
  "type": "repo_releases",
  "owner": "golang",
  "repo": "go",
  "enabled": true,
  "category": "oss"
}
```

- `category` — optional tag for balanced digest grouping; set per source entry

**Authentication**: Set `GITHUB_TOKEN` in your environment for higher rate limits (5000 req/hr vs 60 without).

## RSS

**File**: `src/scrapers/rss.py`

Fetches any Atom/RSS feed using the `feedparser` library. Tries multiple date fields (`published`, `updated`, `created`) with fallback parsing.

**Config** (`sources.rss`, list of entries):

```json
{
  "name": "Simon Willison",
  "url": "https://simonwillison.net/atom/everything/",
  "enabled": true,
  "category": "ai-tools",
  "content_extractor": "trafilatura"
}
```

- `category` — optional tag for grouping (e.g., `"programming"`, `"microblog"`)
- `content_extractor` — optional name of an extractor defined in `extractors` config; when set, the full article text replaces the feed-provided excerpt (see [Extractors](extractors.md))

**Extracted data**: title, URL, author, content (from `summary`/`description`/`content` fields, or full article text if an extractor is configured), feed name, category, and entry tags.

## Reddit

**File**: `src/scrapers/reddit.py`

Uses public, no-key Reddit endpoints. Subreddit listings and comments prefer `old.reddit.com` HTML because Reddit's unauthenticated JSON and RSS endpoints can intermittently block or fail:

- `GET https://old.reddit.com/r/{subreddit}/{sort}/` — subreddit posts
- `GET https://old.reddit.com/r/{subreddit}/comments/{post_id}/` — post comments
- `GET /r/{subreddit}/{sort}.json` — subreddit posts fallback
- `GET /user/{username}/submitted.json` — user submissions
- `GET /r/{subreddit}/comments/{post_id}.json` — post comments fallback
- `GET /r/{subreddit}/{sort}/.rss` — subreddit posts fallback when JSON is blocked

Subreddits and users are fetched concurrently. Comments are sorted by score, limited to the configured count, and exclude moderator-distinguished comments. Self-text is truncated at 1500 chars, comments at 500 chars.

**Config** (`sources.reddit`):

```json
{
  "enabled": true,
  "fetch_comments": 5,
  "subreddits": [
    {
      "subreddit": "MachineLearning",
      "sort": "hot",
      "fetch_limit": 25,
      "min_score": 10,
      "category": "ai-ml"
    }
  ],
  "users": [
    {
      "username": "spez",
      "sort": "new",
      "fetch_limit": 10,
      "category": "social"
    }
  ]
}
```

- `sort` — `hot`, `new`, `top`, or `rising` (subreddits); `hot` or `new` (users)
- `time_filter` — for `top`/`rising` sorts: `hour`, `day`, `week`, `month`, `year`, `all`
- `min_score` — minimum post score (subreddits only)
- `category` — optional tag for balanced digest grouping; set per subreddit or per user entry

**Rate limiting**: Detects HTTP 429 responses on JSON requests, reads the `Retry-After` header, waits, and retries once. Uses browser-like request headers for no-key public access.

**Extracted data**: title, URL, author, score, upvote ratio, comment count, subreddit, flair, self-text, top comments, and category.

## OpenBB

**File**: `src/scrapers/openbb.py`

Uses the [OpenBB Platform](https://www.openbb.co/platform) Python SDK via `obb.news.company()` to fetch company news for one or more ticker watchlists.

The scraper imports `openbb` lazily. If the optional dependency is not installed, Horizon logs a warning and skips the source instead of failing the whole run.

**Config** (`sources.openbb`):

```json
{
  "enabled": true,
  "watchlists": [
    {
      "name": "megacaps",
      "symbols": ["AAPL", "MSFT", "NVDA"],
      "enabled": true,
      "provider": "yfinance",
      "fetch_limit": 20,
      "category": "equities"
    }
  ]
}
```

- `watchlists` — each enabled watchlist triggers one `news.company()` call per run
- `provider` — OpenBB provider name for that watchlist
- `symbols` — tickers fetched together for the same provider
- `fetch_limit` — maximum rows requested from the provider
- `category` — optional metadata tag stored on each item

Behavior:

- Wraps the synchronous OpenBB SDK in `asyncio.to_thread` so the event loop stays responsive
- Deduplicates duplicate news across watchlists by article URL
- Skips malformed rows, rows without URL/title/date, and items older than the current time window
- Keeps fetching other watchlists if one provider call fails

**Credentials**: provider-specific secrets are resolved by the OpenBB SDK from its own environment variables or settings file. Horizon does not pass those values directly.

**Extracted data**: title, URL, author, published time, article body/excerpt, watchlist name, provider, category, and symbol list.

## Twitter

**File**: `src/scrapers/twitter.py`

Uses the [Apify](https://apify.com) platform to bypass Twitter's anti-scraping measures. The actor `altimis~scweet` is called via the Apify REST API.

Flow:
1. POST to `/v2/acts/{actor_id}/runs` to trigger a run
2. Poll `/v2/actor-runs/{run_id}` until status is `SUCCEEDED` or a terminal failure
3. GET `/v2/datasets/{dataset_id}/items` to retrieve results

Profile timelines use `source_mode: "profiles"`. Keyword discovery and reply expansion reuse `source_mode: "search"` with `search_query`.

When users are configured, a single profile run fetches their timelines together. Each non-empty keyword query starts a separate search run, independently of the configured users. Either path can be used on its own. Results are filtered to the current time window and merged with deduplication by tweet ID across all runs.

**Config** (`sources.twitter`):

```json
{
  "enabled": true,
  "users": ["karpathy", "ylecun"],
  "keywords": ["LLM", "open source"],
  "fetch_limit": 10,
  "fetch_reply_text": false,
  "max_replies_per_tweet": 3,
  "max_tweets_to_expand": 10,
  "reply_min_likes": 5,
  "actor_id": "altimis~scweet",
  "apify_token_env": "APIFY_TOKEN"
}
```

- `users` — Twitter screen names to monitor, without the `@` prefix
- `keywords` — independent Apify search queries (`source_mode: "search"`), not filters on the configured users' timelines. Not supported in Playwright mode.
- `fetch_limit` — in Apify mode, each profile or keyword-search actor run requests up to `max(100, fetch_limit)` tweets. This is not a combined limit: the example above starts three discovery runs with a limit of 100 tweets each, before time filtering and deduplication. Each keyword adds an actor run and associated Apify usage.
- `category` — optional tag for balanced digest grouping (applies to all tweets from this source)
- `fetch_reply_text` — when `true`, a second Apify run fetches reply bodies for each important tweet and appends them as `community` sections (declared tiers, not a text marker), so replies stay out of the claimable evidence layer
- `max_replies_per_tweet` — maximum reply lines per tweet (sorted by engagement score)
- `max_tweets_to_expand` — cap on reply expansion runs per pipeline cycle, to control Apify credit usage
- `reply_min_likes` — minimum likes required for a reply to be included
- `actor_id` — Apify actor ID (default: `altimis~scweet`)
- `apify_token_env` — environment variable name containing the Apify API token

**Authentication**: Set `APIFY_TOKEN` in your `.env`. Get a token at [console.apify.com](https://console.apify.com/account/integrations).

**Extracted data**: tweet text, URL, author, publish time, likes, retweets, replies, views, category, and (optionally) reply-thread text appended as `community` sections.
