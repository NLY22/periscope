"""Shared Playwright browser selection, profile paths and environment setup."""

from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
import errno
import json
import os
import sys
import tempfile
from time import monotonic

from periscope.runtime import paths
from periscope.runtime.files import replace_file


# The repo root, not this file's directory. `ROOT = Path(__file__).parent` was
# here, which made every path below resolve under `collectors/`:
# `collectors/.playwright-browsers` (absent) and `collectors/.pwtmp`. The
# consequence was not a crash - `browser_channel()` looked for the bundled
# chromium in a directory that does not exist, fell through to `"chrome"`, and
# every collector then drove the *user's own* Chrome window. That is the exact
# behaviour this module was written to stop, reintroduced silently.


ROOT = paths.ROOT

BROWSERS = paths.BROWSERS
PW_TMP = paths.PW_TMP
PROFILE_BUSY_EXIT = 3


class BrowserProfileBusy(RuntimeError):
    """Another collector owns this platform's persistent browser session."""


@contextmanager
def profile_lease(profile: Path):
    """One process per platform profile; OS locks are released on process exit."""
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    lock_path = profile.parent / ("." + profile.name + ".collector.lock")
    with lock_path.open("a+b") as handle:
        if not handle.tell():
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise BrowserProfileBusy("profile_busy: " + profile.name) from exc
            raise
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_checkpoint(out: Path, rows: list[dict]) -> None:
    """Replace a completed batch atomically; failed writes retain the old batch."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=out.name + ".", suffix=".tmp", dir=out.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(rows, stream, ensure_ascii=False)
        replace_file(temporary, out)
    finally:
        temporary.unlink(missing_ok=True)


def wait_for_items(page, size, *, previous: int = 0, timeout_ms: float = 8000) -> bool:
    """Dispatch browser events until a new response arrives, up to the deadline."""
    deadline = monotonic() + max(0, timeout_ms) / 1000
    while size() <= previous:
        remaining = (deadline - monotonic()) * 1000
        if remaining <= 0:
            return False
        page.wait_for_timeout(min(100, remaining))
    return True


@contextmanager
def browser_session(profile: Path, *, headed: bool = False, user_agent: str, viewport: dict, prepare=None):
    """One isolated session reused for all query words within this worker."""
    (prepare or ensure_env)()
    from playwright.sync_api import sync_playwright
    with profile_lease(profile), sync_playwright() as playwright:
        kwargs = launch_kwargs(headed=headed)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), user_agent=user_agent, locale="zh-CN", viewport=viewport, **kwargs)
        try:
            yield context
        finally:
            context.close()


def run_collector(main) -> int:
    try:
        return main()
    except BrowserProfileBusy as exc:
        print(str(exc), file=sys.stderr)
        return PROFILE_BUSY_EXIT


def browser_channel() -> str | None:
    """返回应该传给 Playwright 的 channel。

    None 表示用 Playwright 自带的 chromium（首选）。
    "chrome" 只在自带二进制缺失时作为兜底，因为那会驱动用户自己的浏览器。
    """
    exe = BROWSERS / "chromium-1248" / "chrome-win64" / "chrome.exe"
    if exe.exists():
        return None
    # 换个版本号也能命中
    if BROWSERS.is_dir():
        for d in sorted(BROWSERS.glob("chromium-*")):
            for cand in d.glob("chrome-*/chrome.exe"):
                if cand.exists():
                    return None
    return "chrome"


def launch_kwargs(*, headed: bool = False) -> dict:
    """传给 launch_persistent_context 的公共参数。"""
    ch = browser_channel()
    kw: dict = {
        "headless": not headed,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if ch:
        kw["channel"] = ch
    return kw


def ensure_env() -> None:
    """补齐环境变量，避免 Playwright 找不到浏览器或临时目录不可写。"""
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS))
    PW_TMP.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TMPDIR", str(PW_TMP))
    os.environ.setdefault("TEMP", str(PW_TMP))
    os.environ.setdefault("TMP", str(PW_TMP))
