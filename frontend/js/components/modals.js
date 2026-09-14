// ============================================================
// 持仓弹窗组件：新增/编辑/加仓/减仓/删除
// ============================================================
import { getUserPositions, saveUserPositions, getCurrentClient, loadClients, executeAdjustApi, recognizeOcrImage, fetchClientPortfolio, fetchClientPnlHistory, searchStocksApi, syncClientState, revokePositionApi, createAdjustRecord, updateClientRemote } from '../services/clientService.js';
// updateClientPositions 定义在 authService.js（clientService 仅内部 import，未 re-export）
import { updateClientPositions } from '../services/authService.js';
import { formatCurrency, formatNumber, getPnLColor } from '../core/formatters.js';
import { SECTORS } from '../core/config.js';
import { showToast, hideToast } from '../core/ui.js';
import { refreshAll, refreshRealtime } from './overview.js';
import { renderClientProfile } from './workbench.js';
import { refreshClientSummaries } from './workbench.js';
import { canEditClient } from '../permissions/access.js';
import { fetchTradingFees } from './feeSettings.js';
import { getPrice, setPrice } from '../services/priceService.js';

// 缓存手续费配置
let cachedFees = null;

async function getCachedFees() {
    if (!cachedFees) {
        cachedFees = await fetchTradingFees();
    }
    return cachedFees;
}

/** 计算卖出手续费（与后端保持一致） */
function calcSellFee(amount, fees) {
    if (!fees) return { commission: 0, stampTax: 0, transferFee: 0, total: 0, netProceeds: amount };
    const commission = Math.max(amount * (fees.commission_rate || 0), fees.min_commission || 0);
    const stampTax = amount * (fees.stamp_tax_rate || 0);
    const transferFee = amount * (fees.transfer_fee_rate || 0);
    const total = commission + stampTax + transferFee;
    return {
        commission: Math.round(commission * 100) / 100,
        stampTax: Math.round(stampTax * 100) / 100,
        transferFee: Math.round(transferFee * 100) / 100,
        total: Math.round(total * 100) / 100,
        netProceeds: Math.round((amount - total) * 100) / 100,
    };
}

/** 计算买入手续费 */
function calcBuyFee(amount, fees) {
    if (!fees) return { commission: 0, transferFee: 0, total: 0, netCost: amount };
    const commission = Math.max(amount * (fees.commission_rate || 0), fees.min_commission || 0);
    const transferFee = amount * (fees.transfer_fee_rate || 0);
    const total = commission + transferFee;
    return {
        commission: Math.round(commission * 100) / 100,
        transferFee: Math.round(transferFee * 100) / 100,
        total: Math.round(total * 100) / 100,
        netCost: Math.round((amount + total) * 100) / 100,
    };
}

/** 计算单笔手续费（支持 rate / fixed 双模式；未指定时按全局配置） */
function calcFeeByMode(amount, feeMode, feeValue) {
    if (feeMode === 'rate') {
        return Math.round(amount * feeValue * 100) / 100;
    }
    if (feeMode === 'fixed') {
        return Math.round(feeValue * 100) / 100;
    }
    return null; // 使用全局配置
}

/** 校验手续费输入：返回 {valid, mode, value, error} */
function validateFeeInput(feeMode, rawValue) {
    if (feeMode === 'default') return { valid: true, mode: null, value: null };
    const value = parseFloat(rawValue);
    if (isNaN(value)) {
        return { valid: false, error: '请输入手续费数值' };
    }
    if (feeMode === 'rate') {
        // 费率范围：(0, 0.01]，即最高 1%（对应万分之一 ~ 百分之一）
        if (value <= 0 || value > 0.01) {
            return { valid: false, error: '费率需在 0 ~ 1% 之间（如 0.00025 表示万2.5）' };
        }
        return { valid: true, mode: 'rate', value };
    }
    if (feeMode === 'fixed') {
        // 固定金额范围：(0, 100000] 元/笔
        if (value <= 0 || value > 100000) {
            return { valid: false, error: '固定手续费需在 0 ~ 100000 元之间' };
        }
        return { valid: true, mode: 'fixed', value };
    }
    return { valid: true, mode: null, value: null };
}

// --- 股票信息自动填充（新建持仓）---
let stockSearchTimer = null;
let stockSearchSeq = 0; // 递增序号：丢弃过期响应，避免竞态覆盖

function hideStockDropdown() {
    document.getElementById('stockSearchDropdown')?.remove();
}

/** 防抖搜索：输入停顿 300ms 后调搜索接口，渲染候选下拉 */
function scheduleStockSearch(keyword, anchorId) {
    clearTimeout(stockSearchTimer);
    if (!keyword || keyword.length < 2) { hideStockDropdown(); return; }
    stockSearchTimer = setTimeout(async () => {
        const seq = ++stockSearchSeq;
        // 加载提示（持续显示，结果到达后被成功提示覆盖或显式关闭）
        showToast('正在加载数据，请稍候...', 'info', 0);
        try {
            const { results } = await searchStocksApi(keyword, 8);
            if (seq !== stockSearchSeq) return; // 已有更新的输入，丢弃本次结果

            // 名称手动输入完整且唯一精确匹配 → 直接自动填充（无需点下拉）
            const exactMatches = (results || []).filter(s => s.name === keyword);
            if (exactMatches.length === 1) {
                applyStockInfo(exactMatches[0]);
                showToast(`✅ 已自动填充 ${exactMatches[0].name}${exactMatches[0].industry ? '（' + exactMatches[0].industry + '）' : ''}`, 'success');
                return;
            }

            hideToast();
            renderStockDropdown(results || [], anchorId);
        } catch (e) {
            hideToast();
            // 静默失败，不打断用户输入
            console.warn('股票搜索失败:', e);
        }
    }, 300);
}

/** 渲染候选下拉（挂载在输入框父容器下，宽度自适应内容且不小于 300px） */
function renderStockDropdown(items, anchorId) {
    hideStockDropdown();
    if (!items.length) return;
    const anchor = document.getElementById(anchorId);
    if (!anchor) return;

    const dropdown = document.createElement('div');
    dropdown.id = 'stockSearchDropdown';
    // w-max 按内容自适应宽度；min-w 保证 ≥300px 不截断；挂载后按可用空间收敛
    dropdown.className = 'absolute top-full mt-1 z-50 bg-canvas border border-hairline rounded-xl shadow-xl overflow-hidden max-h-64 overflow-y-auto w-max min-w-[300px]';
    dropdown.innerHTML = items.map((s, i) => {
        const pct = s.change_pct;
        const pctColor = pct == null ? 'text-muted' : (pct > 0 ? 'text-up' : pct < 0 ? 'text-down' : 'text-muted');
        const pctText = pct == null ? '' : `${pct > 0 ? '+' : ''}${Number(pct).toFixed(2)}%`;
        return `
        <div class="stock-search-item px-4 py-2.5 flex items-center justify-between gap-3 hover:bg-surface-soft cursor-pointer transition-colors whitespace-nowrap" data-idx="${i}">
            <div class="flex items-center gap-2">
                <span class="text-sm text-ink">${s.name}</span>
                <span class="text-xs text-muted font-mono">${s.code}</span>
            </div>
            <div class="flex items-center gap-2">
                ${s.industry ? `<span class="text-xs px-1.5 py-0.5 rounded bg-surface-soft text-muted">${s.industry}</span>` : ''}
                <span class="text-sm font-mono text-body">${s.price != null ? '¥' + formatNumber(s.price) : '--'}</span>
                <span class="text-xs font-mono ${pctColor} w-14 text-right">${pctText}</span>
            </div>
        </div>`;
    }).join('');

    const cell = anchor.parentElement;       // 网格单元（relative）
    cell.appendChild(dropdown);

    // 宽度收敛：内容自然宽度超出可用空间时，向左展开对齐表单区左缘并限宽，
    // 确保下拉始终完整显示在弹窗面板内（面板横向 overflow-hidden 会裁剪溢出）
    const grid = cell.closest('div.grid');
    if (grid) {
        const cellOffset = cell.offsetLeft - grid.offsetLeft; // 单元格在网格内的左偏移
        const natural = dropdown.offsetWidth;                 // 内容自然宽度（≥300）
        const availRight = grid.offsetWidth - cellOffset;     // 单元格左缘到网格右缘
        if (natural > availRight) {
            dropdown.style.left = `${-cellOffset}px`;
            dropdown.style.width = `${Math.min(Math.max(300, natural), grid.offsetWidth)}px`;
        } else {
            dropdown.style.width = `${Math.max(300, natural)}px`;
        }
    }

    dropdown.querySelectorAll('.stock-search-item').forEach(el => {
        // mousedown 即选中：preventDefault 阻止输入框失焦，
        // 避免 blur → 200ms 后下拉被移除导致 click 落空（点击无响应的根因）
        el.addEventListener('mousedown', (ev) => {
            ev.preventDefault();
            applyStockInfo(items[parseInt(el.dataset.idx)]);
        });
    });
}

/** 将选中的股票信息填充到表单各字段（名称/代码/现价/成本价/板块） */
function applyStockInfo(stock) {
    if (!stock) return;
    hideStockDropdown();
    stockSearchSeq++; // 使未完成的搜索响应失效，避免下拉被重新渲染

    document.getElementById('posName').value = stock.name || '';
    document.getElementById('posCode').value = stock.code || '';

    // 现价：更新显示 + 写入价格缓存（updatePnLPreview 的 getPrice 立即可用）
    if (stock.price && stock.price > 0) {
        setPrice(stock.code, stock.price);
        const priceEl = document.getElementById('posPrice');
        if (priceEl) priceEl.textContent = formatNumber(stock.price);
        // 成本价为空时默认填现价（用户可修改）
        const costInput = document.getElementById('posCostPrice');
        if (costInput && !costInput.value) costInput.value = stock.price;
    }

    // 行业匹配板块下拉：有效板块直接选中；匹配不上兜底"其他"（板块必填不落空）
    const sectorValue = (stock.sector && SECTORS.includes(stock.sector)) ? stock.sector : '其他';
    document.getElementById('posSector').value = sectorValue;

    updatePnLPreview();
}

/** 代码输入满 6 位：精确查询并自动填充全部信息 */
async function autoFillByCode(code) {
    const seq = ++stockSearchSeq;
    // 加载提示（持续显示，结果到达后被成功提示覆盖或显式关闭）
    showToast('正在加载数据，请稍候...', 'info', 0);
    try {
        const { results } = await searchStocksApi(code, 5);
        if (seq !== stockSearchSeq) return;
        const exact = (results || []).find(s => s.code === code);
        if (exact) {
            applyStockInfo(exact);
            showToast(`✅ 已自动填充 ${exact.name}${exact.industry ? '（' + exact.industry + '）' : ''}`, 'success');
        } else {
            hideToast();
        }
    } catch (e) {
        hideToast();
        console.warn('代码自动填充失败:', e);
    }
}

/** 绑定自动填充事件（仅新建模式；编辑模式信息已完整且代码只读） */
function bindStockAutoFill() {
    const nameInput = document.getElementById('posName');
    const codeInput = document.getElementById('posCode');

    // 名称输入：模糊搜索下拉候选
    nameInput.addEventListener('input', (e) => {
        scheduleStockSearch(e.target.value.trim(), 'posName');
    });
    // blur 延迟隐藏：留出时间让候选项的 click 先命中
    nameInput.addEventListener('blur', () => setTimeout(hideStockDropdown, 200));
    codeInput.addEventListener('blur', () => setTimeout(hideStockDropdown, 200));

    // 代码输入：满 6 位数字自动精确填充；其余情况前缀搜索下拉
    codeInput.addEventListener('input', (e) => {
        const v = e.target.value.trim();
        if (/^\d{6}$/.test(v)) {
            hideStockDropdown();
            autoFillByCode(v);
        } else {
            scheduleStockSearch(v, 'posCode');
        }
    });
}

function guardEdit() {
    const c = getCurrentClient();
    if (!c || !canEditClient(c)) {
        showToast('❌ 无权限执行此操作', 'error');
        return false;
    }
    return true;
}

// 持仓回写后端；失败时回滚到后端数据并提示
async function persistPositions(positions) {
    try {
        await saveUserPositions(positions);
        return true;
    } catch (e) {
        showToast('❌ 持仓保存失败：' + (e.message || '未知错误'), 'error');
        await loadClients();
        refreshAll();
        return false;
    }
}

