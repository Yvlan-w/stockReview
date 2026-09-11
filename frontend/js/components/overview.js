// ============================================================
// 总览组件：统计卡片 / 持仓表格 / 图表 / 行业集中度 / 成交量
// ============================================================
import { getUserPositions, saveUserPositions, getUserData, getCurrentClient, computePortfolioStats, assignPortfolio } from '../services/clientService.js';
import { formatCurrency, formatNumber, getPnLColor, getSectorBadgeClass, getSectorBarColor } from '../core/formatters.js';
import { marketDataState } from '../services/marketService.js';
import { canEditClient } from '../permissions/access.js';
import { isModuleVisible } from '../permissions/modules.js';
import { getPrice, getPriceMap, updatePriceCache } from '../services/priceService.js';
import { renderStrategySection } from './strategy.js';

let currentFilter = 'all';
let sortDirection = {};
let chartInstances = {}; // 存储图表实例用于销毁重建
let currentPortfolio = null; // 存储当前portfolio数据供筛选/排序使用

// --- 行业集中度 ---
export function renderSectorConcentration(portfolio = null) {
    const positions = getUserPositions();
    const container = document.getElementById('sectorConcentration');
    const hintEl = document.getElementById('topSectorHint');

    if (positions.length === 0) {
        container.innerHTML = '<span class="text-xs text-muted">暂无持仓数据</span>';
        hintEl.textContent = '';
        return;
    }

    // 构建实时价格映射
    const priceMap = {};
    if (portfolio && portfolio.positions) {
        portfolio.positions.forEach(p => {
            priceMap[p.code] = p;
        });
    }

    const sectorMap = {};
    let totalMV = 0;
    positions.forEach(p => {
        const realtime = priceMap[p.code];
        const currentPrice = realtime?.currentPrice || getPrice(p.code, p.costPrice);
        const mv = currentPrice * p.quantity;
        sectorMap[p.sector] = (sectorMap[p.sector] || 0) + mv;
        totalMV += mv;
    });

    const sorted = Object.entries(sectorMap).sort((a, b) => b[1] - a[1]);

    if (sorted.length > 0) {
        const [topSector, topVal] = sorted[0];
        const topPct = (topVal / totalMV * 100).toFixed(1);
        hintEl.innerHTML = `最大持仓行业: <span class="font-semibold text-ink">${topSector}</span> · 占比 <span class="font-mono font-semibold text-primary">${topPct}%</span>`;
    }

    container.innerHTML = sorted.map(([sector, value]) => {
        const pct = (value / totalMV * 100);
        const barColor = getSectorBarColor(sector);
        return `
            <div class="flex flex-col gap-1 min-w-[80px]">
                <div class="flex items-center justify-between text-xs">
                    <span class="font-medium text-ink">${sector}</span>
                    <span class="font-mono text-muted">${pct.toFixed(1)}%</span>
                </div>
                <div class="h-2 bg-surface-strong rounded-full overflow-hidden">
                    <div class="h-full rounded-full transition-all duration-500" style="width: ${pct}%; background-color: ${barColor};"></div>
                </div>
            </div>
        `;
    }).join('');
}

