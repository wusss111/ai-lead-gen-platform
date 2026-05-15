# PRD：外贸客户初筛评估流水线（CLI / Excel）

**文档版本**：1.0  
**状态**：已定稿（实现以本文与 [TECH_ROADMAP.md](./TECH_ROADMAP.md) 为准）  
**产品定位**：B2B 外贸潜客**初筛助手**——基于公开信息与人工粘贴证据的结构化评分与跟进建议；**不等同**征信、法务或第三方背调报告。

---

## 1. 背景与问题

业务侧对潜客重复进行：看官网、判断与我方产品线是否匹配、粗看公开实力与疑点。人工成本高、格式不统一、难批量排序。

**目标**：将上述过程固化为**可批量执行、输入输出均为 Excel、结果可追溯**的流水线，便于按分数与标志筛选跟进优先级。

---

## 2. 用户与场景

| 角色 | 诉求 |
|------|------|
| 外贸业务 / 运营 | 导入客户表，得到同结构结果表，按列筛选、分配跟进 |
| 管理者（可选） | 抽查依据列与缓存，核对高分或标红行 |

**典型场景**

1. 首次跑批：`company_name` + `website` 等 → 自动抓取 → DeepSeek 出结构化结果。  
2. 抓取失败或证据不足：在 **`evidence_paste`** 粘贴摘录后**重跑**该行或整表。  
3. 产品目录变更：重新生成 `catalog.json`（及版本号），再跑评估以保证与我方报价一致。

---

## 3. 范围（MVP）

### 3.1 本期必做（In Scope）

- **本地 CLI**：读入 `.xlsx`，写出 `.xlsx`（可指定输出路径）。  
- **输入列**（见第 5 节）：含可选 **`evidence_paste`**。  
- **采集**：对 `website` 尝试常见路径；超时/重试；正文抽取；**本地磁盘缓存**（便于复核）。  
- **证据合并**：抓取正文 +（若存在）`evidence_paste`（标注为人工粘贴）；仅粘贴无抓取时仍允许评估，并在输出中体现数据来源与质量。  
- **大模型**：**DeepSeek**（OpenAI 兼容 API），输出**严格 JSON**（schema 见仓库 `schemas/` 与实现侧校验）。  
- **程序侧**：加权综合分、人工复核标志、Excel 列写入（含可选第二 Sheet 或长 JSON 列策略，见技术路线）。  
- **我方侧**：版本化产品目录（`catalog.json`）+ 版本化 **`product_kb`** 摘要进提示词。

### 3.2 本期不做（Out of Scope）

- Web 上传界面、多租户、登录。  
- **自动**搜索降级（Bing/Google/DuckDuckGo 等）：失败仅 `errors` + 低 `data_quality`，依赖 **`evidence_paste`** 补证。  
- 等同征信/司法尽调的承诺或对外产品表述。

### 3.3 后续可选（Backlog，不在 MVP 验收内）

- FastAPI + 队列、定时任务。  
- 可选「抓取失败则调用商业搜索 API」。  
- Playwright 等对强 JS 站点的增强抓取策略。

---

## 4. 成功指标（验收）

1. 给定符合列模板的输入表（含 10 行样例），CLI 在无密钥错误配置下能**完整跑通**并生成输出表。  
2. 每行输出包含：`product_fit_score`、`capability_score`、`reputation_safety_score`（或等价字段）、`deal_recommendation`、`next_action`、`confidence`、`data_quality`、`fetched_pages`、`errors`、`overall_score_computed`、`manual_review_flag`，以及理由/信号类短文本列（或 JSON 子结构展开）。  
3. **`citations`**（或等价结构）：关键判断带来源 URL 与片段；无来源时允许空 URL 但须降低置信度并在文案中体现。  
4. 抓取失败时：`errors` 非空，`data_quality` 为 `low`（或规则定义的档位）；填入 **`evidence_paste`** 后重跑，模型输入中**必须**包含该粘贴块。  
5. 综合分 **`overall_score_computed`** 由程序按文档约定权重计算，**不由模型单独拍板**为最终对外分（模型可辅助子维度，最终以程序公式为准）。  
6. **`manual_review_flag`**：满足约定规则之一时为「需复核」（实现可用列值 + 条件格式标红）。

---

## 5. 数据需求：Excel 列

### 5.1 输入（建议列名，实现可兼容大小写映射）

| 列名 | 必填 | 说明 |
|------|------|------|
| `company_name` | 是 | 公司名 |
| `website` | 否 | 官网 URL；多个用分号或换行分隔 |
| `country_region` | 否 | 国家/地区 |
| `target_products` | 否 | 希望对齐的产品线或关键词 |
| `priority` | 否 | 高/中/低；程序可原样回写 |
| `notes` | 否 | 内部备注 |
| `evidence_paste` | 否 | 人工粘贴摘录；抓取弱/失败时用于补证与重跑 |

### 5.2 输出（在输入列基础上追加，名称以实现 `schemas/excel_io.json` 为准）

- 契合度：分数 + 理由（短句或可拼接）  
- 实力：`capability_score` + `capability_signals`  
- 信誉：`reputation_*` 展开列 + `reputation_safety_score`  
- 跟进：`deal_recommendation`、`next_action`  
- 质量与调试：`confidence`、`data_quality`、`fetched_pages`、`search_fallback_used`（MVP 固定为 `no` 或等价）、`errors`  
- 决策支持：`overall_score_computed`、`manual_review_flag`  
- 审计：`eval_json`（整行 JSON 字符串）或第二 Sheet `Detail` 存全文  

**主表原则**：列宽可控、便于筛选；长文本优先第二 Sheet 或 `eval_json`。

---

## 6. 业务规则摘要

- **加权公式（默认，可配置）**：  
  `overall_score_computed = w_pf * product_fit_score + w_cap * capability_score + w_rep * reputation_safety_score`  
  其中 `w_pf=0.45`，`w_cap=0.25`，`w_rep=0.30`；各子分均为 1–5。  
- **人工复核标志（默认规则，可配置）**：例如  
  - `overall_score_computed >= 4.0`，或  
  - `reputation_safety_score <= 2`，或  
  - `reputation_concerns` 命中关键词列表（中英文可配置）  
  满足其一则 `manual_review_flag = YES`（或等价枚举）。  
- **合规**：遵守 `robots.txt` 与合理请求频率；最小化存储；不向用户承诺背调法律效力。

---

## 7. 非功能需求

- **可维护性**：配置（权重、阈值、路径）与代码分离（如 `config` 或环境变量）。  
- **可观测**：每行 `errors` 可读；日志包含行号与公司名（避免打印完整 API 密钥）。  
- **失败容忍**：单行 LLM 或抓取失败不应阻塞整表（可配置「遇错即停」为可选）。

---

## 8. 文档关系

- 技术选型、目录结构、迭代顺序见 **[TECH_ROADMAP.md](./TECH_ROADMAP.md)**。  
- Cursor 侧总体规划见 `.cursor/plans` 内外贸客户评估 Agent 计划；**实现以本 PRD 与技术路线为优先**。  
- 在 Cursor 中后续改本仓库流水线时，可 **@ 引用技能** `trade-customer-excel-pipeline`（项目技能路径：`.cursor/skills/trade-customer-excel-pipeline/SKILL.md`），以便自动带上文约束与检查清单。

---

## 9. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0 | 2026-05-07 | 首版：CLI、DeepSeek、`evidence_paste`、无自动搜索降级 |
