// ============================================================
// 认证与站内信服务（Phase 2 后端接入）
// 提供：登录/登出/会话管理、站内信 REST 接口、WebSocket 实时推送。
// ============================================================

const TOKEN_KEY = 'stock_review_token';
const USER_KEY = 'stock_review_user';

export const ROLE_LABELS = {
    guest: '游客',
    user: '用户',
    advisor: '投资顾问',
    service: '客服',
    admin: '管理员',
};

export const SUBROLE_LABELS = {
    client: '客户',
    non_client: '普通用户',
};

// ---- 会话管理 ----
export function getToken() {
    return localStorage.getItem(TOKEN_KEY) || null;
}

export function getUser() {
    try {
        return JSON.parse(localStorage.getItem(USER_KEY) || 'null');
    } catch {
        return null;
    }
}

export function setSession(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
}

export function isLoggedIn() {
    return !!getToken();
}

export function roleLabel(user) {
    const role = user?.role || 'guest';
    let label = ROLE_LABELS[role] || role;
    if (role === 'user' && user?.sub_role) {
        label += '·' + (SUBROLE_LABELS[user.sub_role] || user.sub_role);
    }
    return label;
}

// ---- HTTP 基础请求 ----
async function request(path, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(path, { ...options, headers });
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = data.detail || '请求失败';
        // 令牌失效（401 过期/无效）或账户到期被踢（403）：清除本地会话并广播过期事件，
        // 由 auth.js 统一处理"强制登出 + 打开登录弹窗"，避免界面仍显示已登录的僵尸态。
        if (res.status === 401 || res.status === 403) {
            try { clearSession(); } catch { /* 忽略 */ }
            try {
                window.dispatchEvent(new CustomEvent('auth:expired', {
                    detail: { status: res.status, message: typeof detail === 'string' ? detail : JSON.stringify(detail) },
                }));
            } catch { /* 忽略 */ }
        }
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
}

// ---- 认证接口 ----
export async function login(username, password) {
    const data = await request('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
    });
    setSession(data.access_token, data.user);
    return data.user;
}

export async function logout() {
    clearSession();
}

export async function fetchMe() {
    const me = await request('/api/auth/me');
    // 兜底：后端同步刷新 expired 后如果踢出 403 时请求层已经抛错；
    // 这里只负责把最新用户资料写回 session（避免过期日跨零点刷新也能在前端显示）
    if (me && me.id) {
        const token = getToken();
        if (token) localStorage.setItem(USER_KEY, JSON.stringify(me));
    }
    return me;
}

// ---- 生命周期到期剩余天数的前端展示工具 ----
export function licenseBadgeOf(user) {
    if (!user) return { html: '', classes: '' };
    if (user.role === 'admin') {
        return { text: '永久', html: '<span class="text-xs px-2 py-0.5 rounded bg-primary/10 text-primary">永久有效</span>', level: 'forever' };
    }
    const days = Number.isFinite(user.remaining_days) ? user.remaining_days : null;
    const status = user.status || 'active';
    if (status === 'deleted') return { text: '已删除', html: '<span class="text-xs px-2 py-0.5 rounded bg-hairline text-muted">已删除</span>', level: 'deleted' };
    if (status === 'expired' || (days !== null && days <= 0)) {
        return {
            text: '已到期',
            html: '<span class="text-xs px-2 py-0.5 rounded bg-negative/10 text-negative">已到期（限制登录）</span>',
            level: 'expired',
        };
    }
    if (days === null) {
        return { text: '长期', html: '<span class="text-xs px-2 py-0.5 rounded bg-primary/10 text-primary">长期</span>', level: 'forever' };
    }
    let cls = 'bg-positive/10 text-positive';
    let label = `剩余 ${days} 天`;
    if (days <= 7) cls = 'bg-negative/10 text-negative';
    else if (days <= 30) cls = 'bg-amber-500/15 text-amber-600';
    return { text: label, html: `<span class="text-xs px-2 py-0.5 rounded ${cls}">${label}</span>`, level: days <= 7 ? 'danger' : (days <= 30 ? 'warn' : 'ok'), days };
}

// ---- 用户生命周期管理（管理员操作）----
export async function listUsers() { return request('/api/users'); }
export async function deleteUser(id) { return request(`/api/users/${id}`, { method: 'DELETE' }); }
export async function deleteSelfUser() { return request('/api/users/me', { method: 'DELETE' }); }
export async function resetUserPassword(id, password = null) {
    return request(`/api/users/${id}/reset-password`, {
        method: 'POST', body: JSON.stringify({ password: password || null }),
    });
}
export async function renewUser(id, extendDays) {
    return request(`/api/users/${id}/renew`, { method: 'POST', body: JSON.stringify({ extend_days: extendDays }) });
}
export async function patchUserLifecycle(id, patch) {
    const body = {};
    if ('status' in patch) body.status = patch.status || null;
    if ('expires_at' in patch) body.expires_at = patch.expires_at || null;
    if ('license_days' in patch) body.license_days = Number.isFinite(patch.license_days) ? patch.license_days : null;
    return request(`/api/users/${id}/lifecycle`, { method: 'PATCH', body: JSON.stringify(body) });
}

/**
 * 当 user-client 账户未关联客户档案时，调用后端自动创建一个档案并回填 owner_user_id。
 * 返回 { client_id, created }；已存在档案时 created=false（幂等）。
 * 这样「分配关系」按钮就不需要再 toast 报错，而是自动补齐档案。
 */
