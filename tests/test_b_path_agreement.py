"""The paths B's modules *agree* on, enforced instead of hoped for.

Every test here corresponds to a defect that shipped. The class of bug is the
same each time: two modules compute their own idea of where a file lives, both
paths are valid, neither raises, and the consequence is silent - a cache that is
written but never read, a corpus that is built in one directory while the
diagnostics read another.

That silence is why these are tests and not comments. `test_b_paths.py` checks
the data-dir *switch*; this file checks that nothing has grown a second,
private idea of the layout next to it.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- the fulltext cache
def test_writer_and_reader_name_the_same_fulltext_cache():
    """The 3,648 orphaned bodies.

    `collectors/backfill_fulltext.py` wrote `ROOT/fulltext_cache.jsonl` while
    `tools/ingest_all.py` read `<cache>/fulltext_cache.jsonl`. Both existed;
    the ingest never saw a single row. The cache on this machine held 3,648
    records / 24 MB that had never been merged into any corpus.
    """
    from collectors import backfill_fulltext
    from core import paths

    # The writer resolves through core.paths at call time.
    assert paths.fulltext_cache() == paths.CACHE_DIR / "fulltext_cache.jsonl"

    # The reader must agree. It used to be a module constant; it is now a
    # function precisely so that this comparison cannot drift again.
    import tools.ingest_all as ingest_all

    # `tools/` has no `__init__.py` (namespace package), so an installed
    # distribution named `tools` could shadow it. Assert which one resolved,
    # rather than comparing paths from two different modules and calling the
    # mismatch a cache bug.
    assert Path(ingest_all.__file__).resolve().is_relative_to(ROOT), (
        "imported `tools.ingest_all` from %s, not this checkout" % ingest_all.__file__
    )
    assert Path(ingest_all.fulltext_cache()) == Path(paths.fulltext_cache())


def test_the_legacy_cache_location_is_still_known():
    """The old path has to stay nameable, or migrating it is impossible.

    Renaming a data file without recording where it used to be is how the same
    bug gets diagnosed twice.
    """
    from core import paths

    assert paths.LEGACY_FULLTEXT_CACHE == ROOT / "fulltext_cache.jsonl"
    assert paths.LEGACY_FULLTEXT_CACHE != paths.FULLTEXT_CACHE


def test_a_legacy_cache_is_migrated_once(scratch_path, monkeypatch):
    """First reader moves the file; the canonical path then wins.

    Simulated entirely inside the scratch tree: the real 24 MB cache is not
    touched, because a test that renames the user's data to prove a point is
    worse than the bug.
    """
    from core import paths

    canonical = scratch_path / "cache" / "fulltext_cache.jsonl"
    legacy = scratch_path / "fulltext_cache.jsonl"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"id": "a", "text": "body"}\n', encoding="utf-8")

    monkeypatch.setattr(paths, "FULLTEXT_CACHE", canonical)
    monkeypatch.setattr(paths, "LEGACY_FULLTEXT_CACHE", legacy)

    resolved = paths.fulltext_cache()
    assert resolved == canonical, "the canonical location has to win"
    assert canonical.read_text(encoding="utf-8").strip().endswith('"body"}')
    assert not legacy.exists(), "leaving both copies is how the split returns"

    # Second call is a no-op, not a re-move.
    assert paths.fulltext_cache() == canonical
    assert canonical.exists()


def test_an_existing_canonical_cache_is_not_overwritten(scratch_path, monkeypatch):
    """A staged legacy file must not resurrect old text over newer text.

    `replace` is unconditional, so the guard has to be "only when the canonical
    path is absent". Otherwise a stale copy that someone restored by hand
    silently wins over the current cache.
    """
    from core import paths

    canonical = scratch_path / "cache" / "fulltext_cache.jsonl"
    legacy = scratch_path / "fulltext_cache.jsonl"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text('{"id": "new"}\n', encoding="utf-8")
    legacy.write_text('{"id": "old"}\n', encoding="utf-8")

    monkeypatch.setattr(paths, "FULLTEXT_CACHE", canonical)
    monkeypatch.setattr(paths, "LEGACY_FULLTEXT_CACHE", legacy)

    assert paths.fulltext_cache() == canonical
    assert '"new"' in canonical.read_text(encoding="utf-8")


# ------------------------------------------------------------------- credentials
def _docstring_lines(tree: ast.AST) -> set[int]:
    """Line numbers covered by module/class/function docstrings.

    A docstring that explains *why* a literal path was wrong is documentation,
    not a recurrence of the bug. Flagging it would push the next person to
    delete the explanation rather than keep it.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            end = getattr(first.value, "end_lineno", first.lineno)
            out.update(range(first.lineno, end + 1))
    return out


