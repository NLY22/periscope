"""Run-local comparison of direct linked results with successful API results."""
from periscope.search.identity import canonical_url as _identity


def compare_sources(api_rows: list[dict], direct_rows: list[dict], *, api_succeeded: bool) -> dict:
    api_urls = {_identity(row.get("url") or "") for row in api_rows} - {""}
    direct_urls = {_identity(row.get("url") or "") for row in direct_rows} - {""}
    return {"scope": "this_run", "comparison_available": api_succeeded,
            "api_linked_urls": len(api_urls), "direct_linked_urls": len(direct_urls),
            "direct_urls_not_in_api": len(direct_urls - api_urls) if api_succeeded else None,
            "direct_with_body": sum(len(row.get("body") or "") >= 400 for row in direct_rows),
            "direct_with_metrics": sum(bool(row.get("metrics")) for row in direct_rows)}
