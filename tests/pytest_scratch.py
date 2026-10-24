"""Point pytest's temp base at a directory this machine can actually write to.

Loaded early via `-p tests.pytest_scratch` in `pyproject.toml`, because
`pytest_configure` here runs *before* the tmpdir plugin resolves its base
directory - which `conftest.py` does not, so a fix placed there silently has no
effect on `tmp_path` and looks like it works for the wrong reason.

Why this is needed at all is recorded in `src/_scratch.py`: some confined
harnesses hand a process a system temp directory that is created and then
refused for every read, write and `mkdir`. There, `tmp_path` errors at setup and
the suite reads as a broken project when nothing in the project is broken.

Known limit, stated so nobody trusts this further than it goes: on Windows in
such a harness this still does not rescue `tmp_path`, because pytest provisions
each test directory with `mkdir(mode=0o700)` and strips group/other bits from an
existing base (`_pytest/tmpdir.py`). Use the `scratch_path` fixture in
`tests/conftest.py` for new tests that need scratch space. This plugin is what
keeps the *base* out of a forbidden tree, so that the failure, when it happens,
is the precise permissions one rather than a confusing "cannot list temp".
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config) -> None:
    """Set `--basetemp` unless the caller already named one."""
    if config.option.basetemp:
        return
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from src._scratch import scratch_root

        base = scratch_root() / "pytest-tmp"
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        # No usable scratch root: leave pytest's default in place so its own
        # error message is the one the user sees.
        return
    config.option.basetemp = str(base)
