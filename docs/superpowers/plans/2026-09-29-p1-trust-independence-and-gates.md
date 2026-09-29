# P1：可解释信任分 + 独立性重定义 + noisy-OR 两道门 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans 逐 Task 执行。步骤用 `- [ ]` 复选框跟踪。**Task 9（人评标注）无法由执行者完成** —— 它是唯一的人类阻塞项，执行到那里要停下来交给维护者。

**Goal:** 让「放宽来源」之后仍然可审计：每条进入证据链的内容带一个可拆解的可信度依据，`independent_sources` 不因同源转载 / 模板化文案 / 评论区文本而虚高，`contested` 与「没走到评级」都有可查的原因。

**Architecture:** 三块。① `src/corpus/trust.py`：手工配权的 logistic 信任模型 + 特征对象（特征与分数**一起落库**）+ `Vote` / `distinct_publishers` / `noisy_or` / `Thresholds` / `classify` / `roc_thresholds`。② `src/analysis/claims.py`：`evidence_votes()` 作为唯一证据视图（计数、T、判定规则读同一份，不留三条会漂移的查询），`recompute_independence()` 两次折叠 + 写 `ungraded_reason`，`claim_contradictions` 表把矛盾落成可查的对。③ 两道门：分诊门（`pending_grading(min_trust=...)` 决定哪些声明值得花一次模型调用）与判定门（`classify` 决定答案），默认关闭以保持 P0 行为，等标注到位再谈校准。

**Tech Stack:** Python ≥3.11（3.12 / uv）、sqlite3、pydantic v2（配置）、pytest。无新依赖，无向量库，无 sklearn。

**Spec:** `docs/superpowers/specs/2026-09-29-broad-source-credibility-and-multiturn-research-design.md`（v3）。实现 §5（P1）的 §5.1 / §5.2 / §5.4，并遵守 §1.6（计数门的位置）与 §10 的 P1 验收。

---

## 本文与 P0 / P2 计划的差别

同 P2：写于实现之后（2026-09-29），代码不复贴，给接口契约 + `文件:符号` 锚点，实现以 `feat/p1-trust-independence`（PR #6）为准。**spec §10 的 P1 六条里，本计划只覆盖工程五条；第六条（≥80 条人评标注 + macro-F1 / ROC / θ 拟合）在 Task 9，状态是「工具就位、数据为零」，本文不允许用任何看起来像拟合结果的数字填它。**

## Global Constraints

- **可解释优先于准**：分数必须能拆成「源先验 / 作者等级 / 交叉支持 / 可核验实体 / 新鲜度 / 来源方式 − 模板度」。任何"换个黑盒模型更准"的诱惑都要先过这条（spec §5.1 的立场）。
- **`trust.py` 必须在 `src/corpus/` 下，不能在 `src/analysis/`**：`corpus/store.py` 在**写入时**要它，而 `import analysis.trust` 会走 `analysis/__init__` → `claims` → `corpus.store`，是真循环。这条是运行 `uv run python -c "import src.corpus.store"` 撞出来的，不是读出来的。
- **不新增运行时依赖。** 逻辑回归自己写（`math.exp`），ROC 网格搜索自己写。
- **θ 不许假装校准**：`triage_min_trust` 默认 `0.0`（门关闭）；`Thresholds` 是手工先验；`roc_thresholds()` 在类别不足两个时**返回 `None`**，调用方必须保留默认值并说明。
- **A 档必须可复现**：消融的「旧行为」不是靠删代码得到的，而是靠显式参数 `tiering="marker"`。禁止用全局可变开关。
- **计数口径变更要能单独关掉**：`AnalysisConfig.triage_min_trust` 是配置项，不是硬编码；`pending_grading(min_trust=None)` 必须逐字复现 P1 之前的谓词（消融 harness 依赖它）。
- **`unsupported` 与 FEVER 的 `not_enough_information` 不合并**（`docs/evaluation.md` 已声明的口径）。P1 新增的是「根本没走到评级」这一类，别把它塞进 `unsupported` 的语义里。
- **测试禁止挂钟断言**：`freshness()` 接受注入的 `now`；涉及时间的测试一律传死时间。
- **测试基线：836 collected**（P2 完成时实测）。每 Task 结束时全量绿、collected 只增不减。
- **命令一律 `uv run`**；每 Task 一个 commit。

## File Structure

