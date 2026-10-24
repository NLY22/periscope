"""B's single data-dir switch.

The point of `PERISCOPE_DATA_DIR` is that no script hardcodes the corpus path.
These tests run a fresh interpreter because `core.paths` resolves the env at
import time (which is exactly how the CLIs start).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _corpus_from(env: dict) -> Path:
    out = subprocess.check_output(
        [sys.executable, "-c", "from core import paths; print(paths.CORPUS)"],
        cwd=str(ROOT), env=env, text=True, timeout=60,
    )
    return Path(out.strip())


def test_data_dir_env_override(scratch_path):
    env = {**os.environ, "PERISCOPE_DATA_DIR": str(scratch_path)}
    assert _corpus_from(env) == scratch_path / "corpus.db"
    assert (scratch_path / "corpus.db").parent.is_dir()


def test_default_data_dir_is_the_repo_scratch():
    env = {k: v for k, v in os.environ.items()
           if k not in ("PERISCOPE_DATA_DIR", "PERISCOPE_SCRATCH_DIR")}
    assert _corpus_from(env) == ROOT / ".periscope-scratch" / "corpus.db"
