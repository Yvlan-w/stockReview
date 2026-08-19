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

# 行情数据源（东方财富）
MARKET_SECIDS = "1.000001,0.399001,0.399006,1.000688"
SH_INDEX_SECID = "1.000001"
SZ_INDEX_SECID = "0.399001"
CY_INDEX_SECID = "0.399006"
KC_INDEX_SECID = "1.000688"
INDEX_SECIDS = [SH_INDEX_SECID, SZ_INDEX_SECID, CY_INDEX_SECID, KC_INDEX_SECID]
INDEX_LABELS = {
    "1.000001": "上证指数",
    "0.399001": "深证成指",
    "0.399006": "创业板指",
    "1.000688": "科创50",
}
MARKET_REALTIME_FIELDS = "f2,f3,f4,f5,f6,f7,f12,f13,f14,f104,f105,f106,f107,f108,f152"
EASTMONEY_UT = "fa5fd1943c7b386f172d6893dbfba10b"

# 后台刷新配置（秒；K线一天只刷一次，日 K 不会中间变）
MARKET_REFRESH_INTERVAL_REALTIME = int(os.getenv("MARKET_REFRESH_INTERVAL_REALTIME", "20"))
MARKET_REFRESH_INTERVAL_REALTIME_OFF = int(os.getenv("MARKET_REFRESH_INTERVAL_REALTIME_OFF", "120"))
MARKET_REFRESH_INTERVAL_KLINE = int(os.getenv("MARKET_REFRESH_INTERVAL_KLINE", "3600"))   # 非交易期 kline 一小时刷一次
MARKET_REFRESH_INTERVAL_SECTOR = int(os.getenv("MARKET_REFRESH_INTERVAL_SECTOR", "30"))
MARKET_REFRESH_INTERVAL_SECTOR_OFF = int(os.getenv("MARKET_REFRESH_INTERVAL_SECTOR_OFF", "300"))
MARKET_KLINE_DAYS = int(os.getenv("MARKET_KLINE_DAYS", "40"))
MARKET_HTTP_TIMEOUT = int(os.getenv("MARKET_HTTP_TIMEOUT", "8"))

# 行业板块（东财 m:90 t:2）
SECTOR_FS = "m:90+t:2"
SECTOR_FIELDS = "f2,f3,f4,f8,f12,f14,f20,f104,f105"
SECTOR_PAGE_SIZE = 200
