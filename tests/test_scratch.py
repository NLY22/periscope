"""Scratch directories must work where the system temp folder does not.

The defect these tests pin: the harnesses used `tempfile.TemporaryDirectory()`,
which asks the OS for a temp folder and assumes it is usable. In a confined
Windows environment that assumption is false in a specific and confusing way -
the directory is created, and then every read, write and `sqlite3.connect`
inside it is refused, while `TemporaryDirectory.__exit__` raises during cleanup
so a run that did all its work still exits non-zero and prints a traceback
instead of its results.

Two properties matter and they fail independently:

1. **the fallback order** - an explicit override must win, and the repository
   itself must be tried before the system temp folder, because a checkout is
   the one tree the process has already proven it can write to;
2. **the probe is real** - a path counts as usable only after a file has been
   created *and* the directory listed. Checking `os.access` or existence alone
   would accept exactly the directories that caused the original failure.

The tests deliberately do not assert on the machine's ability to write to any
particular location; they assert on the *rule*, so they hold on a normal
developer box and in a locked-down runner alike.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src._scratch import (  # noqa: E402
    FALLBACK_DIRNAME,
    SCRATCH_ENV_VAR,
    ScratchUnavailable,
    candidate_scratch_roots,
    repo_root,
    scratch_dir,
    scratch_root,
)


def test_scratch_dir_is_actually_usable() -> None:
    """The whole point: a directory you can write in, list, and put a DB in.

    Asserting the three operations and not just existence, because "the path
    exists" is true of the broken case too.
    """
    import sqlite3

    with scratch_dir("test-") as path:
        assert path.is_dir()
        (path / "plain.txt").write_text("x", encoding="utf-8")
        assert (path / "plain.txt").read_text(encoding="utf-8") == "x"
        assert "plain.txt" in os.listdir(path)
        (path / "nested").mkdir()
        conn = sqlite3.connect(str(path / "evidence.db"))
        try:
            conn.execute("create table items (id text)")
            conn.commit()
            assert conn.execute(
                "select name from sqlite_master where type='table'"
            ).fetchone() == ("items",)
        finally:
            conn.close()


def test_scratch_dir_is_removed_afterwards() -> None:
    with scratch_dir("test-") as path:
        created = path
    assert not created.exists()


def test_cleanup_failure_is_not_fatal(monkeypatch, tmp_path_factory) -> None:
    """A refused delete must not turn a finished run into a crashed one.

    In the real confined case `rmtree` does not raise at all - it runs with
    `ignore_errors=True` and quietly leaves the tree - so this test asserts the
    contract that matters: the context manager returns normally and the work is
    not lost, rather than pretending an exception could not escape.
    """
    import src._scratch as scratch

    calls = []

    def refuse(path, ignore_errors=False):
        calls.append((path, ignore_errors))
        return None  # what rmtree(ignore_errors=True) does when it cannot delete

    monkeypatch.setattr(scratch.shutil, "rmtree", refuse)
    with scratch_dir("test-") as path:
        created = path
        assert created.is_dir()
    assert calls == [(created, True)], (
        "cleanup must be best-effort, and must not run with errors enabled"
    )

def test_override_wins_and_is_first(monkeypatch, scratch_path) -> None:
    monkeypatch.setenv(SCRATCH_ENV_VAR, str(scratch_path))
    roots = candidate_scratch_roots()
    assert roots[0] == scratch_path
    assert scratch_root() == scratch_path


def test_repo_local_fallback_is_tried_before_the_system_temp(monkeypatch) -> None:
    """Order is the contract, not an implementation detail.

    A checkout is the one directory this process has already read its own source
    from, so it is the better guess than a temp folder we have never tested.
    """
    monkeypatch.delenv(SCRATCH_ENV_VAR, raising=False)
    roots = candidate_scratch_roots()
    expected = repo_root() / FALLBACK_DIRNAME
    assert expected in roots
    import tempfile

    assert roots.index(expected) < roots.index(Path(tempfile.gettempdir()))


def test_candidates_are_deduplicated(monkeypatch) -> None:
    """Pointing the override at a default must not probe it twice."""
    import tempfile

    monkeypatch.setenv(SCRATCH_ENV_VAR, tempfile.gettempdir())
    roots = candidate_scratch_roots()
    normalised = [str(r).casefold() if os.name == "nt" else str(r) for r in roots]
    assert len(normalised) == len(set(normalised))


def test_an_unusable_candidate_is_skipped_not_chosen(monkeypatch, scratch_path) -> None:
    """A directory that exists but cannot be written must not be selected.

    This is the actual defect being pinned: the old code never checked, so it
    handed `sqlite3` a path it could not open and the error surfaced three
    frames down as `unable to open database file`.
    """
    blocked = scratch_path / "blocked"
    blocked.mkdir()
    monkeypatch.setenv(SCRATCH_ENV_VAR, str(blocked))

    real_usable = __import__("src._scratch", fromlist=["_is_usable"])._is_usable

    def fake(path: Path):
        if path == blocked:
            return False, "simulated refusal"
        return real_usable(path)

    import src._scratch as scratch

    monkeypatch.setattr(scratch, "_is_usable", fake)
    chosen = scratch_root()
    assert chosen != blocked, "an unusable path was selected"


def test_no_writable_candidate_names_every_attempt(monkeypatch) -> None:
    """The failure has to say what was tried and how to fix it.

    A bare errno is what made the original problem hard to diagnose; the
    operator needs the candidate list and the env var name.
    """
    import src._scratch as scratch

    monkeypatch.setattr(
        scratch, "_is_usable", lambda path: (False, "simulated refusal")
    )
    with pytest.raises(ScratchUnavailable) as caught:
        scratch_root()
    message = str(caught.value)
    assert SCRATCH_ENV_VAR in message
    assert "simulated refusal" in message
    for root in candidate_scratch_roots():
        assert str(root) in message


def test_scratch_root_is_not_cached_across_calls(monkeypatch, scratch_path) -> None:
    """A long-lived process can outlive the conditions that made a path good.

    The MCP server and the web panel both run for a long time, so a memoised
    answer here would reappear as the opaque database error this module exists
    to prevent.
    """
    monkeypatch.setenv(SCRATCH_ENV_VAR, str(scratch_path))
    first = scratch_root()
    other = scratch_path / "second"
    other.mkdir()
    monkeypatch.setenv(SCRATCH_ENV_VAR, str(other))
    assert scratch_root() == other != first


def test_the_shipped_harnesses_use_scratch_dir() -> None:
    """Guard the two scripts the defect was found in.

    Asserting on the import and the context manager rather than on behaviour,
    because these harnesses write into `data/` and a test that runs them would
    be asserting on this machine's permissions - which is precisely the class of
    mistake that hid the original bug.
    """
    for name in ("eval_retrieval.py", "eval_multiturn.py"):
        source = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "from src._scratch import scratch_dir" in source, (
            f"{name} no longer imports the scratch helper"
        )
        assert "scratch_dir(" in source, f"{name} does not use scratch_dir"
        assert "tempfile.TemporaryDirectory" not in source, (
            f"{name} went back to the system temp folder"
        )


def test_tests_use_the_scratch_fixture_not_tmp_path() -> None:
    """The suite's own half of the same fix.

    `tmp_path` is unusable in a confined Windows harness because pytest creates
    it with `mkdir(mode=0o700)` and strips group/other bits from the base, which
    removes the inherited entries the process needs to traverse it.

    Matched with a word boundary and not a substring: `tmp_path_factory` is a
    different fixture that this file has no business forbidding, and a naive
    `"tmp_path" in text` check flagged it - the vacuous/over-eager guard failure
    mode this suite pins elsewhere.
    """
    offenders = []
    for path in (REPO_ROOT / "tests").glob("*.py"):
        if path.name in {"conftest.py", "pytest_scratch.py", "test_scratch.py"}:
            continue
        if re.search(r"\btmp_path\b", path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert not offenders, (
        f"these tests still depend on tmp_path: {sorted(offenders)}"
    )


def test_the_guard_would_notice_a_real_regression(scratch_path) -> None:
    """The check above must be able to fail, proved rather than asserted.

    A guard that cannot go red is worse than no guard, and the naive version of
    this one was wrong in both directions at once - it flagged `tmp_path_factory`
    while a substring test would also have missed nothing real. Pin the two
    halves separately.
    """
    good = "def test_x(tmp_path_factory):\n    pass\n"
    bad = "def test_x(tmp_path):\n    pass\n"
    assert not re.search(r"\btmp_path\b", good), "a different fixture was flagged"
    assert re.search(r"\btmp_path\b", bad), "the real thing would slip through"
