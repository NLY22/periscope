# 自助测试手册：自己把效果跑一遍

这份文档只干一件事：让**不信任任何散文的人**（包括评审和你自己）用几条命令把这个项目声称的效果重新量出来，并且知道**哪一步看到的"奇怪现象"是正常的**。

约定：所有命令在仓库根目录跑，都用 `uv run`（venv 是 `uv venv --python 3.12` + `uv sync --extra dev`）。

---

## 档 0 · 先确认机器是干净的（1 分钟）

```bash
uv sync --extra dev
uv run pytest -q            # 期望：全部通过，collected 约 1000+
git status -s               # 期望：空（跑过真实采集后也不该出现 *.db）
```

`git status` 里若冒出 `data/corpus.db`，说明 `.gitignore` 被改坏了 —— 那是一条真缺陷。

---

## 档 1 · 不联网、不花额度：机制与数字（5 分钟）

```bash
uv run python scripts/eval_retrieval.py                      # A–F 消融表
uv run python scripts/eval_retrieval.py --tiering marker     # 复现分层前那一档
uv run python scripts/eval_multiturn.py --branches 2,4,8     # 成本比 / 轮次 / 灌水曲线
uv run python scripts/render_eval_charts.py --check          # 图与 JSON 不一致就非零
```

**该看到什么**

| 检查点 | 期望值 | 它证明什么 |
|---|---|---|
| A 行 vs B 行 | precision@5 `0.640 → 0.680`，nDCG@10 `0.830 → 0.850`，recall@10 **掉** 0.008 | 分层买准度与排序，代价是一点点覆盖 |
| 灌水曲线 | 6 条同文转发：旧口径 `4` 票 → 新口径 `1` 票，判定 `unsupported` | 转载不再被数成多家独立支持 |
| 局部重算 | 深一条恒 `2` 次调用，深全部 `3/5/9`，比值 `1.5/2.5/4.5` | 逐轮交互在宽树上才划算（窄树上几乎不省，别反过来用） |
| `--check` | 退出码 0 | 提交的 SVG 能从 JSON 逐字节重画，图上每个数字都不是手写的 |

**两个"看着不对其实正常"**

1. `--tiering sections` 与 `--tiering marker` 在这份 fixture 上**数字相同**。因为 fixture 的人群文本本来就是用中文标记拼的，两条路径切出的层一致。差异只出现在**已迁移的源**上（它们不再写标记），那条差异由 `tests/test_trust_p1.py::test_marker_tiering_ignores_declared_sections` 钉住，不靠这张表证明。
2. D/E/F 三行带 `(stub)`：扩展与语义腿用的是**词形替身**，只证明通路。引用它们时必须带上这句。

---

## 档 1b · 想要"真模型"的那个数（要 key）

```bash
cp data/config.example.json data/config.json     # 在 data/config.json 里填 ai.provider / key 环境变量名
export AGNES_API_KEY=...                          # 或你在配置里指定的其它变量名
uv run python scripts/eval_retrieval.py --expander llm
uv run python scripts/eval_retrieval.py --embedder provider --embedding-model <你的向量模型名>
```

**取不到腿时的正确行为是"降级并说出来"，不是偷偷换回 stub**。没有 `data/config.json` 或没导出 key 时，你会看到：

```
腿的实际状态：
- 没有 data\config.json（先 cp data/config.example.json data/config.json）
- 扩展腿记为 off（不是 stub）：拿不到可用配置或密钥
请求的腿（expander=llm, embedder=stub）实际降级为（off, stub）—— 表中相应行不是真模型数字。
```

同时 D 行 `recall@5` 会从 stub 的 `0.7917` 掉回 `0.7583`（等于 B 行）——**这正是"没有扩展时应该有的样子"**。真腿的结果写到 `data/eval/results.llmstub.json` 这类名字，不覆盖文档表锚定的 `results.json`。

---

## 档 2 · 真实语料上，分层到底拦住了什么（15 分钟）

```bash
uv run periscope --hours 24          # 无 key 也会跑：采集与入库照常，评级段自动降级
```

然后直接问数据库，谁是"只有人群发言、没有作者层"的条目：

