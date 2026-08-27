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

// ---- 客户盈亏摘要（客户列表批量实时数据）----
// 结构：{ summaries: {clientId: {totalMarketValue,totalPnl,totalPnlPct,totalAssets}}, updated_at }
export let clientSummaries = null;
export let summariesLoading = false;

// 批量拉取当前用户可见客户的实时盈亏摘要（后端 15s 缓存）
export async function fetchClientSummaries(force = false) {
    if (summariesLoading && !force) return clientSummaries;
    summariesLoading = true;
    try {
        const resp = await fetch('/api/clients/summaries', {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('stock_review_token')}` }
        });
        if (resp.ok) {
            clientSummaries = await resp.json();
        }
    } catch (e) {
        console.warn('加载客户盈亏摘要失败:', e);
    } finally {
        summariesLoading = false;
    }
    return clientSummaries;
}

// 单客户盈亏摘要：优先后端实时数据，降级本地成本价估算
// 持仓盈亏（floatingPnl）仅含当前持仓浮动盈亏；totalPnl 为累计口径（含已实现）
export function clientSummaryStats(client) {
    const s = clientSummaries?.summaries?.[client?.id];
    if (s) {
        return {
            totalMarket: s.totalMarketValue,
            totalPnl: s.totalPnl,
            totalPnlPct: s.totalPnlPct,
            floatingPnl: s.totalFloatingPnl ?? (s.totalPnl ?? 0),
            floatingPnlPct: s.floatingPnlPct ?? (s.totalPnlPct ?? 0),
            totalAssets: s.totalAssets,
            isRealtime: true,
        };
    }
    // 降级：本地 clientStats（价格缓存 → 成本价兜底）——本地产出即浮动口径
    const local = clientStats(client);
    return {
        ...local,
        floatingPnl: local.totalPnl,
        floatingPnlPct: local.totalPnlPct,
        isRealtime: false,
    };
}

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
        serviceNames: raw.service_names || [],
        positions: (raw.positions || []).map(p => ({
            name: p.name, code: p.code, sector: p.sector,
            quantity: p.quantity, costPrice: p.cost_price,
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

// 调仓/新建持仓成功后：将后端组合数据同步到当前客户内存态（持仓/现金/今日盈亏），
// 使 getUserPositions() 立即反映最新持仓，配合 refreshAll 实现秒级界面刷新
export function syncClientState(portfolio) {
    const client = getCurrentClient();
    if (!client || !portfolio || !Array.isArray(portfolio.positions)) return;

    // 持仓：保留内存中已有但响应缺失的附加字段，响应字段优先
    const oldMap = new Map((client.positions || []).map(p => [p.code, p]));
    client.positions = portfolio.positions.map(p => ({
        name: p.name,
        code: p.code,
        sector: p.sector ?? oldMap.get(p.code)?.sector ?? '',
        quantity: p.quantity,
        costPrice: p.costPrice,
    }));

    if (typeof portfolio.availableCash === 'number') {
        client.availableCash = portfolio.availableCash;
    }
    if (typeof portfolio.todayPnl === 'number') {
        client.todayPnl = portfolio.todayPnl;
    }
}

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

// ---- 风险预警规则参数（与后端 risk_engine.py 保持一致）----
// 各风险等级的组合浮亏/浮盈预警阈值（黄色, 红色），单位：百分比
const RISK_THRESHOLDS = {
    '保守型': [4, 5],
    '稳健型': [8, 10],
    '平衡型': [12, 15],
    '积极型': [16, 20],
    '激进型': [20, 25],
};
const DEFAULT_THRESHOLDS = RISK_THRESHOLDS['稳健型'];
// 单票占比预警阈值：30% ~ 50% 黄色，> 50% 红色
const STOCK_RATIO_YELLOW = 30;
const STOCK_RATIO_RED = 50;
// 行业集中度预警阈值：> 60% 红色
const SECTOR_RATIO_RED = 60;

// 客户风险预警计算（实时价格降级链，规则与后端 risk_engine 对齐）
// level：high(红) / mid(黄) / positive(绿·恭喜)
export function clientRiskAlerts(client) {
    const alerts = [];
    if (!client) return alerts;
    const s = clientStats(client);
    const positions = s.positions || [];
    const prices = getPriceMap(positions);
    const [yellow, red] = RISK_THRESHOLDS[client.riskLevel] || DEFAULT_THRESHOLDS;
    const riskLevel = client.riskLevel || '稳健型';

    // ---- 组合浮亏 / 浮盈（互斥；仅当前持仓的浮动盈亏口径）----
    if (s.totalPnl < 0) {
        if (s.totalPnlPct <= -red) {
            alerts.push({ type: 'loss', level: 'high', title: '组合浮亏超红色预警线', desc: '组合浮亏 ' + s.totalPnlPct.toFixed(1) + '%，已超过 ' + riskLevel + ' 客户红色预警线 -' + red + '%，建议立即关注止损与调仓' });
        } else if (s.totalPnlPct <= -yellow) {
            alerts.push({ type: 'loss', level: 'mid', title: '组合浮亏超黄色预警线', desc: '组合浮亏 ' + s.totalPnlPct.toFixed(1) + '%，已超过 ' + riskLevel + ' 客户黄色预警线 -' + yellow + '%，建议关注组合回撤' });
        }
    } else if (s.totalPnl > 0) {
        if (s.totalPnlPct >= red) {
            alerts.push({ type: 'gain', level: 'positive', title: '恭喜：组合浮盈表现亮眼', desc: '组合浮盈 ' + s.totalPnlPct.toFixed(1) + '%，达到 ' + riskLevel + ' 客户红色指标 ' + red + '%，表现优秀，可考虑适当止盈' });
        } else if (s.totalPnlPct >= yellow) {
            alerts.push({ type: 'gain', level: 'positive', title: '恭喜：组合浮盈达标', desc: '组合浮盈 ' + s.totalPnlPct.toFixed(1) + '%，达到 ' + riskLevel + ' 客户黄色指标 ' + yellow + '%，收益稳健' });
        }
    }

    // ---- 单票占比：30% ~ 50% 黄，> 50% 红 ----
    positions.forEach(p => {
        if (s.totalMarket <= 0) return;
        const pct = prices[p.code] * p.quantity / s.totalMarket * 100;
        if (pct > STOCK_RATIO_RED) {
            alerts.push({ type: 'stock', level: 'high', title: '单票占比过高（红色）', desc: p.name + ' 占组合 ' + pct.toFixed(0) + '%，超过 50% 红色预警线，集中持仓风险极高，建议分批调降' });
        } else if (pct >= STOCK_RATIO_YELLOW) {
            alerts.push({ type: 'stock', level: 'mid', title: '单票占比偏高（黄色）', desc: p.name + ' 占组合 ' + pct.toFixed(0) + '%，处于 30%~50% 黄色预警区间，建议关注集中度风险' });
        }
    });

    // ---- 行业集中度：> 60% 红 ----
    const sectorMap = {};
    positions.forEach(p => { sectorMap[p.sector] = (sectorMap[p.sector] || 0) + prices[p.code] * p.quantity; });
    if (s.totalMarket > 0) {
        Object.entries(sectorMap).forEach(([sector, val]) => {
            const pct = val / s.totalMarket * 100;
            if (pct > SECTOR_RATIO_RED) {
                alerts.push({ type: 'sector', level: 'high', title: '行业集中度过高', desc: sector + ' 行业占组合 ' + pct.toFixed(0) + '%，超过 60% 红色预警线，单一行业风险较大，建议均衡配置' });
            }
        });
    }
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

// 保存交易记录（买入/卖出）—— 保留兼容旧流程；新代码优先用 adjustClient
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

// 原子调仓（买入/卖出）：后端一次性更新 交易流水+成本批次+持仓+现金+当日快照
// adjustData: { code, name?, sector?, action: 'buy'|'sell', quantity, price,
//               fee_mode?: 'rate'|'fixed', fee_value?: number,
//               trade_date?: 'YYYY-MM-DD', from_cash?: true, cost_method?: 'average'|'fifo'|'lifo' }
// 成功返回 { transaction, position, available_cash, portfolio }；校验失败抛 Error(message)
export async function executeAdjustApi(clientId, adjustData) {
    if (!clientId) throw new Error('未选择客户');
    const resp = await fetch(`/api/clients/${clientId}/adjust`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('stock_review_token')}`
        },
        body: JSON.stringify(adjustData)
    });
    if (resp.ok) {
        return await resp.json();
    }
    let message = `调仓失败，请稍后重试`;
    try {
        const text = await resp.text();
        const body = JSON.parse(text);
        if (body?.detail) message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* 忽略解析失败，使用默认 message */ }
    throw new Error(message);
}