// --- 打开持仓弹窗（新增或编辑） ---
export function openPositionModal(code = null) {
    if (!guardEdit()) return;
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
                                <label class="block text-sm font-medium text-ink mb-2">现价（实时）</label>
                                <div class="relative">
                                    <span class="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-sm">¥</span>
                                    <div id="posPrice" class="w-full pl-7 pr-4 py-2.5 bg-surface-soft border border-hairline rounded-xl text-body font-mono text-sm flex items-center">
                                        ${isEdit ? formatNumber(getPrice(position.code, position.costPrice)) : '--'}
                                    </div>
                                </div>
                                <p class="text-xs text-muted mt-1">由实时行情提供，不可编辑</p>
                            </div>
                        </div>

                        ${!isEdit ? `
                        <div>
                            <label class="block text-sm font-medium text-ink mb-2">手续费设置</label>
                            <div class="grid grid-cols-2 gap-3">
                                <select id="posFeeMode"
                                    class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm">
                                    <option value="default">默认费率</option>
                                    <option value="rate">按费率</option>
                                    <option value="fixed">固定金额</option>
                                </select>
                                <div class="relative">
                                    <input type="number" id="posFeeValue" placeholder="0.00025" min="0" step="0.00001" disabled
                                        class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm font-mono disabled:opacity-50 disabled:cursor-not-allowed">
                                    <span id="posFeeUnit" class="absolute right-3 top-1/2 -translate-y-1/2 text-muted text-sm hidden">元</span>
                                </div>
                            </div>
                            <p id="posFeeHint" class="text-xs text-muted mt-1">默认按系统费率配置计算，手续费将计入持仓成本</p>
                        </div>

                        <div class="flex items-center justify-between gap-3 px-4 py-3 bg-surface-soft rounded-xl">
                            <div class="min-w-0">
                                <div class="text-sm font-medium text-ink">新持仓来自可用资金</div>
                                <p id="posFromCashHint" class="text-xs text-muted mt-0.5">开启：买入从可用资金扣款，总资产不变（现金转持仓）</p>
                            </div>
                            <label class="relative inline-flex items-center cursor-pointer shrink-0">
                                <input type="checkbox" id="posFromCash" checked class="sr-only peer">
                                <div class="relative w-10 h-6 bg-hairline rounded-full peer-checked:bg-primary transition-colors after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:w-5 after:h-5 after:bg-white after:rounded-full after:shadow after:transition-transform peer-checked:after:translate-x-4"></div>
                            </label>
                        </div>` : ''}

                        <div id="pnlPreview" class="bg-surface-soft rounded-xl p-4 hidden">
                            <div class="flex items-center justify-between text-sm">
                                <span class="text-muted">预计盈亏</span>
                                <span id="pnlPreviewValue" class="font-mono font-semibold">--</span>
                            </div>
                            <div class="flex items-center justify-between text-sm mt-1">
                                <span class="text-muted">预计收益率</span>
                                <span id="pnlPreviewPct" class="font-mono font-semibold">--</span>
                            </div>
                            <div id="pnlPreviewFee" class="hidden mt-1 pt-1 border-t border-hairline"></div>
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

    ['posQuantity', 'posCostPrice'].forEach(id => {
        document.getElementById(id).addEventListener('input', updatePnLPreview);
    });

    // 手续费模式切换（仅新建时存在）：启用/禁用数值输入 + 更新提示
    const feeModeSel = document.getElementById('posFeeMode');
    if (feeModeSel) {
        document.getElementById('posFeeValue').addEventListener('input', updatePnLPreview);
        feeModeSel.addEventListener('change', (e) => {
            const mode = e.target.value;
            const valueInput = document.getElementById('posFeeValue');
            const unitEl = document.getElementById('posFeeUnit');
            const hintEl = document.getElementById('posFeeHint');
            if (mode === 'default') {
                valueInput.value = '';
                valueInput.disabled = true;
                unitEl.classList.add('hidden');
                hintEl.textContent = '默认按系统费率配置计算，手续费将计入持仓成本';
            } else {
                valueInput.disabled = false;
                if (mode === 'rate') {
                    valueInput.placeholder = '0.00025';
                    valueInput.step = '0.00001';
                    unitEl.classList.add('hidden');
                    hintEl.textContent = '费率范围 0 ~ 1%，如 0.00025 表示万2.5';
                } else {
                    valueInput.placeholder = '5.00';
                    valueInput.step = '0.01';
                    unitEl.classList.remove('hidden');
                    hintEl.textContent = '固定金额范围 0 ~ 100000 元/笔';
                }
            }
            hintEl.className = 'text-xs text-muted mt-1';
            updatePnLPreview();
        });
    }

    // 股票信息自动填充（仅新建模式）：名称模糊搜索下拉 + 代码精确自动填充
    if (!isEdit) {
        bindStockAutoFill();

        // 资金来源切换：更新提示文案（开启扣现金/关闭外部转入）
        const fromCashToggle = document.getElementById('posFromCash');
        if (fromCashToggle) {
            fromCashToggle.addEventListener('change', (e) => {
                const hint = document.getElementById('posFromCashHint');
                if (hint) {
                    hint.textContent = e.target.checked
                        ? '开启：买入从可用资金扣款，总资产不变（现金转持仓）'
                        : '关闭：外部转入持仓，不扣可用资金，总资产随之增加';
                }
            });
        }
    }
}

// --- 实时盈亏预览（新建时含手续费双模式计算，手续费计入成本）---
export async function updatePnLPreview() {
    const qty = parseFloat(document.getElementById('posQuantity').value);
    const cost = parseFloat(document.getElementById('posCostPrice').value);
    // 现价取实时行情（降级用成本价），不再从输入框读取
    const codeInput = document.getElementById('posCode');
    const price = codeInput ? getPrice(codeInput.value.trim(), cost > 0 ? cost : null) : 0;
    const preview = document.getElementById('pnlPreview');

    if (!(qty > 0 && cost > 0 && price > 0)) {
        preview.classList.add('hidden');
        return;
    }

    // 手续费（仅新建时有该区块；编辑模式 feeAmount 为 null，成本价不变）
    let feeAmount = null;
    let feeLabel = '';
    const feeModeSel = document.getElementById('posFeeMode');
    const feeHintEl = document.getElementById('posFeeHint');
    const feeEl = document.getElementById('pnlPreviewFee');
    if (feeModeSel) {
        const feeMode = feeModeSel.value;
        const feeRawValue = document.getElementById('posFeeValue')?.value || '';
        const feeCheck = validateFeeInput(feeMode, feeRawValue);
        if (!feeCheck.valid) {
            if (feeHintEl) {
                feeHintEl.textContent = '⚠ ' + feeCheck.error;
                feeHintEl.className = 'text-xs text-negative mt-1';
            }
            preview.classList.add('hidden');
            return;
        }
        if (feeHintEl) feeHintEl.className = 'text-xs text-muted mt-1';

        const amount = qty * cost;
        const customFee = calcFeeByMode(amount, feeCheck.mode, feeCheck.value);
        if (customFee !== null) {
            feeAmount = customFee;
            feeLabel = feeCheck.mode === 'rate' ? '费率' : '固定';
        } else {
            const fees = await getCachedFees();
            const buyFee = calcBuyFee(amount, fees);
            feeAmount = buyFee.total;
            feeLabel = '默认';
        }
    }

    // 含费成本价：新建时手续费摊入成本（与加仓口径一致）
    const costWithFee = feeAmount !== null ? (qty * cost + feeAmount) / qty : cost;
    const pnl = (price - costWithFee) * qty;
    const pnlPct = ((price - costWithFee) / costWithFee) * 100;

    preview.classList.remove('hidden');
    const pnlEl = document.getElementById('pnlPreviewValue');
    const pctEl = document.getElementById('pnlPreviewPct');
    pnlEl.textContent = `${pnl >= 0 ? '+' : ''}¥${formatCurrency(pnl)}`;
    pnlEl.className = `font-mono font-semibold ${getPnLColor(pnl)}`;
    pctEl.textContent = `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
    pctEl.className = `font-mono font-semibold ${getPnLColor(pnlPct)}`;

    if (feeEl) {
        if (feeAmount !== null) {
            feeEl.classList.remove('hidden');
            feeEl.innerHTML = `
                <div class="flex items-center justify-between text-sm"><span class="text-muted">手续费(${feeLabel})</span><span class="font-mono text-body">¥${formatCurrency(feeAmount)}</span></div>
                <div class="flex items-center justify-between text-sm mt-1"><span class="text-muted">含费成本价</span><span class="font-mono text-body">¥${formatNumber(costWithFee)}</span></div>`;
        } else {
            feeEl.classList.add('hidden');
            feeEl.innerHTML = '';
        }
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
    if (!guardEdit()) return;
    const position = getUserPositions().find(p => p.code === code);
    if (!position) { showToast('❌ 未找到该持仓', 'error'); return; }

    const isAdd = action === 'add';
    const title = isAdd ? '加仓' : '减仓';
    const actionColor = isAdd ? 'text-up' : 'text-down';
    const actionBg = isAdd ? 'bg-red-50' : 'bg-green-50';

    // 实时价格（降级用成本价）
    const livePrice = getPrice(position.code, position.costPrice) || position.costPrice;

    const modalHtml = `
        <div id="adjustModal" class="fixed inset-0 z-[100]">
            <div class="absolute inset-0 modal-backdrop" onclick="closeAdjustModal()"></div>
            <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-3xl mx-4">
                <div class="bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up">
                    <!-- 标题栏 -->
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

                    <!-- 当前持仓信息 -->
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
                                <div class="font-mono text-sm font-semibold text-ink">¥${formatNumber(livePrice)}</div>
                            </div>
                        </div>
                    </div>

                    <!-- 主体：左表单 + 右预览（并排） -->
                    <div class="grid grid-cols-2 divide-x divide-hairline">
                        <!-- 左栏：表单 -->
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
                                    <input type="number" id="adjustPrice" value="${livePrice}" placeholder="0.00" min="0" step="0.01"
                                        class="w-full pl-7 pr-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm font-mono">
                                </div>
                                <p class="text-xs text-muted mt-1">默认为当前实时价格，可修改</p>
                            </div>

                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">交易时间 <span class="text-primary">（可选，补录历史交易时填写）</span></label>
                                <input type="datetime-local" id="adjustExecutedAt"
                                    class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm font-mono">
                                <p class="text-xs text-muted mt-1">留空使用服务器当前时间；补录历史交易时请选择精确时间</p>
                            </div>

                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">手续费设置</label>
                                <div class="grid grid-cols-2 gap-3">
                                    <select id="adjustFeeMode"
                                        class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm">
                                        <option value="default">默认费率</option>
                                        <option value="rate">按费率</option>
                                        <option value="fixed">固定金额</option>
                                    </select>
                                    <div class="relative">
                                        <input type="number" id="adjustFeeValue" placeholder="0.00025" min="0" step="0.00001" disabled
                                            class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-ink placeholder:text-muted-soft focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm font-mono disabled:opacity-50 disabled:cursor-not-allowed">
                                        <span id="adjustFeeUnit" class="absolute right-3 top-1/2 -translate-y-1/2 text-muted text-sm hidden">元</span>
                                    </div>
                                </div>
                                <p id="adjustFeeHint" class="text-xs text-muted mt-1">默认按系统费率配置计算</p>
                            </div>

                            ${isAdd ? `
                            <div class="flex items-center justify-between gap-3 px-4 py-3 bg-surface-soft rounded-xl">
                                <div class="min-w-0">
                                    <div class="text-sm font-medium text-ink">加仓资金来自可用资金</div>
                                    <p id="adjustFromCashHint" class="text-xs text-muted mt-0.5">开启：买入从可用资金扣款，总资产不变（现金转持仓）</p>
                                </div>
                                <label class="relative inline-flex items-center cursor-pointer shrink-0">
                                    <input type="checkbox" id="adjustFromCash" checked class="sr-only peer">
                                    <div class="relative w-10 h-6 bg-hairline rounded-full peer-checked:bg-primary transition-colors after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:w-5 after:h-5 after:bg-white after:rounded-full after:shadow after:transition-transform peer-checked:after:translate-x-4"></div>
                                </label>
                            </div>` : ''}
                        </div>

                        <!-- 右栏：操作预览（常驻） -->
                        <div id="adjustPreview" class="${actionBg} px-6 py-5 flex flex-col">
                            <div class="text-xs font-medium ${actionColor} mb-3 flex items-center gap-1">
                                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                                操作预览
                            </div>
                            <div id="adjustPreviewContent" class="space-y-1.5 text-sm flex-1">
                                <div class="flex flex-col items-center justify-center h-full text-center text-muted text-xs leading-relaxed">
                                    <svg class="w-10 h-10 mb-2 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 17v-2a4 4 0 018 0v2M4 7h16M4 11h16M4 15h10"/></svg>
                                    输入数量和价格后<br/>此处实时显示手续费、成本变动<br/>及盈亏预测
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- 底部按钮 -->
                    <div class="px-8 py-5 border-t border-hairline flex gap-3">
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

    ['adjustQty', 'adjustPrice', 'adjustFeeValue'].forEach(id => {
        document.getElementById(id).addEventListener('input', () => updateAdjustPreview(position, action));
    });

    // 手续费模式切换：启用/禁用数值输入 + 更新提示
    document.getElementById('adjustFeeMode').addEventListener('change', (e) => {
        const mode = e.target.value;
        const valueInput = document.getElementById('adjustFeeValue');
        const unitEl = document.getElementById('adjustFeeUnit');
        const hintEl = document.getElementById('adjustFeeHint');
        if (mode === 'default') {
            valueInput.value = '';
            valueInput.disabled = true;
            unitEl.classList.add('hidden');
            hintEl.textContent = '默认按系统费率配置计算';
            hintEl.className = 'text-xs text-muted mt-1';
        } else {
            valueInput.disabled = false;
            if (mode === 'rate') {
                valueInput.placeholder = '0.00025';
                valueInput.step = '0.00001';
                unitEl.classList.add('hidden');
                hintEl.textContent = '费率范围 0 ~ 1%，如 0.00025 表示万2.5';
            } else {
                valueInput.placeholder = '5.00';
                valueInput.step = '0.01';
                unitEl.classList.remove('hidden');
                hintEl.textContent = '固定金额范围 0 ~ 100000 元/笔';
            }
            hintEl.className = 'text-xs text-muted mt-1';
        }
        updateAdjustPreview(position, action);
    });

    // 加仓：资金来源切换提示文案
    const fromCashToggle = document.getElementById('adjustFromCash');
    if (fromCashToggle) {
        fromCashToggle.addEventListener('change', (e) => {
            const hint = document.getElementById('adjustFromCashHint');
            if (hint) {
                hint.textContent = e.target.checked
                    ? '开启：买入从可用资金扣款，总资产不变（现金转持仓）'
                    : '关闭：外部转入加仓，不扣可用资金，总资产随之增加';
            }
            updateAdjustPreview(position, action);
        });
    }
}

