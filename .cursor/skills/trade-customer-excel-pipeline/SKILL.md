---
name: trade-customer-excel-pipeline
description: >-
  Implements and maintains the B2B foreign-trade prospect screening CLI: Excel in/out,
  httpx fetch with cache, DeepSeek JSON evaluation, product catalog plus versioned
  product_kb, evidence_paste merge, programmatic scoring and manual_review_flag. Use
  when the user works on d:/creat_agent, customer eval pipeline, PRD/TECH_ROADMAP,
  eval_result schema, pipeline modules, or mentions evidence_paste / DeepSeek / catalog.json.
disable-model-invocation: true
---

# 外贸客户初筛流水线（本仓库）

## 权威文档（先读后改）

1. [docs/PRD.md](docs/PRD.md) — 范围、验收、列约定、业务规则。  
2. [docs/TECH_ROADMAP.md](docs/TECH_ROADMAP.md) — 技术栈、目标目录、阶段、环境变量。

代码与提示词变更须满足 PRD 验收项；目录与模块职责以技术路线为准。

## 已定产品与技术约束

- **形态**：本地 **CLI**，读 `.xlsx` 写 `.xlsx`；MVP **无** Web 服务。  
- **LLM**：**DeepSeek**（`tools/deepseek_client.py`，`DEEPSEEK_API_KEY`）。  
- **抓取失败**：**不**做自动搜索 API 降级；写 `errors`，`data_quality` 偏低；用户用 **`evidence_paste`** 补证后重跑。  
- **证据**：组装顺序为抓取正文 + 明确分隔 + `evidence_paste`（标注人工粘贴）；仅粘贴时仍可评估并标明来源。  
- **综合分**：`overall_score_computed` 由程序加权（默认 0.45 / 0.25 / 0.30），不以模型输出为最终对外分。  
- **复核**：`manual_review_flag` 按 PRD 默认规则（高分、低信誉安全分、关键词等）。

## 实现检查清单（Agent 改代码时自测）

- [ ] 输入列含 `company_name`；可选 `website`、`evidence_paste` 等与 PRD 一致。  
- [ ] 输出列含 PRD 所列结果字段；`search_fallback_used` 在 MVP 为 `no` 或等价。  
- [ ] JSON 与 `schemas/eval_result.schema.json`（及 `citations`）对齐；解析失败有重试或降级并写 `errors`。  
- [ ] 读取 `output/catalog.json`（或配置路径）与 `product_kb/v1/kb.json`，提示词带 `catalog_version`。  
- [ ] 缓存路径在 `cache/`（已 gitignore），不提交密钥与大批量缓存。

## 与现有脚本的关系

- `tools/build_product_catalog.py`：生成/更新 `output/catalog.json`。  
- `tools/eval_company_fit.py`：单行/调试；批量逻辑应收敛到 `pipeline/` 或单一 CLI，避免两套 prompt 漂移。

## 术语

统一使用：「初筛」、**`evidence_paste`**、`data_quality`、`manual_review_flag`、`overall_score_computed`、`citations`。

## 延伸阅读

- 详细 API 与阶段拆分见 [docs/TECH_ROADMAP.md](docs/TECH_ROADMAP.md) 第 2–5 节。
