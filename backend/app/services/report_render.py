"""持仓体检报告 · 渲染层（自包含 HTML + 内联 SVG，面向 A4 打印导出 PDF）。

设计约束
--------
- **零外部依赖**：不引用任何 CDN / 字体 / JS 图表库，整份 HTML 可离线打开、可直接打印。
- **纯内联 SVG**：所有图表用手绘 SVG path/rect/polygon，打印时矢量不失真（无需交互）。
- **涨红跌绿**：符合 A 股惯例——上涨 / 资金净流入 / 正相关用红，下跌 / 净流出 / 负相关用绿。
- **分页友好**：每个区块 `page-break-inside: avoid`，@page A4 边距 12mm。

区块顺序与报告模板一致：
个股速览卡片 → 估值对比 → 主力资金动向 → 基本面质量对比 → 技术位置
→ 组合结构 → 加减仓优先级与综合建议 → 免责声明
"""
from __future__ import annotations

import html
import math
from typing import Any, Dict, List, Optional, Sequence

# ---- 配色（浅色纸面主题，为打印优化） ----
C_UP = "#d9363e"        # 涨 / 净流入 / 正相关
C_DOWN = "#17a673"      # 跌 / 净流出 / 负相关
C_INK = "#1f2430"
C_SUB = "#6b7280"
C_LINE = "#e5e7eb"
C_BG_SOFT = "#f7f8fa"
C_ACCENT = "#2f5bea"
C_WARN = "#e8a33d"

DIM_COLORS = {"估值": "#2f5bea", "基本面": "#7b5cf0", "资金": "#e8a33d", "技术": "#12a5a5"}
LABEL_COLORS = {"可加": C_UP, "保留": C_ACCENT, "持有": C_SUB, "减仓": C_DOWN}
SECTOR_PALETTE = ["#2f5bea", "#7b5cf0", "#12a5a5", "#e8a33d", "#d9363e",
                  "#17a673", "#8a8f9c", "#c05fd6", "#3f8cff", "#d97c2b"]


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _n(v: Any, nd: int = 1, dash: str = "—") -> str:
    if v is None:
        return dash
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return dash


def _signed(v: Any, nd: int = 2, unit: str = "") -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{'+' if f >= 0 else ''}{f:.{nd}f}{unit}"


def _updown(v: Any) -> str:
    """按涨红跌绿返回颜色。"""
    try:
        return C_UP if float(v) >= 0 else C_DOWN
    except (TypeError, ValueError):
        return C_SUB


def _money(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(f) >= 1e8:
        return f"¥{f / 1e8:.2f} 亿"
    if abs(f) >= 1e4:
        return f"¥{f / 1e4:.2f} 万"
    return f"¥{f:,.2f}"


# ---------------------------------------------------------------------------
# SVG 图元
# ---------------------------------------------------------------------------

def _svg_radar(four: Dict[str, Any], size: int = 132) -> str:
    """四维雷达图（上=估值，右=基本面，下=资金，左=技术）。"""
    cx = cy = size / 2
    R = size / 2 - 22
    dims = ["估值", "基本面", "资金", "技术"]
    angles = [-90, 0, 90, 180]
    parts = [f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img">']
    # 网格
    for ring in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(
            f"{cx + R * ring * math.cos(math.radians(a)):.1f},{cy + R * ring * math.sin(math.radians(a)):.1f}"
            for a in angles
        )
        parts.append(f'<polygon points="{pts}" fill="none" stroke="{C_LINE}" stroke-width="1"/>')
    for a in angles:
        parts.append(
            f'<line x1="{cx}" y1="{cy}" x2="{cx + R * math.cos(math.radians(a)):.1f}" '
            f'y2="{cy + R * math.sin(math.radians(a)):.1f}" stroke="{C_LINE}" stroke-width="1"/>')
    # 数据面
    pts, labels = [], []
    for d, a in zip(dims, angles):
        v = max(0.0, min(100.0, float(four.get(d) or 0))) / 100.0
        x = cx + R * v * math.cos(math.radians(a))
        y = cy + R * v * math.sin(math.radians(a))
        pts.append(f"{x:.1f},{y:.1f}")
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="{DIM_COLORS[d]}"/>')
        lx = cx + (R + 15) * math.cos(math.radians(a))
        ly = cy + (R + 15) * math.sin(math.radians(a))
        anchor = "middle" if a in (-90, 90) else ("start" if a == 0 else "end")
        dy = 3 if a != -90 else -1
        labels.append(f'<text x="{lx:.1f}" y="{ly + dy:.1f}" text-anchor="{anchor}" '
                      f'font-size="9" fill="{C_SUB}">{d}</text>')
    parts.append(f'<polygon points="{" ".join(pts)}" fill="{C_ACCENT}22" '
                 f'stroke="{C_ACCENT}" stroke-width="1.6"/>')
    parts.extend(labels)
    parts.append("</svg>")
    return "".join(parts)


def _svg_grouped_bars(labels: Sequence[str], groups: Sequence[Dict[str, Any]],
                      width: int = 660, height: int = 240, unit: str = "%",
                      ymax: float = 100.0) -> str:
    """分组竖向柱状图。groups: [{name, color, values[]}]。"""
    pad_l, pad_r, pad_t, pad_b = 44, 12, 26, 46
    pw = width - pad_l - pad_r
    ph = height - pad_t - pad_b
    n = max(1, len(labels))
    g = max(1, len(groups))
    slot = pw / n
    bw = min(26.0, slot / (g + 1.2))
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">']
    # y 轴网格
    for i in range(5):
        v = ymax * i / 4
        y = pad_t + ph - ph * (v / ymax if ymax else 0)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" '
                     f'stroke="{C_LINE}" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{y + 3:.1f}" text-anchor="end" font-size="9" '
                     f'fill="{C_SUB}">{v:.0f}{unit}</text>')
    for i, lab in enumerate(labels):
        cx = pad_l + slot * (i + 0.5)
        for k, grp in enumerate(groups):
            vals = grp.get("values") or []
            v = float(vals[i]) if i < len(vals) and vals[i] is not None else 0.0
            v = max(0.0, min(ymax, v))
            h = ph * (v / ymax if ymax else 0)
            x = cx - (g * bw) / 2 + k * bw
            y = pad_t + ph - h
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw - 2:.1f}" height="{max(h, 1):.1f}" '
                         f'rx="2" fill="{grp.get("color", C_ACCENT)}"/>')
            parts.append(f'<text x="{x + (bw - 2) / 2:.1f}" y="{y - 3:.1f}" text-anchor="middle" '
                         f'font-size="8.5" fill="{C_SUB}">{v:.0f}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{height - pad_b + 15:.1f}" text-anchor="middle" '
                     f'font-size="9.5" fill="{C_INK}">{_esc(lab)}</text>')
    # 图例
    lx = pad_l
    for grp in groups:
        parts.append(f'<rect x="{lx}" y="{height - 16}" width="9" height="9" rx="2" '
                     f'fill="{grp.get("color", C_ACCENT)}"/>')
        parts.append(f'<text x="{lx + 13}" y="{height - 8}" font-size="9" fill="{C_SUB}">'
                     f'{_esc(grp.get("name"))}</text>')
        lx += 20 + len(str(grp.get("name", ""))) * 11
    parts.append("</svg>")
    return "".join(parts)


