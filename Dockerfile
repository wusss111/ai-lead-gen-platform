# 客户评估内部 Web + RQ Worker 共用镜像
# 若无法直连 Docker Hub，可在 compose 或 build 时传入镜像，例如：
#   docker compose build --build-arg PYTHON_IMAGE=你的镜像/python:3.11-slim-bookworm
ARG PYTHON_IMAGE=python:3.11-slim-bookworm
FROM ${PYTHON_IMAGE}

WORKDIR /app

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
