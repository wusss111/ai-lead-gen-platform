# 技术栈与实现路线

**文档版本**：2.0
**配套**：[PRD.md](./PRD.md)

---

## 1. 技术栈

### 1.1 核心框架

| 层级 | 选型 | 说明 |
|------|------|------|
| Web 框架 | FastAPI (Python) | 异步支持，自动 API 文档 |
| ASGI 服务器 | Uvicorn | 高性能，支持热重载 |
| 任务队列 | Redis + RQ | Windows 兼容 (SimpleWorker)，无 Celery 依赖 |
| 数据库 | SQLite (WAL 模式) | 轻量，无需独立服务，线程本地连接 |
| 模板引擎 | Jinja2 (原生) | 不用 Starlette 封装（避免缓存 bug） |
| 前端 | Vanilla JS (ES Modules) | 零构建步骤，浏览器原生支持 |

### 1.2 AI / LLM

| 组件 | 选型 | 说明 |
|------|------|------|
| 大模型 | DeepSeek V4 (OpenAI 兼容) | flash 日常推理，pro 复杂任务 |
| 嵌入模型 | paraphrase-multilingual-MiniLM-L12-v2 | fastembed + ONNX Runtime，384 维，CPU 推理 |
| 向量数据库 | ChromaDB (SQLite 后端) | 轻量，与 SQLite 一致的文件存储模式 |

### 1.3 检索系统

| 优化 | 技术 | 效果 |
|------|------|------|
| Query 改写 | DeepSeek 改写为多条变体 | 口语→关键词，跨语言适配 |
| 混合检索 | BM25 (rank_bm25) + 语义检索 (ChromaDB) | 精确型号匹配 + 语义理解 |
| 融合排序 | RRF (Reciprocal Rank Fusion, k=60) | 合并两路检索结果 |
| 重排序 | DeepSeek 对 top-15 逐条打分 | 排除初检噪音，取 top-5 |
| 父子文档 | 子 200 字检索 → 返回父 1000-1500 字 | 小块精准匹配 + 大块完整上下文 |

### 1.4 文档处理

| 格式 | 工具 | 说明 |
|------|------|------|
| PDF | pdfplumber | 流式逐页，支持文本层提取 |
| 图片 | pytesseract + DeepSeek 清理 | 中文 OCR (chi_sim)，AI 整理碎片文字 |
| XLSX | openpyxl | read_only 模式，JSON 序列化结构化内容 |
| DOCX | python-docx | 段落级读取 |
| TXT/MD | 原生 | UTF-8 直接读取 |

### 1.5 前端

| 层级 | 选型 | 说明 |
|------|------|------|
| CSS 框架 | PicoCSS (定制) | 暗色主题 / 亮色主题 |
| 字体 | Cormorant Garamond + Work Sans + JetBrains Mono | 衬线标题 + 无衬线正文 + 等宽数据 |
| C/S 交互 | SSE (Server-Sent Events) | 流式聊天 + 思维链 |
| 状态管理 | localStorage | 聊天历史、主题偏好、任务追踪 |

### 1.6 部署

| 模式 | 方式 | 适用场景 |
|------|------|----------|
| 本地开发 | `start_all.bat` (Redis + Workers + Uvicorn) | Windows 开发 |
| Docker | `docker compose up -d --build` | Linux 生产 |

---

## 2. 仓库结构

```text
d:\creat_agent\
  docs/
    PRD.md                   # 需求文档
    TECH_ROADMAP.md          # 本文
    ARCHITECTURE.md          # 底层框架与工作流编排
  src/
    core/                    # 框架层：app 工厂、配置、DB、Redis、认证
    agents/                  # Agent 自动发现
      customer_eval/         #   Agent 1: 客户评估
      crm/                   #   Agent 2: 客户资源管理
      inquiry_mail/          #   Agent 3: 询盘邮件
      knowledge_base/        #   Agent 4: 知识库管理
      chat_agent/            #   Agent 5: 智能客服（挂件，无导航）
    templates/               # 共享 Jinja2 模板
    static/                  # 共享静态资源
  tools/                     # 工具层
    deepseek_client.py       #   DeepSeek API 客户端
    embedding.py             #   嵌入服务 (fastembed + ONNX)
    vector_store.py          #   ChromaDB + BM25 + 混合检索
    doc_parser.py            #   文档解析 + 父子分块
    email_generator.py       #   邮件生成
    email_sender.py          #   SMTP 发送
    gmail_sender.py          #   Gmail API 发送
    country_timezone.py      #   时区感知
    import_kb.py             #   CLI 批量入库
    eval_retrieval.py        #   检索精度评测
  var/                       # 运行时数据（gitignore）
  requirements.txt
  CLAUDE.md
```

---

## 3. 分阶段交付

| 阶段 | 内容 | 核心产出 |
|------|------|----------|
| P0 | 基础框架：App 工厂、配置、DB、Redis、认证、Agent 发现 | `src/core/` |
| P1 | Agent 1+2：客户评估（上传→AI→入库）+ CRM（浏览/搜索/筛选） | `customer_eval/`, `crm/` |
| P2 | Agent 3：询盘邮件（生成→预览→发送→追踪）、时区感知 | `inquiry_mail/` |
| P3 | 平台化：全局任务追踪、高级筛选、Text-to-SQL、批量分配 | 全局增强 |
| P4 | 知识库基础设施：嵌入服务、向量存储、文档解析、CLI 导入 | `tools/embedding.py`, `vector_store.py`, `doc_parser.py` |
| P5 | Agent 4：知识库管理界面、检索评测 | `knowledge_base/` |
| P6 | Agent 5：智能客服挂件、Function Calling、流式+思维链 | `chat_agent/` |
| P7 | 存量集成 RAG + 全链路测试 | `email_generator.py`, `inquiry_mail/tasks.py` |

---

## 4. 新增依赖

```
# AI / LLM
openai>=1.0.0           # DeepSeek 兼容客户端

# 向量检索
chromadb>=1.5.0         # 向量数据库
fastembed>=0.5.0        # 轻量嵌入 (ONNX, 无需 PyTorch)
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

## 5. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0 | 2026-05-07 | 首版：CLI 流水线技术栈 |
| 2.0 | 2026-05-19 | 重构为平台级：5 Agent + RAG + 实时客服 |
