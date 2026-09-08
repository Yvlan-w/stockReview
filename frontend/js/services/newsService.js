// ============================================================
// 实时资讯服务（多数据源 JSONP + 自动降级 + 兜底缓存数据）
// ============================================================
import { stripHtml, formatTimeAgo, extractTags } from '../core/formatters.js';
import { loadJsonp } from './marketService.js';
import { getToken } from './authService.js';

// 简单 HTML 转义（标题/代码渲染用）
function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

// 源标识 -> 中文名
function sourceLabel(src) {
    if (src === 'eastmoney_7x24') return '东方财富';
    if (src === 'sina_7x24') return '新浪财经';
    return src || '资讯';
}

// 当前展示持仓相关快讯所归属的客户（供面板「刷新」按钮复用，避免与 workbench 循环依赖）
let _relatedClientId = null;

export let liveNewsLoaded = false;

// 最近一次成功加载的资讯，用于刷新失败时保留上次结果
let lastNewsItems = [];

// 当前可见条数（默认仅展示前 4 条，"显示更多"每次追加）
let newsVisibleCount = 4;
const NEWS_INITIAL_COUNT = 4;
const NEWS_PAGE_SIZE = 5;

// 持仓相关快讯：默认仅展示前 4 条，其余通过"显示更多"按钮展开（与实时资讯一致）
const RELATED_INITIAL_COUNT = 4;
const RELATED_PAGE_SIZE = 5;
let relatedNewsItems = [];
let relatedVisibleCount = RELATED_INITIAL_COUNT;

// 各数据源当前可用状态（供界面展示）
export let newsSourceStatus = { ok: [], fail: [] };

// 兼容 Safari 的日期解析（Safari 不识别 "YYYY-MM-DD HH:mm:ss"，需替换空格为 T）
function safeDate(input) {
    if (input instanceof Date) return input;
    const s = String(input || '').trim();
    if (!s) return new Date();
    const d = new Date(s.replace(' ', 'T'));
    return isNaN(d.getTime()) ? new Date() : d;
}

// ---- 数据源定义：每个源提供 buildUrl() 与 parse(data) ----
const NEWS_SOURCES = [
    {
        id: 'sina_zhibo',
        name: '新浪财经',
        label: '7×24快讯',
        buildUrl: () =>
            'https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=20&zhibo_id=152&tag_id=0&dire=f&dpc=1&type=0',
        callbackParam: 'callback',
        parse: (data) => {
            const list = data?.result?.data?.feed?.list || [];
            return list.map(it => {
                const text = stripHtml(it.rich_text || '').trim();
                const pubDate = safeDate(it.create_time);
                return {
                    source: '新浪财经',
                    title: text,
                    summary: '',
                    link: it.docurl || 'https://finance.sina.com.cn/7x24/',
                    pubDate,
                    time: formatTimeAgo(pubDate),
                    tags: extractTags(text)
                };
            });
        }
    },
    {
        id: 'eastmoney_fastnews',
        name: '东方财富',
        label: '7×24快讯',
        buildUrl: () => {
            const t = Date.now();
            return `https://np-weblist.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_724&fastColumn=102&sortEnd=0&pageSize=20&req_trace=${t}&_=${t - 2}`;
        },
        callbackParam: 'callback',
        parse: (data) => {
            const list = data?.data?.fastNewsList || [];
            return list.map(it => {
                const title = stripHtml(it.title || it.summary || '').trim();
                const pubDate = safeDate(it.showTime || it.ctime);
                return {
                    source: '东方财富',
                    title,
                    summary: '',
                    link: it.code ? `https://finance.eastmoney.com/a/${it.code}.html` : 'https://kuaixun.eastmoney.com/',
                    pubDate,
                    time: formatTimeAgo(pubDate),
                    tags: extractTags(title)
                };
            });
        }
    },
    {
        id: 'sina_roll',
        name: '新浪财经',
        label: '滚动新闻',
        buildUrl: () =>
            'https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num=20&page=1',
        callbackParam: 'callback',
        parse: (data) => {
            const list = data?.result?.data || [];
            return list.map(it => {
                const title = stripHtml(it.title || '').trim();
                const summary = stripHtml(it.intro || '').trim();
                const date = new Date(parseInt(it.ctime || it.intime || '0') * 1000);
                return {
                    source: '新浪财经',
                    title,
                    summary: summary ? summary.substring(0, 120) + '...' : '',
                    link: it.url || it.wapurl || '',
                    pubDate: date,
                    time: formatTimeAgo(date),
                    tags: extractTags(title)
                };
            });
        }
    }
];

