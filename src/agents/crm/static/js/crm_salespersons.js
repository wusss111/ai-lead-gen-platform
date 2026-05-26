// crm_salespersons.js — Salesperson CRUD management
import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/utils.js';

let _salespersonList = [];
let _authStatusCache = {};

// ---- Load auth statuses ----
async function loadAuthStatuses() {
  _authStatusCache = {};
  const checks = _salespersonList.map(async s => {
    try {
      const r = await apiFetch('/crm/api/salespersons/' + s.id + '/gmail-status');
      if (r.ok) {
        const data = await r.json();
        _authStatusCache[s.id] = data.authorized;
      }
    } catch (e) { /* ignore */ }
  });
  await Promise.all(checks);
}

// ---- Load table ----
async function loadTable() {
  const tbody = document.getElementById('tableBody');
  try {
    const r = await apiFetch('/crm/api/salespersons');
    if (!r.ok) { tbody.innerHTML = '<tr><td colspan="8" class="empty-state">加载失败</td></tr>'; return; }
    const list = await r.json();
    _salespersonList = list;
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty-state">暂无销售人员，点击"添加销售"开始</td></tr>';
      return;
    }
    // Load auth statuses in background then re-render
    loadAuthStatuses().then(() => renderTable());
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-state">加载异常</td></tr>';
  }
}

function renderTable() {
  const tbody = document.getElementById('tableBody');
  tbody.innerHTML = _salespersonList.map(s => `
    <tr>
      <td><strong>${esc(s.name)}</strong></td>
      <td>${esc(s.email) || '-'}</td>
      <td>${esc(s.phone) || '-'}</td>
      <td>${(s.smtp_username || s.email) ? esc(s.smtp_username || s.email) : '<span style="color:var(--text-muted)">未绑定</span>'}</td>
      <td>${authBadge(s.id)}</td>
      <td>${s.customer_count || 0}</td>
      <td>${s.is_active ? '<span class="badge badge-green">在职</span>' : '<span class="badge badge-gray">停用</span>'}</td>
      <td style="display:flex; gap:0.35rem; flex-wrap:wrap">
        <button class="btn-small" onclick="editSalesperson(${s.id})">编辑</button>
        <button class="btn-small" onclick="authGmail(${s.id})">Gmail 授权</button>
        <button class="btn-small" onclick="toggleActive(${s.id}, ${s.is_active ? 0 : 1})">${s.is_active ? '停用' : '启用'}</button>
        <button class="btn-small btn-danger" onclick="deleteSalesperson(${s.id}, '${escJs(s.name)}')">删除</button>
      </td>
    </tr>
  `).join('');
}

