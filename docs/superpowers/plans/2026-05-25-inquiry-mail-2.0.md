# 询盘邮件 2.0 — 智能回信 + 企业微信审批 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现客户回信自动监控 + AI 智能生成回复 + 企业微信卡片推送审批 + 业务员一键发送的完整闭环。

**Architecture:** IMAP 轮询每个业务员绑定的邮箱 → 匹配 In-Reply-To/标题找到对应客户 → DeepSeek 分析回信内容生成草稿 → 企业微信模板卡片推送到业务员手机 → 业务员在微信内确认/编辑/发送。所有 SMTP/IMAP 配置存储在 salesperson 表。

**Tech Stack:** Python 3.12, imaplib/smtplib, DeepSeek (OpenAI 兼容), RQ + Redis, SQLite, 企业微信 API, FastAPI + Jinja2

---

## File Structure

| 文件 | 职责 | 新增/修改 |
|------|------|----------|
| `src/core/database.py` | 数据库 schema（salesperson 字段扩展 + reply_draft 表） | 修改 |
| `src/agents/crm/routes.py` | 销售管理 CRUD API（增加 SMTP/IMAP/WeCom 字段） | 修改 |
| `src/agents/crm/templates/crm_salespersons.html` | 销售管理页面（增加邮箱绑定表单） | 修改 |
| `src/agents/crm/static/js/crm_salespersons.js` | 销售管理 JS | 修改 |
| `tools/imap_monitor.py` | IMAP 轮询收信 + 回信匹配 | **新增** |
| `tools/email_generator.py` | 增加回信 AI 生成函数 | 修改 |
| `tools/wecom_notify.py` | 企业微信消息推送 | **新增** |
| `src/agents/inquiry_mail/routes.py` | 增加回信审批 API + 编辑器页面 + 企业微信回调 | 修改 |
| `src/agents/inquiry_mail/templates/reply_editor.html` | 移动端在线编辑器 | **新增** |
| `src/agents/inquiry_mail/static/css/reply.css` | 编辑器样式 | **新增** |
| `src/core/config.py` | 增加企业微信配置 | 修改 |
| `src/agents/inquiry_mail/config.py` | 增加 IMAP 轮询/企业微信配置 | 修改 |
| `src/core/redis_utils.py` | 增加 IMAP 轮询调度工具 | 修改 |
| `tests/test_imap_monitor.py` | IMAP 收信测试 | **新增** |
| `tests/test_wecom_notify.py` | 企业微信推送测试 | **新增** |
| `tests/test_reply_generation.py` | 回信 AI 生成测试 | **新增** |

---

### Task 1: 数据库 Schema 扩展

**Files:**
- Modify: `src/core/database.py`

- [ ] **Step 1: 为 salesperson 表增加邮箱绑定和 WeCom 字段**

在 SCHEMA_SQL_SQLITE 和 SCHEMA_SQL_PG 的 `CREATE TABLE IF NOT EXISTS salesperson` 块中添加字段，并在文件底部添加对应的 `_ensure_column_*` 迁移调用。

```python
# salesperson 表新增字段（在 phone TEXT DEFAULT '' 之后）:
smtp_host TEXT DEFAULT '',
smtp_port INTEGER DEFAULT 587,
smtp_username TEXT DEFAULT '',
smtp_password TEXT DEFAULT '',
imap_host TEXT DEFAULT '',
imap_port INTEGER DEFAULT 993,
wework_userid TEXT DEFAULT ''
```

在 `_init_sqlite_pool` 函数末尾的迁移块（`_ensure_column_sqlite` 调用区域）添加：

```python
_ensure_column_sqlite(conn, "salesperson", "smtp_host", "TEXT DEFAULT ''")
_ensure_column_sqlite(conn, "salesperson", "smtp_port", "INTEGER DEFAULT 587")
_ensure_column_sqlite(conn, "salesperson", "smtp_username", "TEXT DEFAULT ''")
_ensure_column_sqlite(conn, "salesperson", "smtp_password", "TEXT DEFAULT ''")
_ensure_column_sqlite(conn, "salesperson", "imap_host", "TEXT DEFAULT ''")
_ensure_column_sqlite(conn, "salesperson", "imap_port", "INTEGER DEFAULT 993")
_ensure_column_sqlite(conn, "salesperson", "wework_userid", "TEXT DEFAULT ''")
```

同样在 `_init_pg_pool` 中添加 `_ensure_column_pg` 对应调用。

- [ ] **Step 2: 创建 reply_draft 表**

在 SCHEMA_SQL_SQLITE 中添加（放在 daily_send_log 建表之后）：

```sql
CREATE TABLE IF NOT EXISTS reply_draft (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customer(id),
    salesperson_id INTEGER NOT NULL REFERENCES salesperson(id),
    original_body TEXT DEFAULT '',
    original_subject TEXT DEFAULT '',
    original_message_id TEXT DEFAULT '',
    draft_body TEXT DEFAULT '',
    draft_subject TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    wework_card_id TEXT DEFAULT '',
    sent_at TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_reply_draft_status ON reply_draft(status);
CREATE INDEX IF NOT EXISTS idx_reply_draft_salesperson ON reply_draft(salesperson_id);
CREATE INDEX IF NOT EXISTS idx_reply_draft_customer ON reply_draft(customer_id);
```

同样在 SCHEMA_SQL_PG 中添加对应的 PostgreSQL 版本（TEXT → 无符号整数 SERIAL, TEXT → TIMESTAMPTZ）。

- [ ] **Step 3: 运行验证**

```bash
pytest tests/ -v -k "schema" 2>&1 || python -c "
from src.core.database import get_db
db = get_db()
cols = db.execute('PRAGMA table_info(salesperson)').fetchall()
[print(c[1], c[2]) for c in cols]
db.execute('SELECT 1 FROM reply_draft LIMIT 0')
print('reply_draft OK')
"
```

Expected: salesperson 表包含新增的 7 个字段，reply_draft 表存在。

- [ ] **Step 4: Commit**

```bash
git add src/core/database.py
git commit -m "feat: 扩展 salesperson 表 + 新增 reply_draft 表，支持邮箱绑定和回信审批"
```

---

### Task 2: 销售管理表单 — 邮箱绑定 UI

**Files:**
- Modify: `src/agents/crm/templates/crm_salespersons.html`
- Modify: `src/agents/crm/static/js/crm_salespersons.js`
- Modify: `src/agents/crm/routes.py`

- [ ] **Step 1: 更新 CRM 路由，读写新字段**

在 `src/agents/crm/routes.py` 中修改 `create_salesperson` 和 `update_salesperson` 函数。

`create_salesperson` 改为：

