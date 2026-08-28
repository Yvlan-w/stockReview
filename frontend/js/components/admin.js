// ============================================================
// 管理员后台：客户-客服-投顾关系映射配置 + 批量导入导出 + 账户创建。
// 仅管理员（admin）可访问；前端权限在 access.canAccessAdminPanel 校验，
// 后端接口同样以 require_roles(ROLE_ADMIN) 二次校验。
// ============================================================
import { showToast } from '../core/ui.js';
import { canAccessAdminPanel } from '../permissions/access.js';
import { clients, loadClients } from '../services/clientService.js';
import {
    fetchUserOptions, updateClientRelations, deleteClient, createClient,
    createUser, importRelations, exportRelationsJson, exportRelationsCsv, importRelationsCsv,
    listUsers, deleteUser, resetUserPassword, renewUser, patchUserLifecycle,
    listAuditLogs, licenseBadgeOf, roleLabel, ensureClientProfile,
} from '../services/authService.js';

const OVERLAY_ID = 'adminPanelOverlay';

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

async function loadOptions() {
    const { advisors = [], services = [] } = await fetchUserOptions();
    return { advisors, services };
}

function nameOf(list, id) {
    const u = list.find(x => x.id === id);
    return u ? u.name : id;
}

// ---- 打开 / 关闭 ----
export async function openAdminPanel() {
    if (!canAccessAdminPanel()) {
        showToast('❌ 仅管理员可访问关系映射配置', 'error');
        return;
    }
    let overlay = document.getElementById(OVERLAY_ID);
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = OVERLAY_ID;
        overlay.className = 'fixed inset-0 z-[120]';
        overlay.innerHTML = `
            <div class="absolute inset-0 modal-backdrop" onclick="closeAdminPanel()"></div>
            <div class="absolute inset-0 flex items-center justify-center p-4">
            <div class="w-[min(1100px,96vw)] h-[min(86vh,900px)] flex flex-col bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up">
                <div class="px-6 py-5 border-b border-hairline flex items-center justify-between shrink-0">
                    <div>
                        <h2 class="text-lg font-semibold text-ink">关系映射管理后台</h2>
                        <p class="text-sm text-muted mt-0.5">客户 - 投资顾问 - 客服 关系配置、可视化与批量导入导出</p>
                    </div>
                    <button onclick="closeAdminPanel()" class="w-9 h-9 rounded-full bg-surface-strong flex items-center justify-center text-muted hover:text-ink hover:bg-hairline transition-all">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                    </button>
                </div>
                <div class="px-6 pt-4 border-b border-hairline flex items-center gap-2 shrink-0 flex-wrap">
                    ${tabButton('relations', '关系映射', true)}
                    ${tabButton('visualize', '可视化规则', false)}
                    ${tabButton('importexport', '批量导入/导出', false)}
                    ${tabButton('accounts', '账户创建', false)}
                    ${tabButton('account-mgmt', '账户管理', false)}
                    ${tabButton('audit-logs', '查看日志', false)}
                </div>
                <div id="adminPanelBody" class="flex-1 overflow-y-auto px-6 py-5"></div>
            </div>
            </div>
        `;
        document.body.appendChild(overlay);
    }
    overlay.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    await switchAdminTab('relations');
}

export function closeAdminPanel() {
    const overlay = document.getElementById(OVERLAY_ID);
    if (overlay) overlay.classList.add('hidden');
    document.body.style.overflow = '';
}

function tabButton(tab, label, active) {
    return `<button onclick="switchAdminTab('${tab}')" data-admin-tab="${tab}" class="px-4 py-2 text-sm font-medium rounded-lg transition-colors ${active ? 'bg-primary text-white' : 'text-body hover:bg-surface-strong'}">${label}</button>`;
}

function setActiveTab(tab) {
    document.querySelectorAll('[data-admin-tab]').forEach(b => {
        const active = b.dataset.adminTab === tab;
        b.className = `px-4 py-2 text-sm font-medium rounded-lg transition-colors ${active ? 'bg-primary text-white' : 'text-body hover:bg-surface-strong'}`;
    });
}

export async function switchAdminTab(tab) {
    setActiveTab(tab);
    const body = document.getElementById('adminPanelBody');
    if (!body) return;
    if (tab === 'relations') return renderRelationsTab(body);
    if (tab === 'visualize') return renderVisualizeTab(body);
    if (tab === 'importexport') return renderImportExportTab(body);
    if (tab === 'accounts') return renderAccountsTab(body);
    if (tab === 'account-mgmt') return renderAccountMgmtTab(body);
    if (tab === 'audit-logs') return renderAuditLogsTab(body);
}

// ==================== Tab 1：关系映射 ====================
async function renderRelationsTab(body) {
    await loadClients();
    const { advisors, services } = await loadOptions();
    body.innerHTML = `
        <div class="flex items-center justify-between gap-3 mb-4 flex-wrap">
            <div class="relative flex-1 min-w-[240px]">
                <input id="adminRelationSearch" oninput="renderRelationList()" placeholder="按客户姓名 / 编号搜索" class="w-full pl-9 pr-3 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                <svg class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
            </div>
            <div class="flex items-center gap-2">
                <button onclick="refreshAdminPanel()" class="px-4 py-2 text-sm font-medium text-body bg-surface-strong border border-hairline rounded-lg hover:bg-hairline transition-colors">刷新</button>
                <button onclick="openRelationModal()" class="px-4 py-2 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-lg transition-colors">+ 新增关系</button>
            </div>
        </div>
        <div id="adminRelationList" class="space-y-2"></div>
    `;
    window.__adminAdvisors = advisors;
    window.__adminServices = services;
    renderRelationList();
}

export function refreshAdminPanel() {
    switchAdminTab('relations');
}

