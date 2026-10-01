# 检索与取证评测（Phase F 的验收依据）

这份文档回答一个问题：**Periscope 在 Horizon 之上加的这几层，到底让"从噪声里取出可用证据"变好了多少？**
所有数字都可复现：

```bash
uv run python scripts/eval_retrieval.py          # 打印消融表，并写 data/eval/results.json
uv run pytest tests/test_eval_metrics.py         # 指标实现 + harness 的行为断言
```

## 方法

- **语料**：`data/eval/corpus_fixture.json`，19 条 —— 10 条承载答案的信号项 + 9 条**同词不同事**的干扰项（别家的融资轮、`Series B` 名词解释、Northwind 招人、与融资无关的 OpenForge 帖子、`did not close` 的英文轶事）。干扰项是刻意的：没有它们，recall@10 恒等于 1，任何改动都"看起来有效"。
- **标注**：`data/eval/queries.json`，5 个子问题，分级相关度 `2 = 作者亲写文本直接判定该问题（含相互矛盾的说法）`，`1 = 切题但只给部分`。**只在评论/回复里出现的说法一律标 0**，因此把人群噪声当证据的检索会被扣分——这正是分层要解决的问题。
- **指标**：标准 IR 口径，未自创（`src/corpus/metrics.py`）：Recall@5/@10、Precision@5、nDCG@10（增益 `2^g - 1`）、MRR。
- **消融配置**：同一份语料、同一批查询，只换检索策略。

## 结果（2026-09-26 本机运行，Python 3.12 / SQLite FTS5）

| 配置 | recall@5 | recall@10 | precision@5 | nDCG@10 | MRR |
|---|---|---|---|---|---|
| A 旧行为（全量文本，单词条） | 0.733 | 0.883 | 0.640 | 0.830 | 1.000 |
| B A+证据分层 | 0.758 | 0.875 | **0.680** | **0.850** | 1.000 |
| C B+放宽词条 | 0.675 | **1.000** | 0.600 | 0.891 | 1.000 |
| D B+查询扩展（stub） | **0.792** | 1.000 | **0.720** | **0.934** | 1.000 |
| E B+语义路（stub） | 0.758 | 0.967 | 0.680 | 0.904 | 1.000 |
| F 全开（分层+扩展+语义+放宽） | 0.758 | **1.000** | 0.680 | 0.928 | 1.000 |

读法（包括不好看的部分）：

1. **证据分层买的是准度和排序，不是覆盖**：B 相对 A，precision@5 `0.640 → 0.680`、nDCG@10 `0.830 → 0.850`，而 recall@10 反而从 `0.883` 掉到 `0.875` —— 有极少数条目只有评论区提到过，分层后就不算证据了。这是有意的取舍：报告宁可少一条来源，也不能把一个陌生人的回帖当信源。
2. **查询扩展是当前收益最大的一层**（D）：召回、准度、nDCG 全部最高。注意表中用的是**词形替身（stub）**，它证明"只要扩展给出跨语言/别名词形，指标就会涨"这条通路是通的，**不代表真实模型的效果**；真实数字要 `--expander llm` 跑。
3. **放宽词条换覆盖、丢精度**（C）：recall@10 满格，但 recall@5 `0.675`、precision@5 `0.600` 全表最低。所以 F3 的加宽阶梯把它排在第二位且有轮次预算，不是无脑放宽。
4. **语义路（E）在 19 条的小语料上只是"补漏"**：recall@10 `0.967`。它的价值要到语料上万条时才显现，现在的数字只能证明接线正确。

## 分层的实现方式（P0 之后变了）

上面那张表的 B 行（"A+证据分层"）是在**标记反解**的实现上测出来的：`src/corpus/sections.py` 扫 5 个精确中文字符串（`【评论区 Top】` 等），把条目正文切成作者层 / 人群层，只有作者层进 `claimable` 列、被 `claim_fts` 索引、参与证据关联与独立源计数。

P0 把判据换成了 scraper **声明**的类型化字段 `ContentItem.sections`（`Section(tier=…, author=…, provenance=…, asserted=…)`）。对评测有三点影响：

1. **这张表的数字仍然成立，但口径要说清。** A/B 两行测的是同一个 fixture（`data/eval/corpus_fixture.json`，其中人群文本用中文标记拼接），marker 路径与 sections 路径在这份语料上切出的层是一样的。要复现 A/B 行请用 `--tiering=marker`，该路径被显式保留。
2. **marker 路径不能读英文标记，而这一点对解读 A 行是实质性的。** `reddit.py`、`hackernews.py`、`twitter.py` 拼接的是 `--- Top Comments ---`，不在那 5 个中文标记里。所以在 P0 之前，这三个源的人群文本**整段**被当作作者亲写进入 `claimable`（HN 链接帖最坏：正文 100% 是评论）。当前 fixture 不含这类条目，因此表里的数字没有反映这个泄漏；换句话说，**B 行"分层买到的准度"是被低估的下界**，把噪声源真正接进来以后差距只会更大。这是 P0 的验收测试（`tests/test_tier_guard.py`）先在 `main` 上跑红的原因。
3. **老库的层是推断出来的，不是声明的。** schema v3 之前的行在打开数据库时由 marker 路径重建，并统一打上 `provenance="legacy_marker"`。引用这些行时不要说"分层问题已彻底修复"——老数据的层仍来自字符串约定。

另有两处与评测口径直接相关的实现变化：`independent_sources` 的计数逻辑**未变**（P1 才改），`claimable_only` 消融开关**未变**；变的是它读的是 sections 而非标记。日报侧的 `processing/content.py:split_content` 保留，新增 `split_item_content` 优先读 sections，以保证日报与取证两条链路对同一条目切出同样的层。

## 已知不足

- **样本量**：19 条 / 5 问，够验证机制方向和回归，不够当论文级结论；扩充路径是接真实 `corpus.db` 抽样 + 人工标注。
- **stub 与真实模型不能混谈**：D/E/F 的扩展与语义腿用的是脚本里声明的同义形替身。引用这张表时必须带上这句限制。
- **核查层的准确性尚未测**：声明评级（supported / contested / unsupported）与人工判断的一致率还没有数据。工具已就位，缺的是标注本身：

  ```bash
  uv run python scripts/eval_claims.py --export data/corpus.db                    # 导出待标注表（附证据摘录）
  uv run python scripts/eval_claims.py --score  data/eval/claims_labels.json      # 一致率 + 按独立信源数分桶
  ```

  口径是普通的 per-class precision / recall / F1 + macro-F1（`src/analysis/agreement.py`，含手算用例），另按 `independent_sources` 分 1 / 2 / 3+ 桶看人工一致率随源数怎么变 —— 这一条会同时证伪或证实"论坛回帖是否抬高独立源计数"。下一步需要的是 50–100 条人工标注，人评是主证据。
- `unsupported` 与 FEVER 式 `not_enough_information` **不合并**：本流水线的 `unsupported` 指"存储的摘录无法确认"，更接近证据不足而非反驳，合并会悄悄改变数字的含义。
- **报告骨架与引用核验不在这张表里**：`docs/retrieval.md` 描述的模板（背景调查 / 市场调研 / 方法探索）和 `src/corpus/citations.py` 的引用反解是结构性保证，不是排序指标，由测试验证而非消融表。