// --- 统计卡片 ---
// 与「客户名片」renderClientProfile 共用 computePortfolioStats，确保总资产/持仓盈亏数值完全一致
export function renderStatsCards(portfolio) {
    const s = computePortfolioStats(portfolio);
    const totalMarketValue = s.totalMarketValue;
    const totalCost = s.totalCost;
    // 持仓盈亏：仅浮动盈亏（已实现盈亏已移至「策略复盘」展示，此处剔除）
    const totalPnL = s.floatingPnl;
    const totalPnLPct = s.floatingPnlPct;
    const availableCash = s.availableCash;
    const totalAssets = s.totalAssets;
    const todayPnL = s.todayPnl;
    const todayPnLPct = s.todayPnlPct;

    const cards = [
        {
            icon: `<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
            label: '总资产',
            value: `¥${formatCurrency(totalAssets)}`,
            subText: '',
            accent: 'blue',
            iconBg: 'bg-blue-50',
            iconColor: 'text-primary'
        },
        {
            icon: `<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/></svg>`,
            label: '持仓盈亏',
            value: `${totalPnL >= 0 ? '+' : '-'}¥${formatCurrency(totalPnL)}`,
            subText: `${totalPnLPct >= 0 ? '+' : ''}${totalPnLPct.toFixed(2)}%`,
            accent: totalPnL >= 0 ? 'up' : 'down',
            iconBg: totalPnL >= 0 ? 'bg-red-50' : 'bg-green-50',
            iconColor: totalPnL >= 0 ? 'text-up' : 'text-down',
            valueColor: totalPnL >= 0 ? 'text-up' : 'text-down'
        },
        {
            icon: `<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>`,
            label: '持仓市值',
            value: `¥${formatCurrency(totalMarketValue)}`,
            subText: `成本 ¥${formatCurrency(totalCost)}`,
            accent: 'blue',
            iconBg: 'bg-blue-50',
            iconColor: 'text-primary',
        },
        {
            icon: `<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z"/></svg>`,
            label: '可用资金',
            value: `¥${formatCurrency(availableCash)}`,
            subText: '',
            accent: 'purple',
            iconBg: 'bg-purple-50',
            iconColor: 'text-purple-600'
        }
    ];

    const container = document.getElementById('statsCards');
    container.innerHTML = cards.map((card, i) => `
        <div class="stat-card p-5 md:p-6 animate-fade-in-up" data-accent="${card.accent}" style="animation-delay: ${i * 80}ms">
            <div class="flex items-start justify-between mb-4">
                <div class="${card.iconBg} w-11 h-11 rounded-xl flex items-center justify-center ${card.iconColor}">
                    ${card.icon}
                </div>
            </div>
            <div class="text-sm text-muted mb-1">${card.label}</div>
            <div class="font-mono text-xl md:text-2xl font-semibold ${card.valueColor || 'text-ink'}">${card.value}</div>
            ${card.subText ? `<div class="text-xs ${card.valueColor || 'text-muted'} mt-1 font-medium">${card.subText}</div>` : ''}
        </div>
    `).join('');
}