export async function ensureClientProfile(userId) {
    return request(`/api/users/${userId}/ensure-client-profile`, { method: 'POST' });
}
// ---- 审计日志查询（后台「查看日志」Tab）----
export async function listAuditLogs(params = {}) {
    const u = new URLSearchParams();
    if (params.page) u.set('page', params.page);
    if (params.page_size) u.set('page_size', params.page_size);
    if (params.keyword) u.set('keyword', params.keyword);
    if (params.action) u.set('action', params.action);
    if (params.target_type) u.set('target_type', params.target_type);
    if (params.actor_id) u.set('actor_id', params.actor_id);
    if (params.start) u.set('start', params.start);
    if (params.end) u.set('end', params.end);
    const qs = u.toString();
    return request(`/api/audit-logs${qs ? `?${qs}` : ''}`);
}

// ---- 自助：修改个人密码（所有登录用户均可） ----
export async function changePassword(oldPassword, newPassword) {
    return request('/api/users/me/password', {
        method: 'POST',
        body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });
}

// 前端密码强度评分（与后端一致的 0-4 极弱/较弱/一般/较强/极强）
export function passwordStrengthScore(pwd) {
    if (!pwd) return { score: 0, label: '极弱' };
    let score = 0;
    const hasLower = /[a-z]/.test(pwd);
    const hasUpper = /[A-Z]/.test(pwd);
    const hasDigit = /\d/.test(pwd);
    const hasSpecial = /[^A-Za-z0-9]/.test(pwd);
    if (pwd.length >= 8) score++;
    if (hasLower || hasUpper) score++;
    if ((hasLower && hasUpper) || hasDigit) score++;
    if (hasSpecial && (hasLower || hasUpper) && hasDigit) score++;
    const levels = ['极弱', '较弱', '一般', '较强', '极强'];
    return { score: Math.min(score, 4), label: levels[Math.min(score, 4)] };
}

// ---- 客户接口 ----
export async function fetchClients() {
    return request('/api/clients');
}

export async function updateClient(id, patch) {
    return request(`/api/clients/${id}`, { method: 'PUT', body: JSON.stringify(patch) });
}

export async function updateClientPositions(id, positions) {
    return request(`/api/clients/${id}/positions`, { method: 'PUT', body: JSON.stringify({ positions }) });
}

// ---- 风险预警接口 ----
export async function evaluateRisk(clientId) {
    return request(`/api/risk/evaluate/${clientId}`, { method: 'POST' });
}

export async function fetchClientAlerts(clientId) {
    return request(`/api/clients/${clientId}/alerts`);
}

export async function updateAlertStatus(alertId, status) {
    return request(`/api/alerts/${alertId}`, { method: 'PATCH', body: JSON.stringify({ status }) });
}

// ---- 用户 / 客户 / 关系映射接口 ----
export async function fetchUserOptions() {
    return request('/api/users/options');
}

export async function createUser(payload) {
    return request('/api/users', { method: 'POST', body: JSON.stringify(payload) });
}

export async function createClient(payload) {
    return request('/api/clients', { method: 'POST', body: JSON.stringify(payload) });
}

export async function updateClientRelations(id, payload) {
    return request(`/api/clients/${id}/relations`, { method: 'PUT', body: JSON.stringify(payload) });
}

export async function deleteClient(id) {
    return request(`/api/clients/${id}`, { method: 'DELETE' });
}

export async function importRelations(rows) {
    return request('/api/relations/import', { method: 'POST', body: JSON.stringify(rows) });
}

export async function exportRelationsJson() {
    return request('/api/relations/export');
}

// ---- CSV 导入/导出（raw 请求，区别于 JSON 请求体） ----
function authHeaders(extra = {}) {
    const token = getToken();
    const headers = { ...extra };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return headers;
}

export async function exportRelationsCsv() {
    const res = await fetch('/api/relations/export/csv', { headers: authHeaders() });
    if (!res.ok) {
        const text = await res.text().catch(() => '导出失败');
        throw new Error(text);
    }
    return res.text();
}

export async function importRelationsCsv(text) {
    const res = await fetch('/api/relations/import/csv', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'text/csv; charset=utf-8' }),
        body: text,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : '导入失败');
    }
    return data;
}

// ---- 站内信接口 ----
export async function fetchNotifications() {
    return request('/api/notifications');
}

export async function fetchNotification(id) {
    return request(`/api/notifications/${id}`);
}

export async function fetchUnreadCount() {
    return request('/api/notifications/unread-count');
}

export async function markNotificationRead(id) {
    return request(`/api/notifications/${id}/read`, { method: 'POST' });
}

export async function markAllNotificationsRead() {
    return request('/api/notifications/read-all', { method: 'POST' });
}

// ---- WebSocket 实时推送 ----
export function connectSocket(token, onMessage, onStatus) {
    const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    const url = `${proto}${location.host}/ws/${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);

    ws.addEventListener('open', () => onStatus?.('connected'));
    ws.addEventListener('message', (e) => {
        try {
            onMessage(JSON.parse(e.data));
        } catch {
            /* 忽略非法消息 */
        }
    });
    ws.addEventListener('close', () => onStatus?.('disconnected'));
    ws.addEventListener('error', () => onStatus?.('error'));
    return ws;
}
