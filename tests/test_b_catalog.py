"""The source catalog's invariants.

These are the cheap checks that catch the common breakages: a duplicate key
(the dict comprehension in `fanout` would silently drop one), a topic source
that does not exist, a fast source that is not a topic source, or a browser
script path that moved. None of them touches the network.
"""

from __future__ import annotations

from pathlib import Path

import topic
from collectors import collector, fanout

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_keys_are_unique():
    keys = [s.key for s in collector.CATALOG]
    assert len(keys) == len(set(keys)), "duplicate catalog keys: %s" % (
        [k for k in keys if keys.count(k) > 1])


def test_catalog_is_non_trivial_and_has_query_sources():
    assert len(collector.CATALOG) >= 150
    query_capable = [s for s in collector.CATALOG if "{query}" in s.url or s.queries]
    assert len(query_capable) >= 20, "query-capable sources: %d" % len(query_capable)


def test_topic_sources_exist_and_are_query_capable():
    by_key = {s.key: s for s in collector.CATALOG}
    missing = [k for k in fanout.TOPIC_SOURCES if k not in by_key]
    assert not missing, "TOPIC_SOURCES not in CATALOG: %s" % missing
    not_query = [k for k in fanout.TOPIC_SOURCES
                 if "{query}" not in by_key[k].url and not by_key[k].queries]
    assert not not_query, "topic sources that cannot accept a query: %s" % not_query


def test_fast_sources_are_a_subset_of_topic_sources():
    assert set(topic.FAST_SOURCES) <= set(fanout.TOPIC_SOURCES)


def test_browser_scripts_exist():
    for key, (script, fname) in topic.BROWSER_SCRIPTS.items():
        assert (ROOT / script).is_file(), "%s script missing: %s" % (key, script)
        assert fname.endswith(".json"), "%s output is not JSON: %s" % (key, fname)
