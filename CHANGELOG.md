# 变更记录

本仓库**还没有发布过任何版本**：`pyproject.toml` 仍是 `0.1.0`，`git tag` 为空，roadmap 里"在 AtomGit 上发布 Release / 发到 PyPI"仍未做。所以这份记录不按语义化版本分节，只区分**已合入 `main`** 与**待合并的 PR**；版本号与打 tag 由维护者决定，不是文档能替它宣布的。

每条都能自己核对：

```bash
git log --oneline main..feat/p1-trust-independence      # 三个 PR 的全部提交（分支是堆叠的）
uv run pytest                                           # 全量
uv run python scripts/eval_retrieval.py --tiering marker # 复现分层前的 A 档
uv run python scripts/eval_multiturn.py                 # 调用数比值 / 轮次 / 灌水曲线
```

**效果主张的边界**：本项目唯一"能力已实现、效果未主张"的一块是声明核查层的**评级准确率** —— 它需要 50–100 条人工标注（`scripts/eval_claims.py --export/--score`），数据目前为零。因此下面所有关于 trust / 阈值的条目都注明"阈值是手工先验、分诊门默认关闭"，不得被读成已验证的准确率提升。

---

## 待合并（四个 PR，按此顺序合：#3 → #4 → #5 → #6）

分支是堆叠的（`#6` base 是 `#5`，`#5` base 是 `#4`，`#4` base 是 `main`；`#3` 直接 base `main`），所以每合一个，下一个的 diff 会自动缩小。条目里**不写提交数** —— 它每推一次就变，要数就用上面那条命令；下面的 collected 数字是全量 `uv run pytest` 的结果。

### #4 · P0：分层从字符串约定改为类型化字段

**新增**
- `Section`（`tier` / `provenance` / `asserted` / `confidence` / `locator`）与 `ContentItem.{locator, time_basis, sections, citation_url, rebuild_content}`。归一化在 `model_validator(mode="after")` 里做完，所以 `published_at` 构造后永不为 `None`，`items.url` / `published_at` 的 `NOT NULL` 与 `idx_items_published` 都不动。
- corpus **schema v3**：`locator` / `time_basis` / `sections_json` 三列 + 老库幂等回填，回填出的层级标 `provenance="legacy_marker"`。
- `SOURCE_SPECS`（纯元数据）派生 `SOURCE_REGISTRY`，`src/sources/registry.py` 持有 `key → 工厂`；`fetch_all_sources` 的 14 段硬编码 `if` 换成遍历注册表。加一个源的手工同步点 **5 → 2**。
- `src/scrapers/throttle.py`（per-host 令牌桶 + 抖动 + 429 重试一次，clock/sleeper/rng 可注入）、`src/scrapers/auth.py`（env token 与 cookie 文件，含"哪条 cookie 过期了"的点名与失效重读一次）。
- 6 个 emitter（HN / Reddit / Twitter / Discourse / V2EX / Bilibili）全部改为**声明**层级；`src/scrapers/` 里禁止再出现分层标记字面量，由 `tests/test_tier_guard.py` 钉住。
- `processing/content.split_item_content()`：日报链路与取证链路对同一条目切出同样的层。

**修复**
- **现役 bug，不是预防性重构**：仓库里有两套互不相交的分层词表，HN / Reddit / Twitter 的评论在 claim 链路整段被当作作者亲写 → 进 `claimable` → `claim_fts` → 声明蒸馏 → 抬高 `independent_sources`；HN 链接帖最坏（`content` 100% 是评论）。守护测试先在 `main` 上跑红，失败输出留在 commit `20d7ff2`。
- `Retry-After` 按 RFC 9110 可以是 HTTP-date，原先 `int(headers["Retry-After"])` 会抛 `ValueError`，把限速升级成抓取失败。
- `reddit.py` / `telegram.py` 两处重复的 429 处理合并为一个 throttle；三处 `httpx.AsyncClient` 的 `follow_redirects` 不一致统一为 `True`。
- 给只带 `content` 的推文追加回复时，作者本人的正文会失去可 claim 性 —— `append_discussion_sections` 现在把既有内容收编成 `legacy_marker` 的 primary 段（测试逼出来的真 bug）。

**数据**：`uv run pytest` collected **697 → 817**；`scripts/eval_retrieval.py` 重跑后消融表与 `docs/evaluation.md` 逐格一致。

### #5 · P2：调研文档成为带 revision 的草稿工件

