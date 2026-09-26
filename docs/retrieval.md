# 取证检索与证据分层（Phase F 机制说明）

这份文档讲清楚四件事：**噪声从哪来、怎么把它分开、怎么在分开之后仍然找得到、以及怎么知道有没有找到。**

## 噪声从哪来

上游 Horizon 的源以搜索引擎索引到的内容为主，正文即事实载体。本 fork 把来源放宽到论坛、视频与评论区之后，抓取器把这些文字**拼进了同一条 `content`**：

| 标记 | 来源 | 层 |
|---|---|---|
| （正文，无标记） | RSS 全文、报道正文、视频简介、论坛楼主帖 | `primary` |
| `【视频字幕节选】` | 创作者口述的 CC 字幕 | `primary` |
| `【评论区 Top】`、`【回复精选】`、`【楼层讨论】` | 陌生人的回帖与后续楼层 | `community` |

不分层的后果很具体：一句回帖会被蒸馏成"声明"、会被当作一个"独立信源"投票、会被引进研究报告的引用里。这三件事都没有资格发生。

## 分层怎么做（`src/corpus/sections.py` + `corpus.db` schema v2）

- 按标记行切分正文，产出 `primary` / `community` 两段文本；正文首段与创作者字幕属 `primary`。
- `items` 增加 `claimable` 列，并新建只索引 `(title, claimable)` 的 FTS5 镜像 `claim_fts`；原 `items_fts` 保持全量，供面板做全文检索（要的是召回）。
- 旧库自动升级：`ALTER` 加列 → 从 `content` 回填 `claimable` → 重建两个 FTS 镜像。**不回填会让分层前已存在的声明突然查不到任何证据**，静默变成 `unsupported`。
- 消费侧：声明只从 `claimable` 蒸馏（`ClaimAnalyzer._author_text`）、证据只关联 `claimable`（`search(tier="claimable")`）、评级摘录只取 `claimable`、研究取证的 snippet 同样只用 `claimable`。
- 开关：`analysis.claimable_only`、`research.claimable_only`（默认 `true`）。关掉即回到 Phase C/D 的行为，这是消融表里 A 与 B 两列的区别。

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

## 复现

```bash
uv run pytest tests/test_evidence_tiers.py tests/test_hybrid_retrieval.py \
              tests/test_research_widening.py tests/test_report_templates.py \
              tests/test_citation_audit.py tests/test_eval_metrics.py tests/test_claim_agreement.py

uv run python scripts/eval_retrieval.py          # 六配置消融表 -> data/eval/results.json
uv run python scripts/eval_claims.py --export data/corpus.db   # 导出人工标注表
uv run python scripts/eval_claims.py --score data/eval/claims_labels.json  # 一致率与分桶
```

指标读数与已知不足见 [docs/evaluation.md](evaluation.md)。
