// crm_salespersons.js — Salesperson CRUD management
import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/utils.js';

// ---- Load table ----
async function loadTable() {
  const tbody = document.getElementById('tableBody');
  try {
    const r = await apiFetch('/crm/api/salespersons');
    if (!r.ok) { tbody.innerHTML = '<tr><td colspan="6" class="empty-state">加载失败</td></tr>'; return; }
    const list = await r.json();
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-state">暂无销售人员，点击"添加销售"开始</td></tr>';
      return;
    }
    tbody.innerHTML = list.map(s => `
      <tr>
        <td><strong>${esc(s.name)}</strong></td>
        <td>${esc(s.email) || '-'}</td>
        <td>${esc(s.phone) || '-'}</td>
        <td>${s.customer_count || 0}</td>
        <td>${s.is_active ? '<span class="badge badge-green">在职</span>' : '<span class="badge badge-gray">停用</span>'}</td>
        <td style="display:flex; gap:0.35rem; flex-wrap:wrap">
          <button class="btn-small" onclick="editSalesperson(${s.id}, '${escJs(s.name)}', '${escJs(s.email)}', '${escJs(s.phone)}')">编辑</button>
          <button class="btn-small" onclick="toggleActive(${s.id}, ${s.is_active ? 0 : 1})">${s.is_active ? '停用' : '启用'}</button>
          <button class="btn-small btn-danger" onclick="deleteSalesperson(${s.id}, '${escJs(s.name)}')">删除</button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">加载异常</td></tr>';
  }
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
  document.getElementById('spModal').style.display = 'flex';
};

window.editSalesperson = function (id, name, email, phone) {
  document.getElementById('modalTitle').textContent = '编辑销售';
  document.getElementById('spId').value = id;
  document.getElementById('spName').value = name;
  document.getElementById('spEmail').value = email === '-' ? '' : email;
  document.getElementById('spPhone').value = phone === '-' ? '' : phone;
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

document.addEventListener('DOMContentLoaded', loadTable);
