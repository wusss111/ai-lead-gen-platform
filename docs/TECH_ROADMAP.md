# 技术栈与实现路线

**文档版本**：2.0
**配套**：[PRD.md](./PRD.md) · [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## 1. 技术栈

### 1.1 核心框架

| 层级 | 选型 | 说明 |
|------|------|------|
| Web 框架 | FastAPI (Python 3.11+) | 异步，Agent 自动发现 |
| ASGI 服务器 | Uvicorn | 高性能，热重载 |
| 任务队列 | Redis + RQ | Windows 兼容 (SimpleWorker) |
| 数据库 | SQLite (WAL 模式) | 线程本地连接，零运维 |
| 模板引擎 | Jinja2 (原生 Environment) | FileSystemLoader 多搜索路径 |
| 前端 | Vanilla JS (ES Modules) | 零构建，浏览器原生 |

### 1.2 AI / LLM

| 组件 | 选型 | 规格 |
|------|------|------|
| 推理模型 | DeepSeek V4 (OpenAI 兼容) | flash=日常 / pro=复杂任务 |
| 嵌入模型 | paraphrase-multilingual-MiniLM-L12-v2 | 384 维，~120MB |
| 嵌入引擎 | fastembed + ONNX Runtime | CPU 推理，无需 PyTorch |
| 向量数据库 | ChromaDB (PersistentClient) | SQLite 后端，本地文件 |
| 关键词检索 | rank_bm25 (BM25Okapi) | 进程内索引，线程安全 |

### 1.3 文档处理

| 格式 | 工具 | 策略 |
|------|------|------|
| PDF | pdfplumber | 流式逐页提取文本层 |
| 图片 | pytesseract + DeepSeek | OCR 识别 → AI 整理碎片 |
| XLSX | openpyxl (read_only) | JSON 序列化结构化表格 |
| DOCX | python-docx | 段落级读取 |
| TXT/MD | 原生 | UTF-8 直接读取 |

### 1.4 邮件系统

| 通道 | 协议 | 端口 | 认证 |
|------|------|------|------|
| Gmail API | HTTPS | 443 | OAuth 2.0 (Token 自动 refresh) |
| SMTP | TLS / SSL | 587 / 465 | 应用专用密码 |

### 1.5 前端设计

| 层级 | 选型 | 说明 |
|------|------|------|
| CSS 基础 | 定制暗色系统 ("深海航路") | 暗色 `#080c14` + 亮色 `#f5f3ee` |
| 字体 | Cormorant Garamond + Work Sans + JetBrains Mono | 衬线标题 + 无衬线正文 + 等宽数据 |
| 聊天交互 | SSE | 流式输出 + 思维链可视化 |
| 状态持久 | localStorage | 聊天历史、主题偏好、任务追踪 |

---

## 2. 多 Agent 协同架构总图

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                     BROWSER (前端)                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                    │
│  │ customer-eval│  │     crm      │  │ inquiry-mail │  │knowledge-base│                    │
│  │  (上传/进度) │  │(列表/详情/搜索)│  │(选客户/生成/发)│  │(导入/搜索/管理)│                    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                    │
│         │                 │                 │                 │                             │
│  ┌──────┴─────────────────┴─────────────────┴─────────────────┴──────┐                      │
│  │               chat_agent 智能客服挂件 (右下角，所有页面可见)         │                      │
│  │    ┌──────────────────────────────────────────────────────┐       │                      │
│  │    │  SSE Stream: thinking → tool_start → result → content│       │                      │
│  │    └──────────────────────────────────────────────────────┘       │                      │
│  └───────────────────────────────────────────────────────────────────┘                      │
│         │  ▲  ▲  ▲  ▲  ▲                                                                   │
│         │  │  │  │  │  │  localStorage: chat_history_v1, platform-theme, active_jobs        │
│         │  │  │  │  │  │  ES Modules: api.js → utils.js → eval.js/crm.js/mail.js/kb.js/chat.js│
└─────────┼──┼──┼──┼──┼──┼───────────────────────────────────────────────────────────────────┘
          │  │  │  │  │  │
          ▼  │  │  │  │  │     HTTP/SSE (localhost:8000)
     ┌───────┴──┴──┴──┴──┴──────────┐
     │                              │
     ▼                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                              FastAPI Application (Uvicorn)                                    │
│                                                                                              │
│  ┌──────────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────────┐   │
│  │ src/core/app.py          │  │ src/core/config.py       │  │ src/core/auth.py         │   │
│  │  • create_app()          │  │  • PlatformConfig        │  │  • HTTP Basic (可选)     │   │
│  │  • discover_agents()     │  │  • .env → 环境变量       │  │  • Depends(require_auth) │   │
│  │  • mount StaticFiles     │  │  • 单例缓存              │  │                          │   │
│  │  • Jinja2 Environment    │  └──────────────────────────┘  └──────────────────────────┘   │
│  └──────────────────────────┘                                                                │
│                                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                            Agent 自动发现机制 (src/agents/)                             │  │
│  │  扫描 src/agents/*/manifest.py → importlib → register() → AgentManifest               │  │
│  └───────────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Agent 1    │  │   Agent 2    │  │   Agent 3    │  │   Agent 4    │  │   Agent 5    │  │
│  │ customer_eval│  │     crm      │  │ inquiry_mail │  │knowledge_base│  │  chat_agent  │  │
│  │              │  │              │  │              │  │              │  │              │  │
│  │ POST /api/   │  │ GET /api/    │  │ POST /api/   │  │ GET /api/kb/ │  │ POST /api/   │  │
│  │   jobs       │  │   customers  │  │   generate   │  │   collections│  │   chat/stream│  │
│  │ GET /api/    │  │ GET /api/    │  │ POST /api/   │  │   search     │  │   chat/send  │  │
│  │   jobs/{id}  │  │   smart-     │  │   send       │  │   preview    │  │   chat/      │  │
│  │   download   │  │   search     │  │   send-      │  │   import     │  │   confirm    │  │
│  │              │  │              │  │   status     │  │              │  │              │  │
│  │ routes.py    │  │ routes.py    │  │ routes.py    │  │ routes.py    │  │ routes.py    │  │
│  │ tasks.py     │  │              │  │ tasks.py     │  │ tasks.py     │  │ agent_loop.py│  │
│  │ config.py    │  │              │  │ config.py    │  │ config.py    │  │ tools.py     │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                 │                 │                 │           │
└─────────┼─────────────────┼─────────────────┼─────────────────┼─────────────────┼───────────┘
          │                 │                 │                 │                 │
          ▼                 ▼                 ▼                 ▼                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    工具层 (tools/)                                           │
│                                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │ deepseek_client │  │ email_generator │  │   embedding     │  │   doc_parser    │         │
│  │  • chat_json()  │  │  • build_messages│  │  • get_embeddings│  │  • parse_file() │         │
│  │  • make_client()│  │  • knowledge_ctx │  │  • 384-dim vec  │  │  • split_parents │         │
│  │  • 重试3次      │  │  • 多语言自适应  │  │  • fastembed     │  │  • parent→child  │         │
│  │  • 指数退避     │  │  • Deal分级策略  │  │  • ONNX Runtime  │  │  • OCR+AI清理    │         │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘         │
│           │                    │                    │                    │                   │
│  ┌────────┴────────┐  ┌────────┴────────┐  ┌────────┴────────┐  ┌────────┴────────┐         │
│  │  vector_store   │  │  gmail_sender   │  │   import_kb     │  │ eval_retrieval  │         │
│  │  • search()     │  │  • OAuth 2.0    │  │  • CLI批量导入   │  │  • Precision    │         │
│  │  • search_multi │  │  • Token refresh│  │  • tqdm进度条    │  │  • Recall       │         │
│  │  • add_documents│  │  • MIME multipart│  │  • 增量模式      │  │  • MRR/NDCG     │         │
│  │  • BM25+RRF融合 │  │  • HTTPS 443    │  │  • 预览分块      │  │  • 消融实验     │         │
│  │  • DeepSeek重排 │  └─────────────────┘  └─────────────────┘  └─────────────────┘         │
│  │  • Query改写    │                                                                         │
│  └─────────────────┘  ┌─────────────────┐                                                     │
│                        │  email_sender   │                                                     │
│                        │  • SMTP TLS/SSL │                                                     │
│                        │  • STARTTLS降级 │                                                     │
│                        │  • 邮件头规范   │                                                     │
│                        └─────────────────┘                                                     │
│                                                                                              │
│                        ┌─────────────────────┐                                                │
│                        │ country_timezone    │                                                │
│                        │  • 60+国家→IANA TZ  │                                                │
│                        │  • DST自动适配      │                                                │
│                        │  • 工作时间过滤     │                                                │
│                        └─────────────────────┘                                                │
└────────────────────────────────────────────────────────────────────────────────────────────┘
          │                 │                 │                 │                 │
          ▼                 ▼                 ▼                 ▼                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    存储层                                                    │
│                                                                                              │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐               │
│  │   SQLite (WAL)       │  │   ChromaDB (SQLite)  │  │   Redis (RQ)         │               │
│  │                      │  │                      │  │                      │               │
│  │  customer (35列)     │  │  kb_products         │  │  Queue:              │               │
│  │  evaluation_batch    │  │  kb_company_docs     │  │   customer_eval:     │               │
│  │  salesperson         │  │  kb_procurement      │  │     default          │               │
│  │  daily_send_log      │  │  *_parents (父文档)  │  │   inquiry_mail:      │               │
│  │                      │  │                      │  │     default          │               │
│  │  var/platform/       │  │  var/knowledge_base/ │  │     send             │               │
│  │    platform.db       │  │    chroma_db/        │  │                      │               │
│  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘               │
│                                                                                              │
│  ┌──────────────────────┐  ┌──────────────────────┐                                         │
│  │  文件缓存            │  │  会话与配置          │                                         │
│  │  cache/ (*.txt)      │  │  localStorage (前端)│                                         │
│  │  job_outputs/ (*.json)│  │  .env (后端)        │                                         │
│  │  gmail_token.json    │  │  settings.json       │                                         │
│  └──────────────────────┘  └──────────────────────┘                                         │
└────────────────────────────────────────────────────────────────────────────────────────────┘
          │                                   │
          ▼                                   ▼
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 外部服务                                                      │
│                                                                                              │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐               │
│  │ DeepSeek API         │  │ Gmail API            │  │ SMTP Server          │               │
│  │ api.deepseek.com     │  │ googleapis.com:443   │  │ smtp.gmail.com:587   │               │
│  │  • Chat Completions  │  │  • OAuth 2.0         │  │  • TLS/STARTTLS      │               │
│  │  • reasoning_content │  │  • gmail.send scope  │  │  • App Password      │               │
│  │  • Function Calling  │  │  • Token auto-refresh│  │                      │               │
│  │  • JSON mode         │  │                      │  │                      │               │
│  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘               │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. RAG 检索管道 — 详细流程

```
                           用户输入: "我们的数字万用表有什么认证？"
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 1: Query 改写 (DeepSeek)                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Prompt: "将以下用户问题改写为2-3条适合向量检索的关键词查询"   │ │
│  │  输入: "我们的数字万用表有什么认证？"                         │ │
│  │  输出: [                                                    │ │
│  │    "数字万用表 认证标准 CE EMC LVD",                         │ │
│  │    "digital multimeter certification",                      │ │
│  │    "万用表 产品认证 质量体系"                                 │ │
│  │  ]                                                          │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              Query 1 (zh)      Query 2 (en)      Query 3 (zh)
                    │                 │                 │
                    └─────────────────┼─────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 2: 混合检索 (Hybrid Search) — 两路并行                       │
│                                                                   │
│  ┌─────────────────────────┐    ┌─────────────────────────┐      │
│  │  BM25 关键词检索         │    │  语义向量检索            │      │
│  │  (rank_bm25)            │    │  (ChromaDB + fastembed)  │      │
│  │                         │    │                         │      │
│  │  1. 分词: [数字, 万用表, │    │  1. embed(query)        │      │
│  │     认证, CE, EMC, LVD] │    │     → 384-dim vector   │      │
│  │  2. BM25Okapi 计算得分  │    │  2. collection.query()  │      │
│  │  3. 取 top-10           │    │     → cosine similarity │      │
│  │                         │    │  3. 取 top-10           │      │
│  └───────────┬─────────────┘    └───────────┬─────────────┘      │
│              │                              │                     │
│              └──────────────┬───────────────┘                     │
│                             ▼                                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 3: RRF 融合 (Reciprocal Rank Fusion, k=60)                  │
│                                                                   │
│  对每个文档 d:                                                    │
│    RRF(d) = Σ 1/(k + rank_i(d))                                  │
│                                                                   │
│  BM25 rank    Vector rank    RRF Score                           │
│  ─────────    ───────────    ─────────                            │
│  文档A: 1      文档A: 2      = 1/(60+1)+1/(60+2) = 0.0325  ← 最高 │
│  文档B: 3      文档B: 1      = 1/(60+3)+1/(60+1) = 0.0323         │
│  文档C: 2      文档C: 5      = 1/(60+2)+1/(60+5) = 0.0315         │
│  ...                                                              │
│  合并去重 → 按 RRF 降序 → 取 top-15                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 4: DeepSeek 重排序 (Re-rank)                                │
│                                                                   │
│  Prompt: "对以下 15 个文档片段与查询的相关性打分 (0-1)"            │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Query: "数字万用表 认证标准"                                 │ │
│  │                                                              │ │
│  │  候选1: "正原电子全线产品通过欧盟EMC(电磁兼容)、LVD(低电压..." │ │
│  │    → 相关性: 0.92 ✓ (直接匹配认证信息)                        │ │
│  │                                                              │ │
│  │  候选2: "数字万用表具备数据保持、背光显示、自动关机..."        │ │
│  │    → 相关性: 0.45 ✗ (功能描述，非认证)                        │ │
│  │                                                              │ │
│  │  候选3: "DT830L digital multimeter specifications..."        │ │
│  │    → 相关性: 0.38 ✗ (规格说明，非认证)                        │ │
│  │  ...                                                         │ │
│  └─────────────────────────────────────────────────────────────┘ │
│  按 DeepSeek 评分降序 → 取 top-5                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 5: 父文档映射 (Parent Retrieval)                            │
│                                                                   │
│  子文档 chunk_id → parent_id → {collection}_parents 查询          │
│                                                                   │
│  返回:                                                            │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ {                                                            │ │
│  │   "chunk": "温州正原电子有限公司...全线产品通过欧盟EMC...     │ │
│  │            LVD认证，符合全球环保与安全规范...",               │ │
│  │   "source_doc": "公司介绍.doc",                               │ │
│  │   "rerank_score": 0.92,                                      │ │
│  │   "metadata": {"section": "资质认证与品质保障"}               │ │
│  │ }                                                            │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                     返回给 LLM / Agent
```

---

## 4. 智能客服 Agent 协同流程图

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                          用户消息: "帮 rudolph tietzsch 生成询盘邮件"                        │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  run_agent_stream() — SSE 事件流                                                           │
│                                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ Round 0: 上下文准备                                                                    │ │
│  │  ├── localStorage 加载 chat_history_v1                                                │ │
│  │  ├── 历史 >6000 字? → _summarize_history() → DeepSeek 摘要 (100字)                     │ │
│  │  ├── System Prompt: "你是外贸客户平台的智能助手小贸..."                                  │ │
│  │  └── Messages: [system, ...recent_6, user]                                            │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ Round 1: DeepSeek API 调用 (with tools)                                                │ │
│  │                                                                                        │ │
│  │  → reasoning_content: "用户要给 Rudolph Tietzsch 生成邮件，我需要先搜索客户..."          │ │
│  │    yield SSE: event: thinking                                                          │ │
│  │                                                                                        │ │
│  │  → tool_calls: [search_customers(query="rudolph tietzsch")]                            │ │
│  │    yield SSE: event: tool_start {name: "search_customers"}                             │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ 执行工具: execute_search_customers()                                                    │ │
│  │   → SQLite: SELECT * FROM customer WHERE company_name LIKE '%rudolph tietzsch%'       │ │
│  │   → 返回 5 条匹配: [id=11 einkauf@tietzsch.de, id=73 mt@tietzsch.de, ...]              │ │
│  │   yield SSE: event: tool_result {found: true, count: 5}                                │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ Round 2: DeepSeek API 调用                                                             │ │
│  │   → reasoning: "找到5条记录，id=11是采购邮箱且评分最高3.35，先获取详情..."               │ │
│  │   → tool_calls: [get_customer_detail(11)]                                              │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ 执行工具: execute_get_customer_detail(11)                                               │ │
│  │   → SQLite: SELECT c.*, s.name FROM customer c LEFT JOIN salesperson s ...            │ │
│  │   → 返回完整客户详情 (35列)                                                            │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ Round 3: DeepSeek API 调用                                                             │ │
│  │   → tool_calls: [search_knowledge_base(query="电压测试仪 产品"),                        │ │
│  │                  generate_inquiry_email(11, "auto")]                                   │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                          ┌───────────────┴───────────────┐                                 │
│                          ▼                               ▼                                 │
│  ┌──────────────────────────────┐  ┌──────────────────────────────┐                       │
│  │ search_knowledge_base()      │  │ generate_inquiry_email()      │                       │
│  │   → vector_store.search_multi│  │   → 检索知识库 (产品信息)     │                       │
│  │     (["产品信息","公司文档"]) │  │   → email_generator()        │                       │
│  │   → 返回3条相关产品文档      │  │   → 生成德文邮件草稿          │                       │
│  └──────────────────────────────┘  │   → UPDATE customer SET      │                       │
│                                    │     email_status='draft'     │                       │
│                                    │   → needs_confirm: true      │                       │
│                                    └──────────────────────────────┘                       │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ 返回确认卡片                                                                            │ │
│  │   yield SSE: event: content {text: "已为 Rudolph Tietzsch... 生成邮件草稿..."}          │ │
│  │   yield SSE: event: done {confirm: {type:"send_email", ...}}                           │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ 前端渲染确认卡片 → 用户点击 ✓确认                                                       │ │
│  │   → POST /chat/api/chat/confirm                                                       │ │
│  │   → execute_confirmed_action()                                                         │ │
│  │   → UPDATE customer SET email_status='confirmed' WHERE id=11                           │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. 客户评估流水线 — 详细流程

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  POST /customer-eval/api/jobs                                                              │
│  Content-Type: multipart/form-data                                                         │
│  Body: file=demo_input.xlsx                                                                │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  routes.py: create_job()                                                                   │
│  ├── 验证扩展名 (.xlsx / .csv / .xls)                                                      │
│  ├── 生成 UUID job_id → 创建 var/platform/jobs/{job_id}/                                   │
│  ├── 流式保存文件 → input.xlsx                                                             │
│  ├── pandas 读取 → 验证行数 ≤ max_rows (500)                                               │
│  ├── normalize_column_map() → 中英文列名映射 (50+ 别名)                                    │
│  ├── merge_extra_columns_into_notes() → 未知列追加到备注                                   │
│  ├── insert evaluation_batch (status='queued')                                             │
│  ├── enqueue run_eval_job (timeout=2700s)                                                  │
│  └── return {"job_id": "uuid", "rq_job_id": "rq:job:uuid", "status": "queued"}            │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  RQ Worker: tasks.run_eval_job()                                                           │
│                                                                                            │
│  Phase 0: 初始化                                                                           │
│  ├── load_dotenv() (Worker 独立进程)                                                       │
│  ├── update_batch_status("running")                                                        │
│  ├── 加载 product_catalog (output/catalog.json) → compact 到 24000 字                      │
│  └── 加载 product_kb (如果存在) → compact 到 4000 字                                       │
│                                                                                            │
│  Phase 1: 逐行评估                                                                         │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ For each row in Excel:                                                                 │ │
│  │                                                                                        │ │
│  │   Step 1: 抓取网站 (fetch_pages_for_website_field)                                     │ │
│  │   ├── expand_urls(base, ["", "/about", "/products", "/contact"])                      │ │
│  │   ├── httpx.Client GET (timeout=15s)                                                   │ │
│  │   ├── trafilatura.extract() → 纯文本                                                   │ │
│  │   ├── 失败回退: _strip_html_fallback() → re.sub(r'<[^>]+>', '', html)                 │ │
│  │   ├── 磁盘缓存: cache/{sha256[:24]}.txt                                                │ │
│  │   └── 重试 3 次 (指数退避: 0.4s, 0.8s, 1.2s)                                          │ │
│  │                                                                                        │ │
│  │   Step 2: 合并证据 (merge_scrape_and_paste)                                            │ │
│  │   ├── 抓取文本 + evidence_paste 列                                                      │ │
│  │   └── 标注来源: [来自网页抓取] / [来自人工粘贴]                                          │ │
│  │                                                                                        │ │
│  │   Step 3: LLM 评估 (run_llm_eval)                                                      │ │
│  │   ├── 构建 system prompt (评估标准 + 5级评分细则)                                       │ │
│  │   ├── 构建 user prompt (公司信息 + 证据 + catalog + kb)                                 │ │
│  │   ├── deepseek_client.chat_json(messages, response_format="json_object")               │ │
│  │   ├── jsonschema 验证 (eval_result.schema.json)                                        │ │
│  │   └── 字段: product_fit{score,reasons} capability{score,signals}                       │ │
│  │             reputation_risk{facts,concerns,sources} reputation_safety_score             │ │
│  │             buyer_seller_role deal_recommendation next_action confidence data_quality   │ │
│  │                                                                                        │ │
│  │   Step 4: 后处理                                                                       │ │
│  │   ├── cap_model_data_quality() → 无抓取+短粘贴 → data_quality ≤ medium                 │ │
│  │   ├── overall_score_computed = 0.45×product + 0.25×capability + 0.30×reputation       │ │
│  │   ├── manual_review_flag: score≥4.0 OR reputation≤2 OR keywords → "YES"               │ │
│  │   └── 中文显示转换: deal_recommendation → {high_intent:"高意向", watch:"观察"}           │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                            │
│  Phase 2: 写回                                                                             │
│  ├── _save_to_database(): BEGIN TRANSACTION → DELETE old batch → INSERT new → COMMIT       │
│  ├── write_result_xlsx(): openpyxl 输出 (复核行标红)                                        │
│  └── update_batch_status("finished")                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  前端轮询 GET /api/jobs/{job_id}                                                           │
│  ├── RQ Job.meta["progress"] → 进度百分比                                                  │
│  ├── RQ Job.get_status() → queued/started/finished/failed                                 │
│  └── finished → 显示下载按钮 → GET /api/jobs/{job_id}/download                             │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. 询盘邮件 — RAG 增强生成流程

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  用户在 inquiry_mail 页面选择客户 → POST /inquiry-mail/api/generate                         │
│  Body: {"customer_ids": [11, 15, 73], "language": "auto"}                                  │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  routes.py: generate_emails()                                                              │
│  ├── 验证客户邮箱非空                                                                       │
│  ├── enqueue generate_emails_job (timeout=1800s)                                           │
│  └── return {"job_id": "uuid", "status": "queued"}                                         │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  RQ Worker: tasks.generate_emails_job()                                                    │
│                                                                                            │
│  For each customer:                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ Step 1: 检索知识库 (新增 RAG)                                                          │ │
│  │ ├── query = f"{customer.target_products} {customer.company_name}"                      │ │
│  │ ├── vector_store.search_multi(                                                         │ │
│  │ │     ["产品信息", "公司文档"], query, top_k=3, mode="hybrid_rerank"                    │ │
│  │ │   )                                                                                  │ │
│  │ └── knowledge_context = "\n".join(r["chunk"][:400] for r in results)                   │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ Step 2: 生成邮件                                                                       │ │
│  │ ├── email_generator.build_email_messages(                                              │ │
│  │ │     company_name, contact_name, country_region,                                      │ │
│  │ │     target_products, product_fit_reasons, capability_signals,                        │ │
│  │ │     deal_recommendation, next_action, language=language,                             │ │
│  │ │     knowledge_context=knowledge_context  ← 注入知识库内容                             │ │
│  │ │   )                                                                                  │ │
│  │ ├── deepseek_client.chat_json(messages, response_format="json_object")                │ │
│  │ └── 返回: {subject, body_text, body_html, language, skip?, skip_reason}               │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ Step 3: 保存                                                                            │ │
│  │ ├── db.execute("UPDATE customer SET email_subject=?, email_body=?,                     │ │
│  │ │              email_status='draft' WHERE id=?")                                       │ │
│  │ └── 写入 emails.json (job 输出)                                                        │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  发送流程: POST /inquiry-mail/api/send                                                     │
│                                                                                            │
│  前置检查:                                                                                  │
│  ├── Gmail API: var/gmail_token.json 存在? → 使用 Gmail API (HTTPS 443)                   │
│  │   └── Token 过期? → refresh_token() 自动续期                                            │
│  ├── SMTP: SMTP_HOST 已配置? → 使用 SMTP (TLS 587)                                        │
│  ├── 今日配额: SELECT COUNT(*) FROM daily_send_log WHERE sent_date=today                   │
│  │   └── ≥ daily_limit (50)? → 拒绝，提示明日再发                                          │
│  ├── 时区检查: country_timezone.get_utc_offset(country)                                    │
│  │   └── 客户当地时间 9-17 点? → 允许 / 否则 → 延迟或提示                                  │
│  └── send_delay: 每封间隔 45 秒                                                             │
│                                                                                            │
│  发送:                                                                                      │
│  ├── MIME multipart (text/plain + text/html)                                               │
│  ├── Headers: Message-ID, Date, Reply-To, List-Unsubscribe                                 │
│  ├── 追踪像素 (可选): <img src="https://域名/track/pixel/{id}.png" />                       │
│  ├── Gmail API: service.users().messages().send(userId="me", body=raw).execute()          │
│  ├── SMTP: smtplib.SMTP(host, port) → ehlo() → starttls() → login() → send_message()     │
│  └── 写回: UPDATE customer SET email_status='sent', email_sent_at=now WHERE id=?          │
│           INSERT INTO daily_send_log (sent_date, recipient_email, customer_id, status)     │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. 文档解析与入库流程

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  入口: CLI (tools/import_kb.py) 或 Web (POST /knowledge-base/api/kb/import)                 │
│                                                                                            │
│  python tools/import_kb.py E:\产品图\ --collection 产品信息 --incremental                    │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  文件扫描 → 按扩展名路由                                                                    │
│                                                                                            │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐               │
│  │  .pdf    │   │  .jpg    │   │  .png    │   │  .xlsx   │   │  .docx   │               │
│  │          │   │  .jpeg   │   │  .bmp    │   │          │   │          │               │
│  │ pdfplumber│  │ pytesseract│ │ pytesseract│ │ openpyxl │   │python-docx│               │
│  │ 逐页提取 │   │   + OCR   │   │   + OCR   │   │read_only │   │ 段落读取 │               │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘               │
│       │               │               │               │               │                    │
│       │  (如果有 text │  (OCR 提取文字)│  (OCR 提取文字)│  (结构化序列化)│                   │
│       │   layer)      │               │               │               │                    │
│       └───────────────┴───────────────┴───────────────┴───────────────┘                    │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ OCR 清理 (DeepSeek) — 仅图片/XLSX                                                      │ │
│  │ ├── Prompt: "整理以下OCR碎片文字，修正错别字，补全残缺句子"                              │ │
│  │ ├── 输入: 碎片化 OCR 原始文字                                                           │ │
│  │ └── 输出: 通顺连贯的文档文本                                                            │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ split_into_parents(text, source_type)                                                  │ │
│  │ ├── PDF: 按章节标题/一级标题分割                                                        │ │
│  │ ├── TXT/MD: 按空行 + 标题分割                                                           │ │
│  │ ├── OCR 结果: 按自然段落分割                                                            │ │
│  │ ├── XLSX: 按工作表(sheet)分割                                                           │ │
│  │ ├── 合并短段 (<150字) → 避免信息碎片                                                    │ │
│  │ └── 每段 1000-1500 字，保持语义完整                                                      │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ 质量过滤 (_is_low_quality)                                                              │ │
│  │ ├── "请用 Word 打开查看" → 过滤                                                         │ │
│  │ ├── "旧版 Word 文档" → 过滤                                                             │ │
│  │ ├── 长度 < 30 字符 → 过滤                                                               │ │
│  │ └── 仅含文件路径/文件名 → 过滤                                                          │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ parent_to_children(parent_text, child_size=200, overlap=50)                            │ │
│  │ ├── 滑动窗口: 窗口 200 字，步长 150 字 (overlap 50)                                     │ │
│  │ ├── 子文档用于检索 (小粒度精准匹配)                                                     │ │
│  │ └── 父文档返回给 LLM (大粒度完整上下文)                                                  │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                                 │
│                                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ 入库: vector_store.add_documents(collection, documents)                                │ │
│  │ ├── embed child chunks → ChromaDB {collection}                                         │ │
│  │ ├── store parent chunks → ChromaDB {collection}_parents                                │ │
│  │ └── rebuild BM25 index → _rebuild_bm25(collection)                                     │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 8. 分阶段交付

| 阶段 | 内容 | 核心产出 |
|------|------|----------|
| P0 | 基础框架：App 工厂、配置、DB、Redis、认证、Agent 发现 | `src/core/` |
| P1 | Agent 1+2：客户评估 + CRM | `customer_eval/`, `crm/` |
| P2 | Agent 3：询盘邮件（生成→发送→追踪）、时区感知 | `inquiry_mail/` |
| P3 | 平台增强：全局任务追踪、高级筛选、Text-to-SQL 搜索 | 全局 |
| P4 | RAG 基础设施：嵌入、向量存储、文档解析、CLI 导入 | `tools/embedding.py`, `vector_store.py`, `doc_parser.py` |
| P5 | Agent 4：知识库管理界面 + 检索评测 | `knowledge_base/` |
| P6 | Agent 5：智能客服挂件、Function Calling、流式+思维链 | `chat_agent/` |
| P7 | 存量集成 RAG + 全链路测试 + 文档重写 | 本次提交 |

---

## 9. 新增依赖

```
# AI / LLM
openai>=1.0.0           # DeepSeek 兼容客户端

# 向量检索
chromadb>=1.5.0         # 向量数据库
fastembed>=0.5.0        # 嵌入 (ONNX, 无 PyTorch)
rank-bm25>=0.2.0        # BM25 关键词检索

# 文档处理
pdfplumber>=0.10.0      # PDF 解析
pytesseract>=0.3.0      # OCR
Pillow>=10.0.0          # 图片处理
python-docx>=1.0.0      # DOCX 读取
openpyxl>=3.1.0         # XLSX 读取

# Web / 任务
fastapi>=0.110.0        # Web 框架
uvicorn>=0.27.0         # ASGI 服务器
jinja2>=3.1.0           # 模板引擎
redis>=5.0.0            # Redis 客户端
rq>=1.16.0              # 任务队列

# 工具
httpx>=0.27.0           # HTTP 客户端
pandas>=2.0.0           # 数据处理
python-dotenv>=1.0.0    # 环境变量
tqdm>=4.60.0            # 进度条
```

**系统依赖：** Tesseract-OCR Windows 版 + `chi_sim` 中文语言包

---

## 10. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0 | 2026-05-07 | 首版：CLI 流水线技术栈 |
| 2.0 | 2026-05-19 | 重构为平台级：5 Agent + RAG + 实时客服 + 精细化架构图 |
