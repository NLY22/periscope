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
- **导入通路补上面板那一段**：`POST /api/import` 早就存在，但面板里**没有任何地方用到它** —— 而会去导出小红书 / 贴吧内容的人是用户，不是维护者，所以这个缺失正好落在最不该缺的入口上。面板新增「导入你导出的内容」一节：贴 JSON 或选文件 → **只校验** → **入库**，逐条报「第几条为什么不收」，入库后刷新证据列表与统计。同时把预览的语义修正为**与写入同一段校验**（`prepare_import` / `preview_payload`）：CLI 原先的 `--dry-run` 用严格解析器，会把「30 条里 1 个错字」报成整体失败，而真导入会收 29 条 —— 预览与结果不一致的预览比没有预览更坏。面板的 `<script>` 现在过 `node --check`，并且有一条测试把界面读取的字段与端点**实际返回**的字段对比 —— 这两处都是本轮为「UI 只能靠人点」找的替代证据；**浏览器里长什么样仍未验证**（会话浏览器没有可见 surface）。
- **三个入口又齐了一次**（`tests/test_mcp_parity.py`，3 条）：上一条提交给 CLI 与面板加了预览，**`hz_corpus_import` 被落下了** —— §6.1 那句"三个入口"再次变成半真。现在 MCP 工具与 service 都有 `dry_run`，MCP 指南的工具表与导入一节写明；并加了一条**结构性**对称检查：三个入口都能预览、都必须把校验交回 `src/corpus/ingest.py`（不许在别处再实现一遍逐条判断），而 `tiering` **只在 MCP 与 CLI 暴露、面板故意不给** —— `marker` 是复现消融档位的开关，放在用户上传证据的地方等于邀请他用更弱的分层规则入库。**这条护栏的第一版不咬人**：它拿整个函数体做字符串匹配，所以我把参数删掉之后 docstring 里的 `dry_run` 仍然让它通过 —— 是我自己写的"证明它会红"那步当场揭穿的，现在改成只读**签名**。
- **命令行标志也进了同一类检查**（`test_every_documented_command_flag_exists`）：从 README、各指南、CONTRIBUTING/SECURITY 与 CHANGELOG 的**代码片段与代码块**里抽出「我们的命令 + 它后面的 `--flag`」，逐个对回真正的 argparse。覆盖 **13 个目标**（6 个 `periscope-*` 入口、compose 服务 `periscope-collect`、以及 5 个 `scripts/*.py`）与 **23 个标志位出现**，标志层面**全对 —— 这条没抓到假命令，它是预防性的**；但它顺手暴露了一个真缺口：`scripts/spike_sources.py` 在全部文档里**只被提过一次，且没有一条可复制的命令**（README 写"一条命令即可跑"却没把命令写出来），而这个探针正是 S1 / P3 那条链的开关。已给 README 与 CONTRIBUTING 补上 `--source/--kw/--url/--online` 的真实用法，并加一条 `test_the_reachability_probe_is_documented_as_a_runnable_command`（**对着改动前的 README 量过：1 处提及、0 条可跑示例 → 它会红**）。
  过程中三次假警报比结论更值得记：① `--data-dir` / `--config` 不在 `src/main.py` 而在共享的 `src/_cli.py` —— 只 grep 入口文件就会把真文档误判成造假；② `--rm` / `--entrypoint` 属于 docker，且 `docker compose run --rm --entrypoint uv periscope-collect run periscope-wechat test --lang zh` 这种**一行两个命令**的写法会把后一个命令的标志错记到前一个头上 —— 现在按「到下一个命令为止」分段归属，docker/uv 自己的标志单列一份带理由的排除表；③ 最初连散文一起扫，于是 CHANGELOG 里讨论 `--flag` 的那句话本身就成了"文档承诺了一个不存在的标志" —— 现在只扫代码片段与代码块。正则也放宽到认 `"--flag"` 字面量，因为 argparse 常见 `"-d", "--data-dir"` 这种短选项在前的写法。
