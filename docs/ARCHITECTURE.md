# AI 获客平台 — 底层框架与工作流编排技术文档

> 版本：V2.0 | 最后更新：2026-05-19

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [启动流程详解](#2-启动流程详解)
3. [核心框架层](#3-核心框架层)
4. [Agent 自动发现机制](#4-agent-自动发现机制)
5. [Agent 详情](#5-agent-详情)
   - [5.1 客户评估 (customer_eval)](#51-agent-1客户评估-customer_eval)
   - [5.2 客户资源管理 (CRM)](#52-agent-2客户资源管理-crm)
   - [5.3 询盘邮件 (inquiry_mail)](#53-agent-3询盘邮件-inquiry_mail)
   - [5.4 知识库管理 (knowledge_base)](#54-agent-4知识库管理-knowledge_base)
   - [5.5 智能客服 (chat_agent)](#55-agent-5智能客服-chat_agent)
6. [RAG 基础设施](#6-rag-基础设施)
7. [评估流水线](#7-评估流水线)
8. [工具层](#8-工具层)
9. [前端架构](#9-前端架构)
10. [数据库设计](#10-数据库设计)
11. [部署方案](#11-部署方案)

---

## 1. 系统架构总览

```
┌────────────────────────────────────────────────────────────────┐
│                     FastAPI Application                         │
│                     (src/core/app.py)                           │
├────────────────────────────────────────────────────────────────┤
│  GET /                → 首页                                    │
│  GET /health          → 健康检查                                 │
│  /customer-eval/      → Agent 1: 客户评估 (上传→AI→入库)        │
│  /crm/                → Agent 2: 客户资源 (浏览/搜索/筛选/详情)  │
│  /inquiry-mail/       → Agent 3: 询盘邮件 (生成→发送→追踪)      │
│  /knowledge-base/     → Agent 4: 知识库管理 (导入/搜索/预览)     │
│  /chat/               → Agent 5: 智能客服 (挂件/API/确认)       │
├────────────────────────────────────────────────────────────────┤
│                    共享基础设施                                  │
│  src/core/config.py     → PlatformConfig (统一配置)              │
│  src/core/database.py   → SQLite + WAL 模式                     │
│  src/core/redis_utils.py → Redis 连接池 + RQ 任务管理            │
│  src/core/auth.py       → HTTP Basic 认证                       │
├────────────────────────────────────────────────────────────────┤
│                    RAG 检索管道                                  │
│  tools/embedding.py     → 嵌入服务 (fastembed + ONNX)            │
│  tools/vector_store.py  → ChromaDB + BM25 + RRF + 重排序        │
│  tools/doc_parser.py    → 文档解析 + 父子分块                    │
├────────────────────────────────────────────────────────────────┤
│                    后台任务队列                                  │
│  Redis + RQ (rq worker)                                        │
│  customer_eval:default → 评估任务                                │
│  inquiry_mail:default  → 邮件生成任务                            │
│  inquiry_mail:send     → 邮件发送任务                            │
└────────────────────────────────────────────────────────────────┘
```

**技术栈:** Python/FastAPI + SQLite (WAL) + Redis/RQ + ChromaDB + Vanilla JS (ES Modules)

---

## 2. 启动流程详解

### 2.1 入口点

```bash
# 本地开发
python -m uvicorn src.core.app:app --host 127.0.0.1 --port 8000

# 或完整栈
start_all.bat  # Redis → RQ Workers (×3) → Uvicorn Web Server
```

### 2.2 `create_app()` 执行流程

```
1. load_dotenv()                           ← 从 .env 加载环境变量
2. get_config() → PlatformConfig.from_env()  ← 读取所有配置（单例）
3. config.data_dir.mkdir(parents=True)      ← 创建 var/platform/
4. FastAPI(title=config.app_title)          ← 创建 FastAPI 实例
5. discover_agents()                        ← 扫描 src/agents/*/manifest.py
6. 对每个 Agent:
   ├── include_router(prefix="/{name}")     ← 挂载路由
   ├── mount(StaticFiles) at /static/{name} ← Agent 静态文件（优先）
   ├── prepend template_dir 到搜索路径      ← Agent 模板优先
   └── 收集 nav 条目
7. mount shared static at /static           ← 共享静态（最后挂载）
8. 排序 nav_agents（按 nav.order）
9. 创建 Jinja2 Environment (FileSystemLoader)
10. 定义 /health 和 / 路由
11. 注册 shutdown handler → close_db()
```

### 2.3 静态文件挂载顺序（关键）

| 优先级 | 路径 | 用途 |
|--------|------|------|
| 1 (最高) | `/static/{agent_name}` | Agent 专属静态（在共享之前注册） |
| 2 (最低) | `/static` | 共享静态（最后注册，避免拦截 Agent 文件） |

---

## 3. 核心框架层

### 3.1 PlatformConfig (`src/core/config.py`)

| 字段 | 类型 | 默认值 | 环境变量 |
|------|------|--------|----------|
| `app_title` | `str` | `"外贸客户平台"` | `APP_TITLE` |
| `app_version` | `str` | `"3.0.0"` | — |
| `redis_url` | `str` | `"redis://127.0.0.1:6379/0"` | `REDIS_URL` |
| `data_dir` | `Path` | `var/platform` | `PLATFORM_DATA_DIR` |
| `basic_user` | `str\|None` | `None` | `BASIC_USER` |
| `basic_password` | `str\|None` | `None` | `BASIC_PASSWORD` |
| `debug` | `bool` | `False` | `DEBUG` |

### 3.2 数据库管理 (`src/core/database.py`)

**线程本地连接模式：**

```python
_local = threading.local()

def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "connection", None)
    if conn is None:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")      # 写前日志
        conn.execute("PRAGMA foreign_keys=ON")        # 外键约束
        conn.execute("PRAGMA busy_timeout=5000")     # 锁等待 5 秒
        conn.executescript(SCHEMA_SQL)                # 自动建表
        _local.connection = conn
    return conn
```

**关键设计决策：**
- 不使用 `check_same_thread=False`：每个线程独立连接
- WAL 模式：多读一写并发
- `busy_timeout=5000`：写锁冲突等待 5 秒
- 关闭时 `close_db()` 清理

### 3.3 认证 (`src/core/auth.py`)

HTTP Basic 认证，可选开启：
- 未配置 `BASIC_USER` / `BASIC_PASSWORD` → 认证跳过
- 已配置 → 需要凭据，不匹配返回 401

所有 Agent API 通过 `Depends(require_auth)` 注入。

---

## 4. Agent 自动发现机制

### 4.1 AgentManifest 协议 (`src/agents/base.py`)

```python
@dataclass
class AgentManifest:
    name: str               # URL 前缀
    display_name: str       # 导航栏显示名
    description: str = ""   # 简介
    router: Any = None      # FastAPI APIRouter
    template_dir: str | None = None
    static_dir: str | None = None
    config_class: type | None = None
    nav: dict | None = None  # {"icon": "...", "order": N} 或 None（不显示）
```

### 4.2 发现流程

```
discover_agents()
  ├── 扫描 src/agents/ 下所有子目录
  ├── 跳过 _ 或 . 开头的目录
  ├── 检查 manifest.py 存在
  ├── importlib.import_module → 调用 register()
  └── 缓存结果
```

**新增 Agent 只需 3 步：**
1. 创建 `src/agents/<name>/` 目录
2. 创建 `manifest.py` 实现 `register()`
3. 创建 `routes.py` 定义 FastAPI 路由
4. （可选）创建 `templates/` 和 `static/` 目录

无需修改任何核心代码。

### 4.3 已注册 Agent

| Agent | 路径 | nav.order | 可见 |
|-------|------|-----------|------|
| customer-eval | `src/agents/customer_eval/` | 1 | 导航栏 |
| crm | `src/agents/crm/` | 2 | 导航栏 |
| inquiry-mail | `src/agents/inquiry_mail/` | 3 | 导航栏 |
| knowledge-base | `src/agents/knowledge_base/` | 4 | 导航栏 |
| chat-agent | `src/agents/chat_agent/` | — | 右下角挂件 |

---

## 5. Agent 详情

### 5.1 Agent 1：客户评估 (customer_eval)

**URL 前缀:** `/customer-eval/` | **RQ 队列:** `customer_eval:default`

**工作流：**
```
用户上传 Excel (.xlsx / .csv)
  → POST /api/jobs
    ├── 验证文件格式，生成 UUID job_id
    ├── 流式保存 → 验证 max_rows
    ├── 入队 RQ 任务 run_eval_job（timeout=2700s）
    └── 返回 {"job_id", "rq_job_id", "status": "queued"}
  → RQ Worker 执行 run_eval_job()
    ├── run_pipeline() → AI 逐行评估（详见第 7 节）
    ├── _save_to_database() → 批量写入 customer 表
    └── _update_batch_status("finished")
  → GET /api/jobs/{job_id} → 轮询进度
  → GET /api/jobs/{job_id}/download → 下载结果
```

**配置 (CustomerEvalConfig)：**

| 字段 | 默认 | 环境变量 |
|------|------|----------|
| `max_upload_mb` | 32 | `CUSTOMER_EVAL_MAX_UPLOAD_MB` |
| `max_rows` | 500 | `CUSTOMER_EVAL_MAX_ROWS` |
| `queue_name` | `"customer_eval:default"` | `CUSTOMER_EVAL_QUEUE` |

### 5.2 Agent 2：客户资源管理 (CRM)

**URL 前缀:** `/crm/`

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/` | 客户列表页 |
| `GET` | `/{customer_id}` | 客户详情页（服务端渲染） |
| `GET` | `/api/customers` | 客户列表 API（搜索/排序/分页/高级筛选） |
| `GET` | `/api/customers/{id}` | 单客户 JSON |
| `POST` | `/api/smart-search` | **AI 自然语言搜索** (Text-to-SQL) |
| `GET` | `/api/customers/export` | 导出 Excel/CSV |
| `POST` | `/api/customers/delete` | 删除客户（支持批量） |

**筛选参数:** search, deal_recommendation, min_score, max_score, country, review_flag, batch_id, salesperson_id, email_status, 分页/排序

**AI 智能搜索：**
```
用户输入自然语言 → 构建 LLM prompt (含完整表 schema)
  → DeepSeek 生成 SQL → 安全检查（仅 SELECT，禁止 INSERT/UPDATE/DELETE/DROP）
  → db.execute(sql) → 返回 {"query", "sql", "explanation", "customers"}
```

### 5.3 Agent 3：询盘邮件 (inquiry_mail)

**URL 前缀:** `/inquiry-mail/` | **RQ 队列:** `inquiry_mail:default` (生成), `inquiry_mail:send` (发送)

**完整工作流：**
```
Step 1: 选择客户
  GET /api/customers/emailable → 列出有邮箱的客户

Step 2: 生成邮件
  POST /api/generate
    → 检索知识库（产品信息 + 客户行业）
    → LLM 生成个性化多语言邮件
    → 写入 customer 表 email_status='draft'

Step 3: 确认与发送
  POST /api/send
    → 前置检查：Token/SMTP、今日配额、客户当地时区
    → Gmail API (HTTPS 443) 优先，SMTP 回退
    → 记录 daily_send_log

Step 4: 追踪
  GET /api/send-status/{job_id} → 发送进度、已读状态
```

**反风控机制：**

| 机制 | 默认值 | 配置 |
|------|--------|------|
| 发送间隔 | 45 秒/封 | `SMTP_SEND_DELAY` |
| 每日限额 | 50 封/天 | `MAIL_DAILY_LIMIT` |
| 时区感知 | 仅当地 9-17 点 | `MAIL_SEND_HOUR_START/END` |
| 夏令时 | 自动 (zoneinfo) | — |

### 5.4 Agent 4：知识库管理 (knowledge_base)

**URL 前缀:** `/knowledge-base/`

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/` | 知识库管理页面 |
| `GET` | `/api/kb/collections` | 列出三大集合及统计 |
| `GET` | `/api/kb/documents` | 文档列表（分页） |
| `POST` | `/api/kb/import` | 目录路径导入 / 文本粘贴入库 |
| `DELETE` | `/api/kb/documents/{doc_id}` | 删除文档 |
| `GET` | `/api/kb/search` | 测试搜索（支持模式切换） |
| `GET` | `/api/kb/doc/{doc_id}/preview` | 查看父文档内容 |
| `GET` | `/api/kb/jobs/{job_id}` | 查询导入进度 |

**三大集合：**
- **产品信息** — 产品说明书、规格参数、报价单
- **公司文档** — 企业介绍、资质认证、业务范围
- **采购表单** — 供应商报价、采购价格表

### 5.5 Agent 5：智能客服 (chat_agent)

**URL 前缀:** `/chat/` | **无导航栏（右下角挂件）**

| 方法 | 路径 | 功能 |
|------|------|------|
| `POST` | `/api/chat/stream` | **SSE 流式** Agent 响应（主要） |
| `POST` | `/api/chat/send` | 非流式 Agent 响应（兼容） |
| `POST` | `/api/chat/confirm` | 确认执行操作（发送邮件等） |
| `GET` | `/api/chat/tools` | 列出工具定义（调试） |

**Agent 循环 (ReAct 模式)：**

```
用户输入 → System Prompt + 上下文管理
  → DeepSeek (with tools) → 判断意图
  → Tool Call? → 执行工具 → 追加结果 → 继续（最多 5 轮）
  → 写操作? → 返回确认卡片 → 用户确认 → 执行
  → 纯文本? → 流式输出（含思维链）→ 结束
```

**5 个 Function Calling 工具：**

| 工具 | 功能 | 需确认 |
|------|------|:---:|
| `search_knowledge_base` | 混合检索知识库 | — |
| `search_customers` | 搜索 CRM 客户 | — |
| `get_customer_detail` | 客户完整信息 | — |
| `generate_inquiry_email` | 生成询盘邮件草稿 | ✓ |
| `list_email_status` | 查看邮件状态 | — |

**流式事件类型:**

```
event: thinking    → 思维链内容（DeepSeek V4 reasoning_content）
event: tool_start  → 工具调用开始 {name, args}
event: tool_result → 工具调用结果 {name, result}
event: content     → 文本回复（流式）
event: done        → 完成 {confirm?, tool_calls[]}
event: error       → 错误 {message}
```

**上下文管理策略：**
- localStorage 持久化（跨页面保持）
- 历史超过 6000 字符 → DeepSeek 自动摘要旧消息（100 字压缩）
- 最近 6 条原始消息 + 摘要 → system prompt

---

## 6. RAG 基础设施

### 6.1 嵌入服务 (`tools/embedding.py`)

```python
class EmbeddingService:
    # 全局单例，首次调用时加载
    # 模型: paraphrase-multilingual-MiniLM-L12-v2
    # 维度: 384, 大小: ~120MB
    # 引擎: fastembed + ONNX Runtime (CPU, 无需 PyTorch)

def get_embeddings(texts: list[str]) -> list[list[float]]: ...
```

### 6.2 向量存储 (`tools/vector_store.py`)

```
search(collection, query) 流程:
  1. Query Rewrite  → DeepSeek 改写 2-3 条变体
  2. Hybrid Search  → BM25 关键词 + 语义检索，各取 top-10
  3. RRF Fusion     → k=60，合并排序，产生 top-15
  4. Re-rank        → DeepSeek 逐条打分，取 top-5
  5. 返回父文档     → 子文档匹配 → 父文档完整上下文
```

**返回结构：**
```python
{
    "chunk": "父文档文本 (1000-1500字)",
    "source_doc": "来源文件名",
    "score": 0.87,
    "rerank_score": 0.92,
    "metadata": {"section": "...", "page": N}
}
```

### 6.3 文档解析 (`tools/doc_parser.py`)

```
process_file(file_path)
  → parse_file()         # PDF/pdfplumber | 图片/OCR | DOCX/python-docx | XLSX/openpyxl
  → cleanup_ocr()        # DeepSeek 整理 OCR 碎片
  → split_into_parents() # 按章节标题分割，1000-1500 字/段
  → 质量过滤             # _is_low_quality() 过滤占位符、文件名等噪音
  → parent_to_children() # 200 字滑动窗口，overlap 50
  → 返回 [{parent_text, children[], metadata}]
```

---

## 7. 评估流水线

### 7.1 `run_pipeline()` 流程

```
Phase 0: Setup
├── 加载 Excel 配置、权重、规则
├── 标准化列名 (50+ 中英文映射)
└── 计算批次范围

Phase 1: 逐行处理
For each row:
├── 抓取公司网站（首页 + /about /products /contact）
│   ├── httpx GET → trafilatura 提取文本
│   ├── 磁盘缓存 (SHA-256, 自动去重)
│   └── 重试 3 次 (指数退避)
├── 合并 evidence_paste (人工粘贴补证)
├── run_llm_eval() → DeepSeek JSON
│   ├── 加载产品目录 (compact_catalog_for_prompt → 24000字)
│   ├── 加载知识库 (kb_prompt_block → 4000字)
│   └── build_messages() → deepseek_client.chat_json()
├── jsonschema 验证 (eval_result.schema.json)
├── overall_score_computed (加权: 0.45×产品 + 0.25×实力 + 0.30×信誉)
└── manual_review_flag (满足规则之一 → YES)

Phase 2: 输出
├── 写入结果 Excel (openpyxl, 复核行标红)
└── 可选 Detail 第二 Sheet
```

### 7.2 JSON Schema 验证

LLM 输出必须包含：product_fit_score (1-5), capability_score (1-5), reputation_safety_score (1-5), buyer_seller_role (enum), deal_recommendation (enum), confidence (0-1), data_quality (enum), citations[], 严格 `additionalProperties: false`

---

## 8. 工具层

### 8.1 DeepSeek 客户端 (`tools/deepseek_client.py`)

```python
def chat_json(messages, *, model=None, temperature=0.2, max_tokens=8192, max_retries=3) -> dict
```

**特性：**
- OpenAI 兼容协议
- `response_format={"type": "json_object"}` 强制 JSON
- 3 次重试（指数退避 1s/2s/4s）：APIConnectionError, APITimeoutError, HTTP 429, HTTP 5xx
- 失败返回 `{"_error": "..."}` 而非崩溃

### 8.2 邮件系统

**邮件生成 (`email_generator.py`)：**
- System Prompt：多语言自动选择、deal_recommendation 分级策略
- 支持 `knowledge_context` 参数（注入知识库检索结果）

**双通道发送：**
- `gmail_sender.py` — OAuth 2.0, HTTPS 443, Token 自动 refresh
- `email_sender.py` — SMTP TLS/SSL, STARTTLS 降级容错

**时区自适应 (`country_timezone.py`)：**
- 60+ 国家代码 → IANA 时区 ID
- zoneinfo 自动处理夏令时

### 8.3 知识库工具

| 工具 | 功能 |
|------|------|
| `embedding.py` | 嵌入服务单例 (fastembed + ONNX) |
| `vector_store.py` | ChromaDB + BM25 + RRF + 重排序 |
| `doc_parser.py` | 文档解析 + 父子分块 + 质量过滤 |
| `import_kb.py` | CLI 批量导入 (argparse + tqdm) |
| `eval_retrieval.py` | 检索精度/召回率评测 + 消融实验 |

### 8.4 辅助工具

| 工具 | 功能 |
|------|------|
| `build_product_catalog.py` | 从 Excel 报价单提取产品型号，生成 JSON 目录 |
| `make_demo_workbook.py` | 生成示例测试 Excel |
| `map_zh_customer_sheet.py` | 中文客户表列名映射 |
| `setup_gmail_oauth.py` | 交互式 Gmail OAuth 授权 |

---

## 9. 前端架构

### 9.1 设计系统 ("深海航路")

**色彩系统：** 暗色默认 (`#080c14` 最深背景) + 亮色主题 (`#f5f3ee` 暖奶油)

**字体：** Cormorant Garamond (标题) + Work Sans (正文) + JetBrains Mono (数据)

**特色：** SVG 噪点纹理、径向光晕背景、导航栏发光条、卡片悬浮辉光、进度条流光动画

### 9.2 模板继承

```
base.html (导航栏 + Toast + 主题切换 + 聊天挂件)
├── home.html
├── eval_index.html
├── crm_list.html + crm_detail.html
├── mail_index.html
├── kb_index.html
└── chat_widget.html (include 到 base.html 底部)
```

### 9.3 JavaScript 架构

**ES Modules：** `api.js` (fetch 封装) → `utils.js` (Toast/Badge) → 各 Agent 独立模块

**聊天挂件：** SSE 流式读取、思维链可折叠、跨页面 localStorage 持久化

---

## 10. 数据库设计

### 10.1 核心表

**`customer` (客户主表)** — 35+ 列：原始输入 (11列) + 评估结果 (18列) + 邮件状态 (4列) + 元数据 (2列)
索引：batch_id, company_name, overall_score_computed, deal_recommendation, email_status

**`evaluation_batch` (评估批次)** — id, original_filename, total_rows, status, created_at

**`salesperson` (销售负责)** — id, name, active

**`daily_send_log` (每日发送日志)** — sent_date, recipient_email, customer_id, status

### 10.2 数据流

```
Excel 上传 → run_pipeline() AI 评估 → customer 表
  → CRM 浏览/搜索 → 生成邮件 (customer.email_status='draft')
  → 确认发送 → email_status='sent' + daily_send_log
  → ChromaDB 知识库 → 客服 RAG 检索
```

---

## 11. 部署方案

### 11.1 本地运行 (Windows)

```batch
start_all.bat
# 启动: Redis → RQ Workers (×3) → Uvicorn
```

### 11.2 Docker 部署

```bash
docker compose up -d --build
```

服务：redis (7 Alpine), web (FastAPI:8000), worker (RQ, 3GB mem)

### 11.3 环境变量

核心必需（见 `.env.example`）：
- `DEEPSEEK_API_KEY` — DeepSeek API 密钥
- `SMTP_*` — 邮件发送配置（使用 SMTP 时）
- Gmail API 方式需：`var/gmail_client_secret.json` + 运行 `setup_gmail_oauth.py`

---

## 附录 A：技术决策记录

| 决策 | 原因 |
|------|------|
| Jinja2 原生而非 Starlette 封装 | Jinja2 3.1.6 `unhashable type: 'dict'` 缓存 bug |
| SQLite 而非 PostgreSQL | 轻量部署，WAL 模式提供足够并发 |
| 线程本地连接而非连接池 | SQLite 不适合多线程写，线程本地串行化 |
| ES Modules 而非打包工具 | 零构建，浏览器原生支持 |
| Gmail API 优先于 SMTP | 防火墙可能阻断 SMTP，API 走 443 |
| RQ 而非 Celery | Windows 兼容 (Celery 在 Windows 上支持有限) |
| ChromaDB 而非 Pinecone/Weaviate | 轻量本地部署，与 SQLite 理念一致 |
| fastembed + ONNX 而非 sentence-transformers | 无需 PyTorch, Windows 兼容, 内存占用小 |
| SSE 而非 WebSocket | 单向流式推送足够，实现简单 |
| DeepSeek V4 思考模式保持启用 | 统一多轮对话 consistency，避免 400 错误 |

---

## 附录 B：关键路径速查

### 评估：Excel → 数据库

```
POST /customer-eval/api/jobs → enqueue run_eval_job
  → run_pipeline() → run_llm_eval() → DeepSeek JSON
  → _save_to_database() → executemany INSERT INTO customer
```

### 邮件：数据库 → 发送

```
POST /inquiry-mail/api/generate → 检索知识库 → LLM 生成
  → UPDATE customer SET email_status='draft'
  → POST /inquiry-mail/api/send → Gmail API / SMTP
  → UPDATE customer SET email_status='sent' + INSERT daily_send_log
```

### 客服：对话 → 操作

```
POST /chat/api/chat/stream → run_agent_stream()
  → DeepSeek + Function Calling → 调用工具
  → write? → confirm_card → POST /chat/api/chat/confirm
  → read? → 流式 SSE 返回
```

### 知识库检索：查询 → 文档

```
search(collection, query)
  → Query Rewrite → BM25 + Vector → RRF → Re-rank → 父文档
```

---

## 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0 | 2026-05-15 | 首版：3 Agent + 评估流水线 |
| 2.0 | 2026-05-19 | 加入知识库、智能客服、RAG 基础设施 |
