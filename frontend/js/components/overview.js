// ============================================================
// 总览组件：统计卡片 / 持仓表格 / 图表 / 行业集中度 / 成交量
// ============================================================
import { getUserPositions, saveUserPositions, getUserData, getCurrentClient } from '../services/clientService.js';
import { formatCurrency, formatNumber, getPnLColor, getSectorBadgeClass, getSectorBarColor } from '../core/formatters.js';
import { marketDataState } from '../services/marketService.js';
import { canEditClient } from '../permissions/access.js';

let currentFilter = 'all';
let sortDirection = {};
let chartInstances = {}; // 存储图表实例用于销毁重建

// --- 行业集中度 ---
export function renderSectorConcentration() {
    const positions = getUserPositions();
    const container = document.getElementById('sectorConcentration');
    const hintEl = document.getElementById('topSectorHint');

    if (positions.length === 0) {
        container.innerHTML = '<span class="text-xs text-muted">暂无持仓数据</span>';
        hintEl.textContent = '';
        return;
    }

    const sectorMap = {};
    let totalMV = 0;
    positions.forEach(p => {
        const mv = p.price * p.quantity;
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
export function renderStatsCards() {
    const positions = getUserPositions();
    let totalMarketValue = 0;
    let totalCost = 0;
    positions.forEach(p => {
        totalMarketValue += p.price * p.quantity;
        totalCost += p.costPrice * p.quantity;
    });
    const totalPnL = totalMarketValue - totalCost;
    const totalPnLPct = totalCost > 0 ? (totalPnL / totalCost) * 100 : 0;
    const availableCash = getUserData().availableCash || 0;
    const totalAssets = totalMarketValue + availableCash;

    const todayPnL = getUserData().todayPnL || totalPnL * 0.05;
    const todayPnLPct = totalAssets > 0 ? (todayPnL / (totalAssets - todayPnL)) * 100 : 0;

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
            value: `${totalPnL >= 0 ? '+' : ''}¥${formatCurrency(totalPnL)}`,
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
export function renderPositionsTable(filter = 'all') {
    currentFilter = filter;
    let positions = [...getUserPositions()];

    positions = positions.map(p => ({
        ...p,
        pnl: (p.price - p.costPrice) * p.quantity,
        pnlPct: ((p.price - p.costPrice) / p.costPrice) * 100,
        marketValue: p.price * p.quantity
    }));

    const totalMV = positions.reduce((sum, p) => sum + p.marketValue, 0);

    if (filter === 'profit') positions = positions.filter(p => p.pnl > 0);
    else if (filter === 'loss') positions = positions.filter(p => p.pnl < 0);

    document.getElementById('positionCount').textContent = getUserPositions().length;

    const emptyState = document.getElementById('emptyState');
    const tableContainer = document.querySelector('#positions .overflow-x-auto');
    if (getUserPositions().length === 0) {
        emptyState.classList.remove('hidden');
        tableContainer.parentElement.classList.add('hidden');
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
    tbody.innerHTML = positions.map(p => `
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
                ${p.pnl >= 0 ? '+' : ''}¥${formatCurrency(p.pnl)}
            </td>
            <td class="px-5 py-4 text-right font-mono text-sm font-semibold ${getPnLColor(p.pnlPct)}">
                ${p.pnlPct >= 0 ? '+' : ''}${p.pnlPct.toFixed(2)}%
            </td>
            <td class="px-5 py-4 text-right text-sm text-muted hidden lg:table-cell">
                ${(p.marketValue / totalMV * 100).toFixed(1)}%
            </td>
            <td class="px-5 py-4 text-center whitespace-nowrap">
                ${editable ? `
                <button onclick="openAdjustModal('${p.code}', 'add')" class="px-2 py-1 text-xs font-medium text-up hover:bg-red-50 rounded-md transition-colors" title="加仓">加仓</button>
                <button onclick="openAdjustModal('${p.code}', 'reduce')" class="px-2 py-1 text-xs font-medium text-down hover:bg-green-50 rounded-md transition-colors" title="减仓">减仓</button>
                <button onclick="openPositionModal('${p.code}')" class="px-2 py-1 text-xs font-medium text-primary hover:bg-blue-50 rounded-md transition-colors" title="编辑">编辑</button>
                <button onclick="deletePosition('${p.code}')" class="px-2 py-1 text-xs font-medium text-muted hover:bg-surface-strong rounded-md transition-colors" title="删除">删除</button>
                ` : '<span class="text-xs text-muted-soft">只读</span>'}
            </td>
        </tr>
    `).join('');

    renderSectorConcentration();
}

export function filterPositions(filter) {
    renderPositionsTable(filter);
}

export async function sortTable(field) {
    sortDirection[field] = !sortDirection[field];
    let positions = [...getUserPositions()].map(p => ({
        ...p,
        pnl: (p.price - p.costPrice) * p.quantity,
        pnlPct: ((p.price - p.costPrice) / p.costPrice) * 100,
        marketValue: p.price * p.quantity
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
    renderPositionsTable(currentFilter);
}

// --- 图表（支持重建） ---
export function renderCharts() {
    Object.values(chartInstances).forEach(chart => {
        if (chart && typeof chart.destroy === 'function') chart.destroy();
    });
    chartInstances = {};

    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.color = '#7c828a';

    const userPositions = getUserPositions();

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
            labels: [],
            datasets: [{
                label: '组合收益',
                data: [],
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
                label: '沪深300',
                data: [],
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

    // 2. 持仓配置环形图
    const allocCtx = document.getElementById('allocationChart').getContext('2d');
    const topPositions = userPositions.slice(0, 6);
    const allocColors = ['#0052ff', '#05b169', '#cf202f', '#f4b000', '#8b5cf6', '#ec4899'];

    if (topPositions.length === 0) {
        chartInstances.alloc = new Chart(allocCtx, {
            type: 'doughnut',
            data: { labels: ['暂无数据'], datasets: [{ data: [1], backgroundColor: ['#eef0f3'], borderWidth: 0 }] },
            options: { responsive: true, maintainAspectRatio: false, cutout: '68%', plugins: { legend: { display: false }, tooltip: { enabled: false } } }
        });
    } else {
        chartInstances.alloc = new Chart(allocCtx, {
            type: 'doughnut',
            data: {
                labels: topPositions.map(p => p.name),
                datasets: [{
                    data: topPositions.map(p => p.price * p.quantity),
                    backgroundColor: allocColors,
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
    const dailyValues = []; // 暂无历史每日盈亏数据
    const dailyBgColors = dailyValues.map(v => v >= 0 ? 'rgba(207, 32, 47, 0.8)' : 'rgba(5, 177, 105, 0.8)');
    const totalPnL = dailyValues.reduce((a, b) => a + b, 0);

    document.getElementById('weeklyPnLTotal').textContent = `${totalPnL >= 0 ? '+' : ''}¥${formatCurrency(totalPnL)}`;
    document.getElementById('weeklyPnLTotal').className = `font-mono font-semibold ${getPnLColor(totalPnL)}`;

    chartInstances.daily = new Chart(dailyCtx, {
        type: 'bar',
        data: {
            labels: [],
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
                        label: ctx => ` 盈亏: ${ctx.raw >= 0 ? '+' : ''}¥${formatCurrency(ctx.raw)}`
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { font: { size: 11 } }
                },
                y: {
                    grid: { color: '#f0f0f0' },
                    ticks: {
                        font: { size: 11 },
                        callback: v => '¥' + (v / 1000).toFixed(0) + 'k'
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
export function refreshAll() {
    renderStatsCards();
    renderPositionsTable(currentFilter);
    renderCharts();
}