| 路径 | 职责 | 状态 |
|---|---|---|
| `src/corpus/trust.py` | 信任模型、特征、独立性投票与两道门的全部纯函数 | 新建 |
| `src/corpus/store.py` | schema v4：`items.publisher` / `items.trust` / `items.trust_features_json` + 老库 backfill；`add_items(..., tiering=)` 在写入时算 claimable 与 trust | 修改 |
| `src/analysis/claims.py` | `claim_contradictions` 表、`evidence_votes`、`sibling_claims`、`record_contradiction`、`contradictions_for`、`recompute_independence`、`pending_grading(min_trust=)`、判级 prompt 里的 `conflicts` 解析 | 修改 |
| `src/models.py` | `AnalysisConfig.triage_min_trust: float = 0.0` | 修改 |
| `src/corpus/sections.py` | `TIERINGS` / `claimable_of(item, tiering=...)` 接受档位参数 | 修改 |
| `src/orchestrator.py` | 把档位与 trust 需要的字段传给 `add_items` | 修改 |
| `src/research/session.py` | verdict 摘要里列出「未评级 + 原因」，把 `unsupported` 拆成「没找到」与「找到了但可信度不够」 | 修改 |
| `scripts/eval_retrieval.py`、`scripts/eval_claims.py` | `--tiering=sections\|marker`；`results.json` 记下 `tiering` | 修改 |
| `docs/evaluation.md` | 「可信度与独立性（P1 之后）」+「θ 还没有校准」 | 修改 |
| `tests/test_trust_p1.py` | 23 条判据 | 新建 |
| `tests/test_corpus_migration.py` | 由 `test_corpus_v3_migration.py` 改名并扩到 v4 | 修改 |

---

## Task 1: 特征对象与信任分数

**Files:** Create `src/corpus/trust.py`；Test `tests/test_trust_p1.py`

**Interfaces:**
- Produces: `TrustWeights(bias=-1.20, prior=1.10, author_rank=0.45, cross_support=0.55, entity_checkable=0.60, freshness=0.35, provenance=0.80, template_penalty=0.90)`（`frozen`）+ `DEFAULT_WEIGHTS`；`TrustFeatures(source_type, prior, author_rank, cross_support, entity_checkable, freshness, provenance, template, cluster_size=0, time_basis="published")` + `to_dict()` / `from_dict()`；`compute_features(*, source_type, text, author=None, provenance="author", confidence=None, published_at=None, time_basis="published", peers=(), cluster_size=0, meta=None, now=None) -> TrustFeatures`；`trust_score(features, weights=DEFAULT_WEIGHTS) -> float`
- `SOURCE_PRIORS` / `_SOURCE_BY_KEY` 由 `models.SOURCE_SPECS` 派生 —— 源先验**不许**在 trust.py 里再抄一份表
- `PROVENANCE_FACTOR = {"author": 1.0, "transcript": 0.95, "ocr": None, "vlm": 0.6, "legacy_marker": 0.8}`；`None` 由 `provenance_factor(provenance, confidence)` 用该节自己的 `confidence` 顶替

- [ ] **Step 1: 红测试** —— `test_prior_and_numbers_lift_trust_vagueness_lowers_it`：同一源，带数字与实体的一句话比「听说挺厉害」高
- [ ] **Step 2: 实现六个特征** —— `entity_checkable(text)`（CJK + 数字/单位/URL/ISO 日期的启发式）、`author_rank(author, meta)`、`freshness(published_at, now=None, ...)`（半衰 + `time_basis="unknown"` 当中性 0.5，不惩罚）、`template_score(text, peers)`（`shingles(text, 5)` + `jaccard`）、`cross_support = min(1, cluster_size/5)`
- [ ] **Step 3: `legacy_marker` 打折而不是清零** —— `test_legacy_marker_provenance_is_discounted_not_zeroed`。老库的分层是从字符串**推断**的，比**声明**的可信，但把它乘 0 等于宣布历史语料全部无用
- [ ] **Step 4: commit**

## Task 2: 发布者解析 —— 没解析出来就不投票

**Files:** Modify `src/corpus/trust.py`；Test `tests/test_trust_p1.py`

**Interfaces:**
- Produces: `publisher_of(author: Optional[str], locator: str = "", url: str = "") -> Optional[str]`

