"""Isolated public-web search worker; input and output are bounded JSON."""
from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        request = json.loads(sys.stdin.readline())
        query = str(request.get("query") or "").strip()[:200]
        if not query:
            raise ValueError("empty_query")
        from ddgs import DDGS
        results = DDGS().text(query, max_results=20)
        rows = [{"title": str(item.get("title") or "")[:300],
                 "href": str(item.get("href") or "")[:2000],
                 "body": str(item.get("body") or "")[:2000]}
                for item in results or [] if isinstance(item, dict) and item.get("href")][:20]
        print(json.dumps({"results": rows}, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
