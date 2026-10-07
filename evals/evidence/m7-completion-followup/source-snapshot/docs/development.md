# 本地开发与检查

固定工具链：Python 3.12.14、uv 0.12.23、Node.js 26.10.0、npm 12.1.0、PostgreSQL 18.6。后端与前端依赖分别见 `uv.lock` 和 `web/package-lock.json`。当前只支持本地单用户访问，API 绑定 `127.0.0.1:8000`。

## 准备

```sh
uv sync --locked --extra app
npm ci --prefix web
cp .env.app.example .env.app.local
chmod 600 .env.app.local
```

安装 PostgreSQL 18.6 后，将其 `bin` 目录写入 `UNFOLD_PG_BIN`。开发脚本也会查找项目 `.tools/postgres/bin`、Homebrew `postgresql@18/bin` 和 PATH，并核对版本。脚本只管理 `.data/postgres` 的项目集群；初次初始化使用本地 trust 认证，仅绑定回环地址，不能用于远程部署。

```sh
export UNFOLD_PG_BIN=/你的/PostgreSQL/18.6/bin
.venv/bin/python scripts/dev_database.py start
.venv/bin/unfold migrate
```

也可以使用官方 `postgres:18.6` 容器，将数据库地址写入 `.env.app.local`；CI 中镜像固定内容摘要。数据库驱动必须是 `postgresql+psycopg`，SQLite 不代替并发验收。

本次 macOS 验证从官方源码构建，源码 SHA-256 为 `555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f`。受网络影响时可断点续传，必须先通过校验再解压。构建参数为 `--prefix=$PWD/.tools/postgres --without-icu --without-readline --without-zlib`；这用于本地测试，不作为生产 TLS 配置。PostgreSQL 的 `--without-ssl` 不是本版有效选项，应直接保留默认不启用 SSL 的配置。

## 启动

分别在三个终端运行：

```sh
.venv/bin/unfold serve
.venv/bin/unfold worker
npm run dev --prefix web
```

访问 `http://127.0.0.1:5173`。默认 `UNFOLD_MODEL_MODE=fake` 使用确定性开发替身，页面与每次模型调用记录都会标出替身来源，不能作为真实模型或教学效果证据。HTTP 服务负责创建/查询任务，独立 worker 负责执行；没有 worker 时任务保持等待状态。

正式模型模式需要把 `.env.app.local` 中的模式改为 `deepseek`，并通过单独的私密文件或本地环境变量提供模型配置：

```sh
.venv/bin/unfold worker --model-env .env.local
```

`UNFOLD_MAX_COST` 是当前持久账本被授权的整体上限，使用十进制人民币；不要在每次调用后再次减去账本已经记录的费用。调用前预留，已知用量后结算，未知用量保留预留；配置读取/重启不能重置费用或自动提高额度。用户已明确授权总额 10 元；排除账本建立前的 M0 0.880300 元，当前账本上限 9.119700 元，见 [额度记录](decisions/0002-budget-authorization.md)。真实网络错误不由适配层自动重试；任务引擎可以执行有次数、退避和费用限制的恢复。密钥不会传给浏览器，不要提交私密环境文件。

## 验证

```sh
export UNFOLD_TEST_DATABASE_URL=postgresql+psycopg://unfold@127.0.0.1:54318/unfold
npx --prefix web playwright install chromium
.venv/bin/python scripts/check.py --browser
```

数据库测试为每项测试建立独立 schema，并在退出时清理。浏览器测试同样建立独立 schema，使用真实 HTTP、真实 PostgreSQL 和固定替身；它不会读取 `.env.local` 或访问真实模型。默认要求端口 8000/5173 空闲，拒绝复用其他已有服务。

检查入口要求数据库地址，防止数据库测试被静默跳过。结果保存在 `.data/qa/checks.json` 和 `python.xml`，失败浏览器轨迹保存在 `web/test-results`。单独 `pytest` 可跳过未配置的数据库测试，但这不满足 M1 验收。GitHub CI 已配置相同检查入口；没有远程仓库时不能声称远程 CI 已通过。

M0 契约检查与公开模型证据回放仍可独立离线运行：`unfold check`、`unfold verify-evidence`。真实基线实验使用 `unfold baseline`，与确定性 CI 分开控制预算。

## 排查

`UNFOLD_DATABASE_URL` 缺失、驱动错误或远程监听地址会明确失败。先运行 `scripts/dev_database.py status` 检查项目集群；服务日志在 `.data/postgres.log`。Codex 沙箱可能阻止本地数据库 socket、共享内存或浏览器启动，验证时需要获准的本地进程权限；不要因此更换为 SQLite。

停止项目数据库：`.venv/bin/python scripts/dev_database.py stop`。`pg_ctl stop -m fast` 会完整关闭集群并保留数据；不要直接删除 `.data` 来处理迁移问题。

## 任务恢复与故障实验

默认租约 30 秒、每 10 秒续租；单 attempt 截止 180 秒不能通过续租延长；最多三次尝试；全局有效执行资格 2、队列容量 32。事件保留七天，过期游标读取快照。开发替身不改变这些提交资格检查。关闭页面只断开观察，显式取消才改变任务状态。

`tests/test_process_faults.py` 在四个提交窗口终止真实子进程；`scripts/m3_load.py` 创建独立 schema 和临时 HTTP 端口，执行固定 64 请求负载，结束自动清理。两者不读取模型私密配置。历史证据通过 `scripts/verify_reliability_evidence.py` 离线校验。详细样本与限制见 [M3 报告](reports/m3-reliability-2026-10-07.md)。

