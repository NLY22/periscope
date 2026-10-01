# P2：多轮共创调研文档 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans 逐 Task 执行，或 superpowers:subagent-driven-development 每 Task 派一个新执行者。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 让调研文档从「一次 `investigate()` 生成的实时投影」变成**有 revision、可被用户逐节塑形、可局部重算**的持久工件，并给循环加上「停下来向用户索取缺失输入」这个动词。

**Architecture:** 四个新接缝。① `src/research/moves.py` 定义四个顶层动词（`AskUser | Rescope | Deepen | Finalize`）与 `MoveContext` 快照，把「下一步做什么」从隐式循环变量变成显式可断言的返回值。② `src/research/drafts.py` 落两张新表（`research_drafts` / `research_requests`），草稿每节带 `locked`/`hash`/`stale`，上游变了而用户改过 → **只标 stale，不覆盖**。③ `ResearchSession.step()` 只重算被点名的分支，返回 `TurnResult` 差分；`followup()` 保留为「推进直到定稿」的宏，调用方一字不改。④ MCP 加四个动词、Web 加四个端点、面板从「转圈等整报告」改成轮次时间线 + 可编辑章节。

**Tech Stack:** Python ≥3.11（开发环境 3.12 / uv）、sqlite3（沿用 `Corpus._conn`，不新建连接池）、FastAPI + 原生 JS 面板、pytest（`asyncio.run`，不用 pytest-asyncio）。

**Spec:** `docs/superpowers/specs/2026-09-29-broad-source-credibility-and-multiturn-research-design.md`（v3）。本计划实现 §4（P2）全部内容，遵循 §9 的接缝自查（尤其 §9.3 引用编号稳定性、§9.4 `awaiting_user` 不能变死会话、§9.6 无 LLM 时必须能终止）与 §10 的 P2 三条验收。

---

## 本文与 P0 计划的差别（读者需要先知道）

P0 那份计划写于实现**之前**，所以每个 Task 都带可粘贴的完整代码。本文写于实现**之后**（2026-09-29），补上 spec §12 承诺的「各期各自成计划」：

- **代码不复贴**，给的是接口契约 + `文件:符号` 锚点，实现以 `feat/p2-multiturn-drafts`（PR #5）及其后续修正提交为准。
- Task 边界就是当时的提交拆分边界，判据表里的数字是实测值，不是预估。
- 「执行记录」一节写了 3 处实现中被测出来的回归，和 1 处 PR 正文与代码不符的地方（草稿的 lineage 字段声明了但没填）—— 这条已在 `5f3862c` 补上，正是这类事后计划的价值所在。

## Global Constraints

- **`followup()` 的对外签名与语义不变**：`(session_id, user_message) -> SessionReport`，返回整份报告。MCP `hz_research_followup`、Web `/followup`、`scripts/` 里的调用方都依赖它。逐轮是新动词 `step`，不是替换。
- **不新建数据库文件**：两张新表建在 `Corpus._conn` 上，与 `research_*` 既有表同库，沿用 `executescript` + `commit` 的建表风格。
- **不改 `research_sessions` / `research_subquestions` / `research_turns` 的既有列**，只新增枚举值（session status 加 `planning`/`awaiting_user`，turn role 加 `assistant_question`）。老库不需要迁移。
- **无 LLM 时必须能终止**（spec §9.6）：确定性回退只产出 `Deepen` / `Finalize`，**永远不产出 `AskUser`**。一个联系不到模型的会话如果开始连环追问，就是把「可用」变成「卡住」。
- **不做 SSE / WebSocket 流式**：请求-响应 + revision 轮询足够（spec §11）。
- **测试里禁止挂钟断言**（仓库规则，P0 已为此返工过一次）。涉及时间的一律注入。
- **`Planner` 协议是加法式扩展**：`next_move` 是 `hasattr` 探测的可选方法，只实现 `decompose/answer/revise` 的主机（含上游 `ai/*` 适配器）保持原行为，不许因为 P2 而炸。
- **测试基线：817 collected**（P0 完成时实测）。每个 Task 结束时全量必须绿，collected 只增不减。
- **命令一律 `uv run`**。**每个 Task 一个 commit**，消息风格沿用仓库（`Feat:` / `Fix:` / `Test:` / `Docs:` + 祈使句）。

