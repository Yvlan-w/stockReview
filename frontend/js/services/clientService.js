// ============================================================
// 客户数据服务（生成 / 持久化 / 统计 / 风险计算 / 筛选）
// ============================================================
import { ADVISOR_POOL, SERVICE_STAFF_POOL } from '../core/config.js';
import { getVisibleClients } from '../permissions/access.js';
import { fetchClients, updateClient, updateClientPositions, isLoggedIn } from './authService.js';
import { getPriceMap } from './priceService.js';

// 分享版注入数据：由 index.html 内联脚本挂载到 window，导出分享版时会被替换为真实数据。
const EMBEDDED_POSITIONS = window.EMBEDDED_POSITIONS;
const EMBEDDED_USERDATA = window.EMBEDDED_USERDATA;

// ---- 客户工作台可变状态（跨模块共享，live binding）----
export let clients = [];
export let currentClientId = null;
export let clientWarnOnly = false;
export let clientTagEditId = null;

// ---- 组合估值 & 盈亏历史 实时数据 ----
export let portfolioData = null;
export let pnlHistoryData = null;

// 后端返回的客户字段（snake_case）→ 前端渲染字段（camelCase）
function mapClient(raw) {
    return {
        id: raw.id,
        name: raw.name,
        age: raw.age ?? null,
        riskLevel: raw.risk_level ?? '',
        tags: raw.tags || [],
        note: raw.note || '',
        availableCash: raw.available_cash ?? 0,
        advisorId: raw.advisor_id,
        advisorName: raw.advisor_name || null,
        serviceIds: raw.service_ids || [],
        positions: (raw.positions || []).map(p => ({
            name: p.name, code: p.code, sector: p.sector,
            quantity: p.quantity, costPrice: p.cost_price,
            // 现价不再由后端持久化，通过 priceService 实时获取（降级用 costPrice）
        })),
    };
}

// 加载客户实时组合估值
export async function fetchClientPortfolio(id) {
    if (!id) return null;
    try {
        const resp = await fetch(`/api/clients/${id}/portfolio`, {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('stock_review_token')}` }
        });
        if (resp.ok) {
            portfolioData = await resp.json();
            return portfolioData;
        }
    } catch (e) {
        console.warn('加载组合估值失败:', e);
    }
    return null;
}

// 加载客户盈亏历史
export async function fetchClientPnlHistory(id, rangeDays = 30) {
    if (!id) return null;
    try {
        const resp = await fetch(`/api/clients/${id}/pnl-history?range=${rangeDays}`, {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('stock_review_token')}` }
        });
        if (resp.ok) {
            pnlHistoryData = await resp.json();
            return pnlHistoryData;
        }
    } catch (e) {
        console.warn('加载盈亏历史失败:', e);
    }
    return null;
}

// 从后端加载客户（后端已按角色做行级过滤，前端直接信任返回结果）
export async function loadClients() {
    if (!isLoggedIn()) {
        clients = [];
        return clients;
    }
    try {
        const raw = await fetchClients();
        clients = (raw || []).map(mapClient);
    } catch (e) {
        console.warn('加载客户数据失败:', e);
        clients = [];
    }
    return clients;
}

// 更新客户字段并回写后端（后端再次校验可见性）
export async function updateClientRemote(id, patch) {
    const raw = await updateClient(id, patch);
    const mapped = mapClient(raw);
    const idx = clients.findIndex(c => c.id === id);
    if (idx !== -1) clients[idx] = mapped;
    return mapped;
}

export function getCurrentClient() {
    const visible = getVisibleClients(clients);
    return visible.find(c => c.id === currentClientId) || visible[0] || null;
}

export function setCurrentClient(id) { currentClientId = id; }

export function setClientWarnOnly(val) { clientWarnOnly = !!val; }

