"""OCR 截图识别路由：POST /api/ocr/recognize 上传截图，按 kind 返回结构化数据。

- kind=trade（默认）：交易截图 → 返回交易行，落库由前端逐笔调用 /api/clients/{id}/adjust（execute_adjust）。
- kind=holding：持仓截图 → 返回持仓列表 + 截图级字段（可用资金/总资产），落库由前端走
  「整体替换持仓（PUT /positions）+ 调整黄标（POST /transactions action=adjust）+ 写可用资金（PUT /clients）」。
- 仅做识别与结构化，不做落库；落库失败反馈交给前端逐条处理。
- 真实引擎（rapidocr_onnxruntime）缺依赖时返回 503，提示先 `pip install rapidocr-onnxruntime onnxruntime`。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..core.deps import get_current_user
from ..models import User
from ..services.ocr_service import recognize_screenshot

logger = logging.getLogger("ocr")
ocr_router = APIRouter(prefix="/api/ocr", tags=["ocr"])


@ocr_router.post("/recognize")
async def recognize_screenshot_endpoint(
    file: UploadFile = File(...),
    kind: str = Form("trade"),
    user: User = Depends(get_current_user),
):
    if kind not in ("trade", "holding"):
        raise HTTPException(status_code=400, detail="kind 仅支持 trade 或 holding")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="仅支持图片文件（png/jpg）")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")

    try:
        result = recognize_screenshot(data, kind=kind)
    except Exception as e:  # 引擎缺失 / 推理异常
        logger.warning("OCR 识别失败：%s", e)
        raise HTTPException(status_code=503, detail=f"OCR 引擎不可用：{e}")

    if kind == "holding":
        holdings = result["holdings"]
        return {
            "count": len(holdings),
            "rows": [h.to_dict() for h in holdings],
            "available_cash": result.get("available_cash"),
            "total_assets": result.get("total_assets"),
        }
    return {
        "count": len(result),
        "rows": [t.to_dict() for t in result],
    }