- **§9.13 承诺的那对"一致性守护测试"当时并不存在**：接缝自查里写的是"守护测试要覆盖两侧（claim 侧与日报侧）对同一条 Reddit 条目的一致性"，实际只有各侧自己的测试 —— 两侧各自全绿、却可以再次的互不同意（那正是 P0 起因：日报认英文标记、claim 认五个中文标记）。新增 `test_daily_path_and_forensic_path_cut_the_same_layers`（同一条目：`split_item_content(...).main == claimable_of(item, "sections")`；回帖既不进两侧、也仍作为线索留在 `comments`）与 `test_comment_only_item_is_empty_for_both_consumers`（HN 链接帖两侧都为空）。断言是等式而非"含不含某词"，所以两侧真的分叉时会立刻红。
- **§11 的"明确不做"从散文变成断言**（`tests/test_spec_exclusions.py`，7 条）：逐条实测后确认今天四条机械排除**都成立** —— 无 SSE/WebSocket/流式路由；无验证码打码与请求签名逆向（`blocked_captcha` 是**判定**不是打码，所以模式刻意写窄，不误伤检测代码）；`src/scrapers/` 与 `src/sources/` 下没有任何贴吧/小红书抓取模块；语义检索需要 `semantic` 与 `embedding_model` **两个**条件同时成立才开，而后者默认是空串。另把两处"有意不做"的变更也钉住：`items.url` / `published_at` 仍是 `NOT NULL`（排序与引用整条路都靠它），以及**严格度分裂本身** —— `Section`/`ContentItem` 是 `extra="forbid"`、四个证据块故意不是（收紧会让带杂键的现有配置直接加载失败），文档那句话也要求存在。**为什么值得钉**：这类边界最容易被"好心"的改动破掉（加个流式让面板更好看、加个打码依赖把源接进来），而每一次都会把这个项目变成另一个项目，且现有测试一个都不会红。自检也写了：把禁止项喂给模式，必须匹配得到。
- **第 7 条守的是"多账号池"这条排除里唯一能钉的部分 —— 那份指南，不是代码**。写断言前先全仓搜了一遍"池"，结果和 §11 的散文相反：**上游 `src/scrapers/twitter_playwright.py` 本身就带池实现**（`cookie_dir` 按 `cookie_file_pattern` 通配取多个 cookie 文件、每文件开一个浏览器上下文、失败账号下一轮换 cookie 重试、每个上下文的 UA 版本号按序号递增；`models.py:356-357` 的默认值就是通配模式）。所以"代码里没有池"这句**不能**断言——树里就有，本 fork 排除的是**新增**这类能力。可钉的是文档：`docs/twitter-cookies.md` 原第 4 节标题写着"多账号轮询（防封策略）……大幅提升稳定性"、第 6 节写着"建议使用**小号/备用号**"，与 `SECURITY.md` 和本仓库的采集边界正面相反（第 4 节已改成"代码会做什么 / 本 fork 允许什么"两段，本轮把第 6 节那句也改掉、第 7 节的"增加 cookie 数量"已在上一轮处理）。断言两头都钉：**卖池短语**（防封 / 多账号轮询 / 大幅提升稳定性 / 建议使用）不得回来，**边界句**（"一个账号，你自己的账号"、"本 fork 的采集边界"）必须在；并留一段旧文案证明这四个短语确实会命中。**这轮的教训记下来**：一条"我们不做 X"的散文，要先看代码里有没有 X 再决定钉什么 —— 不然断言会写成一句假话，或者更糟：把继承来的实现当成自己的 bug 删掉。
- **一次需求可追溯性回查（spec §3–§7 → 代码 → 测试）**：把设计各节里反引号点到的机制名全量扫出来比对（301 个 token，其中 **281 个真存在于代码里**），再查哪些从没在 `tests/` 出现过（36 个）。**这 36 个绝大多数不是缺陷**：`COALESCE` / `DISTINCT` / `REAL` 是 SQL 关键字，`HttpUrl` / `Literal` 是类型，`research_drafts` 这类表名是通过 store API 间接测的 —— 所以"名字没出现"不等于"行为没测"，这句必须写在结论前面，不然这条统计就是又一次扫描器过度解读。逐条查证后确认**真的没被钉住只有一处**：`analysis.triage_min_trust` 从配置到 `ClaimAnalyzer` 的那一跳（store 层的门槛行为在 `tests/test_trust_p1.py` 有测，但**如果在调用点把它写死成 0.0，所有文档仍然全对、而分诊门已经死了**）。新增 `test_triage_floor_reaches_the_analyzer_that_picks_claims` 同时钉住两件事：默认值确实是 `0.0`（= 文档里那句"门关闭"），以及它每次构造分析器时都重新读配置（顺带证实 `_get_claim_analyzer()` 不缓存）。另外两个"看起来没人测"的 `reset_budget()` 与 `invalidate_after()` 经查**都有调用点也都有行为覆盖**（前者在两处轮次入口、后者由 `test_mcp_run_store.py` 覆盖），所以没有为凑数补测试。
- **MCP 工具转发检查**（`tests/test_mcp_parity.py` 新增 2 条）：27 个 `hz_*` 工具的每一处**参数**都必须真的到达 service 调用。这是**由怀疑找出来的，不是由失败**：上一条修的就是"工具接了参数却没往下传"这种静默空转，写完后没有任何东西会阻止它再发生一次。检查区分三种情形（关键字转发、位置转发、漏传）—— `hz_get_run_meta` 用的是位置转发（`service.get_run_meta(run_id)`，也是唯一手写内联 metrics 的包装器），所以只查关键字会误报；我第一次扫描正是这样把它报成了缺陷，读代码后确认是我的检查错。**自检用例把三种写法都当fixture测过**，所以这条不会只会通过。另两条同轮的部署/资产面检查（compose 服务名、`profiles/` 名册）**结论是没有缺陷**，两次可疑命中经查证都是我的扫描器错，已记在上一条里。
- 新增一条**会咬人的路由可达性护栏**（`tests/test_web_panel.py`）：拿服务真实注册的路由表逐条问"面板里有没有代码调它"，只有两条能豁免（`/api/docs` 是 FastAPI 自带的 Swagger 页；`/api/collect/status` 与 `/api/stats` 是同一份状态，留给 API 客户端），而且豁免必须**写出理由**且"面板确实没调它"，否则免单独自变成藏东西的地方。**这条护栏是对着 git 验过的，不是嘴上说的**：拿改动前的 `index.html`（`1cd1deb`）跑，它精确报出 `/api/import` 一条；拿现在的 HEAD 跑，报 0 条。匹配故意宽松 —— 路径的每个字面片段都要在脚本里出现，因为 `${id}` 模板串让精确匹配做不到（除非去解析 JS）；它可能被巧合的字符串骗过去，这句也写在测试注释里而不是藏起来。
- `src/sources/reachability.py` + `scripts/spike_sources.py`：可达性判别做成六种判定的纯函数（`pass` / `list_only` / `blocked_captcha` / `signed_required` / `blocked_auth` / `error`），**不加 `--online` 不发任何请求**；§14.2 的手工结论现在是断言。
- **P3 的前置不变式**（不必等 S1 通过就能立）：`Section` 的校验器把 `provenance="vlm"` 的块强制 `asserted=False`，于是"VLM 画面描述只能当线索、不得进声明抽取"（spec §7）成为类型规则而不是各 scraper 要记得写的参数；`provenance="ocr"` 保留 `asserted`，只按 `confidence` 打折 —— 图上写的字往往就是作者本人的主张。
- `tests/test_docs_match_code.py`：把文档里的可检查断言钉住（配置字段与默认值、MCP 工具名与数量、README 计数、已删标记不得被教成机制）。
- `docs/architecture.md`：spec §8 图表清单里的**结构性图**（1 广源→可用证据的通路、2 研究会话状态机、3 一轮交互时序），每张图下面写清代码落点，时序图上的调用数是 `scripts/eval_multiturn.py` 的实测值而非估计。附一张 corpus.db 的表清单（12 张普通表 + 2 张 FTS5 虚表，并说明 `items_fts` 含社区层而 `claim_fts` 只含作者层 —— 分层就靠这个差别生效）。**本机没有可用的 mermaid CLI，渲染效果未被验证**，被验证的是内容与代码一致；这一点写在文档里而不是含糊过去。
- `scripts/render_eval_charts.py`：把 §8 的 6、7 号数据图画出来（`docs/assets/flood-independence.svg` 与 `docs/assets/recompute-cost.svg`，嵌在 `docs/evaluation.md` 的对应小节）。**只用标准库** —— `pyproject.toml` / `uv.lock` 里没有 matplotlib / plotly，加绘图依赖是维护者的决定，不是我写文档时能顺手做的。输出是**提交进仓库的字节**，所以 `--check` 与 `tests/test_eval_charts.py` 要求：从 `data/eval/multiturn_results.json` 重渲染必须逐字节相同，画出来的每个数字必须来自那份 JSON，几何必须落在画布与绘图框内。图上的说明句（"旧口径自 2 条转发起就够进判级门"、比值序列、"没有延迟轴"）也是从数据算出来的，不是手写在 SVG 里的。
  4、5 号**仍然没有图**：它们要 50–100 条人评声明标注，数据为零 —— 画一张空图比不画更坏。
