# ============================================================
# 持仓复盘 · 单容器镜像（FastAPI 后端 + 前端静态资源同源托管）
# 构建：docker build -f deploy/Dockerfile -t stock-review:v0.1.0 .
# 注意：构建上下文为项目根目录（backend/ + frontend/）
# ============================================================
FROM python:3.12-slim

# 容器时区固定上海（交易时段判断/行情刷新节奏依赖本地时间）
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 国内网络构建加速：apt/pip 全部走阿里云镜像源
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用 Docker 层缓存：requirements 不变则跳过重装）
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r /app/requirements.txt

# 复制应用代码与前端静态资源
# 布局与仓库一致：backend(=/app) 与 frontend(=/frontend) 平级，
# config.py 中 FRONTEND_DIR = BASE_DIR/../frontend 才能正确解析
COPY backend/app /app/app
COPY backend/run.py /app/run.py
COPY frontend /frontend

# 非 root 运行 + 数据目录（SQLite 挂载点）
# UID/GID 固定 1000：服务器 bind mount 的宿主机目录需 chown 1000:1000 才可写
RUN groupadd -r -g 1000 appuser \
    && useradd -r -u 1000 -g 1000 -s /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# 数据库默认路径（compose 通过 STOCK_REVIEW_DB 覆盖到 /app/data 卷）
# HOST=0.0.0.0：容器内必须监听全部网卡才能被端口映射访问
ENV STOCK_REVIEW_DB=sqlite:////app/data/stock_review.db \
    HOST=0.0.0.0

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "run.py"]
