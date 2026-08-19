// ============================================================
// 客户工作台组件：市场横条 / 客户列表 / 客户详情 / 风险预警 / 标签备注
// ============================================================
import {
    clients, currentClientId, clientWarnOnly,
    setCurrentClient, setClientWarnOnly, getCurrentClient, clientStats, clientRiskAlerts,
    getFilteredClients, getClientRelations, updateClientRemote, getUserPositions
} from '../services/clientService.js';
import { fmtMoney } from '../core/formatters.js';
import { RISK_BADGE } from '../core/config.js';
import { showToast } from '../core/ui.js';
import { marketDataState } from '../services/marketService.js';
import { renderStatsCards, renderPositionsTable, renderCharts, renderSectorConcentration } from './overview.js';
import { renderStrategySection } from './strategy.js';
import { canEditClient, canHandleAlerts } from '../permissions/access.js';
import { fetchClientAlerts, updateAlertStatus, evaluateRisk } from '../services/authService.js';

// --- 市场环境横条 ---
export function renderMarketTicker() {
    const el = document.getElementById('marketTicker');
    if (!el) return;
    const m = marketDataState.realtime;
    if (!m) {
        el.innerHTML = '<div class="text-center text-sm text-muted py-2">暂无行情数据</div>';
        return;
    }
    const indices = m.indices || [];
    const ticker = indices.map(idx => {
        const isUp = idx.change >= 0;
        const color = isUp ? 'text-up' : 'text-down';
        return `<div class="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-surface-soft/60">
            <span class="text-xs text-muted whitespace-nowrap">${idx.name}</span>
            <span class="font-mono text-sm font-semibold ${color}">${idx.value.toFixed(2)}</span>
            <span class="font-mono text-xs ${color}">${isUp ? '+' : ''}${idx.change.toFixed(2)} (${isUp ? '+' : ''}${idx.changePct.toFixed(2)}%)</span>
        </div>`;
    }).join('');
    const adv = m.advCount || 0, dec = m.decCount || 0, tot = m.totalStocks || 1;
    el.innerHTML = `
        <div class="flex flex-wrap items-center gap-3">
            ${ticker}
            <div class="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-surface-soft/60">
                <span class="text-xs text-muted">两市成交</span>
                <span class="font-mono text-sm font-semibold text-ink">${(m.totalVolume || 0).toLocaleString()}亿</span>
            </div>
            <div class="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-surface-soft/60">
                <span class="text-xs text-up font-medium">↑${adv.toLocaleString()}</span>
                <span class="text-xs text-muted">/</span>
                <span class="text-xs text-down font-medium">↓${dec.toLocaleString()}</span>
                <span class="text-xs text-muted">涨占比 ${tot ? (adv / tot * 100).toFixed(1) : 0}%</span>
            </div>
        </div>`;
}

// --- 客户列表 ---
export function renderClientList() {
    const container = document.getElementById('clientList');
    if (!container) return;
    const list = getFilteredClients();
    const cntEl = document.getElementById('clientFilteredCount');
    const totEl = document.getElementById('clientTotalCount');
    if (cntEl) cntEl.textContent = list.length;
    if (totEl) totEl.textContent = clients.length;

    if (!list.length) {
        container.innerHTML = '<div class="p-8 text-center text-sm text-muted">没有符合条件的客户</div>';
        return;
    }

    container.innerHTML = list.map(c => {
        const s = clientStats(c);
        const alerts = clientRiskAlerts(c);
        const isActive = c.id === currentClientId;
        const pnlColor = s.totalPnl >= 0 ? 'text-up' : 'text-down';
        return `
        <div onclick="selectClient('${c.id}')" class="px-4 py-3 cursor-pointer transition-colors hover:bg-surface-soft/70 ${isActive ? 'bg-primary/5 border-l-2 border-primary' : 'border-l-2 border-transparent'}">
            <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2 min-w-0">
                    <span class="w-8 h-8 rounded-full bg-gradient-to-br from-primary to-blue-600 text-white text-xs font-semibold flex items-center justify-center shrink-0">${c.name.charAt(0)}</span>
                    <div class="min-w-0">
                        <div class="flex items-center gap-1.5">
                            <span class="text-sm font-medium text-ink truncate">${c.name}</span>
                            ${alerts.length ? '<span class="w-2 h-2 rounded-full bg-negative shrink-0" title="' + alerts.length + ' 项风险"></span>' : ''}
                        </div>
                        <div class="flex items-center gap-1.5 text-xs text-muted">
                            <span>${c.id}</span><span>·</span><span>${c.age ? c.age + '岁' : '-'}</span><span>·</span>
                            <span class="px-1.5 py-0.5 rounded-md ${RISK_BADGE[c.riskLevel] || 'text-muted'}">${c.riskLevel}</span>
                        </div>
                    </div>
                </div>
                <div class="text-right shrink-0">
                    <div class="font-mono text-sm font-semibold text-ink">${fmtMoney(s.totalAssets)}</div>
                    <div class="font-mono text-xs ${pnlColor}">${s.totalPnl >= 0 ? '+' : ''}${fmtMoney(s.totalPnl)}</div>
                </div>
            </div>
        </div>`;
    }).join('');
}

