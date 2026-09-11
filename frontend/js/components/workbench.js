// ============================================================
// 客户工作台组件：市场横条 / 客户列表 / 客户详情 / 风险预警 / 标签备注
// ============================================================
import {
    clients, currentClientId, clientWarnOnly,
    setCurrentClient, setClientWarnOnly, getCurrentClient, clientStats, clientRiskAlerts,
    getFilteredClients, getClientRelations, updateClientRemote, getUserPositions,
    fetchClientPortfolio, fetchClientPnlHistory, portfolioData, pnlHistoryData,
    clientSummaries, summariesLoading, clientSummaryStats, computePortfolioStats, fetchClientSummaries,
} from '../services/clientService.js';
import { fmtMoney, formatCompactAmount } from '../core/formatters.js';
import { RISK_BADGE, ALL_MANAGED_TAGS } from '../core/config.js';
import { showToast } from '../core/ui.js';
import { marketDataState } from '../services/marketService.js';
import { updatePriceCache, getPrice } from '../services/priceService.js';
import { loadRelatedNews } from '../services/newsService.js';


function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}
import { renderStatsCards, renderPositionsTable, renderCharts, renderSectorConcentration } from './overview.js';
import { renderStrategySection } from './strategy.js';
import { canEditClient, canHandleAlerts, canCreateClient } from '../permissions/access.js';
import { fetchClientAlerts, updateAlertStatus, evaluateRisk, getToken } from '../services/authService.js';
import { initTagSelector, renderTagSelector, getSelectedManagedTags } from './tagSelector.js';

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

// --- 客户列表（盈亏使用后端实时摘要，未加载/失败时降级本地估算） ---
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

    // 摘要未就绪：顶部显示计算状态提示（不阻塞列表骨架渲染）
    const loadingHint = summariesLoading && !clientSummaries
        ? '<div class="px-4 py-1.5 text-xs text-muted bg-surface-soft/60 flex items-center gap-1.5"><span class="inline-block w-3 h-3 border-2 border-primary/30 border-t-primary rounded-full animate-spin"></span>盈亏计算中…</div>'
        : '';

    container.innerHTML = loadingHint + list.map(c => {
        const s = clientSummaryStats(c);
        const alerts = clientRiskAlerts(c);
        // 红点仅统计风险类预警（排除绿色恭喜）；仅有恭喜时展示绿点
        const riskCount = alerts.filter(a => a.level !== 'positive').length;
        const positiveOnly = alerts.length > 0 && riskCount === 0;
        const isActive = c.id === currentClientId;
        // 持仓盈亏：仅当前持仓浮动盈亏（不含已卖出历史收益）
        const pnlColor = s.floatingPnl >= 0 ? 'text-up' : 'text-down';
        // 降级估算时标注（后端摘要缺失，仅按成本价/缓存价估算）
        const pnlValueHtml = s.isRealtime
            ? `${s.floatingPnl >= 0 ? '+' : ''}${fmtMoney(s.floatingPnl)}`
            : `<span title="实时数据未就绪，当前为估算值">${s.floatingPnl >= 0 ? '+' : ''}${fmtMoney(s.floatingPnl)}~</span>`;
        return `
        <div onclick="selectClient('${c.id}')" class="px-4 py-3 cursor-pointer transition-colors hover:bg-surface-soft/70 ${isActive ? 'bg-primary/5 border-l-2 border-primary' : 'border-l-2 border-transparent'}">
            <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2 min-w-0">
                    <span class="w-8 h-8 rounded-full bg-gradient-to-br from-primary to-blue-600 text-white text-xs font-semibold flex items-center justify-center shrink-0">${c.name.charAt(0)}</span>
                    <div class="min-w-0">
                        <div class="flex items-center gap-1.5">
                            <span class="text-sm font-medium text-ink truncate">${c.name}</span>
                            ${riskCount ? '<span class="w-2 h-2 rounded-full bg-negative shrink-0" title="' + riskCount + ' 项风险"></span>' : ''}
                            ${positiveOnly ? '<span class="w-2 h-2 rounded-full bg-positive shrink-0" title="组合浮盈达标"></span>' : ''}
                        </div>
                        <div class="flex items-center gap-1.5 text-xs text-muted">
                            <span>${c.id}</span><span>·</span><span>${c.age ? c.age + '岁' : '-'}</span><span>·</span>
                            <span class="px-1.5 py-0.5 rounded-md ${RISK_BADGE[c.riskLevel] || 'text-muted'}">${c.riskLevel}</span>
                        </div>
                    </div>
                </div>
                <div class="text-right shrink-0">
                    <div class="font-mono text-sm font-semibold text-ink">${fmtMoney(s.totalAssets)}</div>
                    <div class="font-mono text-xs ${pnlColor}">${pnlValueHtml}</div>
                </div>
            </div>
        </div>`;
    }).join('');
}

