# 权限系统实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为平台增加多用户 Session 登录和基于角色的权限控制。管理员创建销售账号、分配客户；销售仅看到分配给自己的客户，无法访问客户评估模块。

**Architecture:** Redis Session + Cookie（HttpOnly）替换现有 HTTP Basic Auth。`auth.py` 提供 `require_auth` / `require_admin` 两个 FastAPI 依赖注入，各模块路由按需引入。数据隔离通过 `apply_sales_filter()` 工具函数在所有客户列表 API 中自动追加 `assigned_salesperson_id` 过滤条件。

**Tech Stack:** bcrypt（密码哈希）、Redis（Session 存储）、FastAPI Dependency Injection

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/core/database.py` | 修改 | 加 `password_hash` / `role` 列迁移 |
| `src/agents/base.py` | 修改 | AgentManifest 加 `admin_only` 字段 |
| `src/core/auth.py` | 重写 | Session 认证 + 权限依赖 + 数据隔离工具 |
| `src/static/js/api.js` | 修改 | 移除 Basic Auth，改用 Cookie + 401 跳转登录 |
| `src/core/app.py` | 修改 | 登录/登出路由 + 用户中间件 + 管理员自举 |
| `src/templates/login.html` | 新建 | 登录页 |
| `src/templates/base.html` | 修改 | 用户名显示 + 登出 + 导航过滤 + JS 用户注入 |
| `src/agents/customer_eval/manifest.py` | 修改 | `admin_only=True` |
| `src/agents/customer_eval/routes.py` | 修改 | 全部路由改用 `require_admin` |
| `src/agents/crm/routes.py` | 修改 | 客户列表加隔离 + 销售管理 API 加 `require_admin` |
| `src/agents/crm/templates/crm_salespersons.html` | 修改 | 表单加密码和角色字段 |
| `src/agents/crm/static/js/crm_salespersons.js` | 修改 | JS 加密码和角色字段提交 |
| `src/agents/crm/static/js/crm_list.js` | 修改 | 销售登录后隐藏销售筛选下拉 |
| `src/agents/inquiry_mail/routes.py` | 修改 | 客户列表 API 加隔离 |
| `src/agents/inquiry_mail/static/js/mail.js` | 修改 | 销售登录后隐藏/锁定销售筛选 |
| `src/agents/social_media/routes.py` | 修改 | 客户列表加隔离 + 销售筛选参数 |
| `src/agents/social_media/templates/social_list.html` | 修改 | 加销售筛选下拉 |
| `src/agents/social_media/static/js/social.js` | 修改 | 销售筛选参数传递 + 隐藏逻辑 |
| `requirements.txt` | 修改 | 加 `bcrypt` |

---

### Task 1: 数据库列迁移 + AgentManifest 扩展

**Files:**
- Modify: `src/core/database.py`
- Modify: `src/agents/base.py`

- [ ] **Step 1: database.py 加两列迁移**

在 `get_db()` 的 `_ensure_column` 调用列表末尾（`_ensure_column(conn, "daily_send_log", "tracking_id", ...)` 之后）添加：

```python
_ensure_column(conn, "salesperson", "password_hash", "TEXT")
_ensure_column(conn, "salesperson", "role", "TEXT DEFAULT 'salesperson'")
```

在 `_init_pg_pool()` 的 PG 迁移区域添加同样的两行（用 `_ensure_column_pg`）。

- [ ] **Step 2: AgentManifest 加 admin_only 字段**

在 `src/agents/base.py` 的 `AgentManifest` dataclass 中加一行：

```python
admin_only: bool = False  # True = 仅管理员可见（导航栏过滤）
```

- [ ] **Step 3: 验证迁移**

Run: `python -c "from src.core.database import get_db; db=get_db(); print(list(db.execute('PRAGMA table_info(salesperson)'))); db.close()"`
Expected: 输出中包含 `password_hash` 和 `role` 两行。

- [ ] **Step 4: Commit**

```bash
git add src/core/database.py src/agents/base.py
git commit -m "feat: salesperson表加password_hash+role列, AgentManifest加admin_only字段"
```

---

### Task 2: 重写认证系统 auth.py

**Files:**
- Modify: `src/core/auth.py`（完全重写）
- Modify: `src/static/js/api.js`

- [ ] **Step 1: 安装 bcrypt**

```bash
pip install bcrypt
```

- [ ] **Step 2: 重写 auth.py**

用以下内容完全替换 `src/core/auth.py`：

```python
"""Session-based auth with Redis. Provides require_auth and require_admin deps."""

