// ============================================================
// 客户标签选择器（四类：资金/偏好/策略限制/交易频次）
// 供开户向导与工作台标签编辑共用，所有组统一药丸标签样式：
// - single 组（资金/偏好/交易频次）：组内互斥单选，再点一次取消
// - multi 组（策略限制）：各项独立多选，可组合勾选多项
// 选中态蓝底白字，未选灰底描边，选择状态一眼可辨。
// ============================================================
import { CLIENT_TAG_GROUPS, ALL_MANAGED_TAGS } from '../core/config.js';

// 当前已选受控标签集合（仅含标签体系内的选项）
let selected = new Set();

/** 用已有标签初始化选择状态（自由标签自动忽略） */
export function initTagSelector(existingTags = []) {
    selected = new Set((existingTags || []).filter(t => ALL_MANAGED_TAGS.includes(t)));
}

/** 读取当前选中的受控标签数组 */
export function getSelectedManagedTags() {
    return Array.from(selected);
}

/** 渲染到 #clientTagSelector 容器（每次点击后整体重渲染，状态来自内存） */
export function renderTagSelector() {
    const el = document.getElementById('clientTagSelector');
    if (!el) return;

    el.innerHTML = CLIENT_TAG_GROUPS.map(g => `
        <div>
            <div class="text-xs font-medium text-muted mb-1.5">${g.label}${g.type === 'multi' ? '（可多选）' : ''}</div>
            <div class="flex flex-wrap gap-2">
                ${g.options.map(opt => {
                    const on = selected.has(opt);
                    return `<button type="button" data-tag="${opt}" data-group="${g.key}"
                        class="tag-option-btn px-3 py-1.5 rounded-full border text-xs font-medium transition-colors ${on ? 'bg-primary text-white border-primary' : 'bg-surface-strong border-hairline text-body hover:border-primary/40 hover:text-primary'}">${opt}</button>`;
                }).join('')}
            </div>
        </div>
    `).join('');

    // 绑定点击：single 组内互斥，再点一次取消；multi 组独立开关
    el.querySelectorAll('[data-tag]').forEach(btn => {
        btn.addEventListener('click', () => {
            const tag = btn.dataset.tag;
            const group = CLIENT_TAG_GROUPS.find(g => g.key === btn.dataset.group);
            if (group && group.type === 'single') {
                group.options.forEach(o => { if (o !== tag) selected.delete(o); });
            }
            selected.has(tag) ? selected.delete(tag) : selected.add(tag);
            renderTagSelector();
        });
    });
}