## File Structure

| 路径 | 职责 | 状态 |
|---|---|---|
| `src/research/moves.py` | 四个动词 dataclass、`Move` union、`MoveContext` 快照与它的三个派生视图（`open_subquestions` / `thin_subquestions` / `contested_claims`） | 新建 |
| `src/research/drafts.py` | `DraftSection` / `Draft` / `ResearchRequest` 三个 dataclass、`body_hash()`、两张表的 DDL、`DraftStore`（草稿版本 + 向用户提的请求） | 新建 |
| `src/research/session.py` | `section_id` / `split_markdown` / `commit_draft` / `edit_section` / `move_context` / `decide_move` / `step` / `_apply_rescope` / `_deepen` / `answer_request` / `TurnResult`；`followup` 改成宏；`Planner` 协议加 `next_move` | 修改 |
| `src/mcp/service.py` | `research_step` / `research_draft` / `research_edit` / `research_answer`（原有 4 个 research 方法保留） | 修改 |
| `src/mcp/server.py` | `hz_research_step` / `_draft` / `_edit` / `_answer` 四个 tool | 修改 |
| `src/web/app.py` | `POST /step`、`GET /draft`、`PATCH /draft/sections/{sid}`、`POST /requests/{rid}` | 修改 |
| `src/web/static/index.html` | `renderDraft()` / `renderRequests()` / `stepRound()`：轮次时间线、可编辑章节（锁定 / 陈旧标记）、待回答卡片 | 修改 |
| `tests/test_research_p2.py` | 本计划全部判据（19 条 + 修正 3 条 = 22 条） | 新建 |

依赖方向：`moves.py` 不 import `session.py`（`MoveContext` 是纯 dict 快照，避免循环）；`drafts.py` 只用 `corpus._conn`，不 import `session`。

---

## Task 1: 草稿与请求的持久层

**Files:** Create `src/research/drafts.py`；Test `tests/test_research_p2.py`

**Interfaces:**
- Produces: `body_hash(body: str) -> str`；`DraftSection(id, title, body, evidence_ids, subquestion_id, locked, hash, stale, verdicts)`（`__post_init__` 里若 `hash` 为空则按 body 计算；`to_dict()` / `from_dict()` 成对）；`Draft(id, session_id, revision, origin, sections, created_at)` + `section(section_id)` + `markdown()`；`ResearchRequest(id, session_id, kind, payload, turn_id, status, answer, created_at, answered_at)`；`DraftStore(corpus)`，方法 `save(session_id, sections, origin) -> Draft`、`latest(session_id) -> Draft|None`、`get(session_id, revision=None)`、`revisions(session_id) -> List[int]`、`open_request(session_id, kind, payload, turn_id=None)`、`answer_request(request_id, answer, *, skip=False) -> bool`、`pending_requests(session_id)`、`has_open_request(session_id, kind=None)`
- 常量词表：`ORIGINS = ("render", "user_edit", "merge")`、`REQUEST_KINDS = ("clarify", "confirm_claim", "choose_scope", "supply_source")`、`REQUEST_STATUSES = ("open", "answered", "skipped")`，非法值 `raise ValueError`

- [ ] **Step 1: 写红测试** —— revision 单调递增且每个 revision 都可回读；`revisions()[0] == 1`
- [ ] **Step 2: 建表** —— `research_drafts(id, session_id, revision, origin, sections_json, created_at)` + `idx_rdrafts_session`；`research_requests(id, session_id, turn_id, kind, payload_json, status, answer, created_at, answered_at)` + `idx_rreq_session`。`sections` 存 JSON 数组而不是每节一行：草稿永远是整份读写，拆行只会让「一次 revision」变成一次事务体操
- [ ] **Step 3: 全量绿 + commit**

