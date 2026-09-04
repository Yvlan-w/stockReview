// ============================================================
// 客户开户向导（客服 / 管理员）
// 分步引导：① 基本信息 → ② 关系分配 → ③ 登录账号确认。
// 客服创建时本人固定为客服之一；管理员可自由选择顾问与客服。
// 表单值跨步骤持久化到 wizData，避免步骤切换销毁 DOM 导致数据丢失。
// ============================================================
import { showToast } from '../core/ui.js';
import { RISK_LEVELS } from '../core/config.js';
import { canCreateClient, isService } from '../permissions/access.js';
import { getUser, fetchUserOptions, createClient } from '../services/authService.js';
import { loadClients } from '../services/clientService.js';
import { renderClientList, refreshClientDetail, refreshClientSummaries } from './workbench.js';

const OVERLAY_ID = 'onboardingOverlay';
let wizStep = 0;
let wizOptions = { advisors: [], services: [] };
let wizData = {};

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

// ---- 打开 / 关闭 ----
export async function openOnboarding() {
    if (!canCreateClient()) {
        showToast('❌ 无权限创建客户账户', 'error');
        return;
    }
    wizOptions = await fetchUserOptions();
    wizStep = 0;
    wizData = {
        name: '', age: '', risk: '', cash: '0', note: '',
        advisorId: '', serviceIds: [],
    };

    let overlay = document.getElementById(OVERLAY_ID);
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = OVERLAY_ID;
        overlay.className = 'fixed inset-0 z-[120]';
        overlay.innerHTML = `
            <div class="absolute inset-0 modal-backdrop" onclick="closeOnboarding()"></div>
            <div class="absolute inset-0 flex items-center justify-center p-4">
            <div class="w-[min(720px,96vw)] max-h-[92vh] flex flex-col bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up">
                <div class="px-6 py-5 border-b border-hairline flex items-center justify-between shrink-0">
                    <div>
                        <h2 class="text-lg font-semibold text-ink">客户账户创建</h2>
                        <p class="text-sm text-muted mt-0.5">分步引导，信息与后端客户模型保持一致</p>
                    </div>
                    <button onclick="closeOnboarding()" class="w-9 h-9 rounded-full bg-surface-strong flex items-center justify-center text-muted hover:text-ink hover:bg-hairline transition-all">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                    </button>
                </div>
                <div id="onboardingSteps" class="px-6 pt-4 flex items-center gap-2 shrink-0"></div>
                <div id="onboardingBody" class="px-6 py-5 flex-1 overflow-y-auto"></div>
                <div id="onboardingFooter" class="px-6 pb-6 flex items-center justify-between gap-3 shrink-0"></div>
            </div>
            </div>
        `;
        document.body.appendChild(overlay);
    }
    overlay.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    renderWizard();
}

export function closeOnboarding() {
    document.getElementById(OVERLAY_ID)?.classList.add('hidden');
    document.body.style.overflow = '';
}

function renderWizard() {
    renderStepIndicator();
    renderStepBody();
    renderStepFooter();
}

function renderStepIndicator() {
    const container = document.getElementById('onboardingSteps');
    if (!container) return;
    const labels = ['基本信息', '关系分配', '账号确认'];
    container.innerHTML = labels.map((label, i) => {
        const active = i === wizStep;
        const done = i < wizStep;
        return `
        <div class="flex items-center gap-2 ${i ? 'flex-1' : ''}">
            ${i ? `<div class="h-px flex-1 ${done || active ? 'bg-primary' : 'bg-hairline'}"></div>` : ''}
            <div class="flex items-center gap-1.5">
                <span class="w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold ${done ? 'bg-primary text-white' : active ? 'bg-primary/10 text-primary' : 'bg-surface-strong text-muted'}">${done ? '✓' : i + 1}</span>
                <span class="text-xs ${active ? 'text-ink font-medium' : 'text-muted'}">${label}</span>
            </div>
        </div>`;
    }).join('');
}