- [ ] **Step 1: 红测试** —— `test_publisher_resolution_falls_back_then_refuses`：先作者，再 locator / url 里的可识别主体，全部缺失 → `None`
- [ ] **Step 2: `Vote` 定型** —— `Vote(source_type, publisher, trust, prior=None, cluster=None)`；`distinct_publishers(votes) = len({(v.source_type, v.publisher) for v in votes if v.publisher})`
- [ ] **Step 3: 写清这是保守选择** —— `test_anonymous_items_cast_no_vote_at_all`。把匿名条目折成一个桶，会让同一来源的刷屏被读成交叉印证；少数是安全方向

## Task 3: noisy-OR 而不是求和

**Files:** Modify `src/corpus/trust.py`；Test `tests/test_trust_p1.py`

**Interfaces:**
- Produces: `noisy_or(votes) -> float`，`T = 1 − Π(1 − trust_i·d_i)`；`_SAME_FAMILY_DISCOUNT = 0.5`；同一 `(source_type, publisher)` 只贡献一次

- [ ] **Step 1: 红测试（本 Task 的存在理由）** —— `test_a_flood_of_low_trust_items_cannot_pass`：刷 200 条低信任，T 依然低于门。求和没有上界，而这正是「放宽来源」会招来的失效模式
- [ ] **Step 2: 折半规则** —— 首个来源族 `d=1`，之后同族的**新发布者** `d=0.5`。折的是族，不是发布者：同族多作者仍有价值，只是边际递减
- [ ] **Step 3: 单调性上界** —— `T` 恒 < 1，写进注释。谁将来想「归一化到 1」时应能看到这里为什么不能动

## Task 4: 两道门与判定规则

**Files:** Modify `src/corpus/trust.py`；Test `tests/test_trust_p1.py`

**Interfaces:**
- Produces: `Thresholds(supported=0.55, triage=0.30, same_family_prior=0.40, same_family_publishers=3)`（frozen，**手工先验**）；`classify(T, votes, thresholds=Thresholds(), *, contradicted=False) -> "supported"|"contested"|"unsupported"`；`roc_thresholds(pairs: Sequence[tuple]) -> Optional[Thresholds]`

- [ ] **Step 1: 红测试** —— `test_two_publishers_clear_the_gate_only_when_trust_aggregates_high`
- [ ] **Step 2: `supported` 的两个分支** —— `cross_family`（≥2 个来源族）**或** `deep_single`（同族内 ≥`same_family_publishers` 个不同发布者且该族先验 ≥`same_family_prior`）。第二个分支是刻意的：`test_single_forum_deep_corroboration_can_still_be_supported` 钉住「只有贴吧讨论过」不是永久不可判定，否则就违背了这个 fork 的命题
- [ ] **Step 3: `contradicted=True` 短路成 `contested`** —— 先于数值判定
- [ ] **Step 4: `roc_thresholds` 的拒答行为** —— 类别不足两个 / 样本 < 2 → 返回 `None`（`test_roc_refuses_to_invent_thresholds_without_two_classes`）。有数据时网格搜 0.01..0.99 的 F1 最大点，`triage = min(best-0.15, best)` 且 ≥0.01

## Task 5: schema v4 —— 写入时算出 trust 与 claimable

**Files:** Modify `src/corpus/store.py`、`src/corpus/sections.py`、`src/models.py`；Test `tests/test_corpus_migration.py`

**Interfaces:**
- Produces: `items.publisher` / `items.trust` / `items.trust_features_json` 三列；`_backfill_v4_trust()`；`Corpus.add_items(items, run_id=None, tiering="sections")`；`claimable_of(item, tiering="sections")`；`TIERINGS = ("sections", "marker")`；`AnalysisConfig.triage_min_trust: float = 0.0`

- [ ] **Step 1: 红测试** —— `test_pre_v4_database_gains_publisher_and_trust`：v3 老库打开后自动 ALTER + 回填，不丢行
- [ ] **Step 2: `SCHEMA_VERSION = 4`**，`ALTER TABLE` 幂等（先查 `PRAGMA table_info`）
- [ ] **Step 3: 写入即打分** —— `add_items` 里用 `compute_features(..., peers=同簇文本, cluster_size=…)` + `trust_score()` 填 `trust`/`trust_features_json`/`publisher`。`test_store_persists_trust_features_for_audit`
- [ ] **Step 4: 档位分派** —— `claimable_of(item, tiering)`：`marker` 走 `claimable_text`（P0 之前的反解，消融 A 档），`sections` 走声明层级；非法档位 `ValueError`。`test_store_honours_the_tiering_switch` / `test_marker_tiering_ignores_declared_sections` 钉住两档确实不同

