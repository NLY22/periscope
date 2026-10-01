"""Shared CLI argument helpers for Horizon entrypoints."""

import argparse
import sys
from pathlib import Path


def force_utf8_output() -> None:
    """Make console output survivable on a Chinese Windows code page.

    The default console encoding there is cp936, which cannot encode `⚠` or any
    of the report's status glyphs, so a script that finishes its work and then
    prints a warning dies with `UnicodeEncodeError` at the last line - the
    result looks like a broken tool, and on a labeling run it costs the printed
    thresholds. Replacing what cannot be encoded beats losing the output.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def display_path(path, root=None) -> str:
    """A path for human-facing output: relative to `root` when it is inside it.

    `Path.relative_to` raises when the path lies elsewhere, so printing it that
    way turned a successful run into a traceback the moment someone pointed an
    argument outside the repository - a scratch directory, or a second drive on
    Windows where the repo lives on D: and the temp folder on C:.
    """
    candidate = Path(path)
    if root is None:
        return str(candidate)
    try:
        return str(candidate.relative_to(Path(root)))
    except ValueError:
        return str(candidate)


def add_log_level_argument(
    parser: argparse.ArgumentParser, default: str = "WARNING"
) -> None:
    parser.add_argument(
        "-l", "--log-level",
        default=default,
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        metavar="LEVEL",
        help=f"Logging level (default: {default})",
    )


def add_data_dir_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-d", "--data-dir",
        default="data",
        metavar="PATH",
        help="Path to the data directory (default: 'data')",
    )
    parser.add_argument(
        "-c", "--config",
        default=None,
        metavar="PATH",
        help="Path to config file (default: <data-dir>/config.json)",
    )
