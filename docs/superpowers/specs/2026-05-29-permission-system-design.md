# 权限系统设计

## 概述

为平台增加多用户登录和权限控制。管理员可以创建销售账号、分配客户；销售登录后只能看到分配给自己的客户数据，且无法访问客户评估模块。

## 数据库改动

在 `salesperson` 表加两个字段：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `password_hash` | TEXT | NULL | bcrypt 哈希密码 |
| `role` | TEXT | `'salesperson'` | `admin` / `salesperson` |

迁移方式：`database.py` 中的 `_ensure_column` 自动添加，无需手动执行 SQL。

## 认证流程

### 废弃 HTTP Basic Auth

删除现有 `src/core/auth.py` 中的 `require_auth`（HTTPBasic 依赖），改为 Session 方案。

### Session 管理

- 后端：Redis 存储 session（key: `session:{session_id}` → JSON `{user_id, name, role}`），TTL 24 小时
- 前端：Cookie 存 `session_id`（HttpOnly, SameSite=Lax）
- 登录 API：`POST /login` — 验证用户名+密码，创建 session，Set-Cookie
- 登出 API：`POST /logout` — 删除 Redis session，清除 Cookie
- 登录页面：`GET /login` — 返回 `login.html`

### 初始化管理员

应用启动时检测 `salesperson` 表中无 `role='admin'` 的记录，则自动创建：
- 用户名：`admin`
- 密码：从环境变量 `ADMIN_PASSWORD` 读取，若未设置则随机生成并打印到控制台

## 权限依赖注入

替换现有的 `require_auth`，在 `src/core/auth.py` 中提供两个依赖：

```python
def get_current_user(request: Request, config: PlatformConfig) -> dict:
    """从 Cookie session_id 读取 Redis session，返回 {id, name, role}。未登录抛 401。"""

def require_auth(user = Depends(get_current_user)) -> dict:
    """需要登录，所有登录用户可访问。返回当前用户信息。"""

def require_admin(user = Depends(require_auth)) -> dict:
    """需要管理员角色，否则抛 403。用于客户评估模块。"""
```

路由使用方式：
```python
@router.get("/api/customers")
def list_customers(user: Annotated[dict, Depends(require_auth)]):
    # user["id"], user["name"], user["role"]
```

## 数据隔离

在每个模块的列表 API 中，根据 `user["role"]` 自动追加过滤条件：

```python
# 位置：src/core/auth.py（与权限依赖放在一起，方便各模块 import）

def _apply_sales_filter(where: list, params: list, user: dict, table_alias: str = "c"):
    """销售只能看到分配给自己的客户，管理员看到全部。"""
    if user["role"] == "salesperson":
        where.append(f"{table_alias}.assigned_salesperson_id = ?")
        params.append(user["id"])
```

### 各模块改动清单

| 模块 | 文件 | API | 改动 |
|------|------|-----|------|
| 认证 | `src/core/auth.py` | 全部重写 | Session 管理 + 两个依赖 |
| 登录 | `src/templates/login.html` | 新增 | 登录表单页面 |
| 页面布局 | `src/templates/base.html` | — | 右上角显示用户名+登出；导航按 role 过滤 |
| 平台核心 | `src/core/app.py` | `/login`, `/logout` | 注册登录路由 |
| CRM | `crm/routes.py` | `list_customers`, `get_customer`, 导出等 | 加 `_apply_sales_filter`；`batch-assign` 加 `require_admin` |
| 询盘邮件 | `inquiry_mail/routes.py` | `get_emailable_customers`, `get_saved_emails` | 加 `_apply_sales_filter` |
| 社媒管理 | `social_media/routes.py` | `list_social_customers` | 加 `_apply_sales_filter` 和销售筛选下拉 |
| 客户评估 | `customer_eval/routes.py` | 全部路由 | `require_auth` → `require_admin` |
| 销售管理 | `crm/routes.py` | 销售 CRUD API | 加 `require_admin`；表单增加密码和角色字段 |
| Agent 清单 | `manifest.py`（3 个） | — | 无需改动，权限在路由层控制 |
| 前端 JS | 各 `static/js/` | — | 销售登录后隐藏销售筛选下拉（自动锁定为自己） |

### 销售筛选下拉框行为

- **管理员**：保持现有的下拉选择功能，可以筛选查看任意销售的客户
- **销售**：下拉框隐藏或锁定为"自己"，前端不显示筛选选项

## 前端改动

### 登录页 (`templates/login.html`)
- 居中卡片式布局
- 用户名 + 密码 + 登录按钮
- 复用 PicoCSS 样式

### base.html
- 右上角：`👤 {用户名}` + `[登出]`
- 导航栏循环时过滤：`admin_only` 的 Agent 对销售隐藏

### Agent Manifest

```python
@dataclass
class AgentManifest:
    # 新增字段
    admin_only: bool = False  # True = 仅管理员可见（导航栏过滤）
```

`customer_eval/manifest.py` 设置 `admin_only=True`。

## 安全注意事项

- 密码用 `bcrypt` 哈希存储（通过 `pip install bcrypt`）
- Session ID 用 `secrets.token_urlsafe(32)` 生成
- Cookie 设置 `HttpOnly=True, SameSite=Lax` 防 XSS
- 销售管理页和客户评估页都是 admin-only
- 所有 API 都经过 `require_auth` 依赖注入，无绕过可能

## 兼容性

- `database.py` 的 `_ensure_column` 保证旧库升级时自动加字段
- 现有 HTTP Basic Auth 被完全移除（环境变量 `BASIC_USER`/`BASIC_PASSWORD` 不再使用）
- 现有 RQ Worker 不需要改动（Worker 不走 HTTP 认证）