from __future__ import annotations

import json
import logging
import secrets
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request
from redis import Redis

from src.core.config import PlatformConfig, get_config

logger = logging.getLogger(__name__)

SESSION_TTL = 86400  # 24 hours


def _redis(config: PlatformConfig) -> Redis:
    return Redis.from_url(config.redis_url)


def _get_session(request: Request, config: PlatformConfig) -> dict | None:
    """Read session_id from cookie, fetch user from Redis. Returns None if not logged in."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None
    try:
        r = _redis(config)
        data = r.get(f"session:{session_id}")
        if data is None:
            return None
        user = json.loads(data if isinstance(data, str) else data.decode("utf-8"))
        # Refresh TTL
        r.expire(f"session:{session_id}", SESSION_TTL)
        return user
    except Exception:
        logger.exception("Session read error")
        return None


def create_session(config: PlatformConfig, user: dict) -> str:
    """Create a Redis session and return session_id. Caller is responsible for Set-Cookie."""
    session_id = secrets.token_urlsafe(32)
    r = _redis(config)
    r.setex(f"session:{session_id}", SESSION_TTL, json.dumps(user, ensure_ascii=False))
    return session_id


def destroy_session(config: PlatformConfig, session_id: str) -> None:
    """Delete a Redis session."""
    try:
        r = _redis(config)
        r.delete(f"session:{session_id}")
    except Exception:
        logger.exception("Session delete error")


def get_current_user(
    request: Request,
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> dict | None:
    """从 Cookie 读取当前用户。未登录返回 None（不抛异常）。"""
    return _get_session(request, config)


def require_auth(
    request: Request,
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> dict:
    """需要登录。返回 user dict {id, name, role}。未登录返回 302 重定向到 /login。"""
    user = _get_session(request, config)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_admin(
    user: Annotated[dict, Depends(require_auth)],
) -> dict:
    """需要管理员角色。非 admin 抛 403。"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可访问")
    return user


def apply_sales_filter(where: list, params: list, user: dict, table_alias: str = "c") -> None:
    """销售只能看到分配给自己的客户，管理员看到全部。"""
    if user.get("role") == "salesperson":
        where.append(f"{table_alias}.assigned_salesperson_id = ?")
        params.append(user["id"])
```

- [ ] **Step 3: 修改 api.js — 移除 Basic Auth，改为 401 跳转登录**

用以下内容替换 `src/static/js/api.js`：

```javascript
// api.js — Shared fetch wrapper (Session auth via Cookie)
export async function apiFetch(url, opts = {}) {
  const r = await fetch(url, { ...opts, credentials: 'same-origin' });
  if (r.status === 401) {
    window.location.href = '/login?redirect=' + encodeURIComponent(window.location.pathname);
    throw new Error('auth_required');
  }
  if (r.status === 403) {
    throw new Error('forbidden');
  }
  return r;
}

export async function apiGet(url) {
  return apiFetch(url);
}

