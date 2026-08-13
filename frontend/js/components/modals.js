// ============================================================
// 持仓弹窗组件：新增/编辑/加仓/减仓/删除
// ============================================================
import { getUserPositions, saveUserPositions } from '../services/clientService.js';
import { formatCurrency, formatNumber, getPnLColor } from '../core/formatters.js';
import { SECTORS } from '../core/config.js';
import { showToast } from '../core/ui.js';
import { refreshAll } from './overview.js';

// --- 打开持仓弹窗（新增或编辑） ---
export function openPositionModal(code = null) {
    const isEdit = code !== null;
    let position = null;
    if (isEdit) {
        position = getUserPositions().find(p => p.code === code);
        if (!position) { showToast('❌ 未找到该持仓', 'error'); return; }
    }

    const modalHtml = `
        <div id="positionModal" class="fixed inset-0 z-[100]">
            <div class="absolute inset-0 modal-backdrop" onclick="closePositionModal()"></div>
            <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg mx-4">
                <div class="bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up max-h-[90vh] overflow-y-auto">
                    <div class="px-8 pt-7 pb-5 relative border-b border-hairline">
                        <button onclick="closePositionModal()" class="absolute top-4 right-4 w-8 h-8 rounded-full bg-surface-strong flex items-center justify-center text-muted hover:text-ink hover:bg-hairline transition-all">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                        </button>
                        <h3 class="text-lg font-semibold text-ink">${isEdit ? '编辑持仓' : '添加持仓'}</h3>
                        <p class="text-sm text-muted mt-1">${isEdit ? '修改持仓信息' : '录入您的股票持仓信息'}</p>
                    </div>

                    <div class="px-8 py-6 space-y-4">
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">股票名称 <span class="text-negative">*</span></label>
                                <input type="text" id="posName" value="${isEdit ? position.name : ''}" placeholder="如：贵州茅台" maxlength="10"
                                    class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">股票代码 <span class="text-negative">*</span></label>
                                <input type="text" id="posCode" value="${isEdit ? position.code : ''}" placeholder="如：600519" maxlength="6" ${isEdit ? 'readonly' : ''}
                                    class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm font-mono ${isEdit ? 'opacity-60 cursor-not-allowed' : ''}">
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">持仓数量 <span class="text-negative">*</span></label>
                                <input type="number" id="posQuantity" value="${isEdit ? position.quantity : ''}" placeholder="如：100" min="1" step="1"
                                    class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm font-mono">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">板块分类</label>
                                <select id="posSector"
                                    class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm">
                                    ${SECTORS.map(s => `<option value="${s}" ${isEdit && position.sector === s ? 'selected' : ''}>${s}</option>`).join('')}
                                </select>
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">成本价 <span class="text-negative">*</span></label>
                                <div class="relative">
                                    <span class="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-sm">¥</span>
                                    <input type="number" id="posCostPrice" value="${isEdit ? position.costPrice : ''}" placeholder="0.00" min="0" step="0.01"
                                        class="w-full pl-7 pr-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm font-mono">
                                </div>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">现价 <span class="text-negative">*</span></label>
                                <div class="relative">
                                    <span class="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-sm">¥</span>
                                    <input type="number" id="posPrice" value="${isEdit ? position.price : ''}" placeholder="0.00" min="0" step="0.01"
                                        class="w-full pl-7 pr-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm font-mono">
                                </div>
                            </div>
                        </div>

                        <div id="pnlPreview" class="bg-surface-soft rounded-xl p-4 hidden">
                            <div class="flex items-center justify-between text-sm">
                                <span class="text-muted">预计盈亏</span>
                                <span id="pnlPreviewValue" class="font-mono font-semibold">--</span>
                            </div>
                            <div class="flex items-center justify-between text-sm mt-1">
                                <span class="text-muted">预计收益率</span>
                                <span id="pnlPreviewPct" class="font-mono font-semibold">--</span>
                            </div>
                        </div>
                    </div>

                    <div class="px-8 pb-7 flex gap-3">
                        <button onclick="closePositionModal()" class="flex-1 px-4 py-3 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors">
                            取消
                        </button>
                        <button onclick="savePosition(${isEdit ? `'${code}'` : 'null'})" class="flex-1 px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors shadow-sm shadow-primary/25">
                            ${isEdit ? '保存修改' : '添加持仓'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    const existing = document.getElementById('positionModal');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', modalHtml);
    document.body.style.overflow = 'hidden';

    ['posQuantity', 'posCostPrice', 'posPrice'].forEach(id => {
        document.getElementById(id).addEventListener('input', updatePnLPreview);
    });
}

// --- 实时盈亏预览 ---
export function updatePnLPreview() {
    const qty = parseFloat(document.getElementById('posQuantity').value);
    const cost = parseFloat(document.getElementById('posCostPrice').value);
    const price = parseFloat(document.getElementById('posPrice').value);
    const preview = document.getElementById('pnlPreview');

    if (qty > 0 && cost > 0 && price > 0) {
        const pnl = (price - cost) * qty;
        const pnlPct = ((price - cost) / cost) * 100;
        preview.classList.remove('hidden');
        const pnlEl = document.getElementById('pnlPreviewValue');
        const pctEl = document.getElementById('pnlPreviewPct');
        pnlEl.textContent = `${pnl >= 0 ? '+' : ''}¥${formatCurrency(pnl)}`;
        pnlEl.className = `font-mono font-semibold ${getPnLColor(pnl)}`;
        pctEl.textContent = `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
        pctEl.className = `font-mono font-semibold ${getPnLColor(pnlPct)}`;
    } else {
        preview.classList.add('hidden');
    }
}