def _svg_diverging(labels: Sequence[str], series: Sequence[Dict[str, Any]],
                   width: int = 660, height: int = 250, unit: str = "亿") -> str:
    """零轴居中的双向柱状图（主力资金净流入/流出，红入绿出）。"""
    pad_l, pad_r, pad_t, pad_b = 66, 12, 22, 44
    pw = width - pad_l - pad_r
    ph = height - pad_t - pad_b
    all_vals = [abs(float(v or 0)) for s in series for v in (s.get("values") or [])]
    vmax = max(all_vals + [1.0]) * 1.15
    zero_y = pad_t + ph / 2
    n = max(1, len(labels))
    g = max(1, len(series))
    slot = pw / n
    bw = min(24.0, slot / (g + 1.2))
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">']
    parts.append(f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{width - pad_r}" y2="{zero_y:.1f}" '
                 f'stroke="{C_SUB}" stroke-width="1.2"/>')
    for frac in (0.5, 1.0):
        for sign in (1, -1):
            y = zero_y - sign * (ph / 2) * frac
            parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" '
                         f'stroke="{C_LINE}" stroke-width="1" stroke-dasharray="3 3"/>')
            parts.append(f'<text x="{pad_l - 6}" y="{y + 3:.1f}" text-anchor="end" font-size="9" '
                         f'fill="{C_SUB}">{sign * vmax * frac:+.1f}{unit}</text>')
    parts.append(f'<text x="{pad_l - 6}" y="{zero_y + 3:.1f}" text-anchor="end" font-size="9" '
                 f'fill="{C_SUB}">0</text>')
    for i, lab in enumerate(labels):
        cx = pad_l + slot * (i + 0.5)
        for k, s in enumerate(series):
            vals = s.get("values") or []
            v = float(vals[i]) if i < len(vals) and vals[i] is not None else 0.0
            h = (ph / 2) * min(1.0, abs(v) / vmax)
            x = cx - (g * bw) / 2 + k * bw
            y = zero_y - h if v >= 0 else zero_y
            color = C_UP if v >= 0 else C_DOWN
            op = "1" if k == 0 else "0.55"
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw - 2:.1f}" '
                         f'height="{max(h, 1):.1f}" rx="2" fill="{color}" opacity="{op}"/>')
            ty = (y - 3) if v >= 0 else (y + h + 10)
            parts.append(f'<text x="{x + (bw - 2) / 2:.1f}" y="{ty:.1f}" text-anchor="middle" '
                         f'font-size="8.5" fill="{color}">{v:+.1f}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{height - pad_b + 16:.1f}" text-anchor="middle" '
                     f'font-size="9.5" fill="{C_INK}">{_esc(lab)}</text>')
    lx = pad_l
    for k, s in enumerate(series):
        parts.append(f'<rect x="{lx}" y="{height - 15}" width="9" height="9" rx="2" '
                     f'fill="{C_SUB}" opacity="{"1" if k == 0 else "0.55"}"/>')
        parts.append(f'<text x="{lx + 13}" y="{height - 7}" font-size="9" fill="{C_SUB}">'
                     f'{_esc(s.get("name"))}</text>')
        lx += 22 + len(str(s.get("name", ""))) * 11
    parts.append("</svg>")
    return "".join(parts)


def _arc_path(cx: float, cy: float, r_out: float, r_in: float,
              a0: float, a1: float) -> str:
    def pt(r: float, a: float):
        rad = math.radians(a)
        return cx + r * math.cos(rad), cy + r * math.sin(rad)
    large = 1 if (a1 - a0) % 360 > 180 else 0
    x0, y0 = pt(r_out, a0)
    x1, y1 = pt(r_out, a1)
    x2, y2 = pt(r_in, a1)
    x3, y3 = pt(r_in, a0)
    return (f"M{x0:.2f},{y0:.2f} A{r_out:.2f},{r_out:.2f} 0 {large} 1 {x1:.2f},{y1:.2f} "
            f"L{x2:.2f},{y2:.2f} A{r_in:.2f},{r_in:.2f} 0 {large} 0 {x3:.2f},{y3:.2f} Z")


