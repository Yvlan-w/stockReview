// ============================================================
// 大盘组件：今日盯大盘 / 刷新 / 板块热力 / 领涨方向 / 驱动因素
// ============================================================
import { marketDataState, fetchRealtimeMarketData, fetchShKlineData, fetchSectors, fetchMarketAnalysis } from '../services/marketService.js';
import { showToast } from '../core/ui.js';
import { renderVolumeChart } from './overview.js';
import { renderMarketTicker } from './workbench.js';
import { isModuleVisible } from '../permissions/modules.js';

// 当前选中的成交量图指数
let selectedVolumeIndex = '1.000001';

// --- 今日盯大盘 ---
function _formatAge(date) {
    if (!date) return '';
    const sec = Math.floor((new Date() - date) / 1000);
    if (sec < 5) return '刚刚';
    if (sec < 60) return `${sec}秒前`;
    if (sec < 3600) return `${Math.floor(sec / 60)}分钟前`;
    return `${Math.floor(sec / 3600)}小时前`;
}

export function renderMarketOverview() {
    if (!isModuleVisible('market_overview')) return;  // 角色/账户模块权限控制
    const m = marketDataState.realtime;

    // 时间与数据源徽标
    const now = new Date();
    document.getElementById('marketTime').textContent = now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    const badge = document.getElementById('marketDataBadge');
    const badgeText = document.getElementById('marketDataBadgeText');
    const sourceBadge = document.getElementById('marketDataSource');

    const st = marketDataState.fetchStatus;
    const age = _formatAge(marketDataState.lastUpdated);
    const stale = marketDataState.lastUpdated && (now - marketDataState.lastUpdated) > 60_000;

    if (marketDataState.source === 'real') {
        if (st === 'fail' || stale) {
            badge.className = 'flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-amber-50 text-amber-700 border border-amber-200';
            badgeText.textContent = stale ? `数据过期 · ${age}` : `更新失败 · ${age}`;
            sourceBadge.textContent = '东方财富';
            sourceBadge.className = 'hidden sm:inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full bg-amber-50 text-amber-700 border border-amber-200';
        } else {
            badge.className = 'flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-positive/10 text-positive';
            badgeText.textContent = '实时';
            sourceBadge.textContent = `东方财富 · ${age}`;
            sourceBadge.className = 'hidden sm:inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full bg-positive/10 text-positive border border-positive/20';
        }
    } else {
        badge.className = 'flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-surface-strong text-muted border border-hairline-soft';
        badgeText.textContent = marketDataState.loading ? '加载中' : '离线';
        sourceBadge.textContent = '暂无数据';
        sourceBadge.className = 'hidden sm:inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full bg-surface-strong text-muted border border-hairline-soft';
    }

    // 无实时数据：显示空态
    if (!m) {
        document.getElementById('marketIndicesLarge').innerHTML = '<div class="col-span-2 sm:col-span-4 text-center text-sm text-muted py-8">暂无行情数据</div>';
        document.getElementById('totalVolume').textContent = '--';
        document.getElementById('volumeChange').textContent = '';
        document.getElementById('advDeclineBar').innerHTML = '';
        document.getElementById('advCount').textContent = '--';
        document.getElementById('advPct').textContent = '';
        document.getElementById('decCount').textContent = '--';
        document.getElementById('decPct').textContent = '';
        document.getElementById('totalStocks').textContent = '暂无数据';
        return;
    }

    // 1. 四大指数
    const indicesHtml = m.indices.map(idx => {
        const isUp = idx.change >= 0;
        const colorClass = isUp ? 'text-up' : 'text-down';
        const arrow = isUp
            ? '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 10l7-7m0 0l7 7m-7-7v18"/></svg>'
            : '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 14l-7 7m0 0l-7-7m7 7V3"/></svg>';

        return `
            <div class="index-card ${isUp ? 'up' : 'down'}">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-sm font-medium text-ink">${idx.name}</span>
                    <span class="${colorClass}">${arrow}</span>
                </div>
                <div class="font-mono text-2xl font-semibold ${colorClass}">${idx.value.toFixed(2)}</div>
                <div class="flex items-center justify-between mt-1.5">
                    <span class="text-xs text-muted">前一日: <span class="font-mono">${idx.prevClose.toFixed(2)}</span></span>
                    <span class="font-mono text-sm font-semibold ${colorClass}">${isUp ? '+' : ''}${idx.change.toFixed(2)} (${isUp ? '+' : ''}${idx.changePct.toFixed(2)}%)</span>
                </div>
            </div>
        `;
    }).join('');
    document.getElementById('marketIndicesLarge').innerHTML = indicesHtml;

    // 2. 两市成交额
    const volEl = document.getElementById('totalVolume');
    const volChangeEl = document.getElementById('volumeChange');
    volEl.textContent = `${Math.round(m.totalVolume).toLocaleString()} 亿`;
    const volDiff = m.totalVolume - m.prevVolume;
    const volPct = m.prevVolume > 0 ? (volDiff / m.prevVolume * 100).toFixed(1) : '0.0';
    const volColor = volDiff >= 0 ? 'text-up' : 'text-down';
    volChangeEl.className = `text-xs font-mono ${volColor}`;
    volChangeEl.textContent = `${volDiff >= 0 ? '↑ +' : '↓ '}${Math.abs(Math.round(volDiff)).toLocaleString()}亿 (${volDiff >= 0 ? '+' : ''}${volPct}%)`;

    // 3. 涨跌家数
    const adv = m.advCount;
    const dec = m.decCount;
    const flat = m.flatCount;
    const total = m.totalStocks;
    const advPctVal = total > 0 ? (adv / total * 100) : 0;
    const decPctVal = total > 0 ? (dec / total * 100) : 0;
    const flatPctVal = total > 0 ? (flat / total * 100) : 0;

    const bar = document.getElementById('advDeclineBar');
    bar.innerHTML = `
        <div class="bg-up flex items-center justify-center transition-all duration-500" style="width: ${advPctVal}%;">
            ${advPctVal > 10 ? `<span class="text-white text-xs font-semibold font-mono">${advPctVal.toFixed(1)}%</span>` : ''}
        </div>
        <div class="bg-hairline flex items-center justify-center transition-all duration-500" style="width: ${flatPctVal}%;">
            ${flatPctVal > 3 ? `<span class="text-muted text-xs font-mono">${flatPctVal.toFixed(1)}%</span>` : ''}
        </div>
        <div class="bg-down flex items-center justify-center transition-all duration-500" style="width: ${decPctVal}%;">
            ${decPctVal > 10 ? `<span class="text-white text-xs font-semibold font-mono">${decPctVal.toFixed(1)}%</span>` : ''}
        </div>
    `;

    document.getElementById('advCount').textContent = adv.toLocaleString() + ' 家';
    document.getElementById('advPct').textContent = `(${advPctVal.toFixed(1)}%)`;
    document.getElementById('decCount').textContent = dec.toLocaleString() + ' 家';
    document.getElementById('decPct').textContent = `(${decPctVal.toFixed(1)}%)`;
    document.getElementById('totalStocks').textContent = `共 ${total.toLocaleString()} 只股票 · 平盘 ${flat} 家`;
}