**新增**
- 顶层动词 `AskUser | Rescope | Deepen | Finalize`（`src/research/moves.py`）与 `MoveContext` 快照。**`ask` 只在有模型时才可能被选中**：无 LLM 的回退只产出 `deepen` / `finalize`，联系不到模型的会话仍会终止而不是连环追问。
- `src/research/drafts.py`：`research_drafts`（`revision`、`origin`、每节 `locked`/`hash`/`stale`）与 `research_requests`（`open`/`answered`/`skipped`）。上游证据变了而该节被用户改过 → **只标 `stale`，不覆盖**；被删分支的章节也带 `stale` 留下。
- `ResearchSession.step()` 返回 `TurnResult`（`revision` / `changed_sections` / `new_evidence` / `verdict_changes` / `pending_request`），只重算被点名的分支；`followup()` 保留为宏，MCP / Web 调用方一字未改。
- 会话状态新增 `planning` / `awaiting_user`，turn 角色新增 `assistant_question`。
- 入口：MCP `hz_research_step` / `_draft` / `_edit` / `_answer`；Web `POST /step`、`GET /draft`、`PATCH /draft/sections/{id}`、`POST /requests/{id}`；面板从"转圈等整报告"改成轮次时间线 + 可编辑章节 + 待回答卡片。

**修复**
- 只讲 `revise` 的老 planner 会静默丢掉用户的范围改动 → `decide_move` 把它的 delta 翻译成 `Rescope`。
- 多轮循环对老 planner 必须只跑一轮（否则每轮重复拓同一批分支并多写 report turn）。

**数据**：collected **817 → 836**；局部重算 2 次调用 vs 全树 5 次（4 分支）；spec §10 的 P2 三条全绿。

### #6 · P1 + 后续（含文档与护栏）

**新增**
- `src/corpus/trust.py`：可拆解的条目信任分 `σ(bias + w·(源先验, 作者等级, 交叉支持, 可核验实体, 新鲜度, 来源方式) − w·模板度)`，**特征与分数一起落库**（`items.trust` / `items.trust_features_json`，**schema v4** 同时补 `publisher`）。
- 独立性口径重定义：先按簇折叠重复内容，再数不同 `(source_type, publisher)`；**解析不出发布者的条目不投票**。声明级聚合 `T = 1 − Π(1 − trust_i·d_i)`（noisy-OR，同族第二个发布者折半）—— 求和没有上界，够多低质源能把任何结论刷成 supported。
- 两道门：分诊门 `analysis.triage_min_trust`（**默认 0 = 关闭**，避免"一篇独立硬稿单独构成 supported"之外还没校准时就误杀）与判定门 `trust.classify`（要求跨族宽度，或同族 ≥3 个发布者且族先验 ≥0.40）。
- `claim_contradictions` 表：`contested` 记下是哪两条声明、靠哪些条目冲突，且矛盾是**判定输入**（已 graded 的声明立刻转 `contested`）。
- 报告不再藏"没走到评级"的声明：`⏳ 未评级（低于分诊门）` / `🔍 证据不足` / `🏷️ 无可核验发布者` / `❌ 可信度不足（T=…）`。
- 消融档位开关 `--tiering=sections|marker`（写入时决定 `claimable` 怎么算，档位记进 `data/eval/results.json`）。
- `scripts/eval_multiturn.py`：spec §5.5 里**不需要人评**的那组系统客观量（调用数比值、轮次到定稿、灌水曲线）。
- `src/corpus/ingest.py` + `scripts/import_corpus.py` + `hz_corpus_import` + `POST /api/import`：§6.1 的降级通路 —— 取不到的源由**用户导出、按声明层级入库**，新增来源方式 `manual_export`（折扣 0.85）。样例负载 `data/export.example.json` 由测试直接解析。
- `src/sources/reachability.py` + `scripts/spike_sources.py`：可达性判别做成六种判定的纯函数（`pass` / `list_only` / `blocked_captcha` / `signed_required` / `blocked_auth` / `error`），**不加 `--online` 不发任何请求**；§14.2 的手工结论现在是断言。
- **P3 的前置不变式**（不必等 S1 通过就能立）：`Section` 的校验器把 `provenance="vlm"` 的块强制 `asserted=False`，于是"VLM 画面描述只能当线索、不得进声明抽取"（spec §7）成为类型规则而不是各 scraper 要记得写的参数；`provenance="ocr"` 保留 `asserted`，只按 `confidence` 打折 —— 图上写的字往往就是作者本人的主张。
- `tests/test_docs_match_code.py`：把文档里的可检查断言钉住（配置字段与默认值、MCP 工具名与数量、README 计数、已删标记不得被教成机制）。
- `docs/architecture.md`：spec §8 图表清单里的**结构性图**（1 广源→可用证据的通路、2 研究会话状态机、3 一轮交互时序），每张图下面写清代码落点，时序图上的调用数是 `scripts/eval_multiturn.py` 的实测值而非估计。附一张 corpus.db 的表清单（12 张普通表 + 2 张 FTS5 虚表，并说明 `items_fts` 含社区层而 `claim_fts` 只含作者层 —— 分层就靠这个差别生效）。**本机没有可用的 mermaid CLI，渲染效果未被验证**，被验证的是内容与代码一致；这一点写在文档里而不是含糊过去。
- `scripts/render_eval_charts.py`：把 §8 的 6、7 号数据图画出来（`docs/assets/flood-independence.svg` 与 `docs/assets/recompute-cost.svg`，嵌在 `docs/evaluation.md` 的对应小节）。**只用标准库** —— `pyproject.toml` / `uv.lock` 里没有 matplotlib / plotly，加绘图依赖是维护者的决定，不是我写文档时能顺手做的。输出是**提交进仓库的字节**，所以 `--check` 与 `tests/test_eval_charts.py` 要求：从 `data/eval/multiturn_results.json` 重渲染必须逐字节相同，画出来的每个数字必须来自那份 JSON，几何必须落在画布与绘图框内。图上的说明句（"旧口径自 2 条转发起就够进判级门"、比值序列、"没有延迟轴"）也是从数据算出来的，不是手写在 SVG 里的。
  4、5 号**仍然没有图**：它们要 50–100 条人评声明标注，数据为零 —— 画一张空图比不画更坏。
