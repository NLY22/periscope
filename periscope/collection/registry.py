"""One source inventory and an explicit, inspectable retrieval plan."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from .extensions import EXTRA_SOURCES
from .models import Source
from .sources import CATALOG


@dataclass(frozen=True)
class SourceEntry:
    source: Source
    mode: str

    @property
    def key(self) -> str:
        return self.source.key

    @property
    def kind(self) -> str:
        return self.source.kind

    def describe(self) -> dict:
        capabilities = [self.mode]
        if self.source.fmt == "rss" or self.source.text_field:
            capabilities.append("text")
        if self.source.metrics:
            capabilities.append("engagement")
        if self.source.cookies_file:
            capabilities.append("session_optional")
        limitations = {
            "feed": "仅当前订阅窗口，不能代表完整历史",
            "browser": "需要浏览器适配；不通过 HTTP 抓取",
            "snapshot": "固定数据快照，不能直接按任意话题检索",
            "page": "页面链接发现，结果需本地话题过滤",
        }
        return {"key": self.key, "name": self.source.name, "kind": self.kind,
                "mode": self.mode, "host": urlsplit(self.source.url).hostname or "",
                "capabilities": capabilities, "limitation": limitations.get(self.mode, "")}


@dataclass(frozen=True)
class SourcePlan:
    selected: tuple[SourceEntry, ...]
    skipped: tuple[SourceEntry, ...] = ()
    reasons: dict[str, str] = field(default_factory=dict)


def retrieval_mode(source: Source) -> str:
    if source.needs_browser:
        return "browser"
    if "{query}" in source.url:
        return "search"
    if source.fmt == "rss":
        return "feed"
    return "page" if source.fmt == "html" else "snapshot"


class SourceRegistry:
    """All configured sources; configuration never implies online health."""

    def __init__(self, extra: tuple[Source, ...] = (), disabled: tuple[str, ...] = ()):
        self._entries = {}
        for source in (*CATALOG, *EXTRA_SOURCES, *extra):
            if source.key in self._entries:
                raise ValueError("duplicate source: " + source.key)
            self._entries[source.key] = SourceEntry(source, retrieval_mode(source))
        unknown = set(disabled) - self._entries.keys()
        if unknown:
            raise ValueError("unknown disabled sources: " + ", ".join(sorted(unknown)))
        self.disabled = frozenset(disabled)

    @classmethod
    def from_file(cls, path: str | Path) -> SourceRegistry:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError("cannot read source configuration: " + str(exc)) from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("sources", []), list):
            raise ValueError("source configuration must contain a sources list")
        allowed = {field.name for field in fields(Source)}
        definitions = []
        for definition in raw.get("sources", []):
            if not isinstance(definition, dict) or set(definition) - allowed:
                raise ValueError("invalid source fields")
            for required in ("key", "name", "url", "kind"):
                if not isinstance(definition.get(required), str) or not definition[required].strip():
                    raise ValueError("source requires " + required)
            if not re.fullmatch(r"[a-z][a-z0-9_]*", definition["key"]):
                raise ValueError("invalid source key")
            parsed = urlsplit(definition["url"])
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("source URL must be HTTP(S) without embedded credentials")
            if definition.get("fmt", "json") not in ("json", "rss", "html"):
                raise ValueError("unsupported source format")
            from .families import KIND_TO_SOURCE
            if definition["kind"] not in KIND_TO_SOURCE:
                raise ValueError("unsupported source kind: " + definition["kind"])
            definition = dict(definition)
            for descriptor in fields(Source):
                if descriptor.name not in definition:
                    continue
                value = definition[descriptor.name]
                default = descriptor.default
                if isinstance(default, bool):
                    valid = isinstance(value, bool)
                elif isinstance(default, int):
                    minimum = 1 if descriptor.name in ("page_step", "per_page", "max_pages") else 0
                    valid = type(value) is int and value >= minimum
                elif isinstance(default, float):
                    valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
                elif isinstance(default, str):
                    valid = isinstance(value, str)
                elif isinstance(default, tuple):
                    valid = isinstance(value, list)
                    if valid and descriptor.name == "headers":
                        valid = all(isinstance(pair, list) and len(pair) == 2 and all(isinstance(v, str) for v in pair) for pair in value)
                        if valid:
                            value = [tuple(pair) for pair in value]
                    elif valid:
                        valid = all(isinstance(v, str) for v in value)
                    if valid:
                        definition[descriptor.name] = tuple(value)
                else:
                    valid = True
                if not valid:
                    raise ValueError("invalid source setting: " + descriptor.name)
            try:
                definitions.append(Source(**definition))
            except TypeError as exc:
                raise ValueError("invalid source definition") from exc
        disabled = raw.get("disabled_sources", [])
        if not isinstance(disabled, list) or any(not isinstance(key, str) for key in disabled):
            raise ValueError("disabled_sources must be a list of source keys")
        return cls(tuple(definitions), tuple(disabled))

    def entries(self) -> tuple[SourceEntry, ...]:
        return tuple(self._entries.values())

    def get(self, key: str) -> SourceEntry:
        try:
            return self._entries[key]
        except KeyError as exc:
            raise ValueError("unknown source: " + key) from exc

    def plan(self, coverage: str = "broad", *, sources: tuple[str, ...] = (),
             kinds: tuple[str, ...] = ()) -> SourcePlan:
        if coverage not in ("broad", "focused"):
            raise ValueError("unknown coverage mode: " + coverage)
        known_kinds = {entry.kind for entry in self.entries()}
        if set(kinds) - known_kinds:
            raise ValueError("unknown source kinds: " + ", ".join(sorted(set(kinds) - known_kinds)))
        if sources:
            candidates = [self.get(key) for key in dict.fromkeys(sources)]
        elif coverage == "focused":
            from .strategies.focused import TOPIC_SOURCES
            candidates = [self.get(key) for key in TOPIC_SOURCES]
        else:
            candidates = list(self.entries())
        selected, skipped, reasons = [], [], {}
        # These recipes repeat another search endpoint or impose old time bounds.
        recipe_aliases = {"hn_cn", "hn_ai_monthly"}
        for entry in candidates:
            if kinds and entry.kind not in kinds:
                continue
            eligible = entry.mode in ("search", "feed") or bool(sources) and entry.mode == "page"
            if entry.key in self.disabled or not eligible or (not sources and entry.key in recipe_aliases):
                skipped.append(entry)
                reasons[entry.key] = ("disabled" if entry.key in self.disabled else "duplicate_search_recipe" if entry.key in recipe_aliases
                                      else entry.describe()["limitation"] or "not_topic_searchable")
            else:
                selected.append(entry)
        # Start genuine query endpoints before bounded latest-feed checks.
        selected.sort(key=lambda entry: entry.mode != "search")
        return SourcePlan(tuple(selected), tuple(skipped), reasons)
