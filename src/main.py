"""CLI entry point for Periscope."""

import argparse
import asyncio
import shlex
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from ._cli import add_data_dir_arguments, add_log_level_argument, force_utf8_output
from .console_icons import get_icons
from .logging_config import configure_logging
from .storage.manager import ConfigError, StorageManager
from .orchestrator import Orchestrator


console = Console(stderr=True)


def print_banner():
    """Print the application banner."""
    banner = r"""
[bold blue]
 ___ _ __ _ __ ___  _ __   ___  ___  ___  ___
/ __| '_ \ '_ ` _ \| '_ \ / _ \/ __|/ _ \/ __|
\__ \ |_) | | | | | | |_) |  __/\__ \ (_) \__ \
|___/ .__/|_| |_| |_| .__/ \___||___/\___/|___/
    |_|             |_|              潜望镜
[/bold blue]
[cyan]  From daily briefing to persistent research[/cyan]
    """
    console.print(banner)


def main():
    """Main CLI entry point."""
    # On a Chinese Windows console the banner arrives mojibaked and every emoji
    # becomes a literal `\U0001f305`; rich escapes what the code page cannot
    # encode instead of failing, so the damage is silent.
    force_utf8_output()
    configure_logging(console)
    print_banner()
    icons = get_icons()

    parser = argparse.ArgumentParser(description="Periscope - AI-Driven Information Aggregation System")
    parser.add_argument("--hours", type=int, help="Force fetch from last N hours")
    add_data_dir_arguments(parser)
    add_log_level_argument(parser)
    args = parser.parse_args()

    configure_logging(console, level=args.log_level)

    try:
        # Load environment variables from .env file
        load_dotenv()

        data_dir = Path(args.data_dir)

        # Initialize storage manager
        storage = StorageManager(data_dir=str(data_dir), config_path=args.config)

        # Load configuration
        try:
            config = storage.load_config()
        except FileNotFoundError:
            console.print(
                f"[bold red]{icons['error']} Configuration file not found![/bold red]\n"
            )
            console.print(f"Expected config: [cyan]{storage.config_path}[/cyan]\n")

            example_path = data_dir / "config.example.json"
            if not example_path.exists():
                example_path = Path("data/config.example.json")
            if example_path.exists():
                target_parent = storage.config_path.parent
                if target_parent != Path("."):
                    console.print(
                        f"Create the destination directory:\n"
                        f"  [cyan]mkdir -p {shlex.quote(str(target_parent))}[/cyan]\n"
                    )
                console.print(
                    f"Copy the example config and edit it:\n"
                    f"  [cyan]cp {shlex.quote(str(example_path))} "
                    f"{shlex.quote(str(storage.config_path))}[/cyan]\n"
                )
            if args.config is None and data_dir == Path("data"):
                console.print(
                    "Or run [bold cyan]uv run periscope-wizard[/bold cyan] to launch the interactive setup wizard.\n"
                )
            sys.exit(1)
        except ConfigError as e:
            console.print(
                f"[bold red]{icons['error']} Error loading configuration: {e}[/bold red]"
            )
            sys.exit(1)
        except Exception as e:
            console.print(
                f"[bold red]{icons['error']} Error loading configuration: {e}[/bold red]"
            )
            sys.exit(1)

        icons = get_icons(config.display.icon_style)

        # Create and run orchestrator
        orchestrator = Orchestrator(config, storage, console=console)
        asyncio.run(orchestrator.run(force_hours=args.hours))

    except KeyboardInterrupt:
        console.print(f"\n[yellow]{icons['warning']} Interrupted by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        # A missing API key is a setup step, not a bug, so it gets one
        # actionable line instead of a traceback. `orchestrator.run()` already
        # printed the message before re-raising - repeating it here showed the
        # same sentence twice, so this branch adds only what its own scope
        # knows: which files to edit, and that the run was not wasted.
        message = str(e)
        if "api_key" in message or "API key" in message:
            # Only what this scope knows: which files to edit, and whether the
            # run's collection work is already safely stored. Paths come from
            # the live storage/config, so a `--data-dir` user is not sent to
            # default locations that were never written.
            hints = []
            if config.corpus.enabled:
                hints.append(
                    f"本轮已采集并入库的条目不会丢（看 {Path(storage.data_dir) / config.corpus.path}）"
                )
            hints.append(
                f"在 {storage.config_path} 里把 ai.api_key_env 指向一个已导出的环境变量"
                "（key 本身写进 .env 或 shell），然后重跑即可继续评级与日报"
            )
            console.print(f"[yellow]{icons['warning']} {'；'.join(hints)}。[/yellow]\n")
            sys.exit(1)
        console.print(f"\n[bold red]{icons['error']} Fatal error: {e}[/bold red]")
        console.print_exception()
        sys.exit(1)


def print_config_template():
    """Print configuration template."""
    template = """
{
  "ai": {
    "provider": "anthropic",
    "model": "claude-sonnet-4.5-20250929",
    "api_key_env": "ANTHROPIC_API_KEY",
    "temperature": 0.3,
    "max_tokens": 4096
  },
  "display": {
    "icon_style": "emoji"
  },
  "sources": {
    "github": [
      {
        "type": "user_events",
        "username": "torvalds",
        "enabled": true,
        "profile": "tech-news"
      }
    ],
    "hackernews": {
      "enabled": true,
      "fetch_top_stories": 30,
      "min_score": 100,
      "profile": "tech-news"
    },
    "rss": [
      {
        "name": "Example Blog",
        "url": "https://example.com/feed.xml",
        "enabled": true,
        "category": "software-engineering",
        "profile": "auto"
      }
    ]
  },
  "collection": {
    "time_window_hours": 24
  },
  "digest": {
    "max_items": null,
    "profile_order": [
      "tech-news",
      "tech-blog",
      "finance-news"
    ],
    "category_groups": {},
    "default_group": "other",
    "default_group_limit": null
  },
  "processing": {
    "profiles_dir": "profiles",
    "default_profile": "tech-news",
    "profile_settings": {
      "tech-news": {
        "threshold": 7.0,
        "topic_dedup": true
      },
      "tech-blog": {
        "threshold": 4.0,
        "topic_dedup": false
      },
      "finance-news": {
        "threshold": 7.0,
        "topic_dedup": true
      }
    }
  }
}

Also create a .env file with:
ANTHROPIC_API_KEY=your_api_key_here
GITHUB_TOKEN=your_github_token_here (optional but recommended)
"""
    console.print(template)


if __name__ == "__main__":
    main()