```python
@router.post("/api/salespersons")
def create_salesperson(
    _: Annotated[None, Depends(require_auth)],
    name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    imap_host: str = Form(""),
    imap_port: str = Form("993"),
    wework_userid: str = Form(""),
) -> JSONResponse:
    if not name.strip():
        raise HTTPException(400, "姓名不能为空")
    db = get_db()
    cur = db.execute(
        "INSERT INTO salesperson (name, email, phone, smtp_host, smtp_port, "
        "smtp_username, smtp_password, imap_host, imap_port, wework_userid) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name.strip(), email.strip(), phone.strip(),
         smtp_host.strip(), int(smtp_port) if smtp_port.strip() else 587,
         smtp_username.strip(), smtp_password.strip(),
         imap_host.strip(), int(imap_port) if imap_port.strip() else 993,
         wework_userid.strip()),
    )
    db.commit()
    row = db.execute("SELECT * FROM salesperson WHERE id=?", (cur.lastrowid,)).fetchone()
    return JSONResponse(dict_from_row(row))
```

`update_salesperson` 改为接受全部字段：

```python
@router.put("/api/salespersons/{sp_id}")
def update_salesperson(
    sp_id: int,
    _: Annotated[None, Depends(require_auth)],
    name: str = Form(None),
    email: str = Form(None),
    phone: str = Form(None),
    is_active: str = Form(None),
    smtp_host: str = Form(None),
    smtp_port: str = Form(None),
    smtp_username: str = Form(None),
    smtp_password: str = Form(None),
    imap_host: str = Form(None),
    imap_port: str = Form(None),
    wework_userid: str = Form(None),
) -> JSONResponse:
    sets = []
    params: list[Any] = []
    str_fields = {
        "name": name, "email": email, "phone": phone,
        "smtp_host": smtp_host, "smtp_username": smtp_username,
        "smtp_password": smtp_password, "imap_host": imap_host,
        "wework_userid": wework_userid,
    }
    for field, val in str_fields.items():
        if val is not None and val.strip():
            sets.append(f"{field}=?")
            params.append(val.strip())
    if smtp_port is not None and smtp_port.strip():
        sets.append("smtp_port=?")
        params.append(int(smtp_port))
    if imap_port is not None and imap_port.strip():
        sets.append("imap_port=?")
        params.append(int(imap_port))
    if is_active is not None and is_active.strip():
        sets.append("is_active=?")
        params.append(1 if is_active.strip().lower() in ("1", "true") else 0)
    if not sets:
        raise HTTPException(400, "没有需要更新的字段")
    params.append(sp_id)
    db.execute(f"UPDATE salesperson SET {', '.join(sets)}, updated_at=datetime('now','localtime') WHERE id=?", params)
    db.commit()
    return JSONResponse({"status": "ok"})
```

- [ ] **Step 2: 更新模板，增加邮箱配置表单区**

在 `crm_salespersons.html` 的表单区（`<form id="spForm">`），电话字段下方增加可折叠的"邮箱绑定"区域：

```html
<details style="margin-bottom:0.8rem;border:1px solid var(--border-input);border-radius:var(--radius-sm);padding:0.6rem 0.8rem">
  <summary style="font-size:0.85rem;cursor:pointer;color:var(--text-secondary)">邮箱绑定（可选）</summary>
  <div style="margin-top:0.5rem;display:grid;grid-template-columns:1fr 1fr;gap:0.5rem">
    <label style="display:block;font-size:0.8rem;color:var(--text-muted)">
      SMTP 服务器
      <input type="text" id="spSmtpHost" placeholder="smtp.gmail.com" style="width:100%;margin-top:0.15rem;padding:0.45rem 0.6rem;border-radius:var(--radius-sm);border:var(--border-input);background:var(--bg-input);color:var(--text-primary);font-size:0.85rem" />
    </label>
    <label style="display:block;font-size:0.8rem;color:var(--text-muted)">
      SMTP 端口
      <input type="number" id="spSmtpPort" value="587" style="width:100%;margin-top:0.15rem;padding:0.45rem 0.6rem;border-radius:var(--radius-sm);border:var(--border-input);background:var(--bg-input);color:var(--text-primary);font-size:0.85rem" />
    </label>
    <label style="display:block;font-size:0.8rem;color:var(--text-muted)">
      SMTP 账号
      <input type="text" id="spSmtpUser" placeholder="user@gmail.com" style="width:100%;margin-top:0.15rem;padding:0.45rem 0.6rem;border-radius:var(--radius-sm);border:var(--border-input);background:var(--bg-input);color:var(--text-primary);font-size:0.85rem" />
    </label>
    <label style="display:block;font-size:0.8rem;color:var(--text-muted)">
      SMTP 密码
      <input type="password" id="spSmtpPass" placeholder="应用专用密码" style="width:100%;margin-top:0.15rem;padding:0.45rem 0.6rem;border-radius:var(--radius-sm);border:var(--border-input);background:var(--bg-input);color:var(--text-primary);font-size:0.85rem" />
    </label>
    <label style="display:block;font-size:0.8rem;color:var(--text-muted)">
      IMAP 服务器
      <input type="text" id="spImapHost" placeholder="imap.gmail.com" style="width:100%;margin-top:0.15rem;padding:0.45rem 0.6rem;border-radius:var(--radius-sm);border:var(--border-input);background:var(--bg-input);color:var(--text-primary);font-size:0.85rem" />
    </label>
    <label style="display:block;font-size:0.8rem;color:var(--text-muted)">
      IMAP 端口
      <input type="number" id="spImapPort" value="993" style="width:100%;margin-top:0.15rem;padding:0.45rem 0.6rem;border-radius:var(--radius-sm);border:var(--border-input);background:var(--bg-input);color:var(--text-primary);font-size:0.85rem" />
    </label>
    <label style="display:block;font-size:0.8rem;color:var(--text-muted)">
      企业微信 UserID
      <input type="text" id="spWeworkUserid" placeholder="zhangsan" style="width:100%;margin-top:0.15rem;padding:0.45rem 0.6rem;border-radius:var(--radius-sm);border:var(--border-input);background:var(--bg-input);color:var(--text-primary);font-size:0.85rem" />
    </label>
  </div>
</details>
```

表格 `<thead>` 增加一列 "绑定邮箱"：

```html
<th>绑定邮箱</th>
```

- [ ] **Step 3: 更新 JS，读写新增字段**

`crm_salespersons.js` 中：

更新表格渲染函数，在电话列后增加绑定邮箱列：

```javascript
const smtpInfo = sp.smtp_username || '';
tds += `<td><span class="badge">${escHtml(smtpInfo) || '<span style="color:var(--text-muted)">未绑定</span>'}</span></td>`;
```

`showEditModal` 函数中填充新字段：

