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

/** 渲染到 #clientTagSelector 容器（每次点击后整体重渲染，状态来自内存）
 *
 * 标签按钮由「文字 + × 移除按钮」组成：
 *  - 未选中：灰色胶囊 + 纯文字（无 ×），hover 蓝色边框
 *  - 选中：蓝底白字，右侧显示 ×；点击 × 或胶囊任何一处都能取消选中
 */
export function renderTagSelector() {
    const el = document.getElementById('clientTagSelector');
    if (!el) return;

    el.innerHTML = CLIENT_TAG_GROUPS.map(g => `
        <div>
            <div class="text-xs font-medium text-muted mb-1.5">${g.label}${g.type === 'multi' ? '（可多选）' : ''}</div>
            <div class="flex flex-wrap gap-2">
                ${g.options.map(opt => {
                    const on = selected.has(opt);
                    if (on) {
                        return `<div class="inline-flex items-center gap-1 pl-3 pr-2 py-1.5 rounded-full border text-xs font-medium transition-colors bg-primary text-white border-primary">
                            <span>${opt}</span>
                            <button type="button" data-tag="${opt}" data-group="${g.key}"
                                class="tag-remove w-4 h-4 rounded-full flex items-center justify-center text-white/90 hover:bg-white/20 transition-colors"
                                title="移除标签「${opt}」">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12"/></svg>
                            </button>
                        </div>`;
                    }
                    return `<button type="button" data-tag="${opt}" data-group="${g.key}"
                        class="tag-option-btn px-3 py-1.5 rounded-full border text-xs font-medium transition-colors bg-surface-strong border-hairline text-body hover:border-primary/40 hover:text-primary">${opt}</button>`;
                }).join('')}
            </div>
        </div>
    `).join('');

    // 绑定点击：single 组内互斥，再点一次取消；multi 组独立开关；× 移除按钮直接 remove
    el.querySelectorAll('[data-tag]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const tag = btn.dataset.tag;
            const group = CLIENT_TAG_GROUPS.find(g => g.key === btn.dataset.group);
            if (group && group.type === 'single') {
                group.options.forEach(o => { if (o !== tag) selected.delete(o); });
            }
            if (selected.has(tag)) selected.delete(tag);
            else selected.add(tag);
            renderTagSelector();
        });
    });
}
