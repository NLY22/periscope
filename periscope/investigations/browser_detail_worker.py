"""Run one blocking browser detail adapter in an owned subprocess."""
import json
import sys
import time


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        source = payload["source"]
        if source == "xhs_browser":
            from periscope.collection.platforms import xhs as platform
        elif source == "douyin_browser":
            from periscope.collection.platforms import douyin as platform
        else:
            return 2
        url = str(payload["url"])
        limit = max(1, min(60, int(payload["max_comments"])))
        budget = max(0.0, min(180.0, float(payload["budget"]), float(payload["deadline"]) - time.monotonic()))
        if budget:
            detail, comments, coverage = platform._fetch_many_sync([{"url": url}], max_comments=limit,
                                                                    budget_seconds=budget)[url]
        else:
            detail, comments, coverage = {}, [], {"status": "partial", "truncated_by": ["time_budget"], "errors": []}
        print(json.dumps({"detail": detail, "comments": comments, "coverage": coverage}, ensure_ascii=True))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
