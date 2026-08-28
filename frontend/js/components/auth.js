// ============================================================
// 认证 UI 集成：登录表单、导航栏角色/未读展示、站内信中心、WebSocket。
// 说明：后端 Phase 2 提供 /api/auth、/api/notifications 与 /ws/{token}。
// ============================================================
import { showToast, showAdBanner } from '../core/ui.js';
import {
    isLoggedIn, getToken, getUser, login, logout, roleLabel,
    fetchNotifications, fetchNotification, fetchUnreadCount, markNotificationRead,
    markAllNotificationsRead, connectSocket, deleteSelfUser,
} from '../services/authService.js';
import { getFilteredClients, setCurrentClient, loadClients } from '../services/clientService.js';
import { renderClientList, refreshClientDetail, refreshClientSummaries } from './workbench.js';
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
    // 异步拉取客户盈亏摘要（后台加载，结果就绪后自动刷新列表盈亏显示）
    refreshClientSummaries().catch(() => {});
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

    // —— 自助删除账户按钮：仅用户(role=user / sub_role 普通/客户)可见，客服/投顾/管理员隐藏 ——
    const delBtn = document.getElementById('authDeleteSelfBtn');
    if (delBtn) {
        if (user?.role === 'user') {
            delBtn.classList.remove('hidden');
        } else {
            delBtn.classList.add('hidden');
        }
    }
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
        showAdBanner();   // 每次登录成功后重新展示广告 Banner
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
        const msg = (e.message || '未知错误').toString();
        // 把"账户到期请联系管理员续费"这条区分出来单独 toast（红色 + 提示条），
        // 避免混在"用户名密码错误"里用户注意不到。
        if (msg.includes('到期') || msg.includes('expired')) {
            showToast('⚠️ 账户到期 — ' + msg, 'error', 12000);
            const tip = document.getElementById('loginExpiredHint');
            if (tip) {
                tip.textContent = msg + '（请联系管理员续费）';
                tip.classList.remove('hidden');
            }
        } else {
            showToast('登录失败：' + msg, 'error');
            const tip = document.getElementById('loginExpiredHint');
            if (tip) tip.classList.add('hidden');
        }
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

// ---- 普通用户自助注销账户（软删除）----
export async function handleDeleteSelfAccount() {
    const user = getUser();
    if (!user) { showToast('请先登录', 'warning'); return; }
    if (user.role !== 'user') {
        showToast('当前角色不允许自助删除账户，请联系管理员', 'error');
        return;
    }
    const username = (user.username || '').toString();
    const displayName = (user.name || user.username || '').toString();
    const confirm1 = window.confirm(
        `⚠️ 账户注销确认\n\n将删除账户「${displayName}（${username}）」并解除所有客户关系。\n此操作为“软删除”，数据不会立即物理清除，但您将无法再登录。\n\n确定继续吗？请在下一个输入框中准确输入您的账号。`,
    );
    if (!confirm1) return;
    const input = window.prompt('为了确保是本人操作，请输入您的「账号」（用户名）：', '');
    if (input == null) return;
    if (input.trim() !== username) {
        showToast('❌ 账号输入不一致，注销操作已取消', 'error');
        return;
    }
    const confirm2 = window.confirm(`最后确认：账户「${username}」下的客户持仓 / 交易流水不会丢失，但账户无法再登录。\n点击「确定」立即注销。`);
    if (!confirm2) return;
    try {
        await deleteSelfUser();
    } catch (e) {
        showToast('❌ 注销失败：' + (e?.message || '未知错误'), 'error');
        return;
    }
    // 成功：强制断开实时、清缓存、退出登录并提示回到首页/登录
    try { disconnectRealtime(); } catch { /* ignore */ }
    try { await logout(); } catch { /* ignore */ }
    try { await loadClients(); } catch { /* ignore */ }
    try { applyAccessControl(); } catch { /* ignore */ }
    hideNotificationPanel();
    renderNavState();
    renderAuthModal();
    // 关闭身份管理弹窗（避免遮挡）
    const modal = document.getElementById('identityModal');
    if (modal) { modal.classList.add('hidden'); document.body.style.overflow = ''; }
    showToast('✅ 账户已注销成功，欢迎下次使用', 'success', 6000);
}

// ---- 站内信 ----
function hideNotificationPanel() {
    const panel = document.getElementById('notificationPanel');
    const backdrop = document.getElementById('notificationBackdrop');
    if (panel) panel.classList.add('hidden');
    if (backdrop) backdrop.classList.add('hidden');
}