def _svg_donut(items: Sequence[Dict[str, Any]], size: int = 250, center_text: str = "") -> str:
    """行业占比环形图。"""
    cx = cy = size / 2
    r_out, r_in = size / 2 - 34, size / 2 - 62
    parts = [f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img">']
    total = sum(float(x.get("pct") or 0) for x in items) or 100.0
    a = -90.0
    for i, x in enumerate(items):
        pct = float(x.get("pct") or 0)
        sweep = 360.0 * pct / total
        if sweep <= 0:
            continue
        color = SECTOR_PALETTE[i % len(SECTOR_PALETTE)]
        a1 = a + min(sweep, 359.99)
        parts.append(f'<path d="{_arc_path(cx, cy, r_out, r_in, a, a1)}" fill="{color}"/>')
        mid = math.radians((a + a1) / 2)
        if pct >= 7:
            lx = cx + (r_out + 15) * math.cos(mid)
            ly = cy + (r_out + 15) * math.sin(mid)
            anchor = "start" if math.cos(mid) >= 0 else "end"
            parts.append(f'<text x="{lx:.1f}" y="{ly + 3:.1f}" text-anchor="{anchor}" font-size="9.5" '
                         f'fill="{C_INK}">{_esc(x.get("name"))} {pct:.1f}%</text>')
        a = a1
    if center_text:
        parts.append(f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" font-size="12" '
                     f'font-weight="600" fill="{C_INK}">{_esc(center_text)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_weight_bars(weights: Sequence[Dict[str, Any]], width: int = 330) -> str:
    """个股权重横向条。"""
    rowh = 26
    height = max(1, len(weights)) * rowh + 10
    label_w, val_w = 78, 46
    bar_w = width - label_w - val_w - 10
    vmax = max([float(w.get("pct") or 0) for w in weights] + [1.0])
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">']
    for i, w in enumerate(weights):
        y = 6 + i * rowh
        pct = float(w.get("pct") or 0)
        bl = bar_w * pct / vmax
        color = SECTOR_PALETTE[i % len(SECTOR_PALETTE)]
        parts.append(f'<text x="0" y="{y + 13}" font-size="10" fill="{C_INK}">{_esc(w.get("name"))}</text>')
        parts.append(f'<rect x="{label_w}" y="{y + 4}" width="{bar_w}" height="12" rx="6" fill="{C_BG_SOFT}"/>')
        parts.append(f'<rect x="{label_w}" y="{y + 4}" width="{max(bl, 2):.1f}" height="12" rx="6" fill="{color}"/>')
        parts.append(f'<text x="{label_w + bar_w + 6}" y="{y + 14}" font-size="10" font-weight="600" '
                     f'fill="{C_INK}">{pct:.1f}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def _corr_color(v: float) -> str:
    """正相关→红，负相关→绿，接近 0→灰白（涨红跌绿延伸用法）。"""
    a = min(1.0, abs(v))
    if v >= 0:
        r, g, b = 217, 54, 62
    else:
        r, g, b = 23, 166, 115
    mix = 0.12 + 0.78 * a
    return f"rgb({int(255 - (255 - r) * mix)},{int(255 - (255 - g) * mix)},{int(255 - (255 - b) * mix)})"


def _svg_heatmap(labels: Sequence[str], matrix: Sequence[Sequence[float]], width: int = 660) -> str:
    """相关性热力图。"""
    n = max(1, len(labels))
    left, top = 86, 62
    cell = min(56.0, (width - left - 12) / n)
    height = top + cell * n + 14
    parts = [f'<svg viewBox="0 0 {width} {height:.0f}" width="100%" height="{height:.0f}" role="img">']
    for j, lab in enumerate(labels):
        x = left + cell * (j + 0.5)
        parts.append(f'<text x="{x:.1f}" y="{top - 8}" text-anchor="middle" font-size="9" '
                     f'fill="{C_SUB}" transform="rotate(-22 {x:.1f} {top - 8})">{_esc(lab)}</text>')
    for i, lab in enumerate(labels):
        y = top + cell * i
        parts.append(f'<text x="{left - 6}" y="{y + cell / 2 + 3:.1f}" text-anchor="end" font-size="9.5" '
                     f'fill="{C_INK}">{_esc(lab)}</text>')
        for j in range(n):
            try:
                v = float(matrix[i][j])
            except (IndexError, TypeError, ValueError):
                v = 0.0
            x = left + cell * j
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell - 2:.1f}" height="{cell - 2:.1f}" '
                         f'rx="3" fill="{_corr_color(v)}"/>')
            fill = "#ffffff" if abs(v) > 0.55 else C_INK
            parts.append(f'<text x="{x + (cell - 2) / 2:.1f}" y="{y + cell / 2 + 3:.1f}" '
                         f'text-anchor="middle" font-size="9.5" fill="{fill}">{v:.2f}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_position_track(stocks: Sequence[Dict[str, Any]], width: int = 660) -> str:
    """250 日价格区间位置轨道：低点—高点之间标出当前位置。"""
    rowh = 40
    height = max(1, len(stocks)) * rowh + 26
    label_w, right_w = 84, 116
    track_w = width - label_w - right_w
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">']
    parts.append(f'<text x="{label_w}" y="12" font-size="9" fill="{C_SUB}">250 日最低</text>')
    parts.append(f'<text x="{label_w + track_w}" y="12" text-anchor="end" font-size="9" '
                 f'fill="{C_SUB}">250 日最高</text>')
    for i, s in enumerate(stocks):
        y = 24 + i * rowh
        pos = max(0.0, min(100.0, float(s.get("pos250") or 50)))
        px = label_w + track_w * pos / 100.0
        # 位置越高越接近高点 → 红（过热），越低越接近低点 → 绿
        color = C_UP if pos >= 60 else (C_DOWN if pos <= 35 else C_WARN)
        parts.append(f'<text x="0" y="{y + 14}" font-size="10" fill="{C_INK}">{_esc(s.get("name"))}</text>')
        parts.append(f'<rect x="{label_w}" y="{y + 7}" width="{track_w}" height="10" rx="5" '
                     f'fill="url(#posgrad)"/>')
        parts.append(f'<line x1="{px:.1f}" y1="{y + 2}" x2="{px:.1f}" y2="{y + 22}" '
                     f'stroke="{color}" stroke-width="2.4"/>')
        parts.append(f'<circle cx="{px:.1f}" cy="{y + 12}" r="4.2" fill="{color}" '
                     f'stroke="#fff" stroke-width="1.4"/>')
        parts.append(f'<text x="{label_w + track_w + 8}" y="{y + 10}" font-size="9.5" '
                     f'font-weight="600" fill="{color}">分位 {pos:.0f}%</text>')
        parts.append(f'<text x="{label_w + track_w + 8}" y="{y + 21}" font-size="8.5" fill="{C_SUB}">'
                     f'{_n(s.get("low250"), 2)} → {_n(s.get("high250"), 2)}</text>')
    parts.insert(1, f'<defs><linearGradient id="posgrad" x1="0" y1="0" x2="1" y2="0">'
                    f'<stop offset="0%" stop-color="{C_DOWN}" stop-opacity="0.28"/>'
                    f'<stop offset="50%" stop-color="{C_WARN}" stop-opacity="0.28"/>'
                    f'<stop offset="100%" stop-color="{C_UP}" stop-opacity="0.32"/>'
                    f'</linearGradient></defs>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_score_gauge(value: float, width: int = 300, height: int = 74) -> str:
    """组合平均综合分横向刻度条。"""
    v = max(0.0, min(100.0, float(value or 0)))
    x0, x1 = 10, width - 10
    bw = x1 - x0
    px = x0 + bw * v / 100.0
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">'
        f'<defs><linearGradient id="gg" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{C_DOWN}"/><stop offset="50%" stop-color="{C_WARN}"/>'
        f'<stop offset="100%" stop-color="{C_UP}"/></linearGradient></defs>'
        f'<rect x="{x0}" y="34" width="{bw}" height="12" rx="6" fill="url(#gg)" opacity="0.85"/>'
        f'<line x1="{px:.1f}" y1="26" x2="{px:.1f}" y2="54" stroke="{C_INK}" stroke-width="2"/>'
        f'<circle cx="{px:.1f}" cy="40" r="5" fill="#fff" stroke="{C_INK}" stroke-width="2"/>'
        f'<text x="{px:.1f}" y="20" text-anchor="middle" font-size="13" font-weight="700" '
        f'fill="{C_INK}">{v:.1f}</text>'
        f'<text x="{x0}" y="66" font-size="9" fill="{C_SUB}">0 偏弱</text>'
        f'<text x="{x1}" y="66" text-anchor="end" font-size="9" fill="{C_SUB}">100 占优</text>'
        f'</svg>'
    )


# ---------------------------------------------------------------------------
# 区块
# ---------------------------------------------------------------------------

def _section(title: str, subtitle: str, body: str, index: str = "") -> str:
    idx = f'<span class="sec-idx">{_esc(index)}</span>' if index else ""
    return (f'<section class="sec">'
            f'<div class="sec-head">{idx}<h2>{_esc(title)}</h2>'
            f'<p class="sec-sub">{_esc(subtitle)}</p></div>'
            f'<div class="sec-body">{body}</div></section>')


def _header(meta: Dict[str, Any], pf: Dict[str, Any]) -> str:
    notes = "、".join(meta.get("data_notes") or []) or "公开数据源"
    w = meta.get("weights") or {}
    wtxt = " + ".join(f"{k} {float(v) * 100:.0f}%" for k, v in w.items()) if w else ""
    kpis = [
        ("持仓只数", f'{meta.get("stock_count", 0)} 只', C_INK),
        ("组合市值", _money(pf.get("total_mv")), C_INK),
        ("平均综合分", _n(pf.get("avg_composite")), C_ACCENT),
        ("行业集中度 HHI", _n(pf.get("hhi"), 0), C_INK),
        ("年化波动率", f'{_n(pf.get("vol_annual_pct"))}%', C_WARN),
        ("最大回撤", f'{_n(pf.get("max_drawdown_pct"))}%', C_DOWN),
    ]
    cells = "".join(
        f'<div class="kpi"><span class="kpi-l">{_esc(k)}</span>'
        f'<span class="kpi-v" style="color:{c}">{_esc(v)}</span></div>'
        for k, v, c in kpis
    )
    return (
        f'<header class="rp-head">'
        f'<div class="rp-title-row">'
        f'<div><h1>{_esc(meta.get("title") or "持仓体检报告")}</h1>'
        f'<p class="rp-meta">生成时间 {_esc(meta.get("generated_at"))} ｜ 数据源：{_esc(notes)}'
        f'{" ｜ 打分权重：" + _esc(wtxt) if wtxt else ""}</p></div>'
        f'<div class="rp-actions no-print">'
        f'<button onclick="window.print()">打印 / 导出 PDF</button></div>'
        f'</div>'
        f'<div class="kpi-row">{cells}</div>'
        f'</header>'
    )


def _cards(stocks: Sequence[Dict[str, Any]], narr_map: Dict[str, Dict[str, Any]]) -> str:
    cards = []
    for s in stocks:
        nv = narr_map.get(s["code"]) or {}
        label = nv.get("label") or "持有"
        lc = LABEL_COLORS.get(label, C_SUB)
        chg = s.get("change_pct")
        pnl = s.get("pnl_pct")
        rows = [
            ("现价", f'{_n(s.get("price"), 2)}', _updown(chg)),
            ("日涨跌", f'{_signed(chg, 2, "%")}', _updown(chg)),
            ("持仓权重", f'{_n(s.get("weight_pct"))}%', C_INK),
            ("浮动盈亏", f'{_signed(pnl, 2, "%")}' if pnl is not None else "—", _updown(pnl)),
            ("PE / 分位", f'{_n(s.get("pe"))} / {_n(s.get("pe_pct"), 0)}%', C_INK),
            ("ROE", f'{_n(s.get("roe"))}%', C_INK),
            ("主力5日", f'{_signed(s.get("fund_d5"), 2, "亿")}', _updown(s.get("fund_d5"))),
            ("RSI6 / MACD", f'{_n(s.get("rsi6"), 0)} · {_esc(s.get("macd_dir"))}', C_INK),
        ]
        kv = "".join(
            f'<div class="kv"><span>{_esc(k)}</span><b style="color:{c}">{v}</b></div>'
            for k, v, c in rows
        )
        four = s.get("four_scores") or {}
        chips = "".join(
            f'<span class="chip" style="background:{DIM_COLORS[d]}18;color:{DIM_COLORS[d]}">'
            f'{d} {_n(four.get(d))}</span>' for d in ("估值", "基本面", "资金", "技术")
        )
        cards.append(
            f'<article class="card">'
            f'<div class="card-top">'
            f'<div class="card-id"><h3>{_esc(s.get("name"))}</h3>'
            f'<span class="code">{_esc(s.get("code"))} · {_esc(s.get("sector"))}</span></div>'
            f'<div class="card-tag"><span class="badge" style="background:{lc}">{_esc(label)}</span>'
            f'<span class="score">综合 {_n(s.get("composite"))}</span></div>'
            f'</div>'
            f'<div class="card-mid"><div class="radar">{_svg_radar(four)}</div>'
            f'<div class="kvs">{kv}</div></div>'
            f'<div class="chips">{chips}</div>'
            f'<p class="card-note">{_esc(nv.get("reason") or "")}</p>'
            f'</article>'
        )
    return f'<div class="cards">{"".join(cards)}</div>'


def _valuation_block(stocks: Sequence[Dict[str, Any]], narr_map) -> str:
    names = [s["name"] for s in stocks]
    chart = _svg_grouped_bars(
        names,
        [{"name": "PE 历史分位", "color": DIM_COLORS["估值"],
          "values": [s.get("pe_pct") for s in stocks]},
         {"name": "PB 历史分位", "color": "#8fa7f5",
          "values": [s.get("pb_pct") for s in stocks]},
         {"name": "估值得分", "color": C_UP,
          "values": [(s.get("four_scores") or {}).get("估值") for s in stocks]}],
        unit="%",
    )
    head = "".join(f"<th>{h}</th>" for h in
                   ("标的", "PE(倍)", "PE 分位", "PB(倍)", "PB 分位", "估值得分", "判读"))
    rows = []
    for s in stocks:
        nv = narr_map.get(s["code"]) or {}
        pct = float(s.get("pe_pct") or 50)
        col = C_UP if pct >= 70 else (C_DOWN if pct <= 30 else C_INK)
        rows.append(
            f'<tr><td class="nm">{_esc(s["name"])}</td><td>{_n(s.get("pe"))}</td>'
            f'<td style="color:{col};font-weight:600">{_n(pct, 0)}%</td>'
            f'<td>{_n(s.get("pb"), 2)}</td><td>{_n(s.get("pb_pct"), 0)}%</td>'
            f'<td><b>{_n((s.get("four_scores") or {}).get("估值"))}</b></td>'
            f'<td class="txt">{_esc((nv.get("narrative") or {}).get("估值") or "")}</td></tr>'
        )
    return (chart +
            f'<p class="hint">分位越低代表相对自身历史越便宜；估值得分 = 100 − (PE 分位×0.6 + PB 分位×0.4)。</p>'
            f'<table class="tb"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def _fund_block(stocks: Sequence[Dict[str, Any]], narr_map) -> str:
    names = [s["name"] for s in stocks]
    chart = _svg_diverging(
        names,
        [{"name": "近 5 日主力净额", "values": [s.get("fund_d5") for s in stocks]},
         {"name": "近 20 日主力净额", "values": [s.get("fund_d20") for s in stocks]}],
    )
    items = []
    for s in stocks:
        nv = narr_map.get(s["code"]) or {}
        d5, d20 = s.get("fund_d5"), s.get("fund_d20")
        items.append(
            f'<li><div class="fl-h"><b>{_esc(s["name"])}</b>'
            f'<span style="color:{_updown(d5)}">5日 {_signed(d5, 2, "亿")}</span>'
            f'<span style="color:{_updown(d20)}">20日 {_signed(d20, 2, "亿")}</span>'
            f'<span class="sc">资金分 {_n((s.get("four_scores") or {}).get("资金"))}</span></div>'
            f'<p>{_esc((nv.get("narrative") or {}).get("资金") or "")}</p></li>'
        )
    return (chart +
            f'<p class="hint">红柱为主力资金净流入、绿柱为净流出（深色为 5 日，浅色为 20 日）；'
            f'资金分以净流入占流通市值比例衡量，50 为中性。</p>'
            f'<ul class="flow">{"".join(items)}</ul>')


def _fundamental_block(stocks: Sequence[Dict[str, Any]], narr_map) -> str:
    names = [s["name"] for s in stocks]
    chart = _svg_grouped_bars(
        names,
        [{"name": "ROE(%)", "color": DIM_COLORS["基本面"], "values": [s.get("roe") for s in stocks]},
         {"name": "净利率(%)", "color": "#b39cf7", "values": [s.get("net_margin") for s in stocks]},
         {"name": "基本面得分", "color": C_ACCENT,
          "values": [(s.get("four_scores") or {}).get("基本面") for s in stocks]}],
        unit="",
    )
    head = "".join(f"<th>{h}</th>" for h in
                   ("标的", "ROE", "毛利率", "净利率", "资产负债率", "营收同比", "净利同比", "基本面得分"))
    rows = []
    for s in stocks:
        rows.append(
            f'<tr><td class="nm">{_esc(s["name"])}</td><td>{_n(s.get("roe"))}%</td>'
            f'<td>{_n(s.get("gross_margin"))}%</td><td>{_n(s.get("net_margin"))}%</td>'
            f'<td>{_n(s.get("debt_ratio"))}%</td>'
            f'<td style="color:{_updown(s.get("rev_yoy"))}">{_signed(s.get("rev_yoy"), 1, "%")}</td>'
            f'<td style="color:{_updown(s.get("profit_yoy"))}">{_signed(s.get("profit_yoy"), 1, "%")}</td>'
            f'<td><b>{_n((s.get("four_scores") or {}).get("基本面"))}</b></td></tr>'
        )
    reads = "".join(
        f'<li><b>{_esc(s["name"])}</b>：'
        f'{_esc(((narr_map.get(s["code"]) or {}).get("narrative") or {}).get("基本面") or "")}</li>'
        for s in stocks
    )
    return (chart +
            f'<table class="tb"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<ul class="reads">{reads}</ul>')


def _technical_block(stocks: Sequence[Dict[str, Any]], narr_map) -> str:
    track = _svg_position_track(stocks)
    head = "".join(f"<th>{h}</th>" for h in
                   ("标的", "现价", "MA20", "MA60", "RSI6", "RSI12", "MACD", "250日分位", "距高点", "技术得分"))
    rows = []
    for s in stocks:
        pos = float(s.get("pos250") or 50)
        pc = C_UP if pos >= 60 else (C_DOWN if pos <= 35 else C_WARN)
        rows.append(
            f'<tr><td class="nm">{_esc(s["name"])}</td><td>{_n(s.get("price"), 2)}</td>'
            f'<td>{_n(s.get("ma20"), 2)}</td><td>{_n(s.get("ma60"), 2)}</td>'
            f'<td>{_n(s.get("rsi6"), 0)}</td><td>{_n(s.get("rsi12"), 0)}</td>'
            f'<td>{_esc(s.get("macd_dir"))}</td>'
            f'<td style="color:{pc};font-weight:600">{pos:.0f}%</td>'
            f'<td style="color:{_updown(s.get("dist_high_pct"))}">{_signed(s.get("dist_high_pct"), 1, "%")}</td>'
            f'<td><b>{_n((s.get("four_scores") or {}).get("技术"))}</b></td></tr>'
        )
    reads = "".join(
        f'<li><b>{_esc(s["name"])}</b>：'
        f'{_esc(((narr_map.get(s["code"]) or {}).get("narrative") or {}).get("技术") or "")}</li>'
        for s in stocks
    )
    return (track +
            f'<p class="hint">轨道左端为 250 交易日最低价、右端为最高价，标记为当前价位置；'
            f'分位越高意味着追高风险越大。</p>'
            f'<table class="tb"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<ul class="reads">{reads}</ul>')


def _portfolio_block(pf: Dict[str, Any], nv_pf: Dict[str, Any]) -> str:
    donut = _svg_donut(pf.get("industry_share") or [], center_text=f'{pf.get("stock_count", 0)} 只')
    bars = _svg_weight_bars(pf.get("weights") or [])
    heat = _svg_heatmap(pf.get("corr_labels") or [], pf.get("corr_matrix") or [])
    gauge = _svg_score_gauge(pf.get("avg_composite") or 0)
    return (
        f'<div class="grid2">'
        f'<div class="panel"><h4>行业占比</h4>{donut}'
        f'<p class="read">{_esc(nv_pf.get("readConcentration") or "")}</p></div>'
        f'<div class="panel"><h4>个股权重</h4>{bars}'
        f'<h4 style="margin-top:10px">组合平均综合分</h4>{gauge}</div>'
        f'</div>'
        f'<div class="panel"><h4>个股相关性矩阵（近 60 个交易日日收益率）</h4>{heat}'
        f'<p class="hint">红色为正相关（同涨同跌，分散化作用弱），绿色为负相关（互为缓冲）。</p>'
        f'<p class="read">{_esc(nv_pf.get("readCorrelation") or "")}</p></div>'
        f'<div class="panel risk"><h4>组合风险特征</h4>'
        f'<div class="riskrow">'
        f'<div><span>年化波动率</span><b style="color:{C_WARN}">{_n(pf.get("vol_annual_pct"))}%</b></div>'
        f'<div><span>最大回撤</span><b style="color:{C_DOWN}">{_n(pf.get("max_drawdown_pct"))}%</b></div>'
        f'<div><span>HHI 集中度</span><b>{_n(pf.get("hhi"), 0)}</b></div>'
        f'<div><span>平均相关性</span><b>{_n(pf.get("corr_avg"), 2)}</b></div>'
        f'<div><span>第一大重仓</span><b>{_esc(pf.get("top1_name"))} {_n(pf.get("top1_pct"))}%</b></div>'
        f'<div><span>前三大合计</span><b>{_n(pf.get("top3_pct"))}%</b></div>'
        f'</div><p class="read">{_esc(nv_pf.get("readRisk") or "")}</p></div>'
    )


def _priority_block(nv_pf: Dict[str, Any]) -> str:
    head = "".join(f"<th>{h}</th>" for h in ("优先级", "标的", "综合分", "当前权重", "标签", "建议动作", "说明"))
    rows = []
    for p in nv_pf.get("priority") or []:
        lc = LABEL_COLORS.get(p.get("label"), C_SUB)
        rows.append(
            f'<tr><td class="rk">{p.get("rank")}</td>'
            f'<td class="nm">{_esc(p.get("name"))} <span class="code">{_esc(p.get("code"))}</span></td>'
            f'<td><b>{_n(p.get("composite"))}</b></td><td>{_n(p.get("weight_pct"))}%</td>'
            f'<td><span class="badge sm" style="background:{lc}">{_esc(p.get("label"))}</span></td>'
            f'<td>{_esc(p.get("action"))}</td><td class="txt">{_esc(p.get("note"))}</td></tr>'
        )
    return (f'<table class="tb"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<div class="advice"><h4>综合建议</h4><p>{_esc(nv_pf.get("advice") or "")}</p></div>')


# ---------------------------------------------------------------------------
# 样式
# ---------------------------------------------------------------------------

_CSS = f"""
:root{{--ink:{C_INK};--sub:{C_SUB};--line:{C_LINE};--soft:{C_BG_SOFT};
--up:{C_UP};--down:{C_DOWN};--accent:{C_ACCENT};}}
*{{box-sizing:border-box;}}
body{{margin:0;background:#eef0f4;color:var(--ink);
font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB","Source Han Sans SC",sans-serif;
font-size:12.5px;line-height:1.6;-webkit-print-color-adjust:exact;print-color-adjust:exact;}}
.wrap{{max-width:920px;margin:0 auto;padding:18px;}}
.rp-head{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:14px;}}
.rp-title-row{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;}}
h1{{margin:0;font-size:21px;letter-spacing:.5px;}}
.rp-meta{{margin:6px 0 0;color:var(--sub);font-size:11px;}}
.rp-actions button{{border:1px solid var(--accent);background:var(--accent);color:#fff;
border-radius:8px;padding:7px 14px;font-size:12px;cursor:pointer;}}
.kpi-row{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-top:14px;}}
.kpi{{background:var(--soft);border-radius:9px;padding:8px 10px;display:flex;flex-direction:column;gap:2px;}}
.kpi-l{{font-size:10px;color:var(--sub);}}
.kpi-v{{font-size:14px;font-weight:700;}}
.sec{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 20px 18px;
margin-bottom:14px;page-break-inside:avoid;break-inside:avoid;}}
.sec-head{{border-bottom:1px solid var(--line);padding-bottom:9px;margin-bottom:13px;position:relative;}}
.sec-idx{{display:inline-block;background:var(--accent);color:#fff;font-size:10px;font-weight:700;
border-radius:5px;padding:1px 6px;margin-right:7px;vertical-align:2px;}}
.sec-head h2{{display:inline;font-size:15.5px;margin:0;}}
.sec-sub{{margin:5px 0 0;color:var(--sub);font-size:11px;}}
.cards{{display:grid;grid-template-columns:repeat(2,1fr);gap:11px;}}
.card{{border:1px solid var(--line);border-radius:10px;padding:11px 12px;background:#fff;
page-break-inside:avoid;break-inside:avoid;}}
.card-top{{display:flex;justify-content:space-between;align-items:flex-start;}}
.card-id h3{{margin:0;font-size:14px;}}
.code{{color:var(--sub);font-size:10px;}}
.card-tag{{text-align:right;}}
.badge{{display:inline-block;color:#fff;font-size:11px;font-weight:700;border-radius:5px;padding:2px 8px;}}
.badge.sm{{font-size:10px;padding:1px 6px;}}
.score{{display:block;font-size:10px;color:var(--sub);margin-top:3px;}}
.card-mid{{display:flex;gap:8px;align-items:center;margin-top:6px;}}
.radar{{flex:0 0 132px;}}
.kvs{{flex:1;display:grid;grid-template-columns:repeat(2,1fr);gap:2px 8px;}}
.kv{{display:flex;justify-content:space-between;font-size:10.5px;border-bottom:1px dotted var(--line);padding:1px 0;}}
.kv span{{color:var(--sub);}}
.chips{{display:flex;gap:5px;margin-top:7px;flex-wrap:wrap;}}
.chip{{font-size:10px;font-weight:600;border-radius:5px;padding:2px 7px;}}
.card-note{{margin:7px 0 0;font-size:10.5px;color:#4a5160;background:var(--soft);
border-radius:7px;padding:6px 8px;}}
.tb{{width:100%;border-collapse:collapse;margin-top:11px;font-size:11px;}}
.tb th{{background:var(--soft);color:var(--sub);font-weight:600;text-align:right;
padding:6px 7px;border-bottom:1px solid var(--line);white-space:nowrap;}}
.tb td{{text-align:right;padding:6px 7px;border-bottom:1px solid var(--line);}}
.tb th:first-child,.tb td:first-child,.tb .nm{{text-align:left;}}
.tb .txt{{text-align:left;color:#4a5160;font-size:10.5px;line-height:1.5;}}
.tb .rk{{text-align:center;font-weight:700;color:var(--accent);}}
.hint{{margin:8px 0 0;font-size:10.5px;color:var(--sub);}}
.flow{{list-style:none;padding:0;margin:10px 0 0;}}
.flow li{{border-top:1px solid var(--line);padding:7px 0;}}
.fl-h{{display:flex;gap:12px;align-items:baseline;font-size:11.5px;}}
.fl-h .sc{{margin-left:auto;color:var(--sub);font-size:10.5px;}}
.flow p{{margin:3px 0 0;font-size:10.5px;color:#4a5160;}}
.reads{{margin:10px 0 0;padding-left:17px;font-size:10.5px;color:#4a5160;}}
.reads li{{margin-bottom:4px;}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:11px;}}
.panel{{border:1px solid var(--line);border-radius:10px;padding:11px 12px;margin-bottom:11px;}}
.panel h4{{margin:0 0 7px;font-size:12px;}}
.read{{margin:8px 0 0;font-size:10.5px;color:#3d4351;background:var(--soft);
border-left:3px solid var(--accent);border-radius:0 7px 7px 0;padding:7px 9px;}}
.riskrow{{display:grid;grid-template-columns:repeat(6,1fr);gap:7px;}}
.riskrow div{{background:var(--soft);border-radius:8px;padding:7px 8px;display:flex;
flex-direction:column;gap:2px;}}
.riskrow span{{font-size:10px;color:var(--sub);}}
.riskrow b{{font-size:13px;}}
.advice{{margin-top:12px;border:1px solid var(--line);border-left:4px solid var(--accent);
border-radius:0 10px 10px 0;background:var(--soft);padding:11px 13px;}}
.advice h4{{margin:0 0 5px;font-size:12.5px;}}
.advice p{{margin:0;font-size:11.5px;line-height:1.75;}}
.disc{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:13px 18px;
color:var(--sub);font-size:10.5px;line-height:1.7;}}
.disc b{{color:var(--ink);}}
@media print{{
  body{{background:#fff;}}
  .wrap{{max-width:none;padding:0;}}
  .no-print{{display:none !important;}}
  .sec,.rp-head,.disc{{border-color:#dcdfe6;box-shadow:none;}}
}}
@page{{size:A4;margin:12mm;}}
"""


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def render_html_report(report: Dict[str, Any]) -> str:
    """把 `{meta, stocks, portfolio, narrative}` 渲染为自包含 HTML（可直接打印为 PDF）。"""
    meta = report.get("meta") or {}
    stocks: List[Dict[str, Any]] = list(report.get("stocks") or [])
    pf = report.get("portfolio") or {}
    nv = report.get("narrative") or {}
    nv_pf = nv.get("portfolio") or {}
    narr_map = {x.get("code"): x for x in (nv.get("per_stock") or [])}

    engine_txt = "规则引擎（确定性）" if nv.get("engine") == "heuristic" else str(nv.get("engine") or "-")

    body = "".join([
        _header(meta, pf),
        _section("个股速览卡片", "四维打分（估值 / 基本面 / 资金 / 技术）与关键指标一览",
                 _cards(stocks, narr_map), "01"),
        _section("估值对比", "当前 PE / PB 及其在自身历史序列中的百分位",
                 _valuation_block(stocks, narr_map), "02"),
        _section("主力资金动向", "近 5 日与近 20 日主力资金净流入（红入绿出）",
                 _fund_block(stocks, narr_map), "03"),
        _section("基本面质量对比", "盈利能力、成长性与杠杆水平横向比较",
                 _fundamental_block(stocks, narr_map), "04"),
        _section("技术位置", "250 交易日价格区间分位、均线与动量指标",
                 _technical_block(stocks, narr_map), "05"),
        _section("组合结构", "行业占比、个股权重、相关性矩阵与风险特征",
                 _portfolio_block(pf, nv_pf), "06"),
        _section("加减仓优先级与综合建议", "按综合分排序的调整优先级与整体结论",
                 _priority_block(nv_pf), "07"),
        f'<div class="disc"><b>免责声明：</b>本报告由系统依据公开市场数据自动计算生成，'
        f'四维打分与结论均来自既定规则口径（叙事引擎：{_esc(engine_txt)}），'
        f'历史数据不代表未来表现。报告仅用于投顾内部研究与客户沟通参考，'
        f'<b>不构成任何投资建议</b>，亦不构成买卖要约。投资者应结合自身风险承受能力独立决策，'
        f'据此操作风险自担。</div>',
    ])

    title = _esc(meta.get("title") or "持仓体检报告")
    date = _esc(meta.get("generated_date") or "")
    return (
        "<!doctype html>\n"
        f'<html lang="zh-CN"><head><meta charset="utf-8"/>'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"/>'
        f'<title>{title}{" · " + date if date else ""}</title>'
        f"<style>{_CSS}</style></head>"
        f'<body><div class="wrap">{body}</div></body></html>'
    )