// --- 加仓/减仓实时预览（含手续费双模式计算）---
export async function updateAdjustPreview(position, action) {
    const qty = parseInt(document.getElementById('adjustQty').value);
    const price = parseFloat(document.getElementById('adjustPrice').value);
    const content = document.getElementById('adjustPreviewContent');
    if (!content) return;

    // 无输入时：显示占位提示
    if (!qty || qty < 1 || !price || price <= 0) {
        content.innerHTML = `
            <div class="flex flex-col items-center justify-center h-full text-center text-muted text-xs leading-relaxed">
                <svg class="w-10 h-10 mb-2 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 17v-2a4 4 0 018 0v2M4 7h16M4 11h16M4 15h10"/></svg>
                输入数量和价格后<br/>此处实时显示手续费、成本变动<br/>及盈亏预测
            </div>`;
        return;
    }

    const isAdd = action === 'add';

    // 手续费模式与数值（含校验提示）
    const feeModeSel = document.getElementById('adjustFeeMode');
    const feeHintEl = document.getElementById('adjustFeeHint');
    const feeMode = feeModeSel ? feeModeSel.value : 'default';
    const feeRawValue = document.getElementById('adjustFeeValue')?.value || '';
    const feeCheck = validateFeeInput(feeMode, feeRawValue);

    if (!feeCheck.valid) {
        if (feeHintEl) {
            feeHintEl.textContent = '⚠ ' + feeCheck.error;
            feeHintEl.className = 'text-xs text-negative mt-1';
        }
        content.innerHTML = `<div class="text-negative text-xs flex items-start gap-1.5 p-3 bg-negative/5 rounded-lg"><svg class="w-4 h-4 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>${feeCheck.error}</div>`;
        return;
    }
    if (feeHintEl) {
        feeHintEl.className = 'text-xs text-muted mt-1';
    }

    // 获取手续费配置（全局默认）
    const fees = await getCachedFees();

    if (isAdd) {
        const oldTotalCost = position.costPrice * position.quantity;
        const addAmount = price * qty;

        // 优先使用用户指定的手续费模式
        let feeAmount, feeDetailHtml;
        const customFee = calcFeeByMode(addAmount, feeCheck.mode, feeCheck.value);
        if (customFee !== null) {
            feeAmount = customFee;
            feeDetailHtml = `
            <div class="flex justify-between"><span class="text-muted">手续费(${feeCheck.mode === 'rate' ? '费率' : '固定'})</span><span class="font-mono text-body">¥${formatCurrency(feeAmount)}</span></div>`;
        } else {
            const buyFee = calcBuyFee(addAmount, fees);
            feeAmount = buyFee.total;
            feeDetailHtml = `
            <div class="flex justify-between"><span class="text-muted">买入佣金</span><span class="font-mono text-body">¥${formatCurrency(buyFee.commission)}</span></div>
            <div class="flex justify-between"><span class="text-muted">买入过户费</span><span class="font-mono text-body">¥${formatCurrency(buyFee.transferFee)}</span></div>`;
        }

        const addCostWithFee = addAmount + feeAmount;
        const newQty = position.quantity + qty;
        const newCostPrice = (oldTotalCost + addCostWithFee) / newQty;
        const costDiff = newCostPrice - position.costPrice;

        content.innerHTML = `
            <div class="flex justify-between"><span class="text-muted">加仓金额</span><span class="font-mono font-semibold text-ink">¥${formatCurrency(addAmount)}</span></div>
            ${feeDetailHtml}
            <div class="flex justify-between border-t border-hairline/60 pt-1.5 mt-1.5"><span class="text-muted">买入总成本</span><span class="font-mono font-semibold text-ink">¥${formatCurrency(addCostWithFee)}</span></div>
            <div class="flex justify-between"><span class="text-muted">加仓后总量</span><span class="font-mono font-semibold text-ink">${newQty.toLocaleString()} 股</span></div>
            <div class="flex justify-between"><span class="text-muted">新成本价(含费)</span><span class="font-mono font-semibold text-ink">¥${formatNumber(newCostPrice)}</span></div>
            <div class="flex justify-between"><span class="text-muted">成本变动</span><span class="font-mono font-semibold ${costDiff >= 0 ? 'text-up' : 'text-down'}">${costDiff >= 0 ? '+' : ''}¥${formatNumber(Math.abs(costDiff))}</span></div>
        `;
    } else {
        if (qty > position.quantity) {
            content.innerHTML = `<div class="text-negative text-xs flex items-start gap-1.5 p-3 bg-negative/5 rounded-lg"><svg class="w-4 h-4 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>减仓数量不能超过当前持仓 ${position.quantity.toLocaleString()} 股</div>`;
            return;
        }
        const reduceAmount = price * qty;

        // 优先使用用户指定的手续费模式
        let feeAmount, feeDetailHtml;
        const customFee = calcFeeByMode(reduceAmount, feeCheck.mode, feeCheck.value);
        if (customFee !== null) {
            feeAmount = customFee;
            feeDetailHtml = `
            <div class="flex justify-between"><span class="text-muted">手续费(${feeCheck.mode === 'rate' ? '费率' : '固定'})</span><span class="font-mono text-body">¥${formatCurrency(feeAmount)}</span></div>`;
        } else {
            const sellFee = calcSellFee(reduceAmount, fees);
            feeAmount = sellFee.total;
            feeDetailHtml = `
            <div class="flex justify-between"><span class="text-muted">卖出佣金</span><span class="font-mono text-body">¥${formatCurrency(sellFee.commission)}</span></div>
            <div class="flex justify-between"><span class="text-muted">印花税</span><span class="font-mono text-body">¥${formatCurrency(sellFee.stampTax)}</span></div>
            <div class="flex justify-between"><span class="text-muted">过户费</span><span class="font-mono text-body">¥${formatCurrency(sellFee.transferFee)}</span></div>`;
        }

        const netProceeds = reduceAmount - feeAmount;
        const remainingQty = position.quantity - qty;
        const isAllClear = remainingQty === 0;
        const grossPnL = (price - position.costPrice) * qty;
        // 净盈亏 = 卖出净收入 - 买入成本（含手续费）
        const netPnL = netProceeds - position.costPrice * qty;
        const netPnLPct = position.costPrice > 0 ? (netPnL / (position.costPrice * qty)) * 100 : 0;

        content.innerHTML = `
            <div class="flex justify-between"><span class="text-muted">减仓金额</span><span class="font-mono font-semibold text-ink">¥${formatCurrency(reduceAmount)}</span></div>
            ${feeDetailHtml}
            <div class="flex justify-between border-t border-hairline/60 pt-1.5 mt-1.5"><span class="text-muted">卖出净收入</span><span class="font-mono font-semibold text-ink">¥${formatCurrency(netProceeds)}</span></div>
            <div class="flex justify-between"><span class="text-muted">毛盈亏(不含费)</span><span class="font-mono ${getPnLColor(grossPnL)}">${grossPnL >= 0 ? '+' : ''}¥${formatCurrency(grossPnL)}</span></div>
            <div class="flex justify-between"><span class="text-muted">净盈亏(含费)</span><span class="font-mono font-semibold ${getPnLColor(netPnL)}">${netPnL >= 0 ? '+' : ''}¥${formatCurrency(netPnL)}</span></div>
            <div class="flex justify-between"><span class="text-muted">净收益率</span><span class="font-mono font-semibold ${getPnLColor(netPnLPct)}">${netPnLPct >= 0 ? '+' : ''}${netPnLPct.toFixed(2)}%</span></div>
            <div class="flex justify-between"><span class="text-muted">剩余数量</span><span class="font-mono font-semibold text-ink">${remainingQty.toLocaleString()} 股</span></div>
            ${isAllClear ? '<div class="text-warning text-xs pt-1.5 flex items-center gap-1"><svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>该操作将清仓此股票</div>' : ''}
        `;
    }
}

// --- 执行加仓/减仓（原子调仓，手续费双模式）---
export async function executeAdjust(code, action) {
    if (!guardEdit()) return;
    const qty = parseInt(document.getElementById('adjustQty').value);
    const price = parseFloat(document.getElementById('adjustPrice').value);

    if (!qty || qty < 1) { showToast('❌ 请输入有效的数量', 'error'); return; }
    if (!price || price <= 0) { showToast('❌ 请输入有效的价格', 'error'); return; }

    // 手续费模式与数值（校验）
    const feeMode = document.getElementById('adjustFeeMode')?.value || 'default';
    const feeRawValue = document.getElementById('adjustFeeValue')?.value || '';
    const feeCheck = validateFeeInput(feeMode, feeRawValue);
    if (!feeCheck.valid) {
        showToast('❌ ' + feeCheck.error, 'error');
        return;
    }

    let positions = getUserPositions();
    const idx = positions.findIndex(p => p.code === code);
    if (idx === -1) { showToast('❌ 未找到该持仓', 'error'); return; }

    const position = positions[idx];
    const isAdd = action === 'add';

    if (!isAdd && qty > position.quantity) {
        showToast(`❌ 减仓数量不能超过持仓 ${position.quantity} 股`, 'error');
        return;
    }

    const currentClient = getCurrentClient();

    // 构造调仓请求（手续费双模式：default 不传，后端走全局配置；rate/fixed 显式传值）
    // 资金来源：加仓时来自可用资金（扣现金） / 外部转入（不扣现金，总资产增加）
    // 减仓时一律 from_cash=true，卖出所得进可用现金
    const fromCash = isAdd
        ? document.getElementById('adjustFromCash')?.checked !== false
        : true;
    const payload = {
        code: position.code,
        name: position.name,
        sector: position.sector || null,
        action: isAdd ? 'buy' : 'sell',
        quantity: qty,
        price: price,
        from_cash: fromCash,
        cost_method: 'average',
    };
    // 可选人为指定交易时间（补录历史交易）；datetime-local 不带秒，补 :00 以完整匹配后端
    const executedAtEl = document.getElementById('adjustExecutedAt');
    if (executedAtEl && executedAtEl.value) {
        payload.executed_at = `${executedAtEl.value}:00`;
    }
    if (feeCheck.mode) {
        payload.fee_mode = feeCheck.mode;
        payload.fee_value = feeCheck.value;
    }

    // 调仓原子执行：一次性写入 交易流水 + 成本批次 + 持仓 + 现金 + 当日快照
    let result;
    try {
        result = await executeAdjustApi(currentClient.id, payload);
    } catch (e) {
        showToast('❌ ' + (e.message || '调仓失败'), 'error');
        return;
    }

    // 用后端返回的权威数据更新内存态（避免前端计算与后端不一致）
    // 1) 更新持仓数组：若有 position 字段则替换/移除，否则按原逻辑处理
    const returnedPos = result.position;
    if (returnedPos) {
        const updIdx = positions.findIndex(p => p.code === returnedPos.code);
        const mapped = {
            name: returnedPos.name, code: returnedPos.code,
            sector: position.sector || '',  // 保留原 sector（后端可能不回传）
            quantity: returnedPos.quantity,
            costPrice: returnedPos.cost_price,
        };
        if (updIdx >= 0) positions[updIdx] = mapped;
        else positions.push(mapped);
    } else if (!isAdd) {
        // 卖出清仓后端 position=null → 从前端数组移除
        const remain = position.quantity - qty;
        if (remain <= 0) positions.splice(idx, 1);
        else positions[idx].quantity = remain;
    }
    // 2) 写回持仓（为了前端其它模块即时感知；后端已持久化，此步仅同步内存 + 后端二次校验）
    try {
        await saveUserPositions(positions);
    } catch (_) { /* 原子调仓已在后端持久化，此处忽略失败 */ }

    // 3) 同步可用现金（后端返回的 available_cash 为最新权威值）
    if (typeof result.available_cash === 'number' && currentClient) {
        currentClient.availableCash = result.available_cash;
    }

    // Toast：区分买入/卖出文案，展示手续费、盈亏、成本变动
    const tx = result.transaction || {};
    const feeTotal = typeof tx.fee_amount === 'number' ? tx.fee_amount : 0;
    if (isAdd) {
        const newCost = returnedPos ? returnedPos.cost_price : (positions[idx]?.costPrice ?? price);
        showToast(
            `✅ 已加仓 ${position.name} ${qty}股 @ ¥${formatNumber(price)}` +
            `（扣费${formatCurrency(feeTotal)}，新成本 ¥${formatNumber(newCost)}）`,
            'success'
        );
    } else {
        const realized = typeof tx.realized_pnl === 'number' ? tx.realized_pnl : 0;
        if (returnedPos) {
            showToast(
                `✅ 已减仓 ${position.name} ${qty}股，已实现盈亏 ${realized >= 0 ? '+' : ''}${formatCurrency(realized)}（扣费${formatCurrency(feeTotal)}）`,
                'success'
            );
        } else {
            showToast(
                `✅ 已清仓 ${position.name}，卖出 ${qty}股 @ ¥${formatNumber(price)}，已实现盈亏 ${realized >= 0 ? '+' : ''}${formatCurrency(realized)}（扣费${formatCurrency(feeTotal)}）`,
                'success'
            );
        }
    }

    closeAdjustModal();
    // 刷新所有组件：重新获取组合数据和盈亏历史
    const portfolio = await fetchClientPortfolio(currentClient.id);
    const pnlHistory = await fetchClientPnlHistory(currentClient.id);
    refreshAll(portfolio, pnlHistory);
    // 同步刷新客户名片（与持仓概览同源，确保总资产/持仓盈亏数值一致）
    renderClientProfile(portfolio);
    // 同步刷新客户列表右侧金额（后端实时盈亏摘要），保证调仓后列表金额立即更新
    await refreshClientSummaries().catch(() => {});
}