export function toggleWarnOnly() {
    setClientWarnOnly(!clientWarnOnly);
    const btn = document.getElementById('warnOnlyBtn');
    if (btn) {
        btn.classList.toggle('bg-negative/10', clientWarnOnly);
        btn.classList.toggle('text-negative', clientWarnOnly);
        btn.classList.toggle('border-negative/30', clientWarnOnly);
    }
    renderClientList();
}

export function selectClient(id) {
    setCurrentClient(id);
    refreshClientDetail();
    renderClientList();
}

// --- 客户详情刷新（资料 + 风险 + 统计 + 持仓 + 图表） ---
export function refreshClientDetail() {
    renderClientProfile();
    renderRiskAlerts();
    renderStatsCards();
    renderPositionsTable('all');
    renderCharts();
    renderSectorConcentration();
    renderStrategySection();
    const cnt = document.getElementById('positionCount');
    if (cnt) cnt.textContent = getUserPositions().length;
}

// --- 客户资料卡 ---
export function renderClientProfile() {
    const el = document.getElementById('clientProfileCard');
    if (!el) return;
    el.classList.remove('animate-pulse');
    const c = getCurrentClient();
    if (!c) { el.innerHTML = ''; return; }
    const s = clientStats(c);
    const rel = getClientRelations(c);
    const pnlColor = s.totalPnl >= 0 ? 'text-up' : 'text-down';
    el.innerHTML = `
    <div class="premium-card p-6">
        <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="flex items-center gap-4">
                <span class="w-14 h-14 rounded-2xl bg-gradient-to-br from-primary to-blue-600 text-white text-xl font-semibold flex items-center justify-center shadow-sm shadow-primary/25">${c.name.charAt(0)}</span>
                <div>
                    <div class="flex items-center gap-2 flex-wrap">
                        <h2 class="text-xl font-semibold text-ink">${c.name}</h2>
                        <span class="text-xs text-muted font-mono">${c.id}</span>
                        <span class="px-2 py-0.5 text-xs font-medium rounded-full ${RISK_BADGE[c.riskLevel] || 'bg-surface-strong text-muted'}">${c.riskLevel}</span>
                        ${(c.tags || []).map(t => `<span class="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-primary/10 text-primary">${t}<button onclick="removeClientTag('${t}')" class="hover:text-down transition-colors">×</button></span>`).join('')}
                        <button onclick="addClientTag()" class="px-2 py-0.5 text-xs text-muted border border-dashed border-hairline rounded-full hover:text-primary hover:border-primary/40 transition-colors">+ 标签</button>
                    </div>
                    <div class="flex items-center gap-3 mt-1.5 text-sm text-muted flex-wrap">
                        <span>${c.age ? c.age + ' 岁' : '-'}</span><span>·</span>
                        <span>持仓 ${(c.positions || []).length} 只</span><span>·</span>
                        <span>可用资金 <span class="font-mono text-ink">${fmtMoney(c.availableCash)}</span></span>
                    </div>
                    <div class="flex items-center gap-2 mt-1.5 text-xs text-muted">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"/></svg>
                        <span>顾问 <span class="text-ink">${rel.advisorName}</span></span>
                        <span>·</span>
                        <span>客服 <span class="text-ink">${rel.serviceNames.join('、') || '未分配'}</span></span>
                    </div>
                </div>
            </div>
            <div class="flex items-center gap-5 flex-wrap">
                <div class="text-right">
                    <div class="text-xs text-muted">总资产</div>
                    <div class="font-mono text-2xl font-semibold text-ink">${fmtMoney(s.totalAssets)}</div>
                </div>
                <div class="text-right">
                    <div class="text-xs text-muted">持仓盈亏</div>
                    <div class="font-mono text-2xl font-semibold ${pnlColor}">${s.totalPnl >= 0 ? '+' : ''}${fmtMoney(s.totalPnl)} <span class="text-sm">(${s.totalPnlPct >= 0 ? '+' : ''}${s.totalPnlPct.toFixed(1)}%)</span></div>
                </div>
                <button onclick="exportClientReport()" class="btn-primary flex items-center gap-1.5 px-4 py-2.5 text-sm font-semibold rounded-pill">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
                    导出客户报告
                </button>
            </div>
        </div>

        <div class="mt-5 pt-4 border-t border-hairline">
            <label class="text-xs font-medium text-muted mb-1.5 flex items-center gap-1.5">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                客户备注
            </label>
            <textarea id="clientNoteInput" onchange="updateClientNote(this.value)" rows="2" placeholder="记录客户偏好、跟进计划…" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all resize-none">${c.note || ''}</textarea>
        </div>
    </div>`;
}