- 贡献者侧：`CONTRIBUTING.md` 的「先读」从三份变四份（`docs/architecture.md` 排第一），并加第 9 条硬约束 —— 改动 `Session.status` / `Move` 动词 / corpus 表 / 可达性判定 / 加宽阶梯 / 源族数量就得同步那张图，且**这条规则的预期是它会红**：这些名字都还在动，红了就画图，别删断言。

**修复**
- **标注工具并不能产出阈值，这条修了**：`roc_thresholds()` 一直有（也有单元测试），但 `scripts/eval_claims.py --score` **从没调用它**，而 `--export` 生成的标注表里**根本没有 `c.trust` 这一列** —— θ 是按 `T(claim)` 定义的，也就是说即便你今天标完 100 条，跑 `--score` 只会得到一致率与 macro-F1，阈值仍然是手工先验。文档当时写的是"工具已就位，缺的是标注本身"，这句**夸大了**。现在：导出的每行带 `machine_trust`（并说明它是被测对象、别改），`--score` 会用 `roc_thresholds()` 给出 `thresholds_suggested`（附在打印与 `claims_results.json` 里），带 T 值的标注不足两个类别时**明写"跳过"而不是编一个数**。新增 `tests/test_eval_claims_calibration.py`（4 条）覆盖整条路径。顺带把三个口径写进 `docs/evaluation.md`：建议≠生效值；**macro-F1 固定按三个标签取平均**（标注里没有 `contested` 时，完美一致也只有 0.667 —— 这条有断言钉住）；这张表不是盲标，一致率因此偏乐观。
- **部署面与资产面的两项检查：结论是"没找到缺陷"，但过程值得记**（`tests/test_docs_match_code.py` 新增 2 条护栏）。① `docker-compose.yml` 只有 `periscope-web` / `periscope-collect` 两个服务，文档里 5 条 `docker compose …` 命令引用的正是这两个，端口 `8790:8790` 也与面板默认一致；② `docs/profiles.md` 的「Built-in Profiles」表 4 行，与 `profiles/` 下 4 个目录**逐一对应**，且四个都满足文档承诺的"四文件布局"。**两次我以为抓到了问题，都是我的扫描器错**：第一次把 `docker compose run --rm --entrypoint uv periscope-collect run periscope-wechat test` 里的**容器内脚本名**当成服务名（与 §15.17 标志归属同一类"一行两个命令"）；第二次表格解析越界吃到后面表的 `| \`analysis.md\` |` 行。两次都在动手"修文档"之前先查证原文，于是文档一个字没被误改。护栏留着：将来改名或漏登记会直接红。
- **四个"代码在读、文档从不提"的环境变量补齐了**：`HORIZON_PATH`（MCP 的报错信息里直接写着 "Pass horizon_path or set HORIZON_PATH"，却没有一份文档提到它）、`HORIZON_MCP_SECRETS_PATH`（指错路径会 `HZ_SECRETS_NOT_FOUND` 响亮失败，不静默回退）、`HORIZON_API_URL`（预置目录基址，**默认是上游站点 `horizon1123.top`，本 fork 不部署它** —— 所以要么指自己的实例，要么用下面的离线开关）、`HORIZON_OFFLINE`（`1/true/yes` 让向导与预置加载完全不联网）。新增 `docs/configuration.md` 的「Process Environment Variables」一节 + `.env.example` 四行 + 护栏 `test_every_env_var_the_code_reads_is_documented`（**对改动前跑过：四个全会被点名**）。这与上一条 `RESEND_API_KEY` 是同一类死路：软件让你设一个说明书里没有的东西。
- 草稿章节的 `evidence_ids` / `subquestion_id` / `verdicts` **声明了但渲染路径从未填** → 面板每节显示"0 条证据"（实际引用三条），且 `MoveContext.contested_claims` 恒为空（选下一个动词的模型从来看不到矛盾）。`5f3862c`。
- 面板在 start / 推一轮之后不重绘轮次状态（轮次时间线、可编辑章节、待回答卡片要刷新才出现），状态行硬编码 `· active` —— 浏览器实测抓到，端点级测试当时全绿。`905ab17`。
- **套件会因机器的 DNS 而红，这条修掉了**：`src/url_security.py` 的 SSRF 校验会**真去解析**主机名（这是它该有的行为 —— 防的是把通知目标配成 `169.254.169.254`）。但在这台机器上，出站解析被网络拦截并把 `example.com` 一类主机名回答成 **RFC 2544 基准段 `198.18.0.x`**，而它不是 globally routable，于是"正确工作的安全检查"把 **20 条 webhook 测试**判红（外加一条按顺序才红的抽取测试）。2026-10-01 实测：`21 failed, 951 passed` → 修完 `972 passed`，连跑两次一致。
  做法是给校验加一个**可注入的 resolver**（`_default_resolver` 这个接缝，与仓库里 clock / sleeper / rng 的既有约定同形），测试在 `tests/conftest.py` 里用 autouse fixture 把默认解析钉成固定公网地址；**没有删测试、没有放宽校验、没有改生产默认行为**。要测解析分支的测试仍然自己 patch `_resolve_hostname`（见 `tests/test_url_security.py`），所以确定性与覆盖率都保住了。生产侧要注入的话：`validate_public_http_url(url, resolver=...)`。