def test_no_b_module_hardcodes_a_cookie_directory():
    """A literal `D:\\\\work\\\\...` path is one person's checkout.

    `collectors/collector.py` carried
    `Path(r"D:\\\\work\\\\periscope\\\\data\\\\cookies")`. On any other machine the
    sources that need a session collected anonymously and reported "0 items"
    instead of "no cookie file" - a wrong answer that looks like a real one.

    Docstrings are exempt: the explanation of that bug quotes the path, and
    deleting the explanation to satisfy a test would be the wrong repair.
    """
    offenders = []
    for path in [*sorted((ROOT / "collectors").glob("*.py")),
                 *sorted((ROOT / "core").glob("*.py")),
                 ROOT / "topic.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        documented = _docstring_lines(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if getattr(node, "lineno", -1) in documented:
                    continue
                low = node.value.lower().replace("/", "\\")
                if "\\work\\periscope" in low:
                    offenders.append(f"{path.name}:{node.lineno} {node.value[:40]!r}")
    assert not offenders, "absolute checkout paths in B:\n" + "\n".join(offenders)


def test_the_cookie_directory_follows_the_override(scratch_path):
    """`PERISCOPE_COOKIE_DIR` exists so credentials can live outside the repo.

    Read in a fresh interpreter because `core.paths` resolves it at import time,
    which is exactly how the CLIs start.
    """
    env = {**os.environ, "PERISCOPE_COOKIE_DIR": str(scratch_path)}
    out = subprocess.check_output(
        [sys.executable, "-c",
         "from core import paths; print(paths.COOKIES); print(len(paths.COOKIES.name))"],
        cwd=str(ROOT), env=env, text=True, timeout=60,
    )
    assert out.splitlines()[0] == str(scratch_path)


def test_collector_reads_cookies_from_the_shared_location():
    from collectors import collector
    from core import paths

    assert Path(collector.COOKIE_DIR) == Path(paths.COOKIES)


# --------------------------------------------------------------- the run store
def test_a_stray_json_in_the_topic_dir_does_not_become_the_latest_run(
        scratch_path, monkeypatch):
    """`--ingest-last` used to poison every later run.

    It materialised `http_items.topic.json` *inside* `<DATA_DIR>/topics/`.
    `latest()` sorted by filename, and `http_items.topic.json` sorts after every
    `2026...json`, so one `--ingest-last` made the next one read the leftover
    instead of the newest run. The materialised file is now written to a temp
    directory, and this test pins the defence in depth.
    """
    from core import paths, runstore

    monkeypatch.setattr(paths, "TOPIC_DIR", scratch_path)

    real = scratch_path / "20261019-132000_人形机器人.json"
    real.write_text('{"topic": "人形机器人", "rows": []}', encoding="utf-8")
    stray = scratch_path / "http_items.topic.json"
    stray.write_text("[]", encoding="utf-8")

    assert runstore.latest() == real, "a non-run file took over latest()"
    assert runstore.find("人形机器人") == real


def test_the_run_name_filter_still_accepts_a_real_run(scratch_path, monkeypatch):
    """Bite proof: the filter must not reject the files it is meant to find."""
    from core import paths, runstore

    monkeypatch.setattr(paths, "TOPIC_DIR", scratch_path)
    for name in ("20261019-132000_甲.json", "20260101-000000_乙.json"):
        (scratch_path / name).write_text("{}", encoding="utf-8")
    assert runstore.latest().name == "20261019-132000_甲.json"


def test_materialise_writes_outside_the_topic_directory(scratch_path, monkeypatch):
    """The temp file must not land among the run shards.

    Checked by execution rather than by reading the source: the point is where
    the file actually goes.

    Uses the `scratch_path` fixture rather than pytest's temporary-directory
    fixture, because the latter is provisioned with `mkdir(mode=0o700)` and this
    Windows sandbox refuses that. See `tests/conftest.py`.
    """
    import json

    import topic
    from core import paths

    topics = scratch_path / "topics"
    topics.mkdir()
    scratch = scratch_path / "scratch"
    scratch.mkdir()

    monkeypatch.setattr(paths, "TOPIC_DIR", topics)
    monkeypatch.setattr(paths, "SCRATCH", scratch)

    payload = {"topic": "t", "rows": [
        {"_layer": "platform", "source": "weibo_search", "title": "一条",
         "url": "https://weibo.com/1"},
        {"_layer": "local", "title": "本地", "url": "https://x/2"},
    ]}
    out = topic._materialise(payload)

    try:
        assert out.parent != topics, "the temp file landed among the run shards"
        assert out.name.startswith("http_items.topic.")
        rows = json.loads(out.read_text(encoding="utf-8"))
        assert len(rows) == 1, "local rows are already in the corpus"
        assert rows[0]["source"] == "weibo_search"
        assert "_layer" not in rows[0]
    finally:
        out.unlink(missing_ok=True)


def test_the_run_shard_conversion_is_shared_and_correct():
    """`topic_persist.py` handed a run shard to an ingest that reads a list.

    `ingest_all --only` iterates a *list* of rows. A run shard is
    `{"version": ..., "rows": [...]}`. The ingest therefore iterated the dict,
    got its keys, and asked the string `"version"` for `.get`:

        AttributeError: 'str' object has no attribute 'get'

    It happened after the ingest had already reported nothing useful, so the
    log said `ingest rc=1` and both halves of the script had done nothing.
    `topic.py --ingest-last` had its own conversion, so the two callers had
    drifted apart. One function now serves both.
    """
    from core import runstore

    payload = {
        "version": 1,
        "topic": "t",
        "rows": [
            {"_layer": "api", "title": "api 行", "url": "https://a/1"},
            {"_layer": "local", "title": "本地", "url": "https://a/2"},
            {"_layer": "platform", "source": "weibo_search",
             "title": "微博", "url": "https://a/3"},
            {"title": "无 source", "url": "https://a/4"},
        ],
    }
    rows = runstore.ingestable_rows(payload)
    urls = [r["url"] for r in rows]
    assert "https://a/2" not in urls, "local rows are already in the corpus"
    assert "https://a/4" not in urls, "a row without a source cannot be filed"
    assert rows[0]["source"] == "dsapi"
    assert rows[1]["source"] == "weibo_search"
    assert rows[1]["kind"] == "community"
    assert all("_layer" not in r for r in rows)


def test_the_ingest_file_is_a_list_and_is_written_outside_the_topic_dir(
        scratch_path):
    """`--only` reads a list; the materialised file must not sit among the runs."""
    import json

    from core import runstore

    topics = scratch_path / "topics"
    topics.mkdir()
    out = runstore.write_ingest_file(
        {"rows": [{"_layer": "platform", "source": "weibo_search",
                   "title": "一条", "url": "https://weibo.com/1"}]},
        directory=scratch_path / "scratch",
    )
    try:
        assert out.parent != topics
        assert out.name.startswith("http_items.topic.")
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(payload, list), "the ingest iterates this; a dict fails"
        assert payload[0]["source"] == "weibo_search"
    finally:
        out.unlink(missing_ok=True)


def test_persist_reads_a_run_shard_the_way_the_store_writes_it(scratch_path):
    """End to end through the real writer, without running the ingest.

    The ingest is a subprocess that opens the corpus, so it is out of scope for
    a unit test. What is in scope is the agreement that broke: `runstore.write`
    produces a dict, and the persist path must handle a dict.
    """
    from datetime import datetime

    from core import paths, runstore

    base = scratch_path / "topics"
    base.mkdir()
    import unittest.mock as mock

    with mock.patch.object(paths, "TOPIC_DIR", base):
        path = runstore.write(
            "话题",
            [{"_layer": "platform", "source": "weibo_search",
              "title": "一条", "url": "https://weibo.com/1", "_origin": "platform"}],
            created_at=datetime(2026, 10, 19, 12, 0, 0),
        )
    payload = runstore.read(path)
    assert isinstance(payload, dict), "the store writes a dict; callers must expect one"
    assert payload["rows"][0]["_layer"] == "platform"
    rows = runstore.ingestable_rows(payload)
    assert len(rows) == 1


# --------------------------------------------------------------- the browser
def test_the_browser_launcher_resolves_paths_from_the_repo_root():
    """One `parent` too few, and every collector drives the user's own Chrome.

    `collectors/browser_launch.py` had `ROOT = Path(__file__).parent`, i.e.
    `collectors/`. It then looked for the bundled chromium in
    `collectors/.playwright-browsers`, which does not exist, concluded the
    binary was missing, and returned `channel="chrome"`. Playwright's "chrome"
    channel launches the *installed* Chrome - a window on the user's desktop,
    sharing their profile, bookmarks and logged-in sessions. That is precisely
    what this module's docstring says it exists to prevent.
    """
    from collectors import browser_launch
    from core import paths

    assert Path(browser_launch.ROOT) == Path(paths.ROOT)
    assert Path(browser_launch.BROWSERS) == Path(paths.BROWSERS)
    assert Path(browser_launch.PW_TMP) == Path(paths.PW_TMP)
    assert Path(browser_launch.ROOT) != Path(browser_launch.__file__).parent, (
        "the launcher is back to resolving paths from its own directory"
    )


def test_the_launcher_uses_the_bundled_browser_when_it_is_present():
    """`None` means "Playwright's own chromium"; a string names an installed one.

    Skips when the checkout has no bundled browser: asserting the *decision*
    requires the input it decides on, and a machine without the download has
    not demonstrated anything either way.
    """
    from collectors import browser_launch

    if not browser_launch.BROWSERS.is_dir():
        pytest.skip("no bundled Playwright browser in this checkout")
    bundled = any(browser_launch.BROWSERS.glob("chromium-*/chrome-*/chrome.exe"))
    if not bundled:
        pytest.skip("bundled browser directory exists but holds no executable")
    assert browser_launch.browser_channel() is None, (
        "a bundled chromium is present but the launcher still asks for the "
        "user's installed Chrome"
    )


def test_the_browser_collectors_share_the_profile_directory():
    """Three collectors, one profiles root: a rename must not desync them."""
    from collectors import collect_douyin, collect_kuaishou, collect_xhs
    from core import paths

    for module in (collect_xhs, collect_douyin, collect_kuaishou):
        assert Path(module.PROFILES) == Path(paths.PROFILES), module.__name__


# --------------------------------------------------------------- console safety
def test_no_module_rebinds_sys_stdout_at_import_time():
    """The root cause of "I/O operation on closed file" at shutdown.

    Eight modules did `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`.
    Under pytest that detaches the capture object; the process then dies while
    tearing down, after the work is done. `reconfigure` is the fix, and this
    pins it so the old idiom cannot come back.

    Docstrings are exempt by line range, not by string content: `core/console.py`
    explains the two forms and necessarily quotes the wrong one.
    """
    offenders = []
    for path in [*sorted((ROOT / "core").glob("*.py")),
                 *sorted((ROOT / "collectors").glob("*.py")),
                 *sorted((ROOT / "tools").glob("*.py")),
                 ROOT / "topic.py"]:
        source = path.read_text(encoding="utf-8")
        documented = _docstring_lines(ast.parse(source))
        for i, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if i in documented or stripped.startswith("#"):
                continue
            if "sys.stdout =" in stripped:
                offenders.append(f"{path.relative_to(ROOT)}:{i}: {stripped}")
    assert not offenders, "modules still rebind sys.stdout:\n" + "\n".join(offenders)


def test_the_console_helper_is_idempotent_and_survives_a_plain_stream():
    """Called from several entry points, and from objects that cannot reconfigure.

    `reconfigure` exists on `TextIOWrapper` but not on a `StringIO`, a pytest
    capture object, or a caller's own writer. Raising there would turn an
    encoding fix into an import-time crash, so a stream that cannot be
    reconfigured must be left exactly as it is.
    """
    from core.console import force_utf8_output

    force_utf8_output()
    force_utf8_output()          # idempotent

    class Plain:
        pass

    original = Plain()
    saved = sys.stdout
    sys.stdout = original
    try:
        force_utf8_output()
        assert sys.stdout is original, "a non-reconfigurable stream was replaced"
    finally:
        sys.stdout = saved


def test_ingest_all_can_be_imported_without_owning_stdout():
    """`--fulltext-only` is reachable in-process, so the import must be safe.

    The module used to do `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`
    at import time. Importing it under pytest replaced the capture object and
    the run died at shutdown with "I/O operation on closed file" - a crash
    caused by an import that only printed.
    """
    import importlib

    class Captor:
        """Stand-in for pytest's capture object: no `.buffer`, no `reconfigure`."""

        def write(self, _s):
            return 0

        def flush(self):
            pass

    saved = sys.stdout
    captor = Captor()
    sys.stdout = captor
    try:
        import tools.ingest_all as mod

        importlib.reload(mod)
        assert sys.stdout is captor, "importing the module replaced the caller's stdout"
    finally:
        sys.stdout = saved


@pytest.mark.parametrize("relative", [
    "topic.py",
    "tools/dump_shard.py",
    "tools/topic_persist.py",
    "tools/sessions.py",
    "collectors/backfill_fulltext.py",
    "collectors/fetch_bodies.py",
    "collectors/collect_http.py",
])
def test_entrypoint_still_starts(relative: str):
    """Every path change above is checked against a real process.

    `--help` because it exercises imports, `ROOT` resolution and argument
    parsing without touching the network or the corpus.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, relative, "--help"],
        cwd=str(ROOT), env=env, capture_output=True,
        encoding="utf-8", errors="replace", timeout=90,
    )
    assert proc.returncode == 0, proc.stderr[-900:]


# ------------------------------------------------------- flags that do nothing
def test_save_says_so_when_there_is_nothing_to_save(scratch_path, monkeypatch):
    """`--cached-only --save` used to accept the flag and stay silent.

    Every row on that path came out of the corpus, so the ingest would
    re-offer rows that are already stored. That is a fine thing to skip, but it
    was skipped *silently*: the run printed a shard path and no ingest line, so
    a reader could not tell whether the save happened or was a no-op.

    The assertion is on the note, not on the write, because "changed nothing"
    is the correct behaviour here - the defect is the missing explanation.
    """
    import topic
    from core import paths

    monkeypatch.setattr(paths, "TOPIC_DIR", scratch_path)
    monkeypatch.setattr(sys, "argv", [
        "topic.py", "--topic", "固态电池", "--cached-only", "--save",
        "--limit", "1", "--no-shard",
    ])

    captured = {}

    def fake_emit(text, out_path=""):
        captured["text"] = text

    monkeypatch.setattr(topic, "emit", fake_emit)
    # No corpus on this machine is not a failure for this test: the note is
    # added before any row is read, and local_lookup must not be allowed to
    # reach the real 1.5 GB database.
    monkeypatch.setattr(topic, "local_lookup", lambda t, lim: ([], 0))

    assert topic.main() == 0
    text = captured.get("text", "")
    assert "--save" in text, "the run stayed silent about --save being a no-op"
