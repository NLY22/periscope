# P0：结构化分层 + 6 个 emitter 迁移 + 源注册表 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"作者层 / 人群层"的判据从字符串标记约定改成 `ContentItem.sections` 类型化字段，并把仓库里现存的全部 6 个 emitter 迁过去，从而堵住三条**当前正在泄漏**的通路（HN / Reddit / Twitter 的评论正被判为作者亲写并进入 `claimable`）。

**Architecture:** 三层改动。① `models.py` 增加 `Section` 与 `ContentItem.{locator,time_basis,sections}`，归一化在 `model_validator(mode="after")` 里一次做完，构造后 `published_at` 永不为 `None`。② `corpus/sections.py` 保留旧的标记反解路径（改名 `MarkerSection`）作为 legacy 与消融 A 档，新增 `claimable_of(item)` 作为唯一分派入口；`corpus/store.py` 升到 schema v3，新增 `locator`/`time_basis`/`sections_json` 三列并对老库 backfill。③ 源元数据收敛为 `models.py` 的 `SOURCE_SPECS`（`SOURCE_REGISTRY` 由它派生），scraper 绑定放新模块 `src/sources/registry.py`（避免循环导入），`orchestrator.fetch_all_sources` 的 14 段硬编码 `if` 换成遍历注册表。

**Tech Stack:** Python ≥3.11（开发环境 3.12 / uv）、pydantic v2、httpx、SQLite + FTS5、BeautifulSoup4、pytest。

**Spec:** `docs/superpowers/specs/2026-09-29-broad-source-credibility-and-multiturn-research-design.md`（v3）。本计划实现其 §3（P0）全部内容，并遵循 §9 的逻辑接缝自查与 §10 的验收判据。执行者应同时读 spec 的 §1.1、§3、§9、§14。

## Global Constraints

- **不新增运行时依赖。** `pyproject.toml` 的 `dependencies` 保持原样；`beautifulsoup4>=4.12.0` 已在其中，HTML 解析只用它 + `html.parser`（不引入 `lxml`/`selectolax`）。
- **`src/models.py` 只准 import stdlib + pydantic。** 它绝不能 import `src.corpus.*` 或 `src.scrapers.*`：`src/corpus/__init__.py` 会 `from .store import Corpus`，而 `store.py` import `models` —— 一旦 `models` 反向 import `corpus.sections`，就是 `models → corpus/__init__ → corpus.store → models` 的死循环。因此 `sections_to_content()` 必须写在 `models.py` 里，不能写在 `corpus/sections.py` 里。
- **反向可以：** `corpus/sections.py` 允许 `from ..models import ContentItem, Section`（models 不依赖它）。
- **不改数据库的 NOT NULL 约束。** `items.url`（`store.py:54`）与 `items.published_at`（`:56`）保持 `NOT NULL`；`idx_items_published`（`:66`）保持有效。
- **不改 `data/config.json` 的格式。** `SourcesConfig`（`models.py:614-630`）仍是每源一个强类型字段。
- **不删 `split_sections`。** 它是老库 backfill 与 P1 消融 A 档（`--tiering=marker`）的唯一实现。
- **不动 `_deduplication_url_key` 的七元组语义。** `tests/test_cross_source_duplicates.py:36-72` 钉住了它。
- **测试里禁止挂钟断言。** 本仓库已经为此返工过一次（commit `c79cfcf` "Test: make the LLM throttle assertion deterministic instead of wall-clock"）。所有涉及时间的测试必须注入 clock / sleeper / random。
- **测试基线：697 collected**（2026-09-29 实测 `uv run pytest --collect-only` → `697 tests collected in 1.81s`）。每个 Task 结束时全量必须绿，且 collected 数只增不减。
- **命令一律用 `uv run`**（venv 是 `uv venv --python 3.12` + `uv sync --extra dev`）。
- **提交粒度：每个 Task 一个 commit**，消息用仓库现有风格（`Feat:` / `Fix:` / `Test:` / `Docs:` 首字母大写 + 祈使句）。

## File Structure

**新建**

| 路径 | 职责 |
|---|---|
| `src/sources/__init__.py` | 只有 docstring。**不得** re-export `registry`，否则 `import src.sources` 会连带拉起全部 scraper |
| `src/sources/registry.py` | `BuildContext`、`ScraperFactory`、`simple()`、`SCRAPER_BINDINGS`、`is_enabled()`、`build_throttle()`。可以 import models 与 scrapers 两边 |
| `src/scrapers/throttle.py` | per-host 令牌桶 + 抖动 + `429/Retry-After` 重试一次；clock / sleeper / rng 全部可注入 |
| `src/scrapers/auth.py` | 两种 provider：env token、cookie 文件（含过期检测与失效重试一次） |
| `tests/test_section_model.py` | Task 1：Section / ContentItem 归一化 |
| `tests/test_claimable_dispatch.py` | Task 2：`claimable_of` 的 sections 路径与 legacy 路径 |
| `tests/test_corpus_v3_migration.py` | Task 3：schema v3 迁移与写入侧回填 |
| `tests/test_locator_dedup.py` | Task 4：locator 去重键与跨源合并 |
| `tests/test_source_registry.py` | Task 5 + Task 9：注册表 ↔ enum ↔ `SourcesConfig` ↔ bindings 一致性 |
| `tests/test_throttle.py` | Task 6（+ Task 8 追加 BaseScraper 黏合） |
| `tests/test_scraper_auth.py` | Task 7 |
| `tests/test_tier_guard.py` | Task 10–12：P0 的核心验收（泄漏必须先红后绿）+ 无标记守护 |

**修改**

| 路径 | 改什么 |
|---|---|
| `src/models.py` | `Section`、`sections_to_content`、`ContentItem` 四字段 + after-validator + `citation_url` + `rebuild_content`；`RateLimit`、`SourceSpec`、`SOURCE_SPECS`；`SOURCE_REGISTRY` 改为派生 |
| `src/corpus/sections.py` | dataclass `Section` → `MarkerSection`；新增 `marker_sections_to_model`、`claimable_from_sections`、`claimable_of` |
| `src/corpus/store.py` | `SCHEMA_VERSION 3`；三列 + 迁移 backfill；`add_items` 写 `locator`/`time_basis`/`sections_json`、`claimable` 改用 `claimable_of`；新增 `known_ids` |
| `src/analysis/claims.py` | `_author_text(content)` → `_author_text(item)`（`:624-631`、调用点 `:443`）；`:448` 用 `citation_url` |
| `src/orchestrator.py` | `_deduplication_item_key`；合并时同步 `sections`；14 段 `if` → 注册表循环；`follow_redirects=True`；`persist_to_corpus` 返回新 id；`analyze_claims` 跳过已见 id；`:1351-1352` 跟随 twitter 新签名 |
| `src/processing/content.py` | 新增 `split_item_content(item)`；`split_content` 保留 |
| `src/ai/analyzer.py:109`、`src/ai/prompting/enrichment.py:155` | 改调 `split_item_content(item)` |
| `src/ai/prompting/analysis.py:43`、`classification.py:38`、`enrichment.py:169`、`processing/tools.py:78`、`services/webhook.py:552` | `str(item.url)` / `{item.url}` → `item.citation_url` |
| `src/scrapers/base.py` | `__init__` 可选注入 `throttle` / `auth`，新增 `_request` |
| `src/scrapers/hackernews.py:100-144` | 迁移到 sections |
| `src/scrapers/reddit.py:488-507`、`:531-552` | 迁移到 sections；429 改走 throttle |
| `src/scrapers/twitter.py:168-266` | `_extract_reply_lines` → `_extract_reply_sections`；`append_discussion_content` → `append_discussion_sections` |
| `src/scrapers/discourse.py:142-155` | 迁移到 sections |
| `src/scrapers/v2ex.py:105-112` | 迁移到 sections |
| `src/scrapers/bilibili.py:101-117` | 迁移到 sections（字幕 `provenance="transcript"`） |
| `src/scrapers/telegram.py:65-71` | 429 改走 throttle |
| `tests/test_evidence_tiers.py:198` | `_author_text(item.content)` → `_author_text(item)` |
| `tests/test_twitter.py:516-610` | 三处断言从"标记在 content 里"改为"reply 落在 community section" |

---

### Task 1: `Section` 模型与 `ContentItem` 的 locator / time_basis / sections

**Files:**
- Modify: `src/models.py:7`（import）、`:111-126`（`ContentItem`），并在 `ContentItem` 之前插入 `Section` 与 `sections_to_content`
- Modify: `src/ai/prompting/analysis.py:43`、`src/ai/prompting/classification.py:38`、`src/ai/prompting/enrichment.py:169`、`src/analysis/claims.py:448`、`src/processing/tools.py:78`、`src/services/webhook.py:552`
- Test: `tests/test_section_model.py`

**Interfaces:**
- Consumes: 无（P0 的第一个 Task）
- Produces:
  - `TimeBasis = Literal["published", "crawled", "unknown"]`
  - `SectionProvenance = Literal["author", "transcript", "ocr", "vlm", "legacy_marker"]`
  - `class Section(BaseModel)`，字段：`tier: Literal["primary","community"]`、`text: str`、`author: Optional[str] = None`、`provenance: SectionProvenance = "author"`、`asserted: bool = True`、`confidence: Optional[float] = None`（`ge=0, le=1`）、`locator: Optional[str] = None`、`meta: Dict[str, Any] = {}`
  - `def sections_to_content(sections: List[Section]) -> str`
  - `ContentItem.locator: str`、`.time_basis: TimeBasis`、`.sections: List[Section]`、`.url: Optional[HttpUrl]`、`.published_at: Optional[datetime]`
  - `ContentItem.citation_url -> str`（property）、`ContentItem.rebuild_content() -> None`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_section_model.py`：

```python
"""ContentItem carries typed sections; identity and time are normalised once."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.models import ContentItem, Section, SourceType, sections_to_content

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def make(**overrides):
    base = dict(
        id="t:1",
        source_type=SourceType.DISCOURSE,
        title="Why does my build fail",
        url="https://forum.test/t/7",
        published_at=NOW,
        fetched_at=NOW,
    )
    base.update(overrides)
    return ContentItem(**base)


def test_sections_default_to_empty_and_content_is_untouched() -> None:
    item = make(content="author body")
    assert item.sections == []
    assert item.content == "author body"


def test_locator_defaults_to_the_url() -> None:
    assert make().locator == "https://forum.test/t/7"


def test_locator_may_be_a_non_url_string() -> None:
    item = make(url=None, locator="tieba:p/123")
    assert item.locator == "tieba:p/123"
    assert item.url is None


def test_item_with_neither_url_nor_locator_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make(url=None)


def test_content_is_recomputed_from_sections() -> None:
    item = make(sections=[
        Section(tier="primary", text="author body"),
        Section(tier="community", text="borrow conflict", author="helper", locator="#2"),
    ])
    assert item.content == "author body\n\n- @helper: borrow conflict"


def test_sections_without_author_render_bare_text() -> None:
    assert sections_to_content([Section(tier="community", text="anon reply")]) == "anon reply"


def test_blank_sections_are_dropped_from_content() -> None:
    assert sections_to_content([Section(tier="primary", text="  ")]) == ""


def test_missing_published_at_falls_back_to_fetch_time_and_flags_time_basis() -> None:
    item = make(published_at=None, fetched_at=NOW)
    assert item.published_at == NOW
    assert item.time_basis == "unknown"


def test_explicit_time_basis_is_preserved() -> None:
    assert make(published_at=None, time_basis="crawled").time_basis == "crawled"


def test_citation_url_prefers_url_then_locator() -> None:
    assert make().citation_url == "https://forum.test/t/7"
    assert make(url=None, locator="xhs:note:abc").citation_url == "xhs:note:abc"


def test_rebuild_content_picks_up_in_place_section_changes() -> None:
    item = make(sections=[Section(tier="primary", text="one")])
    item.sections.append(Section(tier="community", text="two", author="b"))
    assert item.content == "one"  # plain assignment does not revalidate
    item.rebuild_content()
    assert item.content == "one\n\n- @b: two"


def test_extra_fields_are_still_forbidden() -> None:
    with pytest.raises(ValidationError):
        make(nonsense=1)


def test_section_rejects_an_unknown_tier() -> None:
    with pytest.raises(ValidationError):
        Section(tier="crowd", text="x")
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_section_model.py -v`
Expected: FAIL，`ImportError: cannot import name 'Section' from 'src.models'`

- [ ] **Step 3: 在 `src/models.py` 里实现**

import 行（`:7`）补 `model_validator`：

```python
from pydantic import BaseModel, ConfigDict, HttpUrl, Field, field_validator, model_validator
```

在 `class ContentItem` 之前插入：

```python
TimeBasis = Literal["published", "crawled", "unknown"]
SectionProvenance = Literal["author", "transcript", "ocr", "vlm", "legacy_marker"]


class Section(BaseModel):
    """One authored block of an item body, tagged with its authorship tier.

    Tiering used to be recovered by scanning `content` for five literal
    Chinese marker strings. That silently promoted crowd text to
    author-written whenever a scraper used any other separator, so the tier
    now travels with the text instead of being inferred from it.
    """

    model_config = ConfigDict(extra="forbid")

    tier: Literal["primary", "community"]
    text: str
    author: Optional[str] = None
    provenance: SectionProvenance = "author"
    asserted: bool = True
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    locator: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


def sections_to_content(sections: List[Section]) -> str:
    """Flatten sections into the legacy single-string body.

    Community blocks keep an `@author` prefix: `items_fts` indexes this
    string and the panel renders it, so dropping attribution would make a
    stranger's reply read like the author's own words.
    """
    parts: List[str] = []
    for section in sections:
        text = section.text.strip()
        if not text:
            continue
        if section.tier == "community" and section.author:
            parts.append(f"- @{section.author}: {text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)
```

把 `ContentItem`（`:111-126`）整体替换为：

```python
class ContentItem(BaseModel):
    """Unified content item model from any source."""

    model_config = ConfigDict(extra="forbid")

    id: str  # Format: {source}:{subtype}:{native_id}
    source_type: SourceType
    title: str
    url: Optional[HttpUrl] = None  # display only; `locator` is the identity
    locator: str = ""  # stable id: a URL, or "tieba:p/123", "xhs:note:abc"
    content: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    time_basis: TimeBasis = "published"
    sections: List[Section] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    profile: ProfileRoute = None
    processing: Optional[ProcessingResult] = None

    @model_validator(mode="after")
    def _normalise_identity_and_time(self) -> "ContentItem":
        """Fill the two fields the storage schema cannot make optional.

        `items.url` and `items.published_at` are NOT NULL in SQLite, and
        `idx_items_published` plus every time-ordered query depends on that.
        Normalising here means no consumer ever sees None, and a source with
        no canonical URL or no publish time stops being silently dropped.
        """
        if not self.locator:
            if self.url is None:
                raise ValueError("ContentItem needs either url or locator")
            self.locator = str(self.url)
        if self.published_at is None:
            self.published_at = self.fetched_at
            self.time_basis = "unknown"
        self.rebuild_content()
        return self

    def rebuild_content(self) -> None:
        """Re-derive `content` after `sections` was mutated in place."""
        if self.sections:
            self.content = sections_to_content(self.sections)

    @property
    def citation_url(self) -> str:
        """Best available link for prompts, reports and webhook payloads."""
        return str(self.url) if self.url is not None else self.locator
```

- [ ] **Step 4: 把 6 处会把字面量 `"None"` 写进提示词/载荷的地方换成 `citation_url`**

`src/ai/prompting/analysis.py:43`：

```python
URL: {item.citation_url}
```

`src/ai/prompting/classification.py:38`：

```python
URL: {item.citation_url}
```

`src/ai/prompting/enrichment.py:169`：

```python
URL: {item.citation_url}
```

`src/analysis/claims.py:448`（`extract_claims` 里拼 user prompt 的那行）：

```python
            f"来源: {item.source_type.value} ({item.citation_url})\n"
```

`src/processing/tools.py:78`：

```python
                exclude_url=current_item.citation_url,
```

`src/services/webhook.py:552`：

```python
                            "item_url": view_item.item.citation_url,
```

`src/ai/summarizer.py:312` 与 `:365` **不用改**：`_safe_url(None)`（`:27-37`）走 `str(None)` → `urlsplit("None").scheme == ""` → 返回 `None` → 标题不渲染成链接，是优雅降级。

- [ ] **Step 5: 跑测试，确认通过**

Run: `uv run pytest tests/test_section_model.py -v`
Expected: PASS，13 passed

- [ ] **Step 6: 跑全量，确认没有回归**

Run: `uv run pytest -q`
Expected: 全绿，collected = 697 + 13 = 710。

本 Task 不该有回归：已用 AST 扫过全仓，`ContentItem(...)` 的构造点**没有一处**省略 `url=`（`src/` + `tests/` + `scripts/` 共 25 个文件，0 处缺失），所以把 `url` 变 Optional 不会让任何现有构造失败。`horizon_adapter.py:240` 的 `ContentItem.model_validate(payload)` 也不受影响——payload 来自 `model_dump(mode="json")`，新字段会随往返一起带上。

- [ ] **Step 7: 提交**

```bash
git add src/models.py src/ai/prompting/analysis.py src/ai/prompting/classification.py \
        src/ai/prompting/enrichment.py src/analysis/claims.py src/processing/tools.py \
        src/services/webhook.py tests/test_section_model.py
git commit -m "Feat: give ContentItem typed sections, a locator and a time basis"
```

---

### Task 2: `claimable_of(item)` —— 唯一的分层分派入口

**Files:**
- Modify: `src/corpus/sections.py:22-26`（import）、`:47-53`（dataclass 改名）、`:70`/`:79`（构造名）、文件末尾追加三个函数
- Modify: `src/analysis/claims.py:37`（import）、`:443`（调用点）、`:624-631`（`_author_text`）
- Modify: `tests/test_evidence_tiers.py:198`
- Test: `tests/test_claimable_dispatch.py`

**Interfaces:**
- Consumes: `Section`、`ContentItem`（Task 1）
- Produces:
  - `class MarkerSection`（原 `Section` dataclass 改名；字段 `tier: str`、`marker: str | None`、`text: str` 不变）
  - `def split_sections(content: str | None) -> List[MarkerSection]`（签名不变，只是返回类型换了名字）
  - `def marker_sections_to_model(content: str | None) -> List[Section]`
  - `def claimable_from_sections(sections: Iterable[Section]) -> str`
  - `def claimable_of(item: ContentItem) -> str`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_claimable_dispatch.py`：

```python
"""One entry point decides what is author-written, whichever path produced it."""

from datetime import datetime, timezone

from src.corpus.sections import (
    MarkerSection,
    claimable_from_sections,
    claimable_of,
    marker_sections_to_model,
    split_sections,
)
from src.models import ContentItem, Section, SourceType

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def make(content=None, sections=None) -> ContentItem:
    return ContentItem(
        id="d:1",
        source_type=SourceType.V2EX,
        title="t",
        url="https://e.com/1",
        content=content,
        sections=sections or [],
        published_at=NOW,
        fetched_at=NOW,
    )


def test_sections_win_over_the_marker_path() -> None:
    item = make(sections=[
        Section(tier="primary", text="the author says X"),
        Section(tier="community", text="a stranger says Y", author="s"),
    ])
    assert claimable_of(item) == "the author says X"


def test_items_without_sections_fall_back_to_markers() -> None:
    item = make(content="author body\n\n【评论区 Top】\ncrowd body")
    assert claimable_of(item) == "author body"


def test_unasserted_primary_sections_are_not_claimable() -> None:
    item = make(sections=[
        Section(tier="primary", text="caption of an image", provenance="vlm", asserted=False),
        Section(tier="primary", text="the author's own words"),
    ])
    assert claimable_of(item) == "the author's own words"


def test_claimable_from_sections_joins_in_document_order() -> None:
    assert claimable_from_sections([
        Section(tier="community", text="c"),
        Section(tier="primary", text="p1"),
        Section(tier="primary", text="p2"),
    ]) == "p1\n\np2"


def test_marker_conversion_tags_tiers_and_flags_provenance() -> None:
    converted = marker_sections_to_model("author body\n\n【评论区 Top】\ncrowd body")
    assert [(s.tier, s.provenance) for s in converted] == [
        ("primary", "legacy_marker"),
        ("community", "legacy_marker"),
    ]
    assert converted[1].locator == "【评论区 Top】"


def test_marker_conversion_of_empty_content_is_empty() -> None:
    assert marker_sections_to_model(None) == []
    assert marker_sections_to_model("   ") == []


def test_legacy_dataclass_keeps_its_marker_attribute() -> None:
    sections = split_sections("a\n\n【楼层讨论】\nb")
    assert isinstance(sections[1], MarkerSection)
    assert sections[1].marker == "【楼层讨论】"
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_claimable_dispatch.py -v`
Expected: FAIL，`ImportError: cannot import name 'MarkerSection' from 'src.corpus.sections'`

- [ ] **Step 3: 改 `src/corpus/sections.py`**

import 段（`:22-26`）改为：

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List

from ..models import ContentItem, Section
```

dataclass（`:47-53`）改名，并更新 `split_sections` 的返回标注：

```python
@dataclass
class MarkerSection:
    """One contiguous block found by scanning for a scraper-inserted marker.

    Legacy path: kept for backfilling databases written before structured
    sections existed, and for the `--tiering=marker` ablation arm. New writes
    go through `ContentItem.sections` instead.
    """

    tier: str
    marker: str | None
    text: str


def split_sections(content: str | None) -> List[MarkerSection]:
```

`split_sections` 的函数体只有两处构造要跟着改名：`:70` 的 `Section(TIER_PRIMARY, None, head.strip())` 与 `:79` 的 `Section(_MARKERS[marker], marker, text)`，都改成 `MarkerSection(...)`。其余不动。

文件末尾追加：

```python
def marker_sections_to_model(content: str | None) -> List[Section]:
    """Convert the legacy marker split into typed sections.

    Every converted section is stamped `legacy_marker` because its tier was
    inferred from a string convention rather than declared by the scraper;
    P1 discounts that provenance when scoring trust.
    """
    return [
        Section(
            tier=s.tier,
            text=s.text,
            provenance="legacy_marker",
            asserted=True,
            locator=s.marker,
        )
        for s in split_sections(content)
    ]


def claimable_from_sections(sections: Iterable[Section]) -> str:
    """Author-asserted text only — the sole layer evidence may rest on."""
    return "\n\n".join(
        s.text.strip()
        for s in sections
        if s.tier == TIER_PRIMARY and s.asserted and s.text.strip()
    ).strip()


def claimable_of(item: ContentItem) -> str:
    """The claimable layer of an item, whichever path produced its sections.

    Items written by a migrated scraper carry typed sections; items read back
    from a pre-v3 database, or produced by a scraper still concatenating
    markers, fall back to the marker scan.
    """
    if item.sections:
        return claimable_from_sections(item.sections)
    return claimable_text(item.content)
```

- [ ] **Step 4: 把 claim 抽取的输入切到 `claimable_of`**

`src/analysis/claims.py:37`：

```python
from ..corpus.sections import claimable_of
```

（`claimable_text` 在这个文件里只被 `_author_text` 用过一次，替换后就是未用 import，必须一起删掉，否则 lint 会报。）

`src/analysis/claims.py:443`（`extract_claims` 的第一行）：

```python
        body = self._author_text(item)
```

`src/analysis/claims.py:624-631`（`_author_text` 整体替换）：

```python
    def _author_text(self, item: ContentItem) -> str:
        """Author-written text of an item.

        A reply in a comment thread is somebody's opinion, not the item's
        assertion; feeding it to extraction turns crowd noise into claims.
        """
        if not self.claimable_only:
            return (item.content or "").strip()
        return claimable_of(item)
```

- [ ] **Step 5: 修好被签名变更打断的现有测试**

`tests/test_evidence_tiers.py:198`：

```python
    author_text = analyzer._author_text(item)
```

- [ ] **Step 6: 跑测试，确认通过**

Run: `uv run pytest tests/test_claimable_dispatch.py tests/test_evidence_tiers.py tests/test_claims.py -v`
Expected: PASS。`test_evidence_tiers.py` 的 12 条应全部仍绿——它构造的 item 没有 `sections`，走 legacy 分支。

- [ ] **Step 7: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 710 + 7 = 717

- [ ] **Step 8: 提交**

```bash
git add src/corpus/sections.py src/analysis/claims.py tests/test_claimable_dispatch.py \
        tests/test_evidence_tiers.py
git commit -m "Feat: route the claimable layer through typed sections, markers as fallback"
```

---

### Task 3: corpus schema v3 —— 三列、迁移、写入侧

**Files:**
- Modify: `src/corpus/store.py:34-36`（import）、`:38`（版本）、`:49-64`（`items` 建表）、`:145-146`（`__init__` 顺序）、`:178` 之后新增 backfill 方法、`:203-238`（`add_items` + 新增 `known_ids`）、`:429-438`（`_row_to_dict`）
- Test: `tests/test_corpus_v3_migration.py`

**Interfaces:**
- Consumes: `claimable_of`、`marker_sections_to_model`（Task 2）
- Produces:
  - `SCHEMA_VERSION = 3`
  - `items` 新列：`locator TEXT NOT NULL DEFAULT ''`、`time_basis TEXT NOT NULL DEFAULT 'published'`、`sections_json TEXT NOT NULL DEFAULT '[]'`
  - `Corpus.known_ids(ids: Iterable[str]) -> set`（返回其中**已存在**的 id）
  - `Corpus._row_to_dict` 把 `sections_json` 弹出并解析成 `sections`（与 `metadata_json` → `metadata` 同一套做法）

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_corpus_v3_migration.py`：

```python
"""Schema v3: locator/time_basis/sections_json, backfilled on old databases."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.corpus.store import SCHEMA_VERSION, Corpus
from src.models import ContentItem, Section, SourceType

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)