export async function apiPost(url, body, isJson = false) {
  const opts = { method: 'POST' };
  if (isJson) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  } else {
    opts.body = body;
  }
  return apiFetch(url, opts);
}
```

- [ ] **Step 4: Commit**

```bash
git add src/core/auth.py src/static/js/api.js requirements.txt
git commit -m "feat: Session认证替换HTTP Basic Auth, api.js改用Cookie+401跳转"
```

---

### Task 3: 平台核心 — 登录/登出/中间件/管理员自举

**Files:**
- Modify: `src/core/app.py`

- [ ] **Step 1: 在 app.py 添加用户中间件和登录路由**

在 `app.py` 中：

**a)** 在 `from fastapi import FastAPI, Request` 行，加 `Response`:
```python
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
```

**b)** 在 `_SHARED_TEMPLATES` 之后，加导入：
```python
from src.core.auth import get_current_user, create_session, destroy_session
```

**c)** 在 `@app.get("/health")` 之前，添加用户中间件：

```python
@app.middleware("http")
async def _user_middleware(request: Request, call_next):
    """Attach current_user to request.state for all routes."""
    user = _get_session_mw(request)
    request.state.current_user = user
    response = await call_next(request)
    return response


def _get_session_mw(request: Request) -> dict | None:
    from src.core.auth import _get_session
    config = get_config()
    return _get_session(request, config)
```

注：为避免中间件重复创建 Redis 连接，复用 `auth.py` 的 `_get_session`。

**d)** 在 `@app.get("/health")` 之后，添加登录/登出路由：

```python
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    t = jinja_env.get_template("login.html")
    return HTMLResponse(t.render({"request": request}))


@app.post("/login")
def login_action(
    request: Request,
    config: Annotated[PlatformConfig, Depends(get_config)],
    username: str = Form(""),
    password: str = Form(""),
    redirect: str = Form("/"),
):
    from src.core.auth import create_session
    from src.core.database import get_db as _login_db
    import bcrypt as _bcrypt

    db = _login_db()
    sp = db.execute(
        "SELECT id, name, password_hash, role FROM salesperson WHERE name=? AND is_active=1",
        (username.strip(),),
    ).fetchone()

    if not sp or not sp["password_hash"]:
        return HTMLResponse(
            jinja_env.get_template("login.html").render({
                "request": request, "error": "用户名或密码错误"
            }), status_code=401)

    try:
        pw_ok = _bcrypt.checkpw(
            password.encode("utf-8"),
            sp["password_hash"].encode("utf-8") if isinstance(sp["password_hash"], str) else sp["password_hash"],
        )
    except Exception:
        pw_ok = False

    if not pw_ok:
        return HTMLResponse(
            jinja_env.get_template("login.html").render({
                "request": request, "error": "用户名或密码错误"
            }), status_code=401)

    session_id = create_session(config, {
        "id": sp["id"], "name": sp["name"], "role": sp["role"] or "salesperson",
    })
    resp = RedirectResponse(url=redirect.strip() or "/", status_code=303)
    resp.set_cookie("session_id", session_id, httponly=True, samesite="lax", max_age=86400)
    return resp


@app.post("/logout")
def logout_action(
    request: Request,
    config: Annotated[PlatformConfig, Depends(get_config)],
):
    from src.core.auth import destroy_session
    session_id = request.cookies.get("session_id", "")
    if session_id:
        destroy_session(config, session_id)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session_id")
    return resp