function renderStepBody() {
    const body = document.getElementById('onboardingBody');
    if (!body) return;
    if (wizStep === 0) return renderBasicInfo(body);
    if (wizStep === 1) return renderRelations(body);
    if (wizStep === 2) return renderConfirm(body);
}

function renderStepFooter() {
    const footer = document.getElementById('onboardingFooter');
    if (!footer) return;
    const isSv = isService();
    const backBtn = wizStep > 0
        ? '<button onclick="onboardingBack()" class="px-5 py-2.5 text-sm font-medium text-body bg-surface-strong border border-hairline rounded-xl hover:bg-hairline transition-colors">上一步</button>'
        : '<span></span>';
    const nextBtn = wizStep < 2
        ? `<button onclick="onboardingNext()" class="px-5 py-2.5 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">下一步</button>`
        : `<button onclick="submitOnboarding()" class="px-6 py-2.5 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">${isSv ? '创建客户账户' : '确认创建'}</button>`;
    footer.innerHTML = backBtn + nextBtn;
}

// ---- Step 1：基本信息 ----
function renderBasicInfo(body) {
    body.innerHTML = `
        <div class="space-y-4">
            <div>
                <label class="block text-sm font-medium text-ink mb-2">客户姓名 <span class="text-negative">*</span></label>
                <input id="obName" type="text" maxlength="64" value="${escapeHtml(wizData.name)}" placeholder="如：张伟" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
            </div>
            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-ink mb-2">年龄</label>
                    <input id="obAge" type="number" min="1" max="120" value="${escapeHtml(wizData.age)}" placeholder="可选" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                </div>
                <div>
                    <label class="block text-sm font-medium text-ink mb-2">风险等级</label>
                    <select id="obRisk" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                        <option value="">未评估</option>
                        ${RISK_LEVELS.map(r => `<option value="${r}" ${wizData.risk === r ? 'selected' : ''}>${r}</option>`).join('')}
                    </select>
                </div>
            </div>
            <div>
                <label class="block text-sm font-medium text-ink mb-2">可用资金（元）</label>
                <input id="obCash" type="number" min="0" step="0.01" value="${escapeHtml(wizData.cash)}" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
            </div>
            <div>
                <label class="block text-sm font-medium text-ink mb-2">备注</label>
                <textarea id="obNote" rows="3" placeholder="投资偏好、关注方向等" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary resize-none">${escapeHtml(wizData.note)}</textarea>
            </div>
        </div>
    `;
}

// ---- Step 2：关系分配 ----
function renderRelations(body) {
    const { advisors, services } = wizOptions;
    const user = getUser();
    const isSv = isService();
    const advisorOptions = [
        `<option value="">(不分配 — 由客户本人管理持仓)</option>`,
        ...advisors.map(a => `<option value="${a.id}" ${wizData.advisorId === a.id ? 'selected' : ''}>${escapeHtml(a.name)}</option>`),
    ].join('');

    let serviceField;
    if (isSv) {
        const selfName = user?.name || user?.username || '本人';
        const others = services.filter(s => s.id !== user?.id);
        serviceField = `
            <div class="bg-primary/5 border border-primary/15 rounded-xl p-3 mb-3 text-sm">
                <span class="text-muted">本人固定为客服：</span><span class="font-medium text-primary">${escapeHtml(selfName)}（${escapeHtml(user?.id || '')}）</span>
                <p class="text-xs text-muted mt-1">客服本人固定为该客户的客服之一，可再添加另一名客服（最多 2 名）。</p>
            </div>
            <div class="grid grid-cols-2 gap-2">
                ${others.map(s => `<label class="flex items-center gap-2 text-sm text-body"><input type="checkbox" class="ob-svc" value="${s.id}" ${wizData.serviceIds.includes(s.id) ? 'checked' : ''}> ${escapeHtml(s.name)}</label>`).join('')}
            </div>`;
    } else {
        serviceField = `
            <p class="text-xs text-muted mb-2">最多选 2 名客服（可留空）</p>
            <div class="grid grid-cols-2 gap-2">
                ${services.map(s => `<label class="flex items-center gap-2 text-sm text-body"><input type="checkbox" class="ob-svc" value="${s.id}" ${wizData.serviceIds.includes(s.id) ? 'checked' : ''}> ${escapeHtml(s.name)}</label>`).join('')}
            </div>`;
    }

    body.innerHTML = `
        <div class="space-y-4">
            <div>
                <label class="block text-sm font-medium text-ink mb-2">投资顾问 <span class="text-negative">*</span></label>
                <select id="obAdvisor" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">${advisorOptions}</select>
            </div>
            <div>
                <label class="block text-sm font-medium text-ink mb-2">客服 <span class="text-negative">*</span></label>
                ${serviceField}
            </div>
        </div>
    `;
}