// --- 从实时源刷新大盘数据（东方财富） ---
// 实时行情与 K 线独立并行获取，互不拖累；失败仅空态该板块，另一板块照常更新
export async function refreshMarket() {
    if (marketDataState.loading) return;
    marketDataState.loading = true;

    const refreshBtn = document.getElementById('marketRefreshBtn');
    const originalBtnHtml = refreshBtn.innerHTML;
    refreshBtn.innerHTML = `
        <svg class="w-3.5 h-3.5 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" class="opacity-25"/>
            <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" class="opacity-75"/>
        </svg>
        刷新中
    `;
    refreshBtn.disabled = true;

    // 并行发起，各自独立 try/catch
    const klinePromise = isModuleVisible('index_turnover')
        ? fetchShKlineData(selectedVolumeIndex)
            .then(k => {
                marketDataState.kline = k;
                renderVolumeChart();
                return true;
            })
            .catch(err => {
                console.warn('K线拉取失败，成交量图跳过:', err.message);
                renderVolumeChart();
                return false;
            })
        : Promise.resolve(true);  // 模块隐藏：跳过拉取

    const realtimePromise = fetchRealtimeMarketData()
        .then(r => {
            marketDataState.realtime = r;
            marketDataState.source = 'real';
            marketDataState.lastUpdated = new Date();
            marketDataState.error = null;
            if (isModuleVisible('market_overview')) renderMarketOverview();
            renderMarketTicker();
            return true;
        })
        .catch(err => {
            console.warn('实时行情拉取失败:', err.message);
            marketDataState.error = err.message;
            if (!marketDataState.realtime) {
                marketDataState.source = 'mock';
            }
            if (isModuleVisible('market_overview')) renderMarketOverview();
            return false;
        });

    const sectorPromise = isModuleVisible('sector_performance')
        ? fetchSectors(80)
            .then(s => {
                marketDataState.sectors = s;
                renderSectorHeatmap();
                return true;
            })
            .catch(err => {
                console.warn('板块数据拉取失败:', err.message);
                marketDataState.sectors = null;
                renderSectorHeatmap();
                return false;
            })
        : Promise.resolve(true);  // 模块隐藏：跳过拉取

    const analysisPromise = fetchMarketAnalysis()
        .then(a => {
            marketDataState.analysis = a;
            renderLeadershipAnalysis();
            renderDriverAnalysis();
            return true;
        })
        .catch(err => {
            console.warn('市场分析拉取失败:', err.message);
            marketDataState.analysis = null;
            renderLeadershipAnalysis();
            renderDriverAnalysis();
            return false;
        });

    const [klineOk, rtOk, sectorOk, analysisOk] = await Promise.all([klinePromise, realtimePromise, sectorPromise, analysisPromise]);

    // 统一 toast：根据各自成功情况组合
    const allOk = rtOk && klineOk && sectorOk;
    if (allOk) {
        showToast('✅ 大盘数据已更新', 'success');
    } else if (rtOk && klineOk) {
        showToast('⚠️ 行情与K线已更新，板块数据获取失败', 'warning');
    } else if (rtOk && sectorOk) {
        showToast('⚠️ 行情与板块已更新，K线获取失败', 'warning');
    } else if (rtOk) {
        showToast('⚠️ 行情已更新，K线与板块获取失败', 'warning');
    } else if (marketDataState.realtime) {
        showToast('⚠️ 刷新失败，已保留上次行情数据', 'warning');
    } else {
        showToast('⚠️ 行情数据获取失败，请稍后重试', 'warning');
    }

    marketDataState.loading = false;
    refreshBtn.innerHTML = originalBtnHtml;
    refreshBtn.disabled = false;
}

