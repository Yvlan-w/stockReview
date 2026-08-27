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
    return request('/api/auth/me');
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
