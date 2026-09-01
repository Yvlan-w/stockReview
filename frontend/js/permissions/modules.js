// ============================================================
// 模块可见性控制（基于角色的页面模块显隐 + 预留账户级扩展）
// ------------------------------------------------------------
// 单一事实来源在后端 module_permission_service：
//   GET /api/modules/visible  → 当前用户/指定角色/指定账户的可见模块列表
// 本文件负责：
//   1. 拉取后端可见性配置（失败则回退到前端内置默认）；
//   2. 提供 isModuleVisible(key) 供各渲染函数守卫；
//   3. applyModuleVisibility() 根据 data-module 标记切换 DOM 显隐。
//
// 新增可配置模块：后端 MODULE_REGISTRY 登记 + 前端在 index.html 对应容器加
//   data-module="<key>" 即可，无需改动任何业务渲染代码。
// ============================================================
import { request, getUser } from '../services/authService.js';

// 模块标识常量（与后端 MODULE_REGISTRY.key 保持一一对应）
export const MODULE = {
    MARKET_OVERVIEW: 'market_overview',
    INDEX_TURNOVER: 'index_turnover',
    SECTOR_PERFORMANCE: 'sector_performance',
    MARKET_ANALYSIS: 'market_analysis',
    MARKET_TICKER: 'market_ticker',
    LIVE_NEWS: 'live_news',
    CLIENT_WORKBENCH: 'client_workbench',
    POSITION_DETAIL: 'position_detail',
    STRATEGY_REVIEW: 'strategy_review',
    RISK_WARNING: 'risk_warning',
    NOTIFICATIONS: 'notifications',
    ADMIN_PANEL: 'admin_panel',
};

// 前端回退默认：与后端 DEFAULT_HIDDEN_FOR_ROLE 保持一致（后端不可达时降级用）
const FALLBACK_HIDDEN_FOR_ROLE = {
    service: new Set(['market_overview', 'index_turnover', 'sector_performance']),
};

let _visibility = null;   // { [key]: bool }
let _loaded = false;

// 解析后端返回的 modules 数组为 {key:bool} 映射
function _mapFromModules(modules = []) {
    const map = {};
    for (const m of modules) map[m.key] = m.visible !== false;
    return map;
}

function _fallbackVisibility() {
    const role = getUser()?.role || 'guest';
    const hidden = FALLBACK_HIDDEN_FOR_ROLE[role] || new Set();
    const map = {};
    for (const key of Object.values(MODULE)) map[key] = !hidden.has(key);
    return map;
}

// 拉取后端可见性配置；失败则使用前端内置默认（保证 UI 始终可用）
export async function loadModuleVisibility() {
    try {
        const data = await request('/api/modules/visible');
        _visibility = _mapFromModules(data.modules);
        _loaded = true;
    } catch {
        _visibility = _fallbackVisibility();
        _loaded = true;
    }
    return _visibility;
}

export function isModuleVisible(key) {
    if (_visibility && key in _visibility) return _visibility[key];
    // 未加载或未知 key：回退默认
    if (!_visibility) _visibility = _fallbackVisibility();
    if (key in _visibility) return _visibility[key];
    return true; // 未知模块默认可见
}

export function getVisibleModules() {
    return Object.keys(MODULE).filter(k => isModuleVisible(MODULE[k]));
}

export function getHiddenModules() {
    return Object.keys(MODULE).filter(k => !isModuleVisible(MODULE[k]));
}

// 根据 data-module 标记统一切换 DOM 显隐（display:none / 恢复）
export function applyModuleVisibility(root = document) {
    const nodes = root.querySelectorAll('[data-module]');
    nodes.forEach(el => {
        const key = el.getAttribute('data-module');
        const visible = isModuleVisible(key);
        el.classList.toggle('hidden', !visible);
    });
}