// --- 保存持仓（新增或编辑）---
// 新建：走原子调仓接口（buy），流水+持仓+现金+快照 单事务更新，手续费摊入成本价
// 编辑：保留 PUT positions（纠错用途：名称/板块/数量/成本价的直接修正）
export async function savePosition(originalCode = null) {
    if (!guardEdit()) return;
    const name = document.getElementById('posName').value.trim();
    const code = document.getElementById('posCode').value.trim();
    const quantity = parseInt(document.getElementById('posQuantity').value);
    const costPrice = parseFloat(document.getElementById('posCostPrice').value);
    const sector = document.getElementById('posSector').value;

    if (!name) { showToast('❌ 请输入股票名称', 'error'); return; }
    if (!code || code.length < 5) { showToast('❌ 请输入正确的股票代码（至少5位）', 'error'); return; }
    if (!quantity || quantity < 1) { showToast('❌ 请输入有效的持仓数量', 'error'); return; }
    if (!costPrice || costPrice <= 0) { showToast('❌ 请输入有效的成本价', 'error'); return; }

    const currentClient = getCurrentClient();

    // ---- 新建持仓：原子调仓接口（buy，含手续费；现金不足时后端拒绝）----
    if (!originalCode) {
        const positions = getUserPositions();
        if (positions.find(p => p.code === code)) {
            showToast(`❌ 股票代码 ${code} 已存在`, 'error');
            return;
        }
        if (!currentClient) { showToast('❌ 未选择客户', 'error'); return; }

        // 手续费校验（数值范围；默认模式时后端按全局配置计算）
        const feeMode = document.getElementById('posFeeMode')?.value || 'default';
        const feeRawValue = document.getElementById('posFeeValue')?.value || '';
        const feeCheck = validateFeeInput(feeMode, feeRawValue);
        if (!feeCheck.valid) {
            showToast('❌ ' + feeCheck.error, 'error');
            return;
        }
        const feeParams = feeCheck.mode
            ? { fee_mode: feeCheck.mode, fee_value: feeCheck.value }
            : {};

        // 资金来源：来自可用资金（扣现金） / 外部转入（不扣现金，总资产增加）
        const fromCash = document.getElementById('posFromCash')?.checked !== false;

        let result;
        try {
            result = await executeAdjustApi(currentClient.id, {
                code, name, sector,
                action: 'buy',
                quantity, price: costPrice,
                from_cash: fromCash,
                ...feeParams,
            });
        } catch (e) {
            showToast('❌ 新建持仓失败：' + e.message, 'error');
            return;
        }

        closePositionModal();

        // ---- 即时刷新（<1s）：事务后组合数据同步内存态并重渲染 ----
        syncClientState(result.portfolio);
        refreshRealtime(result.portfolio);
        // 同步刷新客户名片（与持仓概览同源，确保总资产/持仓盈亏数值一致）
        renderClientProfile(result.portfolio);

        // ---- 异步校准：并行拉取组合估值与盈亏历史，更新图表 ----
        try {
            const [portfolio, pnlHistory] = await Promise.all([
                fetchClientPortfolio(currentClient.id),
                fetchClientPnlHistory(currentClient.id),
            ]);
            if (portfolio) {
                syncClientState(portfolio);
                refreshAll(portfolio, pnlHistory);
                // 同步刷新客户名片（与持仓概览同源，确保总资产/持仓盈亏数值一致）
                renderClientProfile(portfolio);
                // 同步刷新客户列表右侧金额（后端实时盈亏摘要）
                await refreshClientSummaries().catch(() => {});
            }
        } catch (e) {
            console.warn('新建持仓后数据校准失败:', e);
        }

        const feeAmount = result.transaction?.fee_amount ?? 0;
        if (feeAmount > 0) {
            showToast(`✅ 已添加持仓 ${name}，手续费 ¥${formatCurrency(feeAmount)} 已计入成本`, 'success');
        } else {
            showToast('✅ 持仓已添加', 'success');
        }
        return;
    }

    // ---- 编辑持仓：整体替换（纠错用途）----
    let positions = getUserPositions();
    const idx = positions.findIndex(p => p.code === originalCode);
    if (idx === -1) { showToast('❌ 未找到该持仓', 'error'); return; }

    const old = positions[idx];
    // 数量/成本价变化会破坏与交易流水的一致性，提示优先使用调仓操作
    if (old.quantity !== quantity || old.costPrice !== costPrice) {
        const confirmed = confirm(
            `检测到数量或成本价的直接修改：\n` +
            `这会导致持仓与交易流水不一致（对账告警会标记该项）。\n` +
            `建议使用「加仓 / 减仓」完成调仓。\n\n仍要继续直接修改吗？`);
        if (!confirmed) return;
    }
    positions[idx] = { name, code, quantity, costPrice, sector };

    if (!(await persistPositions(positions))) return;

    // 需求一：编辑持仓后在策略复盘模块自动新增一条"调整"记录
    // （黄色"调整"标签；其余字段与"买入"记录保持一致，仅记录当前编辑后的快照）
    try {
        const currentClient = getCurrentClient();
        if (currentClient) {
            await createAdjustRecord(currentClient.id, {
                code,
                name,
                quantity,
                price: costPrice,
                cost_price: costPrice,
                trade_date: new Date().toISOString().slice(0, 10),
            });
        }
    } catch (e) {
        console.warn('写入调整复盘记录失败:', e);
    }

    closePositionModal();
    refreshAll();
    showToast('✅ 持仓已更新', 'success');
}

// --- 撤销持仓：精确批次反转最近一笔操作（买入/卖出/调整），并清理对应复盘记录 ---
export async function revokePosition(code) {
    if (!guardEdit()) return;
    const position = getUserPositions().find(p => p.code === code);
    if (!position) return;

    if (!confirm(
        `确定撤销「${position.name}(${position.code})」最近一笔操作吗？\n\n` +
        `撤销将按批次精确回退最近一次买入 / 卖出 / 编辑，并同步清理对应的策略复盘记录` +
        `（更早批次的复盘记录会完整保留）。`)) {
        return;
    }
    const currentClient = getCurrentClient();
    if (!currentClient) { showToast('❌ 未选择客户', 'error'); return; }

    try {
        const result = await revokePositionApi(currentClient.id, code);
        // 重新拉取组合与盈亏历史，确保持仓 / 现金 / 复盘面板同步刷新（与调仓后一致）
        try {
            const [portfolio, pnlHistory] = await Promise.all([
                fetchClientPortfolio(currentClient.id),
                fetchClientPnlHistory(currentClient.id),
            ]);
            if (portfolio) {
                syncClientState(portfolio);
                refreshAll(portfolio, pnlHistory);
                // 同步刷新客户名片（与持仓概览同源，确保总资产/持仓盈亏数值一致）
                renderClientProfile(portfolio);
                // 同步刷新客户列表右侧金额（后端实时盈亏摘要）
                await refreshClientSummaries().catch(() => {});
            } else {
                refreshAll();
            }
        } catch (e) {
            console.warn('撤销后数据刷新失败:', e);
            refreshAll();
        }
        const label = result?.action === 'sell' ? '卖出'
            : result?.action === 'buy' ? '买入'
            : result?.action === 'adjust' ? '调整' : '操作';
        showToast(`✅ 已撤销最近一笔「${label}」操作`, 'success');
    } catch (e) {
        // 跨批次卖出：后端返回 409，明确提示原因，不静默失败
        if (e?.status === 409) {
            showToast('⚠️ ' + (e.message || '该笔卖出跨越多个批次，无法精确撤销') + '（请改用手工调仓）', 'error', 10000);
        } else {
            showToast('❌ 撤销失败：' + (e?.message || '未知错误'), 'error');
        }
    }
}

// ============================================================
// OCR 截图识别导入：上传截图 → 预览编辑 → 按序导入（逐条淡出）
// 落库复用既有 executeAdjustApi（POST /api/clients/{id}/adjust），逐笔原子事务，
// 因此无需在此重复实现持仓/现金/成本批次/Tier2 更新逻辑。
// ============================================================
let ocrRows = [];          // 当前预览的可编辑行：{uid,name,code,action,quantity,price,fee,sector,trade_date,trade_time,datetime,conf}
let ocrImporting = false;
let ocrSeq = 0;
let ocrPendingKind = 'trade';   // 待识别截图类型：trade=交易，holding=持仓
let ocrBatchActive = false;     // 交易导入批次是否处于「已开启、可继续追加截图」状态

