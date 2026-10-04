"""Serve the Periscope web panel (entry point: periscope-web)."""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from .._cli import add_data_dir_arguments, add_log_level_argument
from ..logging_config import configure_logging
from ..main import console
from ..orchestrator import Orchestrator
from ..storage.manager import StorageManager


def build_orchestrator(data_dir: str, config: str | None = None) -> Orchestrator:
    """Same wiring the daily CLI does — one config, one storage, one orch."""
    load_dotenv()
    storage = StorageManager(data_dir=data_dir, config_path=config)
    app_config = storage.load_config()
    return Orchestrator(app_config, storage)


def main() -> None:
    parser = argparse.ArgumentParser(description="Periscope web panel")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8790)
    add_data_dir_arguments(parser)
    add_log_level_argument(parser, default="INFO")
    args = parser.parse_args()

    configure_logging(console, level=args.log_level)
    orchestrator = build_orchestrator(args.data_dir, args.config)

    import uvicorn

    from .app import create_app

    console.print(f"[cyan]Periscope panel[/cyan] → http://{args.host}:{args.port}")
    uvicorn.run(
        create_app(orchestrator),
        host=args.host,
        port=args.port,
        log_level=args.log_level.lower(),
    )


if __name__ == "__main__":
    main()
