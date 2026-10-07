# 生成协议 v1

权威实现是 [`src/unfold/contracts.py`](../src/unfold/contracts.py)，API 候选边界是 [`api_contract.py`](../src/unfold/api_contract.py)。`schemas/` 由实现生成，修改后运行 `unfold export-schemas`；`unfold export-schemas --check` 检查漂移。

本目录维护生成候选与共享任务协议。M4 已接入问卷/多轮设定、大纲、单层展开、讲解、独立重评、结构预览、版本化练习与来源图像。隔离网页与工具 Agent 继续 M5。

| 阶段 | 输入 / 成功候选 | 确定性检查 |
| --- | --- | --- |
| questionnaire | 原始领域/说明、已知维度与剩余预算 / 少量问题 | 稳定 key、选项唯一、维度去重、累计题数、未知与自由补充 |
| profile | 问卷、原始答案 / 来源标记的学习设定 | 选项引用、每题一份答案、陈述引用真实原始证据、未知不冒充已知 |
| outline | 设定快照、可复用概念 / 根和第一层 | 概述与范围、局部 ID、关系、禁止孙节点、严格前置环、叶子空子项 |
| expansion | 设定/目标/路径/已存在子项 / 当前判定与下一层 | 有效叶子与过期判定拒绝；目标精确匹配；禁止祖先引用、空可展开结果 |
| reassessment | 当前设定/路径/反馈 / 新判定与结构建议 | 允许评估旧叶子；不产生子节点；已有子树改叶子须说明预览建议 |
| explanation | 当前目标、补充要求、资源能力 / 内容块与资源需求 | 不含大纲变更；块与资源 key、类型一致；练习引用必须存在 |
| exercises | 已提供内容修订、题量/题型 / 冻结答案与解析的题组 | 单选唯一、答案合法、内容引用、请求数量；判分使用 ID 集合 |
| resource | 内容与资源需求、真实候选/能力 / 复用或制作建议 | 引用仅限工具提供的候选、不声明 ready、不提供 URL/路径；能力不足明确退出 |

成功与失败实例见 [`evals/contracts`](../evals/contracts)。它们是手写协议材料，字段 `evidence_kind=hand_authored_contract_example`，不得作为模型基线。版本号、字段缺失、错误引用、非法答案、叶子带子项、假设伪装用户来源等均会失败。

API 输入、模型候选和未来数据库实体分别定义。服务端创建 `run_id`、attempt、权限、实际用量、正式修订与资源地址。模型可以回显给定目标 key，但不能创建服务端实体。源 schema 保留长度和数量预算；供应商 schema 使用保守子集，省略部分长度/正则约束，完整业务校验仍在本地执行。

## HTTP 边界

M2 新增 `profile-input.v2`，以 `rounds` 分别保留两轮问题和答案，累计仍最多五题；原 `profile-input.v1` 的单轮语义继续支持，历史证据可回放。运行快照与事件采用 `run.v2`/`event.v2`，新增明确的 `result_kind=knowledge|structure_preview`；v1 快照定义仍保留用于历史读取。

M4 新增 `exercises-input.v2`，包含实际指令与必需题型；保留 v1。当前共享快照/事件为 `run.v3`/`event.v3`，以 `exercise_set`、`resource` 区分正式结果类型，v1/v2 仍可读历史证据。

当前共 38 份生成 schema。实际服务的完整接口可从 `/openapi.json` 读取；本目录的 `openapi.v1.json` 描述共享任务路由与各版本模型，不能代替所有后续学习路由的运行实现。

M7 新增 questionnaire-input.v2 / profile-input.v3，允许未提供用途的空说明；旧版本语义保留。空说明保存为空值，服务端按非未知回答生成来源清单，不能借 brief 引用把不存在的用途/基础/目标宣称为 user 陈述。

结构重生成先保存预览，只有 `POST /api/v1/spaces/{id}/suggestions/{preview}/apply` 才采用；`dismiss` 保留原结构。采用时重新检查设定、当前节点与依赖版本，过期候选不能发布，重复采用不重复生成。概念规范身份与路径展示标题独立；显式复用要求规范范围保持一致，不从同名判断同义。

生成的 [`OpenAPI`](schemas/openapi.v1.json) 定义以下共享接口：

- `POST /api/v1/runs`：必需 `Idempotency-Key`，提交明确的 operation、知识空间、基础修订和输入快照；成功返回 202 与任务快照，同键不同参数返回 409。M1 真实 PostgreSQL 并发测试验证请求身份和活动复用别名的保存。
- `GET /api/v1/runs/{run_id}`：持久任务快照；普通读取不发起模型调用。
- `POST /api/v1/runs/{run_id}/cancel`：独立取消操作，终态请求返回实际当前状态。
- `GET /api/v1/runs/{run_id}/events`：SSE `id=sequence`，`data=RunEvent JSON`，`Last-Event-ID` 恢复。M1 已实现序号与重放；保留期清理、过期 410 与快照回退在 M3 完成。断开连接不取消任务。

API 的 profile/base revision 必须匹配请求快照；数据库提交资格、租约、取消与版本冲突在 M1/M3 实现。task 的 `succeeded` 必须有正式结果引用；失败/冲突必须有错误，候选本身不拥有成功状态。

## 错误分类与重试

| 类别 | 示例 | 处理 |
| --- | --- | --- |
| 输入/契约 | configuration、invalid_input、invalid_json、schema_validation、domain_validation | 错误配置/输入不调用模型；已执行但候选非法最多修复一次，计入原实验 |
| 供应商结果 | refusal、truncated、content_filter、schema_unsupported | 记录失败；不当作 leaf，不用自由文本猜测成功；schema 不支持须另作明确接入决策 |
| 外部失败 | authentication、rate_limit、provider_error、timeout、outcome_unknown | M0 不自动网络重试；超时/部分网络故障记录未知外部结果与潜在费用 |
| 执行/提交限制 | capability_limit、budget_exceeded、cancelled、version_conflict | 明确退出/取消/冲突，不能伪装为知识终点 |

缺少用量为 `null`，不是 0；模型事实正确、学习收益、资源执行通过均不是结构校验的结论。真实模型调用记录不保存模型内部推理文本。