// ---- 多张交易截图去重（跨截图） ----
// 判定键：交易时间 + 股票 + 买卖方向 + 数量 + 价格 五个字段完全一致即视为同一笔。
// 股票优先取 6 位代码；无代码（如同花顺历史成交）则退化为名称。其余字段逐一精确比较。
function ocrNormalizeDatetime(r) {
  let dt = (r.datetime || '').trim();
  if (!dt) {
    const d = (r.trade_date || '').trim();
    const t = (r.trade_time || '').trim();
    dt = (d + ' ' + t).trim();
  }
  return dt.replace(/\s+/g, ' ');
}
function ocrDupKey(r) {
  const stock = (r.code && /^\d{6}$/.test(String(r.code).trim()))
    ? String(r.code).trim()
    : String(r.name || '').trim();
  const dt = ocrNormalizeDatetime(r);
  const q = (r.quantity == null || r.quantity === '') ? '' : String(r.quantity);
  const p = (r.price == null || r.price === '') ? '' : String(r.price);
  return [stock, r.action || '', q, p, dt].join('');
}
/** 把当前 DOM 中已编辑的值回写到 ocrRows，使去重/重渲染基于用户最终值。*/
function syncOcrRowEdits() {
  ocrRows = ocrRows.map(r => {
    const card = document.getElementById('ocrCard_' + r.uid);
    if (!card) return r;
    const g = (id) => document.getElementById(id)?.value ?? '';
    const qv = g('ocrQty_' + r.uid);
    const pv = g('ocrPrice_' + r.uid);
    const fv = g('ocrFee_' + r.uid);
    return {
      ...r,
      name: g('ocrName_' + r.uid).trim(),
      code: g('ocrCode_' + r.uid).trim(),
      action: g('ocrAction_' + r.uid),
      quantity: qv === '' ? '' : parseInt(qv, 10),
      price: pv === '' ? '' : parseFloat(pv),
      fee: fv ? parseFloat(fv) : '',
      sector: g('ocrSector_' + r.uid),
      datetime: g('ocrDate_' + r.uid).trim(),
      ignore: document.getElementById('ocrIgnore_' + r.uid)?.checked || false,
    };
  });
}
/** 跨截图去重：保留首次出现的记录，返回被移除的条数。*/
function dedupeOcrRows() {
  const seen = new Set();
  const kept = [];
  let removed = 0;
  for (const r of ocrRows) {
    const key = ocrDupKey(r);
    if (seen.has(key)) { removed++; continue; }
    seen.add(key);
    kept.push(r);
  }
  ocrRows = kept;
  return removed;
}
/** 依据最新 ocrRows 重渲染预览列表（保留已编辑值），并更新计数。*/
function rebuildOcrList() {
  const list = document.getElementById('ocrList');
  if (list) list.innerHTML = ocrRows.map(ocrCardHtml).join('');
  const c = document.getElementById('ocrCount');
  if (c) c.textContent = String(ocrRows.length);
  ocrRows.forEach(row => {
    const nameEl = document.getElementById('ocrName_' + row.uid);
    if (nameEl) nameEl.addEventListener('blur', () => ocrResolveName(row.uid));
  });
}
/** 在已开启的交易导入批次中追加更多截图（跨截图去重入口）。*/
export function addOcrScreenshots() {
  if (!guardEdit()) return;
  if (!ocrBatchActive) return;
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/*';
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (file) await handleOcrFile(file);
  };
  input.click();
}

// 持仓截图预览状态（与交易截图流程相互独立，避免相互污染）
let ocrHoldingRows = [];          // [{uid,name,code,quantity,cost_price,current_price,market_value,float_pnl,pnl_pct,sector,conf}]
let ocrHoldingImporting = false;
let ocrHoldingSeq = 0;
let ocrHoldingScreenshot = { available_cash: null, total_assets: null };
let ocrHoldingMergeMode = 'merge'; // 持仓导入方式：merge=合并（默认），overwrite=覆盖（整体替换）

const OCR_FIELD_LABEL = { name: '名称', code: '代码', action: '方向', quantity: '数量', price: '价格', amount: '金额', fee: '手续费', trade_date: '时间' };

function ocrUid() { return 'ocr' + (++ocrSeq); }

function escapeAttr(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function confSummary(conf) {
    const lows = Object.entries(conf || {}).filter(([, v]) => v < 0.7).map(([k]) => OCR_FIELD_LABEL[k] || k);
    return lows.length ? '偏低: ' + lows.join('·') : '完整';
}
function labelOf(uid) {
    const i = ocrRows.findIndex(r => r.uid === uid);
    return i >= 0 ? i + 1 : '?';
}

/** 触发文件选择并识别（窗口函数）。kind: 'trade'=交易截图，'holding'=持仓截图 */
export function startOcrImport(kind = 'trade') {
    if (!guardEdit()) return;
    ocrPendingKind = kind === 'holding' ? 'holding' : 'trade';
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.onchange = async () => {
        const file = input.files && input.files[0];
        if (file) await handleOcrFile(file);
    };
    input.click();
}

async function handleOcrFile(file) {
    showOcrLoading(ocrPendingKind === 'holding' ? '正在识别持仓截图，请稍候…' : '正在识别交易截图，请稍候…');
    let data;
    try {
        data = await recognizeOcrImage(file, ocrPendingKind);
    } catch (e) {
        hideOcrLoading();
        showToast('❌ ' + (e?.message || '截图识别失败'), 'error', 6000);
        return;
    }
    // 持仓截图：解析成持仓列表 + 截图级字段，走合并/覆盖 + 标色记录 + 写可用资金
    if (ocrPendingKind === 'holding') {
        const hrows = (data.rows || []).map(normalizeOcrHolding);
        if (!hrows.length) {
            hideOcrLoading();
            showToast('未识别到持仓记录，请检查截图清晰度', 'warn', 4000);
            return;
        }
        // 反查期间保持遮罩（预览尚未出现），文案切换为匹配中
        showOcrLoading('正在匹配股票代码与实时价…');
        await resolveHoldingMeta(hrows);
        ocrHoldingScreenshot = {
            available_cash: data.available_cash != null ? Number(data.available_cash) : null,
            total_assets: data.total_assets != null ? Number(data.total_assets) : null,
        };
        ocrHoldingRows = hrows;
        openOcrHoldingModal();
        hideOcrLoading();
        return;
    }
    const rows = (data.rows || []).map(normalizeOcrRow);
    if (!rows.length) {
        hideOcrLoading();
        showToast('未识别到交易记录，请检查截图清晰度', 'warn', 4000);
        return;
    }
    // 名称→代码/板块 最佳努力解析（不覆盖用户最终可编辑值）
    for (const row of rows) {
        if (row.name && !row.code) {
            try {
                const { results } = await searchStocksApi(row.name, 5);
                const exact = (results || []).find(s => s.name === row.name) || (results || [])[0];
                if (exact) {
                    row.code = exact.code || row.code;
                    if (!row.sector && exact.sector) row.sector = exact.sector;
                }
            } catch { /* 静默失败，用户手动填 */ }
        }
    }
    // 批次已开启：将新截图识别出的交易追加进当前批次，并执行跨截图去重。
    if (ocrBatchActive) {
        syncOcrRowEdits();                 // 先回写用户已编辑的值
        ocrRows = ocrRows.concat(rows);    // 追加
        const removed = dedupeOcrRows();    // 按五字段去重（保留首条）
        rebuildOcrList();                   // 重渲染列表（保留已编辑值）
        hideOcrLoading();
        if (removed > 0) {
            showToast(`已自动去除 ${removed} 条重复交易（同一笔出现在多张截图）`, 'success', 4500);
        } else {
            showToast(`已追加 ${rows.length} 笔，当前共 ${ocrRows.length} 笔待确认`, 'success', 3000);
        }
        return;
    }
    ocrRows = rows;
    openOcrImportModal();
    ocrBatchActive = true;
    hideOcrLoading();
}

function normalizeOcrRow(r) {
    return {
        uid: ocrUid(),
        name: r.name || '',
        code: r.code || '',
        action: r.action || 'buy',
        quantity: r.quantity != null ? r.quantity : '',
        price: r.price != null ? r.price : '',
        fee: r.fee != null ? r.fee : '',
        sector: r.sector || '',
        trade_date: r.trade_date || '',
        trade_time: r.trade_time || '',
        conf: r.confidences || {},
    };
}

/** 名称失焦：按名称搜索补全代码与板块 */
async function ocrResolveName(uid) {
    const row = ocrRows.find(r => r.uid === uid);
    if (!row) return;
    const nameEl = document.getElementById('ocrName_' + uid);
    const name = nameEl?.value.trim();
    if (!name) return;
    try {
        const { results } = await searchStocksApi(name, 5);
        const exact = (results || []).find(s => s.name === name) || (results || [])[0];
        if (exact) {
            const codeEl = document.getElementById('ocrCode_' + uid);
            const sectorEl = document.getElementById('ocrSector_' + uid);
            if (codeEl && !codeEl.value) codeEl.value = exact.code || '';
            if (sectorEl && exact.sector && SECTORS.includes(exact.sector)) sectorEl.value = exact.sector;
            row.code = codeEl?.value || row.code;
            if (exact.sector) row.sector = sectorEl?.value || exact.sector;
        }
    } catch { /* 静默 */ }
}

function ocrCardHtml(row) {
    const low = (f) => (row.conf[f] != null && row.conf[f] < 0.7) ? 'border-amber-400 bg-amber-50/40' : '';
    return `
        <div class="ocr-card bg-surface-strong border border-hairline rounded-2xl p-4 mb-3" id="ocrCard_${row.uid}" data-uid="${row.uid}">
            <div class="flex items-center gap-2 mb-3">
                <span class="ocr-dot w-2.5 h-2.5 rounded-full bg-muted" id="ocrDot_${row.uid}"></span>
                <span class="text-xs text-muted font-mono" id="ocrStatus_${row.uid}">待录入</span>
                <span class="text-[11px] text-muted">置信度 ${confSummary(row.conf)}</span>
                <label class="ml-auto flex items-center gap-1 text-xs text-muted cursor-pointer">
                    <input type="checkbox" id="ocrIgnore_${row.uid}"> 忽略
                </label>
                <button onclick="ocrDeleteRow('${row.uid}')" class="text-xs text-negative hover:underline ml-1">删除</button>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
                <div>
                    <label class="block text-[11px] text-muted mb-1">时间</label>
                    <input id="ocrDate_${row.uid}" value="${escapeAttr((row.trade_date || '') + (row.trade_time ? ' ' + row.trade_time : ''))}" placeholder="YYYY-MM-DD HH:MM" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg ${low('trade_date')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">名称</label>
                    <input id="ocrName_${row.uid}" value="${escapeAttr(row.name)}" placeholder="如：贵州茅台" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg ${low('name')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">代码</label>
                    <input id="ocrCode_${row.uid}" value="${escapeAttr(row.code)}" placeholder="600519" maxlength="6" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg font-mono ${low('code')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">方向</label>
                    <select id="ocrAction_${row.uid}" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg ${low('action')}">
                        <option value="buy" ${row.action === 'buy' ? 'selected' : ''}>买入</option>
                        <option value="sell" ${row.action === 'sell' ? 'selected' : ''}>卖出</option>
                    </select>
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">数量</label>
                    <input id="ocrQty_${row.uid}" value="${escapeAttr(row.quantity)}" placeholder="100" type="number" min="1" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg font-mono ${low('quantity')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">价格</label>
                    <input id="ocrPrice_${row.uid}" value="${escapeAttr(row.price)}" placeholder="0.00" type="number" step="0.01" min="0" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg font-mono ${low('price')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">手续费</label>
                    <input id="ocrFee_${row.uid}" value="${escapeAttr(row.fee)}" placeholder="0.00" type="number" step="0.01" min="0" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg font-mono ${low('fee')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">板块</label>
                    <select id="ocrSector_${row.uid}" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg">
                        ${SECTORS.map(s => `<option value="${s}" ${row.sector === s ? 'selected' : ''}>${s}</option>`).join('')}
                    </select>
                </div>
            </div>
        </div>`;
}

export function openOcrImportModal() {
    const modalHtml = `
        <div id="ocrImportModal" class="fixed inset-0 z-[100]">
            <div class="absolute inset-0 modal-backdrop" onclick="closeOcrImportModal()"></div>
            <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-2xl mx-4">
                <div class="bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up max-h-[92vh] flex flex-col">
                    <div class="px-6 pt-5 pb-4 relative border-b border-hairline flex items-center">
                        <button onclick="closeOcrImportModal()" class="absolute top-4 right-4 w-8 h-8 rounded-full bg-surface-strong flex items-center justify-center text-muted hover:text-ink hover:bg-hairline transition-all">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                        </button>
                        <div>
                            <h3 class="text-lg font-semibold text-ink">截图识别结果预览</h3>
                            <p class="text-sm text-muted mt-1">共 <span id="ocrCount">${ocrRows.length}</span> 笔待确认 · 可继续添加截图（自动去重）· 请核对后按序导入</p>
                        </div>
                    </div>

                    <div id="ocrProgressWrap" class="hidden px-6 pt-3">
                        <div class="h-2 w-full bg-surface-strong rounded-full overflow-hidden">
                            <div id="ocrProgressBar" class="h-full bg-primary transition-all duration-300" style="width:0%"></div>
                        </div>
                        <p id="ocrProgressText" class="text-xs text-muted mt-1">录入进度 0 / 0</p>
                    </div>

                    <div id="ocrList" class="px-6 py-4 overflow-y-auto flex-1" style="max-height:60vh">
                        ${ocrRows.map(ocrCardHtml).join('')}
                    </div>

                    <div class="px-6 py-4 border-t border-hairline flex gap-3">
                        <button onclick="addOcrScreenshots()" class="px-4 py-3 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors whitespace-nowrap">+ 添加截图</button>
                        <button onclick="closeOcrImportModal()" class="flex-1 px-4 py-3 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors">取消</button>
                        <button id="ocrImportBtn" onclick="importOcrRows()" class="flex-1 px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors shadow-sm shadow-primary/25">确认并按序导入</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    const existing = document.getElementById('ocrImportModal');
    if (existing) existing.remove();
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    document.body.style.overflow = 'hidden';
    ocrRows.forEach(row => {
        const nameEl = document.getElementById('ocrName_' + row.uid);
        if (nameEl) nameEl.addEventListener('blur', () => ocrResolveName(row.uid));
    });
}

export function closeOcrImportModal() {
    const modal = document.getElementById('ocrImportModal');
    if (modal) modal.remove();
    document.body.style.overflow = '';
    ocrRows = [];
    ocrImporting = false;
    ocrBatchActive = false;
}

export function ocrDeleteRow(uid) {
    ocrRows = ocrRows.filter(r => r.uid !== uid);
    const card = document.getElementById('ocrCard_' + uid);
    if (card) card.remove();
    const c = document.getElementById('ocrCount');
    if (c) c.textContent = String(ocrRows.length);
}

function readOcrRow(uid) {
    const g = (id) => document.getElementById(id)?.value ?? '';
    return {
        uid,
        name: g('ocrName_' + uid).trim(),
        code: g('ocrCode_' + uid).trim(),
        action: g('ocrAction_' + uid),
        quantity: parseInt(g('ocrQty_' + uid)),
        price: parseFloat(g('ocrPrice_' + uid)),
        fee: g('ocrFee_' + uid) ? parseFloat(g('ocrFee_' + uid)) : null,
        sector: g('ocrSector_' + uid),
        datetime: g('ocrDate_' + uid).trim(),
        ignore: document.getElementById('ocrIgnore_' + uid)?.checked || false,
    };
}

function markRowError(uid, msg) {
    const card = document.getElementById('ocrCard_' + uid);
    if (!card) return;
    card.classList.add('animate-shake', 'border-negative');
    const st = document.getElementById('ocrStatus_' + uid);
    if (st) st.textContent = '校验未过：' + msg;
    const dot = document.getElementById('ocrDot_' + uid);
    if (dot) dot.className = 'ocr-dot w-2.5 h-2.5 rounded-full bg-negative';
}

function setProgress(done, total) {
    const bar = document.getElementById('ocrProgressBar');
    const txt = document.getElementById('ocrProgressText');
    if (bar) bar.style.width = total ? (done / total * 100) + '%' : '0%';
    if (txt) txt.textContent = `录入进度 ${done} / ${total}`;
}

function fadeOutRow(card) {
    card.classList.add('animate-ocr-fade-out');
    card.addEventListener('animationend', () => card.remove(), { once: true });
}

function buildPayload(r) {
    const payload = {
        code: r.code,
        name: r.name || null,
        sector: r.sector || null,
        action: r.action,
        quantity: r.quantity,
        price: r.price,
        from_cash: true,
        cost_method: 'average',
        skip_if_duplicate: true,   // 落库时跳过「同客户+5字段」已存在的重复交易
    };
    if (r.fee != null && !isNaN(r.fee)) { payload.fee_mode = 'fixed'; payload.fee_value = r.fee; }
    if (r.datetime) {
        const iso = r.datetime.replace(' ', 'T');
        payload.executed_at = iso.length === 16 ? iso + ':00' : iso;   // YYYY-MM-DDTHH:MM → 补秒
    }
    return payload;
}

async function runSingleImport(uid, currentClient) {
    const r = readOcrRow(uid);
    const card = document.getElementById('ocrCard_' + uid);
    const dot = document.getElementById('ocrDot_' + uid);
    const status = document.getElementById('ocrStatus_' + uid);
    if (dot) dot.className = 'ocr-dot w-2.5 h-2.5 rounded-full bg-primary animate-pulse-soft';
    if (status) status.textContent = '录入中…';
    if (card) card.classList.remove('animate-shake', 'border-negative');
    const result = await executeAdjustApi(currentClient.id, buildPayload(r));
    if (result && result.duplicate) {
        // 后端命中「同客户+5字段」已存在交易：标记为已跳过（不淡出，保留供用户核对）
        if (card) {
            card.classList.add('border-dashed', 'opacity-70');
            if (status) status.textContent = '已存在（已跳过重复）';
            const d = document.getElementById('ocrDot_' + uid);
            if (d) d.className = 'ocr-dot w-2.5 h-2.5 rounded-full bg-muted';
        }
    } else if (card) {
        fadeOutRow(card);
    }
    return result;
}

export async function importOcrRows() {
    if (!guardEdit()) return;
    if (ocrImporting) return;
    const currentClient = getCurrentClient();
    if (!currentClient) { showToast('❌ 未选择客户', 'error'); return; }

    const rows = ocrRows.map(r => readOcrRow(r.uid));
    const toImport = rows.filter(r => !r.ignore);

    for (const r of toImport) {
        if (!/^\d{6}$/.test(r.code)) { markRowError(r.uid, '代码须为6位'); showToast(`❌ 第 ${labelOf(r.uid)} 行代码无效`, 'error'); return; }
        if (r.action !== 'buy' && r.action !== 'sell') { markRowError(r.uid, '方向无效'); showToast(`❌ 第 ${labelOf(r.uid)} 行方向无效`, 'error'); return; }
        if (!(r.quantity > 0)) { markRowError(r.uid, '数量无效'); showToast(`❌ 第 ${labelOf(r.uid)} 行数量无效`, 'error'); return; }
        if (!(r.price > 0)) { markRowError(r.uid, '价格无效'); showToast(`❌ 第 ${labelOf(r.uid)} 行价格无效`, 'error'); return; }
        if (r.action === 'buy' && (!r.name || !r.sector)) { markRowError(r.uid, '买入需名称+板块'); showToast(`❌ 第 ${labelOf(r.uid)} 行买入需补全名称与板块`, 'error'); return; }
    }
    if (!toImport.length) { showToast('没有需要导入的记录', 'warn'); return; }

    // 按时间升序（无时间保持原序），逐条录入
    toImport.sort((a, b) => (a.datetime || '~').localeCompare(b.datetime || '~'));

    ocrImporting = true;
    document.getElementById('ocrProgressWrap')?.classList.remove('hidden');
    const total = toImport.length;
    let done = 0, failed = 0;
    setProgress(0, total);

    for (const r of toImport) {
        try {
            await runSingleImport(r.uid, currentClient);
            done++;
        } catch (e) {
            failed++;
            const card = document.getElementById('ocrCard_' + r.uid);
            if (card) {
                card.classList.add('animate-shake', 'border-negative');
                const st = document.getElementById('ocrStatus_' + r.uid);
                if (st) st.innerHTML = '失败：' + escapeHtml(e.message || '未知错误') + ' <button onclick="ocrRetryRow(\'' + r.uid + '\')" class="text-primary underline ml-1">重试</button>';
                const dot = document.getElementById('ocrDot_' + r.uid);
                if (dot) dot.className = 'ocr-dot w-2.5 h-2.5 rounded-full bg-negative';
            }
            showToast(`⚠️ 第 ${labelOf(r.uid)} 行导入失败：${e.message || ''}`, 'error', 5000);
        }
        setProgress(done + failed, total);
    }

    ocrImporting = false;
    if (failed === 0) {
        finishImport(done, total, 0);
    } else {
        showToast(`导入完成：成功 ${done} / 失败 ${failed}，请重试失败项`, 'warn', 6000);
        const btn = document.getElementById('ocrImportBtn');
        if (btn) { btn.textContent = `完成（成功 ${done} / 失败 ${failed}）`; btn.onclick = () => finishImport(done, total, failed); }
    }
}

/** 单笔重试（失败行上的「重试」按钮） */
export async function ocrRetryRow(uid) {
    if (!guardEdit()) return;
    const currentClient = getCurrentClient();
    if (!currentClient) return;
    try {
        await runSingleImport(uid, currentClient);
        showToast('✅ 该笔已导入', 'success');
        // 若已无失败项，整体收尾
        setTimeout(() => {
            if (document.querySelectorAll('.ocr-card.animate-shake').length === 0) {
                const total = ocrRows.length;
                finishImport(total, total, 0);
            }
        }, 500);
    } catch (e) {
        const card = document.getElementById('ocrCard_' + uid);
        if (card) {
            card.classList.add('animate-shake', 'border-negative');
            const st = document.getElementById('ocrStatus_' + uid);
            if (st) st.innerHTML = '失败：' + escapeHtml(e.message || '未知错误') + ' <button onclick="ocrRetryRow(\'' + uid + '\')" class="text-primary underline ml-1">重试</button>';
        }
        showToast('⚠️ 重试失败：' + (e.message || ''), 'error', 5000);
    }
}

function finishImport(done, total, failed) {
    ocrBatchActive = false;
    const currentClient = getCurrentClient();
    if (currentClient) {
        Promise.all([fetchClientPortfolio(currentClient.id), fetchClientPnlHistory(currentClient.id)])
            .then(([portfolio, pnlHistory]) => {
                if (portfolio) {
                    syncClientState(portfolio);
                    refreshAll(portfolio, pnlHistory);
                    renderClientProfile(portfolio);
                    refreshClientSummaries().catch(() => {});
                } else {
                    refreshAll();
                }
            })
            .catch(() => refreshAll());
    }
    const bar = document.getElementById('ocrProgressWrap');
    if (bar) bar.classList.add('hidden');
    const list = document.getElementById('ocrList');
    if (list) {
        list.innerHTML = `
            <div class="text-center py-10">
                <div class="text-2xl mb-2">✅</div>
                <div class="text-sm text-ink">已处理 ${done} 笔${failed ? '，失败 ' + failed + ' 笔' : ''}</div>
                <div class="text-xs text-muted mt-1">列表已清空</div>
            </div>`;
    }
    showToast(`✅ 已导入 ${done} 笔${failed ? '，' + failed + ' 笔失败' : ''}`, failed ? 'warn' : 'success');
    const btn = document.getElementById('ocrImportBtn');
    if (btn) { btn.textContent = '完成'; btn.onclick = () => closeOcrImportModal(); }
}

// ============================================================
// 持仓截图识别导入：上传截图 → 预览编辑 → 合并/覆盖持仓 + 按分类打标 + 写可用资金
// 落库三步：① PUT /positions（合并=按代码合并现有；覆盖=整体替换）
//           ② 每只生成复盘记录：合并模式按「前态 vs 导入态」分类为 买入/卖出/调整，覆盖模式全为调整
//           ③ 若截图含可用资金，PUT /clients 写 available_cash（客户总体必要数据；
//              total_assets 由系统按「可用资金+持仓资产」计算、只读不回写；可用资金不参与任何单只持仓判定）
// ============================================================
function ocrHoldingUid() { return 'och' + (++ocrHoldingSeq); }

function normalizeOcrHolding(r) {
    return {
        uid: ocrHoldingUid(),
        name: r.name || '',
        code: r.code || '',
        quantity: r.quantity != null ? r.quantity : '',
        cost_price: r.cost_price != null ? r.cost_price : '',
        current_price: r.current_price != null ? r.current_price : '',
        market_value: r.market_value != null ? r.market_value : '',
        float_pnl: r.float_pnl != null ? r.float_pnl : '',
        pnl_pct: r.pnl_pct != null ? r.pnl_pct : '',
        sector: r.sector || '',
        conf: r.confidences || {},
    };
}

// 反查结果回填：代码 / 实时现价 / 板块。截图里的现价一律忽略，改用 API 实时价；
// 仅当 API 无价（exact.price 缺失）才留空，由持仓列表定时刷新回填。
function _applyHoldingLookup(row, exact) {
    if (!exact) return;
    if (!row.code && exact.code) row.code = exact.code;
    if (!row.name && exact.name) row.name = exact.name;
    if (exact.sector) row.sector = exact.sector;
    if (exact.price != null && !isNaN(parseFloat(exact.price))) row.current_price = exact.price;
    if (!document.getElementById('ocrHoldingModal')) return; // 预览未打开时不碰 DOM（卡片按 row 渲染）
    const codeEl = document.getElementById('ocrHCode_' + row.uid);
    const priceEl = document.getElementById('ocrHPrice_' + row.uid);
    const sectorEl = document.getElementById('ocrHSector_' + row.uid);
    if (codeEl && exact.code) codeEl.value = exact.code;
    if (priceEl && exact.price != null && !isNaN(parseFloat(exact.price))) priceEl.value = exact.price;
    if (sectorEl && exact.sector && SECTORS.includes(exact.sector)) sectorEl.value = exact.sector;
    const pnlEl = document.getElementById('ocrHPnl_' + row.uid);
    if (pnlEl) pnlEl.innerHTML = holdingPnlInner(row);
}

async function resolveHoldingMeta(rows) {
    for (const row of rows) {
        try {
            let exact = null;
            if (row.code) {
                const { results } = await searchStocksApi(row.code, 5);
                exact = (results || []).find(s => s.code === row.code) || (results || [])[0];
            } else if (row.name) {
                const { results } = await searchStocksApi(row.name, 5);
                exact = (results || []).find(s => s.name === row.name) || (results || [])[0];
            }
            _applyHoldingLookup(row, exact);
        } catch { /* 静默失败，用户手动填 */ }
    }
}

function labelOfHolding(uid) {
    const i = ocrHoldingRows.findIndex(r => r.uid === uid);
    return i >= 0 ? i + 1 : '?';
}

// 预览盈亏：由 成本价 / 现价 / 股数 计算（不采用 OCR 抽出的盈亏值），与 overview.js 口径一致。
// 三项任一缺失 → 返回 null（调用方显示「—/待刷新」）。A股惯例：盈利红、亏损绿。
function computeHoldingPnl(row) {
    const cp = parseFloat(row.cost_price);
    const px = parseFloat(row.current_price);
    const qty = parseFloat(row.quantity);
    if (!(cp >= 0) || !(px >= 0) || !(qty > 0)) return null;
    const pnl = (px - cp) * qty;
    const pct = cp > 0 ? (px - cp) / cp * 100 : 0;
    return { pnl, pct };
}
function holdingPnlInner(row) {
    const r = computeHoldingPnl(row);
    if (!r) return '<span class="text-muted">盈亏 —/待刷新</span>';
    const color = r.pnl > 0 ? 'text-rose-500' : (r.pnl < 0 ? 'text-emerald-500' : 'text-muted');
    const sign = r.pnl > 0 ? '+' : '';
    return `<span class="${color}">盈亏 ${sign}${formatNumber(r.pnl)} (${sign}${r.pct.toFixed(2)}%)</span>`;
}
// 用户手动改 股数/成本价 时实时重算预览盈亏
export function recomputeHoldingPnl(uid) {
    const row = { quantity: '', cost_price: '', current_price: '' };
    const q = document.getElementById('ocrHQty_' + uid);
    const c = document.getElementById('ocrHCost_' + uid);
    const p = document.getElementById('ocrHPrice_' + uid);
    if (q) row.quantity = q.value;
    if (c) row.cost_price = c.value;
    if (p) row.current_price = p.value;
    const el = document.getElementById('ocrHPnl_' + uid);
    if (el) el.innerHTML = holdingPnlInner(row);
}

function ocrHoldingCardHtml(row) {
    const low = (f) => (row.conf[f] != null && row.conf[f] < 0.7) ? 'border-amber-400 bg-amber-50/40' : '';
    const pnlHint = `<span id="ocrHPnl_${row.uid}" class="text-[11px]">${holdingPnlInner(row)}</span>`;
    return `
        <div class="ocr-card bg-surface-strong border border-hairline rounded-2xl p-4 mb-3" id="ocrHCard_${row.uid}" data-uid="${row.uid}">
            <div class="flex items-center gap-2 mb-3">
                <span class="ocr-dot w-2.5 h-2.5 rounded-full bg-muted" id="ocrHDot_${row.uid}"></span>
                <span class="text-xs text-muted font-mono" id="ocrHStatus_${row.uid}">待确认</span>
                <span class="text-[11px] text-muted">置信度 ${confSummary(row.conf)}</span>
                ${pnlHint}
                <label class="ml-auto flex items-center gap-1 text-xs text-muted cursor-pointer">
                    <input type="checkbox" id="ocrHIgnore_${row.uid}"> 忽略
                </label>
                <button onclick="ocrHoldingDeleteRow('${row.uid}')" class="text-xs text-negative hover:underline ml-1">删除</button>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
                <div>
                    <label class="block text-[11px] text-muted mb-1">名称</label>
                    <input id="ocrHName_${row.uid}" value="${escapeAttr(row.name)}" placeholder="如：贵州茅台" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg ${low('name')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">代码</label>
                    <input id="ocrHCode_${row.uid}" value="${escapeAttr(row.code)}" placeholder="600519" maxlength="6" readonly class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg font-mono text-muted ${low('code')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">持仓数量</label>
                    <input id="ocrHQty_${row.uid}" value="${escapeAttr(row.quantity)}" placeholder="100" type="number" min="1" oninput="recomputeHoldingPnl('${row.uid}')" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg font-mono ${low('quantity')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">成本价</label>
                    <input id="ocrHCost_${row.uid}" value="${escapeAttr(row.cost_price)}" placeholder="0.00" type="number" step="0.01" min="0" oninput="recomputeHoldingPnl('${row.uid}')" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg font-mono ${low('cost_price')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">现价（选填）</label>
                    <input id="ocrHPrice_${row.uid}" value="${escapeAttr(row.current_price)}" placeholder="—/待刷新" type="number" step="0.01" min="0" readonly class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg font-mono text-muted ${low('current_price')}">
                </div>
                <div>
                    <label class="block text-[11px] text-muted mb-1">板块</label>
                    <select id="ocrHSector_${row.uid}" class="w-full px-2 py-1.5 text-sm bg-canvas border border-hairline rounded-lg">
                        ${SECTORS.map(s => `<option value="${s}" ${row.sector === s ? 'selected' : ''}>${s}</option>`).join('')}
                    </select>
                </div>
            </div>
        </div>`;
}

export function openOcrHoldingModal() {
    const cash = ocrHoldingScreenshot.available_cash;
    const assets = ocrHoldingScreenshot.total_assets;
    const fmt = (v) => '¥' + Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const metaBanner = (cash != null || assets != null)
        ? `<div class="mx-6 mt-3 p-3 rounded-xl bg-surface-soft border border-hairline text-sm text-muted">
               截图账户信息：
               ${cash != null ? `可用资金 <span class="text-ink font-mono">${fmt(cash)}</span>` : ''}
               ${cash != null && assets != null ? ' · ' : ''}
               ${assets != null ? `总资产 <span class="text-ink font-mono">${fmt(assets)}</span>` : ''}
               ${cash != null ? '<div class="mt-1 text-[11px]">导入后将写入客户「可用资金」；总资产为只读参考（系统按 持仓市值+可用资金 计算，不回写）。</div>' : ''}
           </div>`
        : '';
    const mode = ocrHoldingMergeMode; // 默认 merge（合并）
    const modalHtml = `
        <div id="ocrHoldingModal" class="fixed inset-0 z-[100]">
            <div class="absolute inset-0 modal-backdrop" onclick="closeOcrHoldingModal()"></div>
            <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-2xl mx-4">
                <div class="bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up max-h-[92vh] flex flex-col">
                    <div class="px-6 pt-5 pb-4 relative border-b border-hairline">
                        <button onclick="closeOcrHoldingModal()" class="absolute top-4 right-4 w-8 h-8 rounded-full bg-surface-strong flex items-center justify-center text-muted hover:text-ink hover:bg-hairline transition-all">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                        </button>
                        <div>
                            <h3 class="text-lg font-semibold text-ink">持仓截图识别预览</h3>
                            <p id="ocrHSub" class="text-sm text-muted mt-1">共 <span id="ocrHCount">${ocrHoldingRows.length}</span> 只待确认 · 导入将<span class="${mode === 'merge' ? 'text-primary' : 'text-negative'} font-medium">${mode === 'merge' ? '合并' : '整体替换'}</span>当前持仓并生成「调整」记录</p>
                            <div class="flex flex-wrap items-center gap-2 mt-2">
                                <span class="text-xs text-muted">导入方式</span>
                                <div class="inline-flex rounded-lg bg-surface-strong p-0.5 text-xs font-medium" id="ocrModeToggle">
                                    <button type="button" data-mode="merge" class="px-3 py-1 rounded-md transition-colors ${mode === 'merge' ? 'bg-primary text-white' : 'text-muted hover:text-ink'}">合并持仓</button>
                                    <button type="button" data-mode="overwrite" class="px-3 py-1 rounded-md transition-colors ${mode === 'overwrite' ? 'bg-negative text-white' : 'text-muted hover:text-ink'}">覆盖持仓</button>
                                </div>
                                <span id="ocrModeHint" class="text-[11px] text-muted">${mode === 'merge' ? '按代码合并：新增/覆盖，未涉及保留' : '整体替换：截图未涉及的原有持仓会被清除'}</span>
                            </div>
                        </div>
                    </div>
                    ${metaBanner}
                    <div id="ocrHoldingProgressWrap" class="hidden px-6 pt-3">
                        <div class="h-2 w-full bg-surface-strong rounded-full overflow-hidden">
                            <div id="ocrHoldingProgressBar" class="h-full bg-primary transition-all duration-300" style="width:0%"></div>
                        </div>
                        <p id="ocrHoldingProgressText" class="text-xs text-muted mt-1">录入进度 0 / 0</p>
                    </div>
                    <div id="ocrHoldingList" class="px-6 py-4 overflow-y-auto flex-1" style="max-height:60vh">
                        ${ocrHoldingRows.map(ocrHoldingCardHtml).join('')}
                    </div>
                    <div class="px-6 py-4 border-t border-hairline flex gap-3">
                        <button onclick="closeOcrHoldingModal()" class="flex-1 px-4 py-3 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors">取消</button>
                        <button id="ocrHoldingImportBtn" onclick="importOcrHoldings()" class="flex-1 px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors shadow-sm shadow-primary/25">确认并${mode === 'merge' ? '合并持仓' : '整体更新持仓'}</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    const existing = document.getElementById('ocrHoldingModal');
    if (existing) existing.remove();
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    document.body.style.overflow = 'hidden';

    // 导入方式切换：切到覆盖需二次确认（整体替换有破坏性）
    const toggle = document.getElementById('ocrModeToggle');
    if (toggle) {
        toggle.querySelectorAll('button[data-mode]').forEach(btn => {
            btn.addEventListener('click', () => {
                const next = btn.getAttribute('data-mode');
                if (next === ocrHoldingMergeMode) return;
                if (next === 'overwrite') {
                    const ok = confirm('覆盖持仓将「整体替换」该客户全部持仓，截图未包含的原有持仓会被清除（不可恢复）。\n确定切换到覆盖模式吗？');
                    if (!ok) return; // 维持合并
                }
                ocrHoldingMergeMode = next;
                updateHoldingModeUI();
            });
        });
    }

    ocrHoldingRows.forEach(row => {
        const nameEl = document.getElementById('ocrHName_' + row.uid);
        if (nameEl) {
            nameEl.addEventListener('blur', () => ocrResolveHoldingName(row.uid));
            // 名称输入即时反查（防抖 400ms）：回填代码+实时现价+板块
            let t;
            nameEl.addEventListener('input', () => {
                clearTimeout(t);
                t = setTimeout(() => ocrResolveHoldingName(row.uid), 400);
            });
        }
    });
}

// 同步 toggle / 副标题 / 按钮文案 到当前 ocrHoldingMergeMode
function updateHoldingModeUI() {
    const toggle = document.getElementById('ocrModeToggle');
    if (toggle) toggle.querySelectorAll('button[data-mode]').forEach(b => {
        const m = b.getAttribute('data-mode');
        const active = m === ocrHoldingMergeMode;
        b.className = 'px-3 py-1 rounded-md transition-colors ' + (active ? (m === 'merge' ? 'bg-primary text-white' : 'bg-negative text-white') : 'text-muted hover:text-ink');
    });
    const hint = document.getElementById('ocrModeHint');
    if (hint) hint.textContent = ocrHoldingMergeMode === 'merge' ? '按代码合并：新增/覆盖，未涉及保留' : '整体替换：截图未涉及的原有持仓会被清除';
    const sub = document.getElementById('ocrHSub');
    if (sub) sub.innerHTML = `共 <span id="ocrHCount">${ocrHoldingRows.length}</span> 只待确认 · 导入将<span class="${ocrHoldingMergeMode === 'merge' ? 'text-primary' : 'text-negative'} font-medium">${ocrHoldingMergeMode === 'merge' ? '合并' : '整体替换'}</span>当前持仓并生成「调整」记录`;
    const btn = document.getElementById('ocrHoldingImportBtn');
    if (btn) btn.textContent = '确认并' + (ocrHoldingMergeMode === 'merge' ? '合并持仓' : '整体更新持仓');
}

export function closeOcrHoldingModal() {
    const modal = document.getElementById('ocrHoldingModal');
    if (modal) modal.remove();
    document.body.style.overflow = '';
    ocrHoldingRows = [];
    ocrHoldingImporting = false;
}

// ============================================================
// OCR 识别加载遮罩（独立于 #toast，避免被「大盘数据刷新成功」等周期 toast 覆盖）
// 全屏半透明背景拦截点击 = 识别期间禁用用户其他操作。
// ============================================================
export function showOcrLoading(msg = '正在识别截图，请稍候…') {
    hideOcrLoading();
    const el = document.createElement('div');
    el.id = 'ocrLoadingOverlay';
    el.className = 'fixed inset-0 z-[200] flex items-center justify-center';
    el.innerHTML = `
        <div class="absolute inset-0 bg-black/50 backdrop-blur-sm"></div>
        <div class="relative bg-canvas rounded-3xl shadow-2xl px-10 py-8 flex flex-col items-center gap-4 max-w-xs mx-4 animate-fade-in-up">
            <div class="w-14 h-14 rounded-full border-4 border-primary/30 border-t-primary animate-spin"></div>
            <div id="ocrLoadingText" class="text-base font-medium text-ink text-center">${msg}</div>
            <div class="text-xs text-muted">识别期间请勿进行其他操作</div>
        </div>`;
    document.body.appendChild(el);
}

export function hideOcrLoading() {
    document.getElementById('ocrLoadingOverlay')?.remove();
}

export function ocrHoldingDeleteRow(uid) {
    ocrHoldingRows = ocrHoldingRows.filter(r => r.uid !== uid);
    const card = document.getElementById('ocrHCard_' + uid);
    if (card) card.remove();
    const c = document.getElementById('ocrHCount');
    if (c) c.textContent = String(ocrHoldingRows.length);
}

async function ocrResolveHoldingName(uid) {
    const row = ocrHoldingRows.find(r => r.uid === uid);
    if (!row) return;
    const nameEl = document.getElementById('ocrHName_' + uid);
    const name = nameEl?.value.trim();
    if (!name) return;
    // 反查中标记
    const dot = document.getElementById('ocrHDot_' + uid);
    const st = document.getElementById('ocrHStatus_' + uid);
    if (dot) dot.className = 'ocr-dot w-2.5 h-2.5 rounded-full bg-primary animate-pulse-soft';
    if (st) st.textContent = '反查中…';
    try {
        const { results } = await searchStocksApi(name, 5);
        const exact = (results || []).find(s => s.name === name) || (results || [])[0];
        if (exact) {
            _applyHoldingLookup(row, exact);
        } else {
            // 名称搜不到代码 → 清空代码/现价，导入前校验会拦下，提示改名或忽略
            row.code = '';
            row.current_price = '';
            const codeEl = document.getElementById('ocrHCode_' + uid);
            if (codeEl) codeEl.value = '';
            const priceEl = document.getElementById('ocrHPrice_' + uid);
            if (priceEl) priceEl.value = '';
            if (dot) dot.className = 'ocr-dot w-2.5 h-2.5 rounded-full bg-negative';
            if (st) st.textContent = '未匹配代码，请改名或勾选「忽略」';
        }
    } catch { /* 静默 */ }
    finally {
        if (dot) dot.className = 'ocr-dot w-2.5 h-2.5 rounded-full bg-muted';
        if (st) st.textContent = '待确认';
    }
}

function readOcrHoldingRow(uid) {
    const g = (id) => document.getElementById(id)?.value ?? '';
    return {
        uid,
        name: g('ocrHName_' + uid).trim(),
        code: g('ocrHCode_' + uid).trim(),
        quantity: parseInt(g('ocrHQty_' + uid)),
        cost_price: g('ocrHCost_' + uid).trim(),
        current_price: g('ocrHPrice_' + uid).trim(),
        sector: g('ocrHSector_' + uid),
        ignore: document.getElementById('ocrHIgnore_' + uid)?.checked || false,
    };
}

function markHoldingRowError(uid, msg) {
    const card = document.getElementById('ocrHCard_' + uid);
    if (!card) return;
    card.classList.add('animate-shake', 'border-negative');
    const st = document.getElementById('ocrHStatus_' + uid);
    if (st) st.textContent = '校验未过：' + msg;
    const dot = document.getElementById('ocrHDot_' + uid);
    if (dot) dot.className = 'ocr-dot w-2.5 h-2.5 rounded-full bg-negative';
}

function setHoldingProgress(done, total) {
    const bar = document.getElementById('ocrHoldingProgressBar');
    const txt = document.getElementById('ocrHoldingProgressText');
    if (bar) bar.style.width = total ? (done / total * 100) + '%' : '0%';
    if (txt) txt.textContent = `录入进度 ${done} / ${total}`;
}

// 从若干候选价中取第一个有限且 >0 的值（后端 price 要求 >0）；都没有返回 null
function posPrice(...vals) {
    for (const v of vals) {
        const n = parseFloat(v);
        if (isFinite(n) && n > 0) return n;
    }
    return null;
}

export async function importOcrHoldings() {
    if (!guardEdit()) return;
    if (ocrHoldingImporting) return;
    const currentClient = getCurrentClient();
    if (!currentClient) { showToast('❌ 未选择客户', 'error'); return; }

    const rows = ocrHoldingRows.map(r => readOcrHoldingRow(r.uid));
    const toImport = rows.filter(r => !r.ignore);

    for (const r of toImport) {
        if (!/^\d{6}$/.test(r.code)) { markHoldingRowError(r.uid, '代码须为6位'); showToast(`❌ 第 ${labelOfHolding(r.uid)} 行代码无效`, 'error'); return; }
        if (!(r.quantity > 0)) { markHoldingRowError(r.uid, '数量无效'); showToast(`❌ 第 ${labelOfHolding(r.uid)} 行数量无效`, 'error'); return; }
        const cp = parseFloat(r.cost_price);
        if (r.cost_price === '' || isNaN(cp) || cp < 0) { markHoldingRowError(r.uid, '成本价无效'); showToast(`❌ 第 ${labelOfHolding(r.uid)} 行成本价无效`, 'error'); return; }
    }
    if (!toImport.length) { showToast('没有需要导入的持仓', 'warn'); return; }

    // 模式相关确认：合并 → 提示将保留未涉及持仓；覆盖 → 提示将整体替换
    let confirmMsg;
    if (ocrHoldingMergeMode === 'merge') {
        confirmMsg = '持仓截图将以「合并」方式导入：\n· 按代码新增/覆盖持仓（覆盖项数量、成本价、板块一律以截图为准）\n· 未涉及的原有持仓予以保留\n· 按分类生成复盘记录：新增/加仓=买入(红)，减仓=卖出(绿)，仅成本价变=调整(黄)；完全未变的持仓跳过\n\n确定继续吗？';
    } else {
        confirmMsg = '持仓截图导入将「整体替换」当前客户持仓（未涉及的原有持仓将被清空），并为每只股票生成一条「调整」复盘记录。\n确定继续吗？';
    }
    const ok = confirm(confirmMsg);
    if (!ok) return;

    ocrHoldingImporting = true;
    document.getElementById('ocrHoldingProgressWrap')?.classList.remove('hidden');
    const total = toImport.length;
    setHoldingProgress(0, total);

    try {
        // 导入前持仓（前态），用于合并模式分类；必须在写入前捕获
        const prevByCode = new Map((currentClient.positions || []).map(p => [p.code, p]));
        // ① 组装持仓 payload（覆盖=仅截图集；合并=按代码合并现有持仓）
        let positionsPayload;
        if (ocrHoldingMergeMode === 'merge') {
            const existing = (currentClient.positions || []).map(p => ({
                name: p.name,
                code: p.code,
                sector: p.sector,
                quantity: p.quantity,
                cost_price: parseFloat(p.costPrice) || 0,
            }));
            const byCode = new Map(existing.map(p => [p.code, p]));
            for (const r of toImport) {
                byCode.set(r.code, {
                    name: r.name || r.code,
                    code: r.code,
                    sector: SECTORS.includes(r.sector) ? r.sector : '其他',
                    quantity: r.quantity,
                    cost_price: parseFloat(r.cost_price),
                });
            }
            positionsPayload = [...byCode.values()];
        } else {
            positionsPayload = toImport.map(r => ({
                name: r.name || r.code,
                code: r.code,
                sector: SECTORS.includes(r.sector) ? r.sector : '其他',
                quantity: r.quantity,
                cost_price: parseFloat(r.cost_price),
            }));
        }
        await updateClientPositions(currentClient.id, positionsPayload);

        // ② 生成复盘记录：合并模式按「前态 vs 导入态」分类打标，覆盖模式维持全黄「调整」
        //    可用资金不参与任何单只持仓判定（仅客户总体数据，见③）。
        //    记录数量=交易差额（加仓=增持数、减仓=减持数；新增无前态→全额；仅成本变/覆盖→全量）。
        //    成交价：新增=成本价；加仓=由成本+股数精确反推；减仓=快照现价（日均刷新→误差小）；均保证 >0。
        const records = [];
        for (const r of toImport) {
            const cp = parseFloat(r.cost_price) || 0;
            const pxRaw = (r.current_price !== '' && !isNaN(parseFloat(r.current_price))) ? parseFloat(r.current_price) : null;
            let action = 'adjust';
            let price = null;
            // 复盘记录数量=交易差额（非持仓全量）；新增无前态→差额=全量，仅成本变/覆盖→全量
            let qty = r.quantity;
            if (ocrHoldingMergeMode === 'merge') {
                const prev = prevByCode.get(r.code);
                if (!prev) {
                    action = 'buy';                                   // 新增（差额=全量）
                    price = posPrice(cp) || 0.01;
                } else {
                    const qtyOld = prev.quantity;
                    const costOld = parseFloat(prev.costPrice) || 0;
                    const qtyNew = r.quantity;
                    const costNew = cp;
                    if (qtyNew > qtyOld) {
                        // 加仓：成交价由成本+股数反推；数量=差额
                        const dq = qtyNew - qtyOld;
                        const buyPrice = (qtyNew * costNew - qtyOld * costOld) / dq;
                        action = 'buy';
                        qty = dq;
                        price = posPrice(buyPrice, costNew) || 0.01;
                    } else if (qtyNew < qtyOld) {
                        // 减仓：均价法下成本不变无法反推，成交价取快照现价；数量=差额
                        action = 'sell';
                        qty = qtyOld - qtyNew;
                        price = posPrice(pxRaw, costNew) || 0.01;
                    } else if (costNew !== costOld) {
                        // 仅成本价变（股数同）：调整，数量用全量
                        action = 'adjust';
                        price = posPrice(costNew) || 0.01;
                    } else {
                        // 完全未变：跳过，不生成记录
                        continue;
                    }
                }
            } else {
                action = 'adjust';                                 // 覆盖模式维持原行为（全量）
                price = posPrice(pxRaw, cp) || 0.01;
            }
            records.push({
                code: r.code,
                name: r.name || r.code,
                quantity: qty,
                price,
                cost_price: cp,
                trade_date: new Date().toISOString().slice(0, 10),
                action,
            });
        }
        for (const rec of records) {
            await createAdjustRecord(currentClient.id, {
                code: rec.code,
                name: rec.name,
                quantity: rec.quantity,
                price: rec.price,
                cost_price: rec.cost_price,
                trade_date: rec.trade_date,
            }, rec.action);
        }

        // ③ 可用资金（截图有则写入，属客户总体必要数据；total_assets 由系统按「可用资金+持仓资产」计算、不回写）
        if (ocrHoldingScreenshot.available_cash != null) {
            await updateClientRemote(currentClient.id, { available_cash: ocrHoldingScreenshot.available_cash });
        }

        const done = records.length;
        setHoldingProgress(done, done);
        finishHoldingImport(done);
    } catch (e) {
        ocrHoldingImporting = false;
        showToast('❌ 持仓导入失败：' + (e?.message || '未知错误'), 'error', 6000);
    }
}

function finishHoldingImport(done) {
    const currentClient = getCurrentClient();
    if (currentClient) {
        Promise.all([fetchClientPortfolio(currentClient.id), fetchClientPnlHistory(currentClient.id)])
            .then(([portfolio, pnlHistory]) => {
                if (portfolio) {
                    syncClientState(portfolio);
                    refreshAll(portfolio, pnlHistory);
                    renderClientProfile(portfolio);
                    refreshClientSummaries().catch(() => {});
                } else {
                    refreshAll();
                }
            })
            .catch(() => refreshAll());
    }
    const bar = document.getElementById('ocrHoldingProgressWrap');
    if (bar) bar.classList.add('hidden');
    const list = document.getElementById('ocrHoldingList');
    if (list) {
        list.innerHTML = `
            <div class="text-center py-10">
                <div class="text-2xl mb-2">✅</div>
                <div class="text-sm text-ink">已更新 ${done} 只持仓</div>
                <div class="text-xs text-muted mt-1">列表已清空</div>
            </div>`;
    }
    const modeLabel = ocrHoldingMergeMode === 'merge' ? '合并更新' : '整体替换';
    showToast(`✅ 已${modeLabel} ${done} 只持仓${ocrHoldingScreenshot.available_cash != null ? '，可用资金已写入' : ''}`, 'success');
    const btn = document.getElementById('ocrHoldingImportBtn');
    if (btn) { btn.textContent = '完成'; btn.onclick = () => closeOcrHoldingModal(); }
}
