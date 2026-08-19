// ============================================================
// 应用入口：组装所有 ES 模块 + 暴露内联事件处理函数
// ============================================================
import { setCurrentDate } from './core/ui.js';
import { loadClients, setCurrentClient, getFilteredClients } from './services/clientService.js';
import { isWorkbenchVisible } from './permissions/access.js';
import {
    renderMarketTicker, renderClientList, refreshClientDetail,
    toggleWarnOnly, selectClient, addClientTag, removeClientTag,
    exportClientReport, updateClientNote, evaluateClientRisk, handleAlertStatus,
} from './components/workbench.js';
import {
    renderVolumeChart, filterPositions, sortTable,
} from './components/overview.js';
import {
    renderMarketOverview, refreshMarket, renderSectorHeatmap,
    renderLeadershipAnalysis, renderDriverAnalysis, initVolumeChartTabs,
} from './components/market.js';
import { renderNewsSection } from './components/news.js';
import { fetchLiveNews } from './services/newsService.js';
import {
    openPositionModal, closePositionModal, openAdjustModal, closeAdjustModal,
    executeAdjust, savePosition, deletePosition,
} from './components/modals.js';
import {
    openIdentityModal, closeIdentityModal, exportShareSnapshot,
} from './components/profileOperations.js';
import {
    initAuth, handleLogin, handleLogout, toggleNotificationPanel,
    openNotification, markAllRead,
} from './components/auth.js';
import {
    openAdminPanel, closeAdminPanel, switchAdminTab, refreshAdminPanel,
    renderRelationList, openRelationModal, closeRelationModal, saveRelation,
    removeClientRelation, exportRelationsFile, handleRelationImportFile,
    toggleAccountSubrole, submitAccountForm, copyText as copyTextAdmin,
} from './components/admin.js';
import {
    openOnboarding, closeOnboarding, onboardingNext, onboardingBack,
    submitOnboarding,
} from './components/onboarding.js';

// ---- 暴露给内联 onclick 使用（ES module 作用域隔离）----
Object.assign(window, {
    renderClientList, toggleWarnOnly, selectClient, addClientTag, removeClientTag,
    exportClientReport, updateClientNote, evaluateClientRisk, handleAlertStatus,
    filterPositions, sortTable,
    refreshMarket,
    fetchLiveNews,
    openIdentityModal, closeIdentityModal, exportShareSnapshot,
    openPositionModal, closePositionModal, openAdjustModal, closeAdjustModal,
    executeAdjust, savePosition, deletePosition,
    handleLogin, handleLogout, toggleNotificationPanel, openNotification, markAllRead,
    openAdminPanel, closeAdminPanel, switchAdminTab, refreshAdminPanel,
    renderRelationList, openRelationModal, closeRelationModal, saveRelation,
    removeClientRelation, exportRelationsFile, handleRelationImportFile,
    toggleAccountSubrole, submitAccountForm,
    openOnboarding, closeOnboarding, onboardingNext, onboardingBack, submitOnboarding,
    copyText: copyTextAdmin,
});

// --- 顶部导航高亮：点击 + 滚动联动 ---
function setupNavScrollSpy() {
    const links = Array.from(document.querySelectorAll('nav a[data-nav]'));
    const sections = links.map(a => document.getElementById(a.dataset.nav)).filter(Boolean);

    function setActive(id) {
        links.forEach(a => {
            const active = a.dataset.nav === id;
            a.classList.toggle('text-primary', active);
            a.classList.toggle('bg-blue-50/60', active);
            a.classList.toggle('text-body', !active);
            a.classList.toggle('hover:text-ink', !active);
            a.classList.toggle('hover:bg-surface-strong/60', !active);
        });
    }

    links.forEach(a => a.addEventListener('click', () => setActive(a.dataset.nav)));

    const offset = 100;
    const onScroll = () => {
        const y = window.scrollY + offset;
        let current = sections[0] ? sections[0].id : '';
        for (const sec of sections) {
            const top = sec.getBoundingClientRect().top + window.scrollY;
            if (top <= y) current = sec.id;
        }
        setActive(current);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
}

document.addEventListener('DOMContentLoaded', async () => {
    setCurrentDate();
    setupNavScrollSpy();
    initAuth();

    // 从后端加载客户（已按角色过滤），再渲染客户数据
    await loadClients();
    renderMarketTicker();
    if (isWorkbenchVisible()) {
        renderClientList();
        const firstClient = getFilteredClients()[0];
        setCurrentClient(firstClient ? firstClient.id : null);
        refreshClientDetail();
    }

    renderMarketOverview();       // 今日盯大盘（不依赖客户数据）
    renderVolumeChart();          // 上证成交量图（先渲染模拟数据占位）
    initVolumeChartTabs();        // 初始化成交量图切换按钮
    renderSectorHeatmap();
    renderLeadershipAnalysis();
    renderDriverAnalysis();
    renderNewsSection();
    fetchLiveNews();              // 加载实时资讯

    // 首屏渲染后再拉取东方财富实时数据
    setTimeout(() => refreshMarket(), 300);

    // 交易时段（09:15-15:05 CST）每 30 秒自动刷新
    const marketRefreshInterval = setInterval(() => {
        const hour = new Date().getHours();
        const minute = new Date().getMinutes();
        const isTrading = (hour === 9 && minute >= 15) ||
                          (hour >= 10 && hour < 15) ||
                          (hour === 15 && minute <= 5);
        if (isTrading && document.visibilityState === 'visible') {
            refreshMarket();
        }
    }, 30000);

    // 页面重新可见时立即刷新
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            refreshMarket();
        }
    });
});

// Escape 关闭所有弹窗
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closePositionModal();
        closeAdjustModal();
        closeIdentityModal();
    }
});
