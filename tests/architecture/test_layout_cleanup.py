"""Directory ownership and artifact relocation must preserve working entry points."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("old,new", [
    ("periscope.collection.http", "periscope.collection.transport.client"),
    ("periscope.collection.parsing", "periscope.collection.transport.parsing"),
    ("periscope.collection.fanout", "periscope.collection.strategies.focused"),
    ("periscope.collection.broad", "periscope.collection.strategies.broad"),
    ("periscope.collection.batch", "periscope.collection.strategies.batch"),
    ("periscope.collection.browser_search", "periscope.collection.strategies.browser"),
    ("periscope.collection.crawler", "periscope.collection.crawling.engine"),
    ("periscope.collection.documents", "periscope.collection.crawling.documents"),
    ("periscope.collection.document_worker", "periscope.collection.crawling.worker"),
])
def test_old_collection_imports_share_the_canonical_module(old, new):
    assert importlib.import_module(old) is importlib.import_module(new)


def test_artifact_plan_is_read_only_and_preserves_unrelated_files(scratch_path):
    from periscope.operations.organize import plan_artifacts
    (scratch_path / "http_items.json").write_text("[]")
    (scratch_path / "personal.json").write_text("private")
    plans = plan_artifacts(scratch_path, scratch_path / "collect", scratch_path / "reports")
    assert len(plans) == 1
    assert Path(plans[0]["source"]).name == "http_items.json"
    assert not (scratch_path / "collect").exists()
    assert (scratch_path / "http_items.json").exists()


def test_artifact_relocation_never_overwrites_a_collision(scratch_path):
    from periscope.operations.organize import plan_artifacts, apply_artifacts
    collect, reports = scratch_path / "collect", scratch_path / "reports"
    collect.mkdir()
    (collect / "http_items.json").write_text('[{"old":true}]')
    (scratch_path / "http_items.json").write_text('[{"new":true}]')
    (scratch_path / ".benchmark.txt").write_text("measurement")
    plans = plan_artifacts(scratch_path, collect, reports)
    manifest = reports / "migration.json"
    apply_artifacts(plans, scratch_path, manifest)
    assert (collect / "http_items.json").read_text() == '[{"old":true}]'
    assert not (scratch_path / "http_items.json").exists()
    relocated = next(plan for plan in plans if plan["source"].endswith("http_items.json"))
    assert Path(relocated["target"]).read_text() == '[{"new":true}]'
    assert json.loads(manifest.read_text(encoding="utf-8"))["files"] == plans
    assert plan_artifacts(scratch_path, collect, reports) == []


def test_relocation_checks_every_target_before_moving(scratch_path):
    from periscope.operations.organize import apply_artifacts
    file = scratch_path / "http_items.json"
    file.write_text("[]")
    outside = scratch_path.parent / "outside.json"
    with pytest.raises(ValueError):
        apply_artifacts([{"source": str(file), "target": str(outside)}], scratch_path, scratch_path / "manifest.json")
    assert file.exists()


def test_existing_manifest_is_not_overwritten(scratch_path):
    from periscope.operations.organize import plan_artifacts, apply_artifacts
    source = scratch_path / "http_items.json"
    source.write_text("[]")
    manifest = scratch_path / "manifest.json"
    manifest.write_text("existing record")
    plans = plan_artifacts(scratch_path, scratch_path / "collect", scratch_path / "reports")
    with pytest.raises(ValueError):
        apply_artifacts(plans, scratch_path, manifest)
    assert manifest.read_text() == "existing record"
    assert source.exists()


def test_manifest_cannot_replace_an_artifact(scratch_path):
    from periscope.operations.organize import plan_artifacts, apply_artifacts
    source = scratch_path / "http_items.json"
    source.write_text("[]")
    plans = plan_artifacts(scratch_path, scratch_path / "collect", scratch_path / "reports")
    with pytest.raises(ValueError):
        apply_artifacts(plans, scratch_path, Path(plans[0]["target"]))
    assert source.exists()
    assert not Path(plans[0]["target"]).exists()


def test_platform_default_outputs_use_the_collection_directory():
    from periscope.runtime import paths
    for name in ("xhs", "douyin", "kuaishou"):
        module = importlib.import_module("periscope.collection.platforms." + name)
        assert module.OUT_DEFAULT.parent == paths.COLLECT_DIR


def test_full_ingestion_reads_all_collected_http_and_ugc_files(scratch_path, monkeypatch):
    from periscope.operations import ingest
    from src.corpus.store import Corpus
    collect = scratch_path / "collect"
    shards = scratch_path / "shards"
    collect.mkdir(); shards.mkdir()
    (collect / "http_items.json").write_text(json.dumps([{"source": "github_issues", "kind": "community",
        "url": "https://example.org/report", "title": "robot report", "text": "robot evidence " * 50}]))
    (collect / "ugc_items.json").write_text(json.dumps([{"platform": "xhs", "title": "robot note", "url": "https://example.org/note"}]))
    (shards / "ugc_search.douyin.json").write_text(json.dumps([{"platform": "douyin", "title": "robot clip", "url": "https://example.org/clip"}]))
    monkeypatch.setattr(ingest, "HTTP_ITEMS", scratch_path / "absent-http.json")
    monkeypatch.setattr(ingest, "UGC_ALL", scratch_path / "absent-all.json")
    monkeypatch.setattr(ingest, "UGC_ITEMS", scratch_path / "absent-ugc.json")
    monkeypatch.setattr(ingest, "ROOT", scratch_path)
    monkeypatch.setattr(ingest, "DATA_DIRS", [collect, shards])
    monkeypatch.setattr(ingest, "CORPUS", scratch_path / "corpus.db")
    monkeypatch.setattr(ingest, "fulltext_cache", lambda: scratch_path / "absent-cache.jsonl")
    monkeypatch.setattr(ingest.sys, "argv", ["ingest"])
    assert ingest.main() == 0
    corpus = Corpus(scratch_path / "corpus.db")
    assert corpus._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3
    corpus.close()


def test_batch_report_is_read_from_its_new_location(scratch_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from periscope.collection.strategies import batch
    collect, reports = scratch_path / 'collect', scratch_path / 'reports'
    collect.mkdir(); reports.mkdir()
    source = batch.collector.CATALOG[0]
    async def fixture_collect(client, picked):
        return [], SimpleNamespace(error='fixture-error')
    monkeypatch.setattr(batch.collector, 'collect', fixture_collect)
    monkeypatch.setattr(batch.paths, 'COLLECT_DIR', collect)
    monkeypatch.setattr(batch.paths, 'REPORT_DIR', reports)
    monkeypatch.setattr(batch, 'ROOT', scratch_path)
    monkeypatch.setattr(batch.sys, 'argv', ['batch', '--topic', 'robot', '--sources', source.key])
    assert batch.main() == 0
    assert 'fixture-error' in capsys.readouterr().out
    assert 'fixture-error' in (reports / '.collect_http.txt').read_text(encoding='utf-8')
    assert not (scratch_path / '.collect_http.txt').exists()


def test_focused_entry_writes_its_report_without_recreating_root_artifacts(scratch_path, monkeypatch):
    import os
    import runpy
    import sys
    from periscope.collection.transport import client
    from periscope.runtime import paths
    reports = scratch_path / 'reports'
    reports.mkdir()
    monkeypatch.setattr(client, 'CATALOG', [])
    monkeypatch.setattr(paths, 'ROOT', scratch_path)
    monkeypatch.setattr(paths, 'REPORT_DIR', reports)
    monkeypatch.setattr(sys, 'argv', ['focused', 'robot'])
    monkeypatch.setattr(os, 'write', lambda *args: None)
    monkeypatch.delitem(sys.modules, 'periscope.collection.strategies.focused', raising=False)
    runpy.run_module('periscope.collection.strategies.focused', run_name='__main__')
    assert 'robot' in (reports / '.fanout_out.txt').read_text(encoding='utf-8')
    assert not (scratch_path / '.fanout_out.txt').exists()


@pytest.mark.parametrize('filename', ['ugc_items.json', 'ugc_all.json'])
def test_migrated_ugc_collision_files_remain_ingestible(filename, scratch_path, monkeypatch):
    from periscope.operations import ingest
    from periscope.operations.organize import plan_artifacts, apply_artifacts
    from src.corpus.store import Corpus
    collect, reports = scratch_path / 'collect', scratch_path / 'reports'
    collect.mkdir()
    def row(suffix):
        return [{'platform': 'xhs', 'title': 'robot ' + suffix,
                 'url': 'https://example.org/' + suffix}]
    (collect / filename).write_text(json.dumps(row('current')))
    (scratch_path / filename).write_text(json.dumps(row('historical')))
    apply_artifacts(plan_artifacts(scratch_path, collect, reports), scratch_path, reports / 'migration.json')
    monkeypatch.setattr(ingest, 'HTTP_ITEMS', scratch_path / 'absent-http.json')
    monkeypatch.setattr(ingest, 'UGC_ALL', collect / 'ugc_all.json')
    monkeypatch.setattr(ingest, 'UGC_ITEMS', collect / 'ugc_items.json')
    monkeypatch.setattr(ingest, 'ROOT', scratch_path)
    monkeypatch.setattr(ingest, 'DATA_DIRS', [collect, scratch_path])
    monkeypatch.setattr(ingest, 'CORPUS', scratch_path / 'corpus.db')
    monkeypatch.setattr(ingest, 'fulltext_cache', lambda: scratch_path / 'absent-cache.jsonl')
    monkeypatch.setattr(ingest.sys, 'argv', ['ingest'])
    assert ingest.main() == 0
    corpus = Corpus(scratch_path / 'corpus.db')
    assert corpus._conn.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 2
    corpus.close()
