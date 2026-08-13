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