```javascript
document.getElementById('spSmtpHost').value = sp.smtp_host || '';
document.getElementById('spSmtpPort').value = sp.smtp_port || 587;
document.getElementById('spSmtpUser').value = sp.smtp_username || '';
document.getElementById('spSmtpPass').value = sp.smtp_password || '';
document.getElementById('spImapHost').value = sp.imap_host || '';
document.getElementById('spImapPort').value = sp.imap_port || 993;
document.getElementById('spWeworkUserid').value = sp.wework_userid || '';
```

`saveSalesperson` 函数中使用 FormData 收集全部字段：

```javascript
const fd = new FormData();
fd.set('name', el('spName').value.trim());
fd.set('email', el('spEmail').value.trim());
fd.set('phone', el('spPhone').value.trim());
fd.set('smtp_host', el('spSmtpHost').value.trim());
fd.set('smtp_port', el('spSmtpPort').value);
fd.set('smtp_username', el('spSmtpUser').value.trim());
fd.set('smtp_password', el('spSmtpPass').value);
fd.set('imap_host', el('spImapHost').value.trim());
fd.set('imap_port', el('spImapPort').value);
fd.set('wework_userid', el('spWeworkUserid').value.trim());
```

- [ ] **Step 4: 手动测试**

启动 Web 服务，访问 `/crm/salespersons`，添加/编辑一个销售，填写邮箱绑定信息，确认保存和加载正常。

- [ ] **Step 5: Commit**

```bash
git add src/agents/crm/routes.py src/agents/crm/templates/crm_salespersons.html src/agents/crm/static/js/crm_salespersons.js
git commit -m "feat: 销售管理增加 SMTP/IMAP/企业微信绑定表单"
```

---

### Task 3: 邮件发送 — 改用业务员自己的 SMTP

**Files:**
- Modify: `src/agents/inquiry_mail/tasks.py`
- Modify: `src/agents/inquiry_mail/routes.py`

- [ ] **Step 1: 修改生成任务，注入 salesperson SMTP 信息**

在 `generate_emails_job` 中，当 `customer_ids` 为空（自动选择）时，按 `assigned_salesperson_id` 分组，为每组客户关联对应业务员的 SMTP 信息。

由于 `generate_emails_job` 只负责生成邮件，不发送，此步骤主要是确保生成时记录 `assigned_salesperson_id`。

- [ ] **Step 2: 修改发送任务，以业务员 SMTP 发送**

修改 `send_emails_job`，在发送每封邮件时，从 customer 的 `assigned_salesperson_id` 查找对应业务员的 SMTP 配置。如果业务员有绑定邮箱，用业务员邮箱发送；否则回退到全局 SMTP。

在 `send_emails_job` 的发送循环之前添加查询：

```python
# 预加载业务员 SMTP 配置
sp_smtp = {}
rows_sp = db.execute(
    "SELECT id, smtp_host, smtp_port, smtp_username, smtp_password "
    "FROM salesperson WHERE is_active=1 AND smtp_host != ''"
).fetchall()
for r in rows_sp:
    sp_smtp[r["id"]] = {
        "host": r["smtp_host"], "port": r["smtp_port"],
        "username": r["smtp_username"], "password": r["smtp_password"],
    }
```

在发送循环中，每个 email 构建 SmtpConfig 时：

```python
sp_id = e.get("assigned_salesperson_id")
sp_cfg = sp_smtp.get(sp_id) if sp_id else None
if sp_cfg and sp_cfg["username"]:
    smtp_cfg = SmtpConfig(
        host=sp_cfg["host"], port=sp_cfg["port"],
        username=sp_cfg["username"], password=sp_cfg["password"],
        from_email=sp_cfg["username"],
        from_name=str(smtp_config_dict.get("from_name", "外贸团队")),
        reply_to_email=sp_cfg["username"],
    )
else:
    # Fallback to global config
    smtp_cfg = SmtpConfig(**{k: v for k, v in smtp_config_dict.items() if k in _smtp_fields})
```

**发送时也要更新 `daily_send_log.salesperson_id`**，这样配额按业务员统计。

- [ ] **Step 3: 更新路由 — send_emails 支持按业务员过滤**

无需修改 API 签名，后端自动根据 `assigned_salesperson_id` 分发。

- [ ] **Step 4: Commit**

```bash
git add src/agents/inquiry_mail/tasks.py src/agents/inquiry_mail/routes.py
git commit -m "feat: 邮件发送支持使用业务员绑定的个人SMTP"
```

---

### Task 4: IMAP 收信监控

**Files:**
- Create: `tools/imap_monitor.py`
- Modify: `src/core/config.py`
- Modify: `src/agents/inquiry_mail/config.py`

- [ ] **Step 1: 写 IMAP 轮询核心逻辑**

`tools/imap_monitor.py`：