// ---- Step 3：账号确认 ----
function renderConfirm(body) {
    const isSv = isService();
    const advisorName = wizOptions.advisors.find(a => a.id === wizData.advisorId)?.name || wizData.advisorId;
    const serviceNames = wizData.serviceIds.map(id => wizOptions.services.find(s => s.id === id)?.name || id).join('、') || '本人';

    body.innerHTML = `
        <div class="space-y-4">
            <div class="bg-surface-soft rounded-xl p-4 text-sm space-y-1.5">
                <div class="flex justify-between"><span class="text-muted">客户姓名</span><span class="text-ink font-medium">${escapeHtml(wizData.name)}</span></div>
                <div class="flex justify-between"><span class="text-muted">投资顾问</span><span class="text-ink font-medium">${escapeHtml(advisorName)}</span></div>
                <div class="flex justify-between"><span class="text-muted">客服</span><span class="text-ink font-medium">${escapeHtml(serviceNames)}</span></div>
            </div>
            <label class="flex items-start gap-3 rounded-xl border border-hairline p-4 cursor-pointer">
                <input id="obCreateLogin" type="checkbox" class="mt-0.5" ${isSv ? 'checked' : ''}>
                <div>
                    <div class="text-sm font-medium text-ink">同时创建登录账号（user-client）</div>
                    <p class="text-xs text-muted mt-1">自动生成「姓名拼音#序号」用户名与随机初始密码，并回填为该客户的归属账号。创建成功后请将初始密码转交客户，客户登录后可自行修改。</p>
                </div>
            </label>
        </div>
    `;
}

function getSelectedServicesFromDom() {
    const user = getUser();
    const isSv = isService();
    let ids = Array.from(document.querySelectorAll('.ob-svc:checked')).map(x => x.value);
    if (isSv && user?.id) ids = [user.id, ...ids.filter(id => id !== user.id)];
    return ids;
}

// ---- 分步导航 ----
export function onboardingNext() {
    if (wizStep === 0) {
        const name = document.getElementById('obName')?.value.trim();
        if (!name) { showToast('❌ 请输入客户姓名', 'error'); return; }
        wizData.name = name;
        wizData.age = document.getElementById('obAge')?.value.trim() || '';
        wizData.risk = document.getElementById('obRisk')?.value || '';
        wizData.cash = document.getElementById('obCash')?.value.trim() || '0';
        wizData.note = document.getElementById('obNote')?.value.trim() || '';
    }
    if (wizStep === 1) {
        const advisorId = document.getElementById('obAdvisor')?.value || '';
        const serviceIds = getSelectedServicesFromDom();
        if (serviceIds.length > 2) { showToast('❌ 客服最多可选 2 名', 'error'); return; }
        wizData.advisorId = advisorId;
        wizData.serviceIds = serviceIds;
    }
    wizStep = Math.min(2, wizStep + 1);
    renderWizard();
}

export function onboardingBack() {
    wizStep = Math.max(0, wizStep - 1);
    renderWizard();
}

