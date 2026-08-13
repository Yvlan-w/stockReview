// ============================================================
// 客户数据服务（生成 / 持久化 / 统计 / 风险计算 / 筛选）
// ============================================================
import {
    CLIENTS_STORAGE_KEY, CLIENTS_COUNT, RISK_LEVELS, RISK_WEIGHTS,
    CLIENT_TAGS_POOL, CLIENT_NOTES_POOL, CLIENT_SURNAMES, CLIENT_GIVEN,
    STOCK_POOL, POSITIONS_STORAGE_KEY, USERDATA_STORAGE_KEY,
    ADVISOR_POOL, SERVICE_STAFF_POOL
} from '../core/config.js';
import { EMBEDDED_POSITIONS, EMBEDDED_USERDATA, DEFAULT_POSITIONS } from '../data/mockData.js';
import { showToast } from '../core/ui.js';

// ---- 客户工作台可变状态（跨模块共享，live binding）----
export let clients = [];
export let currentClientId = null;
export let clientWarnOnly = false;
export let clientTagEditId = null;

function pickWeighted(items, weights) {
    const r = Math.random();
    let acc = 0;
    for (let i = 0; i < items.length; i++) {
        acc += weights[i];
        if (r <= acc) return items[i];
    }
    return items[items.length - 1];
}

export function generateClients(count) {
    count = count || CLIENTS_COUNT;
    const arr = [];
    const usedNames = new Set();
    for (let i = 0; i < count; i++) {
        let name, tries = 0;
        do {
            name = CLIENT_SURNAMES[Math.floor(Math.random() * CLIENT_SURNAMES.length)]
                 + CLIENT_GIVEN[Math.floor(Math.random() * CLIENT_GIVEN.length)];
            tries++;
        } while (usedNames.has(name) && tries < 30);
        usedNames.add(name);

        const riskLevel = pickWeighted(RISK_LEVELS, RISK_WEIGHTS);
        const pickCount = 3 + Math.floor(Math.random() * 10); // 3-12 只
        const shuffled = STOCK_POOL.slice().sort(() => Math.random() - 0.5).slice(0, pickCount);
        const positions = shuffled.map(s => {
            const price = +(s.base * (0.90 + Math.random() * 0.22)).toFixed(2);
            const costPrice = +(s.base * (0.84 + Math.random() * 0.32)).toFixed(2);
            const qtyBase = Math.max(100, Math.round(12000 / price));
            const quantity = Math.max(100, Math.round(qtyBase * (0.5 + Math.random() * 1.2)));
            return { name: s.name, code: s.code, quantity, costPrice, price, sector: s.sector };
        });
        const totalMarket = positions.reduce((sum, p) => sum + p.price * p.quantity, 0);
        const availableCash = Math.round(totalMarket * (0.3 + Math.random() * 1.2));

        const tags = [];
        const tagCount = Math.random() < 0.45 ? 0 : (Math.random() < 0.5 ? 1 : 2);
        for (let t = 0; t < tagCount; t++) {
            const tag = CLIENT_TAGS_POOL[Math.floor(Math.random() * CLIENT_TAGS_POOL.length)];
            if (!tags.includes(tag)) tags.push(tag);
        }

        const advisor = ADVISOR_POOL[Math.floor(Math.random() * ADVISOR_POOL.length)];
        const serviceCount = Math.random() < 0.4 ? 1 : 2; // 1~2 名客服
        const shuffledServices = SERVICE_STAFF_POOL.slice().sort(() => Math.random() - 0.5);
        const serviceIds = shuffledServices.slice(0, serviceCount).map(s => s.id);

        arr.push({
            id: 'C' + String(i + 1).padStart(3, '0'),
            name, age: 25 + Math.floor(Math.random() * 45),
            riskLevel, tags,
            note: CLIENT_NOTES_POOL[Math.floor(Math.random() * CLIENT_NOTES_POOL.length)],
            positions, availableCash, todayPnL: null,
            advisorId: advisor.id, serviceIds
        });
    }
    return arr;
}

