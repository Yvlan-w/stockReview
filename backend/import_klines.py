"""脚本：手动导入真实市场数据到 stock_daily_price 表。

用法：cd backend && python import_klines.py [--days 90]
"""
import asyncio
import sys

# 设置路径
sys.path.insert(0, ".")

from app.services.stock_price_service import refresh_all_daily_klines
from app.database import SessionLocal


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    db = SessionLocal()
    try:
        print(f"开始刷新日K线数据（最近 {days} 天）...")
        result = await refresh_all_daily_klines(db, limit=days)
        print(f"\n结果:")
        print(f"  成功: {result['success']} 只")
        print(f"  失败: {result['failed']} 只")
        print(f"  总计: {result['total']} 只")
        if result.get("error"):
            print(f"  错误: {result['error']}")
        if result.get("results"):
            print("\n详细结果:")
            for r in result["results"]:
                status = r.get("status", "unknown")
                code = r.get("code", "")
                if status == "success":
                    print(f"  ✓ {code}: {r['count']} 条")
                elif status == "empty":
                    print(f"  ⚠ {code}: 无数据")
                else:
                    print(f"  ✗ {code}: {r.get('error', '未知错误')}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())