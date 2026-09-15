// ============================================================
// 策略复盘组件：真实交易流水驱动（不再基于持仓伪造数据）
// 数据源：
//   fetchTransactions()        → 逐笔流水（买入/卖出每行独立）
//   fetchTransactionsSummary() → 按股票分组汇总（买卖量额/已实现盈亏/三费合计）
//   fetchCostBasis()           → 当前持仓未实现盈亏（用于最大回撤卡片）
// ============================================================
import { getCurrentClient, fetchTransactions, fetchTransactionsSummary, fetchCostBasis } from '../services/clientService.js';
import { formatCurrency, formatNumber, getBadgeClass, getTypeLabel, getPnLColor } from '../core/formatters.js';
import { showToast } from '../core/ui.js';

// 市场代码 → 中文徽标
const MARKET_LABEL = {
    SH: '沪', SZ: '深', BJ: '北', HK: '港', US: '美',
};

// 交易来源 → 中文标签（溯源：区分手工 / 交易截图导入 / 持仓截图导入）
const SOURCE_LABEL = {
    ocr_import: '截图导入',
    holding_import: '持仓截图',
    manual: '手工',
    adjust: '调仓执行',
};

// 格式化数据库时间戳 executed_at → YYYY-MM-DD HH:mm:ss（统一东八区 UTC+8）
// 后端以零时区（UTC，无时区标记的 naive 字符串，如 2026-09-04T06:23:08）存储并返回；
// 必须先将其显式按 UTC 解释（补 Z），否则 new Date() 会把无时区字符串当作「本地时间」解析，
// 在 UTC+8 浏览器中 +8h 偏移被抵消、最终显示回零时区（UTC）墙钟时间（即本模块曾出现的 bug）。
// 修正后：按 UTC 解析 → 整体 +8h → 读取 UTC 组件，得到稳定的东八区展示值，不受浏览器时区影响。
function formatDateTime(dt) {
    if (!dt) return '—';
    let iso = String(dt).trim();
    // 无时区标记（既无 Z 也无 ±HH:MM 偏移）→ 视为 UTC，补 Z 以强制按零时区解析
    if (!/[zZ]$/.test(iso) && !/[+\-]\d{2}:?\d{2}$/.test(iso)) {
        iso = iso.replace(' ', 'T');
        if (!/[zZ]$/.test(iso)) iso += 'Z';
    }
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(dt).slice(0, 19).replace('T', ' ');
    // 将 UTC 即时时刻整体 +8h，再取 UTC 组件 → 稳定东八区（UTC+8）展示，不受浏览器本地时区影响
    const d8 = new Date(d.getTime() + 8 * 3600 * 1000);
    const pad = n => String(n).padStart(2, '0');
    return `${d8.getUTCFullYear()}-${pad(d8.getUTCMonth()+1)}-${pad(d8.getUTCDate())} ${pad(d8.getUTCHours())}:${pad(d8.getUTCMinutes())}:${pad(d8.getUTCSeconds())}`;
}

// 计算策略统计 4 张卡片（真实口径）
function calcStats(transactions, summary, costBasis) {
    const totalTrades = Array.isArray(transactions) ? transactions.length : 0;
    const sells = (transactions || []).filter(t => t && t.action === 'sell');
    const sellCount = sells.length;
    const wins = sells.filter(t => (t.realized_pnl || 0) > 0).length;
    const winRate = sellCount ? Number((wins / sellCount * 100).toFixed(1)) : 0;
    const totalRealized = (summary && typeof summary.total_realized_pnl === 'number')
        ? summary.total_realized_pnl
        : sells.reduce((s, t) => s + (t.realized_pnl || 0), 0);
    const avgProfit = sellCount ? totalRealized / sellCount : 0;

    // 最大回撤：当前持仓浮亏率最差值
    let maxDrawdown = 0;
    if (costBasis && Array.isArray(costBasis.positions)) {
        for (const p of costBasis.positions) {
            const pnlPct = typeof p.pnlPct === 'number' ? p.pnlPct : 0;
            if (pnlPct < maxDrawdown) maxDrawdown = pnlPct;
        }
    }
    maxDrawdown = Number(maxDrawdown.toFixed(2));

    return { totalTrades, winRate, avgProfit, maxDrawdown, sellCount, totalRealized };
}