// --- 风险预警卡 ---
const ALERT_STATUS = {
    open: { label: '待处理', cls: 'bg-negative/10 text-negative' },
    acknowledged: { label: '已确认', cls: 'bg-warning/10 text-warning' },
    resolved: { label: '已解决', cls: 'bg-positive/10 text-positive' },
};

// 从后端读取某客户的预警列表（后端已校验可见性）
async function loadRiskAlerts(clientId) {
    try {
        return await fetchClientAlerts(clientId);
    } catch (e) {
        console.warn('加载风险预警失败:', e);
        return [];
    }
}

export async function renderRiskAlerts() {
    const el = document.getElementById('riskAlertCard');
    if (!el) return;
    el.classList.remove('animate-pulse');
    const c = getCurrentClient();
    if (!c) { el.innerHTML = ''; return; }
    const clientId = c.id;

    const alerts = await loadRiskAlerts(clientId);
    // 切换客户后丢弃过期请求结果
    if (getCurrentClient()?.id !== clientId) return;

    const canHandle = canHandleAlerts();

    if (!alerts.length) {
        el.innerHTML = `<div class="premium-card px-5 py-4 flex items-center justify-between gap-3 border-positive/20">
            <div class="flex items-center gap-3">
                <svg class="w-5 h-5 text-positive" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                <span class="text-sm text-body">该客户组合当前无重大风险预警</span>
            </div>
            ${canHandle ? `<button onclick="evaluateClientRisk()" class="px-3 py-1.5 text-xs font-medium rounded-lg border border-hairline text-muted hover:text-primary hover:border-primary/40 transition-colors">立即评估</button>` : ''}
        </div>`;
        return;
    }

    el.innerHTML = `<div class="premium-card p-5 border-negative/20">
        <div class="flex items-center gap-2 mb-3">
            <svg class="w-4 h-4 text-negative" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
            <h3 class="text-sm font-semibold text-ink">风险预警</h3>
            <span class="text-xs text-negative font-mono">${alerts.length} 项</span>
            ${canHandle ? `<button onclick="evaluateClientRisk()" class="ml-auto px-3 py-1 text-xs font-medium rounded-lg border border-hairline text-muted hover:text-primary hover:border-primary/40 transition-colors">重新评估</button>` : ''}
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            ${alerts.map(a => renderAlertCard(a, canHandle)).join('')}
        </div>
    </div>`;
}

