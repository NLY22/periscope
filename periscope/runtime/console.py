"""Console encoding for B's command-line entry points.

The problem, stated once: the default console encoding on Chinese Windows is
cp936, which cannot encode `⚠`, `·` or any of the report glyphs. A script that
finishes its work and then prints its summary dies with `UnicodeEncodeError` on
the last line - the output looks like a broken tool, and the work that ran is
lost with it.

There are two ways to fix that, and only one of them is safe:

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)   # replaces the object
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # reconfigures it

The replacement form is what this project used in eight files, and it is the
root cause of a bug that took a while to attribute: whatever owned `sys.stdout`
before (pytest's capture, a redirect, a caller's wrapper) is now detached from
the real fd, and the process dies at shutdown with "I/O operation on closed
file". The reconfigure form changes only the encoding of the existing stream,
so it composes with a captor instead of breaking it.

So: no module in B rebinds `sys.stdout`. Modules that need UTF-8 output call
`force_utf8_output()` from their `__main__` path, and everything else just
prints.

`src/_cli.py` has the same function for A. It is deliberately a separate copy
rather than a shared import, because `core/` is not part of the installed
package (`pyproject.toml` ships only `src`), so `src` may not depend on it.
"""

from __future__ import annotations

import sys


def force_utf8_output() -> None:
    """Make stdout/stderr survive a code page that cannot encode the output.

    Idempotent and safe to call from several entry points in one process. A
    stream that does not support `reconfigure` (a plain `io.StringIO`, a pipe
    wrapper from a test) is left alone: replacing it would trade an encoding
    problem for the detached-stream crash this module exists to avoid.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