```

**e)** 在 `_start_imap_scheduler` 之前，添加管理员自举逻辑：

```python
@app.on_event("startup")
async def _bootstrap_admin() -> None:
    """Create default admin if none exists."""
    import bcrypt as _bcrypt
    import os as _os
    from src.core.database import get_db as _bootstrap_db

    db = _bootstrap_db()
    existing = db.execute(
        "SELECT 1 FROM salesperson WHERE role='admin' AND is_active=1"
    ).fetchone()
    if existing:
        return

    admin_pw = (_os.environ.get("ADMIN_PASSWORD") or "").strip()
    if not admin_pw:
        import secrets as _secrets
        admin_pw = _secrets.token_urlsafe(12)
        logger.warning("=" * 60)
        logger.warning("  No ADMIN_PASSWORD set. Generated admin password: %s", admin_pw)
        logger.warning("  Please change it after first login!")
        logger.warning("=" * 60)

    pw_hash = _bcrypt.hashpw(admin_pw.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    db.execute(
        "INSERT INTO salesperson (name, password_hash, role) VALUES ('admin', ?, 'admin')",
        (pw_hash,),
    )
    db.commit()
    logger.info("Admin account created: username=admin")
```

注：上面两个 `@app.on_event("startup")` 需要手动合为一个，因为 FastAPI 只支持每种事件一个 handler。在最终实现时会把 `_bootstrap_admin` 和 `_start_imap_scheduler` 合并到同一个 startup handler 中。

- [ ] **Step 2: Commit**

```bash
git add src/core/app.py
git commit -m "feat: 登录/登出路由 + 用户中间件 + 管理员自举"
```

---

### Task 4: 登录页面

**Files:**
- Create: `src/templates/login.html`

- [ ] **Step 1: 创建 login.html**

```html
<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>登录 - 外贸客户平台</title>
  <link rel="stylesheet" href="/static/css/platform.css" />
  <style>
    .login-wrapper {
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
      background: var(--bg-page, #0d1117);
    }
    .login-card {
      width: 100%; max-width: 400px; padding: 2.5rem 2rem;
      background: var(--bg-card, #161b22); border: 1px solid var(--border-input, #30363d);
      border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    }
    .login-card h1 {
      text-align: center; margin: 0 0 0.5rem; font-size: 1.5rem; color: var(--text-primary);
    }
    .login-card .subtitle {
      text-align: center; color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 1.5rem;
    }
    .login-card label {
      display: block; margin-bottom: 0.75rem; font-size: 0.85rem; color: var(--text-secondary);
    }
    .login-card input {
      width: 100%; padding: 0.6rem 0.75rem; margin-top: 0.25rem;
      border: 1px solid var(--border-input); border-radius: 4px;
      background: var(--bg-input); color: var(--text-primary); font-size: 0.95rem;
      box-sizing: border-box;
    }
    .login-card button {
      width: 100%; padding: 0.65rem; margin-top: 1rem;
      background: #238636; border: none; border-radius: 4px;
      color: white; font-size: 1rem; cursor: pointer; font-weight: 600;
    }
    .login-card button:hover { background: #2ea043; }
    .login-error {
      color: #f85149; font-size: 0.85rem; text-align: center; margin-bottom: 0.75rem;
      padding: 0.5rem; background: rgba(248,81,73,0.1); border-radius: 4px;
    }
  </style>
</head>
<body>
  <div class="login-wrapper">
    <div class="login-card">
      <h1>&#9951; 外贸客户平台</h1>
      <p class="subtitle">请使用您的销售账号登录</p>
      {% if error %}<div class="login-error">{{ error }}</div>{% endif %}
      <form method="post" action="/login">
        <input type="hidden" name="redirect" value="{{ request.query_params.get('redirect', '/') }}" />
        <label>用户名
          <input type="text" name="username" required autofocus autocomplete="username" />
        </label>
        <label>密码
          <input type="password" name="password" required autocomplete="current-password" />
        </label>
        <button type="submit">登 录</button>
      </form>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add src/templates/login.html
git commit -m "feat: 登录页面"
```

---

### Task 5: base.html 更新 — 用户信息 + 导航权限 + JS注入

**Files:**
- Modify: `src/templates/base.html`

- [ ] **Step 1: 修改 base.html**

**a)** 在导航栏 `.nav-actions` div 中（theme-toggle 按钮之前）添加用户信息和登出：

```html
<div class="nav-actions">
  {% set cu = request.state.current_user %}
  {% if cu %}
  <span class="nav-user" title="角色: {{ '管理员' if cu.role == 'admin' else '销售' }}">
    &#128100; {{ cu.name }}
  </span>
  <a href="#" onclick="document.getElementById('logoutForm').submit(); return false;" class="nav-logout">登出</a>
  <form id="logoutForm" method="post" action="/logout" style="display:none"></form>
  {% endif %}
```

**b)** 在导航链接循环中过滤 admin_only：

```html
{% for agent in nav_agents %}
{% if not agent.admin_only or (cu and cu.role == 'admin') %}
<li>
  <a href="/{{ agent.name }}/"
     class="nav-item {% if active_agent == agent.name %}active{% endif %}">
    ...
  </a>
</li>
{% endif %}
{% endfor %}
```

**c)** 在 `{% block scripts %}{% endblock %}` 之后、chat.js 脚本之前注入用户信息：

```html
<script>
window.__currentUser = {{ cu | tojson | safe if cu else 'null' }};
</script>
```

**d)** 在 base.html 的 CSS 中为 `.nav-user` 和 `.nav-logout` 加一点样式（或直接内联）：

在 `<style>` 标签或 platform.css 中添加：
```css
.nav-user { font-size: 0.82rem; color: var(--text-secondary); margin-right: 0.5rem; }
.nav-logout { font-size: 0.78rem; color: var(--text-muted); text-decoration: none; }
.nav-logout:hover { color: var(--color-danger); }
```

- [ ] **Step 2: Commit**

```bash
git add src/templates/base.html
git commit -m "feat: base.html加用户显示+登出+导航权限过滤+JS用户注入"
```

---

### Task 6: 客户评估 — admin_only 访问控制

**Files:**
- Modify: `src/agents/customer_eval/manifest.py`
- Modify: `src/agents/customer_eval/routes.py`

- [ ] **Step 1: manifest.py 加 admin_only**

在 `register()` 的 `AgentManifest` 构造中加一行：

```python
admin_only=True,
```

- [ ] **Step 2: routes.py — 全部路由改用 require_admin**

**a)** 修改 import：
```python
from src.core.auth import require_auth, require_admin
```
→
```python
from src.core.auth import require_admin
```

**b)** 将所有路由函数中的 `_: Annotated[None, Depends(require_auth)]` 替换为 `_: Annotated[None, Depends(require_admin)]`。

包括：
- `create_job`
- `continue_job`
- `list_eval_batches`（这里用的是不同的方式，需单独处理）
- `get_job_status`
- `cancel_job`
- `pause_job`
- `resume_job`
- `download_result`
- `url_eval`

对于路由 `list_eval_batches` 和 `get_job_status`，它们的函数签名略有不同，不加 `_` 参数，需要额外加 `_admin = Depends(require_admin)`。实际上可以用统一方式：在每个路由函数添加 `_admin: Annotated[None, Depends(require_admin)]`。

- [ ] **Step 3: Commit**

```bash
git add src/agents/customer_eval/manifest.py src/agents/customer_eval/routes.py
git commit -m "feat: 客户评估模块admin-only访问控制"
```

---

### Task 7: CRM — 数据隔离 + 销售管理 admin-only

**Files:**
- Modify: `src/agents/crm/routes.py`
- Modify: `src/agents/crm/static/js/crm.js`

- [ ] **Step 1: routes.py — 客户列表加数据隔离**

**a)** 修改 import：
```python
from src.core.auth import require_auth
```
→
```python
from src.core.auth import require_auth, require_admin, apply_sales_filter
```

**b)** 在 `list_customers` 函数中，将 `_: Annotated[None, Depends(require_auth)]` 改为：
```python
user: Annotated[dict, Depends(require_auth)],
```

然后在 `where_clause` 构建之前（即所有其他筛选条件添加完毕后、`"WHERE ...".join(where)` 之前）加：
```python
apply_sales_filter(where, params, user)
```

**c)** `get_customer` 函数改为接受 user：
```python
def get_customer(
    customer_id: int,
    user: Annotated[dict, Depends(require_auth)],
) -> JSONResponse:
```
并在 WHERE 条件加隔离：
```python
where_extra = ""
params_extra: list = []
if user["role"] == "salesperson":
    where_extra = " AND c.assigned_salesperson_id = ?"
    params_extra.append(user["id"])

row = db.execute(
    "SELECT c.*, s.name as salesperson_name, s.email as salesperson_email "
    "FROM customer c LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
    f"WHERE c.id=?{where_extra}", (customer_id, *params_extra)
).fetchone()
```

如果销售尝试访问不属于自己的客户，返回 404。

**d)** `export_customers` 加 `user` 和 `apply_sales_filter`。

**e)** 销售管理 CRUD API 加 `require_admin`：
- `create_salesperson`
- `update_salesperson`
- `delete_salesperson`
- `assign_customer`
- `batch_assign_customers`
- `gmail_auth_salesperson`

每个函数把 `_: Annotated[None, Depends(require_auth)]` 改为 `_: Annotated[None, Depends(require_admin)]`。

- [ ] **Step 2: crm.js — 销售隐藏销售筛选和控制按钮**

在 `crm.js` 的初始化逻辑（`DOMContentLoaded` 末尾或 `loadSalespersonData` 之后）添加：

```javascript
// 销售角色：隐藏销售筛选下拉、批量分配和快速分配按钮
if (window.__currentUser && window.__currentUser.role === 'salesperson') {
  const spFilter = document.getElementById('filterSalesperson');
  if (spFilter) spFilter.style.display = 'none';
  const afFilter = document.getElementById('afSalesperson');
  if (afFilter) afFilter.style.display = 'none';
  // 隐藏批量操作条
  const batchBar = document.querySelector('.batch-bar');
  if (batchBar) batchBar.style.display = 'none';
  // 隐藏每行快速分配下拉（在渲染后处理）
  document.querySelectorAll('.quick-assign-cell select').forEach(s => s.style.display = 'none');
}
```

同时需要确保 `loadSalespersonData` 在销售角色时不尝试加载销售列表（避免 403 错误）：
```javascript
if (window.__currentUser && window.__currentUser.role === 'salesperson') {
  return; // 销售角色不需要加载销售列表
}
```

- [ ] **Step 3: Commit**

```bash
git add src/agents/crm/routes.py src/agents/crm/static/js/
git commit -m "feat: CRM客户数据隔离+销售管理API admin-only"
```

---

### Task 8: CRM 销售表单 — 加密码和角色字段

**Files:**
- Modify: `src/agents/crm/templates/crm_salespersons.html`
- Modify: `src/agents/crm/static/js/crm_salespersons.js`

- [ ] **Step 1: HTML 表单加密码和角色字段**

在 `crm_salespersons.html` 的表单中（姓名/邮箱/电话字段之后、"邮箱绑定"折叠块之前），添加密码和角色字段：

```html
<label style="display:block; margin-bottom:0.8rem; font-size:0.85rem; color:var(--text-secondary)">
  登录密码
  <input type="password" id="spPassword" placeholder="留空则不修改" style="width:100%; margin-top:0.25rem; padding:0.55rem 0.7rem; border-radius:var(--radius-sm);
    border:var(--border-input); background:var(--bg-input); color:var(--text-primary); font-family:var(--font-body); font-size:0.9rem" />
</label>
<label style="display:block; margin-bottom:1rem; font-size:0.85rem; color:var(--text-secondary)">
  角色
  <select id="spRole" style="width:100%; margin-top:0.25rem; padding:0.55rem 0.7rem; border-radius:var(--radius-sm);
    border:var(--border-input); background:var(--bg-input); color:var(--text-primary); font-family:var(--font-body); font-size:0.9rem">
    <option value="salesperson">销售</option>
    <option value="admin">管理员</option>
  </select>
</label>
```

- [ ] **Step 2: JS 表单逻辑 — 密码和角色字段的读写**

在 `crm_salespersons.js` 中：

**a)** `showAddModal`：清空密码和角色字段
```javascript
document.getElementById('spPassword').value = '';
document.getElementById('spRole').value = 'salesperson';
```

**b)** `editSalesperson`：从数据填充
```javascript
document.getElementById('spPassword').value = '';
document.getElementById('spRole').value = sp.role || 'salesperson';
```

**c)** `saveSalesperson`：FormData 中加 password 和 role
```javascript
const pw = document.getElementById('spPassword').value;
if (pw) fd.append('password', pw);
fd.append('role', document.getElementById('spRole').value);
```

- [ ] **Step 3: routes.py — 后端 API 接受 password 和 role**

在 `create_salesperson` 函数参数中加：
```python
password: str = Form(""),
role: str = Form("salesperson"),
```

并在 INSERT 中处理密码哈希：
```python
import bcrypt as _bcrypt
pw_hash = None
if password.strip():
    pw_hash = _bcrypt.hashpw(password.strip().encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
```

SQL 改为包含 password_hash 和 role：
```python
cur = db.execute(
    "INSERT INTO salesperson (name, email, phone, password_hash, role, smtp_host, smtp_port, "
    "smtp_username, smtp_password, imap_host, imap_port, wework_userid) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (name.strip(), email.strip(), phone.strip(),
     pw_hash, role.strip(),
     smtp_host.strip(), int(smtp_port) if smtp_port.strip() else 587,
     smtp_username.strip(), smtp_password.strip(),
     imap_host.strip(), int(imap_port) if imap_port.strip() else 993,
     wework_userid.strip()),
)
```

在 `update_salesperson` 函数中同样加参数：
```python
password: str = Form(None),
role: str = Form(None),
```

并在更新逻辑中处理：
```python
if password is not None and password.strip():
    import bcrypt as _bcrypt
    pw_hash = _bcrypt.hashpw(password.strip().encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    sets.append("password_hash=?")
    params.append(pw_hash)
if role is not None and role.strip():
    sets.append("role=?")
    params.append(role.strip())
```

在 `list_salespersons` 的 SELECT 中加 `s.role`。
在 `create_salesperson` 的返回中也加 role。

- [ ] **Step 4: Commit**

```bash
git add src/agents/crm/templates/crm_salespersons.html src/agents/crm/static/js/crm_salespersons.js src/agents/crm/routes.py
git commit -m "feat: 销售管理表单加密码和角色字段"
```

---

### Task 9: 询盘邮件 — 数据隔离

**Files:**
- Modify: `src/agents/inquiry_mail/routes.py`
- Modify: `src/agents/inquiry_mail/static/js/mail.js`

- [ ] **Step 1: routes.py — 客户列表 API 加隔离**

**a)** 修改 import：
```python
from src.core.auth import require_auth
```
→
```python
from src.core.auth import require_auth, require_admin, apply_sales_filter
```

**b)** `get_emailable_customers` 把 `_: Annotated[None, Depends(require_auth)]` 改为：
```python
user: Annotated[dict, Depends(require_auth)],
```

然后在 `where_clause` 构建前加：
```python
apply_sales_filter(where, params, user)
```

对于销售角色，如果前端仍传了 `salesperson_id` 参数，忽略它（或确保它锁定为自己的 id）。

**c)** `get_saved_emails` 同样加 `user` 和 `apply_sales_filter`。

**d)** `generate_emails`、`send_emails`、`confirm_emails` 等操作类 API 保持不变（它们操作的是已隔离的客户列表中的选中项）。

- [ ] **Step 2: mail.js — 销售隐藏/锁定销售筛选**

```javascript
// 页面加载时
if (window.__currentUser && window.__currentUser.role === 'salesperson') {
  const spFilter = document.getElementById('filterSalesperson');
  if (spFilter) spFilter.parentElement.style.display = 'none';
}
```

- [ ] **Step 3: Commit**

```bash
git add src/agents/inquiry_mail/routes.py src/agents/inquiry_mail/static/js/mail.js
git commit -m "feat: 询盘邮件客户列表数据隔离"
```

---

### Task 10: 社媒管理 — 数据隔离 + 销售筛选

**Files:**
- Modify: `src/agents/social_media/routes.py`
- Modify: `src/agents/social_media/templates/social_list.html`
- Modify: `src/agents/social_media/static/js/social.js`

- [ ] **Step 1: routes.py — 加隔离 + 销售筛选**

**a)** 修改 import：
```python
from src.core.auth import require_auth
```
→
```python
from src.core.auth import require_auth, require_admin, apply_sales_filter
```

**b)** `list_social_customers` 把 `_: Annotated[None, Depends(require_auth)]` 改为：
```python
user: Annotated[dict, Depends(require_auth)],
```

加参数：
```python
salesperson_id: str = Query(""),
```

在 `has_social` 条件后加：
```python
# 销售只能看到分配给自己的客户
apply_sales_filter(where, params, user)

if salesperson_id.strip():
    if salesperson_id.strip().lower() == "unassigned":
        where.append("c.assigned_salesperson_id IS NULL")
    else:
        where.append("c.assigned_salesperson_id = ?")
        params.append(int(salesperson_id))
```

SELECT 中加 `c.assigned_salesperson_id, COALESCE(s.name, '') as salesperson_name`，并加 `LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id`。

- [ ] **Step 2: social_list.html — 加销售筛选下拉**

在 filter bar 中（`filterHasSocial` 之后）添加：
```html
<select id="filterSalesperson" onchange="reloadCustomers()" class="fs-select">
  <option value="">全部销售</option>
</select>
```

在 JS 中加载销售列表来填充这个下拉。

- [ ] **Step 3: social.js — 销售筛选交互**

**a)** `loadCustomers` 中加 salesperson_id 参数：
```javascript
const spId = el('filterSalesperson').value;
if (spId) params.set('salesperson_id', spId);
```

**b)** 页面加载时根据角色隐藏/显示：
```javascript
if (window.__currentUser && window.__currentUser.role === 'salesperson') {
  const spFilter = document.getElementById('filterSalesperson');
  if (spFilter) spFilter.parentElement.style.display = 'none';
}
```

**c)** 加载销售列表填充下拉（仅管理员可见时有用）：
```javascript
async function loadSalespersonFilter() {
  if (!window.__currentUser || window.__currentUser.role !== 'admin') return;
  try {
    const r = await apiFetch('/crm/api/salespersons');
    if (r.ok) {
      const list = await r.json();
      const sel = el('filterSalesperson');
      list.forEach(sp => {
        const opt = document.createElement('option');
        opt.value = sp.id;
        opt.textContent = sp.name;
        sel.appendChild(opt);
      });
    }
  } catch (_) {}
}
```

在 `DOMContentLoaded` 中调用 `loadSalespersonFilter()`。

- [ ] **Step 4: Commit**

```bash
git add src/agents/social_media/routes.py src/agents/social_media/templates/social_list.html src/agents/social_media/static/js/social.js
git commit -m "feat: 社媒管理数据隔离+销售筛选"
```

---

### Task 11: 最终验证

**验证清单（不重启服务，只验证代码正确性）：**

- [ ] **Step 1: Python 语法检查**

```bash
python -m py_compile src/core/auth.py
python -m py_compile src/core/app.py
python -m py_compile src/agents/crm/routes.py
python -m py_compile src/agents/inquiry_mail/routes.py
python -m py_compile src/agents/social_media/routes.py
python -m py_compile src/agents/customer_eval/routes.py
```

Expected: 每个命令返回码为 0，无输出。

- [ ] **Step 2: 数据库迁移验证**

```bash
python -c "from src.core.database import get_db; db=get_db(); cols=[r[1] for r in db.execute('PRAGMA table_info(salesperson)')]; assert 'password_hash' in cols; assert 'role' in cols; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: bcrypt 可用性验证**

```bash
python -c "import bcrypt; h=bcrypt.hashpw(b'test', bcrypt.gensalt()); assert bcrypt.checkpw(b'test', h); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git commit -m "verify: 权限系统语法检查和依赖验证通过" --allow-empty
```

---

## 部署注意事项

所有代码改动部署完成后，需要重启 Web 服务（`python -m uvicorn`），RQ Worker 不受影响可继续运行。

现有销售账号需要管理员在"销售管理"页面为他们设置登录密码后才能登录。