## 练习与来源资源

练习默认三题，依赖实际所读正文；旧题保留、反馈后停用。已保存答案按稳定 ID 与集合完全匹配，判分不再调用模型；普通题目响应和公开 trace 隐藏未提交答案。

Commons 图片制作独立执行，失败不回滚正文；源码外部资源禁止模型编造 URL。文件保存于受管 `resources/files`，以内容 SHA 命名；数据库与文件需要一起备份。PNG/JPEG/WebP 限 4 MB、16 MP，静态图才接受。开发/浏览器验证可以重放已取得的许可图片，记录 `recorded_fixture_replay`；真实网络获取另列。图示错误保留文字替代，代码显示为未执行。

当前共享事件为 run.v3/event.v3，旧版本证据仍可回放。`scripts/verify_practice_evidence.py` 只校验冻结字节和结果，不再推理或下载。六题审阅和独立人审边界见 [M4 报告](reports/m4-practice-resources-2026-10-07.md)。

## 受管网页与资源 Agent

网页资源在独立 Chromium 进程执行；模型脚本不放进应用 DOM，服务只传 PNG 画面与受限坐标/按键/文本。需要已安装 npm 依赖与 Playwright Chromium。默认会话两分钟、同时一个，停止后可重新打开保存资源，不重新推理。错误只影响资源，正文保留。

DeepSeek 模式采用有界资源工具循环，默认至多八步/八次模型调用/首次执行起150秒；替身模式是明确标记的 scripted policy。组件与网页文件按内容 SHA 保存，独立检查决定候选可否交给应用发布。工具只能访问当前任务的候选，不能修改检查规则。通用主题仅通过加载/重置技术检查，不自动称作领域内容核验。

真实资源对照只从 evals/datasets/resources.v1.json 的公开合成任务新建空间，不读取已有私人学习数据。报告保留单次/固定修复/Agent 成本及不成功案例。离线检查使用 scripts/verify_agent_evidence.py；执行边界与没有严格内存配额的限制见 [执行决策](decisions/0003-managed-resource-browser.md)。

## 上下文选择

学习页“本次参考范围”可选当前路径、结构相关性或全已保存正文历史。默认保留当前路径；非默认策略会改变任务的活动身份。必需目标与路径装不下时返回 413，不能转成叶子；全历史超限不偷偷截断。程序摘录有来源、依赖 SHA 与省略量，不等于事实认证。

M6 保留集使用新建公开合成数据，取得候选后主动取消以保持正式快照；没有正式知识发布。离线运行 `scripts/verify_context_evidence.py` 与 `scripts/summarize_context.py` 即可核对结果，不读取密钥或重新推理。重新实验需指定新目录，不覆盖冻结记录；独立人审与事实准确性未知。

## 最终私有交付与恢复

M7 补齐问答后首层大纲的持久衔接、最多五份当前正文的综合练习、已有题目内容块、单块渲染回退和临时网页代码副本。自动首层超预算时设定完整保留，显示原因；源码副本的重置恢复保存文件，不发布修改。

清洁安装验证不复制旧 .venv/node_modules 或任何私密环境文件，复用声明的系统 Python/Node/PostgreSQL。执行 `scripts/clean_install.py --output .data/qa/新的报告.json`，配置 UNFOLD_TEST_DATABASE_URL；临时空 schema、实际 HTTP/worker、自动大纲、练习与判分验证后清理。网络安装按锁文件取公开包；--offline 要求缓存已完整，不能把缓存缺失当作应用成功。

备份前停止 API、worker 和其他写入者，保持资源文件不被人工删除。工具检查没有 running attempt；这不替代停止写入者的操作。只备份 public schema 和正式格式的受管文件，不含模型环境文件或 PostgreSQL 角色凭据。

```sh
.venv/bin/python scripts/backup.py create --output .data/backups/新的名称
.venv/bin/python scripts/backup.py restore .data/backups/新的名称 --database unfold_restore_demo --data-directory .data/restores/新的名称
.venv/bin/python scripts/verify_restore.py --database unfold_restore_demo --data-directory .data/restores/新的名称 --output .data/qa/新的恢复报告.json
```

恢复只创建新数据库/目录，拒绝覆盖；失败也不 DROP 原数据库。目标名使用 unfold_restore_ 前缀。复制 .env.app.example 到受控环境，将数据库名和数据目录指向恢复位置，保持 fake 模式先核对。查询、资源版本、答案和模型账本一并恢复；密钥通过独立受控配置提供。

同一数据库中的账本才共享开销。**恢复验收与清洁安装都不启动真实模型 worker；不要同时在原库和复制库消费同一授权预算。** 本轮真实调用只由原实例承担，累计 2.865662 元；恢复不会把旧开销清零。

私有源码包由 `scripts/package_demo.py --output .data/releases/新的名称` 生成，含锁文件、公开冻结证据和文档，排除 .data、.tools、.venv、node_modules、evals/runs、私密 .env。build-manifest.json 使用本地源指纹；当前没有 Git 提交、远程 CI 或发布记录。三段回放录屏与面试讲解见 [面试材料](interview.md)；不要将回放称作实时推理。

领域可单独输入，用途留空后通过问答确定。空说明不使用占位文本，新协议为 questionnaire-input.v2 / profile-input.v3，旧协议继续回放；来源未提供就保留 unknown 或明确假设。