## Task 6: 独立信源数 —— 两次折叠

**Files:** Modify `src/analysis/claims.py`；Test `tests/test_trust_p1.py`

**Interfaces:**
- Consumes: Task 1–4 的 `Vote` / `distinct_publishers` / `noisy_or`
- Produces: `ClaimStore.evidence_votes(claim_id) -> List[Vote]`、`ClaimStore.recompute_independence() -> int`（写 `independent_sources`、`trust`、`ungraded_reason`）

- [ ] **Step 1: 红测试** —— `test_independence_collapses_clusters_then_counts_publishers`
- [ ] **Step 2: 顺序不能反** —— ① 每簇留信任最高的条目做代表（模板稿在一个论坛里复制 = 1 票）；② 代表里数不同 `(source_type, publisher)`（同一作者换簇重复 = 1 票，这是旧口径数成独立的案例）。先数再折会把 ① 想防的东西算进去
- [ ] **Step 3: `ungraded_reason` 三种** —— `no_evidence` / `no_identified_publisher` / `no_linked_evidence`（`status='linked'` 但 `trust IS NULL` 的补 0）。`UPDATE ... WHERE id=? AND (...)` 的谓词保证幂等，日报每轮重跑不刷写放大
- [ ] **Step 4: `evidence_votes` 是唯一视图** —— 计数、T、判定三处都读它。留三条独立查询就会漂移成三个「独立信源数」

## Task 7: 矛盾从标签变成可对质

**Files:** Modify `src/analysis/claims.py`、`src/research/session.py`；Test `tests/test_trust_p1.py`

**Interfaces:**
- Produces: `claim_contradictions` 表、`record_contradiction(...)`、`contradictions_for(claim_id)`、`sibling_claims(claim_id, limit=8)`；判级 prompt 输出 `conflicts` 并解析；`research/session.py` 的 verdict 摘要按 `ungraded_reason` 给出 `⏳ 未评级（低于分诊门）` / `🔍 证据不足` / `🏷️ 无可核验发布者` / `❌ 可信度不足（T=…）`

- [ ] **Step 1: 红测试** —— `test_a_recorded_contradiction_forces_contested`、`test_contradiction_pairs_are_recorded_once_and_visible`（同一对反着记两次要去重）
- [ ] **Step 2: 矛盾是判定输入，不是事后注解** —— `recompute_independence()` 里只要 `contradictions_for()` 非空，已 `graded` 的声明立刻改 `contested`：判级模型可能还没跑，但标签不能自相矛盾
- [ ] **Step 3: 兄弟声明** —— `sibling_claims()` 取同一批证据上的邻近声明（`_same_statement` / bigram 重叠），既做矛盾候选，也做 prompt 里的对照文本
- [ ] **Step 4: 报告把「没走到评级」暴露出来** —— `test_anonymous_evidence_reports_why_it_is_ungraded`。P1 之前 `verdict IS NULL` + 计数不足会被过滤器整个丢掉，读者分不清「语料里没有」和「有，但没被评级」

## Task 8: 分诊门 + 消融开关

**Files:** Modify `src/analysis/claims.py`、`src/orchestrator.py`、`scripts/eval_retrieval.py`、`scripts/eval_claims.py`、`data/eval/results.json`、`docs/evaluation.md`

**Interfaces:**
- Consumes: `Thresholds.triage`
- Produces: `ClaimStore.pending_grading(min_sources, limit, min_trust=None)`；`--tiering` CLI 参数；`results.json` 的 `tiering` 字段

- [ ] **Step 1: 谓词逐字对齐** —— `min_trust is None` 时 `WHERE independent_sources>=?`，与 P1 之前完全一致；给了 `min_trust` 才加 `AND trust>=?`。`test_triage_gate_selects_on_aggregated_trust`
- [ ] **Step 2: 地板不能撤** —— `min_sources` 保留，别让一条 0.99 信任的条目挤掉真正被交叉印证的多源声明
- [ ] **Step 3: 默认关闭** —— `triage_min_trust=0.0`。在没有标注之前开启它，等于用一个没校准过的数决定哪些声明配得上一次模型调用
- [ ] **Step 4: A 档可复现** —— `uv run python scripts/eval_retrieval.py --tiering marker` 必须与 `docs/evaluation.md` 表格**逐格一致**（0.733 / 0.883 / 0.640 / 0.830 / 1.000）。跑出来不一样就是这次改动移动了旧行为，要先查清为什么
- [ ] **Step 5: 文档里写「θ 还没有校准」** —— 包括「求和 vs noisy-OR」「发布者解析失败不投票」「同族第 2 个发布者折半」「`unsupported` 拆成两种」四条口径变化

