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

# 种子数据执行模式（三态：never / first / always）
#   never  = 永不执行 seed（生产稳定后锁定）
#   first  = 仅当 users 表 AND clients 表都为空时才执行（生产默认，只建 1 个 admin）
#   always = 总是执行 seed（幂等：admin 已存在则跳过 insert）
_RUN_SEED_RAW = (os.getenv("STOCK_REVIEW_RUN_SEED") or "first").lower()
if _RUN_SEED_RAW in ("0", "off", "false", "no", "never"):
    RUN_SEED = "never"
elif _RUN_SEED_RAW in ("1", "on", "true", "yes", "always"):
    RUN_SEED = "always"
else:
    RUN_SEED = "first"

# admin 初始密码（仅首次建号时使用；可用环境变量覆盖）
ADMIN_PASSWORD_DEFAULT = os.getenv("STOCK_REVIEW_ADMIN_PASSWORD", "jdzt123456")

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
MARKET_HTTP_TIMEOUT = int(os.getenv("MARKET_HTTP_TIMEOUT", "15"))

# 行业板块（东财 m:90 t:2）
SECTOR_FS = "m:90+t:2"
SECTOR_FIELDS = "f2,f3,f4,f8,f12,f14,f20,f104,f105"
SECTOR_PAGE_SIZE = 200

# 个股行情配置
STOCK_REFRESH_INTERVAL = int(os.getenv("STOCK_REFRESH_INTERVAL", "20"))
STOCK_REFRESH_INTERVAL_OFF = int(os.getenv("STOCK_REFRESH_INTERVAL_OFF", "120"))
STOCK_FIELDS = "f2,f3,f4,f5,f6,f7,f12,f13,f14,f15,f16,f17,f18"
STOCK_CACHE_TTL = int(os.getenv("STOCK_CACHE_TTL", "15"))  # 内存缓存有效期（秒）

# 手续费配置（A股交易费用）
TRADING_FEE_COMMISSION = float(os.getenv("TRADING_FEE_COMMISSION", "0.00025"))   # 佣金费率（万2.5）
TRADING_FEE_MIN_COMMISSION = float(os.getenv("TRADING_FEE_MIN_COMMISSION", "5.0"))  # 最低佣金（元）
TRADING_FEE_STAMP_TAX = float(os.getenv("TRADING_FEE_STAMP_TAX", "0.0005"))        # 印花税（千0.5，仅卖出）
TRADING_FEE_TRANSFER_FEE = float(os.getenv("TRADING_FEE_TRANSFER_FEE", "0.00001")) # 过户费（万0.1，沪深两市）

# ---------------------------------------------------------------------------
# 持仓相关资讯采集（后端常驻 7×24 联动层；两表：news_item + client_news）
# 采集频率：事件流刷新快，upsert 天然去重（重复轮询只产生请求成本，不会重复入库），
# 故采用 30~60s 级别轮询；交易期更密、非交易期放宽，并加 ±jitter 避免多实例共振。
# ---------------------------------------------------------------------------
NEWS_SOURCES = [s.strip() for s in os.getenv("NEWS_SOURCES", "eastmoney,sina").split(",") if s.strip()]
NEWS_INGEST_INTERVAL_SEC = int(os.getenv("NEWS_INGEST_INTERVAL_SEC", "60"))    # 交易期采集间隔
NEWS_INGEST_INTERVAL_OFF = int(os.getenv("NEWS_INGEST_INTERVAL_OFF", "300"))   # 非交易期采集间隔
NEWS_INGEST_JITTER = int(os.getenv("NEWS_INGEST_JITTER", "10"))               # 间隔随机抖动上限（秒）
NEWS_TTL_HOURS = int(os.getenv("NEWS_TTL_HOURS", "24"))                       # 关联/孤儿资讯 TTL
NEWS_PAGE_SIZE = int(os.getenv("NEWS_PAGE_SIZE", "50"))                        # 单次抓取条数
NEWS_HTTP_TIMEOUT = int(os.getenv("NEWS_HTTP_TIMEOUT", "15"))                  # 抓取超时（秒）
NEWS_USER_AGENT = os.getenv(
    "NEWS_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)