## Task 2: 把渲染好的报告切成可定位的章节

**Files:** Modify `src/research/session.py`；Test `tests/test_research_p2.py`

**Interfaces:**
- Consumes: `render_report(session_id) -> str`（既有，未改）
- Produces: `ResearchSession.section_id(title) -> "s_<slug>"`（staticmethod）；`split_markdown(markdown) -> List[DraftSection]`；`commit_draft(session_id, markdown) -> Draft`

- [ ] **Step 1: 红测试** —— 章节 id 只由标题决定，因此**同一标题在下一个 revision 仍是同一个 id**，用户的锁才不会漂到别的小节上
- [ ] **Step 2: 实现切分** —— `_HEADING = ^## +(.+?)$`。第一个 `##` 之前的内容成为 `s_overview`（标题「概览」），因为报告头的进度行也是读者会改的地方
- [ ] **Step 3: 关键不变式（写进注释，别留给下一个人重新发现）** —— 标题即 identity：它是读者唯一看得到的东西，也是重渲染能复现的东西。所以「按标题锁定」在 revision 之间成立，而它下面的引用编号可以随便重排（spec §9.3）
- [ ] **Step 4: `commit_draft` 的三条保护** —— ① 上一节被 `locked` 或 `hash` 与正文不符（= 用户改过）且新渲染不同 → 保留用户正文，置 `stale=True`；② 上一节在新渲染里消失了 → **仍然带过去**并标 stale，丢分支是范围决策，静默删用户的字正是版本化工件最不该做的事；③ 只有渲染路径会调用它，`edit_section` 走自己的 origin

## Task 3: 用户编辑是一条独立 revision，不是覆盖

**Files:** Modify `src/research/session.py`；Test `tests/test_research_p2.py`

**Interfaces:**
- Produces: `edit_section(session_id, section_id, body) -> Draft`（`origin="user_edit"`，该节 `locked=True`、`stale=False`，其余字段原样搬运，`revision` 比上一份大）
- 无草稿 → `KeyError("session ... has no draft to edit")`；未知 section → `KeyError("unknown section ... in revision N")`

- [ ] **Step 1: 红测试** —— `test_edit_section_creates_a_user_edit_revision_and_locks_it`
- [ ] **Step 2: 实现** —— 深拷贝全部节（`DraftSection.from_dict(s.to_dict())`）再替换目标：直接改 `latest()` 返回的对象会污染 `DraftStore` 里同一份草稿
- [ ] **Step 3: `stale` 必须能被洗清** —— 补一条 `test_stale_flag_is_false_when_the_recomputed_text_matches`：重算结果与用户写的一致时不该继续挂着陈旧标记，否则 stale 会退化成「用户动过」的永久纹身

## Task 4: 四个动词与 `MoveContext`

**Files:** Create `src/research/moves.py`；Test `tests/test_research_p2.py`

**Interfaces:**
- Produces：`AskUser(kind: AskUserKind, question: str, options: tuple = ())`、`Rescope(add: tuple, drop: tuple)`、`Deepen(subquestion_ids: tuple)`、`Finalize(reason: str = "")`；`AskUserKind = Literal["clarify","confirm_claim","choose_scope","supply_source"]`（与 `REQUEST_KINDS` 同一套词，别造第二个）；`Move = Union[...]`；`MoveContext(session_id, question, subquestions, draft_sections, pending_requests, user_message, budget_left)` + 三个派生属性
- `thin_subquestions` = open **且** `evidence_ids` 为空 → 这是「该问用户要源」的诚实信号，不是「模型觉得少」
- `contested_claims` = 任一节 `verdicts` 里有 `contested`

