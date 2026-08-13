// ============================================================
// 策略组件：策略统计 + 交易时间线（与客户持仓工作台联动）
// ============================================================
import { getCurrentClient } from '../services/clientService.js';
import { formatCurrency, formatNumber, getBadgeClass, getTypeLabel, getResultBadge } from '../core/formatters.js';

const STRATEGIES = ['均线突破策略', '趋势跟随策略', '价值回归策略', '事件驱动策略', '动量轮动策略'];
const NOTES = {
    buyWin: '放量突破关键均线，趋势确认，择机建仓',
    buyLose: '估值偏离合理区间，暂持观察，等待企稳',
    sellWin: '反弹至压力位，获利了结',
    sellLose: '跌破止损位，执行纪律性止损',
};

function hashCode(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
    return Math.abs(h);
}

function seededRandom(seed) {
    let s = seed || 1;
    return () => {
        s = (s * 9301 + 49297) % 233280;
        return s / 233280;
    };
}

// 由当前客户持仓推导策略统计与交易时间线（确定性，按客户 id 播种）
function buildClientStrategy(client) {
    const positions = (client && Array.isArray(client.positions)) ? client.positions : [];
    if (!positions.length) {
        return { stats: { totalTrades: 0, winRate: 0, avgProfit: 0, maxDrawdown: 0 }, trades: [] };
    }

    const rand = seededRandom(hashCode(client.id || 'default'));
    const trades = positions.map((p, i) => {
        const pnl = (p.price - p.costPrice) * p.quantity;
        const pnlPct = (p.price - p.costPrice) / p.costPrice * 100;
        const isBuy = i % 2 === 0;
        const result = pnlPct > 3 ? 'success' : (pnlPct < -3 ? 'failed' : 'partial');

        const day = 11 - Math.floor(i / 2);
        const hour = 9 + Math.floor(rand() * 5);
        const minute = Math.floor(rand() * 60);
        const second = Math.floor(rand() * 60);

        const note = isBuy
            ? (pnlPct > 3 ? NOTES.buyWin : (pnlPct < -3 ? NOTES.buyLose : '分批建仓，控制单票仓位'))
            : (pnlPct > 3 ? NOTES.sellWin : NOTES.sellLose);

        return {
            id: i + 1,
            time: `2025-08-${String(Math.max(1, day)).padStart(2, '0')} ${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}`,
            strategy: STRATEGIES[i % STRATEGIES.length],
            type: isBuy ? 'buy' : 'sell',
            stock: p.name,
            code: p.code,
            price: isBuy ? p.costPrice : p.price,
            quantity: p.quantity,
            result,
            note,
        };
    });

    const successCount = trades.filter(t => t.result === 'success').length;
    const winRate = trades.length ? successCount / trades.length * 100 : 0;
    const profits = positions.map(p => (p.price - p.costPrice) * p.quantity);
    const avgProfit = profits.length ? profits.reduce((a, b) => a + b, 0) / profits.length : 0;
    const maxDrawdown = Math.min(0, ...positions.map(p => (p.price - p.costPrice) / p.costPrice * 100));

    return {
        stats: {
            totalTrades: trades.length,
            winRate: Number(winRate.toFixed(1)),
            avgProfit,
            maxDrawdown: Number(maxDrawdown.toFixed(2)),
        },
        trades,
    };
}

export function renderStrategySection() {
    const client = getCurrentClient();
    const { stats, trades } = buildClientStrategy(client);

    const avgStr = (stats.avgProfit >= 0 ? '+' : '-') + '¥' + formatCurrency(Math.abs(stats.avgProfit));

    const statsContainer = document.getElementById('strategyStats');
    statsContainer.innerHTML = [
        { label: '总交易次数', value: stats.totalTrades, sub: '次', icon: '📊', color: 'bg-blue-50' },
        { label: '胜率', value: stats.winRate, sub: '%', icon: '🎯', color: 'bg-green-50', valueColor: 'text-positive' },
        { label: '平均盈利', value: avgStr, sub: '', icon: '💰', color: 'bg-purple-50' },
        { label: '最大回撤', value: `${stats.maxDrawdown}%`, sub: '', icon: '⚠️', color: 'bg-red-50', valueColor: 'text-negative' },
    ].map(s => `
        <div class="${s.color} rounded-2xl p-5">
            <div class="text-xs text-muted mb-1">${s.label}</div>
            <div class="font-mono text-xl font-semibold ${s.valueColor || 'text-ink'}">${s.value}<span class="text-sm font-normal text-muted ml-0.5">${s.sub}</span></div>
        </div>
    `).join('');

    const timelineContainer = document.getElementById('strategyTimeline');
    if (!trades.length) {
        timelineContainer.innerHTML = '<div class="p-8 text-center text-sm text-muted">该客户暂无交易记录</div>';
        return;
    }

    timelineContainer.innerHTML = trades.map(trade => `
        <div class="timeline-item relative pl-14 pr-5 py-5 hover:bg-surface-soft/30 transition-colors">
            <div class="absolute left-4 top-6 w-3 h-3 rounded-full ${trade.type === 'buy' ? 'bg-up' : 'bg-down'} ring-4 ${trade.type === 'buy' ? 'ring-red-50' : 'ring-green-50'}"></div>

            <div class="flex flex-col lg:flex-row lg:items-start gap-3">
                <div class="lg:w-64 flex-shrink-0">
                    <div class="text-xs text-muted font-mono">${trade.time.split(' ')[0]}</div>
                    <div class="text-xs text-muted/70 font-mono">${trade.time.split(' ')[1]}</div>
                    <div class="mt-2 inline-block px-2 py-0.5 text-xs font-medium rounded-md bg-surface-strong text-body">${trade.strategy}</div>
                </div>

                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="px-2 py-0.5 text-xs font-semibold rounded-full ${getBadgeClass(trade.type)}">${getTypeLabel(trade.type)}</span>
                        <span class="font-medium text-ink">${trade.stock}</span>
                        <span class="text-sm text-muted font-mono">${trade.code}</span>
                        ${getResultBadge(trade.result)}
                    </div>
                    <div class="mt-2 flex items-center gap-4 text-sm">
                        <span class="text-muted">价格: <span class="font-mono text-ink">¥${formatNumber(trade.price)}</span></span>
                        <span class="text-muted">数量: <span class="font-mono text-ink">${trade.quantity.toLocaleString()}股</span></span>
                        <span class="text-muted">金额: <span class="font-mono text-ink">¥${formatCurrency(trade.price * trade.quantity)}</span></span>
                    </div>
                    <p class="mt-2 text-sm text-muted leading-relaxed">${trade.note}</p>
                </div>
            </div>
        </div>
    `).join('');
}