export function getClients() {
    try {
        const saved = localStorage.getItem(CLIENTS_STORAGE_KEY);
        if (saved) {
            const arr = JSON.parse(saved);
            if (Array.isArray(arr) && arr.length >= 20) return arr;
        }
    } catch (e) { console.warn('Failed to load clients:', e); }
    const generated = generateClients(CLIENTS_COUNT);
    try { localStorage.setItem(CLIENTS_STORAGE_KEY, JSON.stringify(generated)); } catch (e) {}
    return generated;
}

// 初始化客户数据（将 getClients() 结果写入模块级 live binding）
export function initClients() {
    clients = getClients();
}

export function saveClients() {
    try { localStorage.setItem(CLIENTS_STORAGE_KEY, JSON.stringify(clients)); } catch (e) { console.warn('save clients failed', e); }
}

export function getCurrentClient() {
    if (!clients.length) clients = getClients();
    return clients.find(c => c.id === currentClientId) || clients[0] || null;
}

export function setCurrentClient(id) { currentClientId = id; }

export function setClientWarnOnly(val) { clientWarnOnly = !!val; }

// 客户资产 / 盈亏统计
export function clientStats(client) {
    if (!client) return { totalMarket: 0, totalCost: 0, totalPnl: 0, totalPnlPct: 0, totalAssets: 0, positions: [] };
    const positions = Array.isArray(client.positions) ? client.positions : [];
    let totalMarket = 0, totalCost = 0;
    positions.forEach(p => {
        totalMarket += p.price * p.quantity;
        totalCost += p.costPrice * p.quantity;
    });
    const totalPnl = totalMarket - totalCost;
    const totalAssets = totalMarket + (client.availableCash || 0);
    return {
        totalMarket, totalCost, totalPnl,
        totalPnlPct: totalCost > 0 ? (totalPnl / totalCost * 100) : 0,
        totalAssets, positions, availableCash: client.availableCash || 0
    };
}

// 客户风险预警计算
export function clientRiskAlerts(client) {
    const alerts = [];
    if (!client) return alerts;
    const s = clientStats(client);
    const positions = s.positions || [];
    // 亏损超阈值：总亏损 > -8%
    if (s.totalPnl < 0 && s.totalPnlPct <= -8) {
        alerts.push({ type: 'loss', level: 'high', title: '组合亏损超阈值', desc: '累计亏损 ' + s.totalPnlPct.toFixed(1) + '%，建议关注止损与调仓' });
    } else if (s.totalPnl < 0) {
        alerts.push({ type: 'loss', level: 'mid', title: '组合浮亏', desc: '累计亏损 ' + s.totalPnlPct.toFixed(1) + '%' });
    }
    // 行业集中度 > 50%
    const sectorMap = {};
    positions.forEach(p => { sectorMap[p.sector] = (sectorMap[p.sector] || 0) + p.price * p.quantity; });
    const topSector = Object.entries(sectorMap).sort((a, b) => b[1] - a[1])[0];
    if (topSector && s.totalMarket > 0) {
        const pct = topSector[1] / s.totalMarket * 100;
        if (pct > 50) alerts.push({ type: 'sector', level: 'high', title: '行业集中度过高', desc: topSector[0] + ' 占比 ' + pct.toFixed(0) + '%，单一行业风险较大' });
    }
    // 单票占比 > 40%
    positions.forEach(p => {
        if (s.totalMarket > 0 && p.price * p.quantity / s.totalMarket * 100 > 40) {
            alerts.push({ type: 'stock', level: 'high', title: '单票占比过高', desc: p.name + ' 占比 ' + (p.price * p.quantity / s.totalMarket * 100).toFixed(0) + '%，集中持仓风险高' });
        }
    });
    // 亏损单只 > 20%
    positions.forEach(p => {
        const pnlPct = (p.price - p.costPrice) / p.costPrice * 100;
        if (pnlPct <= -20) alerts.push({ type: 'stock', level: 'mid', title: '个股深度亏损', desc: p.name + ' 亏损 ' + pnlPct.toFixed(1) + '%' });
    });
    return alerts;
}

