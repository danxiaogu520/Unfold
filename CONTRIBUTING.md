# 参与开发

新功能先说明用户问题与预期行为；修复请提供复现步骤、日志和工具链版本。涉及产品规则时同步维护 PRODUCT.md，长期技术取舍维护 DESIGN.md 或 docs/decisions。历史试验保持原有结论，不将固定替身或结构校验称作事实准确率。

## 配置与检查

按 [开发说明](docs/development.md) 安装锁定依赖、准备 PostgreSQL 和 Chromium。开发与测试使用 fake 模式，不需要模型密钥。

```sh
uv sync --locked --extra app
npm ci --prefix web
npx --prefix web playwright install chromium
.venv/bin/python scripts/dev_database.py start
export UNFOLD_TEST_DATABASE_URL=postgresql+psycopg://unfold@127.0.0.1:54318/unfold
.venv/bin/python scripts/check.py --browser
```

提交前执行 `.venv/bin/python scripts/audit_publication.py --staged`。检查会拒绝私密配置、数据库、备份、依赖目录、疑似密钥和本机用户路径进入提交，并且只输出文件名和错误类型。源文件的低风险改动按需验证；修改任务、并发、资源隔离或预算逻辑时补充能够复现问题的回归测试。

## 提交与证据

- 保持改动聚焦，在 PR 中说明行为变化和实际验证结果。
- 不提交 `.env.local`、`.env.app.local`、`.data`、原始 `evals/runs`、数据库转储或个人学习数据。
- 新的公开评测使用合成输入，发布前审查模型请求、响应和截图，保留失败与未知用量。
- 历史公开证据仅在有明确脱敏记录时调整表示；不得改写任务结果。新实验使用新目录与新清单。
- MIT 适用于原创贡献；新增第三方资源应记录作者、来源、许可和修改情况。

安全问题请按 [安全说明](SECURITY.md) 报告。
