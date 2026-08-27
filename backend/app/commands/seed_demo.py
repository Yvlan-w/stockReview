"""显式灌入演示数据（仅供本地开发预览使用）。

用法：
  cd backend
  python -m app.commands.seed_demo

说明：
- 默认灌入 1 个 admin + 5 个演示用户 + 5 个演示客户（C001~C005）
- 幂等：已存在的账号/客户不会重复写入
- 生产环境绝对不要执行本脚本
"""
from ..database import SessionLocal
from ..services.seed import seed_demo_data


def main():
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()
    print("已灌入演示数据（admin + 5 演示用户 + C001~C005），密码均为 123456。")


if __name__ == "__main__":
    main()