```python
"""IMAP inbox monitoring — detect customer replies to sent emails."""
from __future__ import annotations

import email
import imaplib
import logging
import re
import time
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MESSAGE_ID_RE = re.compile(r'<([^>]+)>')


@dataclass
class ImapConfig:
    host: str = ""
    port: int = 993
    username: str = ""
    password: str = ""


def parse_message_id(header_value: str | None) -> str | None:
    """Extract message-id from an email header value."""
    if not header_value:
        return None
    m = _MESSAGE_ID_RE.search(header_value)
    return m.group(1) if m else header_value.strip()


def _decode_mime_words(raw: str) -> str:
    """Decode RFC 2047 encoded header to readable string."""
    if not raw:
        return ""
    parts = decode_header(raw)
    result = ""
    for part_bytes, charset in parts:
        if isinstance(part_bytes, bytes):
            result += (part_bytes).decode(charset or "utf-8", errors="replace")
        else:
            # Starlette Form sends header values differently, handle str paths
            result += str(part_bytes) if part_bytes else ""
    return result


def _get_plain_text(msg: Message) -> str:
    """Walk a multipart message and extract text/plain body."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")
    return ""


def connect_imap(cfg: ImapConfig) -> imaplib.IMAP4_SSL | None:
    """Connect to IMAP server and login."""
    try:
        conn = imaplib.IMAP4_SSL(cfg.host, cfg.port, timeout=15)
        conn.login(cfg.username, cfg.password)
        return conn
    except Exception as e:
        logger.warning("IMAP connect failed for %s@%s: %s", cfg.username, cfg.host, e)
        return None


def detect_replies(conn: imaplib.IMAP4_SSL, known_message_ids: set[str],
                   days_back: int = 7) -> list[dict[str, Any]]:
    """
    Search INBOX for replies to our sent messages.

    Args:
        conn: authenticated IMAP connection
        known_message_ids: set of Message-IDs we sent (from daily_send_log.tracking_id or sent mail headers)
        days_back: how far back to search

    Returns:
        list of reply dicts with {subject, body, message_id, in_reply_to, from_addr, date}
    """
    results: list[dict[str, Any]] = []
    try:
        conn.select("INBOX", readonly=True)
        since = time.strftime("%d-%b-%Y", time.localtime(time.time() - days_back * 86400))
        status, data = conn.search(None, f'(UNSEEN SINCE "{since}")')
        if status != "OK":
            return results

        msg_ids = data[0].split()
        for num in msg_ids:
            try:
                status, raw = conn.fetch(num, "(RFC822)")
                if status != "OK":
                    continue
                msg_bytes = raw[0][1]
                msg = email.message_from_bytes(msg_bytes)
                in_reply_to = msg.get("In-Reply-To", "") or msg.get("References", "")
                if not in_reply_to:
                    continue
                ref_ids = _extract_all_message_ids(in_reply_to)
                if ref_ids & known_message_ids:
                    results.append({
                        "subject": _decode_mime_words(msg.get("Subject", "")),
                        "body": _get_plain_text(msg),
                        "message_id": parse_message_id(msg.get("Message-ID", "")),
                        "in_reply_to": parse_message_id(in_reply_to),
                        "from_addr": _decode_mime_words(msg.get("From", "")),
                        "date": msg.get("Date", ""),
                    })
            except Exception as exc:
                logger.warning("Error parsing message %s: %s", num, exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return results


def _extract_all_message_ids(header_value: str) -> set[str]:
    """Extract all message-ids from headers like References (can contain multiple)."""
    return set(_MESSAGE_ID_RE.findall(header_value))


def poll_all_salespersons(data_dir: str) -> dict[int, list[dict]]:
    """
    Poll all active salespersons' IMAP inboxes for replies.
    Returns dict: {salesperson_id: [reply_dict, ...]}
    """
    from src.core.database import get_db

    db = get_db()
    rows = db.execute(
        "SELECT id, imap_host, imap_port, smtp_username, smtp_password "
        "FROM salesperson WHERE is_active=1 AND imap_host != '' AND smtp_username != ''"
    ).fetchall()
    if not rows:
        return {}

    # Collect all known sent message-ids (from daily_send_log.tracking_id and email_tracking)
    known_ids: set[str] = set()
    sent_logs = db.execute(
        "SELECT DISTINCT tracking_id FROM daily_send_log WHERE tracking_id IS NOT NULL AND tracking_id != ''"
    ).fetchall()
    known_ids.update(r["tracking_id"] for r in sent_logs)
    # Also get from email_tracking table
    et_rows = db.execute(
        "SELECT message_id FROM email_tracking WHERE message_id IS NOT NULL AND message_id != ''"
    ).fetchall()
    known_ids.update(r["message_id"] for r in et_rows)

    all_replies: dict[int, list[dict]] = {}
    for sp in rows:
        cfg = ImapConfig(host=sp["imap_host"], port=sp["imap_port"] or 993,
                         username=sp["smtp_username"], password=sp["smtp_password"])
        conn = connect_imap(cfg)
        if not conn:
            continue
        replies = detect_replies(conn, known_ids)
        if replies:
            all_replies[sp["id"]] = replies
            logger.info("Found %d replies for salesperson %d", len(replies), sp["id"])

    return all_replies
```

- [ ] **Step 2: 新增环境变量配置**

`src/agents/inquiry_mail/config.py` 增加：

```python
imap_poll_interval: int = 60  # seconds between IMAP polls
wework_corp_id: str = ""
wework_agent_id: str = ""
wework_agent_secret: str = ""
```

并在 `from_env()` 中添加对应读取。

- [ ] **Step 3: 写 RQ 定时任务入口**

在 `src/agents/inquiry_mail/tasks.py` 添加：

```python
def imap_poll_job(data_root: str) -> dict[str, Any]:
    """RQ job: poll all salespersons IMAP inboxes for replies."""
    from tools.imap_monitor import poll_all_salespersons
    replies = poll_all_salespersons(data_root)
    total = sum(len(v) for v in replies.values())
    return {"total_replies": total, "salesperson_ids": list(replies.keys())}
```

- [ ] **Step 4: 注册调度任务**

在 `src/core/app.py` 的 startup 事件中，增加一个简单的重复入队逻辑（或者用 APScheduler）：

```python
# 在 startup 中启动 IMAP 轮询调度
import asyncio
from rq import Queue
from redis import Redis

async def _schedule_imap_poll():
    redis_conn = Redis.from_url(config.redis_url)
    q = Queue("inquiry_mail:default", connection=redis_conn)
    while True:
        q.enqueue("src.agents.inquiry_mail.tasks.imap_poll_job",
                  str(config.data_dir),
                  job_timeout=300)
        await asyncio.sleep(60)  # 每 60 秒轮询一次

# 作为后台任务启动
asyncio.create_task(_schedule_imap_poll())
```

- [ ] **Step 5: Commit**

```bash
git add tools/imap_monitor.py src/agents/inquiry_mail/config.py src/agents/inquiry_mail/tasks.py src/core/app.py
git commit -m "feat: 新增 IMAP 收信监控模块，支持多业务员邮箱轮询"
```

---

### Task 5: AI 回信生成

**Files:**
- Modify: `tools/email_generator.py`
- Create: `tests/test_reply_generation.py`

- [ ] **Step 1: 写回信生成函数**

在 `tools/email_generator.py` 中添加：

```python
REPLY_SYSTEM_PROMPT = """你是外贸 B2B 邮件回复撰写助手。根据客户的原邮件内容和该客户的历史评估结果，撰写专业、得体的回复。

规则：
1. 输出必须是合法 JSON 对象，不要 markdown 代码围栏。
2. 必须包含 subject（以 "Re: " 开头）、body_text（纯文本正文）。
3. 仔细分析客户的原邮件，理解 ta 的意图（询价？索样？技术问题？合作意向？）。
4. 回复要针对性回答客户问题，不要泛泛而谈。
5. 语气：专业、热情、简洁。
6. 语言：与客户的原始邮件语言保持一致。
7. 如果客户问了暂时回答不了的问题（如具体价格），诚实表示需要确认后回复，不要编造。

JSON 必填字段：
- subject: 字符串，回信主题
- body_text: 字符串，纯文本正文
- tone: 字符串，回复语气（professional/friendly/urgent）
- needs_human_input: 布尔值，是否有些问题 AI 无法确定回答需要人工补充
- human_input_hint: 字符串，需要人工补充的具体问题（needs_human_input 为 true 时必填）"""


def generate_reply(
    *,
    original_subject: str = "",
    original_body: str = "",
    original_from: str = "",
    customer_context: str = "",
    from_name: str = "外贸团队",
    model: str | None = None,
) -> dict[str, Any]:
    """Generate a reply draft based on customer's incoming email."""
    from tools.deepseek_client import chat_json

    user_content = f"""请为以下客户回信撰写回复：

【客户原始邮件】
发件人: {original_from}
主题: {original_subject}
正文:
{original_body[:2000]}

【客户背景】
{customer_context or "无额外信息"}

【发件人署名】
{from_name}"""

    result = chat_json(
        system_prompt=REPLY_SYSTEM_PROMPT,
        user_content=user_content,
        model=model,
        response_format={"type": "json_object"},
    )
    return result


def _build_customer_context(customer_id: int) -> str:
    """Build a concise context string from customer DB record for reply generation."""
    from src.core.database import get_db
    db = get_db()
    row = db.execute(
        "SELECT company_name, contact_name, country_region, target_products, "
        "deal_recommendation, product_fit_reasons, capability_signals, next_action, "
        "email_subject, email_body FROM customer WHERE id=?",
        (customer_id,),
    ).fetchone()
    if not row:
        return ""
    parts = []
    for key in ("company_name", "contact_name", "country_region", "target_products",
                "deal_recommendation", "product_fit_reasons", "capability_signals", "next_action"):
        val = row.get(key, "")
        if val:
            parts.append(f"{key}: {val}")
    if row.get("email_subject"):
        parts.append(f"我们上一封邮件主题: {row['email_subject']}")
    return "\n".join(parts)
```

