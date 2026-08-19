// ============================================================
// 资讯组件：静态资讯列表 / 人民日报解读 / 市场指数
// 说明：实时资讯由 newsService.fetchLiveNews 异步填充；
//       人民日报要闻解读与主要指数暂无数据源，先显示空态。
// ============================================================

export function renderNewsSection() {
    const newsContainer = document.getElementById('newsList');
    newsContainer.innerHTML = '<div class="bg-white rounded-2xl border border-hairline p-8 text-center text-sm text-muted">资讯加载中...</div>';

    const pdContainer = document.getElementById('peoplesDailyList');
    pdContainer.innerHTML = '<div class="text-center text-sm text-muted py-4">暂无人民日报要闻解读</div>';

    const indicesContainer = document.getElementById('marketIndices');
    indicesContainer.innerHTML = '<div class="text-center text-sm text-muted py-4">暂无指数数据</div>';
}
