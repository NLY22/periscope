from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src._scratch import scratch_root  # noqa: E402


def _pin_basetemp() -> None:
    """Point pytest's own temp base at a directory we have proven is writable.

    `tmp_path` is provisioned under the system temp folder. Where that folder is
    usable this changes nothing of consequence; where it is not, every
    `tmp_path` test errors at *setup* and the suite reads as a broken project
    when nothing in the project is broken.

    This helps, but it does **not** rescue `tmp_path` on its own, and the reason
    is worth recording because the fix looks like it should work. pytest
    provisions each test's directory with `mkdir(mode=0o700)` and additionally
    strips group/other bits off an existing base (`_pytest/tmpdir.py`). On
    Windows that removes the inherited entries a confined process needs to
    traverse the directory, so the path is refused even though this module just
    created it successfully. Tests that need scratch space should therefore
    depend on the `scratch_path` fixture below rather than on `tmp_path`.

    An explicit `--basetemp` always wins: whoever named a directory meant it.
    """
    if any(arg.startswith("--basetemp") for arg in sys.argv):
        return
    try:
        base = scratch_root() / "pytest-tmp"
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Leave pytest alone so its own error is the visible one, rather than a
        # half-configured state we cannot explain.
        return
    sys.argv.extend(["--basetemp", str(base)])


_pin_basetemp()


@pytest.fixture
def scratch_path():
    """A writable scratch directory that works where `tmp_path` does not.

    Same contract as `tmp_path` for every use this suite has (a `Path` that
    exists and is empty, removed afterwards), but created with a plain `mkdir`
    instead of pytest's `mode=0o700`, which is the part that fails on Windows in
    a confined harness. Prefer this over `tmp_path` in new tests.

    Cleanup is best-effort by design: see `src/_scratch.py`. A refused delete
    must not turn a passing run into a crashing one.
    """
    root = scratch_root()
    path = root / f"test-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def outside_repo_path():
    """A writable scratch directory guaranteed to be *outside* the checkout.

    `scratch_path` deliberately lives inside the repository, which is right for
    almost everything but wrong for the tests that check what the CLI prints for
    a path it does not own: `display_path` relativises anything inside the repo,
    so an inside-the-repo fixture cannot express "a path elsewhere" at all.

    Yields a `Path` when the system temp folder is usable, and skips when it is
    not. The skip is the honest outcome rather than a pass: a confined runner
    that refuses its own temp folder has not demonstrated the out-of-repo case
    either way, and reporting that as green would be the silent-vacuous-check
    failure this suite guards against elsewhere.
    """
    import tempfile

    candidate = Path(tempfile.gettempdir()).resolve()
    if str(candidate).lower().startswith(str(ROOT).lower()):
        pytest.skip("the system temp folder is inside the repository here")
    path = candidate / f"periscope-outside-{uuid.uuid4().hex}"
    try:
        path.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        pytest.skip(f"system temp folder is not writable here ({exc.strerror or exc})")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def pin_dns(monkeypatch):
    """Never let the machine's resolver decide whether the suite is green.

    `src/url_security.py` resolves webhook hostnames on purpose -- that check
    is what stops a config typo from pointing the notifier at 169.254.169.254.
    But behind a network that intercepts DNS, `example.com` answers with an
    address from the RFC 2544 benchmark range, which is *not* globally
    routable, so a security check that is working correctly failed 20 webhook
    tests on one machine and not another.

    Tests that want to exercise the resolver itself patch
    `src.url_security._resolve_hostname` directly (see test_url_security.py),
    so pinning only the default lookup keeps both the determinism and the
    coverage.
    """

    def fake_resolver(hostname: str, port: int) -> list:
        return [(2, 1, 6, "", ("93.184.215.14", port))]

    monkeypatch.setattr("src.url_security._default_resolver", fake_resolver)
