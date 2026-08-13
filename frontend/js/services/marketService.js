// ============================================================
// 行情数据服务（东方财富 JSONP 实时源）
// ============================================================
import { MARKET_APIS, MARKET_SECIDS, SH_INDEX_SECID, REALTIME_FIELDS, EASTMONEY_UT } from '../core/config.js';

// 行情数据状态
export let marketDataState = {
    source: 'mock',      // 'real' | 'mock'
    loading: false,
    realtime: null,      // parsed realtime data
    kline: null,         // parsed SH kline data
    lastUpdated: null,
    error: null
};

/**
 * 通用 JSONP 加载器（动态 <script> 标签）
 */
export function loadJsonp(url, callbackParam = 'cb', timeout = 12000) {
    return new Promise((resolve, reject) => {
        const cbName = 'em_callback_' + Date.now() + '_' + Math.floor(Math.random() * 100000);
        const sep = url.includes('?') ? '&' : '?';
        const script = document.createElement('script');
        script.referrerPolicy = 'no-referrer'; // 避免携带 localhost/127.0.0.1 来源导致东财重置连接(ERR_ABORTED)
        script.src = `${url}${sep}${callbackParam}=${cbName}`;

        const timer = setTimeout(() => {
            cleanup();
            reject(new Error('请求超时，请检查网络'));
        }, timeout);

        function cleanup() {
            clearTimeout(timer);
            if (script.parentNode) script.parentNode.removeChild(script);
            try {
                delete window[cbName];
            } catch (e) {
                window[cbName] = undefined;
            }
        }

        window[cbName] = (data) => {
            cleanup();
            resolve(data);
        };

        script.onerror = () => {
            cleanup();
            reject(new Error('脚本加载失败，可能被浏览器拦截'));
        };

        document.head.appendChild(script);
    });
}

/**
 * 拉取实时指数 + 涨跌家数（东方财富）
 */
export async function fetchRealtimeMarketData() {
    const query = `fltt=2&invt=2&ut=${EASTMONEY_UT}&fields=${REALTIME_FIELDS}&secids=${MARKET_SECIDS}`;
    let data = null;
    let lastErr = null;

    for (const host of MARKET_APIS.realtime) {
        try {
            data = await loadJsonp(`${host}?${query}`, 'cb', 10000);
            break;
        } catch (err) {
            lastErr = err;
        }
    }

    if (!data || data.rc !== 0 || !data.data || !Array.isArray(data.data.diff)) {
        throw lastErr || new Error('接口返回数据格式异常');
    }

    const items = data.data.diff;
    const sh = items.find(i => i.f13 === 1 && i.f12 === '000001');
    const sz = items.find(i => i.f13 === 0 && i.f12 === '399001');
    const cy = items.find(i => i.f13 === 0 && i.f12 === '399006');

    if (!sh || !sz || !cy) {
        throw new Error('未能获取完整指数数据');
    }

    // 沪深合并全市场涨跌家数（深证成指已含创业板，故不重复加计 399006）
    const advCount = (sh.f104 || 0) + (sz.f104 || 0);
    const decCount = (sh.f105 || 0) + (sz.f105 || 0);
    const flatCount = (sh.f106 || 0) + (sz.f106 || 0);
    const totalStocks = advCount + decCount + flatCount;

    // 两市成交额（亿元，f6 为元）
    const totalTurnoverYi = ((sh.f6 || 0) + (sz.f6 || 0)) / 1e8;

    // 前一日成交额估算：SH 日 K 线前一日成交额 + 今日沪深比
    let prevVolumeYi = marketDataState.realtime?.prevVolume;
    if (marketDataState.kline && marketDataState.kline.length >= 2) {
        const prevDay = marketDataState.kline[marketDataState.kline.length - 2];
        const shPrevTurnoverYi = (prevDay.turnover || 0) / 1e8;
        const shTodayTurnoverYi = (sh.f6 || 0) / 1e8;
        const szTodayTurnoverYi = (sz.f6 || 0) / 1e8;
        const ratio = shTodayTurnoverYi > 0 ? szTodayTurnoverYi / shTodayTurnoverYi : 1;
        prevVolumeYi = shPrevTurnoverYi * (1 + ratio);
    }
    if (!prevVolumeYi || prevVolumeYi <= 0) {
        prevVolumeYi = totalTurnoverYi * 0.95; // fallback
    }

    return {
        indices: [
            {
                name: '上证指数',
                value: sh.f2,
                prevClose: (sh.f2 || 0) - (sh.f4 || 0),
                change: sh.f4,
                changePct: sh.f3
            },
            {
                name: '深证成指',
                value: sz.f2,
                prevClose: (sz.f2 || 0) - (sz.f4 || 0),
                change: sz.f4,
                changePct: sz.f3
            },
            {
                name: '创业板指',
                value: cy.f2,
                prevClose: (cy.f2 || 0) - (cy.f4 || 0),
                change: cy.f4,
                changePct: cy.f3
            }
        ],
        totalVolume: totalTurnoverYi,
        prevVolume: prevVolumeYi,
        advCount,
        decCount,
        flatCount,
        totalStocks
    };
}

// 内存级 K 线缓存（当日有效）
export let klineCache = { date: null, data: null };

/**
 * 拉取上证指数近 30 个交易日日 K 线（东方财富）
 */
export async function fetchShKlineData() {
    const today = new Date().toISOString().slice(0, 10);
    if (klineCache.date === today && klineCache.data) {
        return klineCache.data;
    }

    const fields1 = 'f1,f2,f3,f4,f5,f6';
    const fields2 = 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61';
    const url = `${MARKET_APIS.kline}?secid=${SH_INDEX_SECID}&ut=${EASTMONEY_UT}&fields1=${fields1}&fields2=${fields2}&klt=101&fqt=0&end=20500101&lmt=30`;
    const data = await loadJsonp(url, 'cb', 12000);

    if (!data || data.rc !== 0 || !data.data || !Array.isArray(data.data.klines)) {
        throw new Error('K线接口返回数据格式异常');
    }

    const parsed = data.data.klines.map(line => {
        const parts = line.split(',');
        return {
            fullDate: parts[0],
            date: parts[0].slice(5),          // MM-DD
            open: parseFloat(parts[1]),
            close: parseFloat(parts[2]),
            high: parseFloat(parts[3]),
            low: parseFloat(parts[4]),
            volume: parseFloat(parts[5]),     // 成交量（手）
            turnover: parseFloat(parts[6]),   // 成交额（元）
            amplitude: parseFloat(parts[7]),
            changePct: parseFloat(parts[8]),
            change: parseFloat(parts[9]),
            turnoverRate: parseFloat(parts[10])
        };
    });

    klineCache.date = today;
    klineCache.data = parsed;
    return parsed;
}
