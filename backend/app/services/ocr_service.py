"""OCR 截图识别：把券商交易截图 / 持仓截图转成结构化数据。

设计要点：
- 引擎：RapidOcrEngine（懒加载 rapidocr_onnxruntime，纯 CPU）。运行时仅此一套引擎，
  本地开发/测试与云端生产均安装同一依赖，由部署环境（本地 venv / 云服务器）决定装在哪台机器。
- kind 分流：kind=trade 解析成交易行（OcrTrade，落地走 execute_adjust）；
  kind=holding 解析成持仓列表（OcrHolding）+ 截图级字段（可用资金/总资产），
  落地走「整体替换持仓 + 调整黄标 + 写可用资金」（见路由与前端）。
- 解析器：把 OCR 原始文本行启发式聚合。交易用「名称+代码行」作每笔锚点；
  持仓用「6 位代码锚点 + 表头关键词」抽取每只股票的 数量/成本价/市价/市值/盈亏。
  券商 App 截图格式差异大，解析为尽力而为，缺失项交由前端预览卡片补全。
- 本模块不依赖任何前端逻辑；仅返回结构化数据，落地由调用方负责。
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger("ocr_service")


@dataclass
class OcrTrade:
    name: Optional[str] = None
    code: Optional[str] = None
    action: Optional[str] = None          # buy | sell
    quantity: Optional[int] = None
    price: Optional[float] = None
    amount: Optional[float] = None
    fee: Optional[float] = None
    trade_date: Optional[str] = None      # YYYY-MM-DD
    trade_time: Optional[str] = None      # HH:MM:SS
    confidences: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidences"] = {k: round(float(v), 3) for k, v in (self.confidences or {}).items()}
        return d


@dataclass
class OcrHolding:
    """持仓截图解析出的单只持仓。字段尽可能从表头关键词抽取，缺失项交由前端预览补全。"""
    name: Optional[str] = None
    code: Optional[str] = None
    quantity: Optional[int] = None
    cost_price: Optional[float] = None
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    float_pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    confidences: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidences"] = {k: round(float(v), 3) for k, v in (self.confidences or {}).items()}
        return d


class OcrEngine:
    def recognize(self, image_bytes: bytes) -> list[tuple[str, float]]:
        raise NotImplementedError


class RapidOcrEngine(OcrEngine):
    """RapidOCR（ONNX，纯 CPU）。模型在首次识别时惰性加载并缓存为单例。"""

    def __init__(self) -> None:
        self._engine = None

    def _get(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR()
        return self._engine

    def recognize(self, image_bytes: bytes) -> list[tuple[str, float]]:
        # rapidocr_onnxruntime >=1.x：RapidOCR() 实例化后直接 callable，原生接受 bytes /
        # np.ndarray / 路径，返回 ([ [box, text, score_str], ... ], elapse)；无文本时返回 (None, None)。
        engine = self._get()
        result, _ = engine(image_bytes)
        lines: list[tuple[str, float]] = []
        if not result:
            return lines
        for item in result:
            text = item[1]
            try:
                score = float(item[2])
            except (TypeError, ValueError):
                score = 1.0
            if text and str(text).strip():
                lines.append((str(text).strip(), float(score)))
        return lines

    def recognize_with_boxes(self, image_bytes: bytes) -> list[tuple[list, str, float]]:
        """与 recognize() 类似，但额外返回每个文本块的四点 box 坐标。

        返回 list[(box, text, score)]，box 为 [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]（四点）。
        持仓截图（kind=holding）需要坐标做版面表格解析；交易截图（kind=trade）仍走纯文本的
        recognize()。两种引擎输出都来自同一底层 RapidOCR 调用，这里只是多保留 box 维度。
        """
        engine = self._get()
        result, _ = engine(image_bytes)
        out: list[tuple[list, str, float]] = []
        if not result:
            return out
        for item in result:
            box = item[0]
            text = item[1]
            try:
                score = float(item[2])
            except (TypeError, ValueError):
                score = 1.0
            if text and str(text).strip():
                out.append((box, str(text).strip(), float(score)))
        return out


# ---- 启发式解析 ----
# 名称+代码 / 代码+名称：6 位代码必须独立（前后非数字、且不以小数点续接金额），
# 避免「成交额 168500.00」误判为「成交额(名称) 168500(代码)」。
_NAME_CODE_RE = re.compile(r"([\u4e00-\u9fa5]{2,6})[ \t]+(\d{6})(?![.\d])")
_CODE_NAME_RE = re.compile(r"(?<!\d)(\d{6})(?![.\d])[ \t]+([\u4e00-\u9fa5]{2,6})")
# 同行但 OCR 丢失了名称与代码间的空格（把「贵州茅台 600519」读成「贵州茅台600519」）
_NAME_CODE_GLUED_RE = re.compile(r"([\u4e00-\u9fa5]{2,6})(\d{6})(?![.\d])")
_CODE_NAME_GLUED_RE = re.compile(r"(?<!\d)(\d{6})(?![.\d])([\u4e00-\u9fa5]{2,6})")
# 不应作为「股票名称」的财务关键词（命中则忽略 name+code 锚点）
_NAME_STOPWORDS = {"成交额", "金额", "手续费", "佣金", "费用", "委托时间", "成交价", "价格", "数量", "买入", "卖出", "委托", "股票"}
# 纯中文行（2~6 字）才可能是股票名称，用于跨行锚点（真实 OCR 常把名称与代码拆成两行）
_NAME_LIKE_RE = re.compile(r"^[一-鿿]{2,6}$")
# 后续碎片行：仅含数字。真实 OCR 可能把 6 位代码拆成多行（如 300750 → 30075 / 50 / 0）
_DIGIT_LINE_RE = re.compile(r"^\d+$")
_DATE_RE = re.compile(
    r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})[ 日]*[,\s]*(\d{1,2})[:：](\d{2})(?:[:：](\d{2}))?"
)
_QTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*股")
_PRICE_RE = re.compile(r"(?:成交价|价格|单价|现价)[：: ]*?(\d+(?:\.\d+)?)")
_AMOUNT_RE = re.compile(r"(?:成交额|金额|成交金额)[：: ]*?(\d+(?:\.\d+)?)")
_FEE_RE = re.compile(r"(?:手续费|佣金|费用|规费)[：: ]*?(\d+(?:\.\d+)?)")


def _is_name_like(text: str) -> bool:
    return bool(_NAME_LIKE_RE.match(text)) and text not in _NAME_STOPWORDS


def _code_from_following(lines: list[tuple[str, float]], i: int):
    """从 i 之后的连续纯数字碎片行中拼出 6 位代码；返回 (code, next_index)。

    next_index 指向最后一个被消费的数字行之后，便于主循环跳过这些碎片行。
    向上拼到 6 位即停；若 OCR 偶发丢失末位只剩 5 位（如 300750 → 30075），
    也返回 5 位让锚点成立，缺位交由用户在导入面板校正，避免整笔丢失。
    """
    code = ""
    j = i + 1
    while j < len(lines) and lines[j][0] and _DIGIT_LINE_RE.match(lines[j][0].strip()):
        code += re.sub(r"\D", "", lines[j][0])
        if len(code) >= 6:
            return code[:6], j + 1
        j += 1
    if len(code) == 5:
        return code, j
    return None, i


def _new_trade() -> dict:
    return {
        "name": None, "code": None, "action": None, "quantity": None,
        "price": None, "amount": None, "fee": None,
        "trade_date": None, "trade_time": None, "conf": {},
    }


def _finalize(cur: dict) -> OcrTrade:
    if cur["price"] and cur["quantity"] and cur["amount"] is None:
        cur["amount"] = round(float(cur["price"]) * float(cur["quantity"]), 2)
    return OcrTrade(
        name=cur["name"], code=cur["code"], action=cur["action"],
        quantity=cur["quantity"], price=cur["price"], amount=cur["amount"],
        fee=cur["fee"], trade_date=cur["trade_date"], trade_time=cur["trade_time"],
        confidences={k: v for k, v in cur["conf"].items() if v is not None},
    )


def parse_trade_lines(lines: list[tuple[str, float]]) -> list[OcrTrade]:
    """将 OCR 文本行聚合成交易行。每遇到「名称+代码」即开启新一笔。

    支持两种锚点形态（真实 RapidOCR 输出常是跨行的）：
    - 同行：名称与代码在同一行（如「贵州茅台 600519」）。
    - 跨行：名称行紧接 6 位代码（可能被拆成多行碎片，如「宁德时代」/「30075」/「0」）。
    """
    trades: list[OcrTrade] = []
    cur = _new_trade()
    started = False
    # 时间戳在多数券商截图中是「下一笔交易」的块头（位于 name+code 之前），
    # 用 pending 暂存、遇到锚点时落到新一笔；若已在本笔块内且尚未写入则直接归属当前笔。
    pending_date = pending_time = None

    def start_anchor(name: str, code: str, name_score: float, code_score: float) -> None:
        nonlocal cur, started, pending_date, pending_time
        if started:
            trades.append(_finalize(cur))
        cur = _new_trade()
        started = True
        cur["name"], cur["code"] = name, code
        cur["conf"]["name"] = name_score
        cur["conf"]["code"] = code_score
        if pending_date:
            cur["trade_date"], cur["trade_time"] = pending_date, pending_time
            pending_date = pending_time = None

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i][0]
        score = lines[i][1]
        if not raw or not raw.strip():
            i += 1
            continue
        text = raw.strip()
        fired = False

        # 1) 同行锚点：名称 + 代码（含 OCR 丢失空格的粘连形态）
        nm = (
            _NAME_CODE_RE.search(text)
            or _CODE_NAME_RE.search(text)
            or _NAME_CODE_GLUED_RE.search(text)
            or _CODE_NAME_GLUED_RE.search(text)
        )
        if nm:
            g1, g2 = nm.group(1), nm.group(2)
            if re.match(r"[一-鿿]", g1):
                name, code = g1, g2
            else:
                code, name = g1, g2
            if name not in _NAME_STOPWORDS:
                start_anchor(name, code, score, score)
                fired = True

        # 2) 跨行锚点：名称行紧接 6 位代码（可能被拆成多行碎片）
        if not fired and _is_name_like(text):
            code, j = _code_from_following(lines, i)
            if code:
                code_score = lines[j - 1][1] if (j - 1) < n else score
                start_anchor(text, code, score, code_score)
                i = j  # 跳过已被拼入代码的碎片行
                continue

        if not fired:
            # 时间
            dm = _DATE_RE.search(text)
            if dm:
                y, mo, d, hh, mm = dm.group(1), dm.group(2), dm.group(3), dm.group(4), dm.group(5)
                pd = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
                pt = f"{int(hh):02d}:{int(mm):02d}"
                if dm.group(6):
                    pt += f":{int(dm.group(6)):02d}"
                if started and cur["name"] and cur["trade_time"] is None:
                    # 时间戳出现在「本笔交易块内」且尚未写入 → 直接归属当前笔
                    cur["trade_date"], cur["trade_time"] = pd, pt
                    pending_date = pending_time = None
                else:
                    # 时间戳位于 name+code 之前 → 暂存为下一笔的块头
                    pending_date, pending_time = pd, pt
                cur["conf"]["trade_date"] = score
                i += 1
                continue

            # 方向
            if "买入" in text or ("买" in text and "卖" not in text):
                cur["action"] = "buy"
                cur["conf"]["action"] = score
            elif "卖出" in text or "卖" in text:
                cur["action"] = "sell"
                cur["conf"]["action"] = score

            # 数量 / 价格 / 金额 / 手续费
            qm = _QTY_RE.search(text)
            if qm:
                cur["quantity"] = int(float(qm.group(1)))
                cur["conf"]["quantity"] = score
            pm = _PRICE_RE.search(text)
            if pm:
                cur["price"] = float(pm.group(1))
                cur["conf"]["price"] = score
            am = _AMOUNT_RE.search(text)
            if am:
                cur["amount"] = float(am.group(1))
                cur["conf"]["amount"] = score
            fm = _FEE_RE.search(text)
            if fm:
                cur["fee"] = float(fm.group(1))
                cur["conf"]["fee"] = score

        i += 1

    if started:
        trades.append(_finalize(cur))
    return trades


def _get_engine() -> OcrEngine:
    return RapidOcrEngine()


# ---- 持仓解析（kind=holding）----
# 截图级字段（账户栏，非个股行）
_H_AVAIL_RE = re.compile(r"(?:可用资金|可用余额|资金余额)[：: ]*?(\d+(?:\.\d+)?)")
_H_TOTAL_RE = re.compile(r"(?:总资产|总市值|账户总资产|证券市值)[：: ]*?(\d+(?:\.\d+)?)")
# 个股行内字段（按表头关键词抽取，截图列顺序/换行差异大时尽力而为）
_H_QTY_RE = re.compile(r"(?:持股|持仓量|持仓|数量)[：: ]*?(\d+(?:\.\d+)?)")
_H_COST_RE = re.compile(r"(?:持仓成本|成本价|成本|买入成本|持仓成本价)[：: ]*?(\d+(?:\.\d+)?)")
_H_PRICE_RE = re.compile(r"(?:市价|现价|最新价|行情价|当前价)[：: ]*?(\d+(?:\.\d+)?)")
_H_MV_RE = re.compile(r"(?:参考市值|市值|个股市值)[：: ]*?(\d+(?:\.\d+)?)")
_H_PNL_RE = re.compile(r"(?:浮动盈亏|当日盈亏|持仓盈亏|累计盈亏|盈亏)[：: ]*?(-?\d+(?:\.\d+)?)")
_H_PCT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")


def _extract_screenshot_totals(lines: list[tuple[str, float]]):
    """从全部行抽取截图级账户字段：可用资金 / 总资产。"""
    available_cash = total_assets = None
    for text, _ in lines:
        t = (text or "").strip()
        if not t:
            continue
        m = _H_AVAIL_RE.search(t)
        if m:
            available_cash = float(m.group(1))
        m2 = _H_TOTAL_RE.search(t)
        if m2:
            total_assets = float(m2.group(1))
    return available_cash, total_assets


def _find_holding_anchors(lines: list[tuple[str, float]]):
    """定位每只股票的锚点（名称+代码）。支持：同行粘连、独立 6 位代码行、跨行名称→代码。

    返回 [(name, code, start, end_exclusive), ...]。
    """
    anchors: list = []
    i = 0
    n = len(lines)
    while i < n:
        text = (lines[i][0] or "").strip()
        if not text:
            i += 1
            continue
        # 同行：名称 + 代码（粘连变体）
        gm = _NAME_CODE_GLUED_RE.search(text) or _CODE_NAME_GLUED_RE.search(text)
        if gm:
            g1, g2 = gm.group(1), gm.group(2)
            if re.match(r"[一-鿿]", g1):
                name, code = g1, g2
            else:
                code, name = g1, g2
            if name not in _NAME_STOPWORDS:
                anchors.append((name, code, i, i + 1))
                i += 1
                continue
        # 独立 6 位代码行
        digits = re.sub(r"\D", "", text)
        if _DIGIT_LINE_RE.match(text) and len(digits) == 6:
            anchors.append(("", digits, i, i + 1))
            i += 1
            continue
        # 跨行：名称行紧接 6 位代码（碎片）
        if _is_name_like(text):
            code, j = _code_from_following(lines, i)
            if code:
                anchors.append((text, code, i, j))
                i = j
                continue
        i += 1
    return anchors


def _parse_holding_block(block_lines: list[tuple[str, float]]) -> OcrHolding:
    """从单只股票的行块（含锚点行）抽取字段。账户级汇总行（含「总」）不并入个股。"""
    name = ""
    quantity = cost_price = current_price = market_value = float_pnl = pnl_pct = None
    conf: dict = {}
    for text, score in block_lines:
        t = (text or "").strip()
        if not t:
            continue
        if not name:
            m = re.fullmatch(r"[一-鿿]{2,6}", t)
            if m and t not in _NAME_STOPWORDS:
                name = t
                conf["name"] = score
        is_account = "总" in t
        qm = _H_QTY_RE.search(t) or re.search(r"(\d+(?:\.\d+)?)\s*股", t)
        if qm:
            try:
                quantity = int(float(qm.group(1)))
                conf["quantity"] = score
            except (TypeError, ValueError):
                pass
        cm = _H_COST_RE.search(t)
        if cm:
            cost_price = float(cm.group(1))
            conf["cost_price"] = score
        pm = _H_PRICE_RE.search(t)
        if pm:
            current_price = float(pm.group(1))
            conf["current_price"] = score
        if not is_account:
            mm = _H_MV_RE.search(t)
            if mm:
                market_value = float(mm.group(1))
                conf["market_value"] = score
            fm = _H_PNL_RE.search(t)
            if fm:
                float_pnl = float(fm.group(1))
                conf["float_pnl"] = score
        pcm = _H_PCT_RE.search(t)
        if pcm:
            pnl_pct = float(pcm.group(1))
            conf["pnl_pct"] = score
    return OcrHolding(
        name=name or None, code=None, quantity=quantity,
        cost_price=cost_price, current_price=current_price,
        market_value=market_value, float_pnl=float_pnl, pnl_pct=pnl_pct,
        confidences={k: v for k, v in conf.items() if v is not None},
    )


def parse_holdings(lines: list[tuple[str, float]]) -> dict:
    """将 OCR 文本行解析成持仓列表 + 截图级字段。

    返回 {"holdings": list[OcrHolding], "available_cash": Optional[float], "total_assets": Optional[float]}。
    解析为尽力而为：缺字段交前端预览卡片补全。

    账户级字段（可用资金/总资产）只在「持仓块之外」的行抽取：券商截图常把个股市值列标为
    「总市值」，若全量扫描会误把某只个股市值当成账户总值。持仓块范围由锚点切分，块外通常是
    表头 / 账户汇总 / 页脚，才是真正的账户级字段所在。
    """
    anchors = _find_holding_anchors(lines)
    in_block = [False] * len(lines)
    for idx, (_a_name, _a_code, start, _end) in enumerate(anchors):
        next_start = anchors[idx + 1][2] if idx + 1 < len(anchors) else len(lines)
        for i in range(start, next_start):
            in_block[i] = True
    non_block_lines = [ln for i, ln in enumerate(lines) if not in_block[i]]
    available_cash, total_assets = _extract_screenshot_totals(non_block_lines)

    holdings: list[OcrHolding] = []
    for idx, (a_name, a_code, start, _end) in enumerate(anchors):
        next_start = anchors[idx + 1][2] if idx + 1 < len(anchors) else len(lines)
        block = lines[start:next_start]
        h = _parse_holding_block(block)
        if a_name:
            h.name = a_name
        h.code = a_code
        holdings.append(h)
    return {"holdings": holdings, "available_cash": available_cash, "total_assets": total_assets}


# ---- 持仓版面表格解析（kind=holding，基于 box 坐标）----
# 痛点：中信证券等 App 持仓截图「只有股票名称、没有 6 位代码」，且持仓区是「2 行 × 4 列」纯表格
# （无「label: value」结构），老的正则解析器完全失效。必须改用 box 坐标做版面/表格分析：
# 按 y 聚行、按 x 切列、识别表头行、每 2 行一组解析一只股票。
def _parse_money(s) -> Optional[float]:
    """鲁棒的金额解析，专门处理 OCR 对千分位的误读。

    OCR 常把「8,177,041.50」读成「8,177.041.50」（末位千分位逗号变小数点），且同一张图里
    「6,234,591.50」却读对——无法用位数判断。统一规则：最后一个「.」是小数点，其前的所有
    「.」「,」均为千分位分隔符、直接丢弃；最末的 2~3 位小数是金额/百分比精度。
    """
    if not s:
        return None
    s = str(s).strip()
    neg = False
    if s[:1] in ("+", "-"):
        neg = s[0] == "-"
        s = s[1:]
    s = s.replace("%", "").strip()
    m = re.search(r"\d[\d.,]*", s)
    if not m:
        return None
    tok = m.group(0)
    if "." in tok:
        head, tail = tok.rsplit(".", 1)
        head = head.replace(".", "").replace(",", "")
        tail = tail.replace(",", "")
        num = head + "." + tail if tail != "" else head
    else:
        num = tok.replace(",", "").replace(".", "")
    try:
        v = float(num)
    except ValueError:
        return None
    return -v if neg else v


def _parse_int(s) -> Optional[int]:
    v = _parse_money(s)
    if v is None:
        return None
    return int(round(v))


def _boxes_to_rows(boxes, y_tol: int = 22):
    """把 box 列表聚成行。每行是若干 cell dict（含 cx/cy/text/score）。

    聚行规则：按 y0 升序，相邻 cell 的 y0 差 ≤ y_tol 视为同一行。同一行内按 cx 升序。
    """
    items = []
    for box, text, score in boxes:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        items.append({
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "cx": (x0 + x1) / 2.0, "cy": (y0 + y1) / 2.0,
            "text": text, "score": score,
        })
    items.sort(key=lambda d: (d["y0"], d["x0"]))
    rows: list = []
    cur = []
    cur_y = None
    for it in items:
        if cur_y is None or it["y0"] - cur_y <= y_tol:
            cur.append(it)
            cur_y = it["y0"] if cur_y is None else min(cur_y, it["y0"])
        else:
            rows.append(cur)
            cur = [it]
            cur_y = it["y0"]
    if cur:
        rows.append(cur)
    return rows


def _cell_has(row, kw: str) -> bool:
    return any(kw in c["text"] for c in row)


def _is_name_cell(text: str) -> bool:
    return bool(_NAME_LIKE_RE.match(text)) and text not in _NAME_STOPWORDS


def _assign_columns(row, anchors):
    """把一行 cell 映射到列索引（0..n）。每个 cell 归到离其 cx 最近的 anchor。"""
    col_cells: dict = {}
    for c in row:
        if not anchors:
            continue
        best = 0
        best_d = abs(c["cx"] - anchors[0])
        for i, a in enumerate(anchors):
            d = abs(c["cx"] - a)
            if d < best_d:
                best_d = d
                best = i
        col_cells[best] = c
    return col_cells


def _nearest_cell(row, target_cx):
    if not row:
        return None
    return min(row, key=lambda c: abs(c["cx"] - target_cx))


def parse_holdings_table(boxes) -> dict:
    """基于 box 坐标的版面表格解析（kind=holding 首选路径）。

    流程：
    1. box 聚成行。
    2. 识别账户汇总 header（总资产 / 总市值+可用+可取）抽取 总资产、可用资金。
    3. 识别持仓表 header（市值/盈亏/成本/现价），以其 4 列 cx 为列锚点。
    4. 持仓表内每 2 行一组：首行 C1 为中文名 → 该股票；次行 C1 为市值数字。
       列含义：C1=名称/市值，C2=盈亏额/盈亏率，C3=持仓/可用数量，C4=成本/现价。

    返回与 parse_holdings 同形 dict。未识别到持仓表 header 时返回全空（交由正则兜底）。
    """
    rows = _boxes_to_rows(boxes)
    holdings: list[OcrHolding] = []
    available_cash = total_assets = None

    # ---- 账户汇总字段 ----
    for idx, row in enumerate(rows):
        # 总资产 header：含「总资产」且含「浮动盈亏」
        if _cell_has(row, "总资产") and _cell_has(row, "浮动盈亏"):
            th = next((c for c in row if "总资产" in c["text"]), None)
            for nxt in rows[idx + 1: idx + 3]:
                cand = _nearest_cell(nxt, th["cx"]) if th else None
                if cand:
                    v = _parse_money(cand["text"])
                    if v is not None:
                        total_assets = v
                        break
            break
    for idx, row in enumerate(rows):
        # 可用资金 header：含「可取」「可用」「总市值」（区别于持仓表头「持仓/可用」）
        if _cell_has(row, "可取") and _cell_has(row, "可用") and _cell_has(row, "总市值"):
            ah = next((c for c in row if "可用" in c["text"]), None)
            for nxt in rows[idx + 1: idx + 3]:
                cand = _nearest_cell(nxt, ah["cx"]) if ah else None
                if cand:
                    v = _parse_money(cand["text"])
                    if v is not None:
                        available_cash = v
                        break
            break

    # ---- 持仓表 header（4 列锚点）----
    hold_header_idx = None
    anchors = []
    for idx, row in enumerate(rows):
        if (
            _cell_has(row, "市值")
            and _cell_has(row, "盈亏")
            and _cell_has(row, "成本")
            and (_cell_has(row, "现价") or _cell_has(row, "市价"))
        ):
            hold_header_idx = idx
            anchors = [c["cx"] for c in sorted(row, key=lambda c: c["cx"])][:4]
            break
    if hold_header_idx is None or not anchors:
        return {"holdings": holdings, "available_cash": available_cash, "total_assets": total_assets}

    # ---- 逐只解析（每 2 行一组）----
    i = hold_header_idx + 1
    n = len(rows)
    while i < n:
        row = rows[i]
        col_cells = _assign_columns(row, anchors)
        c1 = col_cells.get(0)
        if c1 and _is_name_cell(c1["text"]):
            name = c1["text"]
            h = OcrHolding(name=name, code=None, confidences={"name": c1["score"]})
            c2 = col_cells.get(1)
            c3 = col_cells.get(2)
            c4 = col_cells.get(3)
            if c2:
                v = _parse_money(c2["text"])
                if v is not None:
                    h.float_pnl = v
                    h.confidences["float_pnl"] = c2["score"]
            if c3:
                v = _parse_int(c3["text"])
                if v is not None:
                    h.quantity = v
                    h.confidences["quantity"] = c3["score"]
            if c4:
                v = _parse_money(c4["text"])
                if v is not None:
                    h.cost_price = v
                    h.confidences["cost_price"] = c4["score"]
            # 次行 = 市值/盈亏率/现价
            if i + 1 < n:
                vrow = rows[i + 1]
                vcols = _assign_columns(vrow, anchors)
                vc1 = vcols.get(0)
                vc2 = vcols.get(1)
                vc3 = vcols.get(2)
                vc4 = vcols.get(3)
                if vc1 and not _is_name_cell(vc1["text"]):
                    mv = _parse_money(vc1["text"])
                    if mv is not None:
                        h.market_value = mv
                        h.confidences["market_value"] = vc1["score"]
                if vc2:
                    pct = _parse_money(vc2["text"])
                    if pct is not None:
                        h.pnl_pct = pct
                        h.confidences["pnl_pct"] = vc2["score"]
                if vc4:
                    px = _parse_money(vc4["text"])
                    if px is not None:
                        h.current_price = px
                        h.confidences["current_price"] = vc4["score"]
                i += 2
                holdings.append(h)
                continue
            holdings.append(h)
            i += 1
            continue
        i += 1

    return {"holdings": holdings, "available_cash": available_cash, "total_assets": total_assets}


def recognize_screenshot(image_bytes: bytes, kind: str = "trade"):
    """识别截图字节，按 kind 返回结构化数据。

    - kind="trade"   → list[OcrTrade]（交易行）
    - kind="holding" → dict{holdings: list[OcrHolding], available_cash, total_assets}

    持仓优先走版面表格解析 parse_holdings_table（基于 box 坐标，能处理「只有名称无代码」的
    券商 App 纯表格截图）；当表格路径未识别到任何持仓/账户字段时，降级到老的文本正则解析
    parse_holdings（覆盖仍带 6 位代码锚点的截图）。两者都不依赖外部前端逻辑。
    """
    engine = _get_engine()
    if kind == "holding":
        boxes = engine.recognize_with_boxes(image_bytes)
        table_result = parse_holdings_table(boxes)
        if (
            table_result["holdings"]
            or table_result["available_cash"] is not None
            or table_result["total_assets"] is not None
        ):
            return table_result
        lines = [(text, score) for (box, text, score) in boxes]
        return parse_holdings(lines)
    lines = engine.recognize(image_bytes)
    return parse_trade_lines(lines)