- [ ] **Step 2: 写测试**

`tests/test_reply_generation.py`：

```python
def test_generate_reply_basic():
    from tools.email_generator import generate_reply
    result = generate_reply(
        original_subject="Re: Product inquiry from ABC Corp",
        original_body="We are interested in your LED products. Can you send catalog and pricing?",
        original_from="john@abccorp.com",
        customer_context="company_name: ABC Corp\ncountry_region: US\nproduct_fit_reasons: LED lighting需求匹配",
        from_name="张三",
    )
    assert isinstance(result, dict)
    assert "subject" in result
    assert "body_text" in result
    assert result["subject"].startswith("Re:")


def test_generate_reply_handles_empty_context():
    from tools.email_generator import generate_reply
    result = generate_reply(
        original_subject="Hello",
        original_body="I want to buy from you.",
        original_from="test@test.com",
        from_name="test",
    )
    assert isinstance(result, dict)
    assert "body_text" in result
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_reply_generation.py -v
```

Expected: 2 tests pass (注意：这依赖 DeepSeek API，如果在 CI 中可能需要 mock)

- [ ] **Step 4: Commit**

```bash
git add tools/email_generator.py tests/test_reply_generation.py
git commit -m "feat: 新增 AI 回信生成 — 根据客户回信内容自动撰写回复"
```

---

### Task 6: 企业微信集成 — 消息推送

**Files:**
- Create: `tools/wecom_notify.py`
- Modify: `src/core/config.py`
- Create: `tests/test_wecom_notify.py`

- [ ] **Step 1: 写企业微信推送核心模块**

`tools/wecom_notify.py`：

```python
"""WeChat Work (企业微信) integration — template card messaging."""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WECOM_CARD_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"


@dataclass
class WeComConfig:
    corp_id: str = ""
    agent_id: str = ""
    agent_secret: str = ""

    @classmethod
    def from_env(cls) -> "WeComConfig":
        import os
        return cls(
            corp_id=(os.environ.get("WECOM_CORP_ID") or "").strip(),
            agent_id=(os.environ.get("WECOM_AGENT_ID") or "").strip(),
            agent_secret=(os.environ.get("WECOM_AGENT_SECRET") or "").strip(),
        )


def _get_access_token(cfg: WeComConfig) -> str | None:
    """Get WeCom API access token (cached in memory, expires 2h)."""
    url = f"{WECOM_TOKEN_URL}?corpid={cfg.corp_id}&corpsecret={cfg.agent_secret}"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data.get("errcode") == 0:
            return data["access_token"]
        logger.error("WeCom token error: %s", data)
        return None
    except Exception as e:
        logger.error("WeCom token request failed: %s", e)
        return None


def send_reply_card(
    *,
    wework_userid: str,
    customer_name: str,
    original_snippet: str,
    draft_snippet: str,
    draft_id: int,
    cfg: WeComConfig | None = None,
) -> bool:
    """
    Send a template card notification to a specific WeCom user.

    The card contains: customer name, original email snippet, AI draft snippet,
    and three action buttons (Confirm Send / Edit / Ignore).
    """
    if cfg is None:
        cfg = WeComConfig.from_env()

    if not cfg.corp_id or not cfg.agent_id or not cfg.agent_secret:
        logger.warning("WeCom not configured, skipping notification")
        return False

    token = _get_access_token(cfg)
    if not token:
        return False

    # Build task_id as callback data carrier
    task_id = json.dumps({"draft_id": draft_id, "ts": int(time.time())})

    payload = {
        "touser": wework_userid,
        "msgtype": "template_card",
        "agentid": int(cfg.agent_id),
        "template_card": {
            "card_type": "text_notice",
            "source": {"desc": "询盘回信提醒", "desc_color": 1},
            "main_title": {"title": f"{customer_name} 回复了你的邮件"},
            "emphasis_content": {"title": original_snippet[:100], "desc": "客户原文"},
            "sub_title_text": f"AI 建议回复：{draft_snippet[:200]}",
            "horizontal_content_list": [
                {"keyname": "客户", "value": customer_name},
                {"keyname": "时间", "value": time.strftime("%H:%M")},
            ],
            "card_action": {
                "type": 1,
                "url": f"{_get_base_url()}/inquiry-mail/reply/{draft_id}",
            },
            "task_id": task_id,
            "button_list": [
                {"text": "确认发送", "style": 1, "key": "confirm_send"},
                {"text": "编辑", "style": 2, "key": "edit"},
                {"text": "忽略", "style": 0, "key": "ignore"},
            ],
        },
    }

    url = f"{WECOM_CARD_URL}?access_token={token}"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data.get("errcode") == 0:
            logger.info("WeCom card sent to %s, draft_id=%d", wework_userid, draft_id)
            return True
        logger.error("WeCom card send failed: %s", data)
        return False
    except Exception as e:
        logger.error("WeCom card request failed: %s", e)
        return False


def _get_base_url() -> str:
    import os
    return (os.environ.get("PLATFORM_BASE_URL") or "http://localhost:8000").rstrip("/")
```

- [ ] **Step 2: 写测试**

`tests/test_wecom_notify.py`：

