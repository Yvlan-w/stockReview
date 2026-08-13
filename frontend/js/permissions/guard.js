// ============================================================
// 权限守卫（Phase 2+ 后端接入时启用；Phase 1 为基础实现）
// ============================================================
//
// 角色层级：guest < user < advisor < service < admin
//   - guest     游客：只读公开内容
//   - user      用户（含客户 / 非客户子角色）：管理自有持仓
//   - advisor   投资顾问：管理所服务客户
//   - service   客服：管理所服务客户（含风险预警处理）
//   - admin     管理员：全部数据
//
// computePermissions(user, resourceOwnerId) 依据「角色 + 资源归属」集中计算权限，
// 避免在 UI 层分散判断。

export const ROLES = {
    GUEST: 'guest',
    USER: 'user',
    ADVISOR: 'advisor',
    SERVICE: 'service',
    ADMIN: 'admin',
};

export const ROLE_LEVEL = {
    guest: 0,
    user: 1,
    advisor: 2,
    service: 3,
    admin: 4,
};

// 用户子角色
export const USER_SUBROLES = ['client', 'non_client'];

export function roleLevel(role) {
    return ROLE_LEVEL[role] ?? ROLE_LEVEL.guest;
}

// 是否满足最低角色要求
export function requireRole(user, minRole) {
    if (!user || !user.role) return false;
    return roleLevel(user.role) >= roleLevel(minRole);
}

// 集中式计算当前用户对某资源的权限
// user: { role, subRole?, id? }
// resourceOwnerId: 资源归属者（如客户记录 id）
export function computePermissions(user, resourceOwnerId = null) {
    const role = user?.role || ROLES.GUEST;
    const level = roleLevel(role);
    const isOwner = resourceOwnerId != null && user?.id != null && String(user.id) === String(resourceOwnerId);

    return {
        role,
        viewPublic: level >= 0,
        viewOwn: level >= 1,
        viewAssigned: level >= 2,      // 顾问/客服查看所服务客户
        viewAll: level >= 4,           // 仅管理员
        editOwn: level >= 1 && (isOwner || role === ROLES.USER),
        editAssigned: level >= 2,
        editAll: level >= 4,
        manageUsers: level >= 4,
        manageRelations: level >= 4,   // 客户-顾问-客服关系映射维护
        handleRiskAlerts: level >= 3,  // 客服及以上处理预警
    };
}

// 便捷断言：无权限时抛出
export function assertPermission(user, permission, resourceOwnerId = null) {
    const perms = computePermissions(user, resourceOwnerId);
    if (!perms[permission]) {
        throw new Error('无权限执行此操作');
    }
    return true;
}
