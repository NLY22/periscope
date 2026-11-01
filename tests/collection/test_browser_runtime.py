"""Browser work should wait for results and preserve completed batches on exit."""
import json
from pathlib import Path
import subprocess
from contextlib import contextmanager
import importlib
import sys
from types import SimpleNamespace

import pytest


def test_ready_results_do_not_sleep_for_the_full_timeout(monkeypatch):
    from periscope.collection.platforms import browser
    clock = [0.0]
    waits = []
    monkeypatch.setattr(browser, "monotonic", lambda: clock[0], raising=False)
    class Page:
        def wait_for_timeout(self, milliseconds):
            waits.append(milliseconds)
            clock[0] += milliseconds / 1000
    assert browser.wait_for_items(Page(), lambda: int(clock[0] >= .2), previous=0, timeout_ms=8000)
    assert sum(waits) <= 300


def test_existing_results_return_immediately_and_empty_results_are_bounded(monkeypatch):
    from periscope.collection.platforms import browser
    clock = [0.0]
    waits = []
    monkeypatch.setattr(browser, "monotonic", lambda: clock[0], raising=False)
    class Page:
        def wait_for_timeout(self, milliseconds):
            waits.append(milliseconds)
            clock[0] += milliseconds / 1000
    assert browser.wait_for_items(Page(), lambda: 1, previous=0, timeout_ms=8000)
    assert not waits
    assert not browser.wait_for_items(Page(), lambda: 0, previous=0, timeout_ms=350)
    assert sum(waits) == pytest.approx(350, abs=1)


def test_atomic_checkpoints_keep_the_last_complete_batch(scratch_path, monkeypatch):
    from periscope.collection.platforms import browser
    out = scratch_path / "nested" / "results.json"
    browser.write_checkpoint(out, [{"title": "First complete batch"}])
    old = out.read_text(encoding="utf-8")
    def fail(*args, **kwargs):
        raise OSError("simulated disk full")
    monkeypatch.setattr(browser.json, "dump", fail)
    with pytest.raises(OSError, match="disk full"):
        browser.write_checkpoint(out, [{"title": "Incomplete new batch"}])
    assert out.read_text(encoding="utf-8") == old
    assert not list(out.parent.glob("*.tmp"))


def test_checkpoint_replacement_tolerates_a_transient_windows_file_lock(scratch_path, monkeypatch):
    from periscope.collection.platforms import browser
    out = scratch_path / "results.json"
    browser.write_checkpoint(out, [{"title": "First batch"}])
    replace = browser.os.replace
    attempts = []
    def briefly_locked(source, target):
        attempts.append(1)
        if len(attempts) == 1:
            error = PermissionError("temporary sharing lock")
            error.winerror = 32
            raise error
        return replace(source, target)
    monkeypatch.setattr(browser.os, "replace", briefly_locked)
    browser.write_checkpoint(out, [{"title": "Second completed batch"}])
    assert json.loads(out.read_text(encoding="utf-8"))[0]["title"] == "Second completed batch"


def test_busy_platform_profile_is_not_unlocked_or_launched_twice(scratch_path):
    from periscope.collection.platforms import browser
    profile = scratch_path / "profile"
    with browser.profile_lease(profile):
        with pytest.raises(browser.BrowserProfileBusy):
            with browser.profile_lease(profile):
                pytest.fail("A second worker acquired the same profile")
    with browser.profile_lease(profile):
        pass


@pytest.mark.parametrize("exit_kind", ["timeout", "failure"])
def test_strategy_recovers_only_valid_checkpoint_rows_after_interruption(scratch_path, monkeypatch, exit_kind):
    from periscope.collection.strategies import browser
    monkeypatch.setattr(browser.paths, "SHARD_DIR", scratch_path)
    monkeypatch.setattr(browser.importlib.util, "find_spec", lambda _: object())
    valid = {"title": "Completed result", "url": "https://example.org/result", "text": "Already captured"}
    def run(cmd, **kwargs):
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_text(json.dumps([valid, None, {"url": "javascript:alert(1)"}]), encoding="utf-8")
        if exit_kind == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(browser, "run_worker", run)
    rows, reports = browser.collect("topic", {"xhs": ("unused.py", "shared.json")}, {"xhs": "unused"},
                                    started=browser.time.monotonic(), budget=5)
    assert len(rows) == 1 and rows[0]["title"] == valid["title"]
    assert reports[0]["status"] == "partial" and reports[0]["rows"] == 1
    assert reports[0]["error"] == ("browser_deadline" if exit_kind == "timeout" else "exit_1")
    assert rows[0]["provenance"]["content_role"] == "community"