// ---- 提交 ----
export async function submitOnboarding() {
    const name = wizData.name;
    const age = parseInt(wizData.age || '', 10);
    const riskLevel = wizData.risk || null;
    const cash = parseFloat(wizData.cash || '0');
    const note = wizData.note || '';
    const advisorId = wizData.advisorId || null;
    const serviceIds = wizData.serviceIds || [];
    const createLogin = document.getElementById('obCreateLogin')?.checked;

    if (!name) { showToast('❌ 请输入客户姓名', 'error'); return; }
    if (serviceIds.length > 2) { showToast('❌ 客服最多可选 2 名', 'error'); return; }

    const payload = {
        name,
        advisor_id: advisorId,
        service_ids: serviceIds,
        age: Number.isFinite(age) && age > 0 ? age : null,
        risk_level: riskLevel,
        available_cash: Number.isFinite(cash) ? cash : 0,
        note,
        create_login: createLogin,
    };

    const btn = document.querySelector('#onboardingFooter button:last-child');
    const original = btn?.innerHTML;
    if (btn) btn.disabled = true;
    try {
        const result = await createClient(payload);
        const login = result.login;
        const body = document.getElementById('onboardingBody');
        // 判断是否已通过管理员站内信分发凭证
        const deliveredToAdmin = login && login.redelivered_via_admin_inbox === true;
        const hasPlainPassword = login && typeof login.initial_password === 'string' && login.initial_password.length > 0;
        body.innerHTML = `
            <div class="text-center py-4">
                <div class="w-14 h-14 mx-auto mb-3 rounded-full bg-positive/10 text-positive flex items-center justify-center">
                    <svg class="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                </div>
                <h3 class="text-lg font-semibold text-ink">创建成功</h3>
                <p class="text-sm text-muted mt-1">客户编号 <span class="font-mono text-ink">${escapeHtml(result.id)}</span></p>
                ${login ? `
                <div class="mt-4 text-left rounded-xl ${deliveredToAdmin ? 'bg-surface-strong border border-hairline' : 'bg-primary/5 border border-primary/15'} p-4 text-sm">
                    <div class="font-medium text-ink mb-2">登录账号已创建</div>
                    <div class="space-y-1 text-body">
                        <div>用户名：<span class="font-mono text-ink">${escapeHtml(login.username)}</span></div>
                        ${hasPlainPassword ? `
                        <div>初始密码：<span class="font-mono text-ink">${escapeHtml(login.initial_password)}</span>
                            <button onclick="copyText('${escapeHtml(login.initial_password)}')" class="ml-2 text-xs text-primary hover:underline">复制</button></div>` : `
                        <div class="rounded-lg bg-warning/10 border border-warning/20 px-3 py-2 mt-2 text-xs leading-relaxed">
                            <div class="font-medium text-warning mb-1">🔒 凭证已走管理员站内信分发</div>
                            <div class="text-body">出于账号安全，本次创建的「初始密码」不再直接返回给客服界面。</div>
                            <div class="text-body mt-1">请联系管理员在顶部“🔔 站内信”中查看本次账户凭证，并将用户名与初始密码安全地转交客户本人。</div>
                        </div>`}
                    </div>
                    ${hasPlainPassword ? `<p class="text-xs text-muted mt-2">请将用户名与初始密码安全转交给客户，客户登录后请提醒及时修改密码。</p>` : ''}
                </div>` : ''}
                <p class="text-sm text-muted mt-4">后续操作：可在「客户持仓工作台」查看该客户，并为其录入持仓与跟进风险预警。</p>
            </div>`;
        document.getElementById('onboardingFooter').innerHTML = `<button onclick="closeOnboarding()" class="w-full px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">完成</button>`;
        showToast('✅ 客户账户创建成功', 'success');
        await loadClients();
        // 新客户加入后重新拉取实时摘要，确保新客户行也显示正确金额（而非成本价估算）
        await refreshClientSummaries().catch(() => {});
        renderClientList();
        refreshClientDetail();
    } catch (e) {
        showToast('❌ ' + (e.message || '创建失败'), 'error');
    } finally {
        if (btn) { btn.disabled = false; if (original) btn.innerHTML = original; }
    }
}

export function copyText(text) {
    navigator.clipboard?.writeText(text).then(
        () => showToast('✅ 已复制到剪贴板', 'success'),
        () => showToast('❌ 复制失败', 'error'),
    );
}
