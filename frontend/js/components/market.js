// ============================================================
// 大盘组件：今日盯大盘 / 刷新 / 板块热力 / 领涨方向 / 驱动因素
// ============================================================
import { marketDataState, fetchRealtimeMarketData, fetchShKlineData } from '../services/marketService.js';
import { mockData } from '../data/mockData.js';
import { showToast } from '../core/ui.js';
import { renderVolumeChart } from './overview.js';

// --- 今日盯大盘 ---
export function renderMarketOverview() {
    const m = marketDataState.realtime || mockData.marketOverview;

    // 1. 三大指数
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

    // 4. 当前时间 + 数据源徽标
    const now = new Date();
    document.getElementById('marketTime').textContent = now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    const badge = document.getElementById('marketDataBadge');
    const badgeText = document.getElementById('marketDataBadgeText');
    const sourceBadge = document.getElementById('marketDataSource');

    if (marketDataState.source === 'real') {
        badge.className = 'flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-positive/10 text-positive';
        badgeText.textContent = '实时';
        sourceBadge.textContent = '东方财富';
        sourceBadge.className = 'hidden sm:inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full bg-positive/10 text-positive border border-positive/20';
    } else {
        badge.className = 'flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-surface-strong text-muted border border-hairline-soft';
        badgeText.textContent = marketDataState.loading ? '加载中' : '离线';
        sourceBadge.textContent = '模拟数据';
        sourceBadge.className = 'hidden sm:inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full bg-surface-strong text-muted border border-hairline-soft';
    }
}

// --- 从实时源刷新大盘数据（东方财富） ---
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

    try {
        const kline = await fetchShKlineData();
        marketDataState.kline = kline;

        const realtime = await fetchRealtimeMarketData();
        marketDataState.realtime = realtime;
        marketDataState.source = 'real';
        marketDataState.lastUpdated = new Date();
        marketDataState.error = null;

        renderMarketOverview();
        renderVolumeChart();
        showToast('✅ 大盘数据已更新（东方财富实时源）', 'success');
    } catch (err) {
        console.error('Market data fetch failed:', err);
        marketDataState.error = err.message;
        marketDataState.source = 'mock';
        showToast('⚠️ 实时数据获取失败，已切换为模拟数据', 'warning');
    } finally {
        marketDataState.loading = false;
        renderMarketOverview();
        refreshBtn.innerHTML = originalBtnHtml;
        refreshBtn.disabled = false;
    }
}

// --- 板块表现热力图 ---
export function renderSectorHeatmap() {
    const sectors = mockData.sectorPerformance;
    const container = document.getElementById('sectorHeatmap');

    const maxAbs = Math.max(...sectors.map(s => Math.abs(s.pct)));

    container.innerHTML = sectors.map(s => {
        const isUp = s.pct >= 0;
        const intensity = Math.abs(s.pct) / maxAbs;
        const bgOpacity = 0.08 + intensity * 0.25;
        const bgColor = isUp
            ? `rgba(207, 32, 47, ${bgOpacity})`
            : `rgba(5, 177, 105, ${bgOpacity})`;
        const textColor = isUp ? 'text-up' : 'text-down';
        const barWidth = (Math.abs(s.pct) / maxAbs * 100).toFixed(0);
        const barColor = isUp ? '#cf202f' : '#05b169';

        return `
            <div class="flex items-center gap-2 group cursor-pointer" title="${s.leader}: ${s.leaderPct >= 0 ? '+' : ''}${s.leaderPct}%">
                <span class="text-xs font-medium text-ink w-16 flex-shrink-0 truncate">${s.name}</span>
                <div class="flex-1 h-6 rounded-md overflow-hidden bg-surface-strong relative">
                    <div class="absolute inset-y-0 ${isUp ? 'left-0' : 'right-0'} rounded-md transition-all duration-500" style="width: ${barWidth}%; background-color: ${barColor}; opacity: ${0.3 + intensity * 0.5};"></div>
                    <div class="absolute inset-0 flex items-center ${isUp ? 'justify-end pr-2' : 'justify-start pl-2'}">
                        <span class="font-mono text-xs font-semibold ${textColor}">${s.pct >= 0 ? '+' : ''}${s.pct.toFixed(2)}%</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

// --- 领涨方向分析（高低切） ---
export function renderLeadershipAnalysis() {
    const items = mockData.leadershipAnalysis;
    const container = document.getElementById('leadershipAnalysis');

    const tagColors = {
        up: 'bg-red-100 text-up',
        warning: 'bg-yellow-100 text-warning',
        primary: 'bg-blue-100 text-primary',
    };

    container.innerHTML = items.map(item => `
        <div class="bg-surface-soft rounded-xl p-4">
            <div class="flex items-center gap-2 mb-2">
                <span class="px-2 py-0.5 text-xs font-semibold rounded-md ${tagColors[item.tagColor] || tagColors.primary}">${item.tag}</span>
                <span class="text-sm font-semibold text-ink">${item.title}</span>
            </div>
            <p class="text-xs text-muted leading-relaxed mb-2">${item.desc}</p>
            ${item.stocks && item.stocks.length > 0 ? `
                <div class="flex items-center gap-1.5 flex-wrap">
                    ${item.stocks.map(s => `<span class="px-1.5 py-0.5 text-xs rounded bg-canvas border border-hairline text-body font-mono">${s}</span>`).join('')}
                </div>
            ` : ''}
        </div>
    `).join('');
}

// --- 核心驱动因素 ---
export function renderDriverAnalysis() {
    const items = mockData.driverAnalysis;
    const container = document.getElementById('driverAnalysis');

    const impactStyles = {
        bullish: { border: 'border-l-positive', bg: 'bg-positive/5', badge: '<span class="px-1.5 py-0.5 text-xs font-medium rounded bg-positive/10 text-positive">利好</span>' },
        bearish: { border: 'border-l-negative', bg: 'bg-negative/5', badge: '<span class="px-1.5 py-0.5 text-xs font-medium rounded bg-negative/10 text-negative">利空</span>' },
        warning: { border: 'border-l-warning', bg: 'bg-yellow-50/50', badge: '<span class="px-1.5 py-0.5 text-xs font-medium rounded badge-warning">注意</span>' },
    };

    container.innerHTML = items.map(item => {
        const style = impactStyles[item.impact] || impactStyles.warning;
        return `
            <div class="rounded-xl ${style.bg} border-l-2 ${style.border.replace('border-l-', 'border-')} p-4">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-2">
                        <span class="text-lg">${item.icon}</span>
                        <span class="text-sm font-semibold text-ink">${item.factor}</span>
                    </div>
                    ${style.badge}
                </div>
                <p class="text-xs text-muted leading-relaxed">${item.desc}</p>
            </div>
        `;
    }).join('');
}
