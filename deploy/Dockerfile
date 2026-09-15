# ============================================================
# Stock Review · Dockerfile (v0.2.0 optimized · single-stage no-BuildKit)
# Build: docker build -f deploy/Dockerfile -t stock-review:v0.2.0 .
# NOTE: .dockerignore root-level 16-category filter guarantees NO
#       tests/venv/IDE config/docs/md/demo html/.git/deploy scripts
#       ever enter the build context. This file implements strict
#       whitelist COPY + post-install cleanup to keep minimal runtime.
# ============================================================
FROM python:3.12-slim

# ---- Runtime environment variables ----
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# ---- Minimal runtime apt packages (tzdata + curl for healthcheck) ----
# Plus OpenCV system libs required by rapidocr-onnxruntime (cv2 import):
#   libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libx11-6 libxcb1 libgthread-2.0-0
# Immediately clean apt caches (saves ~20MB vs leaving lists)
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends tzdata curl \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libx11-6 libxcb1 libgthread-2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /var/cache/apt/* \
    && rm -rf /var/log/apt/*

WORKDIR /app

# ---- Install python dependencies (use Aliyun mirror for China network) ----
# Then immediately CLEAN build artifacts:
#   1. pip/setuptools/wheel are build tools not needed at runtime
#   2. __pycache__ directories and .pyc bytecode (regenerate at runtime, ~10MB waste)
#   3. *.dist-info metadata (WHEEL/RECORD files, ~5MB; LICENSE retained)
COPY backend/requirements.txt /app/requirements.txt
RUN pip install \
        --no-cache-dir \
        --index-url https://mirrors.aliyun.com/pypi/simple/ \
        -r /app/requirements.txt \
    && ( find /usr/local/lib/python3.12/site-packages -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true ) \
    && ( find /usr/local/lib/python3.12/site-packages -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null || true ) \
    && ( find /usr/local/lib/python3.12/site-packages -name RECORD -delete 2>/dev/null || true ) \
    && ( find /usr/local/lib/python3.12/site-packages -name WHEEL -delete 2>/dev/null || true ) \
    && rm -rf /usr/local/lib/python3.12/site-packages/pip \
    && rm -rf /usr/local/lib/python3.12/site-packages/setuptools \
    && rm -rf /usr/local/lib/python3.12/site-packages/wheel \
    && rm -rf /usr/local/lib/python3.12/site-packages/_distutils_hack \
    && ( rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 2>/dev/null || true ) \
    && ( rm -rf /root/.cache 2>/dev/null || true )

# ---- Whitelist backend runtime ONLY (3 items, never entire backend/) ----
# .dockerignore blocks: tests/ venv/ scripts/ commands/ _*.py *.txt debug files
COPY backend/app /app/app
COPY backend/run.py /app/run.py

# Keep requirements.txt inside image for runtime dependency audit (200 bytes, harmless)
# COPY backend/requirements.txt /app/requirements.txt  (already copied above)

# ---- Frontend static assets (index.html + css + js for same-origin hosting) ----
# .dockerignore blocks: demo html / md / any local sample snapshots
COPY frontend /frontend

# ---- Non-root runtime user, UID/GID fixed 1000 for host bind-mount ownership ----
RUN groupadd -r -g 1000 appuser \
    && useradd -r -M -u 1000 -g 1000 -s /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app /frontend
USER appuser

# ---- Runtime defaults ----
ENV STOCK_REVIEW_DB=sqlite:////app/data/stock_review.db \
    HOST=0.0.0.0

EXPOSE 8000

# ---- Healthcheck (matches compose healthcheck, no behavior change) ----
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "run.py"]