// --- 板块表现 Treemap（行业板块，squarified 布局） ---
// 面积 = 成交额（精确映射，使用平方根缩放减少极端值影响），颜色 = 涨跌幅
export function renderSectorHeatmap() {
    if (!isModuleVisible('sector_performance')) return;  // 角色/账户模块权限控制
    const container = document.getElementById('sectorHeatmap');
    const sectors = marketDataState.sectors;

    if (!sectors || sectors.length === 0) {
        container.innerHTML = '<div class="text-center text-sm text-muted py-8">暂无板块数据</div>';
        return;
    }

    // 过滤无效数据并按成交额降序排序
    const validSectors = sectors
        .filter(s => s.turnover && s.turnover > 0)
        .sort((a, b) => b.turnover - a.turnover);

    if (validSectors.length === 0) {
        container.innerHTML = '<div class="text-center text-sm text-muted py-8">暂无有效板块数据</div>';
        return;
    }

    // 根据行业数量动态计算最小高度，确保所有区块完整显示
    const count = validSectors.length;
    const minHeight = Math.max(400, Math.round(count * 7));
    container.innerHTML = `<div class="sector-treemap" id="sectorTreemap" style="height:${minHeight}px"></div>`;
    const treemapEl = document.getElementById('sectorTreemap');
    const containerW = treemapEl.clientWidth;
    const containerH = treemapEl.clientHeight;

    // 平方根缩放成交额 → 目标面积
    const totalSqrtTurnover = validSectors.reduce((sum, s) => sum + Math.sqrt(s.turnover), 0);

    const items = validSectors.map(s => ({
        ...s,
        targetArea: Math.sqrt(s.turnover) / totalSqrtTurnover,
    }));

    // Squarified Treemap 布局
    const layout = _squarifyPixel(items, containerW, containerH);

    // 检测是否溢出，若溢出则增加高度重布局
    const maxBottom = Math.max(...layout.map(b => b.y + b.h));
    const maxRight = Math.max(...layout.map(b => b.x + b.w));
    if (maxBottom > containerH + 1 || maxRight > containerW + 1) {
        const neededH = Math.ceil(maxBottom) + 4;
        treemapEl.style.height = neededH + 'px';
        const newH = neededH;
        const newW = containerW;
        const relayout = _squarifyPixel(items, newW, newH);
        _renderTreemapBlocks(treemapEl, relayout);
    } else {
        _renderTreemapBlocks(treemapEl, layout);
    }

    // 绑定自定义浮窗
    _bindTreemapTooltip(treemapEl);
}