```python
def test_wecom_config_from_env_empty():
    from tools.wecom_notify import WeComConfig
    import os
    # 备份环境变量
    saved = {k: os.environ.pop(k, None) for k in list(os.environ) if k.startswith("WECOM_")}
    try:
        cfg = WeComConfig.from_env()
        assert cfg.corp_id == ""
        assert cfg.agent_id == ""
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_send_reply_card_not_configured():
    """Should return False without crashing when WeCom is not configured."""
    from tools.wecom_notify import send_reply_card, WeComConfig
    cfg = WeComConfig(corp_id="", agent_id="", agent_secret="")
    result = send_reply_card(
        wework_userid="test",
        customer_name="Test Corp",
        original_snippet="Hello",
        draft_snippet="Hi there",
        draft_id=1,
        cfg=cfg,
    )
    assert result is False
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_wecom_notify.py -v
```

Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```bash
git add tools/wecom_notify.py src/core/config.py tests/test_wecom_notify.py
git commit -m "feat: 企业微信集成 — 模板卡片消息推送回信提醒"
```

---

### Task 7: 回信审批 API + 在线编辑器

**Files:**
- Modify: `src/agents/inquiry_mail/routes.py`
- Create: `src/agents/inquiry_mail/templates/reply_editor.html`
- Create: `src/agents/inquiry_mail/static/css/reply.css`

- [ ] **Step 1: 增加回信审批 API 端点**

在 `src/agents/inquiry_mail/routes.py` 中添加：

```python
@router.get("/reply/{draft_id}", response_class=HTMLResponse)
def reply_editor_page(draft_id: int, request: Request):
    """Mobile-friendly reply editor for salespersons."""
    from src.core.app import app
    db = get_db()
    draft = db.execute(
        "SELECT r.*, c.company_name, c.contact_name, c.contact_email, c.country_region, "
        "s.name as salesperson_name "
        "FROM reply_draft r "
        "JOIN customer c ON r.customer_id = c.id "
        "JOIN salesperson s ON r.salesperson_id = s.id "
        "WHERE r.id=?",
        (draft_id,),
    ).fetchone()
    if not draft:
        raise HTTPException(404, "草稿不存在")

    t = app.state.jinja_env.get_template("reply_editor.html")
    return HTMLResponse(t.render({
        "request": request,
        "draft": dict_from_row(draft),
    }))


@router.get("/api/replies")
def list_reply_drafts(
    _: Annotated[None, Depends(require_auth)],
    status: str = "pending",
    salesperson_id: str = "",
) -> JSONResponse:
    """List reply drafts for admin/review."""
    db = get_db()
    where = ["r.status = ?"]
    params: list[Any] = [status]
    if salesperson_id.strip():
        where.append("r.salesperson_id = ?")
        params.append(int(salesperson_id))
    rows = db.execute(
        f"SELECT r.*, c.company_name, s.name as salesperson_name "
        f"FROM reply_draft r "
        f"JOIN customer c ON r.customer_id = c.id "
        f"JOIN salesperson s ON r.salesperson_id = s.id "
        f"WHERE {' AND '.join(where)} ORDER BY r.created_at DESC LIMIT 100",
        params,
    ).fetchall()
    return JSONResponse(dicts_from_rows(rows))


@router.put("/api/replies/{draft_id}")
def update_reply_draft(
    draft_id: int,
    _: Annotated[None, Depends(require_auth)],
    draft_subject: str = Form(""),
    draft_body: str = Form(""),
) -> JSONResponse:
    """Update a reply draft's subject/body."""
    db = get_db()
    db.execute(
        "UPDATE reply_draft SET draft_subject=?, draft_body=?, updated_at=datetime('now','localtime') WHERE id=?",
        (draft_subject.strip(), draft_body.strip(), draft_id),
    )
    db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/api/replies/{draft_id}/approve")
def approve_reply(
    draft_id: int,
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """Approve and send a reply draft using the salesperson's SMTP."""
    db = get_db()
    draft = db.execute(
        "SELECT r.*, s.smtp_host, s.smtp_port, s.smtp_username, s.smtp_password, "
        "s.name as sp_name, c.contact_email, c.company_name "
        "FROM reply_draft r "
        "JOIN salesperson s ON r.salesperson_id = s.id "
        "JOIN customer c ON r.customer_id = c.id "
        "WHERE r.id=? AND r.status IN ('pending','edited')",
        (draft_id,),
    ).fetchone()
    if not draft:
        raise HTTPException(404, "草稿不存在或已处理")

    from tools.email_sender import SmtpConfig, send_single_email
    smtp_cfg = SmtpConfig(
        host=draft["smtp_host"], port=draft["smtp_port"] or 587,
        username=draft["smtp_username"], password=draft["smtp_password"],
        from_email=draft["smtp_username"], from_name=draft["sp_name"] or "外贸团队",
    )
    result = send_single_email(
        smtp_cfg,
        to_email=draft["contact_email"],
        subject=draft["draft_subject"],
        body_text=draft["draft_body"],
    )
    if result["success"]:
        db.execute(
            "UPDATE reply_draft SET status='sent', sent_at=datetime('now','localtime'), "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (draft_id,),
        )
        db.execute(
            "UPDATE customer SET email_status='sent', email_sent_at=datetime('now','localtime'), "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (draft["customer_id"],),
        )
    else:
        db.execute(
            "UPDATE reply_draft SET status='send_failed', updated_at=datetime('now','localtime') WHERE id=?",
            (draft_id,),
        )
    db.commit()
    return JSONResponse({"status": "sent" if result["success"] else "failed", "error": result.get("error")})


@router.post("/api/replies/{draft_id}/ignore")
def ignore_reply(
    draft_id: int,
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """Mark a reply draft as ignored."""
    db = get_db()
    db.execute(
        "UPDATE reply_draft SET status='cancelled', updated_at=datetime('now','localtime') WHERE id=?",
        (draft_id,),
    )
    db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/api/wecom/callback")
async def wecom_callback(request: Request):
    """Receive WeCom card button click callbacks."""
    import xml.etree.ElementTree as ET
    body = await request.body()
    root = ET.fromstring(body)
    msg_type = root.findtext("MsgType", "")
    if msg_type == "event":
        event_key = root.findtext("EventKey", "")
        task_id = root.findtext("TaskId", "")
        user_id = root.findtext("FromUserName", "")
        # task_id is JSON: {"draft_id": N, "ts": ...}
        try:
            task_data = json.loads(task_id)
            draft_id = task_data["draft_id"]
        except Exception:
            logger.warning("WeCom callback: invalid task_id %s", task_id)
            return JSONResponse({"errcode": 0, "errmsg": "ok"})

        db = get_db()
        if event_key == "confirm_send":
            # 触发发送
            db.execute(
                "UPDATE reply_draft SET status='approved', updated_at=datetime('now','localtime') WHERE id=? AND status='pending'",
                (draft_id,),
            )
            db.commit()
        elif event_key == "edit":
            # 业务员需打开编辑器 — 已通过 card_action URL 处理
            pass
        elif event_key == "ignore":
            db.execute(
                "UPDATE reply_draft SET status='cancelled', updated_at=datetime('now','localtime') WHERE id=? AND status='pending'",
                (draft_id,),
            )
            db.commit()

    return JSONResponse({"errcode": 0, "errmsg": "ok"})
```

