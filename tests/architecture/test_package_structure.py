"""Migration contracts: old entry points and the installed package agree."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("old", "new"), [
    ("core.paths", "periscope.runtime.paths"),
    ("core.console", "periscope.runtime.console"),
    ("core.search", "periscope.search.local"),
    ("core.dsapi", "periscope.search.api"),
    ("core.merge", "periscope.search.merge"),
    ("core.runstore", "periscope.topics.runs"),
    ("collectors.collector", "periscope.collection.http"),
    ("collectors.fanout", "periscope.collection.fanout"),
    ("tools.ingest_all", "periscope.operations.ingest"),
    ("topic", "periscope.cli.topic"),
])
def test_compatibility_imports_share_state(old, new):
    # Separate module objects would make a path override affect only one entry.
    assert importlib.import_module(old) is importlib.import_module(new)


def test_source_definitions_are_available_without_runtime_side_effects(scratch_path):
    code = """
import json, socket, sys
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError('source definitions performed runtime I/O')
Path.mkdir = forbidden
socket.socket = forbidden
original_stdout = sys.stdout
from periscope.collection.sources import CATALOG, Source
assert sys.stdout is original_stdout
assert 'periscope.collection.http' not in sys.modules
assert 'periscope.runtime.paths' not in sys.modules
print(json.dumps({'count': len(CATALOG), 'unique': len({s.key for s in CATALOG}),
                  'first': CATALOG[0].key, 'last': CATALOG[-1].key}))
"""
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["count"] == got["unique"] == 179
    assert got["first"] == "arxiv_ai"
    from collectors.collector import CATALOG
    assert got["last"] == CATALOG[-1].key


def test_topic_service_returns_results_without_writing_a_run(scratch_path):
    from periscope.topics.models import TopicRequest
    from periscope.topics.service import TopicService

    row = {"title": "本地研究材料", "url": "https://example.com/source",
           "body": "材料" * 250, "trust": 0.7, "_origin": "local"}
    def local(topic, limit):
        assert topic == "研究问题"
        assert limit == 6
        return [row], 1

    service = TopicService(local_lookup=local)
    result = service.search(TopicRequest(topic="研究问题", limit=2, cached_only=True))
    assert result.local_total == 1
    assert result.merged[0]["url"] == row["url"]
    assert result.raw_rows == [row]
    assert not list(scratch_path.iterdir())


def test_topic_service_keeps_raw_rows_for_replay():
    from periscope.topics.models import TopicRequest
    from periscope.topics.service import TopicService

    api_row = {"title": "第一份原始资料", "url": "https://example.com/api",
               "authority": 4, "_origin": "api"}
    platform_row = {"title": "第二份原始资料", "url": "https://example.com/platform",
                    "source": "weibo_search", "_origin": "platform"}
    service = TopicService(
        local_lookup=lambda *a: ([], 0),
        api_lookup=lambda *a: ([api_row], 0.1, ""),
        platform_lookup=lambda *a: ([platform_row], {"elapsed": 0.2, "failed": []}),
        api_available=lambda: True,
    )
    result = service.search(TopicRequest(topic="研究问题"))
    assert result.raw_rows == [api_row, platform_row]
    assert {r["url"] for r in result.merged} == {api_row["url"], platform_row["url"]}
    assert "API 1 条" in result.info["breakdown"]


def test_installed_data_default_does_not_use_the_package_directory():
    from periscope.runtime.paths import default_data_dir

    windows = {"LOCALAPPDATA": "C:/Users/researcher/AppData/Local"}
    assert default_data_dir(workspace=None, environ=windows, platform="win32",
                            home=Path("C:/Users/researcher")) == Path(windows["LOCALAPPDATA"]) / "Periscope"
    assert default_data_dir(workspace=None, environ={}, platform="linux",
                            home=Path("/home/researcher")) == Path("/home/researcher/.local/share/periscope")


def test_subprocess_keeps_relative_path_overrides_anchored(scratch_path, monkeypatch):
    from periscope.runtime import paths

    monkeypatch.chdir(scratch_path)
    monkeypatch.setattr(paths, "DATA_DIR", Path("relative-data"))
    monkeypatch.setattr(paths, "SCRATCH", Path("relative-scratch"))
    monkeypatch.setattr(paths, "COOKIES", Path("relative-cookies"))
    monkeypatch.setenv("PERISCOPE_DATA_DIR", "relative-data")
    child_cwd = scratch_path / "another-working-directory"
    child_cwd.mkdir()
    code = "import json,os;print(json.dumps([os.environ[k] for k in ('PERISCOPE_DATA_DIR','PERISCOPE_SCRATCH_DIR','PERISCOPE_COOKIE_DIR')]))"
    proc = subprocess.run([sys.executable, "-c", code], cwd=child_cwd,
                          env=paths.subprocess_env(), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert [Path(p) for p in json.loads(proc.stdout)] == [
        scratch_path / "relative-data", scratch_path / "relative-scratch", scratch_path / "relative-cookies"]
    assert os.environ["PERISCOPE_DATA_DIR"] == "relative-data"


@pytest.mark.parametrize("module", ["periscope.cli.topic", "periscope.operations.ingest",
                                  "periscope.operations.backfill", "periscope.operations.persist"])
def test_canonical_entrypoints_offer_help_without_network(module, scratch_path):
    env = {**os.environ, "PERISCOPE_DATA_DIR": str(scratch_path),
           "PERISCOPE_SCRATCH_DIR": str(scratch_path), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-m", module, "--help"], cwd=ROOT,
                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "usage:" in proc.stdout.lower()
    assert not (scratch_path / "corpus.db").exists()


@pytest.mark.parametrize("platform", ["xhs", "douyin", "kuaishou"])
def test_browser_help_does_not_require_playwright_or_change_environment(platform, scratch_path):
    code = f"""