function _renderTreemapBlocks(treemapEl, layout) {
    const maxAbsPct = Math.max(...layout.map(b => Math.abs(b.data.changePct) || 0.01));

    layout.forEach(block => {
        const d = block.data;
        const pct = d.changePct || 0;
        const absPct = Math.abs(pct);
        const intensity = Math.min(1, absPct / maxAbsPct);
        const bgColor = _treemapColor(pct, intensity);
        const textColor = pct >= 0 ? '#991b1b' : pct < 0 ? '#065f46' : '#6b7280';

        const areaPx = block.w * block.h;
        const sizeClass = areaPx > 2000 ? '' : areaPx > 600 ? 'sector-treemap-small' : 'sector-treemap-tiny';

        const el = document.createElement('div');
        el.className = `sector-treemap-block ${sizeClass}`;
        el.dataset.name = d.name;
        el.dataset.pct = pct.toFixed(2);
        el.dataset.turnover = _fmtTurnover(d.turnover);
        el.style.left = block.x + 'px';
        el.style.top = block.y + 'px';
        el.style.width = block.w + 'px';
        el.style.height = block.h + 'px';
        el.style.backgroundColor = bgColor;
        el.style.color = textColor;
        el.innerHTML = `
            <div class="tm-label-wrap">
                <span class="tm-name">${d.name}</span>
            </div>
            <div class="tm-footer">
                <span class="tm-pct" style="color:${textColor}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</span>
                <span class="tm-turnover">${_fmtTurnover(d.turnover)}</span>
            </div>
        `;
        treemapEl.appendChild(el);
    });
}