// 获取用户持仓（工作台优先 → 分享版注入 → localStorage → 默认）
export function getUserPositions() {
    const client = getCurrentClient();
    if (client && Array.isArray(client.positions)) return client.positions;
    if (Array.isArray(EMBEDDED_POSITIONS) && EMBEDDED_POSITIONS.length) return EMBEDDED_POSITIONS;
    try {
        const saved = localStorage.getItem(POSITIONS_STORAGE_KEY);
        if (saved) return JSON.parse(saved);
    } catch (e) { console.warn('Failed to load positions:', e); }
    return [...DEFAULT_POSITIONS];
}

export function saveUserPositions(positions) {
    const client = getCurrentClient();
    if (client) {
        client.positions = positions;
        saveClients();
    }
    try {
        localStorage.setItem(POSITIONS_STORAGE_KEY, JSON.stringify(positions));
    } catch (e) {
        console.error('Failed to save positions:', e);
        showToast('❌ 持仓数据保存失败', 'error');
    }
}

// 获取 / 保存其它用户数据（可用资金等）
export function getUserData() {
    const client = getCurrentClient();
    if (client) return { availableCash: client.availableCash, todayPnL: client.todayPnL ?? null };
    if (EMBEDDED_USERDATA && typeof EMBEDDED_USERDATA === 'object') return EMBEDDED_USERDATA;
    try {
        const saved = localStorage.getItem(USERDATA_STORAGE_KEY);
        if (saved) return JSON.parse(saved);
    } catch (e) { console.warn('Failed to load user data:', e); }
    return { availableCash: 100000, todayPnL: null };
}

export function saveUserData(data) {
    try {
        localStorage.setItem(USERDATA_STORAGE_KEY, JSON.stringify(data));
    } catch (e) { console.error('Failed to save user data:', e); }
}

// 解析客户服务关系（顾问 / 客服显示名）
export function getClientRelations(client) {
    const advisor = ADVISOR_POOL.find(a => a.id === client?.advisorId);
    const serviceNames = (client?.serviceIds || []).map(id => {
        const s = SERVICE_STAFF_POOL.find(x => x.id === id);
        return s ? s.name : id;
    });
    return {
        advisorId: client?.advisorId || null,
        advisorName: advisor ? advisor.name : '未分配',
        serviceIds: client?.serviceIds || [],
        serviceNames
    };
}

// 为身份权限管理预留的查询接口：按顾问 / 客服取客户
export function getClientsByAdvisor(advisorId) {
    return clients.filter(c => c.advisorId === advisorId);
}
export function getClientsByService(staffId) {
    return clients.filter(c => (c.serviceIds || []).includes(staffId));
}

// 客户列表过滤 / 排序（读取工作台筛选控件）
export function getFilteredClients() {
    const q = (document.getElementById('clientSearch').value || '').trim().toLowerCase();
    const risk = document.getElementById('clientRiskFilter').value;
    const sort = document.getElementById('clientSort').value;
    const advisor = document.getElementById('clientAdvisorFilter')?.value || 'all';
    const service = document.getElementById('clientServiceFilter')?.value || 'all';
    let list = clients.filter(c => {
        const matchQ = !q || c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q);
        const matchRisk = risk === 'all' || c.riskLevel === risk;
        const matchWarn = !clientWarnOnly || clientRiskAlerts(c).length > 0;
        const matchAdvisor = advisor === 'all' || c.advisorId === advisor;
        const matchService = service === 'all' || (c.serviceIds || []).includes(service);
        return matchQ && matchRisk && matchWarn && matchAdvisor && matchService;
    });
    list.sort((a, b) => {
        const sa = clientStats(a), sb = clientStats(b);
        switch (sort) {
            case 'assets-desc': return sb.totalAssets - sa.totalAssets;
            case 'assets-asc': return sa.totalAssets - sb.totalAssets;
            case 'pnl-desc': return sb.totalPnl - sa.totalPnl;
            case 'pnl-asc': return sa.totalPnl - sb.totalPnl;
            default: return sb.totalAssets - sa.totalAssets;
        }
    });
    return list;
}
