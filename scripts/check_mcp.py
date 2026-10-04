#!/usr/bin/env python3
"""Local smoke check for Periscope MCP integration."""

from __future__ import annotations

import asyncio
import json

from src.mcp.pipeline_adapter import resolve_periscope_path
from src.mcp.server import ps_get_metrics
from src.mcp.service import PipelineService


async def _main() -> None:
    from src._cli import force_utf8_output

    force_utf8_output()
    periscope_path = resolve_periscope_path()
    service = PipelineService()
    validation = await service.validate_config(
        periscope_path=str(periscope_path),
        check_env=False,
    )
    metrics = ps_get_metrics()

    payload = {
        "ok": True,
        "periscope_path": str(periscope_path),
        "config_path": validation["config_path"],
        "enabled_sources": validation["enabled_sources"],
        "languages": validation["ai"]["languages"],
        "metrics_ok": metrics["ok"],
        "metrics_tool": metrics["tool"],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
