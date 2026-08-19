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
                <div class="px-6 pt-4 border-b border-hairline flex items-center gap-2 shrink-0">
                    ${tabButton('relations', '关系映射', true)}
                    ${tabButton('visualize', '可视化规则', false)}
                    ${tabButton('importexport', '批量导入/导出', false)}
                    ${tabButton('accounts', '账户创建', false)}
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
    const list = (clients || []).filter(c =>
        !q || c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q)
    );
    if (!list.length) {
        container.innerHTML = '<div class="text-center text-sm text-muted py-10">暂无匹配的客户关系</div>';
        return;
    }
    container.innerHTML = list.map(c => {
        const serviceNames = (c.serviceIds || []).map(id => nameOf(services, id)).join('、') || '未分配';
        return `
        <div class="flex items-center gap-4 bg-surface-soft/60 border border-hairline rounded-xl px-4 py-3">
            <div class="w-20 shrink-0">
                <div class="font-mono text-xs text-muted">${escapeHtml(c.id)}</div>
                <div class="text-sm font-semibold text-ink">${escapeHtml(c.name)}</div>
            </div>
            <div class="flex-1 grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
                <div class="flex items-center gap-2">
                    <span class="w-1.5 h-1.5 rounded-full bg-primary shrink-0"></span>
                    <span class="text-muted">顾问：</span><span class="text-ink">${escapeHtml(nameOf(advisors, c.advisorId))}</span>
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

    const advisorOptions = advisors.map(a => `<option value="${a.id}" ${client?.advisorId === a.id ? 'selected' : ''}>${escapeHtml(a.name)}</option>`).join('');
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
                    <p class="text-sm text-muted mt-1">${isEdit ? `客户 ${escapeHtml(client.name)}（${client.id}）` : '创建客户并配置顾问与客服'}</p>
                </div>
                <div class="px-7 py-5 space-y-4">
                    ${isEdit ? '' : `
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">客户姓名 <span class="text-negative">*</span></label>
                        <input id="relName" type="text" maxlength="64" placeholder="如：张伟" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                    </div>`}
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">投资顾问 <span class="text-negative">*</span></label>
                        <select id="relAdvisor" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">${advisorOptions}</select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">客服（1~2 名）<span class="text-negative">*</span></label>
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
    const advisorId = document.getElementById('relAdvisor')?.value;
    const serviceIds = Array.from(document.querySelectorAll('.svc-check:checked')).map(x => x.value);
    if (!advisorId) { showToast('❌ 请选择投资顾问', 'error'); return; }
    if (serviceIds.length < 1 || serviceIds.length > 2) { showToast('❌ 请选择 1~2 名客服', 'error'); return; }

    try {
        if (clientId) {
            await updateClientRelations(clientId, { advisor_id: advisorId, service_ids: serviceIds });
            showToast('✅ 关系已更新', 'success');
        } else {
            const name = document.getElementById('relName')?.value.trim();
            if (!name) { showToast('❌ 请输入客户姓名', 'error'); return; }
            await createClient({ name, advisor_id: advisorId, service_ids: serviceIds });
            showToast('✅ 关系已创建', 'success');
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
function renderAccountsTab(body) {
    body.innerHTML = `
        <div class="max-w-xl">
            <div class="premium-card p-6">
                <h3 class="font-semibold text-ink mb-1">创建账户</h3>
                <p class="text-sm text-muted mb-5">用户名/密码留空则按姓名拼音与随机密码自动生成。</p>
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
                            <select id="accSubrole" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                                <option value="client">客户</option>
                                <option value="non_client">普通用户</option>
                            </select>
                        </div>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-ink mb-2">邮箱（可选）</label>
                        <input id="accEmail" type="email" placeholder="name@example.com" class="w-full px-4 py-2.5 bg-surface-strong border border-hairline rounded-xl text-sm text-ink focus:outline-none focus:border-primary">
                    </div>
                    <button onclick="submitAccountForm()" class="w-full px-4 py-3 text-sm font-semibold text-white bg-primary hover:bg-primary-active rounded-xl transition-colors">创建账户</button>
                </div>
                <div id="accResult" class="mt-4"></div>
            </div>
        </div>
    `;
}

export function toggleAccountSubrole() {
    const role = document.getElementById('accRole')?.value;
    const wrap = document.getElementById('accSubroleWrap');
    if (wrap) wrap.classList.toggle('hidden', role !== 'user');
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
        const user = await createUser(payload);
        const result = document.getElementById('accResult');
        const hasInitial = !!user.initial_password;
        result.innerHTML = `
            <div class="rounded-xl bg-positive/5 border border-positive/15 p-4 text-sm">
                <div class="font-semibold text-ink mb-2">账户创建成功</div>
                <div class="space-y-1 text-body">
                    <div>用户名：<span class="font-mono text-ink">${escapeHtml(user.username)}</span></div>
                    ${hasInitial ? `<div>初始密码：<span class="font-mono text-ink">${escapeHtml(user.initial_password)}</span>
                        <button onclick="copyText('${escapeHtml(user.initial_password)}')" class="ml-2 text-xs text-primary hover:underline">复制</button></div>` : ''}
                </div>
                ${hasInitial ? '<p class="text-xs text-muted mt-2">请将初始密码安全转交给该账户本人，登录后请及时修改密码。</p>' : ''}
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