// 拉取实时资讯（多源并行，失败源自动剔除，全部失败时回退缓存）
export async function fetchLiveNews() {
    const newsContainer = document.getElementById('newsList');
    if (!newsContainer) return;

    newsContainer.innerHTML = `
        <div class="bg-white rounded-2xl border border-hairline p-8 text-center">
            <div class="inline-flex items-center gap-2 text-sm text-muted">
                <svg class="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" class="opacity-25"/>
                    <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" class="opacity-75"/>
                </svg>
                正在获取实时资讯...
            </div>
        </div>
    `;

    const results = await Promise.allSettled(
        NEWS_SOURCES.map(src =>
            loadJsonp(src.buildUrl(), src.callbackParam, 10000)
                .then(data => ({ src, items: src.parse(data) }))
        )
    );

    const allNews = [];
    const ok = [];
    const fail = [];

    results.forEach((r, i) => {
        const src = NEWS_SOURCES[i];
        if (r.status === 'fulfilled' && Array.isArray(r.value.items) && r.value.items.length > 0) {
            allNews.push(...r.value.items);
            ok.push(src.name);
        } else {
            fail.push(src.name);
        }
    });

    // 去重（按标题）
    const seen = new Set();
    const uniqueNews = allNews.filter(item => {
        const key = item.title.trim();
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
    });

    uniqueNews.sort((a, b) => b.pubDate - a.pubDate);

    if (uniqueNews.length === 0) {
        console.warn('所有资讯源均不可用:', fail);
        if (lastNewsItems.length > 0) {
            // 刷新失败：保留上次成功结果，不覆盖
            renderLiveNews(lastNewsItems, { ok: newsSourceStatus.ok || [], fail });
        } else {
            renderFallbackNews(fail);
        }
        return;
    }

    lastNewsItems = uniqueNews.slice(0, 20);
    newsVisibleCount = NEWS_INITIAL_COUNT;   // 每次拉取后重置为仅展示前 3 条
    newsSourceStatus = { ok, fail };
    renderLiveNews(lastNewsItems, { ok, fail });
    liveNewsLoaded = true;
}

// "显示更多"：每次追加 5 条（全部显示后按钮自动隐藏）
export function showMoreNews() {
    newsVisibleCount = Math.min(newsVisibleCount + NEWS_PAGE_SIZE, lastNewsItems.length);
    renderLiveNews(lastNewsItems, newsSourceStatus);
}

