// ============================================================
// 手续费设置：获取/保存费率配置 + 实时预览
// ============================================================
import { showToast } from '../core/ui.js';

let currentFees = null;

/** 获取当前手续费配置 */
export async function fetchTradingFees() {
    try {
        const resp = await fetch('/api/settings/trading-fees', {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('stock_review_token')}` }
        });
        if (resp.ok) {
            currentFees = await resp.json();
            return currentFees;
        }
    } catch (e) {
        console.warn('获取手续费配置失败:', e);
    }
    return null;
}

/** 保存手续费配置 */
export async function saveTradingFees(fees) {
    try {
        const resp = await fetch('/api/settings/trading-fees', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${localStorage.getItem('stock_review_token')}`
            },
            body: JSON.stringify(fees)
        });
        if (resp.ok) {
            currentFees = await resp.json();
            return currentFees;
        }
    } catch (e) {
        console.warn('保存手续费配置失败:', e);
    }
    return null;
}

/** 打开手续费设置模态框 */
export async function openFeeSettings() {
    const modal = document.getElementById('feeSettingsModal');
    if (!modal) return;

    // 加载当前配置
    const fees = await fetchTradingFees();
    if (fees) {
        document.getElementById('feeCommission').value = fees.commission_rate;
        document.getElementById('feeMinCommission').value = fees.min_commission;
        document.getElementById('feeStampTax').value = fees.stamp_tax_rate;
        document.getElementById('feeTransferFee').value = fees.transfer_fee_rate;
        updateFeePreview();
    }

    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    // 绑定实时预览
    ['feeCommission', 'feeMinCommission', 'feeStampTax', 'feeTransferFee'].forEach(id => {
        document.getElementById(id).addEventListener('input', updateFeePreview);
    });
}

/** 关闭手续费设置模态框 */
export function closeFeeSettings() {
    const modal = document.getElementById('feeSettingsModal');
    if (modal) modal.classList.add('hidden');
    document.body.style.overflow = '';
}

/** 更新费用预览 */
function updateFeePreview() {
    const commission = parseFloat(document.getElementById('feeCommission').value) || 0;
    const minCommission = parseFloat(document.getElementById('feeMinCommission').value) || 0;
    const stampTax = parseFloat(document.getElementById('feeStampTax').value) || 0;
    const transferFee = parseFloat(document.getElementById('feeTransferFee').value) || 0;

    // 以10万元交易为例
    const amount = 100000;
    
    // 买入费用
    const buyCommission = Math.max(amount * commission, minCommission);
    const buyTransfer = amount * transferFee;
    const buyTotal = buyCommission + buyTransfer;
    
    // 卖出费用
    const sellCommission = Math.max(amount * commission, minCommission);
    const sellStamp = amount * stampTax;
    const sellTransfer = amount * transferFee;
    const sellTotal = sellCommission + sellStamp + sellTransfer;

    const content = document.getElementById('feePreviewContent');
    if (content) {
        content.innerHTML = `
            <div class="flex justify-between"><span class="text-muted">买入佣金</span><span class="font-mono">¥${buyCommission.toFixed(2)}</span></div>
            <div class="flex justify-between"><span class="text-muted">买入过户费</span><span class="font-mono">¥${buyTransfer.toFixed(2)}</span></div>
            <div class="flex justify-between text-body"><span>买入总费用</span><span class="font-mono font-semibold">¥${buyTotal.toFixed(2)}</span></div>
            <hr class="my-1 border-hairline">
            <div class="flex justify-between"><span class="text-muted">卖出佣金</span><span class="font-mono">¥${sellCommission.toFixed(2)}</span></div>
            <div class="flex justify-between"><span class="text-muted">卖出印花税</span><span class="font-mono">¥${sellStamp.toFixed(2)}</span></div>
            <div class="flex justify-between"><span class="text-muted">卖出过户费</span><span class="font-mono">¥${sellTransfer.toFixed(2)}</span></div>
            <div class="flex justify-between text-body"><span>卖出总费用</span><span class="font-mono font-semibold">¥${sellTotal.toFixed(2)}</span></div>
            <div class="flex justify-between text-ink pt-1 border-t border-hairline"><span>往返总费用</span><span class="font-mono font-semibold text-primary">¥${(buyTotal + sellTotal).toFixed(2)}</span></div>
        `;
    }
}

/** 保存手续费设置 */
export async function saveFeeSettings() {
    const commission_rate = parseFloat(document.getElementById('feeCommission').value);
    const min_commission = parseFloat(document.getElementById('feeMinCommission').value);
    const stamp_tax_rate = parseFloat(document.getElementById('feeStampTax').value);
    const transfer_fee_rate = parseFloat(document.getElementById('feeTransferFee').value);

    // 验证
    if (commission_rate < 0 || stamp_tax_rate < 0 || transfer_fee_rate < 0) {
        showToast('❌ 费率不能为负数', 'error');
        return;
    }
    if (min_commission < 0) {
        showToast('❌ 最低佣金不能为负数', 'error');
        return;
    }

    const result = await saveTradingFees({
        commission_rate,
        min_commission,
        stamp_tax_rate,
        transfer_fee_rate,
    });

    if (result) {
        showToast('✅ 手续费设置已保存', 'success');
        closeFeeSettings();
    } else {
        showToast('❌ 保存失败，请检查网络连接', 'error');
    }
}