- 贡献者侧：`CONTRIBUTING.md` 的「先读」从三份变四份（`docs/architecture.md` 排第一），并加第 9 条硬约束 —— 改动 `Session.status` / `Move` 动词 / corpus 表 / 可达性判定 / 加宽阶梯 / 源族数量就得同步那张图，且**这条规则的预期是它会红**：这些名字都还在动，红了就画图，别删断言。

**修复**
- 草稿章节的 `evidence_ids` / `subquestion_id` / `verdicts` **声明了但渲染路径从未填** → 面板每节显示"0 条证据"（实际引用三条），且 `MoveContext.contested_claims` 恒为空（选下一个动词的模型从来看不到矛盾）。`5f3862c`。
- 面板在 start / 推一轮之后不重绘轮次状态（轮次时间线、可编辑章节、待回答卡片要刷新才出现），状态行硬编码 `· active` —— 浏览器实测抓到，端点级测试当时全绿。`905ab17`。
- `Corpus.add_items()` 的新鲜度读 `datetime.now()`，同一内容不同日期入库得到不同 trust，文档里第四位小数会随日历过期 → 加 `now=` 注入（默认行为不变）。
- `test_llm_cache.py::test_throttle_spaces_upstream_calls` 的"确定性"断言其实仍来自挂钟 → 给 `CachingAIClient` 注入 clock。

