// ============================================================
// 应用入口：组装所有 ES 模块 + 暴露内联事件处理函数
// ============================================================
import { setCurrentDate, initAdBanner, closeAdBanner } from './core/ui.js';
import { loadClients, setCurrentClient, getFilteredClients } from './services/clientService.js';
import { isWorkbenchVisible } from './permissions/access.js';
import { loadModuleVisibility, applyModuleVisibility, isModuleVisible } from './permissions/modules.js';
import {
    renderMarketTicker, renderClientList, refreshClientDetail, refreshClientSummaries,
    toggleWarnOnly, selectClient, addClientTag, removeClientTag,
    exportClientReport, updateClientNote, evaluateClientRisk, handleAlertStatus,
    openClientEditModal, closeClientEditModal, submitClientEdit,
    openTagEditorModal, closeTagEditorModal, addFreeTag, removeFreeTagInEditor, submitTagEditor,
} from './components/workbench.js';
import {
    renderVolumeChart, filterPositions, sortTable,
} from './components/overview.js';
import {
    renderMarketOverview, refreshMarket, renderSectorHeatmap,
    initVolumeChartTabs,
} from './components/market.js';
import { fetchLiveNews, showMoreNews, showMoreRelatedNews } from './services/newsService.js';
import {
    openPositionModal, closePositionModal, openAdjustModal, closeAdjustModal,
    executeAdjust, savePosition, revokePosition,
    startOcrImport, openOcrImportModal, closeOcrImportModal, importOcrRows, ocrDeleteRow, ocrRetryRow, addOcrScreenshots,
    openOcrHoldingModal, closeOcrHoldingModal, importOcrHoldings, ocrHoldingDeleteRow, recomputeHoldingPnl,
} from './components/modals.js';
import {
    openIdentityModal, closeIdentityModal,
} from './components/profileOperations.js';
import {
    initAuth, handleLogin, handleLogout, toggleNotificationPanel,
    openNotification, markAllRead, handleDeleteSelfAccount,
} from './components/auth.js';
import {
    openAdminPanel, closeAdminPanel, switchAdminTab, refreshAdminPanel,
    renderRelationList, openRelationModal, closeRelationModal, saveRelation,
    removeClientRelation, exportRelationsFile, handleRelationImportFile,
    toggleAccountSubrole, toggleAccountClientFields, submitAccountForm, copyText as copyTextAdmin,
    renderAccountMgmtList, renderAccountMgmtTab, adminAccDelete, adminAccResetPwd, submitAccResetPwd,
    adminAccLifecycle, submitAccRenew, submitAccLifecyclePatch,
    adminAccRelation, submitAccRelation,
    auditLogsSearch, auditLogsReset,
} from './components/admin.js';
import {
    openOnboarding, closeOnboarding, onboardingNext, onboardingBack,
    submitOnboarding,
} from './components/onboarding.js';
import { toggleStrategyTimeline } from './components/strategy.js';

// ---- 暴露给内联 onclick 使用（ES module 作用域隔离）----
Object.assign(window, {
    renderClientList, toggleWarnOnly, selectClient, addClientTag, removeClientTag,
    openTagEditorModal, closeTagEditorModal, addFreeTag, removeFreeTagInEditor, submitTagEditor,
    closeAdBanner,
    exportClientReport, updateClientNote, evaluateClientRisk, handleAlertStatus,
    openClientEditModal, closeClientEditModal, submitClientEdit,
    filterPositions, sortTable,
    refreshMarket,
    fetchLiveNews, showMoreNews, showMoreRelatedNews,
    openIdentityModal, closeIdentityModal,
    openPositionModal, closePositionModal, openAdjustModal, closeAdjustModal,
    executeAdjust, savePosition, revokePosition,
    startOcrImport, openOcrImportModal, closeOcrImportModal, importOcrRows, ocrDeleteRow, ocrRetryRow, addOcrScreenshots,
    openOcrHoldingModal, closeOcrHoldingModal, importOcrHoldings, ocrHoldingDeleteRow, recomputeHoldingPnl,
    handleLogin, handleLogout, toggleNotificationPanel, openNotification, markAllRead,
    handleDeleteSelfAccount,
    openAdminPanel, closeAdminPanel, switchAdminTab, refreshAdminPanel,
    renderRelationList, openRelationModal, closeRelationModal, saveRelation,
    removeClientRelation, exportRelationsFile, handleRelationImportFile,
    toggleAccountSubrole, toggleAccountClientFields, submitAccountForm,
    renderAccountMgmtList, renderAccountMgmtTab, adminAccDelete, adminAccResetPwd, submitAccResetPwd,
    adminAccLifecycle, submitAccRenew, submitAccLifecyclePatch,
    adminAccRelation, submitAccRelation,
    auditLogsSearch, auditLogsReset,
    openOnboarding, closeOnboarding, onboardingNext, onboardingBack, submitOnboarding,
    copyText: copyTextAdmin,
    toggleStrategyTimeline,
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

// ---- 大盘模块渲染（按模块可见性权限）----
// 抽成函数，供「首屏」与「切换账号」复用：切换账号后需重新计算可见性并重建内容
// （隐藏时渲染函数会提前返回、内容为空；切换到可见身份时必须重建并拉取数据）。
function renderMarketModules() {
    if (isModuleVisible('market_overview')) renderMarketOverview();       // 今日盯大盘（不依赖客户数据）
    if (isModuleVisible('index_turnover')) {                             // 指数成交额
        renderVolumeChart();          // 上证成交量图（先渲染模拟数据占位）
        initVolumeChartTabs();        // 初始化成交量图切换按钮
    }
    if (isModuleVisible('sector_performance')) renderSectorHeatmap();
    // 任一可见时才拉取实时数据（隐藏态跳过可省流量与无用渲染）
    if (isModuleVisible('market_overview') || isModuleVisible('index_turnover') || isModuleVisible('sector_performance')) {
        refreshMarket();
    }
}

// 切换账号（登录成功 / 登出 / 被踢）后，重新拉取并应用模块可见性，再重建大盘模块。
// 通过事件解耦：auth.js 在身份变化时派发 auth:identity-changed，无需直接依赖 market 模块。
window.addEventListener('auth:identity-changed', () => {
    loadModuleVisibility()
        .then(() => { applyModuleVisibility(); renderMarketModules(); })
        .catch(() => { applyModuleVisibility(); renderMarketModules(); });
});

document.addEventListener('DOMContentLoaded', async () => {
    setCurrentDate();
    initAdBanner();
    setupNavScrollSpy();
    initAuth();

    // 从后端加载客户（已按角色过滤），再渲染客户数据
    await loadClients();
    // 拉取模块可见性配置（基于角色/账户），再统一应用页面模块显隐
    await loadModuleVisibility();
    applyModuleVisibility();
    renderMarketTicker();
    if (isWorkbenchVisible()) {
        // 先拉取后端实时盈亏摘要，确保列表首屏即渲染正确金额（不依赖选中客户）
        await refreshClientSummaries().catch(() => {});
        renderClientList();
        const firstClient = getFilteredClients()[0];
        setCurrentClient(firstClient ? firstClient.id : null);
        refreshClientDetail();
    }

    // 以下三个大盘模块按「角色/账户模块权限」决定是否渲染（客服默认隐藏）
    renderMarketModules();
    fetchLiveNews();              // 加载实时资讯

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
        closeTagEditorModal();
    }
});

// 标签编辑器自由标签输入框回车即添加
document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target?.id === 'freeTagInput') {
        e.preventDefault();
        addFreeTag();
    }
});