// --- 持仓表格 ---
export function renderPositionsTable(filter = 'all', portfolio) {
    currentFilter = filter;
    let positions = [...getUserPositions()];

    // 如果有实时数据，优先使用实时价格
    const priceMap = {};
    if (portfolio && portfolio.positions) {
        portfolio.positions.forEach(p => {
            priceMap[p.code] = p;
        });
    }

    positions = positions.map(p => {
        const realtime = priceMap[p.code];
        const currentPrice = realtime?.currentPrice || getPrice(p.code, p.costPrice);
        // 安全计算盈亏：处理 null/undefined
        const safePnl = realtime?.pnl !== null && realtime?.pnl !== undefined
            ? realtime.pnl
            : (currentPrice - p.costPrice) * p.quantity;
        const safePnlPct = realtime?.pnlPct !== null && realtime?.pnlPct !== undefined
            ? realtime.pnlPct
            : p.costPrice > 0 ? ((currentPrice - p.costPrice) / p.costPrice) * 100 : 0;
        const marketValue = currentPrice * p.quantity;
        const todayPnl = realtime?.todayPnl !== null && realtime?.todayPnl !== undefined
            ? realtime.todayPnl
            : 0;
        return {
            ...p,
            price: currentPrice,
            pnl: safePnl,
            pnlPct: safePnlPct,
            marketValue,
            todayPnl
        };
    });

    // 先计算总市值（用于占比计算）
    const totalMV = positions.reduce((sum, p) => sum + p.marketValue, 0);

    // 应用筛选（安全处理 null/undefined）
    let filteredPositions = positions;
    if (filter === 'profit') filteredPositions = positions.filter(p => (p.pnl ?? 0) > 0);
    else if (filter === 'loss') filteredPositions = positions.filter(p => (p.pnl ?? 0) < 0);

    // 更新持仓数量显示为筛选后的数量
    document.getElementById('positionCount').textContent = filteredPositions.length;

    const emptyState = document.getElementById('emptyState');
    const tableContainer = document.querySelector('#positions .overflow-x-auto');
    if (filteredPositions.length === 0) {
        emptyState.classList.remove('hidden');
        tableContainer.parentElement.classList.add('hidden');
        // 更新空状态文案
        const emptyTitle = emptyState.querySelector('h3');
        const emptyDesc = emptyState.querySelector('p');
        if (filter === 'profit') {
            emptyTitle.textContent = '暂无盈利持仓';
            emptyDesc.textContent = '当前没有盈利的股票';
        } else if (filter === 'loss') {
            emptyTitle.textContent = '暂无亏损持仓';
            emptyDesc.textContent = '当前没有亏损的股票';
        } else {
            emptyTitle.textContent = '暂无持仓数据';
            emptyDesc.textContent = '点击「添加持仓」录入您的第一只股票';
        }
    } else {
        emptyState.classList.add('hidden');
        tableContainer.parentElement.classList.remove('hidden');
    }

    document.querySelectorAll('.filter-btn').forEach(btn => {
        if (btn.dataset.filter === filter) {
            btn.classList.add('bg-canvas', 'text-ink', 'shadow-sm');
            btn.classList.remove('text-muted');
        } else {
            btn.classList.remove('bg-canvas', 'text-ink', 'shadow-sm');
            btn.classList.add('text-muted');
        }
    });

    const tbody = document.getElementById('positionsBody');
    const editable = canEditClient(getCurrentClient());
    tbody.innerHTML = filteredPositions.map(p => `
        <tr class="table-row-hover border-b border-hairline/60 last:border-0 transition-colors">
            <td class="px-5 py-4">
                <div class="flex items-center gap-3">
                    <div class="w-9 h-9 rounded-full bg-surface-strong flex items-center justify-center text-xs font-semibold text-body">
                        ${p.name.charAt(0)}
                    </div>
                    <div>
                        <div class="font-medium text-ink text-sm">${p.name}</div>
                        <div class="text-xs text-muted md:hidden">${p.sector}</div>
                    </div>
                </div>
            </td>
            <td class="px-5 py-4 font-mono text-sm text-body">${p.code}</td>
            <td class="px-5 py-4 hidden md:table-cell whitespace-nowrap">
                <span class="px-2 py-0.5 text-xs font-medium rounded-md whitespace-nowrap ${getSectorBadgeClass(p.sector)}">${p.sector}</span>
            </td>
            <td class="px-5 py-4 text-right font-mono text-sm text-ink">${p.quantity.toLocaleString()}</td>
            <td class="px-5 py-4 text-right font-mono text-sm text-body">¥${formatNumber(p.costPrice)}</td>
            <td class="px-5 py-4 text-right font-mono text-sm font-medium text-ink">¥${formatNumber(p.price)}</td>
            <td class="px-5 py-4 text-right font-mono text-sm font-semibold ${getPnLColor(p.pnl)}">
                ${p.pnl >= 0 ? '+' : '-'}¥${formatCurrency(Math.abs(p.pnl))}
            </td>
            <td class="px-5 py-4 text-right font-mono text-sm font-semibold ${getPnLColor(p.pnlPct)}">
                ${p.pnlPct >= 0 ? '+' : ''}${(p.pnlPct ?? 0).toFixed(2)}%
            </td>
            <td class="px-5 py-4 text-right font-mono text-sm ${getPnLColor(p.todayPnl)}">
                ${p.todayPnl >= 0 ? '+' : '-'}¥${formatCurrency(Math.abs(p.todayPnl))}
            </td>
            <td class="px-5 py-4 text-right text-sm text-muted hidden lg:table-cell">
                ${totalMV > 0 ? (p.marketValue / totalMV * 100).toFixed(1) : '0.0'}%
            </td>
            <td class="px-5 py-4 text-center whitespace-nowrap">
                ${editable ? `
                <button onclick="openAdjustModal('${p.code}', 'add')" class="px-2 py-1 text-xs font-medium text-up hover:bg-red-50 rounded-md transition-colors" title="加仓">加仓</button>
                <button onclick="openAdjustModal('${p.code}', 'reduce')" class="px-2 py-1 text-xs font-medium text-down hover:bg-green-50 rounded-md transition-colors" title="减仓">减仓</button>
                <button onclick="openPositionModal('${p.code}')" class="px-2 py-1 text-xs font-medium text-primary hover:bg-blue-50 rounded-md transition-colors" title="编辑">编辑</button>
                <button onclick="revokePosition('${p.code}')" class="px-2 py-1 text-xs font-medium text-muted hover:bg-surface-strong rounded-md transition-colors" title="撤销">撤销</button>
                ` : '<span class="text-xs text-muted-soft">只读</span>'}
            </td>
        </tr>
    `).join('');

    renderSectorConcentration(portfolio);
}

