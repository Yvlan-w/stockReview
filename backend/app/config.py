"""应用配置（可通过环境变量覆盖）。"""
import os

# 项目根目录（backend/）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据库：默认 SQLite 单文件（私有单机可直接运行）；可切 PostgreSQL 等任意 SQLAlchemy URL
DATABASE_URL = os.getenv(
    "STOCK_REVIEW_DB",
    "sqlite:///" + os.path.join(BASE_DIR, "stock_review.db"),
)

# JWT
JWT_SECRET = os.getenv("STOCK_REVIEW_SECRET", "dev-secret-change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("STOCK_REVIEW_TOKEN_TTL", "720"))  # 默认 12 小时

# 前端静态资源目录（FastAPI 同源托管，避免跨域与 WebSocket 握手问题）
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))

# 关系映射业务约束
MIN_SERVICES_PER_CLIENT = 1
MAX_SERVICES_PER_CLIENT = 2
