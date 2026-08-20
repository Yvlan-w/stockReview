"""脚本：刷新实时行情数据"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.services.stock_price_service import refresh_stock_prices
from app.database import SessionLocal


async def main():
    db = SessionLocal()
    try:
        print("开始刷新实时行情...")
        ok = await refresh_stock_prices(db)
        print(f"结果: {'成功' if ok else '失败'}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())