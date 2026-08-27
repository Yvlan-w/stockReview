#!/usr/bin/env bash
# ============================================================
# 非首次部署：升级到新镜像版本（保留已有数据绝不重写）。
#
# 前置检查（你手动执行，脚本不会代做）：
#   [ ] 轻量服务器控制台 → 快照 → 创建部署前快照（命名建议 pre-<版本>-<日期>）
#   [ ] ACR 已登录（docker login <registry>）
#
# 用法：
#   sudo bash server-upgrade.sh <IMAGE_FULL_TAG>
#   sudo bash server-upgrade.sh crpi-xxx.cn-shanghai.personal.cr.aliyuncs.com/ns/stock-review:v0.1.3
#
# 特性：
#   · 不覆盖 .env 已存在的键（只写缺失项）
#   · compose 原子替换（tmp 文件 diff 后 mv）
#   · 部署前自动 dump schema 文本到 deploy_history（不拷贝 db 文件）
#   · health 3 轮不过，自动把 IMAGE 回滚到 .env 里启动前的值并重 up（R1 轻量回滚）
# ============================================================
set -euo pipefail

IMAGE_NEW="${1:-}"
APP_DIR="/opt/stock-review"
HISTORY_DIR="$APP_DIR/deploy_history"
COMPOSE_FILE="$APP_DIR/docker-compose.yml"
ENV_FILE="$APP_DIR/.env"

if [[ -z "$IMAGE_NEW" ]]; then
  echo "用法: sudo bash $0 <IMAGE_FULL_TAG>"
  echo "示例: sudo bash $0 crpi-xxx.cn-shanghai.personal.cr.aliyuncs.com/ns/stock-review:v0.1.3"
  exit 1
fi
if [[ ! -d "$APP_DIR" || ! -f "$COMPOSE_FILE" ]]; then
  echo "错误：$APP_DIR 未初始化，先用 server-setup.sh 完成首次部署"
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "建议用 sudo 运行（读写 $APP_DIR 需要 root）"
fi

cd "$APP_DIR"
REGISTRY_HOST="${IMAGE_NEW%%/*}"

# ---- 0. 预检查：ACR 登录有效 ----
echo "[0/7] 登录检查：$REGISTRY_HOST"
if ! docker info >/dev/null 2>&1; then
  echo "Docker 未运行？尝试:  systemctl start docker"
  exit 1
fi

# ---- 1. 记录升级前版本 ----
IMAGE_OLD="$(grep -E '^IMAGE=' "$ENV_FILE" | head -n1 | sed 's|^IMAGE=||')"
if [[ -z "$IMAGE_OLD" ]]; then
  echo "警告：.env 中未找到 IMAGE=，无法提供 R1 自动回滚"
fi
echo "[1/7] 升级前镜像：${IMAGE_OLD:-(未记录)}  →  新镜像：$IMAGE_NEW"

# ---- 2. 部署前结构快照（schema text dump，几KB，纯审计用）----
echo "[2/7] 结构快照（SQL schema text dump）..."
mkdir -p "$HISTORY_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
SCHEMA_FILE="$HISTORY_DIR/schema-pre-${IMAGE_NEW##*:}-${TS}.sql"
DB_FILE="$APP_DIR/data/stock_review.db"
if [[ -f "$DB_FILE" ]]; then
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_FILE" .schema > "$SCHEMA_FILE"
  else
    # sqlite3 命令未装：用容器内 sqlite3 兼容输出（用新镜像的 python）
    docker run --rm -v "$APP_DIR/data:/app/data" --entrypoint python "$IMAGE_NEW" -c "