// 渲染实时资讯（默认前 3 条 + 显示更多；两列卡片网格，含动态来源状态提示）
export function renderLiveNews(newsItems, { ok, fail } = {}) {
    const newsContainer = document.getElementById('newsList');
    if (!newsContainer) return;

    const partial = fail && fail.length > 0;
    const sourceText = ok && ok.length ? [...new Set(ok)].join(' / ') : '多来源';

    const statusBadge = partial
        ? `<span class="w-2 h-2 bg-warning rounded-full"></span>
           <span>实时资讯 · 部分来源不可用，已自动切换</span>
           <span class="text-xs text-muted" title="${fail.join('、')} 不可用">（可用：${sourceText}）</span>`
        : `<span class="w-2 h-2 bg-positive rounded-full animate-pulse-soft"></span>
           <span>实时资讯 · 数据来源：${sourceText}</span>`;

    const visibleItems = newsItems.slice(0, newsVisibleCount);
    const remaining = newsItems.length - visibleItems.length;

    // 显示更多按钮：全部展示后不再渲染
    const moreBtnHtml = remaining > 0
        ? `<div class="flex justify-center pt-1">
               <button onclick="showMoreNews()" class="inline-flex items-center gap-1.5 px-5 py-2.5 rounded-xl border border-hairline bg-white text-sm font-medium text-body hover:text-primary hover:border-primary/40 transition-colors">
                   <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                   显示更多（还有 ${remaining} 条）
               </button>
           </div>`
        : '';

    newsContainer.innerHTML = `
        <div class="flex items-center justify-between mb-3 px-1">
            <div class="flex items-center gap-2 text-sm text-muted flex-wrap">
                ${statusBadge}
            </div>
            <button onclick="fetchLiveNews()" class="flex items-center gap-1 text-xs text-primary hover:underline">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                刷新
            </button>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            ${visibleItems.map(item => `
                <article class="news-card bg-white rounded-2xl border border-hairline p-5 cursor-pointer flex flex-col" onclick="window.open('${item.link}', '_blank')">
                    <div class="flex items-start justify-between gap-4 mb-3">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="px-2 py-0.5 text-xs font-medium rounded-md bg-primary/10 text-primary">${item.source}</span>
                            <span class="text-xs text-muted">${item.time}</span>
                            <svg class="w-3 h-3 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
                        </div>
                    </div>
                    <h3 class="font-semibold text-ink text-sm leading-relaxed mb-2 line-clamp-2 hover:text-primary transition-colors">${item.title}</h3>
                    ${item.summary ? `<p class="text-sm text-muted leading-relaxed line-clamp-2 mb-3">${item.summary}</p>` : ''}
                    <div class="flex items-center gap-2 flex-wrap mt-auto">
                        ${item.tags.map(tag => `<span class="px-2 py-0.5 text-xs rounded-md bg-surface-strong text-muted">#${tag}</span>`).join('')}
                    </div>
                </article>
            `).join('')}
        </div>
        ${moreBtnHtml}
    `;
}

// 兜底资讯（全部数据源失败且无上次缓存时显示空态）
export function renderFallbackNews(fail = []) {
    const newsContainer = document.getElementById('newsList');
    if (!newsContainer) return;

    newsSourceStatus = { ok: [], fail };

    newsContainer.innerHTML = `
        <div class="bg-white rounded-2xl border border-hairline p-8 text-center">
            <p class="text-sm text-muted">资讯暂未更新，请稍后重试</p>
            ${fail.length ? `<p class="text-xs text-muted mt-2">（${fail.join('、')} 不可用）</p>` : ''}
            <button onclick="fetchLiveNews()" class="mt-4 inline-flex items-center gap-1 text-xs text-primary hover:underline">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                重试
            </button>
        </div>
    `;
}

// ==================== 持仓相关快讯（后端两表联动：news_item + client_news） ====================
// 后端常驻采集层已按持仓匹配并落库，前端仅消费算好的结果（不再依赖被丢弃的本地 stockList 解析）。
export async function fetchClientRelatedNews(clientId, limit = 50) {
    const token = getToken();
    if (!token || !clientId) return [];
    try {
        const resp = await fetch(
            `/api/clients/${encodeURIComponent(clientId)}/related-news?limit=${limit}`,
            { headers: { 'Authorization': `Bearer ${token}` } },
        );
        // 401/403：令牌失效或无权限 -> 静默返回空（不阻断页面）
        if (resp.status === 401 || resp.status === 403) return [];
        if (!resp.ok) return [];
        const data = await resp.json();
        return Array.isArray(data) ? data : [];
    } catch (e) {
        console.warn('持仓相关快讯加载失败:', e);
        return [];
    }
}

export function renderRelatedNews(items) {
    // 拉取结果存入模块状态，重置可见条数为默认 4；实际渲染交由 _paintRelatedNews
    relatedNewsItems = Array.isArray(items) ? items : [];
    relatedVisibleCount = RELATED_INITIAL_COUNT;
    _paintRelatedNews();
}

// "显示更多"：每次追加 5 条（全部展示后按钮自动隐藏）
export function showMoreRelatedNews() {
    relatedVisibleCount = Math.min(relatedVisibleCount + RELATED_PAGE_SIZE, relatedNewsItems.length);
    _paintRelatedNews();
}