// 并行加载三类数据
async function loadStrategyData(clientId) {
    const [transactions, summary, costBasis] = await Promise.all([
        fetchTransactions(clientId, { limit: 200 }),
        fetchTransactionsSummary(clientId),
        fetchCostBasis(clientId, { method: 'average' }),
    ]);
    return {
        transactions: Array.isArray(transactions) ? transactions : [],
        summary: summary || { total_realized_pnl: 0, fee_summary: { total: 0 } },
        costBasis: costBasis || { positions: [] },
    };
}

function renderStats(stats) {
    const avgProfitStr = (stats.avgProfit >= 0 ? '+' : '-') + '¥' + formatCurrency(Math.abs(stats.avgProfit));
    // 已实现盈亏：正数前面 +，负数前面显式 -（不能用 Math.abs 吞掉负号）
    const realizedSign = stats.totalRealized >= 0 ? '+' : '-';
    const realizedStr = realizedSign + '¥' + formatCurrency(Math.abs(stats.totalRealized));
    return [
        { label: '总交易次数', value: stats.totalTrades, sub: '次', icon: '📊', color: 'bg-blue-50' },
        { label: '胜率（仅卖出）', value: stats.sellCount ? stats.winRate : '—', sub: stats.sellCount ? '%' : '', icon: '🎯', color: 'bg-green-50', valueColor: 'text-positive' },
        { label: '已实现盈亏', value: realizedStr, sub: '', icon: '💰', color: 'bg-purple-50', valueColor: getPnLColor(stats.totalRealized) },
        { label: '当前持仓最大浮亏', value: `${stats.maxDrawdown}%`, sub: '', icon: '⚠️', color: 'bg-red-50', valueColor: stats.maxDrawdown < 0 ? 'text-down' : 'text-ink' },
    ].map(s => `
        <div class="${s.color} rounded-2xl p-5">
            <div class="text-xs text-muted mb-1">${s.label}</div>
            <div class="font-mono text-xl font-semibold ${s.valueColor || 'text-ink'}">${s.value}<span class="text-sm font-normal text-muted ml-0.5">${s.sub}</span></div>
        </div>
    `).join('');
}

// 成本价单元格渲染：
//   加仓（buy + prev_cost_price 存在）→ "成本价 ¥new(+¥Δ)" 或 "成本价 ¥new(-¥Δ)"，Δ 字红绿
//   新买入（buy + prev_cost_price 为空）→ 只显示 "成本价 ¥new"，无括号
//   卖出（sell）→ 仅显示 "成本价 ¥curr"，无括号（用户明确：卖出成本价不变，不显示括号）
//   清仓（sell 且 cost_price 空）→ 回退展示 prev_cost_price（清仓前持仓成本价），而非 "—"
function renderCostPriceCell(tx) {
    const isBuy = tx.action === 'buy';
    const curr = tx.cost_price;
    const prev = tx.prev_cost_price;

    // 基础文案：成本价 + 交易后最新成本价
    let base = '成本价 ';
    if (curr == null || isNaN(curr)) {
        // 清仓（sell 且持仓已删）：交易后成本价为空，但 prev_cost_price 仍记录「清仓前持仓成本价」，
        // 即本次清仓的真实成本基准，应展示而非显示「—」，确保清仓后仍能看到对应持仓的成本价。
        const ref = (prev != null && !isNaN(prev)) ? prev : null;
        if (ref != null) {
            base += `<span class="font-mono text-ink">¥${formatNumber(ref)}</span>`;
        } else {
            base += '<span class="font-mono text-ink">—</span>';
        }
        return base;
    }
    base += `<span class="font-mono text-ink">¥${formatNumber(curr)}</span>`;

    // 仅"加仓"（非首次买入）场景显示括号：buy 且 prev 存在 且 curr != prev（避免 0 变化时无意义）
    if (isBuy && prev != null && !isNaN(prev)) {
        const delta = curr - prev;
        // 极小浮差（< 0.005 元）视为相等，不显示括号避免噪声
        if (Math.abs(delta) >= 0.005) {
            const positive = delta > 0;
            const sign = positive ? '+' : '-';
            const color = positive ? 'text-up' : 'text-down';
            return `${base}<span class="${color} font-mono text-xs ml-1">(${sign}¥${formatCurrency(Math.abs(delta))})</span>`;
        }
    }
    return base;
}

