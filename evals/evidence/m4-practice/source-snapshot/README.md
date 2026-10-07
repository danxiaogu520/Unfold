# Unfold / 知脉

通过用途问答确定学习边界，由 LLM 以结构化输出建立大纲、生成讲解和练习，并在适当位置停止细分。内容支持公式、图片、可运行示例与交互演示，随着探索逐步保存和维护。

当前阶段：**M0～M3 已验收，M4 技术验证完成**。本地应用支持用途问答、版本化设定、大纲、单层展开、讲解、叶子、结构预览与保存；180 项 Python、7 项浏览器测试通过。真实宽/窄链路已保存，内容问题与历史失败保留。任务恢复、取消与事件回退已验收，练习、图示与来源图像已接入，继续 M5 网页隔离和资源 Agent。更新日期：2026-10-07。

工程建设围绕可恢复的生成任务、教学资源制作 Agent 和知识上下文管理展开，持续保留评测与故障证据，用于 Agent / 后端面试展示。

[M4 报告](docs/reports/m4-practice-resources-2026-10-07.md) 记录真实出题、来源文件、明确标识的本地规范修订和独立人审未完成项；[M3 报告](docs/reports/m3-reliability-2026-10-07.md) 记录恢复、有限故障矩阵与合成负载；[M2 报告](docs/reports/m2-learning-2026-10-07.md) 记录真实调用、HTTP 修订、用途预览、费用与未核验项。当前累计保守费用 1.278668 元；用户已将总额度提高到 10 元，剩余 8.721332 元，见 [额度记录](docs/decisions/0002-budget-authorization.md)。任务保存成功不代表事实核验完成。

## 文档入口

| 文档 | 唯一负责的内容 |
| --- | --- |
| [产品定义](PRODUCT.md) | 用途问答、学习边界、节点与叶子、练习、多媒体体验、首版边界和验收 |
| [技术设计](DESIGN.md) | 整体架构、结构化协议、领域模型、任务可靠性、资源 Agent、上下文与评测原则 |
| [项目计划](PLAN.md) | 实施任务、依赖、工作量初估、里程碑验收、风险和面试证据交付 |

[M0 验收报告](docs/reports/m0-baseline-2026-10-07.md) 记录所有校准失败、真实用量、兼容性与剩余风险；[技术决策](docs/decisions/0001-m0-baseline.md) 与 [契约](contracts/README.md) 是 M1 的起点。文中仍标为候选的渲染/运行组件在后续里程碑接入时验证。

## 运行 M0 工具

需要 CPython 3.12.14 和 uv 0.12.23。已有项目虚拟环境可直接运行：

```sh
.venv/bin/unfold check
.venv/bin/unfold verify-evidence
.venv/bin/pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
.venv/bin/mypy src
```

新环境先安装 uv，再执行 `uv sync --locked`；`.python-version` 与 `uv.lock` 固定 Python 和依赖。启动本地应用使用 `uv sync --locked --extra app`、`unfold migrate`、`unfold serve` 和独立 `unfold worker`；完整前端/数据库配置与统一验证见 [开发说明](docs/development.md)，实测边界见 [M1 报告](docs/reports/m1-foundation-2026-10-07.md)。

默认 worker 使用标明来源的开发替身，不收费；显式 DeepSeek 模式和 `unfold baseline` 会真实付费。配置方式见 [开发说明](docs/development.md) 与 [评测说明](evals/README.md)。`.env.local` 不入库，不要提交密钥；确定性检查不访问真实模型。M0 全部调用按高峰未命中价估算 0.8803 元，账户前后余额下降 0.28 元，归因限制见报告；不把格式合法声称为知识准确率。

## 文档维护

- 产品规则在 PRODUCT.md 维护，技术取舍在 DESIGN.md 维护，实施任务与进度在 PLAN.md 维护；入口不重复详细规则。
- 讨论有结论后更新原段落，移除失效提案，避免积累互相冲突的方案。
- 出现跨模块、长期有效的重要取舍时，再拆出简短的决策记录；当前不建立空文档体系。
- 有实现后，模块细节跟随模块维护；API、数据结构等可生成的事实优先从实现生成。
- 已获用户开工授权；每阶段保留实现、验证与证据，再更新 PLAN.md 状态，不把未来应用能力写成完成。