// --- 图表（支持重建） ---
export function renderCharts(pnlHistory, portfolio = null) {
    Object.values(chartInstances).forEach(chart => {
        if (chart && typeof chart.destroy === 'function') chart.destroy();
    });
    chartInstances = {};

    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.color = '#7c828a';

    const userPositions = getUserPositions();

    // 构建实时价格映射（优先使用后端实时数据）
    const priceMap = {};
    if (portfolio && portfolio.positions) {
        portfolio.positions.forEach(p => {
            priceMap[p.code] = p;
        });
    }

    // 计算持仓市值时优先使用实时价格
    const positionsWithRealtime = userPositions.map(p => {
        const realtime = priceMap[p.code];
        const currentPrice = realtime?.currentPrice || getPrice(p.code, p.costPrice);
        const marketValue = currentPrice * p.quantity;
        return { ...p, currentPrice, marketValue };
    });

    // 解析盈亏历史数据
    let historyLabels = [];
    let portfolioData = [];
    let benchmarkData = [];
    let dailyPnlData = [];

    if (pnlHistory && pnlHistory.dates && pnlHistory.dates.length > 0) {
        historyLabels = pnlHistory.dates;
        portfolioData = pnlHistory.cumulativeReturnPct || [];
        benchmarkData = pnlHistory.benchmarkReturn || [];
        dailyPnlData = pnlHistory.dailyPnl || [];
    }

    // x 轴显示标签：YYYY-MM-DD → MM-DD（节省空间，保证更多交易日刻度可见）
    const displayLabels = historyLabels.map(d => {
        const parts = String(d).split('-');
        return parts.length === 3 ? `${parts[1]}-${parts[2]}` : d;
    });

    // 1. 收益曲线
    const returnCtx = document.getElementById('returnChart').getContext('2d');

    const portfolioGradient = returnCtx.createLinearGradient(0, 0, 0, 280);
    portfolioGradient.addColorStop(0, 'rgba(0, 82, 255, 0.25)');
    portfolioGradient.addColorStop(1, 'rgba(0, 82, 255, 0.02)');

    const benchmarkGradient = returnCtx.createLinearGradient(0, 0, 0, 280);
    benchmarkGradient.addColorStop(0, 'rgba(244, 176, 0, 0.15)');
    benchmarkGradient.addColorStop(1, 'rgba(244, 176, 0, 0.01)');

    chartInstances.return = new Chart(returnCtx, {
        type: 'line',
        data: {
            labels: displayLabels,
            datasets: [{
                label: '组合收益',
                data: portfolioData,
                borderColor: '#0052ff',
                backgroundColor: portfolioGradient,
                borderWidth: 3.5,
                fill: true,
                tension: 0.35,
                pointRadius: 0,
                pointHoverRadius: 7,
                pointHoverBackgroundColor: '#0052ff',
                pointHoverBorderColor: '#fff',
                pointHoverBorderWidth: 3,
                pointBackgroundColor: '#0052ff',
                pointBorderColor: '#fff',
                pointBorderWidth: 2,
            }, {
                label: '上证指数',
                data: benchmarkData,
                borderColor: '#f4b000',
                backgroundColor: benchmarkGradient,
                borderWidth: 2.5,
                borderDash: [6, 4],
                fill: true,
                tension: 0.35,
                pointRadius: 0,
                pointHoverRadius: 6,
                pointHoverBackgroundColor: '#f4b000',
                pointHoverBorderColor: '#fff',
                pointHoverBorderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    align: 'end',
                    labels: {
                        usePointStyle: true,
                        pointStyleWidth: 10,
                        padding: 16,
                        font: { size: 12, weight: '500' },
                        color: '#5b616e'
                    }
                },
                tooltip: {
                    backgroundColor: '#0a0b0d',
                    titleColor: '#fff',
                    bodyColor: '#a8acb3',
                    padding: 14,
                    cornerRadius: 10,
                    displayColors: true,
                    boxPadding: 6,
                    callbacks: {
                        title: ctx => `日期: ${ctx[0].label}`,
                        label: ctx => `  ${ctx.dataset.label}: ${ctx.parsed.y >= 0 ? '+' : ''}${ctx.parsed.y.toFixed(2)}%`
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { font: { size: 11 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }
                },
                y: {
                    grid: { color: '#f0f0f0', drawBorder: false },
                    ticks: {
                        font: { size: 11, weight: '500' },
                        callback: v => v + '%'
                    }
                }
            }
        }
    });

    // 2. 持仓配置环形图（显示所有持仓，使用实时价格）
    const allocCtx = document.getElementById('allocationChart').getContext('2d');
    const sortedPositions = [...positionsWithRealtime].sort((a, b) => b.marketValue - a.marketValue);
    const allocColors = ['#0052ff', '#05b169', '#cf202f', '#f4b000', '#8b5cf6', '#ec4899', '#00b42a', '#ff7d00', '#86909c', '#722ed1', '#06b6d4', '#d946ef', '#f97316', '#84cc16', '#6366f1'];

    if (sortedPositions.length === 0) {
        chartInstances.alloc = new Chart(allocCtx, {
            type: 'doughnut',
            data: { labels: ['暂无数据'], datasets: [{ data: [1], backgroundColor: ['#eef0f3'], borderWidth: 0 }] },
            options: { responsive: true, maintainAspectRatio: false, cutout: '68%', plugins: { legend: { display: false }, tooltip: { enabled: false } } }
        });
    } else {
        chartInstances.alloc = new Chart(allocCtx, {
            type: 'doughnut',
            data: {
                labels: sortedPositions.map(p => p.name),
                datasets: [{
                    data: sortedPositions.map(p => p.marketValue),
                    backgroundColor: allocColors.slice(0, sortedPositions.length),
                    borderWidth: 0,
                    hoverOffset: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '68%',
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 12,
                            usePointStyle: true,
                            pointStyleWidth: 8,
                            font: { size: 11 }
                        }
                    },
                    tooltip: {
                        backgroundColor: '#0a0b0d',
                        cornerRadius: 8,
                        callbacks: {
                            label: ctx => ` ${ctx.label}: ¥${formatCurrency(ctx.raw)}`
                        }
                    }
                }
            },
            plugins: [{
                id: 'centerText',
                afterDraw(chart) {
                    const { ctx, chartArea } = chart;
                    const total = chart.data.datasets[0].data.reduce((a, b) => a + b, 0);
                    ctx.save();
                    ctx.font = "600 18px 'JetBrains Mono'";
                    ctx.fillStyle = '#0a0b0d';
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.fillText(`¥${(total / 10000).toFixed(0)}万`, chartArea.left + chartArea.width / 2, chartArea.top + chartArea.height / 2 - 8);
                    ctx.font = "400 11px 'Inter'";
                    ctx.fillStyle = '#7c828a';
                    ctx.fillText('持仓市值', chartArea.left + chartArea.width / 2, chartArea.top + chartArea.height / 2 + 14);
                    ctx.restore();
                }
            }]
        });
    }

    // 3. 每日盈亏柱状图
    const dailyCtx = document.getElementById('dailyPnlChart').getContext('2d');
    const dailyValues = dailyPnlData.length > 0 ? dailyPnlData : [];
    const dailyBgColors = dailyValues.map(v => v >= 0 ? 'rgba(207, 32, 47, 0.8)' : 'rgba(5, 177, 105, 0.8)');
    const totalPnL = dailyValues.reduce((a, b) => a + b, 0);

    document.getElementById('weeklyPnLTotal').textContent = `${totalPnL >= 0 ? '+' : ''}${totalPnL.toFixed(2)}万`;
    document.getElementById('weeklyPnLTotal').className = `font-mono font-semibold ${getPnLColor(totalPnL)}`;

    chartInstances.daily = new Chart(dailyCtx, {
        type: 'bar',
        data: {
            labels: displayLabels,
            datasets: [{
                label: '每日盈亏',
                data: dailyValues,
                backgroundColor: dailyBgColors,
                borderRadius: 6,
                barThickness: 40,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#0a0b0d',
                    cornerRadius: 8,
                    callbacks: {
                        label: ctx => ` 盈亏: ${ctx.raw >= 0 ? '+' : ''}${ctx.raw.toFixed(2)}万`
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { font: { size: 11 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 15 }
                },
                y: {
                    grid: { color: '#f0f0f0' },
                    ticks: {
                        font: { size: 11 },
                        callback: v => v.toFixed(2) + '万'
                    }
                }
            }
        }
    });

    // 4. 上证成交量图（独立销毁重建）
    renderVolumeChart();
}

// --- 上证成交量图 ---
export function renderVolumeChart() {
    if (!isModuleVisible('index_turnover')) return;  // 角色/账户模块权限控制
    const volCtx = document.getElementById('volumeChart').getContext('2d');

    const volData = marketDataState.kline
        ? marketDataState.kline.map(d => ({
            date: d.date,
            volume: d.turnover / 1e8,
            changePct: d.changePct
        }))
        : [];

    const volColors = volData.map((d, i) => {
        if (d.changePct > 0) return 'rgba(207, 32, 47, 0.75)';
        if (d.changePct < 0) return 'rgba(5, 177, 105, 0.75)';
        return 'rgba(0, 82, 255, 0.6)';
    });

    if (chartInstances.volume) chartInstances.volume.destroy();

    chartInstances.volume = new Chart(volCtx, {
        type: 'bar',
        data: {
            labels: volData.map(d => d.date),
            datasets: [{
                label: '成交额(亿元)',
                data: volData.map(d => Math.round(d.volume)),
                backgroundColor: volColors,
                borderRadius: 3,
                barThickness: 'flex',
                maxBarThickness: 18,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#0a0b0d',
                    cornerRadius: 8,
                    padding: 10,
                    callbacks: {
                        title: ctx => `日期: ${ctx[0].label}`,
                        label: ctx => ` 成交额: ${ctx.raw.toLocaleString()} 亿元`
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 }
                },
                y: {
                    grid: { color: '#f0f0f0' },
                    ticks: {
                        font: { size: 10 },
                        callback: v => v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v
                    }
                }
            }
        }
    });
}

// --- 数据变更后刷新总览组件 ---
export async function refreshAll(portfolio, pnlHistory) {
    currentPortfolio = portfolio;
    renderStatsCards(portfolio);
    renderPositionsTable(currentFilter, portfolio);
    renderCharts(pnlHistory, portfolio);
    // 持仓变化后同步刷新策略复盘（依赖最新交易流水）
    try {
        await renderStrategySection();
    } catch (e) {
        console.warn('策略复盘刷新失败:', e);
    }
}

// --- 实时刷新（不重绘图表）：调仓/新建持仓成功后即时反映最新持仓与估值 ---
// 直接使用接口返回的事务后组合数据渲染，配合 syncClientState 实现秒级刷新
export function refreshRealtime(portfolio) {
    if (!portfolio) return;
    // 同步模块级内存态，使「客户名片」(renderClientProfile 读 portfolioData) 与「持仓概览」同源一致
    assignPortfolio(portfolio, getCurrentClient()?.id);
    currentPortfolio = portfolio;
    updatePriceCache(portfolio);
    renderStatsCards(portfolio);
    renderPositionsTable(currentFilter, portfolio);
}

// --- 筛选（使用存储的portfolio）---
export function filterPositions(filter) {
    renderPositionsTable(filter, currentPortfolio);
}

// --- 排序（使用存储的portfolio）---
export async function sortTable(field) {
    sortDirection[field] = !sortDirection[field];
    const prices = getPriceMap(getUserPositions());
    let positions = [...getUserPositions()].map(p => ({
        ...p,
        price: prices[p.code],
        pnl: (prices[p.code] - p.costPrice) * p.quantity,
        pnlPct: p.costPrice > 0 ? ((prices[p.code] - p.costPrice) / p.costPrice) * 100 : 0,
        marketValue: prices[p.code] * p.quantity
    }));

    positions.sort((a, b) => {
        let valA, valB;
        switch (field) {
            case 'name': valA = a.name; valB = b.name; break;
            case 'quantity': valA = a.quantity; valB = b.quantity; break;
            case 'price': valA = a.price; valB = b.price; break;
            case 'pnl': valA = a.pnl; valB = b.pnl; break;
            default: return 0;
        }
        if (typeof valA === 'string') return sortDirection[field] ? valA.localeCompare(valB, 'zh') : valB.localeCompare(valA, 'zh');
        return sortDirection[field] ? valA - valB : valB - valA;
    });

    try {
        await saveUserPositions(positions.map(({ pnl, pnlPct, marketValue, ...rest }) => rest));
    } catch {
        // 排序持久化失败不阻断展示
    }
    renderPositionsTable(currentFilter, currentPortfolio);
}