// 客户资产 / 盈亏统计（实时价格降级链：实时行情 → 成本价兜底）
export function clientStats(client) {
    if (!client) return { totalMarket: 0, totalCost: 0, totalPnl: 0, totalPnlPct: 0, totalAssets: 0, positions: [] };
    const positions = Array.isArray(client.positions) ? client.positions : [];
    const prices = getPriceMap(positions);
    let totalMarket = 0, totalCost = 0;
    positions.forEach(p => {
        totalMarket += prices[p.code] * p.quantity;
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

// 客户风险预警计算（实时价格降级链）
export function clientRiskAlerts(client) {
    const alerts = [];
    if (!client) return alerts;
    const s = clientStats(client);
    const positions = s.positions || [];
    const prices = getPriceMap(positions);
    // 亏损超阈值：总亏损 > -8%
    if (s.totalPnl < 0 && s.totalPnlPct <= -8) {
        alerts.push({ type: 'loss', level: 'high', title: '组合亏损超阈值', desc: '累计亏损 ' + s.totalPnlPct.toFixed(1) + '%，建议关注止损与调仓' });
    } else if (s.totalPnl < 0) {
        alerts.push({ type: 'loss', level: 'mid', title: '组合浮亏', desc: '累计亏损 ' + s.totalPnlPct.toFixed(1) + '%' });
    }
    // 行业集中度 > 50%
    const sectorMap = {};
    positions.forEach(p => { sectorMap[p.sector] = (sectorMap[p.sector] || 0) + prices[p.code] * p.quantity; });
    const topSector = Object.entries(sectorMap).sort((a, b) => b[1] - a[1])[0];
    if (topSector && s.totalMarket > 0) {
        const pct = topSector[1] / s.totalMarket * 100;
        if (pct > 50) alerts.push({ type: 'sector', level: 'high', title: '行业集中度过高', desc: topSector[0] + ' 占比 ' + pct.toFixed(0) + '%，单一行业风险较大' });
    }
    // 单票占比 > 40%
    positions.forEach(p => {
        if (s.totalMarket > 0 && prices[p.code] * p.quantity / s.totalMarket * 100 > 40) {
            alerts.push({ type: 'stock', level: 'high', title: '单票占比过高', desc: p.name + ' 占比 ' + (prices[p.code] * p.quantity / s.totalMarket * 100).toFixed(0) + '%，集中持仓风险高' });
        }
    });
    // 亏损单只 > 20%
    positions.forEach(p => {
        const pnlPct = (prices[p.code] - p.costPrice) / p.costPrice * 100;
        if (pnlPct <= -20) alerts.push({ type: 'stock', level: 'mid', title: '个股深度亏损', desc: p.name + ' 亏损 ' + pnlPct.toFixed(1) + '%' });
    });
    return alerts;
}

// 获取用户持仓（当前可见客户优先 → 分享版注入 → 空）
// 无可见客户时返回空数组，避免把示例/本地缓存数据泄露给无权访问的用户。
export function getUserPositions() {
    const client = getCurrentClient();
    if (client && Array.isArray(client.positions)) return client.positions;
    if (Array.isArray(EMBEDDED_POSITIONS) && EMBEDDED_POSITIONS.length) return EMBEDDED_POSITIONS;
    return [];
}

export async function saveUserPositions(positions) {
    const client = getCurrentClient();
    if (!client) return;
    // 乐观更新内存；后端按可见范围二次校验
    client.positions = positions;
    const payload = positions.map(p => ({
        name: p.name, code: p.code, sector: p.sector,
        quantity: p.quantity, cost_price: p.costPrice,
    }));
    await updateClientPositions(client.id, payload);
}

// 保存交易记录（买入/卖出）
export async function saveTransaction(clientId, txData) {
    if (!clientId) return null;
    try {
        const resp = await fetch(`/api/clients/${clientId}/transactions`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${localStorage.getItem('stock_review_token')}`
            },
            body: JSON.stringify(txData)
        });
        if (resp.ok) {
            return await resp.json();
        } else {
            console.warn('保存交易记录失败:', resp.status, await resp.text());
        }
    } catch (e) {
        console.warn('保存交易记录异常:', e);
    }
    return null;
}

// 获取 / 保存其它用户数据（可用资金等）
export function getUserData() {
    const client = getCurrentClient();
    if (client) return { availableCash: client.availableCash, todayPnL: client.todayPnL ?? null };
    if (EMBEDDED_USERDATA && typeof EMBEDDED_USERDATA === 'object') return EMBEDDED_USERDATA;
    return { availableCash: 0, todayPnL: null };
}

// 解析客户服务关系（顾问 / 客服显示名）
// 顾问名以后端返回的 advisor_name 为准，避免前端静态名单与后端漂移。
export function getClientRelations(client) {
    const advisorName = client?.advisorName
        || (ADVISOR_POOL.find(a => a.id === client?.advisorId)?.name)
        || '未分配';
    const serviceNames = (client?.serviceIds || []).map(id => {
        const s = SERVICE_STAFF_POOL.find(x => x.id === id);
        return s ? s.name : id;
    });
    return {
        advisorId: client?.advisorId || null,
        advisorName,
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
    // 后端 /api/clients 已按角色做行级过滤；此处仅应用工作台筛选/排序
    let list = getVisibleClients(clients).filter(c => {
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
