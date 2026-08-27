// ============================================================
// 基础 UI 工具（Toast 通知 / 日期 / 顶部广告 Banner，无依赖）
// ============================================================

let toastTimer = null;
const TOAST_DURATION = 6000; // 显示时长（毫秒）
const AD_BANNER_KEY = 'ad_banner_closed_v1'; // 关闭状态 7 天内不再显示

// Toast 通知：支持自定义显示时长，duration=0 时不自动关闭（需手动 hideToast）
export function showToast(message, type = 'success', duration = TOAST_DURATION) {
    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toastMessage');
    const toastIcon = document.getElementById('toastIcon');

    toastMsg.textContent = message;

    if (type === 'success') {
        toastIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>';
        toastIcon.setAttribute('class', 'w-4 h-4 text-positive');
    } else if (type === 'error') {
        toastIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>';
        toastIcon.setAttribute('class', 'w-4 h-4 text-negative');
    } else if (type === 'warning') {
        toastIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>';
        toastIcon.setAttribute('class', 'w-4 h-4 text-warning');
    } else if (type === 'info') {
        toastIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>';
        toastIcon.setAttribute('class', 'w-4 h-4 text-primary');
    }

    toast.classList.remove('hidden');

    if (toastTimer) clearTimeout(toastTimer);
    if (duration > 0) {
        toastTimer = setTimeout(() => {
            toast.classList.add('hidden');
        }, duration);
    } else {
        toastTimer = null; // duration=0：留待 hideToast 手动关闭
    }
}

// 立即关闭 Toast（用于加载提示结束时显式收起）
export function hideToast() {
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
    document.getElementById('toast')?.classList.add('hidden');
}

// 设置顶部当前日期
export function setCurrentDate() {
    const now = new Date();
    const options = { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' };
    const el = document.getElementById('currentDate');
    if (el) el.textContent = now.toLocaleDateString('zh-CN', options);
}

// 顶部广告 Banner 初始化：未关闭过则显示
// 布局：banner 高 36px(h-9)，nav 高 64px(h-16)，两者堆叠在顶部。
//   banner 显示：nav 下移 36px，body padding-top 调整为 100px(36+64)；
//   banner 关闭：nav 回到 top-0，body padding-top 为 64px。
export function initAdBanner() {
    const nav = document.querySelector('nav.glass-nav');
    const banner = document.getElementById('adBanner');
    try {
        if (!banner) return;
        const raw = localStorage.getItem(AD_BANNER_KEY);
        const closedAt = raw ? parseInt(raw, 10) : 0;
        const SEVEN_DAYS = 7 * 24 * 60 * 60 * 1000;
        const isClosed = closedAt && (Date.now() - closedAt) < SEVEN_DAYS;

        if (isClosed) {
            _applyBannerState(false, banner, nav);
        } else {
            _applyBannerState(true, banner, nav);
        }
    } catch (e) {
        // localStorage 不可用（隐私模式等）时降级：隐藏 banner，恢复默认 nav 位置
        try { _applyBannerState(false, banner, nav); } catch (_) {}
    }
}

// 应用 banner 显隐对应的全部布局状态
function _applyBannerState(visible, banner, nav) {
    if (!banner) return;
    if (visible) {
        banner.classList.remove('hidden');
        // 确保 banner 在最上层，避免被 nav 遮挡
        banner.style.zIndex = '60';
        // nav 下移到 banner 下方
        if (nav) nav.style.top = '2.25rem';   // 2.25rem = h-9 = 36px
        // body 顶部 padding = banner(36px) + nav(64px) = 100px
        document.body.style.paddingTop = '100px';
        document.body.classList.remove('pt-9');
    } else {
        banner.classList.add('hidden');
        if (nav) nav.style.top = '0';
        document.body.style.paddingTop = '64px';   // nav 高 64px(h-16)
        document.body.classList.remove('pt-9');
    }
}

// 登录成功后强制展示广告 Banner（无视 7 天关闭记录，每次登录均触发）
export function showAdBanner() {
    const nav = document.querySelector('nav.glass-nav');
    const banner = document.getElementById('adBanner');
    _applyBannerState(true, banner, nav);
}

// 关闭顶部广告 Banner（记住 7 天）
export function closeAdBanner() {
    const nav = document.querySelector('nav.glass-nav');
    const banner = document.getElementById('adBanner');
    _applyBannerState(false, banner, nav);
    try { localStorage.setItem(AD_BANNER_KEY, String(Date.now())); } catch (_) {}
}