function _bindTreemapTooltip(treemapEl) {
    let tooltip = document.getElementById('sectorTooltip');
    if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = 'sectorTooltip';
        tooltip.className = 'sector-tooltip';
        document.body.appendChild(tooltip);
    }

    let visible = false;

    treemapEl.addEventListener('mousemove', (e) => {
        const block = e.target.closest('.sector-treemap-block');
        if (!block) {
            if (visible) {
                tooltip.style.opacity = '0';
                tooltip.style.pointerEvents = 'none';
                visible = false;
            }
            return;
        }

        const name = block.dataset.name || '';
        const pct = parseFloat(block.dataset.pct || '0');
        const turnover = block.dataset.turnover || '';
        const isUp = pct > 0;
        const isDown = pct < 0;
        const color = isUp ? '#cf202f' : isDown ? '#05b169' : '#7c828a';
        const sign = pct > 0 ? '+' : '';
        const pctText = `${sign}${pct.toFixed(2)}%`;

        tooltip.innerHTML = `
            <div class="sector-tooltip-header">
                <span class="sector-tooltip-name">${name}</span>
            </div>
            <div class="sector-tooltip-row">
                <span class="sector-tooltip-label">涨跌幅</span>
                <span class="sector-tooltip-value" style="color:${color}">${pctText}</span>
            </div>
            <div class="sector-tooltip-row">
                <span class="sector-tooltip-label">成交额</span>
                <span class="sector-tooltip-value">${turnover}</span>
            </div>
        `;

        // 定位到鼠标附近，确保不超出视口
        const padding = 16;
        const tw = tooltip.offsetWidth || 180;
        const th = tooltip.offsetHeight || 80;
        let left = e.clientX + padding;
        let top = e.clientY + padding;

        if (left + tw > window.innerWidth) left = e.clientX - tw - padding;
        if (top + th > window.innerHeight) top = e.clientY - th - padding;
        if (left < 0) left = 8;
        if (top < 0) top = 8;

        tooltip.style.left = left + 'px';
        tooltip.style.top = top + 'px';
        tooltip.style.opacity = '1';
        tooltip.style.pointerEvents = 'auto';
        visible = true;
    });

    treemapEl.addEventListener('mouseleave', () => {
        tooltip.style.opacity = '0';
        tooltip.style.pointerEvents = 'none';
        visible = false;
    });
}

// --- Treemap squarified 布局算法（像素版本） ---
// 标准 Squarified Treemap 算法：
// 1. 沿短边放置行
// 2. 行厚度 = rowArea / 长边剩余
// 3. 每项在短边上的长度 = itemArea / 行厚度
// 4. 无间隙、无重叠
function _squarifyPixel(items, W, H) {
    const totalArea = W * H;
    const totalTargetArea = items.reduce((s, it) => s + it.targetArea, 0);
    const scale = totalArea / totalTargetArea;
    const queue = items.map(it => ({ ...it, area: it.targetArea * scale }));

    const result = [];
    let rect = { x: 0, y: 0, w: W, h: H };
    let lastRowStart = -1;
    let lastRowIsHorizontal = true;

    while (queue.length > 0) {
        const shortSide = Math.min(rect.w, rect.h);
        const longSide = Math.max(rect.w, rect.h);
        const row = [];

        const candidates = [...queue].sort((a, b) => b.area - a.area);

        while (candidates.length > 0) {
            const candidate = candidates[0];
            const testRow = [...row, candidate];
            const newRatio = _worstRatio(testRow, shortSide, longSide);

            if (row.length === 0 || newRatio <= _worstRatio(row, shortSide, longSide)) {
                row.push(candidate);
                candidates.shift();
            } else {
                break;
            }
        }

        if (row.length === 0 && candidates.length > 0) {
            row.push(candidates.shift());
        }

        for (const item of row) {
            const idx = queue.indexOf(item);
            if (idx !== -1) queue.splice(idx, 1);
        }

        const rowArea = row.reduce((s, it) => s + it.area, 0);
        lastRowStart = result.length;

        if (rect.w >= rect.h) {
            lastRowIsHorizontal = true;
            const rowThickness = rowArea / rect.w;
            let cx = rect.x;
            for (const item of row) {
                const itemW = item.area / rowThickness;
                result.push({
                    x: cx, y: rect.y,
                    w: itemW,
                    h: rowThickness,
                    data: item,
                });
                cx += itemW;
            }
            rect.y += rowThickness;
            rect.h -= rowThickness;
        } else {
            lastRowIsHorizontal = false;
            const rowThickness = rowArea / rect.h;
            let cy = rect.y;
            for (const item of row) {
                const itemH = item.area / rowThickness;
                result.push({
                    x: rect.x, y: cy,
                    w: rowThickness,
                    h: itemH,
                    data: item,
                });
                cy += itemH;
            }
            rect.x += rowThickness;
            rect.w -= rowThickness;
        }
    }

    // 修正最后一行，消除浮点精度导致的间隙
    if (lastRowStart >= 0) {
        if (lastRowIsHorizontal) {
            const lastRow = result.slice(lastRowStart);
            const targetBottom = H;
            const actualBottom = lastRow[0].y + lastRow[0].h;
            if (Math.abs(targetBottom - actualBottom) > 0.5) {
                const diff = targetBottom - actualBottom;
                const count = lastRow.length;
                for (let i = 0; i < count; i++) {
                    lastRow[i].y += diff;
                }
            }
        } else {
            const lastRow = result.slice(lastRowStart);
            const targetRight = W;
            const actualRight = lastRow[0].x + lastRow[0].w;
            if (Math.abs(targetRight - actualRight) > 0.5) {
                const diff = targetRight - actualRight;
                for (const block of lastRow) {
                    block.x += diff;
                }
            }
        }
    }

    return result;
}