export function toggleNotificationPanel(event) {
    if (!isLoggedIn()) {
        showToast('请先登录', 'warning');
        return;
    }
    const panel = document.getElementById('notificationPanel');
    const backdrop = document.getElementById('notificationBackdrop');
    if (!panel) return;
    const willShow = panel.classList.contains('hidden');
    if (willShow) {
        panel.classList.remove('hidden');
        if (backdrop) backdrop.classList.remove('hidden');
        refreshNotifications();
    } else {
        hideNotificationPanel();
    }
    // 点击铃铛/按钮时不冒泡到 document 的全局监听，否则又被当成"点击空白"立刻关闭
    if (event && typeof event.stopPropagation === 'function') {
        event.stopPropagation();
    }
}

// 在 initAuth 时安装：点击站内信面板外 + 遮罩层都能关闭；按 ESC 同样关闭
function _installNotificationCloseHooks() {
    const panel = document.getElementById('notificationPanel');
    const backdrop = document.getElementById('notificationBackdrop');
    const bell = document.getElementById('notificationBell');
    if (backdrop) {
        backdrop.addEventListener('click', (e) => {
            // 点击遮罩（空白处）→ 关闭
            e.stopPropagation();
            hideNotificationPanel();
        });
    }
    if (panel) {
        panel.addEventListener('click', (e) => e.stopPropagation());
    }
    document.addEventListener('click', (e) => {
        const p = document.getElementById('notificationPanel');
        if (!p) return;
        if (p.classList.contains('hidden')) return;
        // 任何在"站内信显示范围"（panel DOM）之外的点击立即关闭
        // 铃铛按钮也排除（由 toggleNotificationPanel 处理，切换逻辑自己管）
        if (p.contains(e.target)) return;
        const bellEl = document.getElementById('notificationBell');
        if (bellEl && bellEl.contains(e.target)) return;
        hideNotificationPanel();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const p = document.getElementById('notificationPanel');
            if (p && !p.classList.contains('hidden')) hideNotificationPanel();
        }
    });
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
    const modal = document.getElementById('notificationDetailModal');
    const titleEl = document.getElementById('notifDetailTitle');
    const timeEl = document.getElementById('notifDetailTime');
    const catEl = document.getElementById('notifDetailCategory');
    const badgeEl = document.getElementById('notifDetailReadBadge');
    const contentEl = document.getElementById('notifDetailContent');
    const loadingEl = document.getElementById('notifDetailLoading');
    if (!modal) return;
    try {
        // 打开时先关闭列表面板 + 显示加载态
        hideNotificationPanel();
        contentEl.classList.add('hidden');
        contentEl.textContent = '';
        if (loadingEl) loadingEl.classList.remove('hidden');
        if (titleEl) titleEl.textContent = '';
        if (timeEl) timeEl.textContent = '';
        if (catEl) catEl.textContent = '';
        if (badgeEl) badgeEl.textContent = '';
        modal.classList.remove('hidden');

        const n = await fetchNotification(id);
        if (loadingEl) loadingEl.classList.add('hidden');
        contentEl.classList.remove('hidden');
        if (titleEl) titleEl.textContent = n.title || '(无标题)';
        if (timeEl) timeEl.textContent = notifTime(n.created_at);
        if (catEl) catEl.textContent = (n.category || 'system').toLowerCase();
        if (badgeEl) {
            badgeEl.textContent = n.is_read ? '已读' : '未读';
            badgeEl.classList.toggle('border-primary/30', !n.is_read);
            badgeEl.classList.toggle('text-primary', !n.is_read);
        }
        contentEl.textContent = n.content ?? '';

        // 已读状态已在后端 get_notification 中自动标记
        refreshNotifications();
    } catch (e) {
        if (loadingEl) loadingEl.classList.add('hidden');
        contentEl.classList.remove('hidden');
        contentEl.textContent = '加载消息详情失败：' + (e?.message || '未知错误');
        showToast('加载消息详情失败', 'error');
    }
}

function closeNotificationDetail() {
    const modal = document.getElementById('notificationDetailModal');
    if (modal) modal.classList.add('hidden');
}

function _installNotificationDetailHooks() {
    const modal = document.getElementById('notificationDetailModal');
    const backdrop = document.getElementById('notificationDetailBackdrop');
    const closeBtn = document.getElementById('notifDetailCloseBtn');
    const okBtn = document.getElementById('notifDetailOkBtn');
    if (backdrop) backdrop.addEventListener('click', closeNotificationDetail);
    if (closeBtn) closeBtn.addEventListener('click', closeNotificationDetail);
    if (okBtn) okBtn.addEventListener('click', closeNotificationDetail);
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (modal && !modal.classList.contains('hidden')) closeNotificationDetail();
    });
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
    _installNotificationCloseHooks();
    _installNotificationDetailHooks();
    if (isLoggedIn()) {
        refreshNotifications();
        connectRealtime();
    }
}