import sqlite3,sys
c=sqlite3.connect('/app/data/stock_review.db')
for row in c.execute(\"SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name\"):
    print(row[0]+';')
" > "$SCHEMA_FILE" 2>/dev/null || echo "（schema dump 跳过：不阻塞升级）"
  fi
  DB_BEFORE_USERS=$(docker run --rm -v "$APP_DIR/data:/app/data" --entrypoint python "$IMAGE_NEW" -c "
import sqlite3
c=sqlite3.connect('/app/data/stock_review.db')
print(c.execute('SELECT COUNT(*) FROM users').fetchone()[0])
" 2>/dev/null || echo "?")
  DB_BEFORE_CLIENTS=$(docker run --rm -v "$APP_DIR/data:/app/data" --entrypoint python "$IMAGE_NEW" -c "
import sqlite3
c=sqlite3.connect('/app/data/stock_review.db')
print(c.execute('SELECT COUNT(*) FROM clients').fetchone()[0])
" 2>/dev/null || echo "?")
  echo "      users=$DB_BEFORE_USERS  clients=$DB_BEFORE_CLIENTS"
else
  echo "      未找到 $DB_FILE（首次升级空数据路径正常）"
  DB_BEFORE_USERS=0; DB_BEFORE_CLIENTS=0
fi

# ---- 3. 更新 .env（只加缺失键 + 原子替换 IMAGE）----
echo "[3/7] 安全更新 .env（只写缺失键，IMAGE 行直接替换指向新版本）"
ensure_key() {
  local key="$1" default_val="$2"
  if ! grep -qE "^${key}=" "$ENV_FILE"; then
    echo "${key}=${default_val}" >> "$ENV_FILE"
    echo "      + 新增 ${key}=${default_val}"
  fi
}
ensure_key STOCK_REVIEW_RUN_SEED first
ensure_key STOCK_REVIEW_TOKEN_TTL 720
ensure_key HOST_PORT 8000
# STOCK_REVIEW_SECRET 和 STOCK_REVIEW_ADMIN_PASSWORD 绝不自动补默认值（secret 靠人工，admin密码有代码默认）

# IMAGE 行替换（保留注释）
if grep -qE '^IMAGE=' "$ENV_FILE"; then
  sed -i "s|^IMAGE=.*|IMAGE=${IMAGE_NEW}|" "$ENV_FILE"
else
  echo "IMAGE=${IMAGE_NEW}" >> "$ENV_FILE"
fi

# ---- 4. docker-compose.yml 原子替换：对比差异后再覆盖 ----
echo "[4/7] 原子替换 docker-compose.yml（diff 检查）..."
TMP_COMPOSE="${COMPOSE_FILE}.new.${TS}"
# 这里假设服务器端 compose 内容与仓库 deploy/docker-compose.yml 一致
# 若不一致（老部署结构），优先写新内容，diff 仅用于打印人工巡检
cat > "$TMP_COMPOSE" <<'COMPOSE_EOF'
# ============================================================
# 持仓复盘 · 服务器部署编排（非首次由 server-upgrade.sh 生成）
# ============================================================
services:
  web:
    image: ${IMAGE:-stock-review:local}
    container_name: stock-review
    restart: unless-stopped
    ports:
      - "${HOST_PORT:-8000}:8000"
    environment:
      STOCK_REVIEW_SECRET: ${STOCK_REVIEW_SECRET:?请在 .env 中设置 STOCK_REVIEW_SECRET}
      STOCK_REVIEW_TOKEN_TTL: ${STOCK_REVIEW_TOKEN_TTL:-720}
      STOCK_REVIEW_RUN_SEED: ${STOCK_REVIEW_RUN_SEED:-first}
      STOCK_REVIEW_ADMIN_PASSWORD: ${STOCK_REVIEW_ADMIN_PASSWORD:-}
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
COMPOSE_EOF

if [[ -f "$COMPOSE_FILE" ]]; then
  DIFF_OUT="$(diff -u "$COMPOSE_FILE" "$TMP_COMPOSE" 2>/dev/null || true)"
  if [[ -n "$DIFF_OUT" ]]; then
    echo "      compose 差异："
    echo "$DIFF_OUT" | sed 's/^/        | /' | head -n 30
  else
    echo "      （compose 内容无变化）"
  fi
fi
mv "$TMP_COMPOSE" "$COMPOSE_FILE"

# ---- 5. 拉取并启动 ----
echo "[5/7] 拉取 $IMAGE_NEW 并启动新容器..."
docker compose --env-file .env pull
docker compose --env-file .env up -d

# ---- 6. 健康检查 3 轮（不过自动 R1 回滚）----
echo "[6/7] 健康检查（最多 3 轮 x 6 秒）..."
HEALTH_OK=0
for i in 1 2 3; do
  sleep 6
  CODE="$(curl -o /dev/null -s -w '%{http_code}' http://127.0.0.1:8000/api/health || true)"
  if [[ "$CODE" == "200" ]]; then
    HEALTH_OK=1; echo "      第 $i 轮 HTTP /api/health = 200  ✅"; break
  else
    echo "      第 $i 轮 HTTP /api/health = $CODE  ⚠️"
  fi
done

if [[ $HEALTH_OK -ne 1 ]]; then
  echo "!! 健康检查未通过，执行 R1 回滚镜像版本：${IMAGE_OLD:-(缺失旧版本信息，回滚失败)}"
  if [[ -n "$IMAGE_OLD" ]]; then
    sed -i "s|^IMAGE=.*|IMAGE=${IMAGE_OLD}|" "$ENV_FILE"
    docker compose --env-file .env pull || true
    docker compose --env-file .env up -d
    echo "      R1 回滚已提交，请等待 30 秒后人工 curl /api/health 检查"
  fi
  exit 2
fi

# ---- 7. 结构一致性核对：users/clients 数量与部署前相同 ----
echo "[7/7] 部署后结构与数据计数核对..."
sleep 3
if [[ -f "$DB_FILE" ]]; then
  DB_AFTER_USERS=$(docker run --rm -v "$APP_DIR/data:/app/data" --entrypoint python "$IMAGE_NEW" -c "
import sqlite3
c=sqlite3.connect('/app/data/stock_review.db')
print(c.execute('SELECT COUNT(*) FROM users').fetchone()[0])
" 2>/dev/null || echo "?")
  DB_AFTER_CLIENTS=$(docker run --rm -v "$APP_DIR/data:/app/data" --entrypoint python "$IMAGE_NEW" -c "
import sqlite3
c=sqlite3.connect('/app/data/stock_review.db')
print(c.execute('SELECT COUNT(*) FROM clients').fetchone()[0])
" 2>/dev/null || echo "?")
  echo "      Before  users=$DB_BEFORE_USERS clients=$DB_BEFORE_CLIENTS"
  echo "      After   users=$DB_AFTER_USERS  clients=$DB_AFTER_CLIENTS"
  if [[ "$DB_BEFORE_USERS" != "?" && "$DB_BEFORE_USERS" != "$DB_AFTER_USERS" ]]; then
    echo "      ⚠️  users 数发生变化（若非首次部署，请人工确认是否预期）"
  fi
  if [[ "$DB_BEFORE_CLIENTS" != "?" && "$DB_BEFORE_CLIENTS" != "$DB_AFTER_CLIENTS" ]]; then
    echo "      ⚠️  clients 数发生变化（若非首次部署，请人工确认是否预期）"
  fi
fi

# 追加部署历史
{
  echo "===== $TS  deploy  $IMAGE_NEW  ====="
  echo "旧镜像: ${IMAGE_OLD:-(无记录)}"
  echo "users  before/after: ${DB_BEFORE_USERS} / ${DB_AFTER_USERS}"
  echo "clients before/after: ${DB_BEFORE_CLIENTS} / ${DB_AFTER_CLIENTS}"
  echo "status: OK (health passed)"
} >> "$HISTORY_DIR/deploy.log"

echo "✅ 升级完成：$IMAGE_NEW"
echo "   部署结构快照: $SCHEMA_FILE"
echo "   部署历史日志: $HISTORY_DIR/deploy.log"
echo "   如需 R2 全量恢复，去控制台快照 → 回滚部署前快照"