// 兼容别名：旧代码调用 adjustClient 时转发到 executeAdjustApi
export async function adjustClient(clientId, payload) {
    return await executeAdjustApi(clientId, payload);
}

// 查询成本基础汇总（含未实现盈亏），支持 3 种成本法
export async function fetchCostBasis(clientId, { method = 'average', code = null } = {}) {
    if (!clientId) return null;
    const params = new URLSearchParams();
    params.set('method', method);
    if (code) params.set('code', code);
    try {
        const resp = await fetch(`/api/clients/${clientId}/cost-basis?${params.toString()}`, {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('stock_review_token')}` }
        });
        if (resp.ok) return await resp.json();
        console.warn('查询成本基础失败:', resp.status);
    } catch (e) {
        console.warn('查询成本基础异常:', e);
    }
    return null;
}

// 交易流水按股票分组汇总：买卖量额、累计已实现盈亏、手续费分类总计
export async function fetchTransactionsSummary(clientId, { code = null } = {}) {
    if (!clientId) return null;
    const params = new URLSearchParams();
    if (code) params.set('code', code);
    try {
        const resp = await fetch(`/api/clients/${clientId}/transactions/summary?${params.toString()}`, {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('stock_review_token')}` }
        });
        if (resp.ok) return await resp.json();
        console.warn('查询交易汇总失败:', resp.status);
    } catch (e) {
        console.warn('查询交易汇总异常:', e);
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
// 优先级：1) 后端返回的 advisor_name / service_names → 2) 前端静态池匹配 → 3) id 兜底
//   后端返回的 names 从 users 表关联获取，保证云端与本地一致；静态池仅作为兼容性兜底。
export function getClientRelations(client) {
    const advisorName = client?.advisorName
        || (ADVISOR_POOL.find(a => a.id === client?.advisorId)?.name)
        || client?.advisorId
        || '未分配';
    const serviceIds = client?.serviceIds || [];
    const backendNames = client?.serviceNames || [];
    const serviceNames = serviceIds.map((id, i) => {
        if (backendNames[i]) return backendNames[i];
        const s = SERVICE_STAFF_POOL.find(x => x.id === id);
        return s ? s.name : id;
    });
    return {
        advisorId: client?.advisorId || null,
        advisorName,
        serviceIds,
        serviceNames
    };
}

// 搜索股票（添加持仓自动填充）：代码 / 名称 / 拼音 → 候选列表
// 返回 { results: [{code, name, price, change_pct, industry, sector}] }
export async function searchStocksApi(keyword, limit = 8) {
    const resp = await fetch(`/api/stocks/search?keyword=${encodeURIComponent(keyword)}&limit=${limit}`);
    if (resp.ok) {
        return await resp.json();
    }
    throw new Error(`股票搜索失败（${resp.status}）`);
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