- `Corpus.add_items()` 的新鲜度读 `datetime.now()`，同一内容不同日期入库得到不同 trust，文档里第四位小数会随日历过期 → 加 `now=` 注入（默认行为不变）。
- `test_llm_cache.py::test_throttle_spaces_upstream_calls` 的"确定性"断言其实仍来自挂钟 → 给 `CachingAIClient` 注入 clock。

**变更（口径）**
- `independent_sources` 不再是簇数；`T(claim)` 用 noisy-OR；`unsupported` 拆成"没找到"与"找到了但可信度不够"；MCP 工具数 22 → **27**。
- 文档与贡献者侧：`docs/configuration.md` 补上本 fork 的四个配置块（逐字段对 `model_fields`）、`retrieval.md` 改讲类型化分层、MCP 指南补 6 个工具并删掉一个不存在的 `hz_claims`、README「六项增强」→ 八项、`CONTRIBUTING.md` / `SECURITY.md` / `CODE_OF_CONDUCT.md` 不再把本 fork 的报告路由给上游邮箱。
- **README 里那句"CI 见 workflows/tests.yml"被删**：本平台不执行 GitHub 语法的 workflow（`check_tasks_num: 0`），同一份文档另一处已经承认这点 —— 两处不能都对。**同一类矛盾的其余两处也一起修了**：README 的"自动化"一节与 `docs/configuration.md` 的 Static Site 一节原先都教读者"把 `daily-summary.yml.disabled` 重命名即可启用定时发布"，而在这里重命名不会有任何效果；现在写的是能跑 shell 的调度器（`cron` / `systemd timer` / `docker compose run`），并把 GitHub-only 的事实放在**同一个段落里**（新护栏按段落判，跨段落的免责声明不算数）。
- `docs/configuration.md` 的 MCP 一节原先只列 7 个工具名（"available tools include…"），现在列全 **27** 个并分组；Dockerfile 补 `COPY scripts`，否则文档里让读者跑的 harness 在镜像里根本不存在（该命令的形状沿用仓库已有的 `--entrypoint uv` 用法，**未在本机跑过 docker build**，`docs/evaluation.md` 里带着这句限制一起写）。
- 新增护栏：文档必须提到 `[project.scripts]` 里的全部 6 个命令；workflow 提及必须与"不生效/不执行"同段；Dockerfile 必须 `COPY scripts`；变更记必须与 `SCHEMA_VERSION`、MCP 工具数、开放 PR 列表一致且不得宣布发布。对改动前的 `HEAD` 跑过：这些都会红（HEAD 上测出 4 处未标注的 workflow 提及）。
- **清掉一处与 `SECURITY.md` 直接冲突的继承文档**：`docs/twitter-cookies.md` 第 4 节原先叫「多账号轮询（防封策略）」，教读者导出多个账号的 cookie 并让 Horizon 轮询它们 —— 而同一仓库的 `SECURITY.md` 与设计 spec §11 写明**本 fork 不做多账号池**。现在这一节分成「代码会做什么」（按匹配文件逐个开上下文、把账号切成等份、失败重试一次、每个上下文的 UA 版本号按序号递增）与**「本 fork 允许什么」**（一个账号、只取该账号已可见的内容；依赖账号池或指纹分散的改动不会被合并）。顺带修掉同文档排障表里那句「或增加 cookie 数量」，并补一条与账号数无关的真实坑：**留着过期的旧导出会被当成第二个上下文**，它预热失败后会分走一半账号导致那些用户直接抓不到。`docs/horizon-hub-design.md` 是上游**未实现提案**，原先被 README 列成「架构与生态设计」——两处现在都写明没有对应代码，架构指向 `docs/architecture.md`。两类都由新护栏钉住（`test_cookie_guide_states_the_collection_boundary`、`test_unimplemented_upstream_proposal_is_labelled_everywhere_it_is_linked`）。
- **全仓库的符号存在性网**：把只用在 `docs/architecture.md` 上的那条检查推广到 15 份面向读者与贡献者的文档 —— 反引号包起来的、形状像代码的名字，必须在 `src/` + `scripts/` + 面板 JS + `tests/` + 配置文件 + 仓库文件名里真的存在。**这次扫出两处真东西**：① `.env.example` 里**根本没有邮件那一段**，而 `docs/configuration.md` 让读者「在 `.env` 里设 `RESEND_API_KEY`」（`email.password_env` 默认是 `EMAIL_PASSWORD`，`services/email.py` 用 `os.getenv` 读）—— 已把 `EMAIL_PASSWORD` / `RESEND_API_KEY` / `LWN_KEY` 补进 `.env.example` 并写明哪个由配置指定；② 两条我原先从没核过的断言（`past_7_days`「上游坏了」、OpenBB 的 `benzinga`）查下来是**外部名字而非虚构符号**，于是它们进的是**带理由的外部词表**，不是被忽略。规则上避免了两个坑：`${VAR}` 里的名字由用户自己起（不当仓库符号）；`UC...` 这类省略号是留给读者补全的形状（不当符号）。护栏自身也有反身性处理：本文件的豁免词表要从词库里减掉，否则「auth_token 不在词库里」这句断言会被自己的允许清单喂饱；而证明它真的会红的假名字是**运行时拼出来的**，因为写死的假名字会随本文件一起进词库、当场把自检变成空转。`docs/superpowers/` 明确排除：计划文档要在实现之前点名接口，把它们一起查要么设计上就红、要么得写一份大到算撒谎的词表。
- 消融表护栏：`docs/evaluation.md` 那张 A–F 六行的表与 `data/eval/results.json` **逐格**对齐（`recall@5` / `recall@10` / `precision@5` / `nDCG@10` / `MRR`，按四舍五入到三位小数比），行首字母还要对得上配置名；另断言文档里写的复现命令含 `results.json` 记录的那个 `tiering` 档位。这张表此前只被眼睛核过，而它是全项目被引用最多的数字 —— **最后一位偏移也要红**，所以带一条 tamper 用例证明它真的会红（改两个格子 → 恰好两条定位到行列的报告）。
- 架构图护栏（`docs/architecture.md` 的三张图）：会话的 7 个状态、子问题的 3 个状态、turn 的 3 个角色、`Move` 的 4 个动词、corpus.db 的 12 张普通表 + 2 张 FTS5 虚表、6 种可达性判定、5 步加宽阶梯、源族数量**必须逐条出现在图里**，反向也必须成立（图里画不出代码没有的名字）；回读方式是 `typing.get_args(Move)` 与对 `CREATE TABLE` 的扫描，而不是把清单再抄一遍到测试里。护栏自带一条自检：用一个真不存在的名（`SOURCE_REGISTRY_V2`）验证它真的会红 —— 因为写图时我把 `SOURCE_REGISTRY` 当成臆造的旧名"修"过一次，它是真的（`src/models.py` 由 `SOURCE_SPECS` 派生）。

