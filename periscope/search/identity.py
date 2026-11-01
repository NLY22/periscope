"""Conservative URL identity for comparison and exact-resource deduplication."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

COMMUNITY_SOURCE_KEYS = {
    "zhihu.com": "zhihu_search", "weibo.com": "weibo_search", "bilibili.com": "bili_search",
    "xiaohongshu.com": "xhs_browser", "douyin.com": "douyin_browser", "kuaishou.com": "kuaishou_browser",
    "v2ex.com": "v2ex_search", "news.ycombinator.com": "hn_ai", "github.com": "github_issues",
    "stackoverflow.com": "stackexchange_search", "csdn.net": "csdn_search", "juejin.cn": "juejin_search",
    "reddit.com": "reddit_public",
}


def community_source_key(raw: str) -> str:
    try:
        host = (urlsplit(raw).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""
    if host == "github.com":
        return "github_issues" if re.match(r"^/[^/]+/[^/]+/(?:issues|pull|pulls|discussions)(?:/|$)", urlsplit(raw).path) else ""
    return next((source for domain, source in COMMUNITY_SOURCE_KEYS.items()
                 if host == domain or host.endswith("." + domain)), "")


def canonical_url(raw: str) -> str:
    try:
        parts = urlsplit(raw or "")
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_") and key.lower() not in ("spm_id_from", "spm", "gclid", "fbclid")]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/",
                           urlencode(sorted(query)), ""))
    except ValueError:
        return ""