- [ ] **Step 1: 红测试** —— `test_move_context_reports_open_and_thin_branches`
- [ ] **Step 2: 实现** —— 全部 `@dataclass(frozen=True)`：动词是一次决策的不可变记录，要改就换一个
- [ ] **Step 3: `MoveContext` 只装 dict，不装 ORM 对象** —— `moves.py` 一旦 import `session.py` 就形成循环（`session` 要 import `moves`）

## Task 5: `decide_move` —— 模型的判断，或确定性回退

**Files:** Modify `src/research/session.py`；Test `tests/test_research_p2.py`

**Interfaces:**
- Consumes: `Planner.next_move(ctx) -> Move`（协议里的可选方法）；`Planner.revise(...)`（既有）
- Produces: `move_context(session_id, user_message="") -> MoveContext`、`async decide_move(ctx) -> Move`

- [ ] **Step 1: 红测试（spec §9.6 的那一条）** —— `test_no_planner_still_finishes_rather_than_hanging`：`planner=None` 时 `move` 只会是 `deepen` 或 `finalize`
- [ ] **Step 2: 回退顺序** —— 有 planner 且预算未耗尽且有 `next_move` → 问模型（返回非 Move / 抛异常 → 记 warning 并落回退）；只有 `revise` → 见 Step 3；否则 `Deepen(open_ids)`，没有 open 分支就 `Finalize("every branch is settled")`
- [ ] **Step 3: 兼容只讲 `revise` 的老 planner** —— 有用户消息时调用 `revise`，把它的 `{add, drop}` **翻译成 `Rescope`**，而不是因为「没有 next_move」就丢掉用户的范围改动。这条是被测出来的回归（见执行记录 ①）
- [ ] **Step 4: 预算耗尽** —— 直接落回退分支，`test_budget_exhausted_falls_back_to_the_deterministic_move`

## Task 6: `step()` —— 一轮只动被点名的地方

**Files:** Modify `src/research/session.py`；Test `tests/test_research_p2.py`

**Interfaces:**
- Consumes: `decide_move`、`_apply_rescope`、`_deepen`、`commit_draft`、`render_report`
- Produces: `async step(session_id, user_message="") -> TurnResult`；`TurnResult(revision, move, changed_sections, new_evidence, verdict_changes, pending_request, markdown)` + `to_dict()`

- [ ] **Step 1: 红测试（P2 验收 ③）** —— `test_deepening_one_branch_costs_fewer_calls_than_deepening_all`：4 个子问题，`Deepen(一条)` = **2** 次模型调用（1 次 `next_move` + 1 次 `answer`），全树重跑 = **5** 次。另加 `test_a_step_costs_no_more_than_a_full_rerun` 守住上界
- [ ] **Step 2: 状态机** —— 进入即 `planning`；`AskUser` → 写 `assistant_question` turn + `open_request` + `awaiting_user`；`Rescope`/`Deepen` → 受影响分支重算后 `render_report` + `commit_draft` + `report` turn + `reported`；`Finalize` → `drafting`
- [ ] **Step 3: 差分怎么算** —— `changed_sections` 按节 id 对齐比较 `body` 与 `stale`；`new_evidence` = 会话证据集合与轮初的差；`verdict_changes` = `_verdict_snapshot` 前后对照。**调用方不该为了知道「这轮变了什么」而 diff 整份报告**
- [ ] **Step 4: `markdown` 返回整份渲染而不是草稿散文** —— 见执行记录 ②：`AskUser` / 无受影响分支时返回轮前的 `report_before`，否则 `followup` 宏会把引用列表和审计行丢掉

## Task 7: 请求的闭合 —— 回答、跳过、恢复

**Files:** Modify `src/research/session.py`；Test `tests/test_research_p2.py`

**Interfaces:**
- Produces: `async answer_request(session_id, request_id, answer, *, skip=False) -> TurnResult`

