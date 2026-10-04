# 贡献给 Periscope

Periscope 的全部工作可以压成一句话：**把来源放宽到论坛、视频与评论区，再用分层、交叉印证与可解释打分把必然下降的质量补回来。** 因此这里的贡献规则只服务于一件事：改动之后，这个补质量的机制还成立吗？

## 先读这四份

| 文件 | 为什么先读 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 三张结构性图：一条内容从噪声到可用证据走的路、研究会话状态机、一轮交互的时序。每张图下面写着代码落点 |
| [`docs/retrieval.md`](docs/retrieval.md) | 分层、可信度、独立性、加宽阶梯、多轮草稿的机制 |
| [`docs/evaluation.md`](docs/evaluation.md) | 现有数字是怎么测出来的，以及**哪些主张目前不许说** |
| [`docs/superpowers/specs/`](docs/superpowers/specs/) | 设计 spec（含 §9 逻辑接缝自查与 §15 交付记录）与三期实现计划 |

## 这个仓库的硬约束

这些不是风格偏好，每一条都有守护测试或明确的失效后果。

1. **分层靠声明，不靠字符串。** 新的抓取器必须把作者文本与人群文本放进 `ContentItem.sections`（`tier="primary"|"community"`，带 `author` / `provenance` / 必要时 `locator`），`content` 由 sections 拼回。`src/scrapers/` 里**禁止出现任何分层标记字面量**（中文 `【…】` 与英文 `--- Top Comments ---` 都不行）—— 曾经有两套互不相交的标记词表，结果三个现役源的评论在取证链路被当成作者亲写，这条规则就是那次事故的产物。守护测试：`tests/test_tier_guard.py`。
2. **人群文本只能是线索。** 声明只能从 `claimable` 蒸馏、证据只与 `claimable` 关联、独立信源计数只认有发布者的条目。如果你的改动让一句回帖能变成"信源"，那是 bug。
3. **加一个源只准动两处。** 源元数据写在 `models.py` 的 `SOURCE_SPECS`（key / label / `credibility_prior` / `login_required` / `config_field` / `item_fields`），scraper 绑定写在 `src/sources/registry.py`。第四处都不许出现手工同步点。守护测试：`tests/test_source_registry.py`。
4. **不许写挂钟断言。** 凡涉及时间的代码都要接受可注入的 `clock` / `now` / `sleeper` / `rng`，测试里传固定值。理由很实际：Windows 时钟粒度约 15ms，挂钟断言会在跑全量时随机变红；而且文档里引用到小数点后四位的数字会随日历过期（`Corpus.add_items(..., now=)` 就是这么加上的）。
5. **`src/models.py` 只准 import stdlib + pydantic。** 它会反向被 `corpus/store.py` import，破这条就是死循环。共享逻辑放 `src/corpus/`，别放 `src/analysis/`（`analysis/__init__` 会拉起 `claims` → `corpus.store`）。
6. **新增能力要么进消融表，要么别说它有用。** 每个新特性都要么在 `scripts/eval_*.py` 里有一列可复现的数字，要么在 `docs/evaluation.md` 的"已知不足"里写清"能力已实现、效果未主张"。后者是本项目的诚实底线。
7. **改了面向用户的行为，就在 `CHANGELOG.md` 对应的 PR 小节里加一条。** 本仓库还没有任何 tag，`pyproject.toml` 仍是 `0.1.0`，所以变更记录按 PR 组织、不按版本号编造发布；版本号与打 tag 由维护者决定。
8. **文档要说真话，而且要有测试说真话。** 配置项、MCP 工具名与数量、README 的计数都由 `tests/test_docs_match_code.py` 钉住；改了模型字段或加工具而没改文档，那条测试会红。
9. **改了这些名字，就要改图：`Session.status` 的取值、`SubQuestion.status`、`Turn.role`、`Move` 的四个动词、corpus.db 的表（12 普通 + 2 FTS5 虚表）、六种可达性判定、五步加宽阶梯、`SOURCE_SPECS` 的源族数量。** 它们是 `docs/architecture.md` 三张图的图元，护栏是**双向**的：代码里有而图里没画会红，图里画了而代码里找不到也会红（后者连文档里反引号的符号名都要在 `src/` + `scripts/` + 面板 JS 里真的存在）。为什么这么严：图上写着的名字看起来就是事实，而没有任何编译器会去读它。加宽阶梯、状态机、表结构都还在动，所以这条规则的预期是**它会红** —— 红了就画图，别删断言。

## 环境

```bash
uv venv --python 3.12
uv sync --extra dev
uv run pytest                       # 全量
uv run python scripts/eval_retrieval.py
uv run python scripts/eval_multiturn.py
```

## 本平台上没有 CI，验证是你的责任

`.github/workflows/` 里的文件是 GitHub 语法，AtomGit 不执行（每个 PR 的 `check_tasks_num` 都是 0，已实测确认）。所以：

- 提交前本地跑全量测试，**collected 数只增不减**；
- 修 bug 时先写一条能红的测试，把失败输出贴进 commit message（本仓库的 P0 就是这么留证据的）；
- PR 正文里给出可复现的命令与实测数字，而不是「测试通过」。

## PR 正文希望包含的内容

1. 这是什么改动，挂在 spec 的哪一节（或哪条现役缺陷）；
2. 改了什么，指到 `文件:符号`；
3. 验收表：判据 / 怎么验 / **结果数字**；
4. **明确没做**：这一期故意不碰什么；
5. 与计划或设计的偏差，以及为什么；
6. 已知不好看的部分照写：本项目靠承认限制来换取可信，藏起来反而失去意义。

## 贡献处理画像（Profile）

画像定义某个内容域该收什么、怎么打分、生成哪些输出块，是 prompt + JSON，不需要改 Python。新增 `profiles/<id>/`：`profile.json`（契约与输出块）、`match.md`（路由规则）、`analysis.md`（评分标准）、`enrichment.md`（输出说明）。内置画像参与自动路由，所以贡献的画像要能描述一个清晰的内容域、对别人也有用、与现有画像有实质差别。个人阈值与话题去重偏好请放在运行期配置里，不要塞进画像。完整格式见 [docs/profiles.md](docs/profiles.md#contributing-a-profile)。

## 贡献信息源

信息源改动**直接开 PR 或 issue**，没有另一条投稿渠道。

新增一个源通常只需要：`SOURCE_SPECS` 一条 + `src/sources/registry.py` 一个工厂绑定 + 抓取器（如果要新增）+ `docs/scrapers.md` 一节。**先确认取得到**：中文 UGC 平台默认有登录墙或验证码，请先在 PR 里贴出实际响应，而不是假设能抓。判别这件事已经做成工具，跑一条就有结论（不加 `--online` 它一个请求都不发）：

```bash
uv run python scripts/spike_sources.py --source tieba --kw <吧名或关键词> --online
```

贴吧的教训记录在 spec §14.2：列表页可达但楼层全部 `HTTP 403`，于是它连"分层"都无法验证。**取不到的源不要写抓取代码**，走导出入库那条路（见 `docs/retrieval.md` 的 §6.1 一节与面板的「导入你导出的内容」）。

## 行为准则与安全披露

见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 与 [SECURITY.md](SECURITY.md)。两个文件里写的联系渠道都以本仓库为准。