import os, runpy, sys
from periscope.runtime import paths
before = dict(os.environ)
sys.modules['playwright'] = None
sys.argv = ['platform', '--help']
try:
    runpy.run_module('periscope.collection.platforms.{platform}', run_name='__main__')
except SystemExit as exc:
    assert exc.code == 0
assert dict(os.environ) == before
"""
    env = {**os.environ, "PERISCOPE_DATA_DIR": str(scratch_path),
           "PERISCOPE_SCRATCH_DIR": str(scratch_path)}
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("platform", ["xhs", "douyin", "kuaishou"])
def test_browser_driver_is_initialized_when_collection_starts(platform, scratch_path, monkeypatch):
    module = importlib.import_module("periscope.collection.platforms." + platform)
    started = []
    class DriverReached(Exception):
        pass
    def driver():
        started.append("driver")
        raise DriverReached
    monkeypatch.setattr(module, "parse_args", lambda: SimpleNamespace(queries=["研究"], limit_queries=0))
    monkeypatch.setattr(module, "PROFILES", scratch_path)
    monkeypatch.setattr(module, "ensure_env", lambda: started.append("environment"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=driver))
    with pytest.raises(DriverReached):
        module.main()
    assert started == ["environment", "driver"]


def test_search_run_can_be_ingested_and_found_again(scratch_path):
    from periscope.topics.models import TopicRequest
    from periscope.topics.service import TopicService
    from periscope.topics import runs

    service = TopicService(local_lookup=lambda *a: ([], 0), api_available=lambda: True,
        api_lookup=lambda *a: ([{"title": "机器人研究材料与实验结果",
            "url": "https://example.com/research", "author": "Research Lab",
            "body": "机器人实验结果与研究方法。" * 60, "_origin": "api"}], 0.1, ""))
    result = service.search(TopicRequest(topic="机器人", speed="off"))
    # Materialization uses the same conversion as CLI --save and --ingest-last.
    payload = {"rows": [dict(row, _layer=row["_origin"]) for row in result.raw_rows]}
    file = runs.write_ingest_file(payload, directory=scratch_path)
    env = {**os.environ, "PERISCOPE_DATA_DIR": str(scratch_path),
           "PERISCOPE_SCRATCH_DIR": str(scratch_path), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-m", "periscope.operations.ingest", "--only", str(file)],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    code = "from periscope.topics.layers import local_lookup; r,n=local_lookup('机器人',3); assert any(x['url']=='https://example.com/research' for x in r),r"
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_cli_save_converts_a_run_to_ingestable_rows(scratch_path):
    code = """
from periscope.cli import topic
topic.local_lookup = lambda *a: ([], 0)
topic.dsapi.available = lambda: True
topic.api_lookup = lambda *a: ([{'title': '机器人保存测试材料',
    'url': 'https://example.com/saved-run', 'author': 'Test Lab',
    'body': '机器人研究与实验材料。' * 60, '_origin': 'api'}], 0.1, '')
assert topic.main(['--topic', '机器人', '--speed', 'off', '--save']) == 0
from src.corpus.store import Corpus
from periscope.runtime import paths
c = Corpus(paths.CORPUS)
assert c._conn.execute('SELECT count(*) FROM items WHERE url=?',
                      ('https://example.com/saved-run',)).fetchone()[0] == 1
c.close()
"""
    env = {**os.environ, "PERISCOPE_DATA_DIR": str(scratch_path),
           "PERISCOPE_SCRATCH_DIR": str(scratch_path), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