- [ ] **Step 1: 红测试** —— `test_a_request_can_be_skipped_without_answering`（`skipped` 也是合法闭合，答案为空不是错误）；`test_answering_an_unknown_or_closed_request_raises`（重复回答同一个 request_id 必须报错，不能静默二次推进）
- [ ] **Step 2:  parked 会话可列可恢复（spec §9.4）** —— `test_a_parked_session_can_still_be_listed_and_resumed`：`awaiting_user` 在 `hz_research_list` 与面板里都看得见，回答后状态离开 `awaiting_user`

## Task 8: `followup()` 改成宏

**Files:** Modify `src/research/session.py`；Test `tests/test_research_p2.py`

**Interfaces:**
- Consumes: `step`
- Produces: `async followup(session_id, user_message, max_rounds=4) -> SessionReport`（签名不变）

- [ ] **Step 1: 循环终止条件** —— 遇到 `pending_request` 或 `finalize` 立即停；**没有 `next_move` 的 planner 只跑一轮**（见执行记录 ③：多轮自治需要模型真的能选动词，否则每一轮都在重复拓同几条分支并多写一条 report turn）
- [ ] **Step 2: 三条停止保险** —— 预算耗尽、本轮既无 `changed_sections` 又无 `new_evidence`，都停
- [ ] **Step 3: 返回哪份 markdown** —— 优先 `result.markdown`，退到 `draft.markdown()`，再退到 `render_report()`

## Task 9: 入口 —— MCP 四个动词 + Web 四个端点 + 面板

**Files:** Modify `src/mcp/service.py`、`src/mcp/server.py`、`src/web/app.py`、`src/web/static/index.html`；Test `tests/test_research_p2.py`

**Interfaces:**
- Produces: `HzMcpService.research_step / research_draft / research_edit / research_answer`（都走 `self._require(...)` 与 `self._jsonable(...)`，错误统一 `HZ_SESSION_NOT_FOUND`）；`hz_research_step` / `hz_research_draft` / `hz_research_edit` / `hz_research_answer` 四个 tool；`POST /api/research/{sid}/step`、`GET .../draft`、`PATCH .../draft/sections/{section_id}`、`POST .../requests/{request_id}`
- Consumes: Task 6 的 `step`、Task 3 的 `edit_section`、Task 7 的 `answer_request`

- [ ] **Step 1: 红测试** —— `test_mcp_service_exposes_the_four_new_verbs`、`test_mcp_tool_list_includes_the_round_verbs`、`test_panel_step_draft_edit_and_answer_round_trip`（端点级 round-trip，不依赖浏览器）
- [ ] **Step 2: 面板** —— 轮次时间线 + 每节「锁定 / 陈旧」标记 + 待回答卡片 + `推一轮` 按钮；`<script>` 过 `node --check`
- [ ] **Step 3: 明确记录 UI 的验证边界** —— 本环境没有可用 config 与模型密钥，**面板没在浏览器里点过**，接线正确性只到端点级测试。这句话要写在 PR 正文里，不能假装测过

## Task 10: 验收与回归

- [ ] P2 验收 ①：`test_spec_acceptance_three_rounds_with_one_system_question` → 3 轮，`moves == [askuser, deepen, finalize]`，草稿 revision ≥ 2
- [ ] P2 验收 ②：`test_a_locked_section_is_never_overwritten_only_marked_stale` → body 与 `locked` 不变，`stale=True`
- [ ] P2 验收 ③：Task 6 Step 1 的 2 vs 5
- [ ] 全量：`uv run pytest -q` → **836 collected 全绿**（P0 基线 817）
- [ ] `uv run pytest tests/test_research.py tests/test_research_widening.py -q` —— P2 之前的研究测试一字未改仍绿，这是「`followup` 语义没被动过」的证据

---

## Self-Review

**1. Spec 覆盖**：§4.1 状态机与草稿工件 → Task 1/2/6/7；§4.2 新动词 → Task 4/5；§4.3 局部重算 → Task 6；§4.4 入口改造 → Task 9；§9.3 引用编号 → Task 2 Step 3；§9.4 parked 会话 → Task 7 Step 2；§9.6 无 LLM 能终止 → Task 5 Step 1；§10 的 P2 三条 → Task 10。没有落到 spec 之外的美化。

