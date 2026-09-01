// 模拟前端 modules.js 逻辑：对每种角色喂入"后端正确返回"的 payload，看 isModuleVisible 结果
const store = new Map();
globalThis.localStorage = {
    getItem: k => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k),
};
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.dispatchEvent = () => {};
globalThis.document = { querySelectorAll: () => [] };

let CURRENT_ROLE = 'admin';
globalThis.fetch = async () => {
    const HID = { market_overview: 1, index_turnover: 1, sector_performance: 1 };
    const keys = ['market_overview', 'index_turnover', 'sector_performance', 'market_analysis', 'client_workbench', 'live_news'];
    const modules = keys.map(k => ({
        key: k, name: k, category: 'm', visible: !(HID[k] && CURRENT_ROLE === 'service'),
    }));
    return { ok: true, status: 200, json: async () => ({ role: CURRENT_ROLE, modules }) };
};

const M = await import('./frontend/js/permissions/modules.js');
const THREE = ['market_overview', 'index_turnover', 'sector_performance'];

function line(tag) {
    return tag + ' -> ' + THREE.map(k => k + '=' + M.isModuleVisible(k)).join('  ');
}

console.log('--- A) 后端可达：每种角色首屏 loadModuleVisibility ---');
for (const role of ['admin', 'advisor', 'user', 'guest', 'service']) {
    CURRENT_ROLE = role;
    store.set('stock_review_user', JSON.stringify({ role: role, id: 'u1', name: role }));
    store.set('stock_review_token', 'tok');
    await M.loadModuleVisibility();
    console.log(line('role=' + role.padEnd(8)));
}

console.log('--- B) 切换账号：service 登录后，不刷新页面切到 advisor（登录路径未重新加载可见性）---');
CURRENT_ROLE = 'service';
store.set('stock_review_user', JSON.stringify({ role: 'service', id: 'u1' }));
await M.loadModuleVisibility();
console.log(line('login as service '));
CURRENT_ROLE = 'advisor';
store.set('stock_review_user', JSON.stringify({ role: 'advisor', id: 'u1' }));
console.log(line('switch to advisor'));
