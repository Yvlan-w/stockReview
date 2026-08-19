// ============================================================
// 访问控制（Phase 2 权限落地）
// 依据「角色 + 资源归属」计算数据范围与操作权限，与后端
// client_service.can_view_client / list_visible_clients 保持一致。
// ============================================================
import { getUser } from '../services/authService.js';

// 当前登录用户（未登录返回 null，等价于 guest）
export function getCurrentUser() {
    return getUser();
}

// 是否登录
export function isLoggedIn() {
    return !!getUser();
}

// 数据范围：后端 /api/clients 已按角色做行级过滤，前端直接信任返回结果。
export function getVisibleClients(allClients) {
    return Array.isArray(allClients) ? allClients : [];
}

// 能否查看某客户（后端返回的均为当前用户可见客户）
export function canViewClient(client) {
    return !!client;
}

// 能否编辑某客户（当前设计：能查看即能编辑）
export function canEditClient(client) {
    return canViewClient(client);
}

// 是否拥有编辑能力（用于隐藏全局「添加持仓/恢复示例数据」等操作）
export function canEdit() {
    const user = getUser();
    if (!user) return false;
    if (['admin', 'advisor', 'service'].includes(user.role)) return true;
    return user.role === 'user' && user.sub_role === 'client';
}

// 能否处理风险预警（状态流转：确认/解决）——与后端 update_alert_status 一致
export function canHandleAlerts() {
    const user = getUser();
    if (!user) return false;
    return ['admin', 'advisor', 'service'].includes(user.role);
}

// 客户持仓工作台是否可见（guest 与未登录不可见）
// 例外：分享版快照页面烘焙了真实持仓，作为匿名访客时以「只读」方式展示。
export function isWorkbenchVisible() {
    const user = getUser();
    if (user && user.role !== 'guest') return true;
    return !!(window.EMBEDDED_POSITIONS && window.EMBEDDED_POSITIONS.length);
}

// 是否管理员（仅管理员可见关系映射配置后台）
export function isAdmin() {
    return getUser()?.role === 'admin';
}

// 是否客服
export function isService() {
    return getUser()?.role === 'service';
}

// 能否进入客户开户流程（管理员可任意开户，客服仅能创建客户账户）
export function canCreateClient() {
    const user = getUser();
    return !!user && ['admin', 'service'].includes(user.role);
}

// 能否访问管理员关系映射面板
export function canAccessAdminPanel() {
    return isAdmin();
}

// 能否访问客服开户向导（管理员也可用）
export function canAccessOnboarding() {
    return canCreateClient();
}
