# 取证检索与证据分层（机制说明）

这份文档讲清楚五件事：**噪声从哪来、怎么把它分开、怎么在分开之后仍然找得到、怎么知道有没有找到、以及每条证据值多少信任。**

## 噪声从哪来

上游 Horizon 的源以搜索引擎索引到的内容为主，正文即事实载体。本 fork 把来源放宽到论坛、视频与评论区之后，抓取器把这些文字**拼进了同一条 `content`**：楼主帖与跟帖、推文与回复、视频简介与 CC 字幕、B 站评论。

不分层的后果很具体：一句回帖会被蒸馏成"声明"、会被当作一个"独立信源"投票、会被引进研究报告的引用里。这三件事都没有资格发生。

## 分层怎么做（`src/models.py` 的 `Section` + `corpus.db` schema v4）

**判据是 scraper 声明的类型化字段，不是字符串约定。** 早期实现靠正文里的分隔标记（中文 `【评论区 Top】` 一类，或英文 `--- Top Comments ---`）反解，两条链路各认一套词表，结果是 HN / Reddit / Twitter 的评论在日报链路被正确剔除、在取证链路却整段被当作作者亲写。现在每个 emitter 产出 `ContentItem.sections`：

| 字段 | 含义 |
|---|---|
| `Section.tier` | `primary`（作者亲写，可承载声明）/ `community`（人群发言，只能当线索） |
| `Section.provenance` | `author` / `transcript` / `ocr` / `vlm` / `legacy_marker` —— 这段文字**怎么来的** |
| `Section.asserted` / `confidence` | 是否算作者断言；OCR 之类带置信度的通路用它打折 |
| `Section.author` / `locator` | 楼层作者与可定位锚点（Discourse 的 `locator` 就是 `#<post_number>`，引用可以精确到一层） |

`content` 由 `sections_to_content()` 拼出来（`item.rebuild_content()`），所以面板与全文检索看到的还是完整文本。

- `items` 有 `claimable` 列，另有只索引 `(title, claimable)` 的 FTS5 镜像 `claim_fts`；`items_fts` 保持全量，供面板做全文检索（要的是召回）。
- 唯一分派入口是 `corpus.sections.claimable_of(item, tiering)`：`tiering="sections"` 读声明的层级；`tiering="marker"` 复现 P0 之前的标记反解，**只为消融 A 档与老库回填保留**，生产路径不走它。
- **取不到的源走导入，不走抓取。** 设计排除验证码打码、签名逆向与多账号池，所以小红书 / 贴吧 / 登录墙论坛的正当通路是**用户自己导出、本仓库按声明的层级入库**：`hz_corpus_import`（MCP）、面板「导入你导出的内容」一节（`POST /api/import`，先「只校验」再「入库」）、`scripts/import_corpus.py`（CLI）。**校验与入库是同一段代码**（`prepare_import`），所以预览不会与结果不符；CLI 早先用严格解析器做 `--dry-run`，会把"30 条里有 1 个错字"报成整体失败，而真正的导入会收 29 条 —— 那条不一致已修掉。导入的分层同样是声明式的：`community` 文字照样只能当线索；没有分层信息的整段导出会落成 `legacy_marker` 并被信任分打折；来源方式记为 `manual_export`（0.85），因为 Periscope 没有亲眼取到那一页。`source_type` 必须从已注册的 14 个族里选，因为它决定源先验与独立性计数里的那个"族"，人不许在导入里给自己发明先验。样例负载见 `data/export.example.json` —— `tests/test_corpus_import.py` 直接解析这个随仓库发布的文件（含 lead-only、`asserted=false`、OCR 带置信度三种情形），所以文档里的样例不会跟实现脱节。
- 旧库自动升级（schema v2→v3→v4）：`ALTER` 加列 → 回填 `locator` / `time_basis` / `sections_json` / `publisher` / `trust` → 重建两个 FTS 镜像。回填出来的层标 `provenance="legacy_marker"`，信任分按 0.8 折扣（见下节）。**不回填会让分层前已存在的声明突然查不到任何证据**，静默变成 `unsupported`。
- 消费侧：声明只从 `claimable` 蒸馏（`ClaimAnalyzer._author_text`）、证据只关联 `claimable`（`search(tier="claimable")`）、评级摘录只取 `claimable`、研究取证的 snippet 同样只用 `claimable`；`processing/content.split_item_content` 让日报链路和取证链路对同一条目切出同样的层。
- **VLM 生成的画面描述进不了声明层，这是类型不变式而不是约定。** `Section` 的校验器把 `provenance="vlm"` 的块强制 `asserted=False`，而 `claimable_from_sections` 只收 `asserted` 的 primary 段（spec §7 要求如此）。留给各 scraper 自己写 `asserted=False`，等于离一个忘写的参数只差一行 —— 那时一句对画面的猜测会变成对世界的断言。`provenance="ocr"` 则保留 `asserted`，只按 `confidence` 打折：图上写的字往往就是作者本人的主张。
- 开关：`analysis.claimable_only`、`research.claimable_only`（默认 `true`）。关掉即回到分层前的行为，这是消融表里 A 与 B 两列的区别。

