// ============================================================
// 资讯组件：静态资讯列表 / 人民日报解读 / 市场指数
// ============================================================
import { mockData } from '../data/mockData.js';
import { getImpactBadge, getPnLColor } from '../core/formatters.js';

export function renderNewsSection() {
    const newsContainer = document.getElementById('newsList');
    newsContainer.innerHTML = mockData.news.map(item => `
        <article class="news-card bg-white rounded-2xl border border-hairline p-5 cursor-pointer">
            <div class="flex items-start justify-between gap-4 mb-3">
                <div class="flex items-center gap-2">
                    <span class="px-2 py-0.5 text-xs font-medium rounded-md bg-primary/10 text-primary">${item.source}</span>
                    <span class="text-xs text-muted">${item.time}</span>
                </div>
            </div>
            <h3 class="font-semibold text-ink text-sm leading-relaxed mb-2 line-clamp-2 hover:text-primary transition-colors">${item.title}</h3>
            <p class="text-sm text-muted leading-relaxed line-clamp-2 mb-3">${item.summary}</p>
            <div class="flex items-center gap-2 flex-wrap">
                ${item.tags.map(tag => `<span class="px-2 py-0.5 text-xs rounded-md bg-surface-strong text-muted">#${tag}</span>`).join('')}
            </div>
        </article>
    `).join('');

    const pdContainer = document.getElementById('peoplesDailyList');
    pdContainer.innerHTML = mockData.peoplesDaily.map(item => `
        <div class="pb-5 last:pb-0 ${item !== mockData.peoplesDaily[mockData.peoplesDaily.length - 1] ? 'border-b border-hairline' : ''}">
            <div class="flex items-start justify-between gap-2 mb-2">
                <h4 class="font-medium text-ink text-sm leading-snug">${item.title}</h4>
                ${getImpactBadge(item.impact)}
            </div>
            <p class="text-xs text-muted leading-relaxed mb-3">${item.analysis}</p>
            <div class="flex flex-wrap gap-1.5">
                ${item.keyPoints.map(kp => `<span class="px-2 py-0.5 text-xs rounded bg-surface-strong text-body">${kp}</span>`).join('')}
            </div>
        </div>
    `).join('');

    const indicesContainer = document.getElementById('marketIndices');
    indicesContainer.innerHTML = mockData.indices.map(idx => `
        <div class="flex items-center justify-between">
            <span class="text-sm text-body">${idx.name}</span>
            <div class="text-right">
                <span class="font-mono text-sm font-medium text-ink">${idx.value.toFixed(2)}</span>
                <span class="font-mono text-xs ml-2 ${getPnLColor(idx.change)}">${idx.pct}</span>
            </div>
        </div>
    `).join('');
}