## Task 9: 人评与校准（人类阻塞项，执行者到此为止）

**Files:** `data/eval/claims_labels.json`（由维护者产出）

- [ ] **Step 1: 导出** —— `uv run python scripts/eval_claims.py --export data/corpus.db --tiering=sections`
- [ ] **Step 2: 人工判 50–100 条** `supported / contested / unsupported`，附证据摘录。人评是主证据（不是 F1 先出来说话）
- [ ] **Step 3: 回灌** —— `uv run python scripts/eval_claims.py --score data/eval/claims_labels.json --tiering=sections`，报 per-class precision / recall / F1 + macro-F1（`src/analysis/agreement.py`），并按 `independent_sources` 分 1 / 2 / 3+ 桶看一致率
- [ ] **Step 4: 分桶那一列会同时证实或证伪「论坛回帖是否抬高独立源计数」** —— 如果 P1 之后 1 源桶的一致率没有随源数上升而改善，说明折叠顺序还是错了
- [ ] **Step 5: 才允许** `roc_thresholds()` 产出 θ_s 与 θ_triage 并写进 `AnalysisConfig`；在那之前任何 θ 数字都只能标成假设

---

## Self-Review

**1. Spec 覆盖**：§5.1 特征与可解释 → Task 1/5；§5.2.1 独立性重定义 → Task 2/6；§5.2.2 noisy-OR 与两道门 → Task 3/4/8；§5.2.3 矛盾 → Task 7；§5.3 人评协议 → Task 9；§5.4 消融 → Task 5/8；§1.6 门的位置 → Task 8。§5.5（多轮交互侧的评测）不在本计划，它依赖 P2 的会话数据积累，spec 里也没给指标。

**2. 占位符扫描**：Task 9 的 Step 2「人工判 50–100 条」不是占位符，它是本计划唯一无法自动化的一步，且已写明工具、口径与产出文件名。

**3. 类型一致性**：`Vote.prior` 允许 `None`（手工构造的 Vote 走 `SOURCE_PRIORS` 兜底，`classify` 里已处理）；`TrustFeatures.from_dict` 的每个默认值必须与 `to_dict` 的字段一一对应，否则老库回填出的 JSON 缺键时会静默用默认值掩盖问题；`ungraded_reason` 的三个字面量在 `claims.py` 与 `session.py` 的摘要映射里是同一套，第四种 `⏳ 低于分诊门` 是**没有 reason** 时（`verdict IS NULL` 且 `trust < triage`）的兜底文案，不要给它新增枚举值。

**4. 已知软肋（写在这里，别让它变成意外）**：一个同时有 newsletter 和 HN 账号的人会被数成两个 `(source_type, publisher)`，因此算两个独立信源。防线是 Task 6 的簇折叠，不是发布者匹配。收紧成「全局唯一 publisher」会把「同一媒体不同栏目」也压成一个，所以没这么做。

## 执行记录

- **循环 import 是撞出来的**：`corpus/store.py` 需要写入时打分，最初把 trust 放 `src/analysis/trust.py`，`import src.corpus.store` 直接炸（`analysis/__init__` → `claims` → `corpus.store` → `trust` → `corpus.store`）。搬到 `src/corpus/trust.py` 后消解。
- **一次 INSERT 列数不匹配**：v4 写入侧给了 6 个值、7 列，由测试逼出。
- **below-floor 用例选错源**：要一个低于 `same_family_prior=0.40` 的族，第一版用了 `twitter`（先验 0.40，正好不满足严格大于），换成 `bilibili`（0.35）才是想要的负例。
- **矛盾测试需要 `status='linked'`**：`recompute_independence()` 的 stale 补 0 分支只处理 `linked`，用 `extracted` 构造的 fixture 走不到判级路径。
- **PR 正文里的一处数字**：`tests/test_research_p2.py` 当时写的是 20 条，实际 19 条（`--collect-only` 实测）。P1 之后加 3 条 lineage 测试变成 22 条，见 P2 计划的执行记录 ④。
