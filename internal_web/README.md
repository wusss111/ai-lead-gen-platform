# 公司内部网页版（客户评估）

## 架构

- **FastAPI**：上传 xlsx、查询任务、下载结果。
- **Redis + RQ**：异步执行 `tools.pipeline.runner.run_pipeline`，避免浏览器超时。
- **Docker Compose**：`web`、`worker`、`redis` 三容器。

## 环境变量

与 CLI 共用：`DEEPSEEK_API_KEY`，可选 `CATALOG_PATH`、`PRODUCT_KB_PATH`、`CACHE_DIR`（见仓库根 `.env.example`）。

网页专用：

| 变量 | 说明 |
|------|------|
| `REDIS_URL` | Redis 连接串，Compose 内默认为 `redis://redis:6379/0` |
| `INTERNAL_WEB_DATA_DIR` | 上传与结果目录，Compose 内为 `/data` |
| `INTERNAL_WEB_BASIC_USER` / `INTERNAL_WEB_BASIC_PASSWORD` | 若均设置则启用 HTTP Basic，全员共用一组账号 |
| `INTERNAL_WEB_MAX_UPLOAD_MB` | 上传大小上限，默认 32 |
| `INTERNAL_WEB_MAX_ROWS` | 单次最大行数，默认 500 |
| `INTERNAL_WEB_QUEUE_NAME` | RQ 队列名，默认 `eval_jobs` |

## 本地开发（不装 Docker）

1. 安装并启动 Redis（Windows 用户可直接用仓库自带的 `var/redis/redis-server.exe`）。
2. 终端 A（RQ Worker）：仓库根目录执行  
   `rq worker -u redis://127.0.0.1:6379/0 eval_jobs --worker-class rq.SimpleWorker`  
   **Windows 注意**：必须使用 `--worker-class rq.SimpleWorker`，因为 RQ 默认的 fork 模式不支持 Windows。
3. 终端 B（Web 服务）：仓库根目录执行  
   `python -m uvicorn internal_web.main:app --reload --host 127.0.0.1 --port 8000`
4. 打开 http://127.0.0.1:8000/health 与首页。

**Windows 一键启动**：双击仓库根目录的 `start_all.bat`，自动启动 Redis + Worker + Web 并打开浏览器。

## Docker Compose

仓库根目录：

```bash
docker compose up -d --build
```

确保镜像内存在 `output/catalog.json`（构建镜像前在宿主机生成并纳入 `COPY`，或对 `./output` 做只读挂载）。

上传表格需已包含流水线要求的列（可用 `tools/map_zh_customer_sheet.py` 先映射中文表头）。