```bash
uv run python -c "import sqlite3;c=sqlite3.connect('data/corpus.db');c.row_factory=sqlite3.Row;
print([dict(r) for r in c.execute('select source_type, count(*) n, sum(length(claimable)=0) empty from items group by source_type order by empty desc')])"
```

**看点**：`empty` 大的那一行（典型是 `hackernews`）就是 P0 那个 bug 的现场 —— 一条链接帖的"正文"整段是评论。没有分层时，这些评论会被蒸馏成声明、被算作独立信源、被引进报告引用。这就是"现役 bug 而不是预防性重构"的意思。

两句预防误会的话：`no such table: items` 是你还没跑上面那条采集（`sqlite3.connect` 会静默建一个空文件）；`empty` 全 0 说明这一批源每条都有作者层，这个查询抓的是"**整条**只有人群发言"的源，不是给总数用的。

顺手抽查一条声明：

```bash
uv run python scripts/eval_claims.py --export data/corpus.db --tiering sections
head -40 data/eval/claims_labels.json      # 每条声明都带 claimable 摘录与 evidence 列表
```

---

## 档 3 · 人眼看面板（仓库自己没验证过的部分，交给你）

```bash
uv run periscope-web --data-dir data       # http://localhost:8790
```

三处必查：① 证据库检索**默认是跨层的**（回帖里出现的词也该带你看主帖，这是设计），要只看作者亲写层就把 URL 改成 `/api/search?q=<词>&tier=claimable`——人群层的话该消失，作者层的话该留着；写错 `tier` 会直接 400，不会悄悄放宽；② 核查台的 verdict 是否出现 `⏳ 未评级 / 🔍 证据不足 / 🏷️ 无可核验发布者 / ❌ 可信度不足` 这些类别；③ 研究面板的**轮次时间线、可编辑章节、待回答卡片**能不能真点动（`ask` 只在有 key 时出现）。

**这一档是有意留给你的**：自动化只证到"界面读的字段与端点返回的字段一致"（有测试钉）与"脚本能过 `node --check`"，**像素与交互从未被人眼验过**。你看到 anything 不对，那都是新缺陷，直接开 issue。

---

## 档 4 · 只有你能做的一步：人评（项目唯一的效果空洞）

```bash
uv run python scripts/eval_claims.py --export data/corpus.db --blind --tiering sections
# 只填 data/eval/claims_labels.json 里的 human_verdict；--blind 把机器判定挪到 .machine.json，避免被带着走
uv run python scripts/eval_claims.py --score data/eval/claims_labels.json --tiering sections
```

50–100 条，**三类（supported / contested / unsupported）都要覆盖**：macro-F1 固定对三类取平均，**样本里没有 contested 时，即使全对也只有 0.667**（这条被测试写成期望值，不是注解）。出分后会同时给 `thresholds_suggested`（θ_s / θ_triage）；在此之前配置里的阈值是**手工先验**，`triage_min_trust=0.0` 等于信任门关闭。

---

## 引用任何数字前，三件必须说清的事

1. **档位**：`--tiering sections` 还是 `marker`（结果 JSON 里记着）。
2. **单位**：成本是 **LLM 调用数**，不是毫秒 —— 本仓库禁止挂钟断言，所以"快了多少"这种话这里没有数据支撑。
3. **腿的成色**：`stub` / `llm` / `provider` / `off`，看 `provenance.legs`。

## 已知的两条软肋（先说比被抓住好）

- 判定门要求跨族宽度，所以**一篇独立硬稿单独不构成 `supported`**（实测 `T=0.7998` 已越过 `0.55` 仍判 `unsupported`）。这是设计意图，但**没过人评**。
- 同一作者换两个 `source_type` 会被数成 2 个独立发布者（实测 `T=0.9137 → supported`）。防线是第一步的簇折叠，不是发布者匹配。

## 还有一条不是缺陷但容易误会

`.github/workflows/tests.yml` 在**这个平台上不会被执行**（PR 的 `check_tasks_num` 一直是 0）。别把"仓库里有 CI 配置"当成"有 CI 兜底"：每个 PR 仍需人工过一遍，本仓库的护栏实际是**测试**，不是流水线。