export function renderRelationList() {
    const container = document.getElementById('adminRelationList');
    if (!container) return;
    const q = (document.getElementById('adminRelationSearch')?.value || '').trim().toLowerCase();
    const advisors = window.__adminAdvisors || [];
    const services = window.__adminServices || [];
    const validAdvisorIds = new Set((advisors || []).map(a => a.id));
    const validServiceIds = new Set((services || []).map(s => s.id));

    const list = (clients || []).filter(c => {
        if (q && !(c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q))) return false;
        // —— 软删除映射过滤：引用了已删除账户的关系视为无效，不再显示 ——
        // 1) advisorId 不在"有效顾问集合"里 → 视为已解除
        const hasValidAdvisor = c.advisorId && validAdvisorIds.has(c.advisorId);
        // 2) serviceIds 仅保留在"有效客服集合"里的 → 剩余为空视为已解除
        const validSvcIds = (c.serviceIds || []).filter(id => validServiceIds.has(id));
        // 3) 若顾问+客服两边都没有有效映射（关系实际上已被软删空）→ 这条客户不显示
        if (!hasValidAdvisor && validSvcIds.length === 0) return false;
        return true;
    });
    if (!list.length) {
        container.innerHTML = '<div class="text-center text-sm text-muted py-10">暂无匹配的客户关系</div>';
        return;
    }
    container.innerHTML = list.map(c => {
        // 展示时再次只取有效顾问/客服：引用已删除账户的不显示名字（避免 ID 泄露）
        const advisorVisible = validAdvisorIds.has(c.advisorId);
        const advisorName = advisorVisible ? nameOf(advisors, c.advisorId) : '未分配';
        const validSvcIds = (c.serviceIds || []).filter(id => validServiceIds.has(id));
        const serviceNames = validSvcIds.length
            ? validSvcIds.map(id => nameOf(services, id)).join('、')
            : '未分配';
        return `
        <div class="flex items-center gap-4 bg-surface-soft/60 border border-hairline rounded-xl px-4 py-3">
            <div class="w-20 shrink-0">
                <div class="font-mono text-xs text-muted">${escapeHtml(c.id)}</div>
                <div class="text-sm font-semibold text-ink">${escapeHtml(c.name)}</div>
            </div>
            <div class="flex-1 grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
                <div class="flex items-center gap-2">
                    <span class="w-1.5 h-1.5 rounded-full bg-primary shrink-0"></span>
                    <span class="text-muted">顾问：</span><span class="text-ink">${escapeHtml(advisorName)}</span>
                </div>
                <div class="flex items-center gap-2">
                    <span class="w-1.5 h-1.5 rounded-full bg-positive shrink-0"></span>
                    <span class="text-muted">客服：</span><span class="text-ink">${escapeHtml(serviceNames)}</span>
                </div>
            </div>
            <div class="flex items-center gap-2 shrink-0">
                <button onclick="openRelationModal('${c.id}')" class="px-3 py-1.5 text-xs font-medium text-primary bg-primary/5 border border-primary/15 rounded-lg hover:bg-primary/10 transition-colors">编辑</button>
                <button onclick="removeClientRelation('${c.id}')" class="px-3 py-1.5 text-xs font-medium text-negative bg-negative/5 border border-negative/15 rounded-lg hover:bg-negative/10 transition-colors">删除</button>
            </div>
        </div>`;
    }).join('');
}

