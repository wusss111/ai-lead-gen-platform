#!/usr/bin/env bash
# ============================================================
#  AI 获客平台 — 云服务器一键部署脚本
# ============================================================
#  适用: Ubuntu 22.04+ / Debian 12+
#  用法: chmod +x setup_server.sh && sudo ./setup_server.sh
#  耗时: 首次运行约 5-10 分钟（取决于网络）
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
REPO_URL="https://github.com/wusss111/ai-lead-gen-platform.git"
APP_DIR="/opt/ai-lead-platform"

echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  AI 获客平台 — 服务器部署脚本${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""

# ---- 1. 检测系统 ----
if [ "$(id -u)" -ne 0 ]; then
    echo "请用 sudo 运行: sudo ./setup_server.sh"
    exit 1
fi

UBUNTU_VERSION=$(lsb_release -rs 2>/dev/null || echo "0")
echo "[1/7] 检测系统... Ubuntu ${UBUNTU_VERSION}"

# ---- 2. 安装 Docker ----
echo "[2/7] 安装 Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker
    systemctl start docker
    echo "Docker 安装完成"
else
    echo "Docker 已安装: $(docker --version)"
fi

# ---- 3. 安装 Docker Compose 插件 ----
echo "[3/7] 安装 Docker Compose..."
if ! docker compose version &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq docker-compose-plugin
    echo "Docker Compose 安装完成"
else
    echo "Docker Compose 已安装"
fi

# ---- 4. 拉取代码 ----
echo "[4/7] 拉取代码..."
if [ -d "${APP_DIR}" ]; then
    echo "目录已存在，更新代码..."
    cd "${APP_DIR}"
    git pull origin master || echo "  Git pull 失败，使用现有代码继续"
else
    git clone "${REPO_URL}" "${APP_DIR}"
    cd "${APP_DIR}"
fi

# ---- 5. 配置环境变量 ----
echo "[5/7] 配置环境变量..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo -e "${YELLOW}============================================================${NC}"
    echo -e "${YELLOW}  请现在编辑 .env 文件，填入你的配置：${NC}"
    echo -e "${YELLOW}  nano .env${NC}"
    echo -e "${YELLOW}                                                           ${NC}"
    echo -e "${YELLOW}  必填项：DEEPSEEK_API_KEY${NC}"
    echo -e "${YELLOW}  建议项：BASIC_USER / BASIC_PASSWORD（访问密码）${NC}"
    echo -e "${YELLOW}  邮件项：SMTP_* 或配置 Gmail OAuth${NC}"
    echo -e "${YELLOW}============================================================${NC}"
    echo ""
    echo -e "${YELLOW}按 Enter 继续（请确认已编辑 .env）...${NC}"
    read -r
else
    echo ".env 已存在，跳过"
fi

# ---- 6. 启动服务 ----
echo "[6/7] 构建并启动所有服务..."
docker compose pull 2>/dev/null || true
docker compose build --quiet
docker compose up -d

echo "等待服务就绪..."
sleep 10

# ---- 7. 配置防火墙 ----
echo "[7/7] 配置防火墙..."
if command -v ufw &> /dev/null; then
    ufw allow 80/tcp comment 'HTTP' 2>/dev/null || true
    ufw allow 443/tcp comment 'HTTPS' 2>/dev/null || true
    ufw allow 22/tcp 2>/dev/null || true
    ufw --force enable 2>/dev/null || true
    echo "防火墙已配置"
else
    echo "  (未检测到 ufw，请手动配置云服务器安全组：放行 80、443 端口)"
fi

# ---- 完成 ----
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || echo "你的服务器IP")
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  部署完成！${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo "  访问地址: http://${SERVER_IP}"
echo "  健康检查: http://${SERVER_IP}/health"
echo ""
echo "  常用命令:"
echo "    cd ${APP_DIR}"
echo "    docker compose ps                    # 查看服务状态"
echo "    docker compose logs -f web           # 查看 Web 日志"
echo "    docker compose logs -f worker-eval   # 查看 Worker 日志"
echo "    docker compose restart web           # 重启 Web"
echo "    docker compose down && docker compose up -d  # 重建所有服务"
echo "    docker compose exec web python -c '...'   # 进入容器执行命令"
echo ""
echo "  配置 HTTPS:"
echo "    1. 将域名 DNS 解析到 ${SERVER_IP}"
echo "    2. 安装 certbot: sudo apt install certbot"
echo "    3. 申请证书: sudo certbot certonly --standalone -d 你的域名"
echo "    4. 复制证书: sudo cp /etc/letsencrypt/live/你的域名/fullchain.pem deploy/ssl/"
echo "       sudo cp /etc/letsencrypt/live/你的域名/privkey.pem deploy/ssl/"
echo "    5. 取消 nginx.conf 中 HTTPS 部分的注释"
echo "    6. 重启: docker compose restart nginx"
echo ""