function _paintRelatedNews() {
    const el = document.getElementById('relatedNewsPanel');
    if (!el) return;
    if (!relatedNewsItems.length) {
        el.innerHTML = '';
        el.classList.add('hidden');
        return;
    }
    el.classList.remove('hidden');

    const visibleItems = relatedNewsItems.slice(0, relatedVisibleCount);
    const remaining = relatedNewsItems.length - visibleItems.length;

    const cards = visibleItems.map(it => {
        const isBoard = it.tier === 2;
        const tierLabel = isBoard ? '板块相关' : '个股相关';
        const tierCls = isBoard
            ? 'bg-purple-100 text-purple-700'
            : 'bg-primary/10 text-primary';
        const unread = it.is_read ? '' : '<span class="w-2 h-2 rounded-full bg-negative ml-1" title="未读"></span>';
        const matched = (it.matched_codes || []).map(c =>
            `<span class="px-1.5 py-0.5 text-xs rounded bg-surface-strong text-muted font-mono">${escapeHtml(c)}</span>`
        ).join('');
        const time = it.first_seen ? formatTimeAgo(new Date(it.first_seen)) : '';
        // 仅当 url 真实存在时才渲染为 <a> 真超链接（右键/中键/复制链接均可）；缺失则降级为 div
        const url = (it.url && it.url !== '#') ? it.url : '';
        const inner = `
            <div class="flex items-center gap-2 flex-wrap mb-3">
                <span class="px-2 py-0.5 text-xs font-medium rounded-md ${tierCls}">${tierLabel}</span>
                ${unread}
                <span class="text-xs text-muted">${escapeHtml(sourceLabel(it.source))}</span>
                <span class="text-xs text-muted">·</span>
                <span class="text-xs text-muted">${time}</span>
                ${url ? '<svg class="w-3 h-3 text-muted ml-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>' : ''}
            </div>
            <h3 class="font-semibold text-ink text-sm leading-relaxed mb-2 line-clamp-2 group-hover:text-primary transition-colors">${escapeHtml(it.title)}</h3>
            ${matched ? `<div class="flex items-center gap-2 flex-wrap mt-auto">${matched}</div>` : ''}
        `;
        const baseCls = 'news-card group bg-white rounded-2xl border border-hairline p-5 flex flex-col no-underline text-inherit hover:border-primary/40 transition-colors';
        return url
            ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="${baseCls} cursor-pointer">${inner}</a>`
            : `<div class="${baseCls} opacity-90">${inner}</div>`;
    }).join('');

    // 显示更多按钮：全部展示后不再渲染
    const moreBtnHtml = remaining > 0
        ? `<div class="flex justify-center pt-1">
               <button onclick="showMoreRelatedNews()" class="inline-flex items-center gap-1.5 px-5 py-2.5 rounded-xl border border-hairline bg-white text-sm font-medium text-body hover:text-primary hover:border-primary/40 transition-colors">
                   <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                   显示更多（还有 ${remaining} 条）
               </button>
           </div>`
        : '';

    el.innerHTML = `
        <div class="mb-3 flex items-center gap-2 flex-wrap">
            <svg class="w-4 h-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <span class="text-sm font-semibold text-ink">持仓相关快讯</span>
            <span class="text-xs text-muted">· 与当前客户持仓相关的市场动态（后端常驻采集，登录即见、隔日耐久）</span>
            <button onclick="refreshRelatedNews()" class="ml-auto inline-flex items-center gap-1 text-xs text-primary hover:underline">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                刷新
            </button>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">${cards}</div>
        ${moreBtnHtml}
    `;
}

// 拉取并渲染指定客户的持仓相关快讯（工作台切换客户时调用）
export async function loadRelatedNews(clientId) {
    _relatedClientId = clientId || null;
    const items = await fetchClientRelatedNews(clientId);
    renderRelatedNews(items);
}

// 刷新当前选中客户的持仓相关快讯（供面板「刷新」按钮调用）
export async function refreshRelatedNews() {
    if (!_relatedClientId) return;
    const items = await fetchClientRelatedNews(_relatedClientId);
    renderRelatedNews(items);
}
