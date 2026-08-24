#!/usr/bin/env bash
# ============================================================
# 轻量应用服务器一键部署脚本（Ubuntu 22.04，root 执行）
# 用法：
#   sudo bash server-setup.sh <完整镜像地址> [JWT密钥]
# 示例：
#   sudo bash server-setup.sh registry.cn-hangzhou.aliyuncs.com/myns/stock-review:v0.1.0
#
# 脚本自包含：装 Docker → 登录 ACR → 生成 compose 与 .env → 拉取启动
# 数据持久化在 /opt/stock-review/data（迁移正式服务器 = 拷贝该目录）
# ============================================================
set -euo pipefail

IMAGE="${1:?用法: sudo bash server-setup.sh <完整镜像地址> [JWT密钥]}"
SECRET="${2:-}"
APP_DIR="/opt/stock-review"

# ---- 1. 安装 Docker（已装则跳过）----
if ! command -v docker &>/dev/null; then
    echo "==> 安装 Docker..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg >/dev/null
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://mirrors.aliyun.com/docker-ce/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
    systemctl enable --now docker
    # Docker Hub 国内加速（ACR 拉取不受影响，备用）
    mkdir -p /etc/docker
    cat > /etc/docker/daemon.json <<'EOF'
{ "registry-mirrors": ["https://docker.mirrors.aliyun.com"] }
EOF
    systemctl restart docker
else
    echo "==> Docker 已安装：$(docker --version)"
fi

# ---- 2. 登录 ACR（镜像非本地时）----
REGISTRY_HOST=$(echo "$IMAGE" | cut -d/ -f1)
if [[ "$REGISTRY_HOST" == *"aliyuncs.com"* ]]; then
    echo "==> 登录 ACR（$REGISTRY_HOST，输入阿里云账号 + ACR 固定密码）..."
    docker login "$REGISTRY_HOST"
fi

# ---- 3. 生成部署目录与配置 ----
mkdir -p "$APP_DIR/data"
# 容器以 UID 1000（appuser）运行：宿主机数据目录必须归 1000 所有，否则 SQLite 无法写入
chown -R 1000:1000 "$APP_DIR/data"
cd "$APP_DIR"

if [[ -z "$SECRET" ]]; then
    SECRET=$(openssl rand -hex 24)
    echo "==> 已自动生成 JWT 密钥（记录于 $APP_DIR/.env）"
fi

cat > .env <<EOF
IMAGE=$IMAGE
STOCK_REVIEW_SECRET=$SECRET
STOCK_REVIEW_TOKEN_TTL=720
HOST_PORT=8000
EOF
chmod 600 .env

cat > docker-compose.yml <<'EOF'
services:
  web:
    image: ${IMAGE}
    container_name: stock-review
    restart: unless-stopped
    ports:
      - "${HOST_PORT:-8000}:8000"
    environment:
      STOCK_REVIEW_SECRET: ${STOCK_REVIEW_SECRET:?need secret}
      STOCK_REVIEW_TOKEN_TTL: ${STOCK_REVIEW_TOKEN_TTL:-720}
      TZ: Asia/Shanghai
    volumes:
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/api/health"]
      interval: 30s
      timeout: 5s
      start_period: 20s
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
EOF

# ---- 4. 拉取并启动 ----
echo "==> 拉取镜像并启动..."
docker compose --env-file .env up -d --pull always

# ---- 5. 验证 ----
sleep 8
if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo ""
    echo "=========================================="
    echo " 部署成功！"
    echo " 访问地址：http://$(curl -s ifconfig.me 2>/dev/null || echo '服务器公网IP'):8000"
    echo " 数据目录：$APP_DIR/data（迁移正式服务器时整体拷贝）"
    echo " 常用命令（在 $APP_DIR 下执行）："
    echo "   查看日志: docker compose logs -f"
    echo "   重启服务: docker compose restart"
    echo "   版本升级: 修改 .env 中 IMAGE 的 tag 后"
    echo "             docker compose --env-file .env up -d --pull always"
    echo "=========================================="
    echo " 注意：请在轻量服务器控制台【防火墙】放行 TCP 8000 端口"
    echo "       建议同时限制来源 IP（仅放行你的常用网络）"
else
    echo "!! 健康检查未通过，查看日志排查：" >&2
    docker compose logs --tail 50 >&2 || true
    exit 1
fi
