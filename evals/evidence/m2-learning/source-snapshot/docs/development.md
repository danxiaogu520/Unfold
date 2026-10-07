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

`UNFOLD_MAX_COST` 是剩余授权预算，使用十进制人民币和已确认的每百万 token 价格。调用前将峰值费用预留写入数据库，已知用量后结算，未知用量保留预留；修改配置或重启进程不会重置已记录支出。真实网络错误不由适配层自动重试。模型密钥不会传给浏览器，不要把私密环境文件提交入库。

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