**数据**：collected **836 → 994**（934 之后追加的 60 条是文档、架构图、两张数据图、消融表、采集边界、符号网、导入面板、路由可达性、命令行标志、三入口对称、阈值校准、环境变量面、部署/资产面、工具转发、配置接线、两条链路一致性、§11 排除项与 cookie 指南的护栏）；灌水抵抗实测 `independent_sources` 旧口径 4 → 新口径 1（旧口径下它本可进判级），`T=0.7998` 越过 `supported=0.55` 仍判 `unsupported`（缺跨族宽度，**设计意图，但未经人评检验**）；已知软肋量化：同一作者跨两个 `source_type` → 数成 2 个发布者对。

### #3 · 文档（spec v4 + 三期实现计划 + 交付记录）

设计 spec v1→v4（§9 逻辑接缝自查、§13/§14/§15 三轮审计与交付记录、§15.6 的浏览器实测证据、§15.10 的可达性判别工具）+ 三期实现计划（P0 是实现**前**写的；P1/P2 是实现**后**补写并标注了这点）。

---

## 尚未合入（本轮，分支 `feat/labeling-coverage-guardrails`）

以下几件事都属于"规则写在纸上，但机器不知道"的同类项（最后一条不是修缺陷，是把回路真跑一遍） —— 上一轮把 §11 变成了断言，这几轮把"断言"再往前推一格：**真跑起来时会拦、会说、会自证**。第四条最重：它是一条写在已合并 PR 正文里的机制，实际从未在生产路径跑过。

