// ============================================================
// 持仓弹窗组件：新增/编辑/加仓/减仓/删除
// ============================================================
import { getUserPositions, saveUserPositions, getCurrentClient, loadClients, executeAdjustApi, fetchClientPortfolio, fetchClientPnlHistory, searchStocksApi, syncClientState, revokePositionApi, createAdjustRecord } from '../services/clientService.js';
import { formatCurrency, formatNumber, getPnLColor } from '../core/formatters.js';
import { SECTORS } from '../core/config.js';
import { showToast, hideToast } from '../core/ui.js';
import { refreshAll, refreshRealtime } from './overview.js';
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

        // ---- 异步校准：并行拉取组合估值与盈亏历史，更新图表 ----
        try {
            const [portfolio, pnlHistory] = await Promise.all([
                fetchClientPortfolio(currentClient.id),
                fetchClientPnlHistory(currentClient.id),
            ]);
            if (portfolio) {
                syncClientState(portfolio);
                refreshAll(portfolio, pnlHistory);
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