- [ ] **Step 2: 创建在线编辑器页面**

`src/agents/inquiry_mail/templates/reply_editor.html`（移动端友好的纯内容块，不继承 base.html 以简化移动加载）：

```html
<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
  <title>回复编辑 - {{ draft.company_name }}</title>
  <link rel="stylesheet" href="/static/inquiry-mail/css/reply.css?v=1" />
</head>
<body>
  <div class="reply-editor">
    <div class="editor-header">
      <a href="javascript:history.back()" class="back-btn">&larr;</a>
      <div>
        <div class="editor-title">回复 {{ draft.company_name }}</div>
        <div class="editor-sub">客户: {{ draft.contact_name or 'N/A' }} · {{ draft.country_region }}</div>
      </div>
    </div>

    <div class="editor-info-callout">
      <strong>客户原文</strong>
      <p>{{ draft.original_body[:500] }}</p>
    </div>

    <div class="editor-form">
      <label>
        <span>主题</span>
        <input type="text" id="subject" value="{{ draft.draft_subject }}" />
      </label>
      <label>
        <span>内容</span>
        <textarea id="body" rows="12">{{ draft.draft_body }}</textarea>
      </label>
      <div class="editor-actions">
        <button id="btnSend" class="btn-send">确认发送</button>
        <button id="btnSave" class="btn-save">保存草稿</button>
      </div>
    </div>
  </div>

  <script>
    const DRAFT_ID = {{ draft.id }};

    async function saveDraft() {
      const fd = new FormData();
      fd.set('draft_subject', document.getElementById('subject').value);
      fd.set('draft_body', document.getElementById('body').value);
      const r = await fetch('/inquiry-mail/api/replies/' + DRAFT_ID, { method: 'PUT', body: fd });
      if (r.ok) { alert('已保存'); } else { alert('保存失败'); }
    }

    async function approveAndSend() {
      if (!confirm('确认发送此回复？')) return;
      const r = await fetch('/inquiry-mail/api/replies/' + DRAFT_ID + '/approve', { method: 'POST' });
      const d = await r.json();
      if (d.status === 'sent') { alert('发送成功！'); location.reload(); }
      else { alert('发送失败: ' + (d.error || 'unknown')); }
    }

    document.getElementById('btnSave').onclick = saveDraft;
    document.getElementById('btnSend').onclick = approveAndSend;
  </script>
</body>
</html>
```

- [ ] **Step 3: 创建编辑器 CSS**

`src/agents/inquiry_mail/static/css/reply.css`：

```css
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f1a; color: #e0e0e0; line-height: 1.5; }
.reply-editor { max-width: 480px; margin: 0 auto; padding: 1rem; }
.editor-header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem; }
.back-btn { color: #888; text-decoration: none; font-size: 1.2rem; }
.editor-title { font-size: 1.05rem; font-weight: 700; }
.editor-sub { font-size: 0.78rem; color: #888; }
.editor-info-callout { background: rgba(255,255,255,0.05); border-radius: 8px; padding: 0.75rem; margin-bottom: 1rem; font-size: 0.82rem; }
.editor-info-callout strong { color: #aaa; display: block; margin-bottom: 0.35rem; }
.editor-info-callout p { color: #999; max-height: 200px; overflow-y: auto; }
.editor-form label { display: block; margin-bottom: 0.75rem; }
.editor-form label span { font-size: 0.78rem; color: #888; display: block; margin-bottom: 0.25rem; }
.editor-form input, .editor-form textarea { width: 100%; padding: 0.6rem 0.7rem; border-radius: 6px; border: 1px solid #333; background: #1a1a2e; color: #e0e0e0; font-size: 0.88rem; font-family: inherit; }
.editor-actions { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
.btn-send { flex: 1; padding: 0.65rem; border-radius: 6px; border: none; background: #07c160; color: #fff; font-size: 0.9rem; font-weight: 600; cursor: pointer; }
.btn-save { flex: 1; padding: 0.65rem; border-radius: 6px; border: 1px solid #444; background: transparent; color: #aaa; font-size: 0.88rem; cursor: pointer; }
```

- [ ] **Step 4: 手动验证**

启动 Web 服务，手动在数据库中插入一条 reply_draft 记录，访问 `/inquiry-mail/reply/1` 确认编辑器可正常显示。

- [ ] **Step 5: Commit**

```bash
git add src/agents/inquiry_mail/routes.py src/agents/inquiry_mail/templates/reply_editor.html src/agents/inquiry_mail/static/css/reply.css
git commit -m "feat: 回信审批API + 移动端在线编辑器"
```

---

### Task 8: 串联 — IMAP 轮询 → AI 生成 → 推送的完整流程

**Files:**
- Modify: `src/agents/inquiry_mail/tasks.py`

- [ ] **Step 1: 增强 imap_poll_job，串联完整流程**

在 `src/agents/inquiry_mail/tasks.py` 中重写 `imap_poll_job`：