function renderAlertCard(a, canHandle) {
    const status = ALERT_STATUS[a.status] || { label: a.status || '未知', cls: 'bg-surface-strong text-muted' };
    const levelDot = a.level === 'high' ? 'bg-negative' : 'bg-warning';
    return `<div class="flex items-start gap-2.5 p-3 rounded-xl bg-negative/5 border border-negative/10">
        <span class="w-2 h-2 rounded-full ${levelDot} mt-1.5 shrink-0"></span>
        <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
                <span class="text-sm font-medium text-ink">${a.title}</span>
                <span class="px-1.5 py-0.5 text-xs font-medium rounded-md ${status.cls}">${status.label}</span>
            </div>
            <div class="text-xs text-muted mt-0.5">${a.description}</div>
            ${canHandle && a.status !== 'resolved' ? `
                <div class="flex items-center gap-2 mt-2">
                    ${a.status === 'open' ? `<button onclick="handleAlertStatus(${a.id},'acknowledged')" class="px-2.5 py-1 text-xs font-medium rounded-lg bg-warning/10 text-warning hover:bg-warning/20 transition-colors">确认</button>` : ''}
                    <button onclick="handleAlertStatus(${a.id},'resolved')" class="px-2.5 py-1 text-xs font-medium rounded-lg bg-positive/10 text-positive hover:bg-positive/20 transition-colors">解决</button>
                </div>` : ''}
        </div>
    </div>`;
}

// 触发风险评估（仅客服/顾问/管理员）
export async function evaluateClientRisk() {
    const c = getCurrentClient();
    if (!c || !canHandleAlerts()) { showToast('❌ 无权限执行此操作', 'error'); return; }
    try {
        await evaluateRisk(c.id);
        showToast('✅ 风险评估已完成', 'success');
        await renderRiskAlerts();
    } catch (e) {
        showToast('❌ 风险评估失败：' + (e.message || '未知错误'), 'error');
    }
}

// 更新预警状态（确认/解决），仅客服/顾问/管理员
export async function handleAlertStatus(alertId, status) {
    if (!canHandleAlerts()) { showToast('❌ 无权限执行此操作', 'error'); return; }
    try {
        await updateAlertStatus(alertId, status);
        showToast('✅ 预警状态已更新', 'success');
        await renderRiskAlerts();
    } catch (e) {
        showToast('❌ 操作失败：' + (e.message || '未知错误'), 'error');
    }
}

// --- 备注 / 标签 ---
function guardEdit() {
    const c = getCurrentClient();
    if (!c || !canEditClient(c)) {
        showToast('❌ 无权限执行此操作', 'error');
        return false;
    }
    return true;
}

export async function updateClientNote(val) {
    if (!guardEdit()) return;
    const c = getCurrentClient();
    if (!c) return;
    c.note = val;
    try {
        await updateClientRemote(c.id, { note: val });
        showToast('✅ 备注已保存', 'success');
    } catch (e) {
        showToast('❌ 备注保存失败：' + (e.message || '未知错误'), 'error');
    }
}

export async function addClientTag() {
    if (!guardEdit()) return;
    const c = getCurrentClient();
    if (!c) return;
    const tag = prompt('输入新标签（如 VIP / 待跟进）');
    if (tag && tag.trim()) {
        const t = tag.trim();
        if (!c.tags.includes(t)) c.tags.push(t);
        try {
            await updateClientRemote(c.id, { tags: c.tags });
            renderClientProfile();
            renderClientList();
            showToast('✅ 标签已添加', 'success');
        } catch (e) {
            showToast('❌ 标签保存失败：' + (e.message || '未知错误'), 'error');
        }
    }
}

export async function removeClientTag(tag) {
    if (!guardEdit()) return;
    const c = getCurrentClient();
    if (!c) return;
    c.tags = (c.tags || []).filter(t => t !== tag);
    try {
        await updateClientRemote(c.id, { tags: c.tags });
        renderClientProfile();
        renderClientList();
        showToast('✅ 标签已移除', 'success');
    } catch (e) {
        showToast('❌ 标签保存失败：' + (e.message || '未知错误'), 'error');
    }
}