// 拉取客户盈亏摘要并刷新列表（初始化/定时刷新/持仓变更后调用）
export async function refreshClientSummaries() {
    const prev = clientSummaries;
    await fetchClientSummaries(true);
    // 仅在有变化时重渲染，避免筛选器输入被刷新打断
    if (!prev || JSON.stringify(prev.summaries) !== JSON.stringify(clientSummaries?.summaries)) {
        renderClientList();
    }
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

export async function selectClient(id) {
    setCurrentClient(id);
    await refreshClientDetail();
    renderClientList();
}

// --- 客户详情刷新（资料 + 风险 + 统计 + 持仓 + 图表） ---
export async function refreshClientDetail() {
    // 并行加载实时数据
    const c = getCurrentClient();
    if (c) {
        await Promise.all([
            fetchClientPortfolio(c.id).catch(() => null),
            fetchClientPnlHistory(c.id, 60).catch(() => null),
        ]);
        // 更新实时价格缓存（供弹窗/策略等组件降级使用）
        updatePriceCache(portfolioData);
    }

    // 名片与概览均基于同一份实时 portfolioData 渲染，确保总资产/持仓盈亏数值完全一致
    renderClientProfile();
    renderRiskAlerts();
    renderStatsCards(portfolioData);
    renderPositionsTable('all', portfolioData);
    renderCharts(pnlHistoryData);
    renderSectorConcentration();
    await renderStrategySection();
    const cnt = document.getElementById('positionCount');
    if (cnt) {
        const positions = portfolioData?.positions || getUserPositions();
        cnt.textContent = positions.length;
    }

    // 持仓相关快讯（后端两表联动）：随客户切换刷新，置顶展示与持仓相关的市场动态
    const relClient = getCurrentClient();
    if (relClient) {
        loadRelatedNews(relClient.id).catch(() => {});
    } else {
        loadRelatedNews(null).catch(() => {});
    }
}

// --- 客户资料卡 ---
// portfolio 缺省取模块级 portfolioData；显式传入时（如调仓后）与持仓概览共用同一对象。
// 通过 computePortfolioStats 与「持仓概览」(renderStatsCards) 共用同一计算入口，
// 保证「总资产 / 持仓盈亏」两处数值完全一致、零差异。
export function renderClientProfile(portfolio = portfolioData) {
    const el = document.getElementById('clientProfileCard');
    if (!el) return;
    el.classList.remove('animate-pulse');
    const c = getCurrentClient();
    if (!c) { el.innerHTML = ''; return; }
    const s = computePortfolioStats(portfolio);
    const rel = getClientRelations(c);
    // 客户概览卡片「总盈亏」= 浮动 + 已实现（累计口径），故用 totalPnl 判定颜色
    const pnlColor = s.totalPnl >= 0 ? 'text-up' : 'text-down';
    const canManage = canCreateClient();
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
                        ${(c.tags || []).map(t => ALL_MANAGED_TAGS.includes(t)
                            // 受控标签：中性药丸样式（无 ×，在标签编辑器统一管理）
                            ? `<span title="受控标签（在标签编辑器中修改）" class="inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full bg-surface-strong text-body border border-hairline">${t}</span>`
                            // 自由标签：药丸蓝底白字 + × 删除
                            : `<span class="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-primary/10 text-primary">${t}<button onclick="removeClientTag('${t}')" class="hover:text-down transition-colors">×</button></span>`
                        ).join('')}
                        <button onclick="openTagEditorModal()" class="px-2 py-0.5 text-xs text-muted border border-dashed border-hairline rounded-full hover:text-primary hover:border-primary/40 transition-colors">+ 标签</button>
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
            <div class="ml-auto w-full sm:w-auto flex flex-col items-stretch sm:items-end gap-3">
                <!-- 第一行：总资产 + 总盈亏 卡片（总盈亏 = 浮动盈亏 + 已实现盈亏，累计口径） -->
                <!-- 两处金额统一走 formatCompactAmount：|值|≥10000 用「万」（1 位小数、去尾 0），
                     否则保持 ¥ + 千分位 + 2 位小数；负值保留负号（-¥1.2万）。
                     whitespace-nowrap 保证数值不换行、不溢出、两卡片宽度稳定不错位 -->
                <div class="flex items-center gap-5 justify-end flex-wrap">
                    <div class="text-right">
                        <div class="text-xs text-muted">总资产</div>
                        <div class="font-mono text-2xl font-semibold text-ink whitespace-nowrap">${formatCompactAmount(s.totalAssets)}</div>
                    </div>
                    <div class="text-right">
                        <div class="text-xs text-muted">总盈亏</div>
                        <div class="font-mono text-2xl font-semibold ${pnlColor} whitespace-nowrap">${formatCompactAmount(s.totalPnl, { sign: true })}</div>
                    </div>
                </div>
                <!-- 第二行：编辑客户 + 导出客户报告 按钮（右对齐） -->
                <div class="flex items-center gap-2 justify-end flex-wrap">
                    ${canManage ? `<button onclick="openClientEditModal()" class="btn-secondary flex items-center gap-1.5 px-4 py-2.5 text-sm font-semibold rounded-pill border border-hairline bg-surface-strong hover:bg-hairline transition-colors">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>
                        编辑客户
                    </button>` : ''}
                    <button onclick="exportClientReport()" class="btn-primary flex items-center gap-1.5 px-4 py-2.5 text-sm font-semibold rounded-pill">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
                        导出客户报告
                    </button>
                </div>
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

// ==================== 客户信息编辑 Modal（管理员 / 客服） ====================
const CLIENT_EDIT_OVERLAY = 'clientEditOverlay';

export function openClientEditModal() {
    const c = getCurrentClient();
    if (!c) { showToast('请先选择客户', 'warning'); return; }
    if (!canCreateClient()) { showToast('❌ 仅管理员或客服可修改客户信息', 'error'); return; }

    let overlay = document.getElementById(CLIENT_EDIT_OVERLAY);
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = CLIENT_EDIT_OVERLAY;
        overlay.className = 'fixed inset-0 z-[110] hidden';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `
        <div class="absolute inset-0 modal-backdrop" onclick="closeClientEditModal()"></div>
        <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md mx-4">
            <div class="bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up">
                <div class="px-8 pt-6 pb-5 flex items-center justify-between">
                    <div>
                        <h3 class="text-lg font-semibold text-ink">编辑客户信息</h3>
                        <p class="text-xs text-muted mt-1">修改将记录审计日志（操作人、前后值对比）</p>
                    </div>
                    <button onclick="closeClientEditModal()" class="w-8 h-8 rounded-full bg-surface-strong flex items-center justify-center text-muted hover:text-ink hover:bg-hairline transition-all">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                    </button>
                </div>
                <form id="clientEditForm" onsubmit="submitClientEdit(event)" class="px-8 pb-6 space-y-3">
                    <div class="text-xs text-muted pb-1 border-b border-hairline">
                        客户 <span class="font-semibold text-ink">${c.name}</span>
                        <span class="font-mono ml-2">${c.id}</span>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-body mb-1">客户姓名 <span class="text-negative">*</span></label>
                        <input id="cefName" type="text" value="${c.name}" maxlength="64" class="w-full px-3 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" placeholder="请输入客户姓名">
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block text-xs font-medium text-body mb-1">年龄</label>
                            <input id="cefAge" type="number" min="0" max="150" value="${c.age ?? ''}" class="w-full px-3 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" placeholder="可选">
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-body mb-1">风险等级</label>
                            <select id="cefRisk" class="w-full px-3 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10">
                                ${['保守型','稳健型','平衡型','积极型','激进型'].map(l => `<option value="${l}" ${c.riskLevel === l ? 'selected' : ''}>${l}</option>`).join('')}
                            </select>
                        </div>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-body mb-1">可用资金（元）<span class="text-negative">*</span></label>
                        <input id="cefCash" type="number" min="0" step="0.01" value="${c.availableCash}" class="w-full px-3 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" placeholder="请输入可用资金金额">
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-body mb-1">客户备注</label>
                        <textarea id="cefNote" rows="2" class="w-full px-3 py-2 bg-surface-strong border border-hairline rounded-xl text-sm text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 resize-none" placeholder="客户偏好、跟进计划…">${c.note || ''}</textarea>
                    </div>
                    <div class="flex gap-2 pt-2">
                        <button type="submit" id="cefSubmit" class="flex-1 px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">确认修改</button>
                        <button type="button" onclick="closeClientEditModal()" class="px-4 py-3 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors">取消</button>
                    </div>
                </form>
            </div>
        </div>
    `;
    overlay.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

export function closeClientEditModal() {
    const overlay = document.getElementById(CLIENT_EDIT_OVERLAY);
    if (!overlay) return;
    overlay.classList.add('hidden');
    overlay.innerHTML = '';
    document.body.style.overflow = '';
}

export async function submitClientEdit(event) {
    event.preventDefault?.();
    event.stopPropagation?.();
    const c = getCurrentClient();
    if (!c) return null;
    const name = (document.getElementById('cefName')?.value || '').trim();
    const ageRaw = document.getElementById('cefAge')?.value;
    const riskLevel = document.getElementById('cefRisk')?.value || null;
    const cashRaw = document.getElementById('cefCash')?.value;
    const note = document.getElementById('cefNote')?.value || '';
    const btn = document.getElementById('cefSubmit');

    if (!name) { showToast('请输入客户姓名', 'warning'); return null; }
    const age = ageRaw === '' || ageRaw == null ? null : parseInt(ageRaw, 10);
    if (age != null && (isNaN(age) || age < 0 || age > 150)) { showToast('年龄输入无效', 'warning'); return null; }
    const cash = parseFloat(cashRaw);
    if (isNaN(cash) || cash < 0) { showToast('可用资金必须为 ≥ 0 的数字', 'warning'); return null; }

    // 转换为后端 snake_case body
    const patch = {
        name: name,
        age: age,
        risk_level: riskLevel,
        available_cash: cash,
        note: note,
    };

    const original = btn?.innerHTML;
    try {
        if (btn) { btn.disabled = true; btn.innerHTML = '提交中...'; }
        const updated = await updateClientRemote(c.id, patch);
        // 强制刷新当前客户
        if (updated && updated.id === currentClientId) {
            setCurrentClient(updated.id);  // 保持当前选中
        }
        showToast('✅ 客户信息已更新（操作已审计）', 'success');
        closeClientEditModal();
        // 刷新持仓概览（含可用资金统计卡片）+ 客户资料卡 + 风险预警 + 图表
        await refreshClientDetail();
        // 刷新客户列表（显示客户名/资金）
        renderClientList();
        return updated;
    } catch (e) {
        showToast('❌ 修改失败：' + (e?.message || '请检查权限或稍后重试'), 'error');
        return null;
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = original || '确认修改'; }
    }
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

    // 预警类别判定：是否仅含恭喜（positive）、是否混合（含风险+恭喜）
    const hasRisk = alerts.some(a => a.level !== 'positive');
    const allPositive = alerts.length && !hasRisk;
    // 主色：仅恭喜用 positive 绿，含任何风险用 negative 红
    const mainCls = allPositive ? 'positive' : 'negative';

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

    const headerIcon = allPositive
        // 恭喜：彩带/礼物 SVG（绿色）
        ? `<svg class="w-4 h-4 text-${mainCls}" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z"/></svg>`
        : `<svg class="w-4 h-4 text-${mainCls}" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>`;
    const title = allPositive ? '客户喜报' : '风险预警';

    el.innerHTML = `<div class="premium-card p-5 border-${mainCls}/20">
        <div class="flex items-center gap-2 mb-3">
            ${headerIcon}
            <h3 class="text-sm font-semibold text-ink">${title}</h3>
            <span class="text-xs text-${mainCls} font-mono">${alerts.length} 项</span>
            ${canHandle ? `<button onclick="evaluateClientRisk()" class="ml-auto px-3 py-1 text-xs font-medium rounded-lg border border-hairline text-muted hover:text-primary hover:border-primary/40 transition-colors">重新评估</button>` : ''}
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            ${alerts.map(a => renderAlertCard(a, canHandle)).join('')}
        </div>
    </div>`;
}

function renderAlertCard(a, canHandle) {
    const status = ALERT_STATUS[a.status] || { label: a.status || '未知', cls: 'bg-surface-strong text-muted' };
    const levelDot = a.level === 'positive' ? 'bg-positive'
                   : a.level === 'high' ? 'bg-negative'
                   : 'bg-warning';
    // 卡片底色：positive恭喜用浅绿，其他风险用浅红
    const cardCls = a.level === 'positive'
        ? 'bg-positive/5 border-positive/10'
        : 'bg-negative/5 border-negative/10';
    return `<div class="flex items-start gap-2.5 p-3 rounded-xl ${cardCls}">
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

// 标签编辑器当前快照：受控标签选择 + 自由标签数组
// 打开弹窗时从当前客户 tags 初始化，保存时再回写
let freeTagSnapshot = [];

// ---- 标签编辑器弹窗（受控分类标签 + 自定义标签双轨编辑）----
export function openTagEditorModal() {
    if (!guardEdit()) return;
    const c = getCurrentClient();
    if (!c) return;
    const tags = c.tags || [];
    // 拆分：受控标签 → tagSelector 状态；自定义标签 → 本地数组
    initTagSelector(tags);
    freeTagSnapshot = tags.filter(t => !ALL_MANAGED_TAGS.includes(t));
    // 设置弹窗副标题：name (id)
    const titleEl = document.getElementById('tagEditorTitle');
    const subEl = document.getElementById('tagEditorSubtitle');
    if (titleEl) titleEl.textContent = '编辑客户标签';
    if (subEl) subEl.textContent = `${c.name || '—'}（${c.id || '—'}）`;
    renderTagSelector();
    renderFreeTagsList();
    document.getElementById('freeTagInput').value = '';
    const modal = document.getElementById('tagEditorModal');
    modal?.classList.remove('hidden');
}

export function closeTagEditorModal() {
    document.getElementById('tagEditorModal')?.classList.add('hidden');
    freeTagSnapshot = [];
}

function renderFreeTagsList() {
    const el = document.getElementById('freeTagsList');
    if (!el) return;
    if (!freeTagSnapshot.length) {
        el.innerHTML = '<div class="text-xs text-muted py-1">暂无自定义标签</div>';
        return;
    }
    el.innerHTML = freeTagSnapshot.map(t => {
        const safe = String(t).replace(/'/g, "\\'").replace(/"/g, '&quot;');
        return `<span class="inline-flex items-center gap-1.5 pl-3 pr-1.5 py-1 text-xs font-medium rounded-full bg-primary/10 text-primary border border-primary/20">
            <span>${escapeHtml(t)}</span>
            <button onclick="removeFreeTagInEditor('${safe}')"
                class="w-4 h-4 rounded-full flex items-center justify-center hover:bg-primary/20 transition-colors"
                title="移除标签「${escapeHtml(t)}」">
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
        </span>`;
    }).join('');
}

export function addFreeTag() {
    const input = document.getElementById('freeTagInput');
    const raw = input?.value.trim();
    if (!raw) return;
    if (ALL_MANAGED_TAGS.includes(raw)) {
        showToast('⚠️ 该标签属于受控分类，请在上方面板中选择', 'info');
        return;
    }
    if (freeTagSnapshot.includes(raw)) {
        showToast('⚠️ 该标签已存在', 'info');
        return;
    }
    freeTagSnapshot.push(raw);
    renderFreeTagsList();
    input.value = '';
    input.focus();
}

export function removeFreeTagInEditor(tag) {
    freeTagSnapshot = freeTagSnapshot.filter(t => t !== tag);
    renderFreeTagsList();
}

// ---- 保存编辑器：合并受控 + 自由，去重后提交 ----
export async function submitTagEditor() {
    if (!guardEdit()) return;
    const c = getCurrentClient();
    if (!c) return;
    const merged = Array.from(new Set([...getSelectedManagedTags(), ...freeTagSnapshot]));
    try {
        await updateClientRemote(c.id, { tags: merged });
        renderClientProfile();
        renderClientList();
        showToast('✅ 标签已保存', 'success');
        closeTagEditorModal();
    } catch (e) {
        showToast('❌ 标签保存失败：' + (e.message || '未知错误'), 'error');
    }
}

// 兼容旧函数：addClientTag 直接打开编辑器（保持原调用点行为）
export function addClientTag() {
    openTagEditorModal();
}

// 快捷删除：客户资料卡上自由标签的 × 按钮（受控标签已改为统一在编辑器管理，故此处直接删）
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

// --- 导出客户报告（持仓体检 / 导出客户报告模块）---
// 实现要点（按需求）：
//  1. 不写死股票 —— 直接把当前客户的 client_id 交给后端，由后端拉取该客户「真实持仓」；
//  2. 数据来源走公开 API（adapter='public'，云端可用；如需离线演示可改 'demo'）；
//  3. 后端返回自包含 HTML（内联 SVG，涨红跌绿），前端开新窗口写入并自动触发打印（可另存为 PDF）。
export async function exportClientReport() {
    const c = getCurrentClient();
    if (!c) {
        showToast('请先选择要导出报告的客户', 'error');
        return;
    }
    const token = getToken();
    if (!token) {
        showToast('登录状态已失效，请重新登录', 'error');
        return;
    }

    showToast('正在生成持仓体检报告（实时分析客户持仓，请稍候）…', 'info');
    try {
        const resp = await fetch('/api/reports/portfolio-health/html', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
            },
            body: JSON.stringify({
                client_id: c.id,          // 后端依此取真实持仓，绝不写死标的
                adapter: 'public',        // 公开 API；离线/演示可改 'demo'
                use_llm: false,
                title: `${c.name} 持仓体检报告`,
            }),
        });

        // 401/403：令牌失效，统一走过期登出流程
        if (resp.status === 401 || resp.status === 403) {
            try { localStorage.removeItem('stock_review_token'); } catch { /* ignore */ }
            window.dispatchEvent(new CustomEvent('auth:expired', {
                detail: { status: resp.status, message: '登录已失效' },
            }));
            showToast('登录已失效，请重新登录', 'error');
            return;
        }
        if (!resp.ok) {
            let msg = '报告生成失败';
            try { const j = await resp.json(); msg = j.detail || msg; } catch { /* ignore */ }
            showToast('❌ ' + msg, 'error');
            return;
        }

        const html = await resp.text();
        const win = window.open('', '_blank');
        if (!win) {
            showToast('⚠️ 浏览器拦截了弹出窗口，请允许本站弹出后重试', 'error');
            return;
        }
        win.document.open();
        win.document.write(html);
        win.document.close();
        win.focus();
        // 等待 DOM 渲染后弹出打印对话框（可另存为 PDF）；延迟兜底避免部分浏览器 onload 不触发
        const doPrint = () => { try { win.print(); } catch { /* ignore */ } };
        if (win.document.readyState === 'complete') {
            setTimeout(doPrint, 400);
        } else {
            win.onload = () => setTimeout(doPrint, 400);
            // 双保险：即使 onload 未触发，500ms 后也尝试一次
            setTimeout(doPrint, 1500);
        }
        showToast('✅ 报告已生成，使用打印对话框可另存为 PDF', 'success');
    } catch (e) {
        showToast('❌ 报告生成失败：' + (e && e.message ? e.message : '网络错误'), 'error');
    }
}
