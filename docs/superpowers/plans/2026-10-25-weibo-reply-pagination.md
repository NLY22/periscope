# 微博楼中楼有限分页实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有微博深采器中按预算读取有限楼中楼页面，并诚实标记未覆盖的回复。

**Architecture:** 扩展 `weibo.fetch_discussion` 的现有 `buildComments` 请求：根评论使用 `fetch_level=0`，根评论 ID 的回复页使用 `fetch_level=1`。回复与内嵌评论去重后加入现有树，继续共用节点上限、时间截止和 `discussion_coverage`。

**Tech Stack:** Python 3.11+、httpx、pytest、现有微博 HTTP 适配器。

**Spec:** `docs/superpowers/specs/2026-10-25-weibo-reply-pagination-design.md`

## Global Constraints

- 每帖最多检查 5 个根评论线程，每线程最多 3 页。
- 根评论与回复共用 `max_comments` 节点上限及 deadline；回复预留最多总节点四分之一。
- 仅使用项目已有微博会话；不启浏览器、不绕过登录、不写真实资料库。
- 不打印或持久化会话值、帖子正文或评论文本作为本轮验收输出。
- 接口计数大于实际取得数时，结果必须保持 partial。

## Review Focus

- 内嵌回复又由 `fetch_level=1` 返回：验证按评论 ID 去重，且原父子树完整。
- 回复页返回新父子字段或字段缺失：验证优先保留接口父 ID，缺失时关联被请求根评论。
- 回复计数大于实际返回：验证 `reply_count_mismatch` 与 partial 状态。
- 回复分页有游标、空页或达到三页：验证 `is_mix`、`max_id`、页上限和截断原因。
- 总节点或时间预算耗尽：验证不超上限、已取得资料保留并标明截断。

---

### Task 1: 为微博适配器补齐有限回复分页

**Files:**
- Modify: `periscope/collection/platforms/weibo.py`
- Test: `tests/collection/test_weibo_discussion.py`
- Modify: `docs/platform-deep-collection-matrix.md`
- Include approved process artifacts: `docs/superpowers/specs/2026-10-25-weibo-reply-pagination-design.md`, `docs/superpowers/plans/2026-10-25-weibo-reply-pagination.md`

**Interfaces:**
- Consumes: `fetch_discussion(post_url, *, post_id, max_comments, budget_seconds, client)`。
- Produces: `discussion_coverage` 新增 `reported_replies`、`reply_threads_attempted`、`reply_pages_attempted`；评论节点沿用 `id`、`parent_id` 和 `replies`。

- [x] **Step 1: 写失败测试**

  在 `tests/collection/test_weibo_discussion.py` 增加以下测试：
  - `test_fetches_reply_pages_with_fetch_level_and_cursor`：首个根回复响应带 `r1` 与游标，后页请求须带 `fetch_level=1,is_mix=1,max_id=<前页游标>`；若根评论已内嵌 `r1`，最终树中只保留一个 `r1`；新回复的 `parent_id` 指向根评论。
  - `test_reply_count_mismatch_marks_partial`：接口报告 3 条、实际仅取得 2 条，coverage 包含 `reply_count_mismatch` 且状态为 partial。
  - `test_reply_collection_respects_thread_page_and_node_limits`：永远有下一页的模拟响应下，线程数不超过 5、每线程页数不超过 3，`fetched_comments` 不超过传入 `max_comments`。
  - `test_reply_time_budget_keeps_partial_material`：子评论请求返回 `time_budget` 后，已采根评论仍保留，coverage 标记 `time_budget` 和 partial。

- [x] **Step 2: 运行定向测试确认失败**

  Run: `uv run pytest tests/collection/test_weibo_discussion.py -q`
  Expected: 新测试因回复端点未请求及覆盖字段不存在而失败。

- [x] **Step 3: 实现受限回复页采集**

  在 `weibo.fetch_discussion` 内增加根评论候选和每线程回复页循环；根评论明确使用 `fetch_level=0`，首次回复请求使用 `fetch_level=1,is_mix=0`，续页用接口游标及 `is_mix=1`。最多 5 个线程、每线程 3 页；总节点中最多四分之一预留给回复，完成回复后在余下预算内继续抓根评论。使用评论 ID 去重并更新覆盖计数。接口空页、计数差异及预算上限分别记录截断原因。

- [x] **Step 4: 运行定向测试确认通过**

  Run: `uv run pytest tests/collection/test_weibo_discussion.py -q`
  Expected: 所有微博讨论测试通过，回复数量从总节点上限扣除。

- [x] **Step 5: 更新平台矩阵并运行全量回归**

  更新微博行与后续记录，写入有限样本证据及其计数差异。
  Run: `uv run pytest -q`
  Expected: 全量测试通过。

- [x] **Step 6: 检查差异并提交**

  Run: `git diff --check; git status --short`
  Expected: 无空白错误；提交实现、测试、平台矩阵和已批准设计/计划文件至当前工作分支。

## Self-review

- Spec coverage：有限 `fetch_level=1` 页面、限额、共享预算、去重、覆盖状态和实样本记录均由 Task 1 覆盖。
- Type consistency：适配器签名不变；新字段只加入 coverage 字典，评论节点继续使用现有结构。
- Proportion：单一适配器改动作为一个独立测试交付。


## Post-review hardening (2026-10-25)

The independent final review exposed four edge cases; all are covered by regression tests and fixed in a follow-up pass:

- Preserve root rows returned in a page when the temporary reply reserve is reached, then use remaining total capacity for them.
- Count only unique comment IDs against the shared node budget, allowing a duplicate reply to contribute newly discovered nested replies.
- Prefer explicit `reply_comment_id` parent data when a duplicate node is enriched, and rebuild trees after collection so child-before-parent arrival produces the correct hierarchy.
- Only report `comment_limit` when a cursor indicates more reply data remains or material is actually deferred.

Verification: `.venv\Scripts\python.exe -m pytest -q tests/collection/test_weibo_discussion.py` passed (13); `.venv\Scripts\python.exe -m pytest -q` passed; collect-only reported 1,474 tests; `git diff --check` passed. Live cursor semantics and response-shape frequency remain only partially verified.