**变更（口径）**
- `independent_sources` 不再是簇数；`T(claim)` 用 noisy-OR；`unsupported` 拆成"没找到"与"找到了但可信度不够"；MCP 工具数 22 → **27**。
- 文档与贡献者侧：`docs/configuration.md` 补上本 fork 的四个配置块（逐字段对 `model_fields`）、`retrieval.md` 改讲类型化分层、MCP 指南补 6 个工具并删掉一个不存在的 `hz_claims`、README「六项增强」→ 八项、`CONTRIBUTING.md` / `SECURITY.md` / `CODE_OF_CONDUCT.md` 不再把本 fork 的报告路由给上游邮箱。
- **README 里那句"CI 见 workflows/tests.yml"被删**：本平台不执行 GitHub 语法的 workflow（`check_tasks_num: 0`），同一份文档另一处已经承认这点 —— 两处不能都对。**同一类矛盾的其余两处也一起修了**：README 的"自动化"一节与 `docs/configuration.md` 的 Static Site 一节原先都教读者"把 `daily-summary.yml.disabled` 重命名即可启用定时发布"，而在这里重命名不会有任何效果；现在写的是能跑 shell 的调度器（`cron` / `systemd timer` / `docker compose run`），并把 GitHub-only 的事实放在**同一个段落里**（新护栏按段落判，跨段落的免责声明不算数）。
- `docs/configuration.md` 的 MCP 一节原先只列 7 个工具名（"available tools include…"），现在列全 **27** 个并分组；Dockerfile 补 `COPY scripts`，否则文档里让读者跑的 harness 在镜像里根本不存在（该命令的形状沿用仓库已有的 `--entrypoint uv` 用法，**未在本机跑过 docker build**，`docs/evaluation.md` 里带着这句限制一起写）。
- 新增护栏：文档必须提到 `[project.scripts]` 里的全部 6 个命令；workflow 提及必须与"不生效/不执行"同段；Dockerfile 必须 `COPY scripts`；变更记必须与 `SCHEMA_VERSION`、MCP 工具数、开放 PR 列表一致且不得宣布发布。对改动前的 `HEAD` 跑过：这些都会红（HEAD 上测出 4 处未标注的 workflow 提及）。
- **清掉一处与 `SECURITY.md` 直接冲突的继承文档**：`docs/twitter-cookies.md` 第 4 节原先叫「多账号轮询（防封策略）」，教读者导出多个账号的 cookie 并让 Horizon 轮询它们 —— 而同一仓库的 `SECURITY.md` 与设计 spec §11 写明**本 fork 不做多账号池**。现在这一节分成「代码会做什么」（按匹配文件逐个开上下文、把账号切成等份、失败重试一次、每个上下文的 UA 版本号按序号递增）与**「本 fork 允许什么」**（一个账号、只取该账号已可见的内容；依赖账号池或指纹分散的改动不会被合并）。顺带修掉同文档排障表里那句「或增加 cookie 数量」，并补一条与账号数无关的真实坑：**留着过期的旧导出会被当成第二个上下文**，它预热失败后会分走一半账号导致那些用户直接抓不到。`docs/horizon-hub-design.md` 是上游**未实现提案**，原先被 README 列成「架构与生态设计」——两处现在都写明没有对应代码，架构指向 `docs/architecture.md`。两类都由新护栏钉住（`test_cookie_guide_states_the_collection_boundary`、`test_unimplemented_upstream_proposal_is_labelled_everywhere_it_is_linked`）。
- 消融表护栏：`docs/evaluation.md` 那张 A–F 六行的表与 `data/eval/results.json` **逐格**对齐（`recall@5` / `recall@10` / `precision@5` / `nDCG@10` / `MRR`，按四舍五入到三位小数比），行首字母还要对得上配置名；另断言文档里写的复现命令含 `results.json` 记录的那个 `tiering` 档位。这张表此前只被眼睛核过，而它是全项目被引用最多的数字 —— **最后一位偏移也要红**，所以带一条 tamper 用例证明它真的会红（改两个格子 → 恰好两条定位到行列的报告）。
- 架构图护栏（`docs/architecture.md` 的三张图）：会话的 7 个状态、子问题的 3 个状态、turn 的 3 个角色、`Move` 的 4 个动词、corpus.db 的 12 张普通表 + 2 张 FTS5 虚表、6 种可达性判定、5 步加宽阶梯、源族数量**必须逐条出现在图里**，反向也必须成立（图里画不出代码没有的名字）；回读方式是 `typing.get_args(Move)` 与对 `CREATE TABLE` 的扫描，而不是把清单再抄一遍到测试里。护栏自带一条自检：用一个真不存在的名（`SOURCE_REGISTRY_V2`）验证它真的会红 —— 因为写图时我把 `SOURCE_REGISTRY` 当成臆造的旧名"修"过一次，它是真的（`src/models.py` 由 `SOURCE_SPECS` 派生）。

**数据**：collected **836 → 960**（934 之后追加的 26 条是文档、架构图、两张数据图、消融表与采集边界的护栏）；灌水抵抗实测 `independent_sources` 旧口径 4 → 新口径 1（旧口径下它本可进判级），`T=0.7998` 越过 `supported=0.55` 仍判 `unsupported`（缺跨族宽度，**设计意图，但未经人评检验**）；已知软肋量化：同一作者跨两个 `source_type` → 数成 2 个发布者对。

### #3 · 文档（spec v4 + 三期实现计划 + 交付记录）

设计 spec v1→v4（§9 逻辑接缝自查、§13/§14/§15 三轮审计与交付记录、§15.6 的浏览器实测证据、§15.10 的可达性判别工具）+ 三期实现计划（P0 是实现**前**写的；P1/P2 是实现**后**补写并标注了这点）。

---

## 已合入 `main`

### PR #2（2026-09-26）· 把 fork 与上游真正分开

README 首屏警示与逐渠道归属隔离、能力对照表（10 行，标（上游）/（本 fork）并给文件位置）、项目状态不再声称 GitHub Pages 可用。顺带修掉一个会报假的节流测试（挂钟断言在 Windows ~15ms 粒度下偶发红）。

### PR #1（2026-09-26）· Phase F1–F5 五个阶段

F1 证据分层（当时靠标记反解，见上文 P0 的更正）· F2 混合取证检索（查询扩展 + 向量路 + RRF）· F3 自适应取证加宽（阶梯 `baseline → widen_terms → switch_source_family → rewrite_query → collect_keywords`，动作写进 `research_actions`）· F4 标准 IR 口径的评测（`src/corpus/metrics.py`、六配置消融表）· F5 引用反解核验（`src/corpus/citations.py`）+ F6 报告骨架与声明一致率工具。另修：Windows 上 UTF-8 文件未写 encoding（10 个 ERROR）、wizard 测试写死 posix 路径（2 个 FAILED）、`.gitattributes` 行尾规范化。

**基线**：本机 `uv run pytest` **697 passed**（上游 README 声称的 642 在本机可复现为 697）。