// --- 导出客户报告（独立 HTML，可 Ctrl+P 转 PDF） ---
export function exportClientReport() {
    const c = getCurrentClient();
    if (!c) return;
    const s = clientStats(c);
    const alerts = clientRiskAlerts(c);
    const pnlColor = s.totalPnl >= 0 ? '#cf202f' : '#05b169';
    const rows = (c.positions || []).map(p => {
        const pnl = (p.price - p.costPrice) * p.quantity;
        const pnlPct = (p.price - p.costPrice) / p.costPrice * 100;
        const color = pnl >= 0 ? '#cf202f' : '#05b169';
        return `<tr>
            <td>${p.name}</td><td>${p.code}</td><td>${p.sector}</td>
            <td>${p.quantity.toLocaleString()}</td><td>${p.costPrice.toFixed(2)}</td><td>${p.price.toFixed(2)}</td>
            <td style="color:${color}">${pnl >= 0 ? '+' : ''}${(pnl / 10000).toFixed(2)}万</td>
            <td style="color:${color}">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</td>
        </tr>`;
    }).join('');
    const alertHtml = alerts.length
        ? alerts.map(a => `<li>${a.title}：${a.desc}</li>`).join('')
        : '<li>无重大风险</li>';
    const dateStr = new Date().toLocaleDateString('zh-CN');
    const report = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>${c.name} 客户持仓报告</title>
    <style>
        body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#0a0b0d;margin:0;padding:32px;background:#fff}
        h1{font-size:22px;margin:0 0 4px} h2{font-size:15px;margin:24px 0 8px;border-left:4px solid #0052ff;padding-left:8px}
        .meta{color:#5b616e;font-size:13px;margin-bottom:16px}
        .stats{display:flex;gap:12px;margin:12px 0;flex-wrap:wrap}
        .stat{flex:1;min-width:140px;border:1px solid #eceef1;border-radius:12px;padding:12px}
        .stat .v{font-size:18px;font-weight:600;font-family:ui-monospace,monospace}
        .stat .l{font-size:12px;color:#7c828a;margin-top:2px}
        table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
        th,td{padding:8px 10px;border-bottom:1px solid #eef0f3;text-align:right}
        th:first-child,td:first-child{text-align:left}
        th{background:#f7f7f7;color:#5b616e;font-weight:600}
        .alert{background:#fdf0f0;border:1px solid #f5c6c6;border-radius:10px;padding:10px 14px;font-size:13px;color:#a01320}
        .ok{background:#f0faf4;border:1px solid #bfe8cf;border-radius:10px;padding:10px 14px;font-size:13px;color:#0a7a44}
        .note{background:#f7f7f7;border-radius:10px;padding:12px;font-size:13px;color:#5b616e}
        .footer{margin-top:28px;font-size:11px;color:#a8acb3;border-top:1px solid #eef0f3;padding-top:12px}
        @media print{body{padding:16px}}
    </style></head><body>
        <h1>客户持仓报告</h1>
        <div class="meta">客户：${c.name}（${c.id}）· ${c.age ? c.age + ' 岁' : '年龄未填'} · ${c.riskLevel} · 报告日期 ${dateStr}</div>
        <div class="stats">
            <div class="stat"><div class="v">${fmtMoney(s.totalAssets)}</div><div class="l">总资产</div></div>
            <div class="stat"><div class="v" style="color:${pnlColor}">${s.totalPnl >= 0 ? '+' : ''}${fmtMoney(s.totalPnl)}</div><div class="l">持仓盈亏 (${s.totalPnlPct >= 0 ? '+' : ''}${s.totalPnlPct.toFixed(1)}%)</div></div>
            <div class="stat"><div class="v">${fmtMoney(s.totalMarket)}</div><div class="l">持仓市值</div></div>
            <div class="stat"><div class="v">${fmtMoney(c.availableCash)}</div><div class="l">可用资金</div></div>
        </div>
        <h2>持仓明细（${(c.positions || []).length} 只）</h2>
        <table><thead><tr><th>股票名称</th><th>代码</th><th>板块</th><th>数量</th><th>成本价</th><th>现价</th><th>盈亏</th><th>盈亏%</th></tr></thead><tbody>${rows}</tbody></table>
        <h2>风险预警</h2>
        <div class="${alerts.length ? 'alert' : 'ok'}"><ul style="margin:0;padding-left:16px">${alertHtml}</ul></div>
        <h2>客户备注</h2>
        <div class="note">${c.note || '（无备注）'}</div>
        <div class="footer">本报告由持仓复盘工作台自动生成，仅供内部参考 · 生成时间 ${new Date().toLocaleString('zh-CN')}</div>
    </body></html>`;
    const blob = new Blob([report], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = c.name + '_客户持仓报告_' + new Date().toISOString().slice(0, 10) + '.html';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast('✅ 客户报告已生成，打开后 Ctrl+P 可另存为 PDF', 'success');
}