export function closePositionModal() {
    const modal = document.getElementById('positionModal');
    if (modal) modal.remove();
    document.body.style.overflow = '';
}

export function closeAdjustModal() {
    const modal = document.getElementById('adjustModal');
    if (modal) modal.remove();
    document.body.style.overflow = '';
}

// --- 打开加仓/减仓弹窗 ---
export function openAdjustModal(code, action) {
    const position = getUserPositions().find(p => p.code === code);
    if (!position) { showToast('❌ 未找到该持仓', 'error'); return; }

    const isAdd = action === 'add';
    const title = isAdd ? '加仓' : '减仓';
    const actionColor = isAdd ? 'text-up' : 'text-down';
    const actionBg = isAdd ? 'bg-red-50' : 'bg-green-50';

    const currentMV = position.price * position.quantity;
    const currentCost = position.costPrice * position.quantity;

    const modalHtml = `
        <div id="adjustModal" class="fixed inset-0 z-[100]">
            <div class="absolute inset-0 modal-backdrop" onclick="closeAdjustModal()"></div>
            <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md mx-4">
                <div class="bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up">
                    <div class="px-8 pt-7 pb-5 relative border-b border-hairline">
                        <button onclick="closeAdjustModal()" class="absolute top-4 right-4 w-8 h-8 rounded-full bg-surface-strong flex items-center justify-center text-muted hover:text-ink hover:bg-hairline transition-all">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                        </button>
                        <div class="flex items-center gap-2">
                            <span class="${actionBg} ${actionColor} w-8 h-8 rounded-lg flex items-center justify-center">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    ${isAdd
                                        ? '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4"/>'
                                        : '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M20 12H4"/>'
                                    }
                                </svg>
                            </span>
                            <h3 class="text-lg font-semibold text-ink">${title}</h3>
                        </div>
                        <p class="text-sm text-muted mt-1.5">${position.name} (${position.code})</p>
                    </div>

                    <div class="px-8 py-4 bg-surface-soft/50 border-b border-hairline">
                        <div class="grid grid-cols-3 gap-3 text-center">
                            <div>
                                <div class="text-xs text-muted mb-0.5">当前持仓</div>
                                <div class="font-mono text-sm font-semibold text-ink">${position.quantity.toLocaleString()}股</div>
                            </div>
                            <div>
                                <div class="text-xs text-muted mb-0.5">成本价</div>
                                <div class="font-mono text-sm font-semibold text-ink">¥${formatNumber(position.costPrice)}</div>
                            </div>
                            <div>
                                <div class="text-xs text-muted mb-0.5">现价</div>
                                <div class="font-mono text-sm font-semibold text-ink">¥${formatNumber(position.price)}</div>
                            </div>
                        </div>
                    </div>

                    <div class="px-8 py-6 space-y-4">
                        <div>
                            <label class="block text-sm font-medium text-ink mb-2">${isAdd ? '加仓数量' : '减仓数量'} <span class="text-negative">*</span></label>
                            <div class="relative">
                                <input type="number" id="adjustQty" placeholder="输入数量" min="1" ${!isAdd ? `max="${position.quantity}"` : ''} step="1"
                                    class="w-full px-4 py-2.5 pr-12 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm font-mono">
                                <span class="absolute right-3 top-1/2 -translate-y-1/2 text-muted text-sm">股</span>
                            </div>
                            ${!isAdd ? `<p class="text-xs text-muted mt-1">最多可减仓 ${position.quantity.toLocaleString()} 股</p>` : ''}
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-ink mb-2">${isAdd ? '加仓价格' : '减仓价格'} <span class="text-negative">*</span></label>
                            <div class="relative">
                                <span class="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-sm">¥</span>
                                <input type="number" id="adjustPrice" value="${position.price}" placeholder="0.00" min="0" step="0.01"
                                    class="w-full pl-7 pr-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm font-mono">
                            </div>
                            <p class="text-xs text-muted mt-1">默认为当前现价，可修改</p>
                        </div>

                        <div id="adjustPreview" class="${actionBg} rounded-xl p-4 hidden">
                            <div class="text-xs font-medium ${actionColor} mb-2 flex items-center gap-1">
                                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                                操作预览
                            </div>
                            <div id="adjustPreviewContent" class="space-y-1.5 text-sm"></div>
                        </div>
                    </div>

                    <div class="px-8 pb-7 flex gap-3">
                        <button onclick="closeAdjustModal()" class="flex-1 px-4 py-3 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors">
                            取消
                        </button>
                        <button onclick="executeAdjust('${code}', '${action}')" class="flex-1 px-4 py-3 text-sm font-semibold text-white ${isAdd ? 'bg-up hover:bg-red-600' : 'bg-down hover:bg-green-600'} rounded-xl transition-colors shadow-sm">
                            确认${title}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    const existing = document.getElementById('adjustModal');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', modalHtml);
    document.body.style.overflow = 'hidden';

    ['adjustQty', 'adjustPrice'].forEach(id => {
        document.getElementById(id).addEventListener('input', () => updateAdjustPreview(position, action));
    });
}

// --- 加仓/减仓实时预览 ---
export function updateAdjustPreview(position, action) {
    const qty = parseInt(document.getElementById('adjustQty').value);
    const price = parseFloat(document.getElementById('adjustPrice').value);
    const preview = document.getElementById('adjustPreview');
    const content = document.getElementById('adjustPreviewContent');

    if (!qty || qty < 1 || !price || price <= 0) {
        preview.classList.add('hidden');
        return;
    }

    const isAdd = action === 'add';
    preview.classList.remove('hidden');

    if (isAdd) {
        const oldTotalCost = position.costPrice * position.quantity;
        const addCost = price * qty;
        const newQty = position.quantity + qty;
        const newCostPrice = (oldTotalCost + addCost) / newQty;
        const costDiff = newCostPrice - position.costPrice;

        content.innerHTML = `
            <div class="flex justify-between"><span class="text-muted">加仓金额</span><span class="font-mono font-semibold text-ink">¥${formatCurrency(addCost)}</span></div>
            <div class="flex justify-between"><span class="text-muted">加仓后总量</span><span class="font-mono font-semibold text-ink">${newQty.toLocaleString()} 股</span></div>
            <div class="flex justify-between"><span class="text-muted">新成本价</span><span class="font-mono font-semibold text-ink">¥${formatNumber(newCostPrice)}</span></div>
            <div class="flex justify-between"><span class="text-muted">成本变动</span><span class="font-mono font-semibold ${costDiff >= 0 ? 'text-up' : 'text-down'}">${costDiff >= 0 ? '+' : ''}¥${formatNumber(Math.abs(costDiff))}</span></div>
        `;
    } else {
        if (qty > position.quantity) {
            content.innerHTML = `<div class="text-negative text-xs">⚠ 减仓数量不能超过当前持仓 ${position.quantity.toLocaleString()} 股</div>`;
            return;
        }
        const reduceMV = price * qty;
        const remainingQty = position.quantity - qty;
        const isAllClear = remainingQty === 0;
        const realizedPnL = (price - position.costPrice) * qty;
        const realizedPnLPct = position.costPrice > 0 ? ((price - position.costPrice) / position.costPrice) * 100 : 0;

        content.innerHTML = `
            <div class="flex justify-between"><span class="text-muted">减仓金额</span><span class="font-mono font-semibold text-ink">¥${formatCurrency(reduceMV)}</span></div>
            <div class="flex justify-between"><span class="text-muted">剩余数量</span><span class="font-mono font-semibold text-ink">${remainingQty.toLocaleString()} 股</span></div>
            <div class="flex justify-between"><span class="text-muted">已实现盈亏</span><span class="font-mono font-semibold ${getPnLColor(realizedPnL)}">${realizedPnL >= 0 ? '+' : ''}¥${formatCurrency(realizedPnL)}</span></div>
            <div class="flex justify-between"><span class="text-muted">收益率</span><span class="font-mono font-semibold ${getPnLColor(realizedPnLPct)}">${realizedPnLPct >= 0 ? '+' : ''}${realizedPnLPct.toFixed(2)}%</span></div>
            ${isAllClear ? '<div class="text-warning text-xs pt-1">⚠ 该操作将清仓此股票</div>' : ''}
        `;
    }
}

// --- 执行加仓/减仓 ---
export function executeAdjust(code, action) {
    const qty = parseInt(document.getElementById('adjustQty').value);
    const price = parseFloat(document.getElementById('adjustPrice').value);

    if (!qty || qty < 1) { showToast('❌ 请输入有效的数量', 'error'); return; }
    if (!price || price <= 0) { showToast('❌ 请输入有效的价格', 'error'); return; }

    let positions = getUserPositions();
    const idx = positions.findIndex(p => p.code === code);
    if (idx === -1) { showToast('❌ 未找到该持仓', 'error'); return; }

    const position = positions[idx];
    const isAdd = action === 'add';

    if (!isAdd && qty > position.quantity) {
        showToast(`❌ 减仓数量不能超过持仓 ${position.quantity} 股`, 'error');
        return;
    }

    if (isAdd) {
        const oldTotalCost = position.costPrice * position.quantity;
        const addCost = price * qty;
        const newQty = position.quantity + qty;
        const newCostPrice = (oldTotalCost + addCost) / newQty;

        positions[idx].quantity = newQty;
        positions[idx].costPrice = parseFloat(newCostPrice.toFixed(4));

        showToast(`✅ 已加仓 ${position.name} ${qty}股 @ ¥${formatNumber(price)}`, 'success');
    } else {
        const remainingQty = position.quantity - qty;

        if (remainingQty <= 0) {
            positions.splice(idx, 1);
            showToast(`✅ 已清仓 ${position.name}，卖出 ${qty}股 @ ¥${formatNumber(price)}`, 'success');
        } else {
            positions[idx].quantity = remainingQty;
            positions[idx].price = price;

            const realizedPnL = (price - position.costPrice) * qty;
            showToast(`✅ 已减仓 ${position.name} ${qty}股，${realizedPnL >= 0 ? '盈利' : '亏损'} ¥${formatCurrency(Math.abs(realizedPnL))}`, 'success');
        }
    }

    saveUserPositions(positions);
    closeAdjustModal();
    refreshAll();
}

// --- 保存持仓（新增或编辑） ---
export function savePosition(originalCode = null) {
    const name = document.getElementById('posName').value.trim();
    const code = document.getElementById('posCode').value.trim();
    const quantity = parseInt(document.getElementById('posQuantity').value);
    const costPrice = parseFloat(document.getElementById('posCostPrice').value);
    const price = parseFloat(document.getElementById('posPrice').value);
    const sector = document.getElementById('posSector').value;

    if (!name) { showToast('❌ 请输入股票名称', 'error'); return; }
    if (!code || code.length < 5) { showToast('❌ 请输入正确的股票代码（至少5位）', 'error'); return; }
    if (!quantity || quantity < 1) { showToast('❌ 请输入有效的持仓数量', 'error'); return; }
    if (!costPrice || costPrice <= 0) { showToast('❌ 请输入有效的成本价', 'error'); return; }
    if (!price || price <= 0) { showToast('❌ 请输入有效的现价', 'error'); return; }

    let positions = getUserPositions();

    if (originalCode) {
        const idx = positions.findIndex(p => p.code === originalCode);
        if (idx === -1) { showToast('❌ 未找到该持仓', 'error'); return; }
        positions[idx] = { name, code, quantity, costPrice, price, sector };
    } else {
        if (positions.find(p => p.code === code)) {
            showToast(`❌ 股票代码 ${code} 已存在`, 'error');
            return;
        }
        positions.push({ name, code, quantity, costPrice, price, sector });
    }

    saveUserPositions(positions);
    closePositionModal();
    refreshAll();
    showToast(originalCode ? '✅ 持仓已更新' : '✅ 持仓已添加', 'success');
}

// --- 删除持仓 ---
export function deletePosition(code) {
    const position = getUserPositions().find(p => p.code === code);
    if (!position) return;

    if (confirm(`确定删除「${position.name}(${position.code})」的持仓吗？`)) {
        let positions = getUserPositions().filter(p => p.code !== code);
        saveUserPositions(positions);
        refreshAll();
        showToast('✅ 持仓已删除', 'success');
    }
}
