"""启动入口：python run.py（等价于 uvicorn app.main:app）。

HOST/PORT 支持环境变量覆盖：容器内设 HOST=0.0.0.0，本地默认 127.0.0.1。
"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
