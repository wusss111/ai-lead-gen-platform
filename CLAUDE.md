# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 启动与运行

```bash
# 完整启动（Redis + RQ Workers + Web）
start_all.bat

# 单独启动 Web
python -m uvicorn src.core.app:app --host 127.0.0.1 --port 8000

# 启动 RQ Worker（Windows 必须用 SimpleWorker）
rq worker -u redis://127.0.0.1:6379/0 customer_eval:default --worker-class rq.SimpleWorker
rq worker -u redis://127.0.0.1:6379/0 inquiry_mail:default --worker-class rq.SimpleWorker
rq worker -u redis://127.0.0.1:6379/0 inquiry_mail:send --worker-class rq.SimpleWorker

# 运行测试
pytest tests/ -v
pytest tests/test_step01_schemas_and_kb.py -v  # 单个测试文件
```

## 架构概览

平台采用 **Agent 自动发现** 架构。`src/core/app.py` 启动时扫描 `src/agents/*/manifest.py`，每个 manifest 暴露 `register() -> AgentManifest`。新 Agent 只需创建目录 + manifest.py + routes.py 即可自动注册，无需修改核心代码。

### 目录职责

| 目录 | 用途 |
|------|------|
| `src/core/` | 框架层：应用工厂、配置、认证、数据库、Redis |
| `src/agents/<name>/` | 每个 Agent 自包含：manifest.py, routes.py, tasks.py, config.py, templates/, static/ |
| `src/templates/` | 共享 Jinja2 模板（base.html 布局、导航栏） |
| `src/static/` | 共享静态资源（PicoCSS、platform.css、api.js、utils.js） |
| `tools/` | 可复用工具：DeepSeek 客户端、邮件生成/发送、评估流水线 |
| `var/` | 运行时数据：SQLite 数据库、Redis、Gmail token、job 输出 |

### 已注册的 Agent

1. **customer-eval** — 上传 Excel → RQ 后台调用 DeepSeek 评估 → 结果自动写入 SQLite `customer` 表
2. **crm** — 客户资源浏览/搜索/筛选/详情/导出
3. **inquiry-mail** — 选中客户 → DeepSeek 生成询盘邮件 → Gmail API（优先）或 SMTP 发送

### 数据流

```
Excel 上传 → customer_eval/tasks.py (RQ) → LLM 评估 → SQLite customer 表
                                                    ↓
                              crm Agent 浏览/搜索 ← ┘
                                                    ↓
                              inquiry_mail Agent → LLM 生成邮件 → Gmail API 发送
```

## 关键约定与坑

### Windows 兼容
- RQ Worker **必须** 加 `--worker-class rq.SimpleWorker`，因为 Windows 没有 `os.fork()`
- Redis 使用预编译的 `var/redis/redis-server.exe`（Windows 版 Redis 5.0.14.1）

### Static 文件挂载顺序（重要）
`app.py` 中 Agent 专属 static 挂载（`/static/{agent_name}`）必须在共享 `/static` 之前，否则 Agent 的 JS/CSS 会被共享目录拦截返回 404。

### Jinja2 环境
使用原始 `jinja2.Environment` + `FileSystemLoader`，**不用** Starlette 的 `Jinja2Templates`（Jinja2 3.1.6 有 `unhashable type: 'dict'` 缓存 bug）。

### Worker 进程加载 .env
RQ Worker 进程不会继承 Web 进程的环境变量，每个 `tasks.py` 必须在模块顶层调用 `load_dotenv()`。

### pandas NA 处理
`_save_to_database()` 中不能用 `val != val` 判断 NaN（pandas NA 会抛 TypeError），必须用 `pd.isna(val)`。

### 邮件发送
- 优先 Gmail API（OAuth2，走 HTTPS 443），检测逻辑：`var/gmail_client_secret.json` 存在则走 Gmail API
- SMTP 作为 fallback，但 Great Firewall 可能阻断 Gmail SMTP 端口（465/587）
- Gmail OAuth token 保存在 `var/gmail_token.json`，过期自动 refresh

### API 客户端
DeepSeek 使用 OpenAI 兼容协议：`tools/deepseek_client.py` 封装了 `OpenAI` 客户端，支持 `response_format={"type": "json_object"}`。通过 `ANTHROPIC_*` 系列环境变量配置（见 settings.json）。

### 数据库
- SQLite，路径 `{PLATFORM_DATA_DIR}/platform.db`（默认 `var/platform/platform.db`）
- WAL 模式，外键开启，thread-local 连接池
- `customer` 表同时存储评估结果和邮件状态（`email_status`, `email_subject`, `email_body`, `email_sent_at`）

### Docker（备用，不稳定）
`docker-compose.yml` 存在但在这台机器上不稳定，日常开发用 `start_all.bat` 原生运行。