## 每条证据值多少信任（`src/corpus/trust.py`，P1）

分层只回答"这话是不是作者说的"，不回答"该信多少"。信任分是**手工配权的 logistic 模型**，六个可解释特征减去一个惩罚项：

```
trust = σ(bias + w·(源先验, 作者等级, 交叉支持, 可核验实体, 新鲜度, 来源方式) − w·模板度)
```

- **特征与分数一起落库**（`items.trust` + `items.trust_features_json`），因为"为什么这条被当成证据"必须一直答得出来；只存一个数字等于把黑盒搬进报告。
- `provenance` 折扣：`author` 1.0、`transcript` 0.95、`ocr` 用该节自己的 `confidence`、`vlm` 0.6、`legacy_marker` **0.8**（老库的层是推断出来的，不是声明的，打折而不是清零）。
- 源先验取自 `SOURCE_SPECS[*].credibility_prior`，与注册表同一张表，不再抄第二份。
- 新鲜度读写入时传入的时钟：`Corpus.add_items(..., now=)` 可钉住，否则同一内容在不同日期入库会得到不同的分数。

**独立信源数**不再是簇数：先按簇折叠重复内容（每簇只留信任最高的代表），再数不同的 `(source_type, publisher)`；**发布者解析不出来的条目不投票**。声明级信任 `T = 1 − Π(1 − trust_i·d_i)`（noisy-OR，同族第二个发布者折半），求和没有上界，足够多的低质源能把任何结论刷成 supported。

两道门：分诊门（`analysis.triage_min_trust`，默认 `0` 即关闭）决定哪些声明值得花一次模型调用；判定门 `corpus.trust.classify` 要求**跨族宽度**或**同族 ≥3 个发布者**。因此一篇独立硬稿单独仍判 `unsupported` —— 这是有意为之，但阈值未经人工标注校准，见 [docs/evaluation.md](evaluation.md)。

## 分层之后怎么还找得到（`src/corpus/retrieval.py`）

只准的检索会漏——子问题的措辞和语料的措辞往往不同。三条腿：

1. **词面（免费，永远在）**：`discriminating_terms` 按文档频率挑稀有而存在的词形，FTS5 trigram BM25，短词回退转义 `LIKE`。
2. **查询扩展**：`src/ai/expand.py` 让模型给出"语料会用的词形"（中英、别名、实体名、数字），走共享的 `CachingAIClient`，所以重复提问不再花钱。
3. **语义路**：`src/ai/embeddings.py`（OpenAI 兼容 `/embeddings`）+ `src/corpus/semantic.py`（按模型分键的 float32 向量表），与词面结果做 **RRF 融合**——BM25 分数和余弦不可比，序可比。

语义路默认关闭：`retrieval.semantic = false`，且必须显式给 `embedding_model`。原因很实际——很多 hub 不提供 embeddings 端点，每轮报错比静默退回词面更糟。向量索引发生在研究会话的 `investigate()` 里，不在日报流水线里：没人提问就不该为向量付额度。

## 找不到时怎么加宽（`src/research/session.py`）

一个子问题不再只有一次机会。阶梯顺序是**先免费后付费**：

```
baseline → widen_terms(词数 4→2) → switch_source_family(未查过的来源族)
        → rewrite_query(模型改写，可选 decide 接缝) → collect_keywords(GDELT / Google News 现采，需显式开启)
```