function _worstRatio(row, shortSide, longSide) {
    // 计算行的最坏长宽比
    // 行厚度 = rowArea / longSide
    // 每项宽度（沿短边）= itemArea / rowThickness
    // 每项高度（= rowThickness）
    const rowArea = row.reduce((s, it) => s + it.area, 0);
    const rowThickness = rowArea / longSide;

    let worstRatio = 0;
    for (const item of row) {
        const itemLong = item.area / rowThickness;
        const itemShort = rowThickness;
        const ratio = Math.max(itemLong / itemShort, itemShort / itemLong);
        if (ratio > worstRatio) worstRatio = ratio;
    }
    return worstRatio;
}

// --- Treemap 颜色映射 ---
// 涨 → 红色系（深浅映射幅度），跌 → 绿色系，平 → 灰色
function _treemapColor(pct, intensity) {
    // intensity: 0.0 ~ 1.0
    const alpha = 0.18 + intensity * 0.62;  // 0.18 ~ 0.80
    if (pct > 0) {
        return `rgba(220, 38, 38, ${alpha.toFixed(2)})`;
    } else if (pct < 0) {
        return `rgba(22, 163, 74, ${alpha.toFixed(2)})`;
    }
    return 'rgba(107, 114, 128, 0.22)';
}

function _fmtTurnover(v) {
    if (!v || v <= 0) return '--';
    if (v >= 1e12) return (v / 1e12).toFixed(1) + '万亿';
    if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿';
    if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
    return v.toFixed(0);
}

