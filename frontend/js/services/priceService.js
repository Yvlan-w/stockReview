// ============================================================
// 实时价格服务：价格缓存 + 降级策略
// 价格来源：后端 /api/clients/{id}/portfolio（实时行情 → 日K线 → 成本价兜底）
// ============================================================

// 价格缓存 {code: currentPrice}
let priceCache = {};
let cacheTime = 0;
const CACHE_TTL = 30 * 1000; // 30 秒缓存有效期

/**
 * 从组合估值数据更新价格缓存。
 * 由 workbench 在获取 portfolio 后调用。
 * @param {object} portfolio /api/clients/{id}/portfolio 返回值
 */
export function updatePriceCache(portfolio) {
    if (!portfolio || !Array.isArray(portfolio.positions)) return;
    const next = {};
    portfolio.positions.forEach(p => {
        if (p.currentPrice && p.currentPrice > 0) {
            next[p.code] = p.currentPrice;
        }
    });
    priceCache = next;
    cacheTime = Date.now();
}

/** 缓存是否过期 */
export function isCacheStale() {
    return Date.now() - cacheTime > CACHE_TTL;
}

/**
 * 获取单只股票实时价格（缓存 → 降级值兜底）。
 * @param {string} code 股票代码
 * @param {number|null} fallback 降级价格（通常传 costPrice）
 * @returns {number} 实时价格或降级价格，均无效时返回 0
 */
export function getPrice(code, fallback = null) {
    const cached = priceCache[code];
    if (cached && cached > 0) return cached;
    if (fallback && fallback > 0) return fallback;
    return 0;
}

/**
 * 批量构建价格映射（实时价格降级链）。
 * @param {Array} positions 持仓列表（含 code、costPrice）
 * @returns {object} {code: price}
 */
export function getPriceMap(positions = []) {
    const map = {};
    positions.forEach(p => {
        map[p.code] = getPrice(p.code, p.costPrice);
    });
    return map;
}

/** 清空缓存（切换客户时调用） */
export function clearPriceCache() {
    priceCache = {};
    cacheTime = 0;
}