// ---- 新增 / 编辑关系弹窗 ----
export function openRelationModal(clientId = null) {
    const advisors = window.__adminAdvisors || [];
    const services = window.__adminServices || [];
    const client = clientId ? clients.find(c => c.id === clientId) : null;
    const isEdit = !!client;

    const advisorOptions = [
        `<option value="">(不分配 — 由客户本人管理持仓)</option>`,
        ...advisors.map(a => `<option value="${a.id}" ${client?.advisorId === a.id ? 'selected' : ''}>${escapeHtml(a.name)}</option>`),
    ].join('');
    const serviceChecks = services.map(s => {
        const checked = client && (client.serviceIds || []).includes(s.id) ? 'checked' : '';
        return `<label class="flex items-center gap-2 text-sm text-body"><input type="checkbox" class="svc-check" value="${s.id}" ${checked}> ${escapeHtml(s.name)}</label>`;
    }).join('');

    const modal = document.createElement('div');
    modal.id = 'relationModal';
    modal.className = 'fixed inset-0 z-[130]';
    modal.innerHTML = `
        <div class="absolute inset-0 modal-backdrop" onclick="closeRelationModal()"></div>
        <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md mx-4">
            <div class="bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up max-h-[90vh] overflow-y-auto">
                <div class="px-7 pt-6 pb-4 border-b border-hairline">
                    <h3 class="text-lg font-semibold text-ink">${isEdit ? '编辑关系映射' : '新增关系映射'}</h3>
                    <p class="text-sm text-muted mt-1">${isEdit ? `客户 ${escapeHtml(client.name)}（${client.id}）` : '创建客户并配置顾问与客服；两者都可留空，由客户本人登录后管理自己的持仓'}</p>
                </div>
                <div class="px-7 py-5 space-y-4">
                    ${isEdit ? '' : `
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">客户姓名 <span class="text-negative">*</span></label>
                        <input id="relName" type="text" maxlength="64" placeholder="如：张伟" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                    </div>`}
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">投资顾问（可空）</label>
                        <select id="relAdvisor" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">${advisorOptions}</select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">客服（最多 2 名，可空）</label>
                        <div id="relServices" class="grid grid-cols-2 gap-2 bg-surface-soft rounded-xl p-3 border border-hairline">${serviceChecks}</div>
                    </div>
                </div>
                <div class="px-7 pb-6 flex gap-3">
                    <button onclick="closeRelationModal()" class="flex-1 px-4 py-3 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors">取消</button>
                    <button onclick="saveRelation('${clientId || ''}')" class="flex-1 px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">${isEdit ? '保存' : '创建'}</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    document.body.style.overflow = 'hidden';
}

export function closeRelationModal() {
    document.getElementById('relationModal')?.remove();
    document.body.style.overflow = '';
}

export async function saveRelation(clientId = '') {
    const advisorId = document.getElementById('relAdvisor')?.value || '';
    const serviceIds = Array.from(document.querySelectorAll('.svc-check:checked')).map(x => x.value);
    if (serviceIds.length > 2) { showToast('❌ 客服最多可选 2 名', 'error'); return; }

    try {
        if (clientId) {
            await updateClientRelations(clientId, {
                advisor_id: advisorId || null,
                service_ids: serviceIds,
            });
            showToast('✅ 关系已更新（留空表示未分配，由客户本人管理持仓）', 'success');
        } else {
            const name = document.getElementById('relName')?.value.trim();
            if (!name) { showToast('❌ 请输入客户姓名', 'error'); return; }
            await createClient({
                name,
                advisor_id: advisorId || null,
                service_ids: serviceIds,
            });
            showToast('✅ 已创建客户（未分配投顾/客服也可先开户）', 'success');
        }
        closeRelationModal();
        await renderRelationsTab(document.getElementById('adminPanelBody'));
    } catch (e) {
        showToast('❌ ' + (e.message || '操作失败'), 'error');
    }
}

export async function removeClientRelation(clientId) {
    const client = clients.find(c => c.id === clientId);
    if (!client) return;
    if (!confirm(`确定删除客户「${client.name}（${client.id}）」及其关系映射吗？`)) return;
    try {
        await deleteClient(clientId);
        showToast('✅ 已删除', 'success');
        await renderRelationsTab(document.getElementById('adminPanelBody'));
    } catch (e) {
        showToast('❌ ' + (e.message || '删除失败'), 'error');
    }
}

// ==================== Tab 2：可视化规则 ====================
async function renderVisualizeTab(body) {
    await loadClients();
    const { advisors, services } = await loadOptions();
    const advisorClients = advisors.map(a => ({
        ...a,
        list: clients.filter(c => c.advisorId === a.id).map(c => c.name),
    }));
    const serviceClients = services.map(s => ({
        ...s,
        list: clients.filter(c => (c.serviceIds || []).includes(s.id)).map(c => c.name),
    }));

    body.innerHTML = `
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
            <div class="premium-card p-5">
                <h3 class="font-semibold text-ink mb-1">投资顾问 → 客户</h3>
                <p class="text-xs text-muted mb-4">一名顾问服务多名客户（一对多）</p>
                <div class="space-y-2">
                    ${advisorClients.map(a => `
                        <div class="flex items-start gap-3 bg-surface-soft rounded-lg px-3 py-2">
                            <span class="font-medium text-ink text-sm shrink-0 mt-0.5">${escapeHtml(a.name)}</span>
                            <div class="flex-1 flex flex-wrap gap-1.5">
                                ${a.list.length ? a.list.map(n => `<span class="px-2 py-0.5 text-xs bg-primary/10 text-primary rounded-full">${escapeHtml(n)}</span>`).join('') : '<span class="text-xs text-muted-soft">暂无客户</span>'}
                            </div>
                        </div>`).join('')}
                </div>
            </div>
            <div class="premium-card p-5">
                <h3 class="font-semibold text-ink mb-1">客服 → 客户</h3>
                <p class="text-xs text-muted mb-4">一名客服服务多名客户（多对多，单个客户 1~2 名客服）</p>
                <div class="space-y-2">
                    ${serviceClients.map(s => `
                        <div class="flex items-start gap-3 bg-surface-soft rounded-lg px-3 py-2">
                            <span class="font-medium text-ink text-sm shrink-0 mt-0.5">${escapeHtml(s.name)}</span>
                            <div class="flex-1 flex flex-wrap gap-1.5">
                                ${s.list.length ? s.list.map(n => `<span class="px-2 py-0.5 text-xs bg-positive/10 text-positive rounded-full">${escapeHtml(n)}</span>`).join('') : '<span class="text-xs text-muted-soft">暂无客户</span>'}
                            </div>
                        </div>`).join('')}
                </div>
            </div>
        </div>
        <div class="premium-card p-5">
            <h3 class="font-semibold text-ink mb-3">关系映射规则</h3>
            <ul class="space-y-2 text-sm text-body list-disc list-inside">
                <li>每个客户固定 <strong>1 名投资顾问</strong>，顾问服务多个客户（一对多）。</li>
                <li>每个客户分配 <strong>1~2 名客服</strong>，客服服务多个客户（多对多）。</li>
                <li>投资顾问与客服之间 <strong>无直接关系</strong>，仅通过客户间接关联。</li>
                <li>数据范围：顾问仅见名下客户，客服仅见分配客户，客户本人仅见归属账户。</li>
            </ul>
        </div>
    `;
}

// ==================== Tab 3：批量导入/导出 ====================
function renderImportExportTab(body) {
    body.innerHTML = `
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <div class="premium-card p-5">
                <h3 class="font-semibold text-ink mb-1">导出关系映射</h3>
                <p class="text-sm text-muted mb-4">支持 JSON 与 CSV 双格式，包含客户编号、姓名、顾问、客服。</p>
                <div class="flex gap-3">
                    <button onclick="exportRelationsFile('json')" class="flex-1 px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">导出 JSON</button>
                    <button onclick="exportRelationsFile('csv')" class="flex-1 px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">导出 CSV</button>
                </div>
            </div>
            <div class="premium-card p-5">
                <h3 class="font-semibold text-ink mb-1">导入关系映射</h3>
                <p class="text-sm text-muted mb-4">上传 JSON 或 CSV 文件；client_id 存在则更新，否则新建。</p>
                <label class="block">
                    <input id="relationImportFile" type="file" accept=".json,.csv" onchange="handleRelationImportFile(event)" class="block w-full text-sm text-body file:mr-3 file:px-4 file:py-2 file:rounded-lg file:border-0 file:bg-primary file:text-white file:text-sm file:font-semibold hover:file:bg-primary-active cursor-pointer">
                </label>
                <p class="text-xs text-muted mt-3">CSV 列：client_id,name,advisor_id,service_ids（service_ids 用 | 分隔，可留空）。</p>
            </div>
        </div>
        <div id="relationImportResult" class="mt-5"></div>
    `;
}

function download(filename, text, mime) {
    const blob = new Blob([text], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1500);
}

export async function exportRelationsFile(format) {
    try {
        if (format === 'json') {
            const rows = await exportRelationsJson();
            download('关系映射_' + new Date().toISOString().slice(0, 10) + '.json', JSON.stringify(rows, null, 2), 'application/json');
        } else {
            const csv = await exportRelationsCsv();
            download('关系映射_' + new Date().toISOString().slice(0, 10) + '.csv', csv, 'text/csv;charset=utf-8');
        }
        showToast('✅ 导出成功', 'success');
    } catch (e) {
        showToast('❌ 导出失败：' + (e.message || '未知错误'), 'error');
    }
}

export async function handleRelationImportFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    const resultBox = document.getElementById('relationImportResult');
    try {
        let result;
        if (file.name.toLowerCase().endsWith('.json')) {
            const rows = JSON.parse(text);
            if (!Array.isArray(rows)) throw new Error('JSON 根节点应为数组');
            result = await importRelations(rows);
        } else {
            result = await importRelationsCsv(text);
        }
        const errCount = (result.errors || []).length;
        resultBox.innerHTML = `
            <div class="premium-card p-5 ${errCount ? 'border-warning' : ''}">
                <h3 class="font-semibold text-ink mb-2">导入结果</h3>
                <div class="flex gap-4 text-sm mb-3">
                    <span class="text-positive">新增 ${result.created || 0}</span>
                    <span class="text-primary">更新 ${result.updated || 0}</span>
                    <span class="${errCount ? 'text-negative' : 'text-muted'}">失败 ${errCount}</span>
                </div>
                ${errCount ? `<div class="space-y-1 text-xs text-negative">${result.errors.slice(0, 20).map(e => `<div>第 ${e.row} 行：${escapeHtml(e.detail)}</div>`).join('')}</div>` : ''}
            </div>`;
        showToast(`✅ 导入完成：新增 ${result.created || 0}，更新 ${result.updated || 0}`, errCount ? 'warning' : 'success');
        await loadClients();
    } catch (e) {
        if (resultBox) resultBox.innerHTML = `<div class="premium-card p-5 border-negative/20"><p class="text-sm text-negative">${escapeHtml(e.message || '导入失败')}</p></div>`;
        showToast('❌ 导入失败：' + (e.message || '未知错误'), 'error');
    } finally {
        event.target.value = '';
    }
}

// ==================== Tab 4：账户创建（管理员） ====================
async function renderAccountsTab(body) {
    const { advisors, services } = await loadOptions();
    window.__adminAccountOptions = { advisors, services };
    body.innerHTML = `
        <div class="max-w-xl">
            <div class="premium-card p-6">
                <h3 class="font-semibold text-ink mb-1">创建账户</h3>
                <p class="text-sm text-muted mb-5">用户名/密码留空则按姓名拼音与随机密码自动生成。当角色为「用户-客户」时，会同步生成客户档案并归属到该账号（即可登录后直接加持仓）。</p>
                <div class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">姓名 <span class="text-negative">*</span></label>
                        <input id="accName" type="text" maxlength="64" placeholder="如：张伟" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-ink mb-2">用户名（可空）</label>
                            <input id="accUsername" type="text" placeholder="自动生成" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-ink mb-2">初始密码（可空）</label>
                            <input id="accPassword" type="text" placeholder="自动生成" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-ink mb-2">角色 <span class="text-negative">*</span></label>
                            <select id="accRole" onchange="toggleAccountSubrole()" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                                <option value="admin">管理员</option>
                                <option value="advisor">投资顾问</option>
                                <option value="service">客服</option>
                                <option value="user">用户</option>
                            </select>
                        </div>
                        <div id="accSubroleWrap" class="hidden">
                            <label class="block text-sm font-medium text-ink mb-2">子角色</label>
                            <select id="accSubrole" onchange="toggleAccountClientFields()" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                                <option value="client">客户</option>
                                <option value="non_client">普通用户</option>
                            </select>
                        </div>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">邮箱（可选）</label>
                        <input id="accEmail" type="email" placeholder="name@example.com" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                    </div>

                    <div id="accClientWrap" class="hidden space-y-4 rounded-xl border border-hairline bg-surface-soft p-4">
                        <div class="text-sm font-medium text-ink">同步生成客户档案 · 分配关系（可全不选）</div>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">投资顾问（可空）</label>
                                <select id="accAdvisorId" class="w-full px-4 py-2.5 bg-canvas border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                                    <option value="">(不分配)</option>
                                    ${advisors.map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join('')}
                                </select>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">客服（可空，最多 2 名时建议升级为多选，当前支持先不分配，后续在关系映射里改）</label>
                                <select id="accServiceId" class="w-full px-4 py-2.5 bg-canvas border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                                    <option value="">(不分配)</option>
                                    ${services.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('')}
                                </select>
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">风险等级</label>
                                <select id="accRiskLevel" class="w-full px-4 py-2.5 bg-canvas border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                                    <option value="">未指定</option>
                                    <option value="保守型">保守型</option>
                                    <option value="稳健型">稳健型</option>
                                    <option value="平衡型">平衡型</option>
                                    <option value="积极型">积极型</option>
                                    <option value="激进型">激进型</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-ink mb-2">可用资金 (￥)</label>
                                <input id="accCash" type="number" min="0" step="0.01" value="0" class="w-full px-4 py-2.5 bg-canvas border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                            </div>
                        </div>
                        <p class="text-xs text-muted">未选顾问/客服时，客户档案只挂 owner_user_id，该账号本人登录后即可管理持仓；后续如需服务支持，可在「关系映射」里随时补分配。</p>
                    </div>

                    <button onclick="submitAccountForm()" class="w-full px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">创建账户</button>
                </div>
                <div id="accResult" class="mt-4"></div>
            </div>
        </div>
    `;
    // 初始状态同步
    toggleAccountSubrole();
}

export function toggleAccountSubrole() {
    const role = document.getElementById('accRole')?.value;
    const wrap = document.getElementById('accSubroleWrap');
    if (wrap) wrap.classList.toggle('hidden', role !== 'user');
    toggleAccountClientFields();
}

export function toggleAccountClientFields() {
    const role = document.getElementById('accRole')?.value;
    const sub = document.getElementById('accSubrole')?.value;
    const wrap = document.getElementById('accClientWrap');
    if (wrap) wrap.classList.toggle('hidden', !(role === 'user' && sub === 'client'));
}

export async function submitAccountForm() {
    const name = document.getElementById('accName')?.value.trim();
    const username = document.getElementById('accUsername')?.value.trim();
    const password = document.getElementById('accPassword')?.value.trim();
    const role = document.getElementById('accRole')?.value;
    const subRole = document.getElementById('accSubrole')?.value;
    const email = document.getElementById('accEmail')?.value.trim();
    if (!name) { showToast('❌ 请输入姓名', 'error'); return; }

    try {
        const payload = {
            name,
            role,
            email: email || null,
            username: username || null,
            password: password || null,
        };
        if (role === 'user') payload.sub_role = subRole;
        // user-client：同步传分配关系（表单里选的顾问/客服/风险等级/可用资金）
        if (role === 'user' && subRole === 'client') {
            const advisorId = document.getElementById('accAdvisorId')?.value;
            const serviceId = document.getElementById('accServiceId')?.value;
            const risk = document.getElementById('accRiskLevel')?.value;
            const cash = parseFloat(document.getElementById('accCash')?.value || '0');
            if (advisorId) payload.client_advisor_id = advisorId;
            if (serviceId) payload.client_service_ids = [serviceId];
            if (risk) payload.client_risk_level = risk;
            payload.client_available_cash = Number.isFinite(cash) && cash >= 0 ? cash : 0;
        }
        const user = await createUser(payload);
        const result = document.getElementById('accResult');
        const hasInitial = !!user.initial_password;
        const hasClient = !!user.client_id;
        result.innerHTML = `
            <div class="rounded-xl bg-positive/5 border border-positive/15 p-4 text-sm">
                <div class="font-semibold text-ink mb-2">账户创建成功${hasClient ? ' · 已同步生成客户档案' : ''}</div>
                <div class="space-y-1 text-body">
                    <div>用户名：<span class="font-mono text-ink">${escapeHtml(user.username)}</span></div>
                    ${hasClient ? `<div>客户编号：<span class="font-mono text-ink">${escapeHtml(user.client_id)}</span>（已归属到该账号，登录即可加持仓）</div>` : ''}
                    ${hasInitial ? `<div>初始密码：<span class="font-mono text-ink">${escapeHtml(user.initial_password)}</span>
                        <button onclick="copyText('${escapeHtml(user.initial_password)}')" class="ml-2 text-xs text-primary hover:underline">复制</button></div>` : ''}
                </div>
                ${hasInitial ? '<p class="text-xs text-muted mt-2">请将用户名与初始密码安全转交给该账户本人，登录后请及时修改密码。</p>' : ''}
            </div>`;
        showToast('✅ 账户已创建', 'success');
    } catch (e) {
        showToast('❌ ' + (e.message || '创建失败'), 'error');
    }
}

export function copyText(text) {
    navigator.clipboard?.writeText(text).then(
        () => showToast('✅ 已复制到剪贴板', 'success'),
        () => showToast('❌ 复制失败', 'error'),
    );
}

// ==================== Tab 5：账户管理（列表 + 4 动作） ====================
export async function renderAccountMgmtTab(body) {
    body.innerHTML = `
        <div class="flex items-center justify-between gap-3 mb-4 flex-wrap">
            <div class="relative flex-1 min-w-[240px]">
                <input id="adminAccSearch" oninput="renderAccountMgmtList()" placeholder="按姓名 / 用户名 / 邮箱搜索" class="w-full pl-9 pr-3 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                <svg class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
            </div>
            <div class="flex items-center gap-2">
                <button onclick="renderAccountMgmtTab(document.getElementById('adminPanelBody'))" class="px-4 py-2 text-sm font-medium text-body bg-surface-strong border border-hairline rounded-lg hover:bg-hairline transition-colors">刷新</button>
                <button onclick="switchAdminTab('accounts')" class="px-4 py-2 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-lg transition-colors">+ 新建账户</button>
            </div>
        </div>
        <div id="adminAccList" class="space-y-2"></div>
        <div id="adminAccLoading" class="text-center text-sm text-muted py-10">正在加载账户列表…</div>
    `;
    try {
        const users = await listUsers();
        window.__adminUsers = users;
        document.getElementById('adminAccLoading')?.remove();
        renderAccountMgmtList();
    } catch (e) {
        const loading = document.getElementById('adminAccLoading');
        if (loading) loading.textContent = '❌ 加载失败：' + (e.message || '未知错误');
    }
}

export function renderAccountMgmtList() {
    const container = document.getElementById('adminAccList');
    if (!container) return;
    const q = (document.getElementById('adminAccSearch')?.value || '').trim().toLowerCase();
    const list = (window.__adminUsers || []).filter(u => {
        if (!q) return true;
        return (u.name || '').toLowerCase().includes(q)
            || (u.username || '').toLowerCase().includes(q)
            || (u.email || '').toLowerCase().includes(q)
            || (u.id || '').toLowerCase().includes(q);
    });
    if (!list.length) {
        container.innerHTML = '<div class="text-center text-sm text-muted py-10">暂无匹配的账户</div>';
        return;
    }
    container.innerHTML = list.map(u => {
        const badge = licenseBadgeOf(u) || { html: '' };
        const isUserRole = u.role === 'user';   // user 角色才可以分配关系（client + 非客户普通用户都可）
        const canAssignRelation = isUserRole && u.status !== 'deleted';
        const disabled = u.role === 'admin' && u.username === 'admin'; // 超管账号保护（不能删）
        return `
        <div class="bg-surface-soft/60 border border-hairline rounded-xl px-4 py-3">
            <div class="flex items-start gap-4 flex-wrap">
                <div class="shrink-0 min-w-[140px]">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="text-sm font-semibold text-ink">${escapeHtml(u.name)}</span>
                        <span class="text-[10px] px-1.5 py-0.5 rounded bg-surface-strong text-muted font-mono">${escapeHtml(u.role)}</span>
                        ${u.sub_role ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-surface-strong text-muted">${escapeHtml(u.sub_role)}</span>` : ''}
                    </div>
                    <div class="font-mono text-xs text-muted mt-1">${escapeHtml(u.username || '—')}</div>
                    <div class="text-xs text-muted mt-0.5">${escapeHtml(u.email || '无邮箱')}</div>
                </div>
                <div class="flex items-center gap-3 flex-wrap flex-1">
                    <div class="text-xs text-muted">角色：<span class="text-body">${escapeHtml(roleLabel(u))}</span></div>
                    ${badge.html}
                    ${u.client_id ? `<div class="text-xs text-muted">客户编号：<span class="font-mono text-body">${escapeHtml(u.client_id)}</span></div>` : ''}
                    ${u.expires_at ? `<div class="text-xs text-muted">到期日：<span class="text-body">${escapeHtml(u.expires_at.slice(0, 10))}</span></div>` : ''}
                </div>
                <div class="flex items-center gap-1.5 shrink-0 flex-wrap">
                    <button onclick="adminAccDelete('${u.id}')" ${disabled ? 'disabled title="超级管理员不可删除"' : ''} class="px-2.5 py-1.5 text-xs font-medium text-negative bg-negative/5 border border-negative/15 rounded-lg hover:bg-negative/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">删除账户</button>
                    <button onclick="adminAccResetPwd('${u.id}')" class="px-2.5 py-1.5 text-xs font-medium text-primary bg-primary/5 border border-primary/15 rounded-lg hover:bg-primary/10 transition-colors">重置密码</button>
                    <button onclick="adminAccLifecycle('${u.id}')" ${u.role === 'admin' ? 'disabled title="管理员永久有效"' : ''} class="px-2.5 py-1.5 text-xs font-medium text-amber-600 bg-amber-500/10 border border-amber-500/20 rounded-lg hover:bg-amber-500/15 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">生命周期</button>
                    <button onclick="adminAccRelation('${u.id}')" ${!canAssignRelation ? `disabled title="仅 user 角色账户可以分配客户关系（当前 role=${escapeHtml(u.role)}）"` : ''} class="px-2.5 py-1.5 text-xs font-medium text-body bg-surface-strong border border-hairline rounded-lg hover:bg-hairline transition-colors disabled:opacity-40 disabled:cursor-not-allowed">分配关系</button>
                </div>
            </div>
        </div>`;
    }).join('');
}

// ---- 动作 1：删除账户（软删除）—— 兼容所有非 admin 账户（客户/普通用户/投顾/客服）----
export async function adminAccDelete(userId) {
    const user = (window.__adminUsers || []).find(x => x.id === userId);
    if (!user) return;
    const label = `${user.name}（${user.username || user.id}）`;
    const extraHints = [];
    if (user.role === 'advisor') extraHints.push('将同时解除该顾问所有客户的归属绑定（clients.advisor_id 置空）');
    if (user.role === 'service') extraHints.push('将同时解除该客服在 service_assignments 中的全部服务关联');
    if (user.client_id) extraHints.push(`关联客户档案（${user.client_id}）将保留，关系绑定被解除`);
    const extraText = extraHints.length ? `\n📋 同步清理：\n${extraHints.map(h => '  • ' + h).join('\n')}` : '';
    if (!confirm(`⚠️ 确定软删除账户「${label}」吗？\n\n删除后：账户状态变为「已删除」，数据保留但无法登录，可在后端人工恢复。${extraText}`)) return;
    try {
        const stats = await deleteUser(userId);
        let cleared = '';
        if (stats) {
            const c1 = Number(stats.cleared_advisor || 0);
            const c2 = Number(stats.cleared_service_assignments || 0);
            if (c1 || c2) cleared = `（解除顾问归属 ${c1} 条 / 服务关系 ${c2} 条）`;
        }
        showToast(`✅ 账户已软删除${cleared}`, 'success');
        // 同步刷新客户列表与用户选项缓存：避免后续切到"关系映射"时仍看到引用了被删账户的脏关系
        await loadClients();
        if (window.__adminAdvisors || window.__adminServices) {
            try {
                const opts = await loadOptions();
                window.__adminAdvisors = opts.advisors;
                window.__adminServices = opts.services;
            } catch { /* 忽略 */ }
        }
        await renderAccountMgmtTab(document.getElementById('adminPanelBody'));
    } catch (e) {
        showToast('❌ ' + (e.message || '删除失败'), 'error');
    }
}

// ---- 动作 2：重置密码 ----
export function adminAccResetPwd(userId) {
    const user = (window.__adminUsers || []).find(x => x.id === userId);
    if (!user) return;
    const id = 'accResetPwdModal';
    const modal = document.createElement('div');
    modal.id = id;
    modal.className = 'fixed inset-0 z-[130]';
    modal.innerHTML = `
        <div class="absolute inset-0 modal-backdrop" onclick="document.getElementById('${id}')?.remove()"></div>
        <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md mx-4">
            <div class="bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up">
                <div class="px-7 pt-6 pb-4 border-b border-hairline">
                    <h3 class="text-lg font-semibold text-ink">重置密码 — ${escapeHtml(user.name)}</h3>
                    <p class="text-sm text-muted mt-1">留空则由系统生成随机密码并返回给你</p>
                </div>
                <div class="px-7 py-5">
                    <label class="block text-sm font-medium text-ink mb-2">新密码（可空）</label>
                    <input id="rpwdInput" type="text" placeholder="留空自动生成" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                </div>
                <div class="px-7 pb-6 flex gap-3">
                    <button onclick="document.getElementById('${id}')?.remove()" class="flex-1 px-4 py-3 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors">取消</button>
                    <button onclick="submitAccResetPwd('${userId}', '${id}')" class="flex-1 px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">确认重置</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(modal);
}

export async function submitAccResetPwd(userId, modalId) {
    const pwd = document.getElementById('rpwdInput')?.value?.trim() || null;
    try {
        const res = await resetUserPassword(userId, pwd);
        const newPwd = res?.new_password || '(未返回)';
        document.getElementById(modalId)?.remove();
        showToast('✅ 密码已重置', 'success');
        alert(`账户密码已重置。\n新密码：${newPwd}\n请安全转交账户本人。`);
    } catch (e) {
        showToast('❌ ' + (e.message || '重置失败'), 'error');
    }
}

// ---- 动作 3：生命周期管理（续费 / 改状态 / 改到期日）----
export function adminAccLifecycle(userId) {
    const user = (window.__adminUsers || []).find(x => x.id === userId);
    if (!user) return;
    const id = 'accLifecycleModal';
    const curStatus = user.status || 'active';
    const curExpire = user.expires_at ? user.expires_at.slice(0, 10) : '';
    const modal = document.createElement('div');
    modal.id = id;
    modal.className = 'fixed inset-0 z-[130]';
    modal.innerHTML = `
        <div class="absolute inset-0 modal-backdrop" onclick="document.getElementById('${id}')?.remove()"></div>
        <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg mx-4">
            <div class="bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up max-h-[90vh] overflow-y-auto">
                <div class="px-7 pt-6 pb-4 border-b border-hairline">
                    <h3 class="text-lg font-semibold text-ink">生命周期管理 — ${escapeHtml(user.name)}</h3>
                    <p class="text-sm text-muted mt-1">当前角色：${escapeHtml(roleLabel(user))}　当前状态：${escapeHtml(curStatus)}　剩余：${(licenseBadgeOf(user) || {}).text || '—'}</p>
                </div>
                <div class="px-7 py-5 space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">续期天数（填 30 表示续费 30 天）</label>
                        <div class="flex items-center gap-2">
                            <input id="lcRenewDays" type="number" min="0" step="1" placeholder="如：365" class="flex-1 px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                            <button onclick="submitAccRenew('${userId}', '${id}')" class="px-4 py-2.5 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors whitespace-nowrap">执行续费</button>
                        </div>
                        <p class="text-xs text-muted mt-1">续费会在当前到期日基础上累加；如果已经到期，将从「今天」开始累加。</p>
                    </div>
                    <div class="h-px bg-hairline"></div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-ink mb-2">状态</label>
                            <select id="lcStatus" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                                <option value="active" ${curStatus === 'active' ? 'selected' : ''}>active（正常）</option>
                                <option value="expired" ${curStatus === 'expired' ? 'selected' : ''}>expired（到期）</option>
                                <option value="deleted" ${curStatus === 'deleted' ? 'selected' : ''}>deleted（软删除）</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-ink mb-2">到期日期</label>
                            <input id="lcExpire" type="date" value="${escapeHtml(curExpire)}" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                        </div>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">或直接设置「赠送 N 天」（从零重置有效期，覆盖上面的到期日）</label>
                        <input id="lcLicenseDays" type="number" min="0" step="1" placeholder="如：365" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                    </div>
                </div>
                <div class="px-7 pb-6 flex gap-3">
                    <button onclick="document.getElementById('${id}')?.remove()" class="flex-1 px-4 py-3 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors">取消</button>
                    <button onclick="submitAccLifecyclePatch('${userId}', '${id}')" class="flex-1 px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">保存以上状态/日期修改</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(modal);
}

export async function submitAccRenew(userId, modalId) {
    const days = parseInt(document.getElementById('lcRenewDays')?.value || '0', 10);
    if (!days || days <= 0) { showToast('❌ 请填写续期天数', 'error'); return; }
    try {
        await renewUser(userId, days);
        showToast(`✅ 已续费 ${days} 天`, 'success');
        document.getElementById(modalId)?.remove();
        await renderAccountMgmtTab(document.getElementById('adminPanelBody'));
    } catch (e) {
        showToast('❌ ' + (e.message || '续费失败'), 'error');
    }
}

export async function submitAccLifecyclePatch(userId, modalId) {
    const status = document.getElementById('lcStatus')?.value || null;
    const expire = document.getElementById('lcExpire')?.value || null;
    const licenseDays = document.getElementById('lcLicenseDays')?.value?.trim();
    try {
        const patch = {};
        if (status) patch.status = status;
        if (expire) patch.expires_at = expire + 'T23:59:59';
        if (licenseDays) patch.license_days = parseInt(licenseDays, 10);
        if (!Object.keys(patch).length) { showToast('⚠️ 未做任何修改', 'warning'); return; }
        await patchUserLifecycle(userId, patch);
        showToast('✅ 生命周期已更新', 'success');
        document.getElementById(modalId)?.remove();
        await renderAccountMgmtTab(document.getElementById('adminPanelBody'));
    } catch (e) {
        showToast('❌ ' + (e.message || '更新失败'), 'error');
    }
}

// ---- 动作 4：分配关系（仅 user-client 账户）----
export async function adminAccRelation(userId) {
    let user = (window.__adminUsers || []).find(x => x.id === userId);
    // 情形 A：账户不在缓存里，或未关联 client_id → 先尝试自动补齐客户档案
    //   补齐逻辑走后端 ensure-client-profile 接口：它会校验角色、校验是否已删除、返回已存在的档案。
    if (!user || !user.client_id) {
        try {
            const resp = await ensureClientProfile(userId);
            const parts = [];
            if (resp && resp.created) parts.push(`新建客户档案 ${resp.client_id}`);
            else if (resp && resp.client_id) parts.push(`关联已有客户档案 ${resp.client_id}`);
            if (resp && resp.role_changed) parts.push('角色已自动由「普通用户/非客户」升级为「客户」');
            if (parts.length) {
                showToast(`✅ ${parts.join('；')}，请继续分配关系`, 'success');
            } else {
                showToast('⚠️ 未获取到客户档案，无法分配关系', 'warning');
                return;
            }
            // 刷新账户列表缓存，让新的 user.client_id 生效，再继续弹窗
            const panelBody = document.getElementById('adminPanelBody');
            if (panelBody) {
                const { renderAccountMgmtTab } = await import('./admin.js').catch(() => ({ renderAccountMgmtTab: null }));
                if (renderAccountMgmtTab) await renderAccountMgmtTab(panelBody);
            }
            // 同步本地缓存
            if (window.__adminUsers) {
                const latest = window.__adminUsers.find(x => x.id === userId);
                if (latest) user = latest;
            }
            if (!user) {
                showToast('⚠️ 账户资料刷新失败，请稍后重试', 'warning');
                return;
            }
        } catch (e) {
            const msg = (e && e.message) ? e.message : '无法补齐客户档案';
            showToast('⚠️ ' + msg, 'warning');
            return;
        }
    }
    // 兜底：补齐成功仍没有 client_id（极端情况）
    if (!user.client_id) {
        showToast('⚠️ 客户档案仍未关联，请刷新页面后重试', 'warning');
        return;
    }
    // 确保客户列表也已拉到最新（刚补齐档案 clients 里可能还没这一条）
    if (!(clients || []).find(x => x.id === user.client_id)) {
        await loadClients();
    }
    const { advisors, services } = window.__adminAccountOptions || await loadOptions();
    const c = (clients || []).find(x => x.id === user.client_id) || {};
    const advisorOptions = [
        `<option value="">(不分配)</option>`,
        ...advisors.map(a => `<option value="${a.id}" ${c.advisorId === a.id ? 'selected' : ''}>${escapeHtml(a.name)}</option>`),
    ].join('');
    const serviceChecks = services.map(s => {
        const checked = (c.serviceIds || []).includes(s.id) ? 'checked' : '';
        return `<label class="flex items-center gap-2 text-sm text-body"><input type="checkbox" class="rel-svc-check" value="${s.id}" ${checked}> ${escapeHtml(s.name)}</label>`;
    }).join('');
    const id = 'accRelationModal';
    const modal = document.createElement('div');
    modal.id = id;
    modal.className = 'fixed inset-0 z-[130]';
    modal.innerHTML = `
        <div class="absolute inset-0 modal-backdrop" onclick="document.getElementById('${id}')?.remove()"></div>
        <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md mx-4">
            <div class="bg-canvas rounded-3xl modal-panel overflow-hidden animate-fade-in-up max-h-[90vh] overflow-y-auto">
                <div class="px-7 pt-6 pb-4 border-b border-hairline">
                    <h3 class="text-lg font-semibold text-ink">分配客户关系</h3>
                    <p class="text-sm text-muted mt-1">账户 ${escapeHtml(user.name)} → 客户 ${escapeHtml(c.name || user.client_id)}（${escapeHtml(user.client_id)}）</p>
                </div>
                <div class="px-7 py-5 space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">投资顾问（可空）</label>
                        <select id="arAdvisor" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">${advisorOptions}</select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">客服（最多 2 名，可空）</label>
                        <div class="grid grid-cols-2 gap-2 bg-surface-soft rounded-xl p-3 border border-hairline">${serviceChecks || '<div class="text-xs text-muted col-span-2">暂无客服可选</div>'}</div>
                    </div>
                </div>
                <div class="px-7 pb-6 flex gap-3">
                    <button onclick="document.getElementById('${id}')?.remove()" class="flex-1 px-4 py-3 text-sm font-medium text-body bg-surface-strong hover:bg-hairline rounded-xl transition-colors">取消</button>
                    <button onclick="submitAccRelation('${user.client_id}', '${id}')" class="flex-1 px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">保存</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(modal);
}

export async function submitAccRelation(clientId, modalId) {
    const advisorId = document.getElementById('arAdvisor')?.value || '';
    const svcIds = Array.from(document.querySelectorAll('.rel-svc-check:checked')).map(x => x.value);
    if (svcIds.length > 2) { showToast('❌ 客服最多选 2 名', 'error'); return; }
    try {
        await updateClientRelations(clientId, {
            advisor_id: advisorId || null,
            service_ids: svcIds,
        });
        showToast('✅ 关系已更新', 'success');
        document.getElementById(modalId)?.remove();
    } catch (e) {
        showToast('❌ ' + (e.message || '保存失败'), 'error');
    }
}

// ==================== Tab 6：查看日志（audit_logs 多条件查询 + 表格） ====================
const AUDIT_ACTION_OPTIONS = [
    '', 'user.login', 'user.login_fail', 'user.create', 'user.update',
    'user.delete', 'user.password_change', 'user.password_reset',
    'user.renew', 'user.lifecycle_patch',
    'client.create', 'client.update', 'client.delete', 'client.relations_update',
    'position.create', 'position.update', 'position.delete',
    'transaction.adjust', 'transaction.create', 'transaction.delete',
    'relations.import', 'relations.export',
];
const AUDIT_TARGET_TYPE_OPTIONS = ['', 'user', 'client', 'position', 'transaction', 'audit_log'];

async function renderAuditLogsTab(body) {
    body.innerHTML = `
        <div class="premium-card p-4 mb-4">
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
                <div class="lg:col-span-2">
                    <label class="block text-xs font-medium text-muted mb-1">关键词（操作人/描述/目标/IP 通配搜索）</label>
                    <div class="relative">
                        <input id="alKeyword" placeholder="如：张伟 / user.create / C001 / 192.168" class="w-full pl-9 pr-3 py-2 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                        <svg class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
                    </div>
                </div>
                <div>
                    <label class="block text-xs font-medium text-muted mb-1">操作类型</label>
                    <select id="alAction" class="w-full px-3 py-2 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                        ${AUDIT_ACTION_OPTIONS.map(a => `<option value="${a}">${a || '（全部）'}</option>`).join('')}
                    </select>
                </div>
                <div>
                    <label class="block text-xs font-medium text-muted mb-1">目标类型</label>
                    <select id="alTargetType" class="w-full px-3 py-2 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                        ${AUDIT_TARGET_TYPE_OPTIONS.map(a => `<option value="${a}">${a || '（全部）'}</option>`).join('')}
                    </select>
                </div>
                <div>
                    <label class="block text-xs font-medium text-muted mb-1">操作人 ID（可空）</label>
                    <input id="alActorId" placeholder="如：uuid 或 admin" class="w-full px-3 py-2 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                </div>
                <div>
                    <label class="block text-xs font-medium text-muted mb-1">开始时间（>=）</label>
                    <input id="alStart" type="datetime-local" class="w-full px-3 py-2 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                </div>
                <div>
                    <label class="block text-xs font-medium text-muted mb-1">结束时间（<=）</label>
                    <input id="alEnd" type="datetime-local" class="w-full px-3 py-2 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                </div>
                <div class="flex items-end gap-2">
                    <button onclick="auditLogsSearch(1)" class="flex-1 px-4 py-2 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">查询</button>
                    <button onclick="auditLogsReset()" class="px-4 py-2 text-sm font-medium text-body bg-surface-strong border border-hairline rounded-xl hover:bg-hairline transition-colors">重置</button>
                </div>
            </div>
        </div>
        <div class="flex items-center justify-between mb-2 text-xs text-muted">
            <div id="alSummary">—</div>
            <div class="flex items-center gap-2">
                <span>每页</span>
                <select id="alPageSize" onchange="auditLogsSearch(1)" class="px-2 py-1 bg-surface-strong border border-hairline rounded-lg text-xs">
                    <option value="20">20</option>
                    <option value="50" selected>50</option>
                    <option value="100">100</option>
                    <option value="200">200</option>
                </select>
                <span>条</span>
            </div>
        </div>
        <div class="premium-card overflow-hidden">
            <div class="overflow-x-auto">
                <table class="w-full text-sm">
                    <thead class="bg-surface-soft text-xs text-muted">
                        <tr>
                            <th class="text-left px-3 py-2.5 font-medium">时间</th>
                            <th class="text-left px-3 py-2.5 font-medium">操作人</th>
                            <th class="text-left px-3 py-2.5 font-medium">动作</th>
                            <th class="text-left px-3 py-2.5 font-medium">目标</th>
                            <th class="text-left px-3 py-2.5 font-medium">描述</th>
                            <th class="text-left px-3 py-2.5 font-medium">IP / UA</th>
                        </tr>
                    </thead>
                    <tbody id="alTableBody" class="divide-y divide-hairline">
                        <tr><td colspan="6" class="text-center text-muted py-8">正在加载…</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
        <div id="alPagination" class="mt-4 flex items-center justify-between flex-wrap gap-2"></div>
    `;
    window.__auditLogsState = { page: 1 };
    await auditLogsSearch(1);
}

export function auditLogsReset() {
    ['alKeyword', 'alAction', 'alTargetType', 'alActorId', 'alStart', 'alEnd'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    auditLogsSearch(1);
}

export async function auditLogsSearch(page) {
    const params = {
        page: page || 1,
        page_size: parseInt(document.getElementById('alPageSize')?.value || '50', 10),
    };
    const kw = document.getElementById('alKeyword')?.value?.trim();
    if (kw) params.keyword = kw;
    const action = document.getElementById('alAction')?.value;
    if (action) params.action = action;
    const tt = document.getElementById('alTargetType')?.value;
    if (tt) params.target_type = tt;
    const actorId = document.getElementById('alActorId')?.value?.trim();
    if (actorId) params.actor_id = actorId;
    const start = document.getElementById('alStart')?.value;
    if (start) params.start = start.replace('T', ' ') + ':00';
    const end = document.getElementById('alEnd')?.value;
    if (end) params.end = end.replace('T', ' ') + ':59';

    try {
        const data = await listAuditLogs(params);
        window.__auditLogsState = { page, total: data.total || 0, page_size: params.page_size };
        renderAuditLogTable(data.items || []);
        renderAuditLogPagination();
    } catch (e) {
        const body = document.getElementById('alTableBody');
        if (body) body.innerHTML = `<tr><td colspan="6" class="text-center text-negative py-8">❌ 查询失败：${escapeHtml(e.message || '未知错误')}</td></tr>`;
    }
}

function renderAuditLogTable(items) {
    const body = document.getElementById('alTableBody');
    const sum = document.getElementById('alSummary');
    const state = window.__auditLogsState || {};
    if (sum) {
        sum.textContent = `共 ${state.total || 0} 条记录${items.length ? `，当前显示第 ${(state.page - 1) * state.page_size + 1} - ${Math.min(state.page * state.page_size, state.total)} 条` : ''}`;
    }
    if (!body) return;
    if (!items.length) {
        body.innerHTML = `<tr><td colspan="6" class="text-center text-muted py-8">暂无符合条件的记录</td></tr>`;
        return;
    }
    body.innerHTML = items.map(log => {
        const time = log.created_at ? new Date(log.created_at).toLocaleString('zh-CN', { hour12: false }) : '—';
        const actionBadge = colorOfAction(log.action);
        const targetLink = log.target_id
            ? `<div><span class="text-[10px] px-1.5 py-0.5 rounded bg-surface-strong text-muted mr-1">${escapeHtml(log.target_type || '—')}</span><span class="font-mono text-xs">${escapeHtml(log.target_id)}</span></div>`
            : '<span class="text-muted text-xs">—</span>';
        return `
        <tr class="align-top hover:bg-surface-soft/40">
            <td class="px-3 py-2.5 font-mono text-xs text-muted whitespace-nowrap">${escapeHtml(time)}</td>
            <td class="px-3 py-2.5">
                <div class="text-sm text-ink font-medium">${escapeHtml(log.actor_name || '—')}</div>
                <div class="font-mono text-[10px] text-muted">${escapeHtml(log.actor_id || '')}</div>
            </td>
            <td class="px-3 py-2.5"><span class="text-[11px] px-2 py-0.5 rounded ${actionBadge.cls}">${escapeHtml(log.action || '—')}</span></td>
            <td class="px-3 py-2.5">${targetLink}</td>
            <td class="px-3 py-2.5 max-w-[320px]">
                <div class="text-xs text-body whitespace-pre-wrap break-all">${escapeHtml(log.note || '—')}</div>
            </td>
            <td class="px-3 py-2.5 max-w-[180px]">
                <div class="font-mono text-[10px] text-muted">${escapeHtml(log.ip_address || '—')}</div>
                <div class="text-[10px] text-muted truncate" title="${escapeHtml(log.user_agent || '')}">${escapeHtml(log.user_agent || '—')}</div>
            </td>
        </tr>`;
    }).join('');
}

function colorOfAction(action) {
    const a = (action || '').toLowerCase();
    if (a.includes('delete') || a.includes('fail') || a.includes('expired')) return { cls: 'bg-negative/10 text-negative' };
    if (a.includes('create') || a.includes('login') && !a.includes('fail')) return { cls: 'bg-positive/10 text-positive' };
    if (a.includes('update') || a.includes('patch') || a.includes('change') || a.includes('reset') || a.includes('adjust')) return { cls: 'bg-primary/10 text-primary' };
    if (a.includes('export') || a.includes('import')) return { cls: 'bg-amber-500/15 text-amber-600' };
    return { cls: 'bg-surface-strong text-body' };
}

function renderAuditLogPagination() {
    const el = document.getElementById('alPagination');
    if (!el) return;
    const state = window.__auditLogsState || {};
    const total = state.total || 0;
    const pageSize = state.page_size || 50;
    const cur = state.page || 1;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    if (total === 0) { el.innerHTML = ''; return; }

    const buttons = [];
    const addBtn = (p, label, disabled = false, active = false) => {
        buttons.push(`<button onclick="auditLogsSearch(${p})" ${disabled ? 'disabled' : ''} class="px-3 py-1.5 text-xs rounded-lg border transition-colors ${active ? 'bg-primary text-white border-primary' : disabled ? 'bg-surface-strong text-muted border-hairline cursor-not-allowed opacity-50' : 'bg-canvas text-body border-hairline hover:bg-surface-strong'}">${label}</button>`);
    };
    addBtn(1, '首页', cur === 1);
    addBtn(cur - 1, '上一页', cur === 1);
    const windowSize = 2;
    const start = Math.max(1, cur - windowSize);
    const end = Math.min(pages, cur + windowSize);
    if (start > 1) buttons.push('<span class="text-xs text-muted px-1">…</span>');
    for (let p = start; p <= end; p++) addBtn(p, String(p), false, p === cur);
    if (end < pages) buttons.push('<span class="text-xs text-muted px-1">…</span>');
    addBtn(cur + 1, '下一页', cur === pages);
    addBtn(pages, '末页', cur === pages);

    el.innerHTML = `
        <div class="text-xs text-muted">第 ${cur} / ${pages} 页 · 共 ${total} 条</div>
        <div class="flex items-center gap-1 flex-wrap">${buttons.join('')}</div>
    `;
}
