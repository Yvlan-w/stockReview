// ============================================================
// 认证 UI 集成：登录表单、导航栏角色/未读展示、站内信中心、WebSocket。
// 说明：后端 Phase 2 提供 /api/auth、/api/notifications 与 /ws/{token}。
// ============================================================
import { showToast } from '../core/ui.js';
import {
    isLoggedIn, getToken, getUser, login, logout, roleLabel,
    fetchNotifications, fetchUnreadCount, markNotificationRead,
    markAllNotificationsRead, connectSocket,
} from '../services/authService.js';
import { getFilteredClients, setCurrentClient, loadClients } from '../services/clientService.js';
import { renderClientList, refreshClientDetail } from './workbench.js';
import { isWorkbenchVisible, canEdit, canAccessAdminPanel, canAccessOnboarding } from '../permissions/access.js';

let socket = null;

// ---- 权限驱动界面可见性 ----
function applyAccessControl() {
    const visible = isWorkbenchVisible();
    const workbench = document.getElementById('workbench');
    const placeholder = document.getElementById('workbenchPlaceholder');
    if (workbench) workbench.classList.toggle('hidden', !visible);
    if (placeholder) placeholder.classList.toggle('hidden', visible);

    // 编辑入口（添加持仓 / 恢复示例数据等）按角色显示
    const canEditNow = canEdit();
    document.querySelectorAll('[data-require-edit]').forEach(el => {
        el.classList.toggle('hidden', !canEditNow);
    });

    // 角色专属入口：管理后台（仅管理员）/ 客户开户（客服或管理员）
    const adminBtn = document.getElementById('adminPanelBtn');
    if (adminBtn) adminBtn.classList.toggle('hidden', !canAccessAdminPanel());
    const onboardingBtn = document.getElementById('onboardingBtn');
    if (onboardingBtn) onboardingBtn.classList.toggle('hidden', !canAccessOnboarding());

    // 客户相关图表 / 策略复盘按工作台可见性显示
    document.querySelectorAll('[data-scope="client"]').forEach(el => {
        el.classList.toggle('hidden', !visible);
    });

    if (!visible) {
        const list = document.getElementById('clientList');
        if (list) list.innerHTML = '';
        const profile = document.getElementById('clientProfileCard');
        if (profile) profile.innerHTML = '';
        const risk = document.getElementById('riskAlertCard');
        if (risk) risk.innerHTML = '';
    }
}

// 按当前角色重新渲染客户列表与详情
function renderWorkspaceData() {
    if (!isWorkbenchVisible()) return;
    renderClientList();
    const first = getFilteredClients()[0];
    setCurrentClient(first ? first.id : null);
    refreshClientDetail();
}

// ---- 导航栏用户状态渲染 ----
function renderNavState() {
    const user = getUser();
    const label = document.getElementById('authUserLabel');
    if (label) label.textContent = user ? roleLabel(user) : '身份管理';
    const bell = document.getElementById('notificationBell');
    if (bell) bell.classList.toggle('hidden', !isLoggedIn());
    if (!isLoggedIn()) hideNotificationPanel();
}

// ---- 身份弹窗登录/用户信息切换 ----
export function renderAuthModal() {
    const loginForm = document.getElementById('authLoginForm');
    const userPanel = document.getElementById('authUserPanel');
    const user = getUser();
    if (!user) {
        if (loginForm) loginForm.classList.remove('hidden');
        if (userPanel) userPanel.classList.add('hidden');
        return;
    }
    if (loginForm) loginForm.classList.add('hidden');
    if (userPanel) userPanel.classList.remove('hidden');

    const nameEl = document.getElementById('authUserName');
    const roleEl = document.getElementById('authUserRole');
    const usernameEl = document.getElementById('authUserUsername');
    if (nameEl) nameEl.textContent = user.name || user.username;
    if (roleEl) roleEl.textContent = roleLabel(user);
    if (usernameEl) usernameEl.textContent = user.username;
}