- 每次尝试写入 `research_actions`（第几轮、动作、查询、来源族、新增条数）。
- `research.max_retrieval_rounds` 与 `research.min_evidence_for_answer` 控制预算与停止条件。
- 未回答的子问题在报告里输出「取证尝试：动作(+条数) …」，把"语料里确实没有"和"这次的问法没查到"分开。
- `retrieval.on_demand_collection` 默认 `false`：它会在会话中途联网。

## 报告骨架与引用核验

- `research.report_template`（默认 `auto`）按主问题推断骨架：背景调查 / 市场调研 / 方法探索，各自的节由关键词规则归属；`flat` 关掉。空节写作"未覆盖"，不用话术填平。**时间线、分歧点、未决问题三块只由存储数据算出**（发布日期、`contested` 判定、未回答的子问题与加宽记录），不经模型。
- `src/corpus/citations.py` 从成品报告反解引用：幽灵引用、指向不存在条目、列了却没引用、以及**被引条目只有人群发言**。结论随报告尾部输出，外部读者可自查。
- 报告里的核查摘要会列出**没走到评级的声明及其原因**：`⏳ 未评级（低于分诊门）` / `🔍 证据不足` / `🏷️ 无可核验发布者` / `❌ 可信度不足（T=…）`。"语料里没有"和"有但没敢信"是两件事。

## 多轮共创（`src/research/moves.py` + `drafts.py`，P2）

调研不是一次性投影。研究循环的顶层动词是 `AskUser | Rescope | Deepen | Finalize`，一轮调用 `step()` 只重算被点名的分支，返回 `TurnResult`（`revision` / `changed_sections` / `new_evidence` / `verdict_changes` / `pending_request`）。

- 报告是**带 revision 的草稿工件**：每节有 `locked` / `hash` / `stale`。上游证据变了而这一节被用户改过或锁定时，**只标 `stale`，不覆盖用户的字**；被删掉分支的章节也不会消失，同样带 `stale` 留下。
- 章节身份是标题的 slug，所以引用编号每个 revision 可以重排，而用户的锁仍落在同一节上。每节还存 `subquestion_id` / `evidence_ids` / `verdicts`，面板的证据条数与"分歧点"信号由它算出。
- `ask` 只在有模型时才可能被选中：没有可用 LLM 时的确定性回退只会 `deepen` / `finalize`，所以联系不到模型的会话仍会终止，而不是开始连环追问。
- 系统主动提的问题落在 `research_requests`，状态 `open / answered / skipped`；会话停在 `awaiting_user` 时可以在列表里看到并被恢复。
- `followup()` 保留为"推进直到定稿"的宏，老调用方一字不改。

入口：MCP `hz_research_step` / `_draft` / `_edit` / `_answer`；Web `POST /api/research/{sid}/step`、`GET .../draft`、`PATCH .../draft/sections/{id}`、`POST .../requests/{id}`。

## 复现

```bash
# 分层与检索
uv run pytest tests/test_evidence_tiers.py tests/test_hybrid_retrieval.py \
              tests/test_research_widening.py tests/test_report_templates.py \
              tests/test_citation_audit.py tests/test_eval_metrics.py tests/test_claim_agreement.py

# P0 的类型化 sections / 注册表 / 迁移 / 分层守护
uv run pytest tests/test_section_model.py tests/test_claimable_dispatch.py \
              tests/test_corpus_migration.py tests/test_locator_dedup.py \
              tests/test_source_registry.py tests/test_tier_guard.py

# P1 的信任分与独立性、P2 的逐轮动词、多轮成本 harness
uv run pytest tests/test_trust_p1.py tests/test_research_p2.py tests/test_eval_multiturn.py

# 用户导出入库（分层声明、manual_export 折扣、幂等与逐条报错）
uv run pytest tests/test_corpus_import.py

uv run python scripts/eval_retrieval.py           # 六配置消融表 -> data/eval/results.json
uv run python scripts/eval_retrieval.py --tiering marker   # 复现分层前的 A 档
uv run python scripts/eval_multiturn.py           # 调用数比值 / 轮次 / 灌水曲线
uv run python scripts/eval_claims.py --export data/corpus.db --tiering=sections   # 导出人工标注表
uv run python scripts/eval_claims.py --score data/eval/claims_labels.json --tiering=sections
```

指标读数与已知不足见 [docs/evaluation.md](evaluation.md)。
