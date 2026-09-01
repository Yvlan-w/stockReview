// ============================================================
// 格式化工具（纯函数，无依赖）
// ============================================================

export function formatCurrency(num, showSign = false) {
    const formatted = Math.abs(num).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (showSign && num > 0) return '+' + formatted;
    if (showSign && num < 0) return '-' + formatted;
    return formatted;
}

export function formatNumber(num, decimals = 2) {
    return num.toLocaleString('zh-CN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function getPnLColor(value) {
    if (value > 0) return 'text-up';
    if (value < 0) return 'text-down';
    return 'text-ink';
}

export function getBadgeClass(type) {
    switch (type) {
        case 'buy': return 'badge-up';
        case 'sell': return 'badge-down';
        case 'adjust': return 'badge-adjust';
        default: return 'badge-neutral';
    }
}

export function getTypeLabel(type) {
    switch (type) {
        case 'buy': return '买入';
        case 'sell': return '卖出';
        case 'adjust': return '调整';
        default: return '';
    }
}

export function getResultBadge(result) {
    switch (result) {
        case 'success': return '<span class="px-2 py-0.5 text-xs font-medium rounded-full bg-positive/10 text-positive">成功</span>';
        case 'partial': return '<span class="px-2 py-0.5 text-xs font-medium rounded-full badge-warning">部分成交</span>';
        case 'failed': return '<span class="px-2 py-0.5 text-xs font-medium rounded-full bg-negative/10 text-negative">失败</span>';
        default: return '';
    }
}

export function getImpactBadge(impact) {
    switch (impact) {
        case 'bullish': return '<span class="inline-flex items-center px-2.5 py-1 text-xs font-semibold rounded-full bg-positive/10 text-positive">● 利好</span>';
        case 'bearish': return '<span class="inline-flex items-center px-2.5 py-1 text-xs font-semibold rounded-full bg-negative/10 text-negative">● 利空</span>';
        default: return '<span class="inline-flex items-center px-2.5 py-1 text-xs font-semibold rounded-full badge-neutral">● 中性</span>';
    }
}

// 板块颜色映射
const SECTOR_COLORS = {
    '消费':   { bg: 'bg-orange-100',   text: 'text-orange-700',   bar: '#f97316' },
    '金融':   { bg: 'bg-blue-100',     text: 'text-blue-700',     bar: '#3b82f6' },
    '科技':   { bg: 'bg-purple-100',   text: 'text-purple-700',   bar: '#8b5cf6' },
    '医疗':   { bg: 'bg-green-100',    text: 'text-green-700',    bar: '#10b981' },
    '新能源': { bg: 'bg-teal-100',     text: 'text-teal-700',     bar: '#14b8a6' },
    '光伏':   { bg: 'bg-yellow-100',   text: 'text-yellow-700',   bar: '#eab308' },
    '资源':   { bg: 'bg-stone-100',    text: 'text-stone-700',    bar: '#78716c' },
    '军工':   { bg: 'bg-red-100',      text: 'text-red-700',      bar: '#ef4444' },
    '半导体': { bg: 'bg-indigo-100',   text: 'text-indigo-700',   bar: '#6366f1' },
    '地产':   { bg: 'bg-pink-100',     text: 'text-pink-700',     bar: '#ec4899' },
    '化工':   { bg: 'bg-lime-100',     text: 'text-lime-700',     bar: '#84cc16' },
    '其他':   { bg: 'bg-gray-100',     text: 'text-gray-700',     bar: '#9ca3af' },
};

export function getSectorBadgeClass(sector) {
    const c = SECTOR_COLORS[sector] || SECTOR_COLORS['其他'];
    return `${c.bg} ${c.text}`;
}

export function getSectorBarColor(sector) {
    return (SECTOR_COLORS[sector] || SECTOR_COLORS['其他']).bar;
}

// 金额缩写（万/亿）
export function fmtMoney(v) {
    const neg = v < 0;
    const abs = Math.abs(v);
    if (abs >= 1e8) return (neg ? '-' : '') + (abs / 1e8).toFixed(2) + '亿';
    if (abs >= 1e4) return (neg ? '-' : '') + (abs / 1e4).toFixed(1) + '万';
    return (neg ? '-' : '') + '¥' + abs.toFixed(0);
}

// 去除 HTML 标签
export function stripHtml(html) {
    const tmp = document.createElement('div');
    tmp.innerHTML = html;
    return tmp.textContent || tmp.innerText || '';
}

// 相对时间格式化
export function formatTimeAgo(dateStr) {
    const date = new Date(dateStr);
    const now = new Date();
    const diff = Math.floor((now - date) / 1000);
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
    return Math.floor(diff / 86400) + '天前';
}

// 从标题提取关键词标签
export function extractTags(title) {
    const keywords = ['A股', '美股', '港股', '新能源', '半导体', '科技', '金融', '医药', '消费', '央行', '美联储', '政策', '宏观', '北向资金', 'GDP'];
    return keywords.filter(kw => title.includes(kw)).slice(0, 3).concat('财经');
}