- **标注管线不再能把覆盖度缺陷报成质量分**。`--score` 以前无条件打印 macro-F1，而它固定在三类标签上取平均：缺 `contested` 样本时**人机完全一致也只有 0.667**。现在缺类会**先**打印一句警告（含那个天花板数字）**再**打表，结果 JSON 里加 `class_coverage` 与 `macro_f1_interpretable`；`--export` 的说明里也写明三类都要标到样本，不等标完 100 条才发现。测试 3 条，含反向用例（三类齐全时不许出警告）。
- **§11 的"多账号池"补上代码半边**。继承来的 `twitter_playwright` 是池形状的（这条上一轮已确认不能靠断言否认），而边界此前只在文档里。现在匹配到 >1 个 cookie 文件时，`_planned_cookie_files()` 在**开跑前**警告一句本 fork 的采集边界是一个账号，并说明账号列表会被切成几份 —— 顺带覆盖那个对单账号也成立的坑（留着过期旧导出＝多了个上下文）。**没有删除继承实现、没有改默认行为、单 cookie 集时不产生任何日志噪音**（反向用例钉住）。
- **盲标从"自己动手遮列"变成一条开关**。文档原来写着"要盲标就把 `machine_verdict` / `machine_trust` 两列遮掉再读摘录" —— 而**被标注者看见的判定会锚定一致率**，这正是项目唯一缺的那块证据最容易被做废的地方。现在 `--export --blind` 把三列移出标注表、写进同名 `.machine.json` 副表（标注期间不用打开），`--score` 按 `claim_id` 自动合回来，报告与结果 JSON 都会写明这批**是不是盲标**，免责那句也跟着分支（盲标不再写"偏乐观"）。`--machine <path>` 可显式指定副表。
  - 实现时踩到并被测试抓住的一处：`machine_verdict` 缺失有两种完全不同的原因 —— 盲标表，和**还没人标**的空表。第一版按列猜，把后者变成了崩溃；现在只有"表自己声明是盲标 / 副表真的存在 / 调用方指了副表"三种情况才去合表，并加了那条反向用例（`test_an_unlabelled_sheet_is_not_mistaken_for_a_blind_one`）。

- **`supported` 的两道门接进了产品路径**（本轮最重的一条）。`trust.classify()` 与 `Thresholds(supported=0.55, same_family_publishers=3)` 一直存在、也有单测，`docs/` 与已合并的 !8 正文都把它写成"判定背后的机制" —— 但**没有任何生产代码调用它**：verdict 完全来自模型读摘录，门只在评测脚本里跑。后果不是"少了一道保险"，是**报告里那句"❌ 可信度不足（T=…）"在当时是一句假话**：它是模型的判断，却被写成聚合层的否决。现在 `grade_claim` 在模型给出 `supported` 后用同一套 `collapse_votes` + `classify` 复核，**只降不升**（摘录是否说同一件事仍归模型，有几个独立声音归算术），并在 `claims.verdict_source`（schema v5 附加列，老库 ALTER）记下判定来自谁；报告对被否决的那些改口成"🚫 未通过可信度门（T=…，模型原判 supported）"。老行留 `NULL` —— 迁移不去假装知道 pre-gate 的 `supported` 能不能过门。
  - 同时补上**阈值无处落地**这条：`docs/evaluation.md` 让用户"把校准值写进 `trust` 配置"，而仓库里根本没有那个配置块。现在 `analysis` 下多四个可选字段（`supported_min_trust` / `triage_gate_trust` / `same_family_prior` / `same_family_publishers`，默认全 `null` = 保持手工先验），`thresholds_from(config)` 生成门限，orchestrator 把它传给分析器 —— `--score` 拟出来的 θ 从此有一个真的去处。新字段未文档化时 `test_every_evidence_config_field_is_documented` 会红，这次也是它先抓到的。

- **让这个判定走到所有读者面前**（同一轮的收尾，别只修写入侧）：`Claim` 多一个 `verdict_source` 字段并进 `to_dict()`，store 读取按列名取（本文件里有几处显式列表的 SELECT，不按名字取就会静默丢），`scripts/eval_claims.py --export` 每行带 `machine_verdict_source`（盲标时与其他 machine 列一起进副表），`--score` 在该字段存在时**按来源拆一致率**并写进 `by_verdict_source`。为什么值得做："人与模型一致、但被门否决"这一类，混起来看只是"系统 67% 对"，拆开看才是"门的阈值可能设严了"——这恰好是人评要回答的那个问题，之前它会被总平均埋掉。

- **聚合面也要分得开**：`ClaimStore.stats()` 增加 `by_verdict_source`（`hz_corpus_stats` 直接把整个 dict 交给 agent，之前它只报 `by_verdict`，门否决的 `unsupported` 与模型自己说的 `unsupported` 在总数里是一个数）。**pre-gate 的老行计入 `unset` 而不是 `llm`** —— 没人知道那些是谁判的。顺手核实了两条读取通路：`hz_list_claims` / `hz_get_claim` 都走 `Claim.to_dict()`，所以上一提交加的字段自动到达，不必再补（这是查证，不是假设）。