// --- 领涨方向分析（高低切） ---
export function renderLeadershipAnalysis() {
    const container = document.getElementById('leadershipAnalysis');
    if (!container) return;
    const analysis = marketDataState.analysis;

    if (!analysis || !analysis.rotation) {
        container.innerHTML = '<div class="text-center text-sm text-muted py-8">暂无领涨方向分析</div>';
        return;
    }

    const rot = analysis.rotation;
    const leaders = rot.leaders || [];
    const laggards = rot.laggards || [];
    const breadth = rot.marketBreadth || {};

    // 轮动信号样式
    const signalColors = {
        strong: 'bg-red-50 text-red-700 border-red-200',
        moderate: 'bg-amber-50 text-amber-700 border-amber-200',
        weak: 'bg-blue-50 text-blue-700 border-blue-200',
        none: 'bg-gray-50 text-gray-600 border-gray-200',
    };
    const signalLabels = {
        strong: '强烈',
        moderate: '中等',
        weak: '温和',
        none: '无明显信号',
    };
    const signalCls = signalColors[rot.rotationSignal] || signalColors.none;
    const signalLabel = signalLabels[rot.rotationSignal] || '无明显信号';

    // 市场宽度
    const breadthHtml = `
        <div class="flex items-center gap-4 text-xs text-muted">
            <span>上涨 <span class="text-up font-semibold">${breadth.upCount || 0}</span> 家</span>
            <span>下跌 <span class="text-down font-semibold">${breadth.downCount || 0}</span> 家</span>
            <span>比例 <span class="font-semibold">${((breadth.upRatio || 0) * 100).toFixed(0)}:${((breadth.downRatio || 0) * 100).toFixed(0)}</span></span>
        </div>
    `;

    // 领涨行业
    let leadersHtml = '';
    if (leaders.length > 0) {
        leadersHtml = `
            <div class="mb-4">
                <div class="flex items-center gap-2 mb-2">
                    <span class="text-xs font-medium text-muted">领涨方向</span>
                    <span class="text-xs px-2 py-0.5 rounded border ${signalCls}">${signalLabel}</span>
                </div>
                <div class="space-y-1.5">
                    ${leaders.map((l, i) => `
                        <div class="flex items-center gap-2 p-2 rounded-lg bg-red-50/60 border border-red-100">
                            <span class="w-5 h-5 rounded-full bg-red-500 text-white text-xs flex items-center justify-center font-semibold">${i + 1}</span>
                            <span class="text-sm font-medium text-ink flex-1 truncate">${l.name}</span>
                            ${l.themeSummary ? `<span class="text-xs text-muted px-1.5 py-0.5 rounded bg-white border border-hairline">${l.themeSummary}</span>` : ''}
                            <span class="text-sm font-semibold text-up">${l.changePct >= 0 ? '+' : ''}${l.changePct.toFixed(2)}%</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    // 领跌行业
    let laggardsHtml = '';
    if (laggards.length > 0) {
        laggardsHtml = `
            <div>
                <div class="flex items-center gap-2 mb-2">
                    <span class="text-xs font-medium text-muted">领跌方向</span>
                </div>
                <div class="space-y-1.5">
                    ${laggards.map((l) => `
                        <div class="flex items-center gap-2 p-2 rounded-lg bg-green-50/60 border border-green-100">
                            <span class="text-sm font-medium text-ink flex-1 truncate">${l.name}</span>
                            ${l.themeSummary ? `<span class="text-xs text-muted px-1.5 py-0.5 rounded bg-white border border-hairline">${l.themeSummary}</span>` : ''}
                            <span class="text-sm font-semibold text-down">${l.changePct >= 0 ? '+' : ''}${l.changePct.toFixed(2)}%</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    container.innerHTML = `
        <div class="mb-3">${breadthHtml}</div>
        ${leadersHtml}
        ${laggardsHtml}
        <div class="mt-3 text-xs text-muted leading-relaxed">${rot.rotationText || ''}</div>
    `;
}

// --- 核心驱动因素 ---
export function renderDriverAnalysis() {
    const container = document.getElementById('driverAnalysis');
    if (!container) return;
    const analysis = marketDataState.analysis;

    if (!analysis || !analysis.drivers) {
        container.innerHTML = '<div class="text-center text-sm text-muted py-8">暂无驱动因素分析</div>';
        return;
    }

    const d = analysis.drivers;
    const cap = d.capitalFlow || {};
    const sent = d.sentiment || {};
    const struct = d.structure || {};
    const risks = d.riskFlags || [];

    // 资金面
    const capitalIcon = cap.direction === 'inflow' ? '📈' : cap.direction === 'outflow' ? '📉' : '➖';
    const capitalColor = cap.direction === 'inflow' ? 'text-up' : cap.direction === 'outflow' ? 'text-down' : 'text-muted';

    // 情绪面
    const sentimentLabels = { extreme: '极度乐观', strong: '积极乐观', moderate: '中性均衡', weak: '情绪偏弱' };
    const sentimentColors = {
        extreme: 'bg-red-50 text-red-700 border-red-200',
        strong: 'bg-orange-50 text-orange-700 border-orange-200',
        moderate: 'bg-blue-50 text-blue-700 border-blue-200',
        weak: 'bg-gray-50 text-gray-600 border-gray-200',
    };
    const sentLabel = sentimentLabels[sent.level] || '中性';
    const sentColor = sentimentColors[sent.level] || sentimentColors.moderate;

    // 领涨主题分类
    let categoriesHtml = '';
    if (struct.leadingCategories && struct.leadingCategories.length > 0) {
        categoriesHtml = `
            <div class="mb-3">
                <div class="text-xs font-medium text-muted mb-2">领涨板块分类</div>
                <div class="space-y-1.5">
                    ${struct.leadingCategories.slice(0, 3).map(c => `
                        <div class="flex items-center gap-2 text-sm">
                            <span class="font-medium text-ink">${c.category}</span>
                            <span class="text-xs text-muted">·</span>
                            <span class="text-xs text-muted">${(c.themes || []).slice(0, 2).join('、')}</span>
                            <span class="text-xs ml-auto font-semibold text-up">+${c.maxPct.toFixed(2)}%</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    // 风险提示
    let riskHtml = '';
    if (risks.length > 0) {
        riskHtml = `
            <div class="mt-3 p-2.5 rounded-lg bg-amber-50 border border-amber-200">
                <div class="text-xs font-medium text-amber-800 mb-1">⚠️ 风险提示</div>
                ${risks.map(r => `<div class="text-xs text-amber-700 leading-relaxed">· ${r}</div>`).join('')}
            </div>
        `;
    }

    // 综合摘要
    const summary = d.summary || '';

    container.innerHTML = `
        <div class="space-y-3">
            <div>
                <div class="flex items-center gap-2 mb-2">
                    <span class="text-xs font-medium text-muted">资金面</span>
                    <span class="${capitalColor} text-sm">${capitalIcon} ${cap.direction === 'inflow' ? '净流入' : cap.direction === 'outflow' ? '净流出' : '持平'}</span>
                </div>
                <div class="text-xs text-muted leading-relaxed">${cap.text || ''}</div>
            </div>
            <div>
                <div class="flex items-center gap-2 mb-2">
                    <span class="text-xs font-medium text-muted">情绪面</span>
                    <span class="text-xs px-2 py-0.5 rounded border ${sentColor}">${sentLabel}</span>
                </div>
                <div class="text-xs text-muted leading-relaxed">${sent.text || ''}</div>
            </div>
            ${categoriesHtml}
            <div class="p-3 rounded-lg bg-ink/5 border border-hairline">
                <div class="text-xs font-medium text-ink mb-1">综合判断</div>
                <div class="text-xs text-muted leading-relaxed">${summary}</div>
            </div>
            ${riskHtml}
        </div>
    `;
}

// --- 指数成交量图切换 ---
export function initVolumeChartTabs() {
    if (!isModuleVisible('index_turnover')) return;  // 角色/账户模块权限控制
    const tabsContainer = document.getElementById('volumeChartTabs');
    if (!tabsContainer) return;

    tabsContainer.addEventListener('click', async (e) => {
        const btn = e.target.closest('.index-vol-tab');
        if (!btn || btn.classList.contains('active')) return;

        // 更新按钮状态
        tabsContainer.querySelectorAll('.index-vol-tab').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        const indexCode = btn.dataset.index;
        selectedVolumeIndex = indexCode;

        // 显示加载态
        const loadingEl = document.getElementById('volumeChartLoading');
        if (loadingEl) loadingEl.classList.remove('hidden');

        try {
            const klineData = await fetchShKlineData(indexCode);
            marketDataState.kline = klineData;
            renderVolumeChart();
        } catch (err) {
            console.warn(`成交量图切换失败 [${indexCode}]:`, err.message);
            showToast(`⚠️ ${btn.textContent} 数据获取失败`, 'warning');
        } finally {
            if (loadingEl) loadingEl.classList.add('hidden');
        }
    });
}
