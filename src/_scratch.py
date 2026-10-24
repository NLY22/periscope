"""Where throwaway working files go, when the system temp directory will not do.

Why this module exists (and why it is not just `tempfile`): `tempfile` answers
"where does this OS put temporary files", which is the wrong question for two
environments this project actually runs in.

1. **A confined harness.** Some sandboxes hand a process a system temp
   directory it cannot use at all: the directory is created, and then every
   read, write, `mkdir` and `sqlite3.connect` inside it is refused. Nothing
   degrades gracefully there - `TemporaryDirectory.__exit__` raises on cleanup,
   so a run that did all its work correctly still exits non-zero and prints a
   traceback instead of its table. That is the failure this module removes.
2. **A box where `/tmp` is small or missing** (a slim container, a locked-down
   CI image). Same symptom, different cause.

The rule: ask the environment first via `PERISCOPE_SCRATCH_DIR`, otherwise take
a directory *inside* the repository - which is the one place a run of this
project has already proven it can write to, because it just read its own source
out of it. Never silently fall back to an unusable path: if nothing is
writable, say which paths were tried and why each failed, because "unable to
open database file" from deep inside `sqlite3` is what this module exists to
stop being the visible error.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

#: Set this to put scratch files somewhere of your choosing. Highest priority.
SCRATCH_ENV_VAR = "PERISCOPE_SCRATCH_DIR"

#: Repository-local fallback, relative to the checkout root.
FALLBACK_DIRNAME = ".periscope-scratch"


class ScratchUnavailable(RuntimeError):
    """No candidate directory could be written to.

    Carries every attempt so the message can name the paths and the reason each
    one failed, instead of a bare errno from three frames down.
    """


def repo_root() -> Path:
    """The checkout root: this file lives at `<root>/src/_scratch.py`."""
    return Path(__file__).resolve().parent.parent


def _is_usable(path: Path) -> "tuple[bool, str]":
    """Can we create a file *and* list the directory? Probe, do not assume.

    Both halves matter and they fail independently: a sandbox can allow reading
    a directory listing while refusing every write, and the confined case above
    refuses both. `os.listdir` is included because `tempfile`'s own cleanup
    walks the tree with `scandir`, so a directory that cannot be listed is
    unusable even for a read-only caller.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"cannot create directory ({exc.strerror or exc})"
    if not path.is_dir():
        return False, "exists but is not a directory"

    probe = path / f".periscope-probe-{uuid.uuid4().hex}"
    try:
        probe.write_text("probe", encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write a file ({exc.strerror or exc})"
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        os.listdir(path)
    except OSError as exc:
        return False, f"cannot list the directory ({exc.strerror or exc})"
    return True, "ok"


def candidate_scratch_roots() -> "list[Path]":
    """Every directory we would accept, best first, in one place.

    Exposed (rather than inlined) so the tests can assert the *order* and the
    *set* without duplicating the rule, and so an error message can report what
    was tried.
    """
    roots: "list[Path]" = []
    override = os.environ.get(SCRATCH_ENV_VAR, "").strip()
    if override:
        roots.append(Path(override).expanduser())
    roots.append(repo_root() / FALLBACK_DIRNAME)
    roots.append(Path(tempfile.gettempdir()))
    # De-duplicate while preserving order: the override may legitimately point
    # at one of the defaults, and probing the same path twice is just noise in
    # the error message.
    seen = set()
    unique: "list[Path]" = []
    for root in roots:
        key = str(root).casefold() if os.name == "nt" else str(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def scratch_root() -> Path:
    """The first candidate that is actually writable, or raise.

    Resolved on every call and not cached: a long-lived process (the MCP server,
    the web panel) can outlive the conditions that made a path good, and a stale
    answer here reappears as the opaque database error this module exists to
    prevent.
    """
    attempts: "list[str]" = []
    for root in candidate_scratch_roots():
        ok, reason = _is_usable(root)
        if ok:
            return root
        attempts.append(f"  {root} - {reason}")
    raise ScratchUnavailable(
        f"No writable scratch directory. Tried:\n" + "\n".join(attempts)
        + f"\nSet {SCRATCH_ENV_VAR} to a writable directory and try again."
    )


@contextmanager
def scratch_dir(prefix: str = "periscope-") -> Iterator[Path]:
    """A fresh directory under `scratch_root()`, removed on the way out.

    Cleanup failure is deliberately non-fatal, which is the one place this
    differs from `tempfile.TemporaryDirectory`. A confined sandbox refuses the
    recursive delete *after* the work is already done and the results already
    printed; letting that raise would turn a successful run into a crashed one,
    which is the exact symptom this module was written to remove. The directory
    is left behind instead, so the leak is visible rather than paid for with a
    traceback.
    """
    base = scratch_root()
    path = base / f"{prefix}{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