// 单条交易流水 → 时间线卡片 HTML（每笔独立一行，不再合并）
function renderTradeRow(tx) {
    const isBuy = tx.action === 'buy' || tx.action === 'adjust';
    const market = MARKET_LABEL[tx.market] || tx.market || '';
    const marketBadge = market
        ? `<span class="px-1.5 py-0.5 text-[10px] font-medium rounded border border-hairline text-muted/80 ml-1">${market}</span>`
        : '';
    const amount = (tx.price || 0) * (tx.quantity || 0);
    const feeCommission = tx.fee_commission || 0;
    const feeStamp = tx.fee_stamp_tax || 0;
    const feeTransfer = tx.fee_transfer_fee || 0;
    const feeTotal = tx.fee_amount || 0;

    // 盈亏显示：
    //   买入 → "持仓中"徽章
    //   卖出 → 第二个标签为"成功/失败"徽章，紧跟盈亏金额（亏损带负号，红色=涨绿色=跌）
    let pnlHtml = '';
    if (isBuy) {
        pnlHtml = `<span class="px-2 py-0.5 text-xs rounded-full bg-surface-strong text-muted">持仓中</span>`;
    } else {
        const rpnl = tx.realized_pnl || 0;
        const positive = rpnl > 0;
        const negative = rpnl < 0;
        // 状态徽章：成功（绿色）/失败（红色）/持平（灰）
        const statusBadge = positive
            ? `<span class="px-2 py-0.5 text-xs font-medium rounded-full bg-positive/10 text-positive">成功</span>`
            : negative
                ? `<span class="px-2 py-0.5 text-xs font-medium rounded-full bg-negative/10 text-negative">失败</span>`
                : `<span class="px-2 py-0.5 text-xs font-medium rounded-full bg-surface-strong text-muted">持平</span>`;
        // 盈亏金额数字：盈利带+号，亏损带-号
        const amountCls = positive ? 'text-up' : (negative ? 'text-down' : 'text-muted');
        const sign = positive ? '+' : (negative ? '-' : '');
        const amountStr = `¥${formatCurrency(Math.abs(rpnl))}`;
        pnlHtml = `${statusBadge}<span class="font-mono text-sm ${amountCls} ml-2">${sign}${amountStr}</span>`;
    }

    const timeStr = formatDateTime(tx.executed_at);
    const [datePart, timePart] = timeStr.includes(' ') ? timeStr.split(' ') : [timeStr, ''];

    return `
        <div class="relative pl-14 pr-5 py-5 hover:bg-surface-soft/30 transition-colors">
            <div class="absolute left-4 top-6 w-3 h-3 rounded-full ${tx.action === 'buy' ? 'bg-up ring-red-50' : tx.action === 'sell' ? 'bg-down ring-green-50' : 'bg-amber-500 ring-amber-100'} ring-4"></div>

            <div class="flex flex-col lg:flex-row lg:items-start gap-3">
                <div class="lg:w-64 flex-shrink-0">
                    <div class="text-xs text-muted font-mono">${datePart || '—'}</div>
                    <div class="text-xs text-muted/70 font-mono">${timePart || ''}</div>
                    ${tx.source ? `<div class="mt-1 inline-block px-2 py-0.5 text-[10px] font-medium rounded-md bg-surface-strong text-muted/80" title="操作链路审计ID: ${tx.audit_log_id ?? '-'}">${SOURCE_LABEL[tx.source] || tx.source}</div>` : ''}
                </div>

                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="px-2 py-0.5 text-xs font-semibold rounded-full ${getBadgeClass(tx.action)}">${getTypeLabel(tx.action)}</span>
                        <span class="font-medium text-ink">${tx.name || '—'}</span>
                        <span class="text-sm text-muted font-mono">${tx.code || ''}</span>
                        ${marketBadge}
                        ${pnlHtml}
                    </div>

                    <div class="mt-2 grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-1 text-sm">
                        <div class="text-muted">价格 <span class="font-mono text-ink">¥${formatNumber(tx.price || 0)}</span></div>
                        <div class="text-muted">数量 <span class="font-mono text-ink">${(tx.quantity || 0).toLocaleString()}股</span></div>
                        <div class="text-muted">成交金额 <span class="font-mono text-ink">¥${formatCurrency(amount)}</span></div>
                        <div class="text-muted">${renderCostPriceCell(tx)}</div>
                    </div>

                    <div class="mt-2 pt-2 border-t border-hairline/60">
                        <div class="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
                            <span>手续费合计：<span class="font-mono text-body">¥${formatCurrency(feeTotal)}</span></span>
                            <span>佣金 <span class="font-mono text-body">¥${formatCurrency(feeCommission)}</span></span>
                            <span>印花 <span class="font-mono text-body">¥${formatCurrency(feeStamp)}</span></span>
                            <span>过户 <span class="font-mono text-body">¥${formatCurrency(feeTransfer)}</span></span>
                            ${!isBuy && typeof tx.realized_pnl === 'number'
                                ? (() => {
                                    const rpnl = tx.realized_pnl;
                                    const positive = rpnl > 0;
                                    const negative = rpnl < 0;
                                    const color = getPnLColor(rpnl);
                                    if (positive) {
                                        return `<span class="ml-auto font-medium ${color}">单笔已实现盈利 ¥${formatCurrency(rpnl)}</span>`;
                                    } else if (negative) {
                                        return `<span class="ml-auto font-medium ${color}">单笔发生亏损 ¥${formatCurrency(Math.abs(rpnl))}</span>`;
                                    } else {
                                        return `<span class="ml-auto font-medium text-muted">单笔已实现盈亏 ¥0.00</span>`;
                                    }
                                })()
                                : ''}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// 渲染时间线：每笔交易单独一行，默认显示前5条，其余点击"展开更多"显示
function renderTimeline(transactions) {
    if (!transactions || !transactions.length) {
        return `<div class="p-12 text-center">
            <div class="text-5xl mb-3">📝</div>
            <div class="text-sm text-muted">该客户暂无交易记录</div>
            <div class="text-xs text-muted/70 mt-1">通过加减仓操作买入/卖出股票后，将在此处显示逐笔交易流水</div>
        </div>`;
    }
    const rows = transactions.map(renderTradeRow);
    const THRESHOLD = 4;
    if (rows.length <= THRESHOLD) {
        return rows.join('');
    }
    const top = rows.slice(0, THRESHOLD);
    const rest = rows.slice(THRESHOLD);
    return `
        ${top.join('')}
        <div id="strategyTimelineRest" class="hidden">
            ${rest.join('')}
        </div>
        <div class="px-14 pb-5 pt-1 sticky bottom-0 bg-canvas/95 backdrop-blur-sm border-t border-hairline/40 -mx-5">
            <div class="pr-9">
                <button id="strategyExpandBtn"
                    onclick="window.toggleStrategyTimeline(this)"
                    class="w-full py-2.5 text-sm text-primary hover:text-primary/80 hover:bg-primary/5 rounded-xl border border-primary/20 transition-all flex items-center justify-center gap-1.5">
                    <svg class="w-4 h-4 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                    <span id="strategyExpandText">展开更多（剩余 ${rest.length} 条记录）</span>
                </button>
            </div>
        </div>
    `;
}

// 展开/收起 策略复盘时间线（全局函数，供 inline onclick 调用）
export function toggleStrategyTimeline(btnEl) {
    const rest = document.getElementById('strategyTimelineRest');
    const textEl = document.getElementById('strategyExpandText');
    const icon = btnEl?.querySelector('svg');
    const container = document.getElementById('strategyTimeline');
    if (!rest) return;
    const expanded = !rest.classList.contains('hidden');
    if (expanded) {
        // 收起 → 隐藏剩余记录；同时**移除**滚动能力，回到"普通显示无滚动条"状态
        rest.classList.add('hidden');
        if (textEl) textEl.textContent = `展开更多（剩余 ${(rest.children.length || rest.querySelectorAll(':scope > div').length)} 条记录）`;
        if (icon) icon.style.transform = 'rotate(0deg)';
        if (container) {
            container.classList.remove(
                'max-h-[65vh]', 'lg:max-h-[75vh]',
                'overflow-y-auto', 'scroll-smooth',
                'scrollbar-thin', 'scrollbar-track-transparent',
            );
            // 回到顶部，避免下次展开时在奇怪的 scrollTop 位置
            container.scrollTop = 0;
        }
    } else {
        // 展开 → 显示剩余记录；**此时才启用**垂直滚动（符合用户要求：只有展开后才能滑动）
        rest.classList.remove('hidden');
        if (textEl) textEl.textContent = '收起';
        if (icon) icon.style.transform = 'rotate(180deg)';
        if (container) {
            container.classList.add(
                'max-h-[65vh]', 'lg:max-h-[75vh]',
                'overflow-y-auto', 'scroll-smooth',
                'scrollbar-thin', 'scrollbar-track-transparent',
            );
            // 平滑滚动到底部，让用户第一时间看到展开后的新记录
            container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
        }
    }
}

// 展开策略复盘时间线（导入交易后调用，确保新记录即使超过初始阈值也立即可见）
export function expandStrategyTimeline() {
    const rest = document.getElementById('strategyTimelineRest');
    if (!rest) return;               // 记录数 ≤ 初始阈值，无需展开
    rest.classList.remove('hidden');
    const btn = document.getElementById('strategyExpandBtn');
    const txt = document.getElementById('strategyExpandText');
    const svg = btn?.querySelector('svg');
    if (svg) svg.style.transform = 'rotate(180deg)';
    if (txt) txt.textContent = '收起';
    const container = document.getElementById('strategyTimeline');
    if (container) {
        container.classList.add('max-h-[65vh]', 'lg:max-h-[75vh]', 'overflow-y-auto', 'scroll-smooth', 'scrollbar-thin', 'scrollbar-track-transparent');
    }
}

// 显示骨架屏 loading
function renderLoading() {
    const statsContainer = document.getElementById('strategyStats');
    const timelineContainer = document.getElementById('strategyTimeline');
    if (statsContainer) statsContainer.innerHTML = Array(4).fill(0).map(() => `
        <div class="bg-surface-soft rounded-2xl p-5 animate-pulse">
            <div class="h-3 w-20 bg-hairline rounded mb-3"></div>
            <div class="h-7 w-32 bg-hairline rounded"></div>
        </div>`).join('');
    if (timelineContainer) timelineContainer.innerHTML = Array(2).fill(0).map(() => `
        <div class="p-5 animate-pulse">
            <div class="h-4 w-40 bg-hairline rounded mb-3"></div>
            <div class="h-3 w-56 bg-hairline rounded mb-2"></div>
            <div class="h-3 w-48 bg-hairline rounded"></div>
        </div>`).join('');
}

// 主入口：异步加载真实数据并渲染
export async function renderStrategySection() {
    const client = getCurrentClient();
    const statsContainer = document.getElementById('strategyStats');
    const timelineContainer = document.getElementById('strategyTimeline');
    if (!statsContainer || !timelineContainer) return;

    if (!client) {
        statsContainer.innerHTML = '';
        timelineContainer.innerHTML = '<div class="p-8 text-center text-sm text-muted">请先选择一位客户</div>';
        return;
    }

    renderLoading();

    try {
        const { transactions, summary, costBasis } = await loadStrategyData(client.id);
        const stats = calcStats(transactions, summary, costBasis);
        statsContainer.innerHTML = renderStats(stats);
        timelineContainer.innerHTML = renderTimeline(transactions);

        // 注意：时间线滚动只在用户手动点击"展开更多"后才启用（toggleStrategyTimeline 中注入）
        // 默认（≤5 条或未展开）保持普通显示、无滚动条（用户需求明确）
    } catch (e) {
        console.error('策略复盘加载失败:', e);
        showToast('策略复盘加载失败，请稍后重试', 'error');
        statsContainer.innerHTML = Array(4).fill(0).map(() => `
            <div class="bg-surface-soft rounded-2xl p-5 opacity-50">
                <div class="text-xs text-muted mb-1">加载失败</div>
                <div class="font-mono text-xl font-semibold text-muted">—</div>
            </div>`).join('');
        timelineContainer.innerHTML = `<div class="p-8 text-center text-sm text-muted">数据加载异常：${e && e.message ? e.message : '未知错误'}</div>`;
    }
}
