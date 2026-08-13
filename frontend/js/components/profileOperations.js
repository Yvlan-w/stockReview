// ============================================================
// 身份管理占位 + 分享快照导出
// 说明：原「个人设置（头像/昵称）」已移除；保留「生成分享版」功能。
//       身份管理将作为后续角色/权限系统（管理员/顾问/客服/用户）的入口。
// ============================================================
import { showToast } from '../core/ui.js';
import { getUserPositions, getUserData } from '../services/clientService.js';
import { renderAuthModal } from './auth.js';

// 分享版快照中的账户名（身份管理接入后替换为当前登录用户姓名）
const SHARE_NICKNAME = 'AGR';

// --- 打开身份管理弹窗（Phase 2 接入角色/权限） ---
export function openIdentityModal() {
    const modal = document.getElementById('identityModal');
    if (!modal) return;
    renderAuthModal();
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

// --- 关闭身份管理弹窗 ---
export function closeIdentityModal() {
    const modal = document.getElementById('identityModal');
    if (!modal) return;
    modal.classList.add('hidden');
    document.body.style.overflow = '';
}

// --- 获取当前页面源码（fetch 优先，DOM 序列化兜底） ---
export async function getSourceHtml() {
    try {
        const res = await fetch(window.location.href, { cache: 'no-store' });
        if (res.ok) {
            const text = await res.text();
            if (text && text.indexOf('EMBEDDED_POSITIONS') !== -1) return text;
        }
    } catch (e) { /* file:// 下 fetch 会失败，走下方兜底 */ }
    return '<!DOCTYPE html>\n' + document.documentElement.outerHTML;
}

// --- 生成分享版快照 HTML（当前真实数据烘焙进标记） ---
export async function exportShareSnapshot() {
    const btn = document.getElementById('exportShareBtn');
    if (!btn) return;
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<svg class="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" class="opacity-25"/><path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" class="opacity-75"/></svg> 生成中...';

    try {
        const positions = getUserPositions();
        const userData = getUserData();
        const profile = {
            nickname: SHARE_NICKNAME,
            avatarDataUrl: null
        };

        let html = await getSourceHtml();
        if (!html || html.indexOf('EMBEDDED_POSITIONS') === -1) {
            throw new Error('无法读取页面源码，请通过 http 访问本页后重试');
        }

        html = html
            .replace('window.EMBEDDED_POSITIO' + 'NS = null;', 'window.EMBEDDED_POSITIONS = ' + JSON.stringify(positions) + ';')
            .replace('window.EMBEDDED_PROFI' + 'LE = null;', 'window.EMBEDDED_PROFILE = ' + JSON.stringify(profile) + ';')
            .replace('window.EMBEDDED_USERDA' + 'TA = null;', 'window.EMBEDDED_USERDATA = ' + JSON.stringify(userData) + ';');

        if (html.indexOf('window.EMBEDDED_POSITIO' + 'NS = null;') !== -1) {
            throw new Error('数据注入失败，源码标记未匹配');
        }

        const dateStr = new Date().toLocaleDateString('zh-CN');
        const safeName = String(profile.nickname).replace(/[&<>"']/g, '');
        html = html.replace(/<title>[^<]*<\/title>/, '<title>' + (safeName || 'AGR') + ' 的持仓复盘 · 分享版</title>');

        const snapshotBar = ''
            + '<div style="position:fixed;bottom:0;left:50%;transform:translateX(-50%);z-index:9999;background:rgba(10,12,16,0.82);color:#fff;font-size:11px;padding:5px 14px;border-radius:8px 8px 0 0;font-family:system-ui,-apple-system,sans-serif;backdrop-filter:blur(4px);white-space:nowrap;">'
            + '📌 持仓复盘分享快照 · 导出于 ' + dateStr + ' · 持仓/账户为导出时刻真实数据，大盘与资讯为实时接口'
            + '</div>';
        html = html.replace('<!--SNAPSHOT_BAR_SL' + 'OT-->', snapshotBar);

        const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const stamp = new Date().toISOString().slice(0, 10);
        a.href = url;
        a.download = '持仓复盘_分享版_' + stamp + '.html';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 1500);

        showToast('✅ 分享版已生成，可直接发给领导', 'success');
    } catch (e) {
        console.error('Export failed:', e);
        showToast('❌ 生成失败：' + (e.message || '未知错误'), 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
    }
}