V2_SCHEMA = """
CREATE TABLE items (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE, source_type TEXT NOT NULL, title TEXT NOT NULL,
    url TEXT NOT NULL, author TEXT, published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
    claimable TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}', fingerprint INTEGER NOT NULL,
    cluster_id TEXT, run_id INTEGER
);
CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
    finished_at TEXT, since TEXT NOT NULL, items_new INTEGER NOT NULL DEFAULT 0,
    items_total_seen INTEGER NOT NULL DEFAULT 0, note TEXT);
"""


def make(idx: str, **overrides) -> ContentItem:
    base = dict(
        id=f"v3:{idx}",
        source_type=SourceType.DISCOURSE,
        title=f"title {idx}",
        url=f"https://forum.test/t/{idx}",
        content="author body",
        published_at=NOW,
        fetched_at=NOW,
    )
    base.update(overrides)
    return ContentItem(**base)


def test_schema_version_is_three(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        assert SCHEMA_VERSION == 3
        assert corpus._conn.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        corpus.close()


def test_new_columns_exist_on_a_fresh_database(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        cols = {r["name"] for r in corpus._conn.execute("PRAGMA table_info(items)")}
        assert {"locator", "time_basis", "sections_json"} <= cols
    finally:
        corpus.close()


def test_add_items_writes_locator_time_basis_and_sections(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        corpus.add_items([make("1", sections=[
            Section(tier="primary", text="the author says X"),
            Section(tier="community", text="a stranger says Y", author="s", locator="#2"),
        ])])
        row = corpus._conn.execute(
            "SELECT locator, time_basis, sections_json, claimable FROM items WHERE id='v3:1'"
        ).fetchone()
        assert row["locator"] == "https://forum.test/t/1"
        assert row["time_basis"] == "published"
        stored = json.loads(row["sections_json"])
        assert [s["tier"] for s in stored] == ["primary", "community"]
        assert stored[1]["locator"] == "#2"
        assert row["claimable"] == "the author says X"
    finally:
        corpus.close()


def test_url_less_item_is_stored_under_its_locator(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        corpus.add_items([make("nourl", url=None, locator="xhs:note:abc")])
        row = corpus._conn.execute(
            "SELECT url, locator FROM items WHERE id='v3:nourl'"
        ).fetchone()
        assert row["url"] == "xhs:note:abc"
        assert row["locator"] == "xhs:note:abc"
    finally:
        corpus.close()


def test_time_basis_unknown_is_persisted(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        corpus.add_items([make("notime", published_at=None)])
        row = corpus._conn.execute(
            "SELECT time_basis, published_at FROM items WHERE id='v3:notime'"
        ).fetchone()
        assert row["time_basis"] == "unknown"
        assert row["published_at"] == NOW.isoformat()
    finally:
        corpus.close()


def test_v2_database_is_backfilled_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(V2_SCHEMA)
    conn.execute(
        """INSERT INTO items (id, source_type, title, url, published_at, fetched_at,
                              content, claimable, fingerprint)
           VALUES ('legacy:1','v2ex','Old','https://e.com/1','2026-09-01T00:00:00+00:00',
                   '2026-09-01T00:00:00+00:00',?,?,0)""",
        ("author body\n\n【评论区 Top】\ncrowd body", "author body"),
    )
    conn.commit()
    conn.close()

    migrated = Corpus(path)
    try:
        row = migrated._conn.execute(
            "SELECT locator, time_basis, sections_json, claimable FROM items WHERE id='legacy:1'"
        ).fetchone()
        assert row["locator"] == "https://e.com/1"
        assert row["time_basis"] == "published"
        sections = json.loads(row["sections_json"])
        assert [(s["tier"], s["provenance"]) for s in sections] == [
            ("primary", "legacy_marker"),
            ("community", "legacy_marker"),
        ]
        assert row["claimable"] == "author body"
    finally:
        migrated.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(V2_SCHEMA)
    conn.execute(
        """INSERT INTO items (id, source_type, title, url, published_at, fetched_at,
                              content, claimable, fingerprint)
           VALUES ('legacy:2','v2ex','Old','https://e.com/2','2026-09-01T00:00:00+00:00',
                   '2026-09-01T00:00:00+00:00','body','body',0)"""
    )
    conn.commit()
    conn.close()

    first = Corpus(path)
    first.close()
    second = Corpus(path)
    try:
        assert second._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        assert second._conn.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        second.close()


def test_row_to_dict_exposes_parsed_sections(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        corpus.add_items([make("1", sections=[Section(tier="primary", text="x")])])
        rows = corpus.recent(limit=5)
        assert rows[0]["sections"][0]["text"] == "x"
        assert "sections_json" not in rows[0]
    finally:
        corpus.close()


def test_known_ids_reports_only_rows_already_stored(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path / "c.db")
    try:
        corpus.add_items([make("1")])
        assert corpus.known_ids(["v3:1", "v3:2"]) == {"v3:1"}
        assert corpus.known_ids([]) == set()
    finally:
        corpus.close()
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_corpus_v3_migration.py -v`
Expected: FAIL，`assert 2 == 3`，以及 `sqlite3.OperationalError: no such column: locator`

- [ ] **Step 3: 改 `src/corpus/store.py`**

import 段（`:34-36`）：

```python
from ..models import ContentItem
from .sections import claimable_of, claimable_text, marker_sections_to_model
from .simhash import cluster_pairs, fingerprint
```

（`claimable_text` 仍被 `_backfill_legacy_layers:175` 使用，不能删。）

版本号（`:38`）：

```python
SCHEMA_VERSION = 3
```

`_SCHEMA` 的 `items` 建表语句（`:49-64`），在 `claimable` 之后、`metadata_json` 之前插入三列：

```sql
CREATE TABLE IF NOT EXISTS items (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    author TEXT,
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    claimable TEXT NOT NULL DEFAULT '',
    locator TEXT NOT NULL DEFAULT '',
    time_basis TEXT NOT NULL DEFAULT 'published',
    sections_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    fingerprint INTEGER NOT NULL,
    cluster_id TEXT,
    run_id INTEGER REFERENCES runs(id)
);
```

`__init__`（`:145-146`）——两行 backfill 的**先后不能颠倒**，因为 `_backfill_legacy_layers` 负责补 `claimable` 列，而 `claim_fts` 建在它之上：

```python
        legacy = self._backfill_legacy_layers()
        legacy = self._backfill_v3_identity() or legacy
        self._conn.executescript(_SCHEMA)
```

在 `_backfill_legacy_layers` 之后新增：

```python
    def _backfill_v3_identity(self) -> bool:
        """Add locator/time_basis/sections_json to a pre-v3 database.

        Rows written before structured sections existed get their sections
        reconstructed by the legacy marker scan and stamped `legacy_marker`,
        so a reader can tell an inferred tier from a declared one.
        """
        if not self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'"
        ).fetchone():
            return False
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(items)")}
        if "locator" in columns:
            return False
        self._conn.execute("ALTER TABLE items ADD COLUMN locator TEXT NOT NULL DEFAULT ''")
        self._conn.execute(
            "ALTER TABLE items ADD COLUMN time_basis TEXT NOT NULL DEFAULT 'published'"
        )
        self._conn.execute(
            "ALTER TABLE items ADD COLUMN sections_json TEXT NOT NULL DEFAULT '[]'"
        )
        rows = self._conn.execute("SELECT rowid, url, content FROM items").fetchall()
        self._conn.executemany(
            "UPDATE items SET locator=?, sections_json=? WHERE rowid=?",
            [
                (
                    r["url"],
                    json.dumps(
                        [s.model_dump() for s in marker_sections_to_model(r["content"])],
                        ensure_ascii=False,
                    ),
                    r["rowid"],
                )
                for r in rows
            ],
        )
        self._conn.commit()
        return True
```

（`time_basis` 由 `DEFAULT 'published'` 直接给出，不需要 UPDATE。）

`add_items`（`:203-238`）整体替换，并在其后新增 `known_ids`：

```python
    def add_items(self, items: Iterable[ContentItem], run_id: Optional[int] = None) -> int:
        """Insert content items; existing ids are skipped (append-only).

        Returns the number of newly stored rows.
        """
        rows = []
        for item in items:
            content = item.content or ""
            text_for_fp = f"{item.title}\n{content}"
            rows.append(
                (
                    item.id,
                    item.source_type.value,
                    item.title,
                    item.locator,
                    item.author,
                    item.published_at.astimezone(timezone.utc).isoformat(),
                    item.fetched_at.astimezone(timezone.utc).isoformat(),
                    content,
                    claimable_of(item),
                    item.locator,
                    item.time_basis,
                    json.dumps([s.model_dump() for s in item.sections], ensure_ascii=False),
                    json.dumps(_jsonable(item.metadata), ensure_ascii=False),
                    _as_signed64(fingerprint(text_for_fp)),
                    run_id,
                )
            )
        before = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        self._conn.executemany(
            """INSERT OR IGNORE INTO items
               (id, source_type, title, url, author, published_at, fetched_at,
                content, claimable, locator, time_basis, sections_json,
                metadata_json, fingerprint, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self._conn.commit()
        after = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        return int(after - before)

    def known_ids(self, ids: Iterable[str]) -> set:
        """Which of `ids` are already stored.

        Keeps a re-fetched, time-unknown item from being analysed again:
        `INSERT OR IGNORE` stops duplicate rows, but nothing else stops
        duplicate LLM spend.
        """
        wanted = [i for i in ids if i]
        if not wanted:
            return set()
        placeholders = ",".join("?" * len(wanted))
        rows = self._conn.execute(
            f"SELECT id FROM items WHERE id IN ({placeholders})", wanted
        ).fetchall()
        return {r["id"] for r in rows}
```

`url` 列写的是 `item.locator`：`locator` 在 Task 1 的 validator 里已保证非空，且当 `url` 存在时 `locator == str(url)`，所以对现有源逐字节等价。`add_items` 的返回类型保持 `int`，`tests/test_corpus.py:93/95/102` 的断言因此不用动。

`_row_to_dict`（`:429-438`）：

```python
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d.pop("rowid", None)
        if "metadata_json" in d:
            try:
                d["metadata"] = json.loads(d.pop("metadata_json"))
            except (json.JSONDecodeError, TypeError):
                d["metadata"] = {}
        if "sections_json" in d:
            try:
                d["sections"] = json.loads(d.pop("sections_json"))
            except (json.JSONDecodeError, TypeError):
                d["sections"] = []
        return d
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `uv run pytest tests/test_corpus_v3_migration.py -v`
Expected: PASS，9 passed

- [ ] **Step 5: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 717 + 9 = 726

`tests/test_evidence_tiers.py:108-144` 用的是 **pre-tiering** schema（连 `claimable` 列都没有），会先走 `_backfill_legacy_layers` 再走 `_backfill_v3_identity`，两条 backfill 都要生效。这是本 Task 最容易踩的顺序坑。

- [ ] **Step 6: 提交**

```bash
git add src/corpus/store.py tests/test_corpus_v3_migration.py
git commit -m "Feat: corpus schema v3 with locator, time basis and typed sections"
```

---

### Task 4: locator 感知的去重键，以及跨源合并要同步 sections

**Files:**
- Modify: `src/orchestrator.py:14`（import）、`:39` 之后（import）、`:88` 之后（新增函数）、`:944`（key 计算）、`:968-972`（合并分支）
- Test: `tests/test_locator_dedup.py`

**Interfaces:**
- Consumes: `ContentItem.locator` / `.sections` / `.rebuild_content()`（Task 1）、`marker_sections_to_model`（Task 2）
- Produces: `def _deduplication_item_key(item: ContentItem) -> tuple`（模块级，`src/orchestrator.py`）

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_locator_dedup.py`：

```python
"""Dedup keys work for URL locators and for non-URL locators alike."""

from datetime import datetime, timezone

from src.corpus.sections import claimable_of
from src.models import ContentItem, Section, SourceType
from src.orchestrator import HorizonOrchestrator, _deduplication_item_key

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def make(idx: str, **overrides) -> ContentItem:
    base = dict(
        id=f"d:{idx}",
        source_type=SourceType.RSS,
        title="t",
        url=f"https://example.com/{idx}",
        content="body",
        published_at=NOW,
        fetched_at=NOW,
    )
    base.update(overrides)
    return ContentItem(**base)


def bare_orchestrator() -> HorizonOrchestrator:
    """No __init__: deduplicate_items touches no instance state."""
    return HorizonOrchestrator.__new__(HorizonOrchestrator)


def test_url_locators_keep_the_existing_normalisation() -> None:
    a = make("a", url="HTTPS://Example.COM:443/story/?utm_source=x")
    b = make("b", url="https://example.com/story")
    assert _deduplication_item_key(a) == _deduplication_item_key(b)


def test_non_url_locators_compare_by_exact_value() -> None:
    a = make("a", url=None, locator="tieba:p/123")
    b = make("b", url=None, locator="tieba:p/124")
    c = make("c", url=None, locator="tieba:p/123")
    assert _deduplication_item_key(a) != _deduplication_item_key(b)
    assert _deduplication_item_key(a) == _deduplication_item_key(c)


def test_non_url_and_url_keys_never_collide() -> None:
    assert _deduplication_item_key(make("a", url=None, locator="x")) != \
        _deduplication_item_key(make("b", url="https://example.com/x"))


def test_merging_section_bearing_items_keeps_sections_and_content_in_sync() -> None:
    primary = make("p", url="https://example.com/story", sections=[
        Section(tier="primary", text="author of the first copy"),
    ])
    duplicate = make(
        "q", url="https://example.com/story", source_type=SourceType.HACKERNEWS,
        sections=[
            Section(tier="primary", text="author of the second copy"),
            Section(tier="community", text="a stranger", author="s"),
        ],
    )
    merged = bare_orchestrator().deduplicate_items([primary, duplicate])
    assert len(merged) == 1
    out = merged[0]
    assert len(out.sections) == 3
    assert "- @s: a stranger" in out.content
    assert out.metadata["merged_sources"] == ["rss", "hackernews"]


def test_merging_items_without_sections_keeps_the_old_string_concatenation() -> None:
    primary = make("p", url="https://example.com/story", content="the richer primary content")
    duplicate = make("q", url="https://example.com/story",
                     source_type=SourceType.HACKERNEWS, content="short")
    merged = bare_orchestrator().deduplicate_items([primary, duplicate])
    assert "--- From hackernews ---" in merged[0].content


def test_merged_community_text_never_reaches_claimable() -> None:
    primary = make("p", url="https://example.com/story",
                   sections=[Section(tier="primary", text="author words")])
    duplicate = make("q", url="https://example.com/story",
                     source_type=SourceType.HACKERNEWS,
                     sections=[Section(tier="community", text="stranger words", author="s")])
    out = bare_orchestrator().deduplicate_items([primary, duplicate])[0]
    assert claimable_of(out) == "author words"


def test_merging_a_marker_item_into_a_section_item_tiers_the_incoming_text() -> None:
    primary = make("p", url="https://example.com/story",
                   sections=[Section(tier="primary", text="author words")])
    duplicate = make("q", url="https://example.com/story",
                     source_type=SourceType.HACKERNEWS,
                     content="their body\n\n--- Top Comments ---\nstranger words")
    out = bare_orchestrator().deduplicate_items([primary, duplicate])[0]
    assert claimable_of(out) == "author words\n\ntheir body"
    assert "stranger words" not in claimable_of(out)
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_locator_dedup.py -v`
Expected: FAIL，`ImportError: cannot import name '_deduplication_item_key' from 'src.orchestrator'`

- [ ] **Step 3: 实现 `_deduplication_item_key`**

`src/orchestrator.py:14` 的 models import 补 `Section`：

```python
from .models import Config, ContentItem, Section
```

紧跟 `:39` 的 `from .processing import ProfileRegistry` 加一行：

```python
from .corpus.sections import marker_sections_to_model
```

在 `_deduplication_url_key`（结束于 `:88`）之后插入：

```python
def _deduplication_item_key(item: ContentItem) -> tuple:
    """Identity key for cross-source deduplication.

    URL locators go through the existing seven-field normalisation so that
    tracking parameters and default ports keep collapsing as before. A
    non-URL locator (an app-only id, a note id) has no host or query to
    normalise, so it is compared verbatim under a distinct tag — a shape
    that can never collide with a URL key.
    """
    locator = item.locator or (str(item.url) if item.url else "")
    if "://" in locator:
        return _deduplication_url_key(locator)
    return ("locator", locator)
```

- [ ] **Step 4: 把去重与合并切到新键、并让 sections 参与合并**

`src/orchestrator.py:944`：

```python
            key = (*_deduplication_item_key(item), requested_profile)
```

`src/orchestrator.py:968-972`（`# Append content (e.g., comments from another source)` 那一段）整体替换：

```python
                # Append the other source's material. When either side carries
                # typed sections, merge those and let `content` be re-derived:
                # concatenating strings would desync the two, and the
                # concatenated tail would land in `claimable` untiered.
                if item is not primary and (item.content or item.sections):
                    incoming = list(item.sections) or marker_sections_to_model(item.content)
                    if incoming or primary.sections:
                        primary.sections.extend(incoming)
                        primary.rebuild_content()
                    elif primary.content and item.content not in primary.content:
                        primary.content = (primary.content or "") + (
                            f"\n\n--- From {item.source_type.value} ---\n" + item.content
                        )
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `uv run pytest tests/test_locator_dedup.py tests/test_cross_source_duplicates.py -v`
Expected: PASS。`test_cross_source_duplicates.py` 的 8 条必须仍全绿——它构造的 item 都没有 sections，走 `elif` 分支，字符串拼接行为逐字节不变。

- [ ] **Step 6: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 726 + 7 = 733

- [ ] **Step 7: 提交**

```bash
git add src/orchestrator.py tests/test_locator_dedup.py
git commit -m "Feat: deduplicate by locator and keep merged sections in sync with content"
```

---

### Task 5: `SourceSpec` / `SOURCE_SPECS`，`SOURCE_REGISTRY` 改为派生

**Files:**
- Modify: `src/models.py:1-7`（import `dataclass`）、`:37-52`（`SOURCE_REGISTRY` 字面量）
- Test: `tests/test_source_registry.py`

**Interfaces:**
- Consumes: `SourceType`、`SourceDefinition`（现有）
- Produces:
  - `SourceKind = Literal["official", "forum", "ugc_social", "aggregator", "search_engine"]`
  - `@dataclass(frozen=True) class RateLimit`：`requests: int = 1`、`per_seconds: float = 2.0`、`jitter: float = 0.3`
  - `@dataclass(frozen=True) class SourceSpec`：`key`、`label`、`kind`、`credibility_prior`、`login_required`、`editorial_gate`、`time_basis_default`、`rate_limit`、`config_field`、`config_is_list=False`、`item_fields=()`
  - `SOURCE_SPECS: tuple`（14 项，顺序与今天 `SOURCE_REGISTRY` 的字面顺序一致）
  - `SOURCE_REGISTRY: Dict[str, SourceDefinition]`（由 `SOURCE_SPECS` 派生，键值与今天完全相同）

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_source_registry.py`：

```python
"""The source registry is the single place a source is declared.

Guard test A (this task): SOURCE_SPECS <-> SourceType <-> SourcesConfig.
Guard test B (Task 9) adds SOURCE_SPECS <-> SCRAPER_BINDINGS.
"""

import pytest

from src.models import SOURCE_REGISTRY, SOURCE_SPECS, SourcesConfig, SourceType

SPEC_KEYS = {spec.key for spec in SOURCE_SPECS}


def test_every_source_type_has_exactly_one_spec() -> None:
    assert SPEC_KEYS == {member.value for member in SourceType}
    assert len(SPEC_KEYS) == len(SOURCE_SPECS)


def test_source_registry_is_derived_and_unchanged() -> None:
    assert set(SOURCE_REGISTRY) == SPEC_KEYS
    for spec in SOURCE_SPECS:
        definition = SOURCE_REGISTRY[spec.key]
        assert definition.config_field == spec.config_field
        assert definition.config_is_list == spec.config_is_list
        assert definition.item_fields == spec.item_fields


def test_every_spec_points_at_a_real_config_field() -> None:
    fields = set(SourcesConfig.model_fields)
    assert sorted(s.config_field for s in SOURCE_SPECS if s.config_field not in fields) == []


def test_every_config_field_is_claimed_by_a_spec() -> None:
    orphan = sorted(set(SourcesConfig.model_fields) - {s.config_field for s in SOURCE_SPECS})
    assert orphan == []


def test_list_specs_match_their_config_annotation() -> None:
    for spec in SOURCE_SPECS:
        annotation = SourcesConfig.model_fields[spec.config_field].annotation
        assert spec.config_is_list == ("List" in str(annotation)), spec.key


@pytest.mark.parametrize("spec", SOURCE_SPECS, ids=lambda s: s.key)
def test_spec_values_are_in_range(spec) -> None:
    assert 0.0 <= spec.credibility_prior <= 1.0
    assert spec.kind in {"official", "forum", "ugc_social", "aggregator", "search_engine"}
    assert spec.time_basis_default in {"published", "crawled", "unknown"}
    assert spec.label.strip() == spec.label
    if spec.rate_limit is not None:
        assert spec.rate_limit.requests >= 1
        assert spec.rate_limit.per_seconds > 0
        assert 0.0 <= spec.rate_limit.jitter < 1.0


def test_item_fields_exist_on_their_config_model() -> None:
    for spec in SOURCE_SPECS:
        if not spec.item_fields:
            continue
        annotation = SourcesConfig.model_fields[spec.config_field].annotation
        inner = getattr(annotation, "args", (None,))[0]
        model = getattr(inner, "args", (inner,))[0]
        for field_name in spec.item_fields:
            assert field_name in getattr(model, "model_fields", {}), (spec.key, field_name)


def test_labels_match_the_names_the_fetch_report_already_uses() -> None:
    assert {spec.label for spec in SOURCE_SPECS} == {
        "GitHub", "Hacker News", "RSS Feeds", "Reddit", "Telegram", "Twitter",
        "OpenBB", "OSS Insight", "GDELT", "Google News", "Bilibili", "V2EX",
        "Discourse", "YouTube",
    }
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_source_registry.py -v`
Expected: FAIL，`ImportError: cannot import name 'SOURCE_SPECS' from 'src.models'`

- [ ] **Step 3: 实现**

`src/models.py:1-7` 的 import 段加 `dataclasses`：

```python
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Annotated, Literal, Optional, List, Dict, Any, NamedTuple, Union
from pydantic import BaseModel, ConfigDict, HttpUrl, Field, field_validator, model_validator
```

把 `SOURCE_REGISTRY = {...}`（`:37-52`）整体替换为下面这块（`SourceDefinition` 的定义 `:29-34` 保持不动）：

```python
SourceKind = Literal["official", "forum", "ugc_social", "aggregator", "search_engine"]


@dataclass(frozen=True)
class RateLimit:
    """How politely one host may be polled."""

    requests: int = 1
    per_seconds: float = 2.0
    jitter: float = 0.3  # +/-30%, so the interval is not a fingerprint


@dataclass(frozen=True)
class SourceSpec:
    """Everything a source is, minus the code that fetches it.

    Pure metadata on purpose: `models.py` must not import scrapers (they all
    import models), so the key -> class binding lives in
    `src/sources/registry.py`. `credibility_prior` is a hand-set constant in
    P0; P1 calibrates it against the human-labelled verdicts.
    """

    key: str
    label: str
    kind: SourceKind
    credibility_prior: float
    login_required: bool
    editorial_gate: bool
    time_basis_default: Literal["published", "crawled", "unknown"]
    rate_limit: Optional[RateLimit]
    config_field: str
    config_is_list: bool = False
    item_fields: tuple = ()


_GENTLE = RateLimit(requests=1, per_seconds=2.0)

SOURCE_SPECS: tuple = (
    SourceSpec("github", "GitHub", "official", 0.75, False, True, "published",
               None, "github", config_is_list=True),
    SourceSpec("hackernews", "Hacker News", "forum", 0.50, False, False, "published",
               _GENTLE, "hackernews"),
    SourceSpec("rss", "RSS Feeds", "official", 0.70, False, True, "published",
               None, "rss", config_is_list=True),
    SourceSpec("reddit", "Reddit", "ugc_social", 0.40, False, False, "published",
               _GENTLE, "reddit", item_fields=("subreddits", "users")),
    SourceSpec("telegram", "Telegram", "official", 0.55, False, False, "published",
               _GENTLE, "telegram", item_fields=("channels",)),
    SourceSpec("twitter", "Twitter", "ugc_social", 0.40, False, False, "published",
               None, "twitter", item_fields=("users", "keywords")),
    SourceSpec("openbb", "OpenBB", "aggregator", 0.60, False, True, "published",
               None, "openbb", item_fields=("watchlists",)),
    SourceSpec("ossinsight", "OSS Insight", "aggregator", 0.60, False, True, "published",
               None, "ossinsight"),
    SourceSpec("gdelt", "GDELT", "aggregator", 0.55, False, True, "published",
               None, "gdelt"),
    SourceSpec("google_news", "Google News", "aggregator", 0.55, False, True, "published",
               None, "google_news"),
    SourceSpec("bilibili", "Bilibili", "ugc_social", 0.35, False, False, "published",
               _GENTLE, "bilibili"),
    SourceSpec("v2ex", "V2EX", "forum", 0.45, False, False, "published",
               _GENTLE, "v2ex"),
    SourceSpec("discourse", "Discourse", "forum", 0.50, False, False, "published",
               _GENTLE, "discourse", item_fields=("sites",)),
    SourceSpec("youtube", "YouTube", "official", 0.60, False, False, "published",
               None, "youtube", item_fields=("channels",)),
)

SOURCE_REGISTRY = {
    spec.key: SourceDefinition(spec.config_field, spec.config_is_list, spec.item_fields)
    for spec in SOURCE_SPECS
}
```

`credibility_prior` 的取值是 P0 的手工常量，P1 用人评 ROC 校准；此刻只需要满足"官方/编辑把关 > 聚合 > 论坛 > UGC 社交"这个序，不要在 P0 里为它辩护。

- [ ] **Step 4: 跑测试，确认通过**

Run: `uv run pytest tests/test_source_registry.py tests/test_mcp_adapter.py -v`
Expected: PASS。`test_mcp_adapter.py:132` 的 `set(SOURCE_REGISTRY) == {s.value for s in SourceType}` 必须仍绿（派生没有改变键集），`horizon_adapter.py:200/218` 消费的三个字段也逐一相同。

- [ ] **Step 5: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 733 + 21 = 754（其中 14 条来自 `test_spec_values_are_in_range` 的 parametrize）

- [ ] **Step 6: 提交**

```bash
git add src/models.py tests/test_source_registry.py
git commit -m "Feat: declare each source once in SOURCE_SPECS, derive SOURCE_REGISTRY"
```

---

### Task 6: `src/scrapers/throttle.py` —— per-host 令牌桶

**Files:**
- Create: `src/scrapers/throttle.py`
- Test: `tests/test_throttle.py`

**Interfaces:**
- Consumes: `RateLimit`（Task 5）
- Produces:
  - `class Throttle`，构造参数 `default: Optional[RateLimit] = None`、`limits: Optional[Dict[str, RateLimit]] = None`、`clock: Callable[[], float] = time.monotonic`、`sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep`、`rng: Optional[random.Random] = None`
  - `async Throttle.acquire(url_or_host: str) -> None`
  - `async Throttle.request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response`
  - `Throttle.limit_for(url_or_host: str) -> Optional[RateLimit]`
  - `def retry_after_seconds(response: httpx.Response, fallback: float = 5.0) -> float`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_throttle.py`：

```python
"""Throttling with an injected clock: no test in here sleeps for real."""

import asyncio
import random

import httpx
import pytest

from src.models import RateLimit
from src.scrapers.throttle import Throttle, retry_after_seconds


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def run(coro):
    return asyncio.run(coro)


def throttle(time: FakeTime, default: RateLimit | None, **kw) -> Throttle:
    return Throttle(default=default, clock=time.clock, sleeper=time.sleep,
                    rng=random.Random(0), **kw)


def test_no_limit_means_no_waiting() -> None:
    t = FakeTime()
    th = throttle(t, None)
    run(th.acquire("https://a.example/x"))
    run(th.acquire("https://a.example/x"))
    assert t.slept == []


def test_second_request_to_the_same_host_waits_one_interval() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=1, per_seconds=2.0, jitter=0.0))
    run(th.acquire("https://a.example/1"))
    run(th.acquire("https://a.example/2"))
    assert t.slept == [2.0]


def test_first_request_never_waits() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=1, per_seconds=5.0, jitter=0.0))
    run(th.acquire("https://a.example/1"))
    assert t.slept == []


def test_different_hosts_are_independent() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=1, per_seconds=2.0, jitter=0.0))
    run(th.acquire("https://a.example/1"))
    run(th.acquire("https://b.example/1"))
    assert t.slept == []


def test_per_host_limit_overrides_the_default() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=1, per_seconds=10.0, jitter=0.0),
                  limits={"a.example": RateLimit(requests=1, per_seconds=1.0, jitter=0.0)})
    run(th.acquire("https://a.example/1"))
    run(th.acquire("https://a.example/2"))
    assert t.slept == [1.0]


def test_requests_per_window_shortens_the_interval() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=4, per_seconds=2.0, jitter=0.0))
    for i in range(3):
        run(th.acquire(f"https://a.example/{i}"))
    assert t.slept == [0.5, 0.5]


def test_jitter_stays_within_the_declared_band() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=1, per_seconds=2.0, jitter=0.3))
    run(th.acquire("https://a.example/1"))
    run(th.acquire("https://a.example/2"))
    assert 1.4 <= t.slept[0] <= 2.6


def test_limit_for_accepts_a_bare_host() -> None:
    th = throttle(FakeTime(), None, limits={"a.example": RateLimit(per_seconds=3.0)})
    assert th.limit_for("a.example").per_seconds == 3.0
    assert th.limit_for("https://a.example/p").per_seconds == 3.0
    assert th.limit_for("https://b.example/p") is None


def test_429_is_retried_once_after_retry_after() -> None:
    t = FakeTime()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = run(throttle(t, None).request(client, "GET", "https://a.example/x"))
    run(client.aclose())
    assert response.status_code == 200
    assert calls == ["/x", "/x"]
    assert 3.0 in t.slept


def test_429_without_retry_after_uses_the_fallback() -> None:
    t = FakeTime()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(429) if len(seen) == 1 else httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    run(throttle(t, None).request(client, "GET", "https://a.example/x"))
    run(client.aclose())
    assert 5.0 in t.slept


def test_429_twice_is_not_retried_a_third_time() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(429)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = run(throttle(FakeTime(), None).request(client, "GET", "https://a.example/x"))
    run(client.aclose())
    assert response.status_code == 429
    assert len(seen) == 2


def test_non_429_errors_are_not_retried() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    run(throttle(FakeTime(), None).request(client, "GET", "https://a.example/x"))
    run(client.aclose())
    assert len(seen) == 1


@pytest.mark.parametrize("header,expected", [
    ("7", 7.0), ("0", 0.0), ("2.5", 2.5), ("", 5.0),
    ("Wed, 21 Oct 2026 07:28:00 GMT", 5.0), ("-3", 0.0),
])
def test_retry_after_parsing_never_raises(header: str, expected: float) -> None:
    headers = {"Retry-After": header} if header else {}
    assert retry_after_seconds(httpx.Response(429, headers=headers)) == expected
```

最后一条针对的是**现有实现的真实缺陷**：`reddit.py:540` 与 `telegram.py:67` 都写 `int(response.headers.get("Retry-After", 5))`，而 `Retry-After` 按 RFC 9110 可以是 HTTP-date，那时 `int()` 会抛 `ValueError`，把一次限速升级成一次抓取失败。

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_throttle.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'src.scrapers.throttle'`

- [ ] **Step 3: 实现 `src/scrapers/throttle.py`**

```python
"""Per-host request throttling.

Two scrapers grew their own 429 handling (reddit, telegram) and both copied
the same bug: `int(headers["Retry-After"])` raises on the HTTP-date form the
RFC allows. This module is the single implementation.

The clock, the sleeper and the RNG are injectable. A throttle test that waits
for real is a wall-clock test, and this repo already had to remove one.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, Dict, Optional
from urllib.parse import urlsplit

import httpx

from ..models import RateLimit

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]

_DEFAULT_RETRY_AFTER = 5.0


def retry_after_seconds(
    response: httpx.Response, fallback: float = _DEFAULT_RETRY_AFTER
) -> float:
    """Parse a Retry-After header, falling back instead of raising.

    Only the delta-seconds form is honoured; an HTTP-date is treated as
    absent, because guessing a wall-clock offset is worse than a fixed delay.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return fallback
    try:
        return max(0.0, float(raw.strip()))
    except (TypeError, ValueError):
        return fallback


class Throttle:
    """Serialise requests per host and absorb 429s with one retry."""

    def __init__(
        self,
        default: Optional[RateLimit] = None,
        limits: Optional[Dict[str, RateLimit]] = None,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
        rng: Optional[random.Random] = None,
    ) -> None:
        self._default = default
        self._limits = dict(limits or {})
        self._clock = clock
        self._sleep = sleeper
        self._rng = rng if rng is not None else random.Random()
        self._next_ok: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _host(url_or_host: str) -> str:
        return urlsplit(url_or_host).hostname or url_or_host

    def limit_for(self, url_or_host: str) -> Optional[RateLimit]:
        return self._limits.get(self._host(url_or_host), self._default)

    async def acquire(self, url_or_host: str) -> None:
        """Wait until this host may be polled again."""
        limit = self.limit_for(url_or_host)
        if limit is None or limit.requests <= 0:
            return
        host = self._host(url_or_host)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = self._clock()
            scheduled = self._next_ok.get(host)
            interval = limit.per_seconds / limit.requests
            if limit.jitter:
                interval *= 1.0 + self._rng.uniform(-limit.jitter, limit.jitter)
            start = now if scheduled is None else max(now, scheduled)
            self._next_ok[host] = start + interval
            if scheduled is not None and scheduled > now:
                await self._sleep(scheduled - now)

    async def request(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs
    ) -> httpx.Response:
        """One throttled request, retried once when the host says 429."""
        await self.acquire(url)
        response = await client.request(method, url, **kwargs)
        if response.status_code != 429:
            return response
        await self._sleep(retry_after_seconds(response))
        await self.acquire(url)
        return await client.request(method, url, **kwargs)
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `uv run pytest tests/test_throttle.py -v`
Expected: PASS，18 passed（含 6 条 parametrize）

- [ ] **Step 5: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 754 + 18 = 772

- [ ] **Step 6: 提交**

```bash
git add src/scrapers/throttle.py tests/test_throttle.py
git commit -m "Feat: one per-host throttle instead of two copies of 429 handling"
```

---

### Task 7: `src/scrapers/auth.py` —— token 与 cookie 两种 provider

**Files:**
- Create: `src/scrapers/auth.py`
- Test: `tests/test_scraper_auth.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `class AuthProvider(Protocol)`：`headers() -> Dict[str, str]`、`on_unauthorized() -> bool`（`True` = "已刷新，值得重试一次"）
  - `class NullAuth`
  - `class EnvTokenAuth(env_var, scheme="Bearer", header="Authorization", environ=None)`
  - `class CookieFileAuth(path, now=..., max_age=None)`，带公开属性 `expired_names: Tuple[str, ...]`
  - `def load_cookie_file(path) -> Dict[str, str]`（Netscape `cookies.txt` 与浏览器导出 JSON 两种格式）

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_scraper_auth.py`：

```python
"""Credential providers: no network, no real clock, no real environment."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.scrapers.auth import (
    CookieFileAuth,
    EnvTokenAuth,
    NullAuth,
    load_cookie_file,
)

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
FRESH = 1893456000   # 2030-01-01, comfortably after T0
STALE = 1000000000   # 2001-09-09, comfortably before T0

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    f"#HttpOnly_.example.com\tTRUE\t/\tTRUE\t{FRESH}\tBDUSS\tsecret-value\n"
    f".example.com\tTRUE\t/\tFALSE\t{FRESH}\tBAIDUID\tABCD:1234\n"
)

BROWSER_JSON = """[
  {"name": "BDUSS", "value": "secret-value", "domain": ".example.com",
   "expirationDate": %d},
  {"name": "BAIDUID", "value": "ABCD:1234", "domain": ".example.com",
   "expirationDate": %d}
]""" % (FRESH, FRESH)


def test_null_auth_contributes_nothing() -> None:
    assert NullAuth().headers() == {}
    assert NullAuth().on_unauthorized() is False


def test_env_token_reads_the_named_variable() -> None:
    assert EnvTokenAuth("MY_TOKEN", environ={"MY_TOKEN": "abc123"}).headers() == {
        "Authorization": "Bearer abc123"
    }


def test_env_token_supports_a_custom_scheme_and_header() -> None:
    auth = EnvTokenAuth("K", scheme="token", header="X-Api-Key", environ={"K": "v"})
    assert auth.headers() == {"X-Api-Key": "token v"}


def test_schemeless_token_is_sent_bare() -> None:
    assert EnvTokenAuth("K", scheme="", environ={"K": "v"}).headers() == {"Authorization": "v"}


def test_missing_or_blank_env_token_degrades_to_no_headers() -> None:
    assert EnvTokenAuth("ABSENT", environ={}).headers() == {}
    assert EnvTokenAuth("K", environ={"K": "   "}).headers() == {}


def test_env_token_cannot_be_refreshed() -> None:
    assert EnvTokenAuth("K", environ={"K": "v"}).on_unauthorized() is False


def test_loads_netscape_cookie_file(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(NETSCAPE, encoding="utf-8")
    assert load_cookie_file(path) == {"BDUSS": "secret-value", "BAIDUID": "ABCD:1234"}


def test_loads_browser_json_cookie_file(tmp_path: Path) -> None:
    path = tmp_path / "cookies.json"
    path.write_text(BROWSER_JSON, encoding="utf-8")
    assert load_cookie_file(path) == {"BDUSS": "secret-value", "BAIDUID": "ABCD:1234"}


def test_unreadable_cookie_file_degrades_to_empty(tmp_path: Path) -> None:
    assert load_cookie_file(tmp_path / "nope.txt") == {}


def test_cookie_auth_sends_one_cookie_header(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(NETSCAPE, encoding="utf-8")
    headers = CookieFileAuth(path, now=lambda: T0).headers()
    assert set(headers) == {"Cookie"}
    pairs = dict(p.split("=", 1) for p in headers["Cookie"].split("; "))
    assert pairs == {"BDUSS": "secret-value", "BAIDUID": "ABCD:1234"}


def test_missing_cookie_file_degrades_to_no_headers(tmp_path: Path) -> None:
    assert CookieFileAuth(tmp_path / "nope.txt", now=lambda: T0).headers() == {}


def test_expired_cookies_are_named_instead_of_silently_sent(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".example.com\tTRUE\t/\tTRUE\t{STALE}\tBDUSS\told\n",
        encoding="utf-8",
    )
    auth = CookieFileAuth(path, now=lambda: T0)
    assert auth.headers() == {}
    assert auth.expired_names == ("BDUSS",)


def test_a_stale_file_allows_exactly_one_reload(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(NETSCAPE, encoding="utf-8")
    auth = CookieFileAuth(path, now=lambda: T0)
    assert auth.on_unauthorized() is True    # re-read from disk
    assert auth.on_unauthorized() is False   # never again for this instance


def test_max_age_rejects_a_file_that_is_not_freshly_exported(tmp_path: Path) -> None:
    """max_age means 'the cookie must be within this far of expiring'.

    A cookie expiring in 2030 is not proof of a recently exported file, so a
    one-day max_age rejects it. This is the guard against replaying a cookie
    dump somebody committed months ago.
    """
    path = tmp_path / "cookies.txt"
    path.write_text(NETSCAPE, encoding="utf-8")
    auth = CookieFileAuth(path, now=lambda: T0, max_age=timedelta(days=1))
    assert auth.headers() == {}
    assert auth.expired_names == ("BDUSS", "BAIDUID")
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_scraper_auth.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'src.scrapers.auth'`

- [ ] **Step 3: 实现 `src/scrapers/auth.py`**

```python
"""Credential providers for sources that need more than an anonymous GET.

Deliberately narrow: an environment token and a cookie file. Signature-based
schemes (x-s/x-t style) are out of scope — see the spec's "explicitly not
doing" list — so no empty abstraction is reserved for them here.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class AuthProvider(Protocol):
    """Headers to attach, and whether a 401/403 is worth one retry."""

    def headers(self) -> Dict[str, str]: ...

    def on_unauthorized(self) -> bool: ...


class NullAuth:
    """The default: behave exactly like an unauthenticated client."""

    def headers(self) -> Dict[str, str]:
        return {}

    def on_unauthorized(self) -> bool:
        return False


class EnvTokenAuth:
    """A bearer/api-key token read from an environment variable."""

    def __init__(
        self,
        env_var: str,
        scheme: str = "Bearer",
        header: str = "Authorization",
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.env_var = env_var
        self.scheme = scheme
        self.header = header
        self._environ = os.environ if environ is None else environ

    def headers(self) -> Dict[str, str]:
        token = (self._environ.get(self.env_var) or "").strip()
        if not token:
            return {}
        value = f"{self.scheme} {token}" if self.scheme else token
        return {self.header: value}

    def on_unauthorized(self) -> bool:
        return False


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        logger.warning("Could not read cookie file %s: %s", path.name, exc)
        return None


def load_cookie_file(path: Path | str) -> Dict[str, str]:
    """Read a Netscape cookies.txt or a browser-exported JSON cookie list."""
    raw = _read(Path(path))
    if raw is None:
        return {}
    if raw.lstrip().startswith("["):
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Unparseable JSON cookie file %s: %s", Path(path).name, exc)
            return {}
        return {
            str(e["name"]): str(e.get("value", ""))
            for e in entries
            if isinstance(e, dict) and e.get("name")
        }
    cookies: Dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7 and fields[5]:
            cookies[fields[5]] = fields[6]
    return cookies


def _cookie_expiry(path: Path) -> Dict[str, datetime]:
    """Best-effort name -> expiry map; entries without one are omitted."""
    raw = _read(path)
    if raw is None:
        return {}
    out: Dict[str, datetime] = {}
    if raw.lstrip().startswith("["):
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            stamp = entry.get("expirationDate") or entry.get("expires")
            if isinstance(stamp, (int, float)) and stamp > 0:
                out[str(entry["name"])] = _EPOCH + timedelta(seconds=float(stamp))
        return out
    for line in raw.splitlines():
        fields = line.strip().split("\t")
        if len(fields) >= 7 and fields[5] and fields[4].isdigit() and int(fields[4]) > 0:
            out[fields[5]] = _EPOCH + timedelta(seconds=int(fields[4]))
    return out


class CookieFileAuth:
    """Cookies loaded from disk, with expiry detection and one reload.

    A stale cookie file is the most common reason a logged-in scraper silently
    starts returning login pages, so the failure is named in `expired_names`
    instead of surfacing downstream as "found 0 items".
    """

    def __init__(
        self,
        path: Path | str,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_age: Optional[timedelta] = None,
    ) -> None:
        self.path = Path(path)
        self._now = now
        self._max_age = max_age
        self._reloaded = False
        self._cookies: Dict[str, str] = {}
        self.expired_names: Tuple[str, ...] = ()
        self._load()

    def _load(self) -> None:
        moment = self._now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        expiries = _cookie_expiry(self.path)
        rejected: List[str] = []
        usable: Dict[str, str] = {}
        for name, value in load_cookie_file(self.path).items():
            deadline = expiries.get(name)
            expired = deadline is not None and deadline <= moment
            not_fresh = (
                self._max_age is not None
                and deadline is not None
                and deadline - moment > self._max_age
            )
            if expired or not_fresh:
                rejected.append(name)
            else:
                usable[name] = value
        self._cookies = usable
        self.expired_names = tuple(rejected)
        if rejected:
            logger.warning(
                "Cookie file %s has unusable entries: %s",
                self.path.name, ", ".join(rejected),
            )

    def headers(self) -> Dict[str, str]:
        if not self._cookies:
            return {}
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self._cookies.items())}

    def on_unauthorized(self) -> bool:
        if self._reloaded:
            return False
        self._reloaded = True
        self._load()
        return bool(self._cookies)
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `uv run pytest tests/test_scraper_auth.py -v`
Expected: PASS，15 passed

- [ ] **Step 5: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 772 + 15 = 787

- [ ] **Step 6: 提交**

```bash
git add src/scrapers/auth.py tests/test_scraper_auth.py
git commit -m "Feat: env-token and cookie-file auth providers with expiry detection"
```

---

### Task 8: `BaseScraper` 注入 throttle/auth，收拢两份 429 实现，统一 `follow_redirects`

**Files:**
- Modify: `src/scrapers/base.py`（整体替换）
- Modify: `src/scrapers/reddit.py:531-552`
- Modify: `src/scrapers/telegram.py:65-71`
- Modify: `src/orchestrator.py:761`、`:1341`
- Test: `tests/test_throttle.py`（追加）、`tests/test_reddit.py`（追加）

**Interfaces:**
- Consumes: `Throttle`（Task 6）、`AuthProvider` / `NullAuth`（Task 7）
- Produces:
  - `BaseScraper.__init__(self, config: dict, http_client: httpx.AsyncClient, throttle: Optional[Throttle] = None, auth: Optional[AuthProvider] = None)`
  - `BaseScraper.throttle: Throttle`（永不为 `None`；缺省是无 limit 的 `Throttle()`，因此 14 个现有 scraper 不改也保持原行为）
  - `BaseScraper.auth: AuthProvider`（永不为 `None`；缺省 `NullAuth()`）
  - `async BaseScraper._request(method: str, url: str, **kwargs) -> httpx.Response`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_throttle.py` 末尾：

```python
# ----------------------------------------------------------- BaseScraper glue
def test_scraper_request_merges_throttle_and_auth_headers() -> None:
    from src.scrapers.auth import EnvTokenAuth
    from src.scrapers.base import BaseScraper

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(200, json={"ok": True})

    class Probe(BaseScraper):
        async def fetch(self, since):
            return []

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    scraper = Probe({}, client, auth=EnvTokenAuth("T", environ={"T": "tok"}))
    run(scraper._request("GET", "https://a.example/x", headers={"User-Agent": "probe"}))
    run(client.aclose())
    assert seen["authorization"] == "Bearer tok"
    assert seen["user-agent"] == "probe"


def test_scraper_retries_once_when_auth_says_refresh() -> None:
    from src.scrapers.base import BaseScraper

    attempts = []

    class FlakyAuth:
        def __init__(self) -> None:
            self.n = 0

        def headers(self):
            self.n += 1
            return {"Authorization": f"attempt-{self.n}"}

        def on_unauthorized(self):
            return self.n < 2

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.headers["authorization"])
        return httpx.Response(401 if len(attempts) == 1 else 200)

    class Probe(BaseScraper):
        async def fetch(self, since):
            return []

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = run(Probe({}, client, auth=FlakyAuth())._request("GET", "https://a.example/x"))
    run(client.aclose())
    assert response.status_code == 200
    assert attempts == ["attempt-1", "attempt-2"]


def test_default_throttle_has_no_limits_so_existing_scrapers_are_unchanged() -> None:
    from src.scrapers.base import BaseScraper

    class Probe(BaseScraper):
        async def fetch(self, since):
            return []

    scraper = Probe({}, httpx.AsyncClient())
    assert scraper.throttle.limit_for("https://anything.example/x") is None
    assert scraper.auth.headers() == {}
```

追加到 `tests/test_reddit.py` 末尾（先看 `tests/test_reddit.py:1-60` 里已有的 config helper 叫什么，复用它，不要新造）：

```python
def test_rate_limited_get_survives_an_http_date_retry_after():
    """Retry-After may legally be an HTTP-date; int() on it used to explode."""
    import asyncio as _asyncio

    import httpx as _httpx

    from src.models import RedditConfig, RedditSubredditConfig
    from src.scrapers.reddit import RedditScraper

    seen = []

    def handler(request: _httpx.Request) -> _httpx.Response:
        seen.append(request.url.path)
        if len(seen) == 1:
            return _httpx.Response(
                429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
            )
        return _httpx.Response(200, json={"kind": "Listing", "data": {"children": []}})

    config = RedditConfig(
        enabled=True, subreddits=[RedditSubredditConfig(subreddit="python")]
    )
    client = _httpx.AsyncClient(transport=_httpx.MockTransport(handler))
    scraper = RedditScraper(config, client)
    result = _asyncio.run(scraper._reddit_get("https://oauth.reddit.com/x", {}))
    _asyncio.run(client.aclose())
    assert len(seen) == 2
    assert result == {"kind": "Listing", "data": {"children": []}}
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_throttle.py -k scraper tests/test_reddit.py -v`
Expected: FAIL，`TypeError: BaseScraper.__init__() got an unexpected keyword argument 'auth'`，以及 reddit 那条抛 `ValueError: invalid literal for int() with base 10`

- [ ] **Step 3: 改 `src/scrapers/base.py`（整体替换）**

```python
"""Base scraper interface."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, List, Optional
import httpx

from ..models import ContentItem
from .auth import AuthProvider, NullAuth
from .throttle import Throttle


class BaseScraper(ABC):
    """Abstract base class for all scrapers."""

    def __init__(
        self,
        config: dict,
        http_client: httpx.AsyncClient,
        throttle: Optional[Throttle] = None,
        auth: Optional[AuthProvider] = None,
    ):
        """Initialize scraper.

        Args:
            config: Scraper-specific configuration
            http_client: Shared async HTTP client
            throttle: Per-host rate limiter. Defaults to an unlimited one, so
                the fourteen existing scrapers keep their current behaviour
                without being touched.
            auth: Credential provider. Defaults to no credentials.
        """
        self.config = config
        self.client = http_client
        self.throttle = throttle if throttle is not None else Throttle()
        self.auth = auth if auth is not None else NullAuth()

    @abstractmethod
    async def fetch(self, since: datetime) -> List[ContentItem]:
        """Fetch content items published since the given time.

        Args:
            since: Only fetch items published after this time. A source whose
                spec declares `time_basis_default="unknown"` (hot lists,
                recommendation feeds) must not filter on it — an item with no
                publish time is not an old item.

        Returns:
            List[ContentItem]: Fetched content items
        """
        pass

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """One throttled, authenticated request.

        The auth provider's headers are merged into whatever the caller
        passed, so a scraper's own User-Agent survives. On 401/403 the
        provider gets exactly one chance to refresh (reload a cookie file); a
        second refusal is returned to the caller untouched.
        """
        headers = dict(kwargs.pop("headers", None) or {})
        headers.update(self.auth.headers())
        response = await self.throttle.request(
            self.client, method, url, headers=headers, **kwargs
        )
        if response.status_code in (401, 403) and self.auth.on_unauthorized():
            headers.update(self.auth.headers())
            response = await self.throttle.request(
                self.client, method, url, headers=headers, **kwargs
            )
        return response

    def _generate_id(self, source_type: str, subtype: str, native_id: str) -> str:
        """Generate unique content item ID.

        Args:
            source_type: Source type (github, hackernews, etc.)
            subtype: Content subtype (event, release, story, etc.)
            native_id: Native ID from the source platform

        Returns:
            str: Unique ID in format {source}:{subtype}:{native_id}
        """
        return f"{source_type}:{subtype}:{native_id}"
```

- [ ] **Step 4: 把 reddit 的 429 处理换成 `_request`**

`src/scrapers/reddit.py:531-552`（`_reddit_get` 里从 `try:` 到 `return response.json()` 的部分）替换为：

```python
    async def _reddit_get(self, url: str, params: dict) -> Optional[Any]:
        try:
            response = await self._request(
                "GET", url, params=params, headers=REDDIT_HEADERS, follow_redirects=True
            )
            if response.status_code == 403 and "/comments/" in url:
                logger.info(
                    "Reddit blocked comments request for %s; continuing without comments",
                    url,
                )
                return None
            if response.status_code == 403:
                raise RedditBlockedError(url)
            response.raise_for_status()
            return response.json()
        except RedditBlockedError:
            raise
```

`:552` 之后的 `except` 分支保持原样。原来手写的 429 分支整段删除——它现在由 `Throttle.request` 负责，且不再会因 HTTP-date 形式的 `Retry-After` 抛 `ValueError`。

- [ ] **Step 5: 把 telegram 的 429 处理换成 `_request`**

`src/scrapers/telegram.py:65-71`（`for web_base in ...` 循环体内的 `try:` 到 `return self._parse_channel_html(...)`）替换为：

```python
            try:
                response = await self._request(
                    "GET", url, headers=headers, follow_redirects=True, timeout=120.0
                )
                response.raise_for_status()
                return self._parse_channel_html(response.text, cfg, since)
```

- [ ] **Step 6: 统一 `follow_redirects=True`**

`src/orchestrator.py:761`：

```python
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
```

`src/orchestrator.py:1341`：

```python
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
```

（`:459` 已经是 `follow_redirects=True`，不动。三处不一致会造成"某个源在 CLI 里抓不到、在按需采集里抓得到"这种无法复现的现象。）

- [ ] **Step 7: 跑测试，确认通过**

Run: `uv run pytest tests/test_throttle.py tests/test_reddit.py tests/test_telegram.py tests/test_twitter.py -v`
Expected: PASS

- [ ] **Step 8: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 787 + 4 = 791

- [ ] **Step 9: 提交**

```bash
git add src/scrapers/base.py src/scrapers/reddit.py src/scrapers/telegram.py \
        src/orchestrator.py tests/test_throttle.py tests/test_reddit.py
git commit -m "Feat: inject throttle and auth into BaseScraper, drop the duplicate 429 code"
```

---

### Task 9: `src/sources/registry.py` 与注册表驱动的抓取循环

**Files:**
- Create: `src/sources/__init__.py`、`src/sources/registry.py`
- Modify: `src/orchestrator.py:14`（import）、`:19-33`（14 行静态 import → 精简）、`:761-845`（14 段 `if` → 循环）
- Test: `tests/test_source_registry.py`（追加 guard test B）

**Interfaces:**
- Consumes: `SOURCE_SPECS` / `SourceSpec` / `RateLimit`（Task 5）、`Throttle`（Task 6）、`BaseScraper`（Task 8）
- Produces:
  - `@dataclass(frozen=True) class BuildContext`：`extractors: Dict[str, Any] = {}`、`throttle: Optional[Throttle] = None`、`auth: Dict[str, AuthProvider] = {}`
  - `ScraperFactory = Callable[[Any, Optional[httpx.AsyncClient], BuildContext], Optional[BaseScraper]]`
  - `def simple(cls: type) -> ScraperFactory`
  - `SCRAPER_BINDINGS: Dict[str, ScraperFactory]`（14 项）
  - `def is_enabled(source_config: Any, spec: SourceSpec) -> bool`
  - `def build_throttle(specs: Iterable[SourceSpec] = SOURCE_SPECS) -> Throttle`
  - `API_HOSTS: Dict[str, tuple]`

- [ ] **Step 1: 写失败的测试（guard test B）**

追加到 `tests/test_source_registry.py`：

```python
# ------------------------------------------------------- guard test B (Task 9)
def test_every_spec_has_a_scraper_binding() -> None:
    from src.sources.registry import SCRAPER_BINDINGS

    assert set(SCRAPER_BINDINGS) == SPEC_KEYS


def test_every_binding_builds_with_a_default_config() -> None:
    import httpx

    from src.scrapers.base import BaseScraper
    from src.sources.registry import SCRAPER_BINDINGS, BuildContext

    config = SourcesConfig()
    client = httpx.AsyncClient()
    try:
        for spec in SOURCE_SPECS:
            source_config = getattr(config, spec.config_field, None)
            scraper = SCRAPER_BINDINGS[spec.key](source_config, client, BuildContext())
            assert isinstance(scraper, BaseScraper), spec.key
    finally:
        client.close()


def test_build_context_hands_the_throttle_to_the_scraper() -> None:
    import httpx

    from src.models import RateLimit
    from src.scrapers.throttle import Throttle
    from src.sources.registry import SCRAPER_BINDINGS, BuildContext

    throttle = Throttle(default=RateLimit(requests=1, per_seconds=2.0, jitter=0.0))
    scraper = SCRAPER_BINDINGS["hackernews"](
        SourcesConfig().hackernews, httpx.AsyncClient(), BuildContext(throttle=throttle)
    )
    assert scraper.throttle is throttle


def test_build_throttle_maps_declared_rate_limits_onto_hosts() -> None:
    from src.sources.registry import build_throttle

    throttle = build_throttle(SOURCE_SPECS)
    assert throttle.limit_for("https://api.bilibili.com/x").per_seconds == 2.0
    assert throttle.limit_for("https://api.github.com/") is None  # github has no limit


def test_is_enabled_matches_the_old_hardcoded_predicates() -> None:
    from src.sources.registry import is_enabled

    by_key = {spec.key: spec for spec in SOURCE_SPECS}
    config = SourcesConfig()
    assert is_enabled([], by_key["github"]) is False            # empty list
    assert is_enabled([object()], by_key["github"]) is True     # non-empty list
    assert is_enabled(config.hackernews, by_key["hackernews"]) is True
    assert is_enabled(None, by_key["twitter"]) is False
    assert is_enabled(config.reddit, by_key["reddit"]) is True


def test_importing_the_sources_package_does_not_pull_in_every_scraper() -> None:
    """`src/sources/__init__.py` must stay import-light."""
    import pathlib

    init = pathlib.Path("src/sources/__init__.py").read_text(encoding="utf-8")
    assert "from .registry" not in init
    assert "import registry" not in init


def test_fetch_all_sources_walks_every_enabled_source(monkeypatch) -> None:
    """The one end-to-end proof that the registry loop replaced all 14 ifs.

    Every scraper's fetch is allowed to fail — `_fetch_with_progress` swallows
    exceptions by design — so this asserts the loop *reached* each source under
    its declared label, not that any source returned data.
    """
    import asyncio
    from datetime import datetime, timezone

    import httpx
    from rich.console import Console

    from src.models import Config
    from src.orchestrator import HorizonOrchestrator

    config = Config.model_validate({
        "ai": {"provider": "openai", "model": "test", "api_key_env": "KEY"},
        "sources": {
            "github": [{"type": "user_events", "username": "alice"}],
            "hackernews": {"enabled": True},
            "rss": [{"name": "Feed", "url": "https://example.com/feed"}],
            "reddit": {"enabled": True, "subreddits": [{"subreddit": "python"}]},
            "telegram": {"enabled": True, "channels": [{"channel": "updates"}]},
            "twitter": {"enabled": True, "users": ["openai"]},
            "openbb": {"enabled": True, "watchlists": [{"name": "tech", "symbols": ["NVDA"]}]},
            "ossinsight": {"enabled": True},
            "gdelt": {"enabled": True},
            "google_news": {"enabled": True},
            "bilibili": {"enabled": True},
            "v2ex": {"enabled": True},
            "discourse": {"enabled": True, "sites": [{"base_url": "https://forum.test"}]},
            "youtube": {"enabled": True, "channels": [{"name": "c", "channel_id": "UCx"}]},
        },
    })
    config.corpus.enabled = False     # keep persist_to_corpus a no-op
    config.analysis.enabled = False

    orch = HorizonOrchestrator.__new__(HorizonOrchestrator)
    orch.config = config
    orch.console = Console(record=True, quiet=True)
    orch.icons = {"fetch": "*", "detail": "-"}
    orch.last_fetch_report = None
    orch._corpus = None

    real_client = httpx.AsyncClient

    def offline_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, json={})
        )
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", offline_client)

    reached = []
    original = HorizonOrchestrator._fetch_with_progress

    async def recording(self, name, scraper, since):
        reached.append(name)
        return await original(self, name, scraper, since)

    monkeypatch.setattr(HorizonOrchestrator, "_fetch_with_progress", recording)

    asyncio.run(orch.fetch_all_sources(datetime(2026, 9, 29, tzinfo=timezone.utc)))

    assert sorted(reached) == sorted(spec.label for spec in SOURCE_SPECS)
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_source_registry.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'src.sources'`

- [ ] **Step 3: 创建 `src/sources/__init__.py`**

```python
"""Source declarations that need to import both models and scrapers.

This package must stay import-light: `registry` pulls in every scraper, so
nothing under `models.py` or `corpus/` may import this package, and this file
must not re-export `registry`.
"""
```

- [ ] **Step 4: 创建 `src/sources/registry.py`**

```python
"""Which scraper serves which declared source.

Lives outside `models.py` because `models.py` may not import scrapers (they
all import models). Two sources do not fit a `key -> class` map, which is why
the values are factories:

  rss      needs a third argument, an ExtractorRegistry built from config
  twitter  picks between two classes on `cfg.mode`, and the Playwright one
           takes no http client at all
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional

import httpx

from ..models import SOURCE_SPECS, RateLimit, SourceSpec
from ..scrapers.auth import AuthProvider
from ..scrapers.base import BaseScraper
from ..scrapers.bilibili import BilibiliScraper
from ..scrapers.discourse import DiscourseScraper
from ..scrapers.gdelt import GDELTScraper
from ..scrapers.github import GitHubScraper
from ..scrapers.google_news import GoogleNewsScraper
from ..scrapers.hackernews import HackerNewsScraper
from ..scrapers.openbb import OpenBBScraper
from ..scrapers.ossinsight import OSSInsightScraper
from ..scrapers.reddit import RedditScraper
from ..scrapers.rss import RSSScraper
from ..scrapers.telegram import TelegramScraper
from ..scrapers.throttle import Throttle
from ..scrapers.twitter import TwitterScraper
from ..scrapers.twitter_playwright import TwitterPlaywrightScraper
from ..scrapers.v2ex import V2EXScraper
from ..scrapers.youtube import YouTubeScraper


@dataclass(frozen=True)
class BuildContext:
    """Everything a factory may need beyond the source's own config."""

    extractors: Dict[str, Any] = field(default_factory=dict)
    throttle: Optional[Throttle] = None
    auth: Dict[str, AuthProvider] = field(default_factory=dict)


ScraperFactory = Callable[
    [Any, Optional[httpx.AsyncClient], BuildContext], Optional[BaseScraper]
]


def simple(cls: type) -> ScraperFactory:
    """Adapt the common `Cls(config, client)` constructor to a factory."""

    def build(config: Any, client: Optional[httpx.AsyncClient], ctx: BuildContext):
        return cls(config, client)

    return build


def _build_rss(config: Any, client: Optional[httpx.AsyncClient], ctx: BuildContext):
    from ..extractors import ExtractorRegistry

    return RSSScraper(config, client, ExtractorRegistry(ctx.extractors))


def _build_twitter(config: Any, client: Optional[httpx.AsyncClient], ctx: BuildContext):
    if getattr(config, "mode", "apify") == "playwright":
        return TwitterPlaywrightScraper(config)
    return TwitterScraper(config, client)


SCRAPER_BINDINGS: Dict[str, ScraperFactory] = {
    "github": simple(GitHubScraper),
    "hackernews": simple(HackerNewsScraper),
    "rss": _build_rss,
    "reddit": simple(RedditScraper),
    "telegram": simple(TelegramScraper),
    "twitter": _build_twitter,
    "openbb": simple(OpenBBScraper),
    "ossinsight": simple(OSSInsightScraper),
    "gdelt": simple(GDELTScraper),
    "google_news": simple(GoogleNewsScraper),
    "bilibili": simple(BilibiliScraper),
    "v2ex": simple(V2EXScraper),
    "discourse": simple(DiscourseScraper),
    "youtube": simple(YouTubeScraper),
}

# Hosts each source actually talks to, so a declared RateLimit can be applied
# per host. Discourse is empty on purpose: its host comes from per-site
# config and is not known until the config is read.
API_HOSTS: Dict[str, tuple] = {
    "github": ("api.github.com",),
    "hackernews": ("hacker-news.firebaseio.com",),
    "reddit": ("oauth.reddit.com", "www.reddit.com", "old.reddit.com"),
    "telegram": ("t.me",),
    "bilibili": ("api.bilibili.com", "www.bilibili.com"),
    "v2ex": ("www.v2ex.com", "global.v2ex.co"),
    "youtube": ("www.youtube.com",),
    "discourse": (),
}


def is_enabled(source_config: Any, spec: SourceSpec) -> bool:
    """The predicate the fourteen hardcoded `if`s used to spell out."""
    if source_config is None:
        return False
    if spec.config_is_list:
        return bool(source_config)
    return bool(getattr(source_config, "enabled", False))


def build_throttle(specs: Iterable[SourceSpec] = SOURCE_SPECS) -> Throttle:
    """One throttle whose per-host limits come from the declared specs."""
    limits: Dict[str, RateLimit] = {}
    for spec in specs:
        if spec.rate_limit is None:
            continue
        for host in API_HOSTS.get(spec.key, ()):
            limits[host] = spec.rate_limit
    return Throttle(default=None, limits=limits)
```

- [ ] **Step 5: 把 `orchestrator.fetch_all_sources` 换成循环**

`src/orchestrator.py:19-33` 的 14 行 scraper import 删掉，换成：

```python
from .sources.registry import SCRAPER_BINDINGS, BuildContext, build_throttle, is_enabled
```

**但先跑一遍** `grep -n "Scraper\b" src/orchestrator.py` —— `:1349` 附近仍在直接用 `TwitterScraper`（回复展开那段）。为它单独保留：

```python
from .scrapers.twitter import TwitterScraper
```

（若 `TwitterPlaywrightScraper` 在别处只用于 `isinstance`/mode 判断，同样单独保留；grep 结果说了算，不要凭记忆。）

把 `:761-845` 从 `async with httpx.AsyncClient(...)` 到 `outcomes = await asyncio.gather(*tasks)` 之间的 14 段 `if` 全部替换为：

```python
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            ctx = BuildContext(
                extractors=self.config.extractors,
                throttle=build_throttle(),
            )
            tasks = []
            for spec in SOURCE_SPECS:
                source_config = getattr(self.config.sources, spec.config_field, None)
                if not is_enabled(source_config, spec):
                    continue
                scraper = SCRAPER_BINDINGS[spec.key](source_config, client, ctx)
                if scraper is None:
                    continue
                tasks.append(self._fetch_with_progress(spec.label, scraper, since))

            # Fetch all concurrently
            outcomes = await asyncio.gather(*tasks)
```

`src/orchestrator.py:14` 的 models import 补上 `SOURCE_SPECS`：

```python
from .models import SOURCE_SPECS, Config, ContentItem, Section
```

- [ ] **Step 6: 跑测试，确认通过**

Run: `uv run pytest tests/test_source_registry.py tests/test_fetch_reporting.py tests/test_category_wiring.py tests/test_mcp_adapter.py tests/test_web_panel.py -v`
Expected: PASS。`test_fetch_reporting.py` 尤其关键——它按显示名断言，`spec.label` 错一个字就会红。

- [ ] **Step 7: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 791 + 7 = 798

- [ ] **Step 8: 提交**

```bash
git add src/sources/ src/orchestrator.py tests/test_source_registry.py
git commit -m "Feat: drive source fetching from the registry instead of fourteen ifs"
```

---

### Task 10: HackerNews 迁移 —— P0 的核心验收（必须先红）

这是整个 P0 的**理由**。spec §1.1 与 §14.3 记录的现象：HN 链接帖的 `content` 100% 是评论，而 100% 都进了 `claimable`。

**Files:**
- Create: `tests/test_tier_guard.py`
- Modify: `src/scrapers/hackernews.py:11`（import）、`:100-144`（`_parse_story`）

**Interfaces:**
- Consumes: `Section`（Task 1）、`claimable_of`（Task 2）、`Corpus`（Task 3）、`ClaimAnalyzer` / `ClaimStore`（现有）
- Produces: 无新公共接口（只改 `_parse_story` 内部）

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_tier_guard.py`：

```python
"""P0 acceptance: crowd text must never reach the claimable layer.

The first test in here fails on `main` — that is the point. reddit,
hackernews and twitter all append "--- Top Comments ---", which is not one of
the five Chinese markers corpus/sections.py looks for, so their comments are
being graded as if the author wrote them.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from src.analysis.claims import Claim, ClaimAnalyzer, ClaimStore
from src.corpus.sections import claimable_of
from src.corpus.store import Corpus
from src.models import HackerNewsConfig, Section, SourceType
from src.scrapers.hackernews import HackerNewsScraper

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
STORY_ID = 999


def _hn_handler(with_text: bool = False):
    story = {
        "id": STORY_ID,
        "type": "story",
        "by": "submitter",
        "time": int(NOW.timestamp()),
        "title": "Show HN: a new parser",
        "url": "https://example.com/parser",
        "score": 400,
        "descendants": 3,
        "kids": [11, 12, 13],
    }
    if with_text:
        story.pop("url")
        story["title"] = "Ask HN: which parser"
        story["text"] = "I need a parser that survives malformed input."
    comments = {
        11: {"id": 11, "by": "stranger_a", "text": "the benchmark is rigged"},
        12: {"id": 12, "by": "stranger_b", "text": "no it isnt, here is data"},
        13: {"id": 13, "by": "stranger_c", "text": "<p>deleted account spam</p>"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/topstories.json"):
            return httpx.Response(200, json=[STORY_ID])
        if "/item/" in path:
            raw = int(path.rsplit("/", 1)[1].split(".")[0])
            if raw == STORY_ID:
                return httpx.Response(200, json=story)
            return httpx.Response(200, json=comments[raw])
        raise AssertionError(f"unexpected {request.url}")

    return handler


def _fetch_hn_item(with_text: bool = False):
    client = httpx.AsyncClient(transport=httpx.MockTransport(_hn_handler(with_text)))
    scraper = HackerNewsScraper(HackerNewsConfig(enabled=True, min_score=1), client)
    items = asyncio.run(scraper.fetch(NOW.replace(hour=0)))
    asyncio.run(client.aclose())
    assert len(items) == 1
    return items[0]


def test_hn_link_post_comments_are_not_claimable() -> None:
    item = _fetch_hn_item()
    claimable = claimable_of(item)
    assert "riged" not in claimable
    assert "stranger_a" not in claimable
    assert claimable == ""          # a link post has no author-written body


def test_hn_comments_become_community_sections_with_locators() -> None:
    item = _fetch_hn_item()
    assert [s.tier for s in item.sections] == ["community"] * 3
    assert [s.locator for s in item.sections] == ["#11", "#12", "#13"]
    assert [s.author for s in item.sections] == ["stranger_a", "stranger_b", "stranger_c"]


def test_hn_comments_stay_visible_in_content_for_the_panel() -> None:
    item = _fetch_hn_item()
    assert "the benchmark is rigged" in item.content
    assert "- @stranger_a:" in item.content


def test_hn_text_post_keeps_the_author_body_claimable() -> None:
    item = _fetch_hn_item(with_text=True)
    assert claimable_of(item) == "I need a parser that survives malformed input."
    assert [s.tier for s in item.sections][0] == "primary"


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    return Corpus(tmp_path / "corpus.db")


def test_hn_comments_never_enter_claim_fts(corpus: Corpus) -> None:
    corpus.add_items([_fetch_hn_item()])
    assert corpus.search("riged", limit=5, tier="claimable") == []
    assert corpus.search("riged", limit=5) != []      # panel keeps full recall


def test_hn_comments_do_not_inflate_independent_sources(corpus: Corpus) -> None:
    item = _fetch_hn_item()
    corpus.add_items([item])
    analyzer = ClaimAnalyzer(
        store=ClaimStore(corpus), corpus=corpus, client=None, claimable_only=True
    )
    claim = Claim(id="claim:hn:1", item_id=item.id, text="the benchmark is rigged")
    analyzer.store.upsert_claims([claim])
    analyzer.link_evidence(claim)
    analyzer.store.recompute_independence()
    stored = analyzer.store.get_claim(claim.id)
    linked = corpus._conn.execute(
        "SELECT COUNT(*) FROM claim_evidence WHERE claim_id=?", (claim.id,)
    ).fetchone()[0]
    assert linked <= 1                      # the origin item only, no crowd votes
    assert stored is not None and stored.independent_sources <= 1
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_tier_guard.py -v`
Expected: FAIL。关键是 `test_hn_link_post_comments_are_not_claimable`，失败信息应类似：

```
AssertionError: assert 'riged' not in '--- Top Comments ---\n\n[stranger_a]: the benchmark is rigged...'
```

这正是 spec §14.3 记录的现象。**把这条失败输出原样贴进 commit message**，它是 P0 存在理由的证据。

- [ ] **Step 3: 迁移 `src/scrapers/hackernews.py`**

`:11` 的 import 补 `Section`：

```python
from ..models import ContentItem, HackerNewsConfig, Section, SourceType
```

`_parse_story`（`:100-144`）整体替换：

```python
    def _parse_story(self, story: dict, comments: List[dict]) -> ContentItem:
        story_id = story["id"]
        title = story.get("title", "")
        url = story.get("url", f"https://news.ycombinator.com/item?id={story_id}")
        author = story.get("by", "unknown")
        published_at = datetime.fromtimestamp(story["time"], tz=timezone.utc)
        hn_discussion_url = f"https://news.ycombinator.com/item?id={story_id}"

        sections: List[Section] = []
        if story.get("text"):
            sections.append(Section(tier="primary", text=story["text"], author=author))
        for c in comments:
            text = re.sub(r"<[^>]+>", " ", c.get("text", "")).strip()
            if len(text) > 500:
                text = text[:497] + "..."
            if not text:
                continue
            sections.append(Section(
                tier="community",
                text=text,
                author=c.get("by", "anon"),
                locator=f"#{c.get('id')}",
            ))

        return ContentItem(
            id=self._generate_id("hackernews", "story", str(story_id)),
            source_type=SourceType.HACKERNEWS,
            title=title,
            url=url,
            author=author,
            published_at=published_at,
            sections=sections,
            profile=self.config.get("profile"),
            metadata={
                "score": story.get("score", 0),
                "descendants": story.get("descendants", 0),
                "type": story.get("type", "story"),
                "discussion_url": hn_discussion_url,
                "comment_count": len(comments),
                "category": self.config.get("category"),
            },
        )
```

`content=` 参数被**删掉**：Task 1 的 validator 会从 `sections` 派生它。对链接帖（无 `story.text`）而言，`content` 现在只含评论行，而 `claimable` 为空 —— 这两件事第一次分开了。

- [ ] **Step 4: 跑测试，确认通过**

Run: `uv run pytest tests/test_tier_guard.py -v`
Expected: PASS，7 passed

- [ ] **Step 5: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 798 + 7 = 805

若 `tests/test_cli.py` / `test_main.py` / `test_balanced_digest.py` 里有断言 HN item 的 `content` 含 `--- Top Comments ---`，改掉断言（改为断言 `- @author:` 前缀），**不要**为了让它绿而把标记加回去。

- [ ] **Step 6: 提交**

```bash
git add src/scrapers/hackernews.py tests/test_tier_guard.py
git commit -m "Fix: Hacker News comments were being graded as author assertions"
```

commit message 正文里贴 Step 2 的失败输出。

---

### Task 11: Reddit 与 Twitter 迁移

**Files:**
- Modify: `src/scrapers/reddit.py:11-13`（import）、`:455-462`（HTML 评论 dict 补 `id`）、`:488-520`（`_parse_post` 的 content 构造）
- Modify: `src/scrapers/twitter.py:168-201`（`fetch_replies_for_item` 返回类型）、`:203-244`（`_extract_reply_lines` → `_extract_reply_sections`）、`:246-266`（`append_discussion_content` → `append_discussion_sections`）
- Modify: `src/orchestrator.py:1351-1352`
- Modify: `tests/test_twitter.py:516-610`
- Test: `tests/test_tier_guard.py`（追加）

**Interfaces:**
- Consumes: `Section`、`ContentItem.rebuild_content()`（Task 1）、`claimable_of`（Task 2）
- Produces:
  - `TwitterScraper._extract_reply_sections(item: ContentItem, rows: list, max_replies: int) -> List[Section]`
  - `TwitterScraper.fetch_replies_for_item(item: ContentItem) -> List[Section]`（返回类型由 `List[str]` 变）
  - `TwitterScraper.append_discussion_sections(item: ContentItem, sections: List[Section]) -> bool`（静态方法，替换 `append_discussion_content`）

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_tier_guard.py`：

```python
# --------------------------------------------------------------------- reddit
def _reddit_item():
    from src.models import RedditConfig, RedditSubredditConfig
    from src.scrapers.reddit import RedditScraper

    post = {
        "kind": "t3",
        "data": {
            "id": "abc123", "name": "t3_abc123", "title": "A claim about parsers",
            "selftext": "The author asserts the parser is linear time.",
            "author": "op_user", "score": 90, "num_comments": 2,
            "created_utc": int(NOW.timestamp()), "subreddit": "python",
            "permalink": "/r/python/comments/abc123/a_claim_about_parsers/",
            "url": "https://example.com/post",
        },
    }
    listing = {"data": {"children": [
        {"kind": "t1", "data": {"id": "c1", "author": "stranger_a", "score": 42,
                                "body": "totally fake, never happened"}},
        {"kind": "t1", "data": {"id": "c2", "author": "stranger_b", "score": 7,
                                "body": "source is a lie"}},
    ]}}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "old.reddit.com" in url:
            return httpx.Response(500)               # force the JSON path
        if url.endswith("/comments/abc123.json"):
            return httpx.Response(200, json=[{"data": {"children": [post]}}, listing])
        if url.endswith("/hot.json"):
            return httpx.Response(200, json={"data": {"children": [post]}})
        return httpx.Response(200, json={})

    config = RedditConfig(enabled=True, fetch_comments=2,
                          subreddits=[RedditSubredditConfig(subreddit="python")])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    items = asyncio.run(RedditScraper(config, client).fetch(NOW.replace(hour=0)))
    asyncio.run(client.aclose())
    assert items, "reddit fixture produced no items"
    return items[0]


def test_reddit_comments_are_not_claimable() -> None:
    item = _reddit_item()
    claimable = claimable_of(item)
    assert "The author asserts" in claimable
    assert "never happened" not in claimable
    assert "stranger_a" not in claimable


def test_reddit_comments_become_community_sections_keeping_their_score() -> None:
    item = _reddit_item()
    community = [s for s in item.sections if s.tier == "community"]
    assert {s.author for s in community} == {"stranger_a", "stranger_b"}
    assert all(s.locator and s.locator.startswith("#") for s in community)
    assert {s.meta.get("score") for s in community} == {42, 7}


def test_reddit_no_longer_emits_the_english_marker() -> None:
    assert "--- Top Comments ---" not in (_reddit_item().content or "")


# -------------------------------------------------------------------- twitter
def _tweet(content: str = "the author's own tweet") -> "ContentItem":
    from src.models import ContentItem

    return ContentItem(
        id="twitter:tweet:42", source_type=SourceType.TWITTER, title="t",
        url="https://twitter.com/x/status/42", content=content, author="x",
        published_at=NOW, fetched_at=NOW, metadata={},
    )


def test_twitter_replies_are_not_claimable() -> None:
    from src.scrapers.twitter import TwitterScraper

    item = _tweet()
    sections = [Section(tier="community", text="reply text", author="alice",
                        locator="@alice/1", meta={"likes": 5})]
    assert TwitterScraper.append_discussion_sections(item, sections) is True
    assert claimable_of(item) == "the author's own tweet"
    assert "reply text" in item.content


def test_twitter_reply_appending_is_idempotent() -> None:
    from src.scrapers.twitter import TwitterScraper

    item = _tweet()
    sections = [Section(tier="community", text="r", author="a", locator="@a/1")]
    assert TwitterScraper.append_discussion_sections(item, sections) is True
    assert TwitterScraper.append_discussion_sections(item, sections) is False
    assert len(item.sections) == 1


def test_empty_reply_list_changes_nothing() -> None:
    from src.scrapers.twitter import TwitterScraper

    item = _tweet()
    assert TwitterScraper.append_discussion_sections(item, []) is False
    assert item.sections == []
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_tier_guard.py -k "reddit or twitter" -v`
Expected: FAIL。reddit 那两条失败于 `assert 'never happened' not in '...[stranger_a (42 pts)]: totally fake...'`；twitter 那三条失败于 `AttributeError: ... has no attribute 'append_discussion_sections'`。

- [ ] **Step 3: 迁移 `src/scrapers/reddit.py`**

`:13` 的 import 补 `Section`：

```python
from ..models import ContentItem, RedditConfig, Section, SourceType
```

（具体 import 行以文件现状为准：先 `grep -n "^from ..models" src/scrapers/reddit.py`，在既有名字里插入 `Section`，不要重写整行。）

`_fetch_comments_html` 里构造评论 dict 的地方（`:455-462`）补 `id`，让 HTML 路径与 JSON 路径的 locator 口径一致：

```python
            comments.append(
                {
                    "id": str(comment_el.get("data-fullname") or ""),
                    "author": str(comment_el.get("data-author") or "anon"),
                    "body": body,
                    "score": self._parse_int(comment_el.get("data-score"), default=0),
                }
            )
```

`_parse_post` 里从 `# Build content`（`:488`）到 `content = "\n\n".join(parts)`（`:507`）整段替换：

```python
        # Typed sections: the post body is the author's, the comments are
        # everybody else's, and the two must never share a tier.
        sections: List[Section] = []
        if post.get("selftext"):
            text = post["selftext"]
            if len(text) > 1500:
                text = text[:1497] + "..."
            sections.append(Section(tier="primary", text=text, author=author))

        for c in comments:
            body = (c.get("body") or "").strip()
            if len(body) > 500:
                body = body[:497] + "..."
            if not body:
                continue
            native = c.get("id")
            sections.append(Section(
                tier="community",
                text=body,
                author=c.get("author", "anon"),
                locator=f"#{native}" if native else None,
                meta={"score": c.get("score", 0)},
            ))
```

紧接着的 `return ContentItem(...)`：删掉 `content=content,` 这一行，加上 `sections=sections,`。其余字段与 `metadata` 一字不动。

- [ ] **Step 4: 迁移 `src/scrapers/twitter.py`**

`:203-244` 的 `_extract_reply_lines` 整体替换为：

```python
    def _extract_reply_sections(
        self, item: ContentItem, rows: list, max_replies: int
    ) -> List[Section]:
        """Convert scweet rows into community sections, best-liked first."""
        min_likes = max(self.config.reply_min_likes, 0)
        tweet_id = str(item.metadata.get("tweet_id") or "")
        own_author = (item.author or "").lstrip("@")
        candidates = []

        for row in rows:
            if not isinstance(row, dict) or row.get("noResults"):
                continue

            row_id = str(row.get("id") or "")
            if row_id.startswith("tweet-"):
                row_id = row_id[6:]
            if tweet_id and row_id == tweet_id:
                continue

            user = row.get("user") or {}
            handle = (
                user.get("handle")
                or row.get("handle")
                or user.get("username")
                or "unknown"
            )
            if handle and own_author and handle.lower() == own_author.lower():
                continue

            text = unescape((row.get("text") or "").strip())
            if not text:
                continue

            likes = int(row.get("favorite_count") or 0)
            replies = int(row.get("reply_count") or 0)
            if likes < min_likes:
                continue

            candidates.append((likes * 2 + replies, Section(
                tier="community",
                text=text[:280],
                author=handle,
                locator=f"@{handle}/{row_id}" if row_id else f"@{handle}",
                meta={"likes": likes, "replies": replies},
            )))

        candidates.sort(key=lambda pair: pair[0], reverse=True)
        return [section for _, section in candidates[:max_replies]]
```

`:201` 的返回改为：

```python
        return self._extract_reply_sections(item, rows, max_replies)
```

`:168` 的签名与 docstring 改为：

```python
    async def fetch_replies_for_item(self, item: ContentItem) -> List[Section]:
        """Fetch reply sections for one tweet using scweet search mode."""
```

`:246-266` 的 `append_discussion_content` 整体替换为：

```python
    @staticmethod
    def append_discussion_sections(item: ContentItem, sections: List[Section]) -> bool:
        """Attach reply sections to an item, skipping any already present.

        Replies land in `sections`, not in a marker-delimited tail of
        `content`: the marker the old version wrote ("--- Top Comments ---")
        was invisible to the claim pipeline's tiering, so strangers' replies
        were being graded as the author's own assertions.
        """
        if not sections:
            return False
        known = {(s.author, s.locator) for s in item.sections}
        fresh = [s for s in sections if (s.author, s.locator) not in known]
        if not fresh:
            return False
        item.sections.extend(fresh)
        item.rebuild_content()
        return True
```

`:11` 附近的 models import 补 `Section`（同样先 grep 现状再插入名字）。

- [ ] **Step 5: 跟随调用点**

`src/orchestrator.py:1351-1352`：

```python
                    reply_sections = await scraper.fetch_replies_for_item(item)
                    if TwitterScraper.append_discussion_sections(item, reply_sections):
```

（`:1352` 之后那条 console 输出如果打印了 `reply_lines` 的长度，改名成 `reply_sections` 即可，语义不变。）

- [ ] **Step 6: 改掉钉住泄漏行为的三条现有断言**

`tests/test_twitter.py:516` 起，`test_fetch_replies_appends_top_comments` 改名为 `test_fetch_replies_returns_community_sections`，把变量名 `reply_lines` 换成 `reply_sections`，并把末尾三条断言换成：

```python
    # min_likes=1 filters out dave (0 likes); max 3 returned sorted by score
    assert len(reply_sections) == 3
    # alice (20 likes) should be first
    assert reply_sections[0].author == "alice"
    assert "Interesting take!" in reply_sections[0].text
    # dave (0 likes) filtered out
    assert all(s.author != "dave" for s in reply_sections)
    assert all(s.tier == "community" for s in reply_sections)
```

`tests/test_twitter.py:574` 的 `test_append_discussion_content_adds_marker` 整体替换为：

```python
def test_append_discussion_sections_keeps_replies_out_of_claimable():
    from src.corpus.sections import claimable_of
    from src.models import ContentItem, Section, SourceType

    item = ContentItem(
        id="twitter:tweet:1",
        source_type=SourceType.TWITTER,
        title="test",
        url="https://twitter.com/x/status/1",
        content="original text",
        author="x",
        published_at=datetime.now(timezone.utc),
        metadata={},
    )
    changed = TwitterScraper.append_discussion_sections(item, [
        Section(tier="community", text="reply text", author="alice",
                locator="@alice/1", meta={"likes": 5}),
    ])
    assert changed is True
    assert "--- Top Comments ---" not in item.content
    assert "reply text" in item.content
    assert claimable_of(item) == "original text"
```

`tests/test_twitter.py:593` 的 `test_append_discussion_content_empty_lines_no_change` 改名为 `test_append_discussion_sections_empty_list_no_change`，调用改为：

```python
    changed = TwitterScraper.append_discussion_sections(item, [])
```

其后的断言 `assert changed is False` 与 `assert item.content == "..."` 保留，另加一条 `assert item.sections == []`。

`tests/test_twitter.py:630` 的 `fetch_replies_for_item` 调用如果断言了返回是字符串列表，改成断言 `Section` 列表（先看该测试的意图再改，不要盲改）。

`tests/test_reddit.py:161` 的 `assert "Top Comments" not in (items[0].content or "")` **不用改**——它断言的是"不含"，迁移后依然成立。

Task 12 的守护测试只扫**代码行**（跳过以 `#` 或引号开头的行），所以 `twitter.py:248` 那句提到 marker 的 docstring 不会被误判。但 Step 4 已经把整个函数替换掉，新 docstring 不要再写回 `--- Top Comments ---` 字面量——它读起来像在说这个标记还存在。

- [ ] **Step 7: 跑测试，确认通过**

Run: `uv run pytest tests/test_tier_guard.py tests/test_twitter.py tests/test_reddit.py -v`
Expected: PASS

- [ ] **Step 8: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 805 + 7 = 812

- [ ] **Step 9: 提交**

```bash
git add src/scrapers/reddit.py src/scrapers/twitter.py src/orchestrator.py \
        tests/test_tier_guard.py tests/test_twitter.py
git commit -m "Fix: Reddit and Twitter replies were being graded as author assertions"
```

---

### Task 12: Discourse / V2EX / Bilibili 迁移，加上"不许再有标记"守护测试

**Files:**
- Modify: `src/scrapers/discourse.py:25`（import）、`:128-174`（`_build_topic`）
- Modify: `src/scrapers/v2ex.py`（import）、`:86-112`（`_build_item`）
- Modify: `src/scrapers/bilibili.py:14`（import）、`:76-118`（`_build_item`）、`:166-184`（`_fetch_comments` 补 `rpid`）
- Modify: `tests/test_discourse.py:104`、`tests/test_bilibili.py:99`、`:171-199`
- Test: `tests/test_tier_guard.py`（追加）

**Interfaces:**
- Consumes: `Section`（Task 1）、`claimable_of`（Task 2）
- Produces: 无新公共接口

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_tier_guard.py`：

```python
# ------------------------------------------------------------------- discourse
def _discourse_item():
    from src.models import DiscourseConfig
    from src.scrapers.discourse import DiscourseScraper
    from unittest.mock import AsyncMock, MagicMock

    latest = {"topic_list": {"topics": [{
        "id": 7, "fancy_title": "为什么我的构建失败了", "slug": "why-build-fails",
        "created_at": "2026-09-29T09:00:00.000Z", "posts_count": 3,
        "views": 250, "like_count": 12, "tags": ["help"], "excerpt": "列表页摘要",
    }]}}
    thread = {"post_stream": {"posts": [
        {"username": "asker", "post_number": 1,
         "cooked": "<p>编译器报 <code>error[E0502]</code></p>"},
        {"username": "helper", "post_number": 2, "cooked": "<p>借用冲突，试试 clone</p>"},
        {"username": "asker", "post_number": 3, "cooked": "<p>解决了，谢谢</p>"},
    ]}}

    async def _get(url, params=None, **kwargs):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = latest if url.endswith("/latest.json") else thread
        return response

    client = AsyncMock()
    client.get.side_effect = _get
    config = DiscourseConfig(enabled=True, sites=[{"base_url": "https://forum.test"}],
                             fetch_replies=5)
    items = asyncio.run(DiscourseScraper(config, client).fetch(NOW.replace(hour=0)))
    assert len(items) == 1
    return items[0]


def test_discourse_floors_become_community_sections_with_post_number_locators() -> None:
    item = _discourse_item()
    assert [s.tier for s in item.sections] == ["primary", "community", "community"]
    assert [s.locator for s in item.sections] == ["#1", "#2", "#3"]
    assert [s.author for s in item.sections] == ["asker", "helper", "asker"]


def test_discourse_floor_text_is_not_claimable() -> None:
    claimable = claimable_of(_discourse_item())
    assert "error[E0502]" in claimable
    assert "借用冲突" not in claimable


# ------------------------------------------------- no scraper may emit markers
def test_no_scraper_emits_a_tier_marker_any_more() -> None:
    """Guard: a seventh emitter must not be able to grow quietly.

    Tiering by string convention is what let three sources leak. This test
    fails the moment any scraper concatenates a marker again — Chinese or
    English — instead of declaring a Section.
    """
    import pathlib
    import re

    forbidden = re.compile(r"【[^】]{2,12}】|--- Top Comments ---")
    offenders = []
    for path in sorted(pathlib.Path("src/scrapers").glob("*.py")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            # Comments and docstring lines may discuss the old markers; only
            # code that concatenates one into a body can reintroduce the leak.
            if stripped.startswith(("#", '"', "'")):
                continue
            if forbidden.search(line):
                offenders.append(f"{path}:{number}: {stripped[:80]}")
    assert offenders == []
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_tier_guard.py -k "discourse or marker" -v`
Expected: FAIL。`test_discourse_floors_become_community_sections_with_post_number_locators` 失败于 `item.sections == []`；`test_no_scraper_emits_a_tier_marker_any_more` 失败并列出 `discourse.py:155`、`v2ex.py:109`、`bilibili.py:106`、`bilibili.py:114`（reddit / twitter / hackernews 已在 Task 10–11 清掉，若它们仍出现在列表里，说明前面的 Task 没做干净，回去补）。

- [ ] **Step 3: 迁移 `src/scrapers/discourse.py`**

`:25` 的 import 补 `Section`：

```python
from ..models import ContentItem, Section, SourceType
```

`_build_topic`（`:128-174`）整体替换：

```python
    async def _build_topic(
        self,
        base: str,
        site_name: str,
        topic: Dict[str, Any],
        created: datetime,
    ) -> Optional[ContentItem]:
        topic_id = int(topic["id"])
        thread = await self._get_json(f"{base}/t/{topic_id}.json")
        title = (topic.get("fancy_title") or topic.get("title") or "").strip()
        sections: List[Section] = []
        if not isinstance(thread, dict):
            excerpt = (topic.get("excerpt") or "").strip()
            author = (topic.get("posters") or [{}])[0].get("description") or ""
            text = _html_to_text(excerpt, self.post_chars)
            if text:
                sections.append(Section(
                    tier="primary", text=text, author=author or None, locator="#1"
                ))
        else:
            posts = thread.get("post_stream", {}).get("posts", [])
            first = posts[0] if posts else {}
            author = first.get("username", "")
            body = _html_to_text(first.get("cooked", ""), self.post_chars)
            if body:
                sections.append(Section(
                    tier="primary", text=body, author=author or None,
                    locator=f"#{first.get('post_number', 1)}",
                ))
            for post in posts[1 : 1 + self.fetch_replies]:
                text = _html_to_text(post.get("cooked", ""), 400)
                if not text:
                    continue
                sections.append(Section(
                    tier="community",
                    text=text,
                    author=post.get("username") or None,
                    locator=f"#{post.get('post_number')}",
                ))

        return ContentItem(
            id=self._generate_id("discourse", site_name, str(topic_id)),
            source_type=SourceType.DISCOURSE,
            title=title,
            url=f"{base}/t/{topic_id}",
            author=(sections[0].author if sections else None),
            published_at=created,
            sections=sections,
            metadata={
                "site": site_name,
                "slug": topic.get("slug"),
                "posts": topic.get("posts_count", 0),
                "views": topic.get("views", 0),
                "likes": topic.get("like_count", 0),
                "tags": topic.get("tags") or [],
                "topic_id": topic_id,
            },
        )
```

- [ ] **Step 4: 迁移 `src/scrapers/v2ex.py`**

models import 补 `Section`（先 `grep -n "^from ..models" src/scrapers/v2ex.py` 看现状）。

`:86-112` 里，把 `content = (topic.get("content") or "").strip()` 与 `ContentItem(... content=content ...)` 以及其后的回复拼接块，整体改为：

```python
        member = topic.get("member") or {}
        node = topic.get("node") or {}
        author = member.get("username")
        body = (topic.get("content") or "").strip()
        # Plain `content` is often empty for API results; fall back to
        # rendered text stripped of tags would need bs4 — keep raw title+node.
        sections: List[Section] = []
        if body:
            sections.append(Section(tier="primary", text=body, author=author))
        item = ContentItem(
            id=self._generate_id("v2ex", "topic", str(tid)),
            source_type=SourceType.V2EX,
            title=(topic.get("title") or "").strip(),
            url=topic.get("url") or f"{self.base_url}/t/{tid}",
            author=author,
            published_at=created,
            sections=sections,
            metadata={
                "node": node.get("title"),
                "node_slug": node.get("slug"),
                "replies": topic.get("replies", 0),
                "last_reply_by": topic.get("last_reply_by"),
                "topic_id": tid,
            },
            profile=self.profile,
        )
        if self.fetch_replies > 0:
            for reply in await self._fetch_replies(tid):
                item.sections.append(Section(
                    tier="community",
                    text=reply["text"],
                    author=reply["user"] or None,
                    locator=f"#{reply['id']}" if reply.get("id") else None,
                ))
            item.rebuild_content()
        return item
```

`_fetch_replies`（`:115-131`）里构造 dict 的地方补 `id`：

```python
            if content:
                out.append(
                    {
                        "id": reply.get("id"),
                        "user": user,
                        "text": content if len(content) <= 300 else content[:300] + "…",
                    }
                )
```

`List[Dict[str, str]]` 的返回标注改成 `List[Dict[str, Any]]`（`id` 是 int），并在文件顶部确认 `Any` 已 import。

- [ ] **Step 5: 迁移 `src/scrapers/bilibili.py`**

`:14` 的 import 补 `Section`：

```python
from ..models import ContentItem, Section, SourceType
```

`_build_item`（`:62-118`）里，从 `owner = rec.get("owner", {})` 到函数结尾整体替换为：

```python
        owner = rec.get("owner", {})
        owner_name = owner.get("name")
        sections: List[Section] = []
        description = self._clean_desc(rec.get("desc", ""))
        if description:
            sections.append(Section(tier="primary", text=description, author=owner_name))

        item = ContentItem(
            id=self._generate_id("bilibili", "video", bvid),
            source_type=SourceType.BILIBILI,
            title=rec.get("title", "").strip(),
            url=VIDEO_URL.format(bvid=bvid),
            author=owner_name,
            published_at=published_at,
            sections=sections,
            metadata={
                "aid": rec.get("aid"),
                "cid": stat.get("cid") or rec.get("cid"),
                "bvid": bvid,
                "tname": rec.get("tname"),
                "views": views,
                "likes": stat.get("like", 0),
                "coins": stat.get("coin", 0),
                "favorites": stat.get("favorite", 0),
                "danmaku": stat.get("danmaku", 0),
                "reply_count": stat.get("reply", 0),
                "duration_sec": rec.get("duration"),
                "up_mid": owner.get("mid"),
            },
        )

        if self.transcript_chars > 0:
            transcript = await self._fetch_transcript(rec.get("aid"), bvid)
            if transcript:
                # A creator-authored CC track is the author's own words, so it
                # stays claimable — but `transcript` records that it arrived
                # through speech recognition, which P1 discounts.
                item.sections.append(Section(
                    tier="primary", text=transcript, author=owner_name,
                    provenance="transcript",
                ))
                item.metadata["has_transcript"] = True

        if self.fetch_comments > 0:
            for comment in await self._fetch_comments(rec.get("aid"), bvid):
                item.sections.append(Section(
                    tier="community",
                    text=comment["text"],
                    author=comment["user"] or None,
                    locator=f"#{comment['rpid']}" if comment.get("rpid") else None,
                    meta={"likes": comment.get("likes", 0)},
                ))

        item.rebuild_content()
        return item
```

`_fetch_comments`（`:166-184`）里构造 dict 的地方补 `rpid` 与 `likes`：

```python
        for reply in replies[: self.fetch_comments]:
            content = (reply.get("content", {}) or {}).get("message", "").strip()
            user = (reply.get("member", {}) or {}).get("uname", "")
            if content:
                out.append({
                    "rpid": reply.get("rpid"),
                    "user": user,
                    "text": self._truncate(content, 300),
                    "likes": reply.get("like", 0),
                })
```

返回标注改成 `List[Dict[str, Any]]`，并确认 `Any` 已在文件顶部 import（`:10` 已经有）。

- [ ] **Step 6: 改掉钉住标记的现有断言**

`tests/test_discourse.py:104`：删掉 `assert "【楼层讨论】" in item.content`，换成：

```python
    assert [s.tier for s in item.sections] == ["primary", "community", "community"]
```

（`:105-106` 的 `assert "- @helper: 借用冲突，试试 clone" in item.content` 与 `assert "- @op: 解决了，谢谢" in item.content` **不用改**：`sections_to_content` 对 community 段渲染的正是 `- @{author}: {text}`。）

`tests/test_bilibili.py:99`：删掉 `assert "【评论区 Top】" in item.content`，换成：

```python
    assert [s.tier for s in item.sections].count("community") == 2
```

（`:100-102` 三条不用改，理由同上。`:123-124` 的 `assert "评论区" not in content` / `assert "字幕" not in content` 迁移后仍然成立。）

`tests/test_bilibili.py:172`：删掉 `assert "【视频字幕节选】" in content`，换成：

```python
    transcripts = [s for s in items[0].sections if s.provenance == "transcript"]
    assert len(transcripts) == 1
    assert transcripts[0].tier == "primary"
```

`tests/test_bilibili.py:198-199` 那两行 `split("【视频字幕节选】\n", 1)` / `split("\n\n【评论区", 1)` 改为直接读 sections：

```python
    transcript = next(s for s in items[0].sections if s.provenance == "transcript")
    block = transcript.text
```

（其后基于 `block` 的断言保留；`block` 现在是纯字幕文本，不含标记行也不含评论区尾巴，原本用 split 排除的东西已经不存在了。）

- [ ] **Step 7: 跑测试，确认通过**

Run: `uv run pytest tests/test_tier_guard.py tests/test_discourse.py tests/test_v2ex.py tests/test_bilibili.py -v`
Expected: PASS

- [ ] **Step 8: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 812 + 4 = 816

- [ ] **Step 9: 提交**

```bash
git add src/scrapers/discourse.py src/scrapers/v2ex.py src/scrapers/bilibili.py \
        tests/test_tier_guard.py tests/test_discourse.py tests/test_bilibili.py
git commit -m "Feat: declare tiers in Discourse, V2EX and Bilibili; forbid markers in scrapers"
```

---

### Task 13: 日报侧换轨 —— `split_item_content`

**Files:**
- Modify: `src/processing/content.py:1-23`
- Modify: `src/ai/analyzer.py:18`（import）、`:109`
- Modify: `src/ai/prompting/enrichment.py:4`（import）、`:155`
- Test: `tests/test_content_selection.py`（追加）

**Interfaces:**
- Consumes: `ContentItem.sections`（Task 1）
- Produces:
  - `def split_item_content(item: ContentItem) -> ContentParts`
  - `split_content(content: str | None) -> ContentParts` 保留不变（legacy 与 P1 消融 A 档要用）

**为什么必须做**（spec §9.13）：emitter 迁移后 `content` 里不再有 `--- Top Comments ---`，`split_content` 会把评论并进 `main`，日报摘要因此**开始引用陌生人评论**。这是 P0 唯一一处"不改就会改坏上游"的地方。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_content_selection.py`：

```python
def test_split_item_content_uses_sections_when_present() -> None:
    from datetime import datetime, timezone

    from src.models import ContentItem, Section, SourceType
    from src.processing.content import split_item_content

    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    item = ContentItem(
        id="c:1", source_type=SourceType.HACKERNEWS, title="t",
        url="https://e.com/1", published_at=now, fetched_at=now,
        sections=[
            Section(tier="primary", text="Article body"),
            Section(tier="community", text="Useful reply", author="stranger"),
        ],
    )
    parts = split_item_content(item)
    assert parts.main == "Article body"
    assert parts.comments == "- @stranger: Useful reply"


def test_split_item_content_falls_back_to_the_marker_path() -> None:
    from datetime import datetime, timezone

    from src.models import ContentItem, SourceType
    from src.processing.content import split_item_content

    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    item = ContentItem(
        id="c:2", source_type=SourceType.RSS, title="t", url="https://e.com/2",
        content="Article body\n\n--- Top Comments ---\n\nLegacy reply",
        published_at=now, fetched_at=now,
    )
    parts = split_item_content(item)
    assert parts.main == "Article body"
    assert parts.comments == "Legacy reply"


def test_split_item_content_of_a_comment_only_item_has_no_main() -> None:
    """The HN link post: nothing the author wrote, so nothing to summarise."""
    from datetime import datetime, timezone

    from src.models import ContentItem, Section, SourceType
    from src.processing.content import split_item_content

    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    item = ContentItem(
        id="c:3", source_type=SourceType.HACKERNEWS, title="t",
        url="https://e.com/3", published_at=now, fetched_at=now,
        sections=[Section(tier="community", text="stranger talk", author="s")],
    )
    assert split_item_content(item).main == ""
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_content_selection.py -v`
Expected: FAIL，`ImportError: cannot import name 'split_item_content' from 'src.processing.content'`

- [ ] **Step 3: 实现**

`src/processing/content.py`：import 段改为

```python
"""Select bounded source content for profile-driven AI stages."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # avoids a real import cycle: models is imported by everything
    from ..models import ContentItem


COMMENTS_MARKER = "--- Top Comments ---"
```

（`ContentItem` 只在标注里出现，用 `TYPE_CHECKING` 就够；`processing/content.py` 被 `ai/analyzer.py` 与 `ai/prompting/enrichment.py` import，运行时引入 models 没有必要。）

在 `split_content` 之后插入：

```python
def split_item_content(item: "ContentItem") -> ContentParts:
    """Separate an item's author layer from its crowd layer.

    Prefers the typed sections a migrated scraper declares. Falls back to the
    marker scan for items that predate sections, so the digest path and the
    claim path cannot drift apart again the way they did when only one of
    them knew about "--- Top Comments ---".
    """
    if item.sections:
        main = "\n\n".join(
            s.text.strip() for s in item.sections
            if s.tier == "primary" and s.asserted and s.text.strip()
        ).strip()
        comments = "\n\n".join(
            f"- @{s.author}: {s.text.strip()}" if s.author else s.text.strip()
            for s in item.sections
            if s.tier == "community" and s.text.strip()
        ).strip()
        return ContentParts(main=main, comments=comments)
    return split_content(item.content)
```

`src/ai/analyzer.py:18` 的 import 改为：

```python
from ..processing.content import select_content, split_item_content
```

`:109`：

```python
        content_parts = split_item_content(item)
```

`src/ai/prompting/enrichment.py:4`：

```python
from ...processing.content import select_content, split_item_content
```

`:155`：

```python
    parts = split_item_content(item)
```

（两处调用的下一行都把 `content_parts.main` / `parts.main` 交给 `select_content`，签名不变。）

- [ ] **Step 4: 跑测试，确认通过**

Run: `uv run pytest tests/test_content_selection.py tests/test_analyzer.py tests/test_enricher.py tests/test_prompting.py -v`
Expected: PASS。`test_content_selection.py` 原有的 `test_split_content_separates_appended_comments` 必须仍绿——`split_content` 一个字没改。

- [ ] **Step 5: 跑全量**

Run: `uv run pytest -q`
Expected: 全绿，collected = 816 + 3 = 819

- [ ] **Step 6: 提交**

```bash
git add src/processing/content.py src/ai/analyzer.py src/ai/prompting/enrichment.py \
        tests/test_content_selection.py
git commit -m "Fix: the digest path read tiers from a marker the scrapers no longer write"
```

---

### Task 14: 分析阶段跳过已见 id，全量回归，文档收口

**Files:**
- Modify: `src/orchestrator.py`（`class HorizonOrchestrator:` 类体开头加类级默认值）、`:278-309`（`persist_to_corpus`）、`:510-529`（`analyze_claims`）
- Modify: `docs/evaluation.md`（记录分层判据已从 marker 换成 sections）
- Test: `tests/test_orchestrator_claims.py`（追加）

**Interfaces:**
- Consumes: `Corpus.known_ids`（Task 3）
- Produces: `HorizonOrchestrator.last_new_item_ids: frozenset`（类级默认 `frozenset()`；`persist_to_corpus` 写入，`analyze_claims` 读取）

**为什么**（spec §3.1 末段）：`time_basis="unknown"` 的条目没有时间门，每轮都会被重抓。`INSERT OR IGNORE` 保证不重复入库，但**不保证不重复烧 LLM 预算** —— 必须在分析阶段跳过已见过的 id。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_orchestrator_claims.py`。该文件已有 `make_orchestrator(tmp_path, monkeypatch)`（`:23-40`，用 `HorizonOrchestrator.__new__` 构造、`Console(record=True, quiet=True)`）与 `make_item(idx, title, content)`（`:41-51`，id 形如 `wire:{idx}`），**直接复用，不要新造 helper**：

```python
def test_analyze_claims_skips_items_the_corpus_has_already_seen(tmp_path, monkeypatch):
    """A re-fetched item must not buy a second extraction pass.

    This is the guard spec §3.1 asks for: an item whose source has no publish
    time (time_basis="unknown") has no `since` filter, so it comes back every
    run. INSERT OR IGNORE stops the duplicate row, not the duplicate spend.
    """
    orch = make_orchestrator(tmp_path, monkeypatch)
    calls: list[str] = []

    class RecordingAnalyzer:
        llm_calls = 0

        class store:
            @staticmethod
            def stats():
                return {"claims": 0, "multi_source": 0}

        async def extract_claims(self, item):
            calls.append(item.id)
            return []

        def link_all_pending(self):
            return 0

        async def grade_pending(self, max_calls):
            return 0

    monkeypatch.setattr(
        HorizonOrchestrator, "_get_claim_analyzer", lambda self: RecordingAnalyzer()
    )

    def timeless(idx: str) -> ContentItem:
        return ContentItem(
            id=f"seen:{idx}", source_type=SourceType.V2EX, title=f"t{idx}",
            url=f"https://e.com/{idx}", content=f"body {idx}",
            published_at=None, fetched_at=NOW,      # -> time_basis "unknown"
        )

    first, second = timeless("1"), timeless("2")
    assert first.time_basis == "unknown"

    orch.persist_to_corpus([first, second], NOW)
    asyncio.run(orch.analyze_claims([first, second]))
    assert sorted(calls) == ["seen:1", "seen:2"]

    calls.clear()
    orch.persist_to_corpus([first, second], NOW)      # nothing new is stored
    asyncio.run(orch.analyze_claims([first, second]))
    assert calls == []

    calls.clear()
    third = timeless("3")
    orch.persist_to_corpus([first, third], NOW)       # only `third` is new
    asyncio.run(orch.analyze_claims([first, third]))
    assert calls == ["seen:3"]

    orch._get_corpus().close()


def test_linking_and_grading_still_run_when_nothing_is_new(tmp_path, monkeypatch):
    """Filtering extraction must not skip the free, resumable stages."""
    orch = make_orchestrator(tmp_path, monkeypatch)
    ran = []

    class RecordingAnalyzer:
        llm_calls = 0

        class store:
            @staticmethod
            def stats():
                return {"claims": 0, "multi_source": 0}

        async def extract_claims(self, item):
            ran.append(("extract", item.id))
            return []

        def link_all_pending(self):
            ran.append(("link",))
            return 0

        async def grade_pending(self, max_calls):
            ran.append(("grade",))
            return 0

    monkeypatch.setattr(
        HorizonOrchestrator, "_get_claim_analyzer", lambda self: RecordingAnalyzer()
    )

    asyncio.run(orch.analyze_claims([make_item("ghost", "t", "b")]))
    assert ("link",) in ran
    assert ("grade",) in ran
    assert not [r for r in ran if r[0] == "extract"]
    orch._get_corpus().close()
```

第二条测试钉住的是本 Task 最容易做错的地方：**不要**因为"没有新条目"就提前 return。

- [ ] **Step 2: 跑测试，确认它失败**

Run: `uv run pytest tests/test_orchestrator_claims.py -k "already_seen or nothing_is_new" -v`
Expected: FAIL。第一条失败于第二轮 `calls` 仍是 `["seen:1", "seen:2"]`（`assert [] == [...]`）；第二条失败于 `AttributeError: 'HorizonOrchestrator' object has no attribute 'last_new_item_ids'`（Step 3 的类级默认值就是为它加的）。

- [ ] **Step 3: 实现**

在 `class HorizonOrchestrator:` 的类体开头（`__init__` 之前）加**类级**默认值，而不是在 `__init__` 里赋实例属性：

```python
    #: Ids stored by the most recent persist_to_corpus. Class-level so that an
    #: orchestrator built via __new__ — which tests/test_orchestrator_claims.py
    #: and test_balanced_digest.py both do — never hits an AttributeError.
    last_new_item_ids: frozenset = frozenset()
```

（`tests/test_orchestrator_claims.py:74-79` 的 `test_analyze_claims_disabled_by_config` 就是**不调** `persist_to_corpus` 直接调 `analyze_claims` 的，且它走 `__new__`。放在 `__init__` 里，这条测试今天能过纯属运气——它在 `analyzer is None` 那一步就返回了。别依赖这个运气。）

`persist_to_corpus`（`:278-309`）整体替换。**注意 `_get_corpus()` 是缓存实例（`:276 return self._corpus`），今天的实现没有也不该有 `corpus.close()`** —— 别顺手加 `finally` 关掉它，那会破坏后续阶段：

```python
    def persist_to_corpus(
        self, items: List[ContentItem], since: datetime
    ) -> frozenset:
        """Store fetched items in the evidence corpus (best-effort).

        Corpus failures must never break the daily pipeline, so errors are
        reported and swallowed; the run simply behaves like stateless Horizon.

        Returns the ids that were newly stored. An item whose source has no
        publish time is re-fetched every run, and `INSERT OR IGNORE` alone
        would let it buy a fresh extraction pass each time.
        """
        self.last_new_item_ids = frozenset()
        try:
            corpus = self._get_corpus()
            if corpus is None:
                return frozenset()
            candidate_ids = [item.id for item in items]
            already = corpus.known_ids(candidate_ids)
            new_ids = frozenset(i for i in candidate_ids if i not in already)
            run_id = corpus.begin_run(since)
            new_count = corpus.add_items(items, run_id)
            corpus.recompute_clusters(
                max_distance=self.config.corpus.cluster_max_distance,
                lookback_rows=self.config.corpus.cluster_lookback_rows,
            )
            corpus.finish_run(
                run_id,
                items_new=new_count,
                items_total_seen=len(items),
            )
            self.last_new_item_ids = new_ids
            self.console.print(
                f"{self.icons['fetched']} Corpus: +{new_count} new items "
                f"({corpus.stats()['items']} total)\n"
            )
            return new_ids
        except Exception as exc:
            self.console.print(
                f"[yellow]Corpus persistence failed (pipeline continues): {exc}[/yellow]\n"
            )
            return frozenset()
```

`analyze_claims`（`:510-529`）只改**一处**：把 `ranked` 的来源从 `items` 换成 `fresh`。

```python
            analyzer = self._get_claim_analyzer()
            if analyzer is None:
                return
            fresh = [i for i in items if i.id in self.last_new_item_ids]
            ranked = sorted(
                fresh,
                key=lambda i: (i.processing.analysis.score or 0.0) if i.processing and i.processing.analysis else 0.0,
                reverse=True,
            )
            targets = ranked[: self.config.analysis.extract_top_items]
```

`:527` 之后的 `extracted = 0` / `for item in targets` / `link_all_pending()` / `grade_pending(...)` / console 输出**逐字保留**。

**⚠ 不要加 `if not fresh: return`。** 那会一并跳过 `link_all_pending` 与 `grade_pending`，而这两步是确定性的、免费的，且未完成的 claim 需要跨 run 续跑（`:513-515` 的 docstring 就是这么承诺的）。"本轮没有新条目"时，今天仍会链接与评级上一轮遗留的 pending claim，这个行为必须保住。要省的只有 extraction 那一步的 LLM 预算，而过滤 `targets` 的来源集合就足够达到目的。

同时把 `analyze_claims` 的 docstring 补一句：

```python
        """Run the correctness loop over freshly fetched items (best-effort).

        Budgets: extraction touches only the top N items that are new to the
        corpus; grading gets its own call budget and only ever sees claims
        with enough independent sources. Unfinished claims persist and resume
        next run — so linking and grading run even when nothing is new.
        """
```

`fetch_all_sources` 里 `:853` 的 `self.persist_to_corpus(all_items, since)` 不用改（返回值被丢弃是无害的，`self.last_new_item_ids` 已经记下）。

- [ ] **Step 4: 跑测试，确认通过**

Run: `uv run pytest tests/test_orchestrator_claims.py -v`
Expected: PASS

- [ ] **Step 5: 更新 `docs/evaluation.md`**

在描述分层（tiering）的段落里补一句，说明判据已换轨。**先读 `docs/evaluation.md` 全文再改**，找到讲 `claimable` / tiering 的那一节，追加：

```markdown
Since P0 the author/crowd split is declared by each scraper as typed
`ContentItem.sections` rather than recovered by scanning `content` for marker
strings. The marker path is retained for two reasons: databases written
before schema v3 are backfilled through it (their sections are stamped
`provenance="legacy_marker"`), and ablation arm A needs it to reproduce the
pre-P0 behaviour. Before P0, three sources (Hacker News, Reddit, Twitter)
appended `--- Top Comments ---`, which the marker table did not contain, so
their comments were graded as author assertions.
```

- [ ] **Step 6: 全量回归**

Run: `uv run pytest -q`
Expected: 全绿。collected 应为 819 + 2 = 821（各 Task 的实际增量以运行时为准；**判据是 exit code 0 且不低于基线 697**，不是"恰好等于 821"）。

Run: `uv run pytest --collect-only 2>&1 | tail -1`
Expected: `821 tests collected in ...`（±，取决于各 Task 实际新增条数）

- [ ] **Step 6b: 可选的真实网络冒烟**

Task 9 的 `test_fetch_all_sources_walks_every_enabled_source` 已经在 `MockTransport` 下证明了注册表循环能到达全部 14 个源，这是**必需**的证据。真实网络冒烟只是锦上添花，而且**本仓库没有 `data/config.json`**（2026-09-29 实测：文件不存在，配置由 `periscope-wizard` 在用户的数据目录里生成），所以它需要维护者自己提供一份配置。若要做：

```bash
uv run periscope --hours 24        # 或维护者惯用的入口
```

判据只有一条：不崩，且 `Corpus: +N new items` 那一行打印出来。**不要**把"抓到了多少条"当成 P0 的验收指标——P0 的验收全在 Step 7 的表里。


- [ ] **Step 7: 对照 spec §10 的 P0 验收逐条打勾**

| 验收项 | 怎么验 |
|---|---|
| ① HN 链接帖 `claimable` 为空、评论不进 `claim_fts`、不抬高 `independent_sources` | `uv run pytest tests/test_tier_guard.py -k hn -v`；并确认 Step 2 的"先红"记录在 Task 10 的 commit message 里 |
| ② Discourse 楼层落 `community`、`locator == "#<post_number>"` | `uv run pytest tests/test_tier_guard.py -k discourse -v` |
| ③ `src/scrapers/` 内无任何分层标记字面量 | `uv run pytest tests/test_tier_guard.py -k marker -v` |
| ④ 手工同步点 5 → 2 | `uv run pytest tests/test_source_registry.py -v`；再人工数一遍：加一个源现在只需改 `SOURCE_SPECS`（1 处元数据）+ 写 scraper 类并登记 `SCRAPER_BINDINGS`（1 处代码）+ `SourcesConfig` 加字段（守护测试强制）。**注意 `SourcesConfig` 字段这第三处仍是手工的**，spec §3.2 表里写的是"5 → 2"，把 config 字段与 scraper 实现算作那 2 处；汇报时按 spec 的口径说，不要说成 1 处 |
| ⑤ 老库迁移不丢数据、`locator`/`time_basis`/`sections_json` 回填正确 | `uv run pytest tests/test_corpus_v3_migration.py tests/test_evidence_tiers.py -v` |
| ⑥ 全量绿、collected 只增不减 | Step 6 |

任何一项不过，**不要**标 P0 完成。

- [ ] **Step 8: 提交**

```bash
git add src/orchestrator.py tests/test_orchestrator_claims.py docs/evaluation.md
git commit -m "Feat: analyse only newly stored items, so re-fetches stop buying LLM passes"
```

---

## Self-Review

写完后对照 spec §3 与 §10 逐条自查的结果。

**1. Spec 覆盖**

| spec 条目 | 落在哪个 Task |
|---|---|
| §3.1 `Section` Pydantic 模型（含 `provenance`/`asserted`/`confidence`/`locator`） | Task 1 |
| §3.1 `ContentItem` 加 `locator`/`time_basis`/`sections`，`url`/`published_at` 降级 | Task 1 |
| §3.1 `claimable` 由 sections 计算而非 marker 反解 | Task 2 |
| §3.1 不动 NOT NULL 约束、写入侧回填 | Task 1（模型层归一化）+ Task 3（`url` 列写 `locator`） |
| §3.1 `store.py:217 str(item.url)` 必改 | Task 3 |
| §3.1 去重复用 `_deduplication_url_key`、非 URL 退化 | Task 4 |
| §3.1 `time_basis="unknown"` 的分析阶段跳过已见 id | Task 14 |
| §3.1 `SCHEMA_VERSION 2 → 3` + backfill 范式 + `legacy_marker` | Task 3 |
| §3.2 两层注册表（元数据在 models、绑定在新模块） | Task 5 + Task 9 |
| §3.2 `SOURCE_REGISTRY` 由 `SourceSpec` 派生 | Task 5 |
| §3.2 `orchestrator` 14 段 `if` → 遍历注册表 | Task 9（含 `test_fetch_all_sources_walks_every_enabled_source`，在 MockTransport 下断言 14 个 `spec.label` 全部到达） |
| §3.2 守护测试：注册表 ↔ enum ↔ `SourcesConfig` ↔ bindings | Task 5（A）+ Task 9（B） |
| §3.2 不改 config 格式 | 全程未动 `SourcesConfig` 结构，仅 Task 5 的守护测试强制字段存在 |
| §3.3 `throttle.py` per-host 令牌桶 + 抖动 + 429，收拢两份重复实现 | Task 6（模块）+ Task 8（收拢） |
| §3.3 `auth.py` 两种 provider + 过期检测 + 失效重试一次 | Task 7（模块）+ Task 8（注入与重试） |
| §3.3 `BaseScraper.__init__` 可选注入、14 个 scraper 不改也能跑 | Task 8 |
| §3.3 共享 client 统一 `follow_redirects=True` | Task 8 |
| §3.4 6 个 emitter 全量迁移 | Task 10（HN）、Task 11（Reddit + Twitter）、Task 12（Discourse + V2EX + Bilibili） |
| §3.4 第 7 处必改点 `split_item_content` | Task 13 |
| §3.4 验收测试三条 | Task 10（HN claimable 为空）、Task 12（Discourse 楼层 locator）、Task 12（无标记守护） |
| §3.4 `tests/test_twitter.py:589` 必须改 | Task 11 Step 6 |
| §3.5 P0 不做的事 | 全程未引入 OCR/VLM/trust 打分/改名/config 格式变更 |
| §9.11 `time_basis=unknown` 反复烧预算 | Task 14 |
| §9.13 迁移会不会改坏日报 | Task 13 |
| §9.14 现有测试钉住错误行为 | Task 11 Step 6、Task 12 Step 6 |
| §10 P0 验收 6 条 | Task 14 Step 7 的对照表 |
| §14.1 #28 跨源合并要同步 sections | Task 4 |

**spec 里刻意不落到 P0 的条目**：`--tiering=marker|sections` 开关属 §5.4（P1 消融），P0 只负责**保留** `split_sections` 让它将来还能用（Global Constraints 已钉）；`SourceSpec.credibility_prior` 在 P0 只是入库常量，校准属 P1；新增源（贴吧/小红书）属 S1/S2 探针，探针未通过前不写抓取代码。

**2. 占位符扫描**：无待定项、无"照 Task N 类推"、无"加上适当的错误处理"。每个代码步骤都给了完整可粘贴的代码。少数几处要求执行者先看文件现状再落笔的地方，都给了具体命令与判断标准，而不是把决定推给执行者：Task 8 Step 4 与 Task 11/12 的 import 行（`grep -n "^from ..models" <file>`，在既有名字里插入 `Section`，不重写整行）、Task 9 Step 5 的 `TwitterScraper` 残留引用（`grep -n "Scraper\b" src/orchestrator.py`）、Task 14 Step 3 的 `_get_corpus()` 缓存语义（已核实：`:276 return self._corpus`，因此不得加 `finally: close()`）。

**3. 类型与命名一致性**（逐个核对过）：
- `Section` 的字段名在 Task 1 定义，Task 10/11/12 的构造全部只用 `tier`/`text`/`author`/`provenance`/`locator`/`meta` —— 一致。
- `claimable_of(item)` 在 Task 2 定义，Task 3（`store.add_items`）、Task 10/11/12（测试）调用 —— 签名一致，都传 item 不传字符串。
- `marker_sections_to_model(content)` 在 Task 2 定义，Task 3（backfill）与 Task 4（合并）调用 —— 都传字符串，一致。
- `ContentItem.rebuild_content()` 在 Task 1 定义，Task 4（合并）、Task 11（twitter/v2ex/bilibili 原地追加后）调用 —— 一致。
- `Corpus.known_ids(ids)` 在 Task 3 定义，Task 14 调用 —— 一致。
- `Throttle.request(client, method, url, **kwargs)` 在 Task 6 定义，Task 8 的 `_request` 调用 —— 一致。
- `SCRAPER_BINDINGS[key](config, client, ctx)` 三参形状在 Task 9 的 `simple()`、`_build_rss`、`_build_twitter` 与测试里一致。
- `append_discussion_sections` 在 Task 11 定义，同 Task 的 `orchestrator.py:1351-1352` 与测试调用 —— 一致，且旧的 `append_discussion_content` 已无任何调用点（Task 11 Step 5 是唯一一处）。
- `split_item_content(item)` 在 Task 13 定义，两个调用点同 Task 内改完 —— 一致。

**4. 已知风险，执行时留意**
- **collected 数字是推算值**，各 Task 的实际增量以运行时为准。判据是"exit 0 且 ≥697"，不是"恰好等于某个数"。
- **Task 14 有两处最容易过度修改**：一是给 `persist_to_corpus` 加 `finally: corpus.close()`（`_get_corpus()` 是缓存实例，关掉会破坏后续阶段）；二是给 `analyze_claims` 加 `if not fresh: return`（会连带跳过 `link_all_pending`/`grade_pending`，破坏跨 run 续跑）。两处正文都已明确禁止。
- **Task 12 的无标记守护测试用正则扫源码**，会被注释与 docstring 里的标记误伤，所以跳过了以 `#` 或引号开头的行。若将来有人在多行 docstring 的**中间行**提到标记，需要把跳过规则升级成"用 `ast` 排除整个 docstring 节点"，而不是删掉这个测试。
- **Task 9 删 14 行 import 前必须 grep**：`orchestrator.py` 在 `:1349` 附近仍直接用 `TwitterScraper`，漏了会 `NameError`，而且只有跑到 Twitter 回复展开那条路径才炸。
- **Task 11 的 reddit fixture 依赖 `_fetch_comments_html` 先失败**：测试里让 `old.reddit.com` 返回 500，才会走 JSON 路径。若将来 reddit 的抓取顺序变了，这条 fixture 要跟着变，否则测的是另一条路径。


---

## 执行记录（P0 已实现，见 PR #4 / 分支 `feat/p0-structured-sections`）

实现结果：`817 collected`、全量绿（基线 697），spec §10 的 P0 六条验收逐条通过，`scripts/eval_retrieval.py` 的消融表与 `docs/evaluation.md` 逐格一致。

下面是**本计划写错或被实现纠正的四处**。它们不是执行者的失误，是计划对代码现状的假设不成立；留在文档里，是因为下一个计划（P1/P2）会复用同样的假设。

1. **Task 4 的方法名不存在。** 计划写 `orchestrator.deduplicate_items(...)`，实际叫 **`merge_cross_source_duplicates`**（`orchestrator.py:938`）。`tests/test_cross_source_duplicates.py:32` 用的是 `object.__new__(HorizonOrchestrator)`，可参考。

2. **`time_basis` 的默认值降级要条件化。** 计划里 `published_at is None → time_basis = "unknown"` 会**覆盖调用方显式声明的 `"crawled"`**。实现改成只在仍等于默认 `"published"` 时才降级。凡是"模型层归一化"都必须区分"没填"与"填了默认值以外的东西"。

3. **共享管线不能靠构造参数注入。** 计划让 `simple(cls)` 传 `throttle=`/`auth=`，但 13 个 scraper 子类各自以**不同形状**转发 `super().__init__`（有的传 `config.model_dump()`，有的传 `{"enabled": ...}`，有的传 dict 化的 config），全部改签名风险大且无行为收益。实现改为工厂事后赋值（`registry._wire()`），`BaseScraper.__init__` 的可选参数保留给未来的新 scraper。

4. **`append_discussion_sections` 必须先收养已有的纯文本正文。** 这条是测试逼出来的**真 bug**，不是测试写错：一个只带 `content=`（无 sections）的推文在追加回复 section 后，`claimable_of` 会改走 sections 分支，于是**作者本人的正文变成不可 claim**。修法是先把它收成一个 `provenance="legacy_marker"` 的 primary section。凡是"给已有 item 原地追加 sections"的代码路径都要做这一步 —— `merge_cross_source_duplicates` 里同理。

另外两处小差异：

- Task 12 的无标记守护测试实际扫到 4 个中文标记点 + 3 个英文标记点，与计划列的 6 个 emitter 一致（`【评论区】` 确实无 emitter，保留在 legacy 路径里）。
- Task 14 Step 1 的计划版测试自带 helper，实际复用了 `tests/test_orchestrator_claims.py:23` 已有的 `make_orchestrator`；`last_new_item_ids` 做成**类属性**而非实例属性，因为有两条测试用 `__new__` 构造 orchestrator。

## 计划之外的一处修复

`tests/test_llm_cache.py::test_throttle_spaces_upstream_calls` 在全量跑时随机红。commit `c79cfcf` 把断言从"实测睡眠时长"换成"请求的延迟"，但那个延迟是 `time.monotonic()` 算出来的，Windows ~15ms 时钟粒度下第二次请求得到 0.035 而非 0.05。已给 `CachingAIClient` 加注入 clock 并收紧容差到 `rel=1e-9`（只剩二进制浮点误差，不再有挂钟依赖）。

**给下一个计划的提醒**：本仓库"已经消灭挂钟断言"这句话不完全成立。凡是断言"某次调用等了多久"，链路上任何一个 `time.monotonic()` 都会把 flakiness 从后门带回来 —— 要注入的是**时钟**，不只是 `sleep`。
