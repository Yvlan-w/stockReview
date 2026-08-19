// ============================================================
// 行情数据服务：从自己后端读取快照（后端定时调东财并落库）
// 前端不再直连第三方行情，彻底解决 CORS/Referer/连接重置等问题
// 注意：loadJsonp 保留供 newsService.js 的资讯 JSONP 调用使用
// ============================================================

// 行情数据状态
export let marketDataState = {
    source: 'mock',           // 'real' | 'mock'
    loading: false,
    realtime: null,           // parsed realtime data
    kline: null,              // parsed SH kline data
    sectors: null,            // parsed sector data (top 80 industries sorted by turnover desc, from East Money)
    analysis: null,           // market analysis: rotation + drivers
    lastUpdated: null,        // 后端快照 updated_at 时间
    fetchStatus: 'pending',   // 'ok' | 'fail' | 'pending'
    fetchError: null,
    error: null
};

// --- 资讯 JSONP 通用加载器（newsService 在用，行情接口已移除 ---

/**
 * 通用 JSONP 加载器（动态 <script> 标签）
 */
export function loadJsonp(url, callbackParam = 'cb', timeout = 12000) {
    return new Promise((resolve, reject) => {
        const cbName = 'cb_' + Date.now() + '_' + Math.floor(Math.random() * 100000);
        const sep = url.includes('?') ? '&' : '?';
        const script = document.createElement('script');
        script.referrerPolicy = 'no-referrer';
        script.src = `${url}${sep}${callbackParam}=${cbName}`;

        const timer = setTimeout(() => {
            cleanup();
            reject(new Error('请求超时，请检查网络'));
        }, timeout);

        function cleanup() {
            clearTimeout(timer);
            if (script.parentNode) script.parentNode.removeChild(script);
            try { delete window[cbName]; } catch (e) { window[cbName] = undefined; }
        }

        window[cbName] = (data) => { cleanup(); resolve(data); };
        script.onerror = () => { cleanup(); reject(new Error('脚本加载失败，可能被浏览器拦截')); };
        document.head.appendChild(script);
    });
}

// --- 行情接口（走自己后端，不再直连东财） ---

const API_BASE = '/api/market';

async function _fetchMarket(path) {
    const resp = await fetch(`${API_BASE}${path}`, { cache: 'no-store' });
    if (!resp.ok) throw new Error(`后端行情接口失败: ${resp.status}`);
    return resp.json();
}

/**
 * 拉取实时行情快照（后端从数据库返回，<1ms）。
 * 空库或后端初始刷新未完成时抛错（调用方按"保留上次结果"策略处理）。
 */
export async function fetchRealtimeMarketData() {
    const body = await _fetchMarket('/realtime');
    marketDataState.fetchStatus = body.fetch_status || 'pending';
    marketDataState.fetchError = body.fetch_error || null;
    if (!body.data) {
        throw new Error(body.fetch_status === 'pending' ? '行情数据尚未就绪' : `行情获取失败: ${body.fetch_error || body.fetch_status}`);
    }
    const d = body.data;
    marketDataState.source = 'real';
    marketDataState.lastUpdated = body.updated_at ? new Date(body.updated_at) : new Date();
    return {
        indices: d.indices,
        totalVolume: d.totalVolume,
        prevVolume: d.prevVolume,
        advCount: d.advCount,
        decCount: d.decCount,
        flatCount: d.flatCount,
        totalStocks: d.totalStocks,
    };
}

/**
 * 拉取指定指数近 N 日 K 线（后端从数据库返回）。
 * @param {string} indexCode - 指数代码: 1.000001(上证) 0.399001(深证) 0.399006(创业板) 1.000688(科创50)
 */
export async function fetchShKlineData(indexCode = '1.000001') {
    const body = await _fetchMarket(`/kline?index=${indexCode}`);
    if (!body.data || body.data.length === 0) {
        throw new Error('K 线数据尚未就绪');
    }
    return body.data;
}

/**
 * 拉取行业板块行情（涨跌幅排序，最多 80 条）。
 */
export async function fetchSectors(limit = 80) {
    const body = await _fetchMarket(`/sectors?limit=${limit}`);
    if (!body.data || body.data.length === 0) {
        throw new Error('板块数据尚未就绪');
    }
    return body.data;
}

/**
 * 拉取市场深度分析（高低切 / 领涨方向 / 核心驱动因素）。
 */
export async function fetchMarketAnalysis() {
    const body = await _fetchMarket('/analysis');
    if (!body.data) {
        throw new Error('市场分析数据尚未就绪');
    }
    return body.data;
}
