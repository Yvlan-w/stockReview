"""持仓体检报告 · 叙事层（自然语言结论）。

分层设计
--------
事实与打分全部来自 `report_service.build_report()`（确定性）；本模块只负责把事实
翻译成人话。两种实现：

1. ``HeuristicLLMNode``（默认，也是永久兜底）
   纯规则模板，逐维阈值判读 + 组合层判读。完全确定性、零外部依赖、零延迟，
   离线与单测均可用，engine 标记为 ``heuristic``。

2. ``ExternalLLMNode``（接口占位，当前不实现）
   为云端部署预留：三节点提示词已在 ``build_prompts()`` 里成形（逐股诊断 /
   组合视角 / 综合建议），只要接上任意 OpenAI 兼容网关即可启用。
   未配置或调用失败时**自动回落**到启发式，报告永远能出。

对外只暴露 ``generate_narrative(facts, node=None)``。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

LABELS = ("保留", "减仓", "可加", "持有")


# ---------------------------------------------------------------------------
# 阈值判读工具
# ---------------------------------------------------------------------------

def _band(v: float, cuts: Sequence[float], words: Sequence[str]) -> str:
    """按升序阈值把数值映射到描述词。len(words) == len(cuts) + 1。"""
    for i, c in enumerate(cuts):
        if v < c:
            return words[i]
    return words[-1]


def _fmt(v: Any, unit: str = "", nd: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}{unit}"
    except (TypeError, ValueError):
        return f"{v}{unit}"


# ---------------------------------------------------------------------------
# 节点基类
# ---------------------------------------------------------------------------

class LLMNode:
    """叙事节点接口。实现 `narrate(facts)` 返回叙事 dict，失败返回 None 即回落。"""

    engine = "base"

    def narrate(self, facts: Dict[str, Any]) -> Optional[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    # 三节点提示词：外部 LLM 与人工 review 共用同一份口径
    def build_prompts(self, facts: Dict[str, Any]) -> Dict[str, str]:
        stocks = facts.get("stocks") or []
        pf = facts.get("portfolio") or {}
        slim = [
            {
                "代码": s["code"], "名称": s["name"], "行业": s["sector"],
                "现价": s["price"], "权重%": s["weight_pct"],
                "PE": s.get("pe"), "PE历史分位%": s.get("pe_pct"),
                "ROE%": s.get("roe"), "净利率%": s.get("net_margin"),
                "营收同比%": s.get("rev_yoy"), "利润同比%": s.get("profit_yoy"),
                "主力5日净流入(亿)": s.get("fund_d5"), "主力20日净流入(亿)": s.get("fund_d20"),
                "RSI6": s.get("rsi6"), "MACD": s.get("macd_dir"),
                "250日分位%": s.get("pos250"), "距高点%": s.get("dist_high_pct"),
                "四维分": s.get("four_scores"), "综合分": s.get("composite"),
            }
            for s in stocks
        ]
        common = (
            "你是持仓体检报告的撰写者。只依据给定的量化事实写作，不得引入外部信息或编造数据。"
            "全文简体中文，措辞客观克制，不得出现收益承诺，结尾不需要免责声明（由系统统一附加）。"
        )
        return {
            "per_stock": common + "\n【任务】逐只输出四维（估值/基本面/资金/技术）判读各一句，"
                                  "并给出一个操作标签（保留/减仓/可加/持有）与一句理由。\n"
                                  f"【事实】{json.dumps(slim, ensure_ascii=False)}",
            "portfolio": common + "\n【任务】就行业集中度、个股相关性、组合波动与回撤各写一段判读。\n"
                                  f"【事实】{json.dumps({k: v for k, v in pf.items() if k != 'corr_matrix'}, ensure_ascii=False)}",
            "advice": common + "\n【任务】给出加减仓优先级说明与一段综合建议（明确说明非投资建议口径）。\n"
                               f"【事实】{json.dumps({'个股': slim, '组合': {k: v for k, v in pf.items() if k != 'corr_matrix'}}, ensure_ascii=False)}",
        }


# ---------------------------------------------------------------------------
# 启发式实现（默认 + 永久兜底）
# ---------------------------------------------------------------------------

class HeuristicLLMNode(LLMNode):
    """规则模板叙事：确定性、可复现、可审计。"""

    engine = "heuristic"

    # ---- 单维判读 ----
    def _valuation_text(self, s: Dict[str, Any]) -> str:
        pct = float(s.get("pe_pct") or 50)
        word = _band(pct, [20, 40, 60, 80], ["历史低位", "偏低区间", "中枢附近", "偏高区间", "历史高位"])
        pe_txt = _fmt(s.get("pe"), "倍") if s.get("pe") else "PE 数据缺失"
        return (f"当前 {pe_txt}，处于自身历史 {pct:.0f}% 分位（{word}）；"
                f"PB {_fmt(s.get('pb'), '倍', 2)}，分位 {_fmt(s.get('pb_pct'), '%', 0)}。"
                + ("估值端提供了安全边际。" if pct < 40 else
                   "估值已不便宜，需由业绩增长消化。" if pct > 70 else "估值处于可接受区间。"))

    def _fundamental_text(self, s: Dict[str, Any]) -> str:
        roe = float(s.get("roe") or 0)
        rev = float(s.get("rev_yoy") or 0)
        pro = float(s.get("profit_yoy") or 0)
        q = _band(roe, [8, 15, 22], ["盈利能力偏弱", "盈利能力中等", "盈利能力良好", "盈利能力优秀"])
        if rev > 0 and pro > rev:
            growth = "利润增速快于营收，经营杠杆与结构改善同时体现"
        elif rev > 0 and pro <= 0:
            growth = "营收仍在增长但利润承压，费用或价格端存在压力"
        elif rev <= 0 and pro > 0:
            growth = "营收下滑而利润回升，主要来自成本或结构优化"
        else:
            growth = "营收与利润同步走弱，景气度尚未回升"
        return (f"ROE {_fmt(roe, '%')}（{q}），毛利率 {_fmt(s.get('gross_margin'), '%')}、"
                f"净利率 {_fmt(s.get('net_margin'), '%')}，资产负债率 {_fmt(s.get('debt_ratio'), '%')}；"
                f"营收同比 {_fmt(rev, '%')}、净利同比 {_fmt(pro, '%')}，{growth}。")

    def _fund_text(self, s: Dict[str, Any]) -> str:
        d5 = float(s.get("fund_d5") or 0)
        d20 = float(s.get("fund_d20") or 0)
        def _d(v: float) -> str:
            return f"净流入 {abs(v):.2f} 亿" if v >= 0 else f"净流出 {abs(v):.2f} 亿"
        if d5 > 0 and d20 > 0:
            judge = "短中期主力资金同向流入，承接力度较好"
        elif d5 < 0 and d20 < 0:
            judge = "短中期资金持续流出，缺乏增量承接"
        elif d5 > 0 >= d20:
            judge = "短线资金转为流入，中期仍需观察能否延续"
        else:
            judge = "短线资金退潮但中期仍为净流入，属于获利了结特征"
        return f"主力资金近 5 日{_d(d5)}、近 20 日{_d(d20)}；{judge}。"

    def _technical_text(self, s: Dict[str, Any]) -> str:
        pos = float(s.get("pos250") or 50)
        rsi6 = float(s.get("rsi6") or 50)
        pos_word = _band(pos, [20, 40, 60, 85], ["250 日低位区", "偏下位置", "中枢位置", "偏上位置", "接近年内高点"])
        rsi_word = _band(rsi6, [30, 45, 70, 85], ["超卖", "偏弱", "中性", "偏强", "超买"])
        ma = ""
        if s.get("ma20") and s.get("ma60"):
            ma = ("，均线呈多头排列" if float(s["ma20"]) > float(s["ma60"]) else "，均线仍为空头排列")
        return (f"股价处于 {pos_word}（250 日分位 {pos:.0f}%，距年内高点 "
                f"{_fmt(s.get('dist_high_pct'), '%')}），RSI6 {rsi6:.0f}（{rsi_word}），"
                f"MACD {s.get('macd_dir') or '震荡'}{ma}。")

    # ---- 标签与理由 ----
    def _label(self, s: Dict[str, Any]) -> tuple[str, str]:
        c = float(s.get("composite") or 0)
        four = s.get("four_scores") or {}
        pos = float(s.get("pos250") or 50)
        val = float(four.get("估值") or 50)
        fund = float(four.get("资金") or 50)
        fund_flag = float(s.get("fund_d5") or 0) < 0 and float(s.get("fund_d20") or 0) < 0

        if c >= 68 and val >= 45:
            label, modifier = "可加", "分批增配"
        elif c >= 56:
            label, modifier = "保留", "核心底仓"
        elif c >= 46:
            label, modifier = "持有", "观察待定"
        else:
            label, modifier = "减仓", "逢高降配"

        # 覆盖规则：高位 + 估值贵 + 资金流出 → 强制降配
        if pos >= 85 and val <= 35:
            label, modifier = "减仓", "高位估值偏贵，优先降配"
        elif label == "可加" and fund_flag:
            label, modifier = "保留", "基本面占优但资金未配合，暂不加仓"
        return label, modifier

    def _reason(self, s: Dict[str, Any], label: str) -> str:
        four = s.get("four_scores") or {}
        ordered = sorted(four.items(), key=lambda kv: -float(kv[1]))
        best, worst = ordered[0], ordered[-1]
        return (f"综合分 {_fmt(s.get('composite'))}，四维中 {best[0]}最强（{_fmt(best[1])}）、"
                f"{worst[0]}最弱（{_fmt(worst[1])}）；权重 {_fmt(s.get('weight_pct'), '%')}，"
                f"故给出「{label}」。")

    # ---- 组合层判读 ----
    def _concentration(self, pf: Dict[str, Any]) -> str:
        hhi = float(pf.get("hhi") or 0)
        top1 = float(pf.get("top1_pct") or 0)
        shares = pf.get("industry_share") or []
        lead = shares[0] if shares else {"name": "—", "pct": 0}
        level = _band(hhi, [1500, 2500, 4000], ["分散", "中等集中", "偏集中", "高度集中"])
        txt = (f"组合共 {pf.get('stock_count')} 只，行业占比最高为{lead['name']}"
               f"（{_fmt(lead.get('pct'), '%')}），赫芬达尔指数 HHI={hhi:.0f}，"
               f"属于{level}结构；第一大重仓 {pf.get('top1_name')} 占 {top1:.1f}%，"
               f"前三大合计 {_fmt(pf.get('top3_pct'), '%')}。")
        if hhi >= 4000 or top1 >= 40:
            txt += "单一方向暴露过大，若该赛道回调，组合净值将同步承压，建议优先做行业维度的再平衡。"
        elif hhi >= 2500:
            txt += "集中度尚在可控范围，但已具备明显的行业倾斜，需关注该行业景气拐点。"
        else:
            txt += "行业分布相对均衡，风险来源较为分散。"
        return txt

    def _correlation(self, pf: Dict[str, Any]) -> str:
        avg = float(pf.get("corr_avg") or 0)
        mx, mn = float(pf.get("corr_max") or 0), float(pf.get("corr_min") or 0)
        labels = pf.get("corr_labels") or []
        matrix = pf.get("corr_matrix") or []
        hi_pair = lo_pair = ""
        best, worst = -2.0, 2.0
        for i in range(len(matrix)):
            for j in range(i + 1, len(matrix)):
                v = float(matrix[i][j])
                if v > best:
                    best, hi_pair = v, f"{labels[i]}—{labels[j]}"
                if v < worst:
                    worst, lo_pair = v, f"{labels[i]}—{labels[j]}"
        level = _band(avg, [0.1, 0.35, 0.6], ["几乎不相关", "低相关", "中等相关", "高度相关"])
        txt = (f"近 60 个交易日日收益率两两相关系数均值 {avg:.2f}（{level}），"
               f"区间 {mn:.2f} ~ {mx:.2f}。")
        if hi_pair:
            txt += f"相关性最高的是 {hi_pair}（{best:.2f}），同涨同跌特征明显，分散化作用有限；"
        if lo_pair:
            txt += f"相关性最低的是 {lo_pair}（{worst:.2f}），对组合起到对冲缓冲作用。"
        if avg >= 0.6:
            txt += "整体同质化偏高，名义上的「多只股票」并未带来实质分散。"
        return txt

    def _risk(self, pf: Dict[str, Any]) -> str:
        vol = float(pf.get("vol_annual_pct") or 0)
        mdd = float(pf.get("max_drawdown_pct") or 0)
        level = _band(vol, [15, 25, 35], ["低波动", "中等波动", "偏高波动", "高波动"])
        txt = (f"以当前权重回溯，组合年化波动率约 {vol:.1f}%（{level}），"
               f"区间内最大回撤约 {mdd:.1f}%。")
        if vol >= 35:
            txt += "波动显著高于宽基指数，须确认客户风险承受能力与该波动匹配；建议通过降低高波动个股权重或增配低相关资产来压降。"
        elif vol >= 25:
            txt += "波动高于稳健型组合的常见区间，适合中高风险偏好客户持有。"
        else:
            txt += "波动处于可接受范围，回撤控制相对稳定。"
        return txt

    # ---- 主流程 ----
    def narrate(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        stocks = facts.get("stocks") or []
        pf = facts.get("portfolio") or {}

        per_stock: List[Dict[str, Any]] = []
        for s in stocks:
            label, modifier = self._label(s)
            per_stock.append({
                "code": s["code"],
                "name": s["name"],
                "label": label,
                "modifier": modifier,
                "composite": s.get("composite"),
                "narrative": {
                    "估值": self._valuation_text(s),
                    "基本面": self._fundamental_text(s),
                    "资金": self._fund_text(s),
                    "技术": self._technical_text(s),
                },
                "reason": self._reason(s, label),
            })

        ranked = sorted(per_stock, key=lambda x: -float(x["composite"] or 0))
        priority: List[Dict[str, Any]] = []
        for i, x in enumerate(ranked, start=1):
            src = next((s for s in stocks if s["code"] == x["code"]), {})
            if x["label"] == "可加":
                action = "优先加仓"
            elif x["label"] == "减仓":
                action = "优先减仓"
            elif x["label"] == "保留":
                action = "维持权重"
            else:
                action = "观察不动"
            priority.append({
                "rank": i,
                "code": x["code"],
                "name": x["name"],
                "composite": x["composite"],
                "label": x["label"],
                "action": action,
                "weight_pct": src.get("weight_pct"),
                "note": x["modifier"],
            })

        add_list = [p["name"] for p in priority if p["label"] == "可加"]
        cut_list = [p["name"] for p in priority if p["label"] == "减仓"]
        keep_list = [p["name"] for p in priority if p["label"] in ("保留", "持有")]
        parts = [
            f"组合平均综合分 {_fmt(pf.get('avg_composite'))}，"
            f"总市值 {_fmt(pf.get('total_mv'), ' 元', 2)}。"
        ]
        if cut_list:
            parts.append("建议优先降低权重的是 " + "、".join(cut_list) + "，主要问题集中在估值分位偏高、资金持续流出或技术位置过高。")
        if add_list:
            parts.append("相对占优、可考虑分批增配的是 " + "、".join(add_list) + "，其基本面与资金面同时给出正向信号。")
        if keep_list:
            parts.append("其余标的（" + "、".join(keep_list) + "）暂以维持权重、跟踪季报与资金流为主。")
        parts.append("操作节奏上建议分批而非一次性调整，单次调仓幅度控制在个股权重的三分之一以内，"
                     "并在调整后重新核对行业集中度与组合波动是否回到目标区间。")
        parts.append("以上结论由量化规则与公开数据自动生成，仅供投顾内部研究与客户沟通参考，不构成任何投资建议。")

        return {
            "engine": self.engine,
            "per_stock": per_stock,
            "portfolio": {
                "readConcentration": self._concentration(pf),
                "readCorrelation": self._correlation(pf),
                "readRisk": self._risk(pf),
                "priority": priority,
                "advice": "".join(parts),
            },
        }


# ---------------------------------------------------------------------------
# 外部大模型节点（接口占位，云端部署时接上即可）
# ---------------------------------------------------------------------------

class ExternalLLMNode(LLMNode):
    """OpenAI 兼容网关占位实现。

    当前**有意不实现**网络调用（按需求：先保留接口，云端部署时再接）。
    需要启用时：
      1. 配置环境变量 ``REPORT_LLM_BASE_URL`` / ``REPORT_LLM_API_KEY`` / ``REPORT_LLM_MODEL``；
      2. 实现下方 `_chat()`（POST {base_url}/chat/completions）；
      3. `narrate()` 依次调用 `build_prompts()` 的三个提示词并组装成与
         `HeuristicLLMNode` **完全一致**的 JSON 形状返回。
    形状不一致或调用异常时，`generate_narrative` 会自动回落到启发式。
    """

    engine = "external"

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 60.0):
        self.base_url = base_url or os.getenv("REPORT_LLM_BASE_URL") or ""
        self.api_key = api_key or os.getenv("REPORT_LLM_API_KEY") or ""
        self.model = model or os.getenv("REPORT_LLM_MODEL") or "gpt-4o-mini"
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _chat(self, prompt: str) -> str:  # pragma: no cover - 占位
        raise NotImplementedError("外部大模型调用尚未启用（云端部署时实现 _chat）")

    def narrate(self, facts: Dict[str, Any]) -> Optional[Dict[str, Any]]:  # pragma: no cover
        if not self.configured:
            logger.info("外部大模型未配置，叙事回落到启发式")
            return None
        try:
            prompts = self.build_prompts(facts)
            raw = {k: self._chat(v) for k, v in prompts.items()}
            logger.debug("外部大模型返回 %s", list(raw))
        except NotImplementedError:
            return None
        except Exception as e:
            logger.warning("外部大模型叙事失败，回落启发式：%s", e)
            return None
        return None


def resolve_node(use_llm: bool = False) -> LLMNode:
    """按开关选择节点：未开启或外部未配置时一律返回启发式节点。"""
    if use_llm:
        node = ExternalLLMNode()
        if node.configured:
            return node
        logger.info("use_llm=True 但外部大模型未配置，使用启发式叙事")
    return HeuristicLLMNode()


def generate_narrative(facts: Dict[str, Any], node: Optional[LLMNode] = None) -> Dict[str, Any]:
    """生成叙事。任何异常或形状不合规都回落到启发式，保证报告永远可出。"""
    fallback = HeuristicLLMNode()
    node = node or fallback
    if isinstance(node, HeuristicLLMNode):
        return node.narrate(facts)
    try:
        out = node.narrate(facts)
    except Exception as e:  # pragma: no cover
        logger.warning("叙事节点 %s 异常，回落启发式：%s", getattr(node, "engine", "?"), e)
        out = None
    if not out or "per_stock" not in out or "portfolio" not in out:
        out = fallback.narrate(facts)
        out["fallback_from"] = getattr(node, "engine", "unknown")
    return out