- **拟合阈值不再能烂在最后一步**：`hz_validate_config` / `periscope` 的配置校验现在会对着 `data/eval/claims_results.json` 回一句"标注已拟出 supported=0.71，而线上还在用 0.55"（`trust.unused_calibration`）。之前这条 loop 的失败方式是静默的：`--score` 写出建议、人读过、门继续用先验，没有任何东西会再想起来。检查放在校验里是因为**那已经是 agent 与 CLI 会调的那一下**，而不是某个没人跑的脚本。非盲标那批还会附上"偏乐观"。
  - **顺手抓出我自己三提交前造的死把手**：`analysis.triage_gate_trust` 是给 `Thresholds.triage` 开的，而**运行期没有任何东西读 `Thresholds.triage`** —— 真正决定"要不要花一次模型调用"的门槛一直是 `analysis.triage_min_trust`。字段已删，拟合的 triage 就写进 `triage_min_trust`（`--score` 那句建议与配置文档同时改了）。**注意这条不是被"没人读"的扫描抓到的**：`thresholds_from()` 里确实 `getattr` 了那个名字，所以名字出现扫描会放行；抓到它的是去查 `.triage` 的**下游读取点**。因此新增两条断言各管一半：字段必须被读，且 `Thresholds.triage` 除 `roc_thresholds()` 产出外不得出现运行期读取点（否则就得重新讨论要不要配置它）。

- **一条声明，一份证据集**（本轮最后一个接缝）。三个地方各自定了摘录上限：linker 按 `analysis.evidence_per_claim`（6）写入、`ClaimStore.evidence_for()` 默认**截到 8**、`eval_claims.py --export` **硬编码 6**；而 `evidence_votes()`（无聚类折叠、无上限）被 `scripts/eval_multiturn.py` 用来算已发布的灌水曲线 —— 也就是说**模型看到的、人标注的、和门算的，可能不是同一批摘录**。改法是把上限放到**写入处**：`add_evidence()` 插完后按分数裁到 `store.evidence_limit`，读侧一律不再自己截（`evidence_for(limit=None)`），死掉的 `evidence_votes()` 删除（它会绕过折叠规则，留着就是等着被人用错）。配置从 orchestrator 的两个构造点都传进去。
  - **这条改动会自己动测量值，所以我重跑了 harness 而不是推理**：第一次重跑时 `independent_sources_before_p1` 那一行从 **5 变成 4** —— 因为生产上限把 9 条植入链接裁到 6 条，曲线两端被压平。这是**修复的副作用而不是发现**，所以 harness 的两个 fixture 现在显式用 `evidence_limit=24`，重跑后 `5` 回来、`noisy_or_trust=0.7998` 与全部判级结论**不变**（匿名条数在两种规则下都不投票，所以 T 本来就不受影响）。
  - 顺带修掉一个"测试替工件打圆场"的老问题：`multiturn_results.json` 里存过生成的 `pending_request` id，两次跑必然不同，于是 `test_no_measurement_reads_the_wall_clock` 一直**先删掉这一列再比较**。现在记录的是"是否问了用户"（布尔），那条测试改成整份工件逐字节相等 —— 以后再有不确定的字段会直接红，而不是被一段 projection 放过。

- **标注表里根本没有摘录，`--tiering` 在这个脚本里根本没有被用过**（本轮最尴尬的一条，因为它属于"人评"那半步唯一的入口）。说明写着"读 evidence 里的原文摘录，只按这些摘录判断"，而每行只有 `item_id / cluster_id / source_type / title / url` —— 标注者只能挨个开链接，或者凭标题猜。同时 `--tiering` 在 `eval_retrieval.py` 里是真接线（`build_corpus(..., tiering=)`），在 `eval_claims.py` 里**只有 argparse 那一行**，`args.tiering` 从未被读；文档还给过 `--tiering=sections` 的具体命令。现在：每行带 `claimable_excerpt`（复用 `ClaimAnalyzer._excerpt` 的同一套空白折叠与 400 字上限，所以人看到的**就是**模型看到的），`marker` 档按标记重切、于是人群文本泄漏在表里看得见；档位写进表与结果 JSON，两档混用时 `--score` 先警告再报数（"两档不能共用同一份 ground truth"）。
  - 断言里带一个真夹具：作者正文 + `--- Top Comments ---` + `[alice]: the benchmark is rigged` —— `sections` 档的摘录**不含** "rigged"，`marker` 档**必须含**（那正是 A 档要量出来的泄漏）。另有一条"这个标志不能只是装饰"的静态断言：`args.tiering` 必须出现两次（导出与评分各一处），导出体里必须有 `claimable_excerpt` 与档位记录。

- **成功之后的打印不该让命令失败**（本轮最后一条，也是同族）：两个评测脚本用 `Path.relative_to(REPO_ROOT)` 展示输出路径 —— `--sheet C:\Users\...\labels.json` 这类**仓库外**的路径会在**导出已经完成之后**抛 `ValueError: ... is not in the subpath of ...`。本机就是最常见的形状：仓库在 `D:`，临时目录在 `C:`。改成 `_cli.display_path()`（在里面就给相对路径，在外面就原样给绝对路径），并留一条静态断言：`scripts/` 里不许再出现 `relative_to(REPO_ROOT)`。顺手补另一处：从**还没跑过声明抽取**的语料库导表，以前是 `sqlite3.OperationalError: no such table: claims` 的裸 traceback，现在明说先跑 `uv run periscope --hours 24`。

