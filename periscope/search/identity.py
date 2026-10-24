"""Conservative URL identity for comparison and exact-resource deduplication."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


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
