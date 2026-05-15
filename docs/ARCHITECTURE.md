# AI 获客平台 — 底层框架与工作流编排技术文档

> 版本：V1.0 | 最后更新：2026-05-15 | 项目：`ai-lead-gen-platform`

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [启动流程详解](#2-启动流程详解)
3. [核心框架层](#3-核心框架层)
4. [Agent 自动发现机制](#4-agent-自动发现机制)
5. [Agent 1：客户评估 (customer_eval)](#5-agent-1客户评估-customer_eval)
6. [Agent 2：客户资源管理 (CRM)](#6-agent-2客户资源管理-crm)
7. [Agent 3：询盘邮件 (inquiry_mail)](#7-agent-3询盘邮件-inquiry_mail)
8. [评估流水线 (Pipeline)](#8-评估流水线-pipeline)
9. [工具层](#9-工具层)
10. [前端架构](#10-前端架构)
11. [数据库设计](#11-数据库设计)
12. [部署方案](#12-部署方案)

---

## 1. 系统架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Application                      │
│                     (src/core/app.py)                        │
├─────────────────────────────────────────────────────────────┤
│  GET /            → 首页 (home.html)                         │
│  GET /health      → 健康检查                                  │
│  /customer-eval/  → Agent 1: 客户评估 (上传→AI审核→入库)     │
│  /crm/            → Agent 2: 客户资源 (浏览/搜索/筛选/详情)   │
│  /inquiry-mail/   → Agent 3: 询盘邮件 (生成→预览→发送)       │
├─────────────────────────────────────────────────────────────┤
│                    共享基础设施                                │
│  src/core/config.py    → PlatformConfig (统一配置)            │
│  src/core/database.py  → SQLite + WAL 模式                   │
│  src/core/redis_utils.py → Redis 连接池 + RQ 任务管理         │
│  src/core/auth.py      → HTTP Basic 认证                     │
├─────────────────────────────────────────────────────────────┤
│                    后台任务队列                                │
│  Redis + RQ (rq worker)                                      │
│  customer_eval:default → 评估任务                             │
│  inquiry_mail:default  → 邮件生成任务                         │
│  inquiry_mail:send     → 邮件发送任务                         │
└─────────────────────────────────────────────────────────────┘
```

**技术栈:** Python/FastAPI + SQLite (WAL) + Redis/RQ + Jinja2 + Vanilla JS (ES Modules)

---

## 2. 启动流程详解

### 2.1 入口点

```
uvicorn src.core.app:app --host 127.0.0.1 --port 8000
```

或通过 `start_all.bat` 启动完整栈：
```
Redis → RQ Workers (×3) → Uvicorn Web Server
```

### 2.2 `create_app()` 完整执行流程

```
1. load_dotenv()                           ← 从仓库根目录 .env 加载环境变量
2. get_config() → PlatformConfig.from_env()  ← 读取所有配置（单例缓存）
3. config.data_dir.mkdir(parents=True)      ← 创建 var/platform/ 目录
4. FastAPI(title=config.app_title)          ← 创建 FastAPI 实例
5. discover_agents()                        ← 扫描 src/agents/*/manifest.py
6. 对每个 Agent:
   ├── include_router(prefix="/{name}")     ← 挂载路由
   ├── mount(StaticFiles) at /static/{name} ← 挂载静态文件（在共享之前）
   ├── prepend template_dir 到搜索路径      ← Agent 模板优先
   └── 收集 nav 条目
7. mount shared static at /static           ← 共享静态文件（最后挂载）
8. 排序 nav_agents（按 nav.order）
9. 创建 Jinja2 Environment (FileSystemLoader) ← 不用 Starlette 的 Jinja2Templates
10. 定义 /health 和 / 路由
11. 将 nav_agents, agents, jinja_env, config 存储到 app.state
12. 注册 shutdown handler → close_db()
```

### 2.3 静态文件挂载顺序（优先级从高到低）

| 优先级 | 路径 | 用途 |
|--------|------|------|
| 1 (最高) | `/static/{agent_name}` | Agent 专属静态文件 |
| 2 (最低) | `/static` | 共享静态文件 |

### 2.4 Jinja2 模板解析顺序

1. Agent 模板目录（插入到搜索路径最前面）
2. `src/templates/`（共享模板）

---

## 3. 核心框架层

### 3.1 PlatformConfig (`src/core/config.py`)

统一平台配置，使用 pydantic-settings 风格但实现为 dataclass。

| 字段 | 类型 | 默认值 | 环境变量 |
|------|------|--------|----------|
| `app_title` | `str` | `"外贸客户平台"` | `APP_TITLE` |
| `app_version` | `str` | `"2.0.0"` | （硬编码） |
| `redis_url` | `str` | `"redis://127.0.0.1:6379/0"` | `REDIS_URL` |
| `data_dir` | `Path` | `REPO_ROOT/var/platform` | `PLATFORM_DATA_DIR` |
| `basic_user` | `str\|None` | `None` | `BASIC_USER` |
| `basic_password` | `str\|None` | `None` | `BASIC_PASSWORD` |
| `debug` | `bool` | `False` | `DEBUG` |
| `db_path` | `Path` | `data_dir/platform.db` | （自动推导） |

`get_config()` 实现为单例模式——首次调用时创建并缓存 `PlatformConfig.from_env()`。

### 3.2 数据库管理 (`src/core/database.py`)

**线程本地连接模式：**

```python
_local = threading.local()

def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "connection", None)
    if conn is None:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")        # 写前日志
        conn.execute("PRAGMA foreign_keys=ON")          # 外键约束
        conn.execute("PRAGMA busy_timeout=5000")       # 锁等待 5 秒
        conn.executescript(SCHEMA_SQL)                  # 自动建表
        _local.connection = conn
    return conn
```

**关键设计决策：**
- 不使用 `check_same_thread=False`：每个线程有自己的连接，无需跨线程共享
- WAL 模式：允许多个读操作与一个写操作并发
- `busy_timeout=5000`：写锁冲突时等待 5 秒而非立即抛出异常
- 应用关闭时通过 `close_db()` 清理连接

### 3.3 Redis / RQ 工具 (`src/core/redis_utils.py`)

**连接复用：** `_get_redis(url)` 使用 `@functools.lru_cache(maxsize=8)` 缓存，同一 URL 返回相同连接。

**核心函数：**

```python
def get_queue(redis_url: str, queue_name: str) -> Queue
    # 返回 RQ Queue 实例

def get_rq_job_info(job_id_file_path: Path, redis_url: str) -> dict
    # 从文件读取 RQ job ID → Redis 获取 Job → 返回 {rq_status, progress}
    # 处理 NoSuchJobError 和其他异常，优雅降级为 "unknown" / "not_found"
```

`get_rq_job_info()` 统一了所有 Agent 路由中的作业状态查询逻辑，消除了 4 处重复代码。

### 3.4 认证 (`src/core/auth.py`)

**HTTP Basic 认证：**
- 如果 `BASIC_USER` 或 `BASIC_PASSWORD` 未配置 → 认证完全跳过
- 如果配置了 → 要求浏览器提供用户名密码，不匹配返回 401

所有 Agent 的 API 路由通过 `Depends(require_auth)` 注入认证依赖。

---

## 4. Agent 自动发现机制

### 4.1 AgentManifest 协议 (`src/agents/base.py`)

```python
@dataclass
class AgentManifest:
    name: str               # URL 前缀，如 "customer-eval"
    display_name: str       # 导航栏显示名，如 "客户评估"
    description: str = ""   # 简介
    router: Any = None      # FastAPI APIRouter
    template_dir: str | None = None   # Jinja2 模板目录
    static_dir: str | None = None     # 静态文件目录
    config_class: type | None = None  # 配置类（如 InquiryMailConfig）
    nav: dict | None = None           # 导航栏配置 {"icon": "...", "order": 1}
```

### 4.2 发现流程 (`src/agents/__init__.py`)

```
discover_agents()
  ├── 扫描 src/agents/ 下所有子目录
  ├── 跳过 _ 或 . 开头的目录
  ├── 检查 manifest.py 是否存在
  ├── importlib.import_module(f"src.agents.{name}.manifest")
  ├── 调用 mod.register() → 获取 AgentManifest 实例
  ├── 存储到 _agents[manifest.name] 字典
  └── 缓存结果，后续调用直接返回
```

**新增 Agent 只需 3 步：**
1. 在 `src/agents/` 下创建目录
2. 创建 `manifest.py` 并实现 `register()` 函数
3. 创建 `routes.py` 定义 FastAPI 路由

无需修改核心代码。

---

## 5. Agent 1：客户评估 (customer_eval)

**URL 前缀:** `/customer-eval/` | **RQ 队列:** `customer_eval:default`

### 5.1 工作流

```
用户上传 Excel (.xlsx)
  → POST /api/jobs
    ├── 验证扩展名为 .xlsx
    ├── UUID 生成 job_id，创建 job 目录
    ├── 流式保存文件到 input.xlsx（限制 max_upload_mb）
    ├── 用 pandas 读取 Excel，验证行数 ≤ max_rows
    ├── 入队 RQ 任务 run_eval_job（timeout=2700s）
    ├── 写 rq_job_id.txt
    ├── 插入 evaluation_batch 记录（status='queued'）
    └── 返回 {"job_id", "rq_job_id", "status": "queued"}
  → RQ Worker 执行 run_eval_job()
    ├── run_pipeline() 调用 AI 评估（详见第 8 节）
    ├── _save_to_database() 批量入库
    └── _update_batch_status("finished")
  → GET /api/jobs/{job_id} 轮询进度
  → GET /api/jobs/{job_id}/download 下载结果 Excel
```

### 5.2 关键端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/` | 评估页面 |
| `POST` | `/api/jobs` | 创建评估任务（上传文件） |
| `POST` | `/api/jobs/{id}/continue` | 继续分批评估 |
| `GET` | `/api/jobs/{id}` | 查询任务状态和进度 |
| `GET` | `/api/jobs/{id}/download` | 下载评估结果 Excel |

### 5.3 配置 (CustomerEvalConfig)

| 字段 | 默认 | 环境变量 |
|------|------|----------|
| `max_upload_mb` | 32 | `CUSTOMER_EVAL_MAX_UPLOAD_MB` |
| `max_rows` | 500 | `CUSTOMER_EVAL_MAX_ROWS` |
| `queue_name` | `"customer_eval:default"` | `CUSTOMER_EVAL_QUEUE` |
| `job_timeout` | 2700 | （硬编码，45 分钟） |

### 5.4 数据库写入流程 (`_save_to_database`)

```
1. BEGIN TRANSACTION
2. DELETE FROM customer WHERE batch_id = ?     ← 删除该批次旧数据
3. executemany INSERT INTO customer (...)      ← 批量写入新数据
4. COMMIT
```

每行数据处理：
- `.item()` 转换 numpy 标量
- `pd.isna()` → None
- 字符串 `.strip()`
- XSS 防护：website 字段若非 http/https 开头，自动补 `https://`

---

## 6. Agent 2：客户资源管理 (CRM)

**URL 前缀:** `/crm/`

### 6.1 关键端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/` | 客户列表页 |
| `GET` | `/{customer_id}` | 客户详情页（服务端渲染） |
| `GET` | `/api/customers` | 客户列表 API（搜索/筛选/排序/分页） |
| `GET` | `/api/customers/{id}` | 单客户 JSON |
| `GET` | `/api/batches` | 评估批次列表 |
| `GET` | `/api/customers/export` | 导出客户数据 |
| `POST` | `/api/smart-search` | **AI 自然语言搜索** |

### 6.2 搜索与筛选逻辑

`GET /api/customers` 支持的查询参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `search` | `str` | `""` | 模糊匹配 company_name, contact_name, contact_email, website |
| `deal_recommendation` | `str` | `""` | 精确匹配：high_intent / watch / no |
| `min_score` | `float` | `None` | 最低评分筛选 |
| `country` | `str` | `""` | 国家模糊匹配 |
| `review_flag` | `str` | `""` | 复核状态筛选 |
| `sort` | `str` | `"-created_at"` | 排序字段（6 种排序方式） |
| `page` | `int` | `1` | 分页（≥1） |
| `page_size` | `int` | `20` | 每页条数（1-200） |
| `batch_id` | `str` | `""` | 按批次筛选 |

**排序选项：** `-created_at`, `created_at`, `-overall_score_computed`, `overall_score_computed`, `-company_name`, `company_name`

### 6.3 AI 智能搜索 (`POST /api/smart-search`)

```
用户输入自然语言
  → 构建 LLM prompt（包含完整 customer 表 schema 提示）
  → chat_json() 调用 DeepSeek
  → 解析返回的 SQL
  → 安全检查：
    ├── 必须以 SELECT 开头
    ├── 禁止 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/EXEC/ATTACH
    └── 不通过 → 返回 400 错误
  → db.execute(sql) 执行查询
  → 返回 {"query", "sql", "explanation", "customers", "count"}
```

---

## 7. Agent 3：询盘邮件 (inquiry_mail)

**URL 前缀:** `/inquiry-mail/` | **RQ 队列:** `inquiry_mail:default`（生成）, `inquiry_mail:send`（发送）

### 7.1 完整工作流

```
Step 1: 选择客户
  GET /api/customers/emailable → 列出有邮箱的客户（搜索/筛选）
  
Step 2: 生成邮件
  POST /api/generate
    → 入队 generate_emails_job
    → LLM 根据客户国家/行业生成个性化邮件（多语言）
    → 写入 emails.json + 更新 customer 表 email_status='generated'

Step 3: 预览与发送
  GET /api/emails/{job_id} → 获取生成的邮件列表
  POST /api/send
    → 前置检查：
      ├── Gmail OAuth token 或 SMTP 配置可用？
      ├── 今日配额是否已满？
      └── 时区感知：客户当地时间是否在工作时段？
    → 入队 send_emails_job
    → Worker 发送（Gmail API 优先，SMTP 回退）
    → 记录 daily_send_log（每日统计）

Step 4: 查看结果
  GET /api/send-status/{job_id} → 发送进度和结果
```

### 7.2 邮件发送通道选择

```
1. var/gmail_token.json 存在？ → Gmail API (HTTPS 443, 不经过 SMTP 端口)
2. var/gmail_client_secret.json 存在但无 token？ → 记录警告，回退 SMTP
3. 否则 → SMTP (需配置 SMTP_HOST / SMTP_FROM_EMAIL)
```

### 7.3 反风控机制

| 机制 | 默认值 | 配置 |
|------|--------|------|
| 发送间隔 | 45 秒/封 | `SMTP_SEND_DELAY` |
| 每日限额 | 50 封/天 | `MAIL_DAILY_LIMIT` |
| 时区感知 | 仅当地 9-17 点发送 | `MAIL_SEND_HOUR_START/END` |
| 夏令时适配 | 自动（zoneinfo） | 无需配置 |
| Message-ID / Date 头 | 自动生成 | - |
| List-Unsubscribe | 自动添加 | - |

### 7.4 配置 (InquiryMailConfig)

| 字段 | 类型 | 默认值 | 环境变量 |
|------|------|--------|----------|
| `smtp_host` | `str` | `""` | `SMTP_HOST` |
| `smtp_port` | `int` | `587` | `SMTP_PORT` |
| `smtp_username` | `str` | `""` | `SMTP_USER` |
| `smtp_password` | `str` | `""` | `SMTP_PASSWORD` |
| `from_email` | `str` | `""` | `SMTP_FROM_EMAIL` |
| `from_name` | `str` | `"外贸团队"` | `SMTP_FROM_NAME` |
| `reply_to_email` | `str` | `""` | `SMTP_REPLY_TO` |
| `send_delay_seconds` | `float` | `45.0` | `SMTP_SEND_DELAY` |
| `max_per_job` | `int` | `50` | `MAIL_MAX_PER_JOB` |
| `daily_limit` | `int` | `50` | `MAIL_DAILY_LIMIT` |
| `default_language` | `str` | `"auto"` | `MAIL_DEFAULT_LANG` |
| `respect_timezone` | `bool` | `True` | `MAIL_RESPECT_TZ` |
| `business_hours_start` | `int` | `9` | `MAIL_SEND_HOUR_START` |
| `business_hours_end` | `int` | `17` | `MAIL_SEND_HOUR_END` |

---

## 8. 评估流水线 (Pipeline)

### 8.1 `run_pipeline()` 完整执行流程

```
Phase 0: Setup
├── load_excel_io() → 加载 Excel 输入/输出列配置
├── merge_meta_with_file() → 合并 pipeline 配置文件
├── merge_meta_from_env() → 合并 PIPELINE_CONFIG_PATH 环境变量
├── read_input_xlsx() → 读取输入 Excel
│   ├── openpyxl 读取
│   ├── normalize_column_map() → 中英文列名映射到标准名称
│   ├── merge_extra_columns_into_notes() → 未知列追加到备注
│   └── ensure_output_columns() → 初始化输出列
├── 解析权重、规则、缓存目录、catalog 路径
└── 计算批次范围 [start_row, start_row + limit)

Phase 1: 逐行处理
For each row:
├── 推断公司名（如果缺失）
├── [如果 no_fetch=False] fetch_pages_for_website_field()
│   ├── expand_urls(base_url, suffixes) → 扩展 URL
│   ├── httpx.Client GET → trafilatura.extract() → 提取文本
│   ├── 磁盘缓存 (SHA-256 key, 24字符)
│   └── 重试 3 次（0.4s/0.8s/1.2s 退避）
├── merge_scrape_and_paste() → 合并抓取文本 + 人工粘贴
├── run_llm_eval() → 调用 DeepSeek 评估（详见 8.2）
├── cap_model_data_quality() → 修正数据质量评分
├── _flatten_eval() → 展平为 DataFrame 列
├── overall_score_computed() → 加权计算总分
│   └── 权重: product_fit=0.45, capability=0.25, reputation_safety=0.3
└── manual_review_flag() → 判断是否需人工复核

Phase 2: 输出
├── build_summary_export_df() → 构建 Summary 工作表
├── 中文显示转换（deal_recommendation → 高意向跟进/观察/不建议深入）
├── write_result_xlsx() → openpyxl 写入，标记需复核行（红色）
└── [如果 append_output=True] 追加到已有文件
```

### 8.2 `run_llm_eval()` — LLM 评估步骤

```
1. 加载 product catalog (JSON, 来自 build_product_catalog.py)
2. compact_catalog_for_prompt() → 压缩到 24000 字符
3. 加载知识库 (kb.json, 如果存在)
4. kb_prompt_block() → 压缩到 4000 字符
5. build_messages() → 构建 system + user prompt
   ├── System: 详细评估指令（评分标准、输出格式）
   └── User: 公司信息 + 网页抓取证据 + 产品目录 + 知识库
6. chat_json() → DeepSeek API → response_format={"type": "json_object"}
7. validate_eval() → jsonschema 验证（eval_result.schema.json）
8. 返回评估结果 dict
```

### 8.3 JSON Schema 验证 (`schemas/eval_result.schema.json`)

LLM 输出必须包含且仅包含：
- `product_fit_score` (int 1-5) + `product_fit_reasons` (string[])
- `capability_score` (int 1-5) + `capability_signals` (string[])
- `reputation_risk` {facts, concerns, sources}
- `reputation_safety_score` (int 1-5)
- `buyer_seller_role` (enum: buyer/seller/both/unclear)
- `deal_recommendation` (enum: high_intent/watch/no)
- `confidence` (float 0-1)
- `data_quality` (enum: high/medium/low)
- `citations` (array of {claim, source_url, source_snippet})
- 严格 `additionalProperties: false`

### 8.4 输入列名标准化

`io_excel.py` 维护了一个约 50 条中英文列名→标准名称的映射表：

```
"客户名称" → company_name
"网址" → website  
"国家/地区" → country_region
"目标产品" → target_products
"company" → company_name
"email" → contact_email
...
```

---

## 9. 工具层

### 9.1 DeepSeek 客户端 (`tools/deepseek_client.py`)

```python
def chat_json(
    messages: list[dict[str, str]],
    *, model=None, temperature=0.2, max_tokens=8192, max_retries=3
) -> dict[str, Any]:
```

**关键特性：**
- 使用 OpenAI 兼容协议 (`openai.OpenAI` 客户端)
- `response_format={"type": "json_object"}` 强制 JSON 输出
- **重试机制**（3次，指数退避 1s/2s/4s）：
  - 可重试：`APIConnectionError`, `APITimeoutError`, HTTP 429, HTTP 5xx
  - 不重试：HTTP 400, 401, 403
- 失败时返回 `{"_error": "..."}` 而非崩溃
- 从环境变量读取 `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `DEEPSEEK_BASE_URL`

### 9.2 邮件生成器 (`tools/email_generator.py`)

**System Prompt 核心规则：**
1. 输出合法 JSON（非 markdown）
2. 语言：根据客户国家自动选择（法国→法语，德国→德语，日本→日语，未知→英语）
3. `deal_recommendation="no"` → 自动跳过
4. `deal_recommendation="watch"` → 试探性短邮件
5. `deal_recommendation="high_intent"` → 详细介绍产品
6. 署名使用 `from_name` 和 `from_company`
7. 无 contact_name 时使用对应语言的称呼

### 9.3 邮件发送器（双通道）

**Gmail API (`tools/gmail_sender.py`)：**
- OAuth 2.0 授权（scope: `gmail.send`）
- Token 存储在 `var/gmail_token.json`，自动 refresh
- MIME multipart 消息：纯文本 + HTML
- 添加 Message-ID, Date, Reply-To, List-Unsubscribe 头

**SMTP (`tools/email_sender.py`)：**
- 支持 TLS (STARTTLS) 和 SSL 两种模式
- STARTTLS 失败时记录警告但不中断
- 同样添加完整的 MIME 头

### 9.4 时区模块 (`tools/country_timezone.py`)

**DST 自适应实现：**

```python
# 60+ 国家代码 → IANA 时区 ID
COUNTRY_TO_TZ = {
    "US": "America/New_York",    # EST/EDT 自动切换
    "DE": "Europe/Berlin",       # CET/CEST 自动切换
    "GB": "Europe/London",       # GMT/BST 自动切换
    "JP": "Asia/Tokyo",          # 无 DST
    ...
}

def get_utc_offset(country: str) -> float | None:
    # 用 zoneinfo.ZoneInfo 获取当前实际 UTC 偏移（含 DST）
    tz = ZoneInfo(tz_name)
    return now_utc.astimezone(tz).utcoffset().total_seconds() / 3600.0
```

### 9.5 网页抓取与缓存 (`tools/pipeline/fetch_cache.py`)

```
fetch_one(url)
├── 计算 SHA-256 hash → 24 字符缓存键
├── 检查磁盘缓存（{key}.txt）
├── [缓存命中] → 直接返回
├── [缓存未命中] → httpx GET
├── trafilatura.extract() 提取纯文本
├── [失败] → _strip_html_fallback() 正则清理
├── 写入缓存
└── 返回 {ok, url, text, from_cache}
```

### 9.6 辅助工具

| 工具 | 功能 |
|------|------|
| `build_product_catalog.py` | 从 Excel 报价单提取产品型号和规格，生成 JSON 目录 |
| `make_demo_workbook.py` | 生成示例测试 Excel 文件 |
| `map_zh_customer_sheet.py` | 中文客户表列名映射 |
| `eval_company_fit.py` | 独立的公司适配评估 CLI 工具 |
| `setup_gmail_oauth.py` | 交互式 Gmail OAuth 授权脚本 |

---

## 10. 前端架构

### 10.1 设计系统："深海航路" (`platform.css`)

**设计理念：** 暗色默认、海洋色系、精致衬线体、全球贸易气质

**色彩系统 (CSS Variables)：**
| 用途 | 变量 | 色值 |
|------|------|------|
| 最深背景 | `--bg-root` | `#080c14` |
| 卡片背景 | `--bg-card` | `#111827` |
| 输入框背景 | `--bg-input` | `#1f2a3f` |
| 主文字 | `--text-primary` | `#e8edf5` |
| 次要文字 | `--text-secondary` | `#8899b4` |
| 强调色 | `--ocean-500` | `#0077b6` |
| 成功色 | `--color-success` | `#06d6a0` |
| 金色点缀 | `--gold-500` | `#c9a94e` |

**字体系统：**
| 用途 | 字体 |
|------|------|
| 标题 | Cormorant Garamond (衬线) |
| 正文 | Work Sans (无衬线) |
| 数据/代码 | JetBrains Mono (等宽) |

**特色设计元素：**
- 全局 SVG 噪点纹理覆盖层（`body::after`, opacity 0.035）
- 径向光晕背景（`radial-gradient` 营造深度感）
- 导航栏 Active 状态底边渐变发光条
- 卡片悬浮时辉光效果 + 上浮动画
- 进度条流光动画（`shimmer` keyframe）
- 自定义 Checkbox（SVG 勾选标记）
- Toast 弹性滑入动画（`cubic-bezier(0.34, 1.56, 0.64, 1)`）
- 完整亮色主题（暖奶油基调 `#f5f3ee`）

### 10.2 模板系统 (`base.html`)

**继承结构：**
```
base.html (导航栏 + Toast容器 + 主题切换)
├── home.html (首页：Hero + Agent 卡片网格)
├── eval_index.html (客户评估页)
├── crm_list.html + crm_detail.html (CRM 页面)
└── mail_index.html (询盘邮件 4 步骤页)
```

**主题切换逻辑：**
```javascript
function toggleTheme() {
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('platform-theme', next);
}
// 页面加载时从 localStorage 恢复主题偏好
```

### 10.3 JavaScript 架构

**模块化 ES Modules：**
```
api.js      → apiFetch/ apiPost (统一 fetch + Basic Auth + 401 自动重试)
utils.js    → showToast / badgeForRecommendation / badgeForReview / 
              badgeForEmailStatus / ProgressBar / esc
eval_main.js → 客户评估页（上传/拖放/进度轮询）
crm.js       → CRM 页（搜索/筛选/排序/分页/导出）
mail.js      → 邮件页（客户选择/生成/预览/发送 4 步流程）
```

**关键设计模式：**
- 模块作用域变量管理页面状态（不污染全局）
- `window.xxx = xxx` 桥接模式：HTML 内联事件可以调用模块函数
- 基于 `FormData` 的 API 请求
- 轮询模式（2 秒间隔）获取任务进度

---

## 11. 数据库设计

### 11.1 完整 Schema

**`evaluation_batch` (评估批次表)**
```sql
CREATE TABLE evaluation_batch (
    id TEXT PRIMARY KEY,           -- UUID
    original_filename TEXT,        -- 上传的文件名
    total_rows INTEGER DEFAULT 0,  -- 总行数
    status TEXT DEFAULT 'queued',  -- queued/running/finished/failed
    created_at TEXT DEFAULT (datetime('now','localtime')),
    completed_at TEXT
);
```

**`customer` (客户主表)** — 35 列，涵盖：
- **原始输入** (11 列): company_name, website, country_region, contact_name, contact_email, contact_phone, contact_address, target_products, priority, notes, batch_id
- **评估结果** (18 列): product_fit_score, product_fit_reasons, capability_score, capability_signals, reputation_* (4列), buyer_seller_*, deal_recommendation, next_action, confidence, data_quality, overall_score_computed, manual_review_flag
- **邮件状态** (4 列): email_subject, email_body, email_status, email_sent_at
- **元数据** (2 列): created_at, updated_at

**索引：** batch_id, company_name, overall_score_computed, deal_recommendation, email_status

**`daily_send_log` (每日发送日志)**
```sql
CREATE TABLE daily_send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_date TEXT NOT NULL,       -- 发送日期
    recipient_email TEXT NOT NULL, -- 收件人
    customer_id INTEGER,           -- 关联客户
    status TEXT NOT NULL DEFAULT 'sent',  -- sent/failed
    sent_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_daily_send_log_date ON daily_send_log(sent_date);
```

### 11.2 数据流

```
Excel 上传 → read_input_xlsx() 标准化
           → run_pipeline() AI 评估
           → _save_to_database() 写入 customer 表
           → CRM 页面浏览/搜索
           → 生成邮件 → email_status='generated'
           → 发送邮件 → email_status='sent' + daily_send_log
```

---

## 12. 部署方案

### 12.1 本地运行 (Windows)

```batch
start_all.bat
```
启动顺序：Redis → RQ Workers (×3) → Uvicorn Web Server

Worker 启动命令：
```bash
rq worker -u redis://127.0.0.1:6379/0 \
  customer_eval:default inquiry_mail:default inquiry_mail:send
```

### 12.2 Docker 部署

```bash
docker compose up -d --build
```

服务配置：
| 服务 | 内存限制 | 说明 |
|------|----------|------|
| redis | - | Redis 7 Alpine, 512MB maxmemory |
| web | - | FastAPI, 端口 8000 |
| worker | 3GB | RQ Worker, 1GB shm |

### 12.3 环境变量

核心必需变量（见 `.env.example`）：
```bash
DEEPSEEK_API_KEY=sk-xxx          # DeepSeek API 密钥
SMTP_HOST=smtp.gmail.com         # SMTP 服务器（使用 SMTP 时）
SMTP_FROM_EMAIL=your@email.com   # 发件邮箱
# Gmail API 方式需要：
#   var/gmail_client_secret.json (OAuth 客户端密钥)
#   运行 python tools/setup_gmail_oauth.py 获取 token
```

---

## 附录 A：技术决策记录

| 决策 | 原因 |
|------|------|
| Jinja2 Environment 而非 Starlette Jinja2Templates | Jinja2 3.1.6 存在 `unhashable type: 'dict'` 缓存 bug |
| SQLite 而非 PostgreSQL | 轻量部署，无需额外服务，WAL 模式提供足够并发 |
| 线程本地连接而非连接池 | SQLite 不适合多线程写，线程本地串行化写入 |
| ES Modules 而非打包工具 | 零构建步骤，浏览器原生支持 |
| Gmail API 优先于 SMTP | 防火墙可能阻断 SMTP 端口 (465/587)，Gmail API 走 HTTPS 443 |
| RQ 而非 Celery | Windows 兼容性（Celery 在 Windows 上支持有限） |
| `datetime.timezone.utc` 而非 `datetime.UTC` | 兼容 Python 3.9+（`datetime.UTC` 需要 3.11+） |
| `zoneinfo` 而非静态时区偏移表 | 自动处理夏令时，无需每年更新 |

---

## 附录 B：关键路径速查表

### 评估：Excel → 数据库

```
1. 前端 POST FormData → /customer-eval/api/jobs
2. routes.create_job() 保存文件 → enqueue run_eval_job
3. tasks.run_eval_job() → runner.run_pipeline() 
4. runner.run_pipeline() → io_excel.read_input_xlsx() → 逐行 run_llm_eval()
5. tasks._save_to_database() → executemany INSERT INTO customer
```

### 邮件：数据库 → 发送

```
1. 前端选择客户 → POST /inquiry-mail/api/generate
2. tasks.generate_emails_job() → email_generator.generate_emails_batch()
3. 写入 emails.json + UPDATE customer SET email_status='generated'
4. 前端预览 → POST /inquiry-mail/api/send
5. tasks.send_emails_job() → gmail_sender/email_sender → 发送
6. UPDATE customer SET email_status='sent' + INSERT daily_send_log
```

### 页面请求流程

```
浏览器 → FastAPI route → app.state.jinja_env.get_template()
  → FileSystemLoader 遍历搜索路径
  → Agent 模板目录优先 → 共享模板回退
  → 渲染 → HTMLResponse
  → 浏览器加载 /static/ 资源
  → Agent 静态优先 → 共享静态回退
```