**2. 占位符扫描**：无 TBD / 无「适当处理」。Task 9 Step 3 是唯一一条「不做某事」的步骤，它要求的是在 PR 正文里说清验证边界。

**3. 类型一致性**：`REQUEST_KINDS` 与 `AskUserKind` 必须是同一套四个词（Task 1 / Task 4 各写一遍是刻意的重复，由 `test_move_context_reports_open_and_thin_branches` 与 `DraftStore.open_request` 的 `ValueError` 共同钉住）；`TurnResult.move` 是**小写类名**（`type(move).__name__.lower()`），断言写 `"askuser"` 而不是 `"AskUser"`；`session.status` 新增值 `planning` / `awaiting_user`，`drafting` / `reported` 是复用的既有值。

**4. 已知风险**：局部重算的依赖图只到 `section ← subquestion ← evidence` 三层，没有新建反向索引表；章节标题改名（用户编辑不了标题，但模板改了标题）会让旧 section 变成 carried-over stale 而不是同名替换 —— 这是刻意的保守，代价是偶尔出现一节在草稿里存在而报告里没有。

## 执行记录（计划与代码撞上之后）

① **只讲 `revise` 的老 planner 会静默丢掉用户的范围改动** —— P2 最初只在 `hasattr(planner, "next_move")` 时问模型，否则落 `Deepen`，于是 `revise` 返回的 `{add, drop}` 被扔掉。修法是 Task 5 Step 3 的翻译层（`revise` → `Rescope`）。

② **`followup` 返回了草稿散文而不是整份报告** —— 因为 `step` 最初只回 `draft.markdown()`，于是宏的调用方丢了引用列表和 `citations.py` 的审计行。修法是 Task 6 Step 4 的 `report_before` 兜底。

③ **多轮循环对老 planner 必须只跑一轮** —— 否则 `revise` 被反复调用、每轮重新拓深同样的分支，多写 report turn 且加宽审计轨迹无意义增长。

④ **草稿的 lineage 字段声明了却从未被填**（PR #5 正文里「草稿每章节存 `evidence_ids`」这句当时**不成立**，属于我的过度陈述）。后果是两个可见缺陷：面板每节的「N 条证据」恒为 0（而那一节实际引用了三条），`MoveContext.contested_claims` 永远为空 → 决定下一步动词的模型从来看不到矛盾。修复在 `5f3862c`：`commit_draft` 结束时按「节标题 = 子问题」或「节内 `###` 小标题」映射回分支，取证据并集；一节对应多个分支时 `subquestion_id` 留 `None`（指向两个分支里的第一个，是在谎报血缘）。三条新测试先红后绿，全量 **862 collected 全绿**。

⑤ **Task 9 Step 3 那句免责声明被证伪了一次** —— 「面板没在浏览器里点过」促使我真去点了一遍，当场抓到缺陷：`ask()` 在 start/followup 之后只手工刷 `#report`，没向服务器要会话的其余状态，于是轮次时间线、可编辑章节、待回答卡片全空，要刷新页面才出现；状态行还硬编码 `· active`，而 `awaiting_user` / `reported` / `drafting` 正是本 Task 新增的状态。换句话说 **P2 的界面在它唯一被使用的地方不可见，而端点级测试全绿**。修复在 `905ab17`（`ask()` 与 `stepRound()` 都改成 `await showSession(...)`），护栏 `tests/test_web_panel.py::test_panel_repaints_round_state_after_it_changes_the_session` 是文本级断言（证明不了渲染，但回到旧写法会红）。浏览器实测逐条结果见 spec §15.6；全量 **882 collected 全绿**。

> 这一条的教训值得单独留一句：**在浏览器里点一遍不是端到端测试的补充，它是唯一能发现「接口全对、用户看不到」那类缺陷的手段。**