def test_strategy_uses_unique_outputs_and_reports_busy_profile(scratch_path, monkeypatch):
    from periscope.collection.strategies import browser
    monkeypatch.setattr(browser.paths, "SHARD_DIR", scratch_path)
    monkeypatch.setattr(browser.importlib.util, "find_spec", lambda _: object())
    outputs = []
    def run(cmd, **kwargs):
        outputs.append(cmd[cmd.index("--out") + 1])
        return SimpleNamespace(returncode=3)
    monkeypatch.setattr(browser, "run_worker", run)
    for _ in range(2):
        rows, reports = browser.collect("topic", {"xhs": ("unused.py", "shared.json")}, {"xhs": "unused"},
                                        started=browser.time.monotonic(), budget=5)
        assert not rows and reports[0]["error"] == "profile_busy"
    assert len(set(outputs)) == 2


def test_strategy_reports_platform_captcha_as_a_source_failure(scratch_path, monkeypatch):
    from periscope.collection.strategies import browser
    monkeypatch.setattr(browser.paths, "SHARD_DIR", scratch_path)
    monkeypatch.setattr(browser.importlib.util, "find_spec", lambda _: object())
    def run(cmd, **kwargs):
        Path(cmd[cmd.index("--out") + 1]).write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=4)
    monkeypatch.setattr(browser, "run_worker", run)

    rows, reports = browser.collect("topic", {"kuaishou": ("unused.py", "shared.json")},
                                    {"kuaishou": "unused"},
                                    started=browser.time.monotonic(), budget=5)

    assert not rows
    assert reports[0]["status"] == "failed"
    assert reports[0]["error"] == "captcha_required"


def test_corrupt_or_empty_timeout_checkpoint_does_not_invent_results(scratch_path, monkeypatch):
    from periscope.collection.strategies import browser
    monkeypatch.setattr(browser.paths, "SHARD_DIR", scratch_path)
    monkeypatch.setattr(browser.importlib.util, "find_spec", lambda _: object())
    def run(cmd, **kwargs):
        Path(cmd[cmd.index("--out") + 1]).write_text('{"incomplete":', encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    monkeypatch.setattr(browser, "run_worker", run)
    rows, reports = browser.collect("topic", {"xhs": ("unused.py", "shared.json")}, {"xhs": "unused"},
                                    started=browser.time.monotonic(), budget=5)
    assert not rows and reports[0]["status"] == "over_budget"


@pytest.mark.parametrize("platform", ["xhs", "douyin", "kuaishou"])
def test_worker_collects_initial_results_without_a_mandatory_sleep_or_scroll(scratch_path, monkeypatch, platform):
    """Drive the real CLI worker with a page that already returned its results."""
    waits = []
    payload = ({"data": [{"aweme_info": {"aweme_id": "123", "desc": "Observed video description"}}]}
               if platform == "douyin" else {"feeds": [{"photo": {"id": "123", "caption": "Observed caption"}}]})
    class Page:
        mouse = SimpleNamespace(wheel=lambda *args: None)
        def on(self, event, callback):
            self.response = callback
        def goto(self, url, **kwargs):
            if platform != "xhs":
                response_url = "https://example.org/aweme/v1/web/search/item/" if platform == "douyin" else "https://example.org/search/feed"
                self.response(SimpleNamespace(url=response_url, headers={"content-type": "application/json"}, json=lambda: payload))
        def wait_for_timeout(self, milliseconds):
            waits.append(milliseconds)
        def wait_for_function(self, *args, **kwargs):
            return SimpleNamespace(dispose=lambda: None)
        def evaluate(self, expression):
            return [{"note_id": "123", "lines": ["Observed note title", "Creator", "20"],
                     "href": "https://www.xiaohongshu.com/explore/123", "raw": "Observed note title"}]
    context = SimpleNamespace(pages=[Page()], close=lambda: None)
    class Driver:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda **kwargs: context))
        def __exit__(self, *args):
            return False
    monkeypatch.setitem(sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=Driver, TimeoutError=TimeoutError))
    worker = importlib.import_module("periscope.collection.platforms." + platform)
    @contextmanager
    def session(*args, **kwargs):
        yield context
    monkeypatch.setattr(worker, "browser_session", session, raising=False)
    monkeypatch.setattr(worker, "browser_channel", lambda: None, raising=False)
    monkeypatch.setattr(worker, "ensure_env", lambda: None, raising=False)
    monkeypatch.setattr(worker, "PROFILES", scratch_path / "profiles")
    out = scratch_path / "results.json"
    monkeypatch.setattr(sys, "argv", ["collector", "--queries", "topic", "--scrolls", "0", "--out", str(out)])
    assert worker.main() == 0
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 1
    assert not waits, "Already available results should not pay a fixed 6–8 second delay"