- **把整条人评回路跑通了一次**（不是修 bug，是补证据）。新增 `tests/test_labeling_loop.py`：在真语料库上建声明、连证据、评级，然后 `--export`（含盲标与两档）→ 填合成标签 → `--score` → 断言 `thresholds_suggested` 真出现、`class_coverage` 三类齐全、`by_verdict_source` 记的是 `llm`，最后**按建议配置**并断言 `unused_calibration` 从"警告"变成"闭嘴"。**结果：没有发现新缺陷** —— 但这条回路此前只有各段的单测，没有任何人从头走过一遍；2026-09-30 那次"工具已就位"翻车（§15.18）就是这个形状。所以这次把"跑通"写成断言而不是句子。

- **把面板真在浏览器里点了一遍，当场抓到一处**：导入区的 `placeholder` 建议用户写 `"source_type": "forum"` —— 而 `forum` 是 `SourceSpec.kind`，端点只认 14 个 source type，照抄示例的人第一下就会吃一个 reject。已改成 `discourse`，并新增 `tests/test_panel_placeholder.py`：占位符里的词表词必须是真 source type、两套词表必须**不相交**（这正是容易混的原因）、可运行的 `data/export.example.json` 必须仍能导入。浏览器实测记录（本地 `127.0.0.1`，真 config + 真 SQLite，无模型降级态）：只校验 → `可读 1 条 · 有作者层 1 条（未写入）` 且坏条目按序号点名；入库 → `新入库 1`、列表刷新到 1 条、toast `导入完成：新增 1 条`。
  - **同一条路径又抓出一个**：核查台（`#claims`）把被可信度门否决的声明渲染得**与模型自己判 `unsupported` 完全一样**，还带着模型的 90% 置信度 —— `/api/claims` 其实一路都带着 `verdict_source`（HTTP 层实测确认），是模板没用它。现在多一枚 `可信度门否决 · 模型原判 supported` 标记，并且**门否决的条目不再显示那个百分比**（置信度属于被推翻的那一方）。实测渲染：`无支撑 / 可信度门否决 · 模型原判 supported / 2 独立源`，`%` 不再出现；护栏 `test_the_claims_pane_says_who_made_a_vetoed_verdict`。**这次覆盖的是导入与核查台两条交互路径**；报告与草稿时间线的排版/样式仍未目测，别读成"整个面板测过了"。

配套：`docs/twitter-cookies.md` §4 补一句这个启动期警告是什么、要你做什么（删掉多余那份，不是多备几个号）；`docs/evaluation.md` 口径 ③ 改写为 `--blind` 的用法与"只有盲标出来的一致率适合被引用"，并写明 `machine_verdict_source` 与拆分读法；`docs/configuration.md` 补三个阈值字段、"设了就改判定"、拟合 triage 该放哪，以及校验会回这句；README 的 `analysis` 行补上三个阈值字段。**四处描述"两道门"的文档也一并跟上事实**（此前它们把一个未接线的函数写成判定机制）：README 能力表标出它跑在 `grade_claim` 并给出 `claims.verdict_source` 位置、`docs/architecture.md` 的 `claims` 行补上该列、`docs/retrieval.md` 写清降级规则与四个可覆盖阈值、`docs/evaluation.md` 第 3 点注明这条现在对产品路径也成立。

**数据**：collected **994 → 1044**（+4 覆盖度与池形状，+5 盲标通路，+6 门接线与迁移，+5 判定来源的读取与拆分，+3 聚合面与 MCP 直传核实，+8 拟合阈值的校验与"死把手"两条断言，+4 证据集上限统一到写入处，+3 摘录入表与档位不共用 ground truth，+4 CLI 输出路径与裸库提示，+4 人评回路端到端，+3 面板示例词表，+1 核查台把"门否决"与"模型判的无支撑"分开；`uv run pytest` exit=0）。逐文件对过账：1040 + 4 = 1044。中途一次 `--collect-only` 报过 1015，与逐文件账目差 1；重跑两次稳定 1014（`test_trust_gate_wired.py` 稳定 11 条），所以采用 1014。**那一次多出的 1 我没查明原因** —— 只记现象与"以复测为准"，不给一个没验证过的解释。（同一轮我还把"新增 4 条"算错过一次：实际 3 条，账目对上才发现是加法错，不是测试丢了。）

---

## 已合入 `main`

### PR #2（2026-09-26）· 把 fork 与上游真正分开

README 首屏警示与逐渠道归属隔离、能力对照表（10 行，标（上游）/（本 fork）并给文件位置）、项目状态不再声称 GitHub Pages 可用。顺带修掉一个会报假的节流测试（挂钟断言在 Windows ~15ms 粒度下偶发红）。

### PR #1（2026-09-26）· Phase F1–F5 五个阶段

F1 证据分层（当时靠标记反解，见上文 P0 的更正）· F2 混合取证检索（查询扩展 + 向量路 + RRF）· F3 自适应取证加宽（阶梯 `baseline → widen_terms → switch_source_family → rewrite_query → collect_keywords`，动作写进 `research_actions`）· F4 标准 IR 口径的评测（`src/corpus/metrics.py`、六配置消融表）· F5 引用反解核验（`src/corpus/citations.py`）+ F6 报告骨架与声明一致率工具。另修：Windows 上 UTF-8 文件未写 encoding（10 个 ERROR）、wizard 测试写死 posix 路径（2 个 FAILED）、`.gitattributes` 行尾规范化。

**基线**：本机 `uv run pytest` **697 passed**（上游 README 声称的 642 在本机可复现为 697）。
