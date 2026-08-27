// ============================================================
// 身份管理弹窗：修改个人密码
// ============================================================
import { showToast } from '../core/ui.js';
import {
    changePassword, passwordStrengthScore,
} from '../services/authService.js';
import { renderAuthModal } from './auth.js';

// ---- 打开身份管理弹窗 ----
export function openIdentityModal() {
    const modal = document.getElementById('identityModal');
    if (!modal) return;
    renderAuthModal();
    renderPasswordChanger();
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

// ---- 关闭身份管理弹窗 ----
export function closeIdentityModal() {
    const modal = document.getElementById('identityModal');
    if (!modal) return;
    modal.classList.add('hidden');
    document.body.style.overflow = '';
}

// ---- 修改密码区块：注入到 authUserPanel 退出登录按钮上方 ----
function renderPasswordChanger() {
    const panel = document.getElementById('authUserPanel');
    if (!panel) return;
    // 已注入则跳过
    if (document.getElementById('pwdChangerSection')) return;

    const section = document.createElement('div');
    section.id = 'pwdChangerSection';
    section.className = 'mt-4 rounded-xl bg-surface-soft border border-hairline p-4';
    section.innerHTML = `
        <div class="flex items-center justify-between mb-3">
            <div class="text-sm font-semibold text-ink">修改密码</div>
            <button id="pwdToggler" class="text-xs text-primary hover:text-primary-active transition-colors">展开</button>
        </div>
        <form id="pwdChangeForm" class="hidden space-y-3 pt-1">
            <div>
                <label class="block text-xs font-medium text-body mb-1">原密码</label>
                <input id="pwdOld" type="password" autocomplete="current-password" class="w-full px-3 py-2 bg-canvas border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" placeholder="请输入原密码">
            </div>
            <div>
                <label class="block text-xs font-medium text-body mb-1">新密码</label>
                <input id="pwdNew" type="password" autocomplete="new-password" class="w-full px-3 py-2 bg-canvas border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" placeholder="至少 6 位，建议字母+数字">
                <div class="mt-2 h-1.5 w-full rounded-full bg-hairline overflow-hidden">
                    <div id="pwdStrengthBar" class="h-full w-0 bg-negative transition-all duration-200"></div>
                </div>
                <div class="mt-1 flex justify-between text-[10px]">
                    <span id="pwdStrengthLabel" class="text-muted">强度：极弱</span>
                    <span class="text-muted">建议 8 位以上 + 大小写字母/数字/特殊字符</span>
                </div>
            </div>
            <div>
                <label class="block text-xs font-medium text-body mb-1">确认新密码</label>
                <input id="pwdConfirm" type="password" autocomplete="new-password" class="w-full px-3 py-2 bg-canvas border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" placeholder="再次输入新密码">
            </div>
            <div class="flex gap-2 pt-1">
                <button type="submit" id="pwdSubmitBtn" class="flex-1 px-3 py-2 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">确认修改</button>
                <button type="button" id="pwdCancelBtn" class="px-3 py-2 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors">取消</button>
            </div>
        </form>
    `;
    // 插入到「退出登录」按钮前面
    const logoutBtn = panel.querySelector('button[onclick="handleLogout()"]');
    if (logoutBtn) {
        panel.insertBefore(section, logoutBtn);
    } else {
        panel.appendChild(section);
    }

    const toggler = section.querySelector('#pwdToggler');
    const form = section.querySelector('#pwdChangeForm');
    const cancelBtn = section.querySelector('#pwdCancelBtn');
    toggler?.addEventListener('click', () => {
        const isHidden = form.classList.contains('hidden');
        form.classList.toggle('hidden');
        toggler.textContent = isHidden ? '收起' : '展开';
    });
    cancelBtn?.addEventListener('click', () => {
        form.classList.add('hidden');
        toggler.textContent = '展开';
        form.reset();
        updateStrengthBar();
    });

    // 强度实时更新
    const newInput = section.querySelector('#pwdNew');
    newInput?.addEventListener('input', updateStrengthBar);

    form?.addEventListener('submit', onPwdSubmit);
}

function updateStrengthBar() {
    const input = document.getElementById('pwdNew');
    const bar = document.getElementById('pwdStrengthBar');
    const label = document.getElementById('pwdStrengthLabel');
    if (!bar || !label) return;
    const pwd = input?.value || '';
    const { score, label: sLabel } = passwordStrengthScore(pwd);
    const widths = ['0%', '25%', '50%', '75%', '100%'];
    const colors = ['bg-negative', 'bg-negative', 'bg-warning', 'bg-positive', 'bg-primary'];
    bar.className = `h-full transition-all duration-200 ${colors[score]}`;
    bar.style.width = widths[score];
    label.textContent = '强度：' + sLabel;
}

// ---- 修改密码提交 ----
export async function handleChangePassword(event) {
    // 兼容 addEventListener 绑定和全局 onclick 两种绑定方式
    if (event) {
        event.preventDefault?.();
        event.stopPropagation?.();
    }
    const form = document.getElementById('pwdChangeForm');
    if (!form) return null;
    const oldEl = document.getElementById('pwdOld');
    const newEl = document.getElementById('pwdNew');
    const confirmEl = document.getElementById('pwdConfirm');
    const btn = document.getElementById('pwdSubmitBtn');
    const oldPwd = oldEl?.value || '';
    const newPwd = newEl?.value || '';
    const confirmPwd = confirmEl?.value || '';

    if (!oldPwd) { showToast('请输入原密码', 'warning'); return null; }
    if (!newPwd) { showToast('请输入新密码', 'warning'); return null; }
    if (newPwd.length < 6) { showToast('新密码长度至少 6 位', 'warning'); return null; }
    if (newPwd !== confirmPwd) { showToast('两次输入的新密码不一致', 'error'); return null; }
    if (newPwd === oldPwd) { showToast('新密码不能与原密码相同', 'warning'); return null; }

    const originalText = btn?.innerHTML;
    try {
        if (btn) { btn.disabled = true; btn.innerHTML = '修改中...'; }
        const res = await changePassword(oldPwd, newPwd);
        form.reset();
        updateStrengthBar();
        const msg = `✅ 密码修改成功（强度：${res?.strength_label ?? '未知'}）`;
        showToast(msg, 'success');
        const toggler = document.getElementById('pwdToggler');
        if (toggler) { toggler.click(); }
        return res;
    } catch (e) {
        showToast('❌ 修改失败：' + (e?.message || '请检查原密码是否正确'), 'error');
        return null;
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = originalText || '确认修改'; }
    }
}

// 全局挂载：表单 submit 绑定到这个函数，避免重复初始化
if (typeof window !== 'undefined' && !window.__pwdChangeBound) {
    window.__pwdChangeBound = true;
    // 把 submit 事件绑定到 DOMContentLoaded 后
    const bind = () => {
        const panel = document.getElementById('authUserPanel');
        if (!panel) return;
        // 使用事件委托：submit 时捕获处理
        panel.addEventListener('submit', e => {
            if (e.target && e.target.id === 'pwdChangeForm') {
                onPwdSubmit(e);
            }
        });
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
}

function onPwdSubmit(event) {
    event.preventDefault();
    event.stopPropagation();
    return handleChangePassword(event);
}