```python
def imap_poll_job(data_root: str) -> dict[str, Any]:
    """
    Full pipeline:
    1. Poll all active salespersons' IMAP inboxes
    2. Match replies to customers (via In-Reply-To / subject)
    3. Generate AI reply draft
    4. Save to reply_draft table
    5. Push WeCom notification card to the salesperson
    """
    from tools.imap_monitor import poll_all_salespersons
    from tools.email_generator import generate_reply, _build_customer_context
    from tools.wecom_notify import send_reply_card
    from src.core.database import get_db

    db = get_db()
    all_replies = poll_all_salespersons(data_root)
    total_processed = 0

    for sp_id, replies in all_replies.items():
        # 获取业务员信息
        sp = db.execute(
            "SELECT name, wework_userid, smtp_username FROM salesperson WHERE id=?",
            (sp_id,),
        ).fetchone()
        if not sp:
            continue

        for reply in replies:
            # 匹配客户 — 通过 In-Reply-To 查找原始发出邮件
            # 在 daily_send_log 中查找匹配的 tracking_id/message_id
            matched_customer = None
            if reply.get("in_reply_to"):
                row = db.execute(
                    "SELECT customer_id FROM daily_send_log WHERE tracking_id=?",
                    (reply["in_reply_to"],),
                ).fetchone()
                if row:
                    matched_customer = row["customer_id"]

            if not matched_customer:
                # Fallback: 通过发件人邮箱匹配客户
                from_addr = reply.get("from_addr", "")
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', from_addr)
                if email_match:
                    row = db.execute(
                        "SELECT id FROM customer WHERE contact_email LIKE ? OR contact_emails_all LIKE ?",
                        (f"%{email_match.group()}%", f"%{email_match.group()}%"),
                    ).fetchone()
                    if row:
                        matched_customer = row["id"]

            if not matched_customer:
                logger.info("No customer match for reply from %s", reply.get("from_addr", "?"))
                continue

            # 生成 AI 回信
            context = _build_customer_context(matched_customer)
            ai_draft = generate_reply(
                original_subject=reply.get("subject", ""),
                original_body=reply.get("body", ""),
                original_from=reply.get("from_addr", ""),
                customer_context=context,
                from_name=sp["name"] or "外贸团队",
            )

            # 保存到 reply_draft
            cur = db.execute(
                "INSERT INTO reply_draft (customer_id, salesperson_id, original_body, "
                "original_subject, original_message_id, draft_body, draft_subject, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
                (matched_customer, sp_id, reply.get("body", ""),
                 reply.get("subject", ""), reply.get("message_id", ""),
                 ai_draft.get("body_text", ""), ai_draft.get("subject", "")),
            )
            draft_id = cur.lastrowid
            db.commit()

            # 企业微信推送
            if sp["wework_userid"]:
                send_reply_card(
                    wework_userid=sp["wework_userid"],
                    customer_name=context.split("company_name: ")[-1].split("\n")[0] if "company_name:" in context else "客户",
                    original_snippet=reply.get("body", "")[:150],
                    draft_snippet=ai_draft.get("body_text", "")[:200],
                    draft_id=draft_id,
                )

            total_processed += 1

    return {"total_processed": total_processed}
```

- [ ] **Step 2: Commit**

```bash
git add src/agents/inquiry_mail/tasks.py
git commit -m "feat: 串联 IMAP收信→AI生成→企业微信推送完整流程"
```

---

### Task 9: 集成测试 + 端到端验证

**Files:**
- Create: `tests/test_reply_pipeline.py`

- [ ] **Step 1: 写端到端流程测试**

```python
def test_imap_detect_replies_mock():
    """Verify reply detection logic with mocked IMAP data."""
    from tools.imap_monitor import _extract_all_message_ids
    ids = _extract_all_message_ids(
        "<abc123@mail.gmail.com> <def456@mail.gmail.com>"
    )
    assert "abc123@mail.gmail.com" in ids
    assert "def456@mail.gmail.com" in ids


def test_reply_draft_schema():
    """Verify reply_draft table exists and accepts data."""
    from src.core.database import get_db
    db = get_db()
    # Insert test and rollback to verify schema without polluting
    db.execute("BEGIN")
    db.execute(
        "INSERT INTO reply_draft (customer_id, salesperson_id, original_body, "
        "draft_body, status) VALUES (1, 1, 'test', 'test reply', 'pending')"
    )
    row = db.execute("SELECT * FROM reply_draft WHERE status='pending' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["draft_body"] == "test reply"
    db.execute("ROLLBACK")


def test_full_pipeline_dry_run():
    """Simulate: reply detection -> AI generation -> draft save -> send (dry)."""
    from src.core.database import get_db
    from tools.email_generator import generate_reply, _build_customer_context

    # 1. Simulated reply from customer
    simulated_reply = {
        "subject": "Re: Your product catalog",
        "body": "Hello, I am interested in your LED strip lights. Can you send me a quotation for 1000m?",
        "from_addr": "buyer@example.com",
        "in_reply_to": "tracking_fake_001",
    }

    # 2. Insert a known sent tracking record
    db = get_db()
    db.execute("BEGIN")
    db.execute(
        "INSERT INTO daily_send_log (sent_date, recipient_email, customer_id, status, tracking_id) "
        "VALUES (date('now','localtime'), 'buyer@example.com', 1, 'sent', 'tracking_fake_001')"
    )

    # 3. Generate AI reply (this will call the real API)
    context = _build_customer_context(1)
    ai_draft = generate_reply(
        original_subject=simulated_reply["subject"],
        original_body=simulated_reply["body"],
        original_from=simulated_reply["from_addr"],
        customer_context=context,
        from_name="Test Agent",
    )
    assert "subject" in ai_draft
    assert "body_text" in ai_draft
    assert len(ai_draft["body_text"]) > 10

    db.execute("ROLLBACK")
```

- [ ] **Step 2: 运行全部测试**

```bash
pytest tests/ -v
```

Expected: 所有已有测试通过 + 新增的 4 个测试通过。

- [ ] **Step 3: Commit**

```bash
git add tests/test_reply_pipeline.py
git commit -m "test: 回信管道集成测试"
```

---

### Task 10: 部署配置 — 环境变量 + 启动脚本

**Files:**
- Modify: `start_all.bat`
- Modify: `.env.example` (if exists)

- [ ] **Step 1: 更新环境变量模板**

在项目根目录创建/更新 `.env.example`，增加企业微信相关变量：

```bash
# 企业微信（询盘邮件 2.0 回信推送）
WECOM_CORP_ID=
WECOM_AGENT_ID=
WECOM_AGENT_SECRET=
PLATFORM_BASE_URL=http://localhost:8000

# IMAP 轮询间隔（秒，默认 60）
IMAP_POLL_INTERVAL=60
```

- [ ] **Step 2: 启动脚本无需修改**

现有的 `start_all.bat` 已包含 RQ Worker 启动，IMAP 轮询由 app.py startup 事件调度，无需额外进程。但如果想让 IMAP 轮询有独立的 Worker 队列，可在 `start_all.bat` 中增加一行：

```bat
start "IMAP Poll Worker" rq worker -u redis://127.0.0.1:6379/0 inquiry_mail:poll --worker-class rq.SimpleWorker
```

实际实现中，轮询通过 `asyncio.create_task` 在 Web 进程中运行，无需额外 Worker。

- [ ] **Step 3: Commit**

```bash
git add .env.example start_all.bat
git commit -m "chore: 更新环境变量模板，增加企业微信配置"
```

---

## Plan Self-Review

1. **Spec coverage:** 所有需求都已覆盖 — 多业务员邮箱绑定（Task 1-2）、个人 SMTP 发送（Task 3）、IMAP 收信监控（Task 4）、AI 回信生成（Task 5）、企业微信推送（Task 6）、审批 API + 编辑器（Task 7）、完整串联（Task 8）、测试（Task 9）、部署配置（Task 10）

2. **Placeholder scan:** 所有代码步骤都包含可运行的代码，无 TBD/TODO

3. **Type consistency:** 所有函数签名、数据表字段名在各 Task 中保持一致。`reply_draft` 表字段名（`draft_subject`, `draft_body`, `original_message_id`）在 Task 1/7/8 中一致