function authBadge(id) {
  if (_authStatusCache[id]) {
    return '<span class="badge badge-green">已授权</span>';
  }
  return '<span class="badge badge-gray">未授权</span>';
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function escJs(s) {
  if (!s) return '';
  return String(s).replace(/'/g, "\\'").replace(/"/g, '\\"').replace(/\n/g, '\\n');
}

// ---- Modal ----
window.showAddModal = function () {
  document.getElementById('modalTitle').textContent = '添加销售';
  document.getElementById('spId').value = '';
  document.getElementById('spName').value = '';
  document.getElementById('spEmail').value = '';
  document.getElementById('spPhone').value = '';
  document.getElementById('spSmtpHost').value = '';
  document.getElementById('spSmtpPort').value = '587';
  document.getElementById('spSmtpUser').value = '';
  document.getElementById('spSmtpPass').value = '';
  document.getElementById('spImapHost').value = '';
  document.getElementById('spImapPort').value = '993';
  document.getElementById('spWeworkUserid').value = '';
  document.getElementById('spModal').style.display = 'flex';
};

window.editSalesperson = function (id) {
  const sp = _salespersonList.find(s => s.id === id);
  if (!sp) { showToast('未找到该销售人员', 'error'); return; }
  document.getElementById('modalTitle').textContent = '编辑销售';
  document.getElementById('spId').value = sp.id;
  document.getElementById('spName').value = sp.name;
  document.getElementById('spEmail').value = sp.email || '';
  document.getElementById('spPhone').value = sp.phone || '';
  document.getElementById('spSmtpHost').value = sp.smtp_host || '';
  document.getElementById('spSmtpPort').value = sp.smtp_port || 587;
  document.getElementById('spSmtpUser').value = sp.smtp_username || '';
  document.getElementById('spSmtpPass').value = sp.smtp_password || '';
  document.getElementById('spImapHost').value = sp.imap_host || '';
  document.getElementById('spImapPort').value = sp.imap_port || 993;
  document.getElementById('spWeworkUserid').value = sp.wework_userid || '';
  document.getElementById('spModal').style.display = 'flex';
};

window.closeModal = function () {
  document.getElementById('spModal').style.display = 'none';
};

window.saveSalesperson = async function () {
  const id = document.getElementById('spId').value;
  const name = document.getElementById('spName').value.trim();
  if (!name) { showToast('请输入姓名', 'error'); return; }

  const fd = new FormData();
  fd.append('name', name);
  fd.append('email', document.getElementById('spEmail').value.trim());
  fd.append('phone', document.getElementById('spPhone').value.trim());
  fd.append('smtp_host', document.getElementById('spSmtpHost').value.trim());
  fd.append('smtp_port', document.getElementById('spSmtpPort').value);
  fd.append('smtp_username', document.getElementById('spSmtpUser').value.trim());
  fd.append('smtp_password', document.getElementById('spSmtpPass').value);
  fd.append('imap_host', document.getElementById('spImapHost').value.trim());
  fd.append('imap_port', document.getElementById('spImapPort').value);
  fd.append('wework_userid', document.getElementById('spWeworkUserid').value.trim());

  const method = id ? 'PUT' : 'POST';
  const url = id ? '/crm/api/salespersons/' + id : '/crm/api/salespersons';

  try {
    const r = await apiFetch(url, { method, body: fd });
    if (!r.ok) { showToast('保存失败: ' + r.status, 'error'); return; }
    closeModal();
    loadTable();
    showToast(id ? '已更新' : '已添加', 'info');
  } catch (e) {
    showToast('保存异常: ' + e.message, 'error');
  }
};

window.toggleActive = async function (id, active) {
  const fd = new FormData();
  fd.append('is_active', active);
  try {
    const r = await apiFetch('/crm/api/salespersons/' + id, { method: 'PUT', body: fd });
    if (!r.ok) { showToast('操作失败', 'error'); return; }
    loadTable();
    showToast(active ? '已启用' : '已停用', 'info');
  } catch (e) {
    showToast('操作异常', 'error');
  }
};

window.deleteSalesperson = async function (id, name) {
  if (!confirm('确定要删除「' + name + '」吗？\n该销售负责的客户将自动取消分配。')) return;
  try {
    const r = await apiFetch('/crm/api/salespersons/' + id, { method: 'DELETE' });
    if (!r.ok) { showToast('删除失败', 'error'); return; }
    loadTable();
    showToast('已删除', 'info');
  } catch (e) {
    showToast('删除异常', 'error');
  }
};

// Close modal on background click
document.getElementById('spModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeModal();
});

window.authGmail = async function (id) {
  const sp = _salespersonList.find(s => s.id === id);
  if (!sp) { showToast('未找到该销售人员', 'error'); return; }
  if (!confirm('即将为「' + sp.name + '」启动 Gmail 授权流程。\n\n浏览器会弹出 Google 授权页面，请用业务员自己的 Gmail 邮箱登录并授权。\n\n点击确定继续。')) return;

  showToast('正在启动授权流程，请查看浏览器...', 'info');
  try {
    const r = await apiFetch('/crm/api/salespersons/' + id + '/gmail-auth', { method: 'POST' });
    if (r.ok) {
      const data = await r.json();
      _authStatusCache[id] = true;
      renderTable();
      showToast('Gmail 授权成功！已绑定邮箱: ' + (data.email || sp.email), 'info');
    } else {
      const err = await r.json().catch(() => ({}));
      showToast('授权失败: ' + (err.error || r.status), 'error');
    }
  } catch (e) {
    showToast('授权异常: ' + e.message, 'error');
  }
};

document.addEventListener('DOMContentLoaded', loadTable);