// ---- 登录 ----
export async function handleLogin(event) {
    if (event) event.preventDefault();
    const username = document.getElementById('loginUsername')?.value.trim();
    const password = document.getElementById('loginPassword')?.value;
    const btn = document.getElementById('loginSubmitBtn');
    if (!username || !password) {
        showToast('请输入用户名和密码', 'warning');
        return;
    }
    const original = btn?.innerHTML;
    if (btn) btn.disabled = true;
    try {
        await login(username, password);
        showToast('登录成功', 'success');
        renderNavState();
        renderAuthModal();
        applyAccessControl();
        await loadClients();
        renderWorkspaceData();
        refreshNotifications();
        connectRealtime();
        const form = document.getElementById('loginForm');
        if (form) form.reset();
        // 登录成功后自动关闭身份弹窗，避免遮挡导航栏铃铛
        const modal = document.getElementById('identityModal');
        if (modal) { modal.classList.add('hidden'); document.body.style.overflow = ''; }
    } catch (e) {
        showToast('登录失败：' + (e.message || '未知错误'), 'error');
    } finally {
        if (btn) { btn.disabled = false; if (original) btn.innerHTML = original; }
    }
}

// ---- 登出 ----
export async function handleLogout() {
    disconnectRealtime();
    await logout();
    renderNavState();
    renderAuthModal();
    await loadClients();  // 清空内存中的客户数据（已退出登录）
    applyAccessControl();
    hideNotificationPanel();
    showToast('已退出登录', 'success');
}

// ---- 站内信 ----
function hideNotificationPanel() {
    const panel = document.getElementById('notificationPanel');
    if (panel) panel.classList.add('hidden');
}

export function toggleNotificationPanel() {
    if (!isLoggedIn()) {
        showToast('请先登录', 'warning');
        return;
    }
    const panel = document.getElementById('notificationPanel');
    if (!panel) return;
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) refreshNotifications();
}

function notifTime(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function renderNotificationList(list) {
    const container = document.getElementById('notificationList');
    if (!container) return;
    if (!list || list.length === 0) {
        container.innerHTML = '<div class="px-4 py-8 text-center text-sm text-muted">暂无站内信</div>';
        return;
    }
    container.innerHTML = list.map(n => `
        <div class="px-4 py-3 hover:bg-surface-soft cursor-pointer ${n.is_read ? '' : 'bg-primary/5'}" onclick="openNotification('${n.id}')">
            <div class="flex items-start justify-between gap-2">
                <span class="text-sm font-medium text-ink leading-snug">${escapeHtml(n.title)}</span>
                ${n.is_read ? '' : '<span class="w-2 h-2 mt-1 rounded-full bg-primary shrink-0"></span>'}
            </div>
            <p class="text-xs text-body mt-1 line-clamp-2">${escapeHtml(n.content)}</p>
            <div class="text-[10px] text-muted-soft mt-1">${notifTime(n.created_at)}</div>
        </div>
    `).join('');
}

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

export async function refreshNotifications() {
    if (!isLoggedIn()) return;
    try {
        const [list, unread] = await Promise.all([fetchNotifications(), fetchUnreadCount()]);
        renderNotificationList(list);
        updateUnreadBadge(unread.unread);
    } catch {
        /* 未登录或网络异常时静默 */
    }
}

function updateUnreadBadge(count) {
    const badge = document.getElementById('unreadBadge');
    if (!badge) return;
    if (count > 0) {
        badge.textContent = count > 99 ? '99+' : String(count);
        badge.classList.remove('hidden');
    } else {
        badge.classList.add('hidden');
    }
}

export async function openNotification(id) {
    try {
        await markNotificationRead(id);
        refreshNotifications();
    } catch (e) {
        showToast('操作失败：' + (e.message || '未知错误'), 'error');
    }
}

export async function markAllRead() {
    try {
        await markAllNotificationsRead();
        refreshNotifications();
    } catch (e) {
        showToast('操作失败：' + (e.message || '未知错误'), 'error');
    }
}

// ---- WebSocket 实时推送 ----
function connectRealtime() {
    disconnectRealtime();
    if (!isLoggedIn()) return;
    const token = getToken();
    if (!token) return;
    socket = connectSocket(
        token,
        (msg) => {
            if (msg?.type === 'risk_alert') {
                showToast(`客户 ${msg.client_name || ''} 触发 ${msg.count || ''} 条风险预警`, 'warning');
                refreshNotifications();
            }
        },
        (status) => {
            if (status === 'error' || status === 'disconnected') {
                // 断开后可选择重连，这里简单提示一次
            }
        },
    );
}

function disconnectRealtime() {
    if (socket) {
        try { socket.close(); } catch { /* ignore */ }
        socket = null;
    }
}

// ---- 初始化 ----
export function initAuth() {
    renderNavState();
    renderAuthModal();
    applyAccessControl();
    if (isLoggedIn()) {
        refreshNotifications();
        connectRealtime();
    }
}
