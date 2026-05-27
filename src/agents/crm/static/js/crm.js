// crm.js — Customer list with batch assignment + smart search + read tracking
import { apiFetch } from '/static/js/api.js';
import { badgeForRecommendation, badgeForReview, badgeForEmailStatus, badgeForReadStatus, showToast } from '/static/js/utils.js';

// ==================== State ====================
let searchTimer = null;
let currentPage = 1;
let salespersonCache = [];
let currentCustomers = [];
let selectedIds = new Set();
let statsCache = { total: 0, highIntent: 0, review: 0, unassigned: 0 };

// ==================== Utils ====================
function esc(s) {
  if (!s && s !== 0) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function escAttr(s) {
  if (!s && s !== 0) return '';
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function badgeForRole(role) {
  const map = { buyer: ['badge-green', '买方'], seller: ['badge-yellow', '卖方'], both: ['badge-blue', '兼营'], unclear: ['badge-gray', '不明'] };
  const d = map[role] || ['badge-gray', role || '-'];
  return '<span class="badge ' + d[0] + '">' + d[1] + '</span>';
}

function fmtTime(ts) {
  if (!ts) return '-';
  // Format: "2026-05-18 11:47:02" → "05-18 11:47"
  const m = String(ts).match(/(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})/);
  if (m) return m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5];
  return String(ts).slice(0, 16);
}

// ==================== Search / Load ====================
window.debounceSearch = function () {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentPage = 1; loadCustomers(); }, 350);
};

window.reloadCustomers = function () {
  currentPage = 1;
  loadCustomers();
};

async function loadCustomers() {
  const params = new URLSearchParams();
  const search = el('searchInput')?.value?.trim() || '';
  const rec = el('filterRec')?.value || '';
  const review = el('filterReview')?.value || '';
  const sort = el('sortBy')?.value || '-created_at';
  const sp = el('filterSalesperson')?.value || el('afSalesperson')?.value || '';

  if (search) params.set('search', search);
  if (rec) params.set('deal_recommendation', rec);
  if (review) params.set('review_flag', review);
  if (sp) params.set('salesperson_id', sp);

  // Advanced filters
  const country = el('afCountry')?.value?.trim() || '';
  const minScore = el('afMinScore')?.value || '';
  const emailSt = el('afEmailStatus')?.value || '';
  const role = el('afBuyerSeller')?.value || '';
  const dq = el('afDataQuality')?.value || '';
  const emailEmpty = el('afEmailEmpty')?.value || '';
  const from = el('afCreatedFrom')?.value || '';
  const to = el('afCreatedTo')?.value || '';

  if (country) params.set('country', country);
  if (minScore) params.set('min_score', minScore);
  if (emailSt) params.set('email_status', emailSt);
  if (role) params.set('buyer_seller_role', role);
  if (dq) params.set('data_quality', dq);
  if (emailEmpty) params.set('email_empty', '1');
  if (from) params.set('created_from', from);
  if (to) params.set('created_to', to);

  params.set('sort', sort);
  params.set('page', currentPage);
  params.set('page_size', '50');

  updateFilterBadge();

  try {
    const r = await apiFetch('/crm/api/customers?' + params.toString());
    if (!r.ok) { showToast('加载失败 (HTTP ' + r.status + ')', 'error'); return; }
    const data = await r.json();
    currentCustomers = data.customers || [];
    renderTable(currentCustomers);
    renderPagination(data);
    updateStats(data.total || 0);
    updateBatchUI();

    // Also refresh stats
    loadStats();
  } catch (e) {
    console.error('loadCustomers error:', e);
    showToast('加载异常: ' + e.message, 'error');
  }
}
window.loadCustomers = loadCustomers;

// ==================== Stats ====================
async function loadStats() {
  try {
    // total + high intent
    const r1 = await apiFetch('/crm/api/customers?page_size=1&deal_recommendation=high_intent');
    if (r1.ok) { const d = await r1.json(); statsCache.highIntent = d.total || 0; }

    // needs review
    const r2 = await apiFetch('/crm/api/customers?page_size=1&review_flag=YES');
    if (r2.ok) { const d = await r2.json(); statsCache.review = d.total || 0; }

    // unassigned: customers with no salesperson assigned
    const r3 = await apiFetch('/crm/api/customers?page_size=1&salesperson_id=unassigned');
    if (r3.ok) { const d = await r3.json(); statsCache.unassigned = d.total || 0; }

    renderStats();
  } catch (e) {
    console.error('loadStats error:', e);
  }
}

function updateStats(total) {
  statsCache.total = total;
  el('statTotal').textContent = total;
}

function renderStats() {
  el('statTotal').textContent = statsCache.total;
  el('statHighIntent').textContent = statsCache.highIntent;
  el('statReview').textContent = statsCache.review;
  el('statUnassigned').textContent = statsCache.unassigned;
  el('statsRow').style.display = 'grid';
}

// ==================== Table ====================
function renderTable(customers) {
  const tbody = el('tableBody');
  if (!customers.length) {
    tbody.innerHTML = `<tr><td colspan="12" class="empty-cell">
      <div class="empty-icon">&#128269;</div><p>无匹配记录</p><p style="font-size:0.78rem;margin-top:0.25rem">尝试调整筛选条件或<a href="/customer-eval/">导入新客户</a></p>
    </td></tr>`;
    return;
  }

  tbody.innerHTML = customers.map(c => {
    const sel = selectedIds.has(c.id);
    const rowClass = sel ? ' class="selected-row"' : '';
    return `
    <tr${rowClass}>
      <td class="col-cb"><input type="checkbox" class="cb-customer" ${sel ? 'checked' : ''} onchange="toggleCustomer(${c.id}, this.checked)" /></td>
      <td class="col-company"><a href="/crm/${c.id}" title="${escAttr(c.company_name || '')}">${esc(c.company_name) || '-'}</a></td>
      <td class="col-country" title="${escAttr(c.country_region || '')}"><span class="country-flag">${esc(c.country_region) || '-'}</span></td>
      <td class="col-contact" title="${escAttr(c.contact_name || '')}">${esc(c.contact_name) || '-'}</td>
      <td class="col-phone" style="font-family:var(--font-mono);font-size:0.78rem">
        <span class="truncate-cell" title="${escAttr(c.contact_phone || '')}" onclick="this.classList.toggle('expanded')">${esc(c.contact_phone) || '-'}</span>
      </td>
      <td class="col-email" style="font-family:var(--font-mono);font-size:0.78rem">
        <span class="truncate-cell" title="${escAttr(c.contact_email || '')}" onclick="this.classList.toggle('expanded')">${esc(c.contact_email) || '-'}</span>
      </td>
      <td class="col-score"><strong>${c.overall_score_computed != null ? c.overall_score_computed.toFixed(1) : '-'}</strong></td>
      <td class="col-rec">${badgeForRecommendation(c.deal_recommendation)}</td>
      <td class="col-review">${badgeForReview(c.manual_review_flag)}</td>
      <td class="col-role">${badgeForRole(c.buyer_seller_role)}</td>
      <td class="col-sp">
        <select class="sp-inline" onchange="quickAssign(${c.id}, this.value)" onclick="event.stopPropagation()">
          <option value="">未分配</option>
          ${salespersonCache.map(s => `<option value="${s.id}" ${s.id === c.assigned_salesperson_id ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}
        </select>
      </td>
      <td class="col-time">${fmtTime(c.created_at)}</td>
    </tr>`;
  }).join('');

  // Sync checkbox in table header with batch card
  syncHeaderCheckboxes();
  updateBatchUI();
}

function syncHeaderCheckboxes() {
  const allChecked = currentCustomers.length > 0 && currentCustomers.every(c => selectedIds.has(c.id));
  const someChecked = currentCustomers.some(c => selectedIds.has(c.id));
  [el('checkAll'), el('checkAll2')].forEach(cb => {
    if (!cb) return;
    cb.checked = allChecked;
    cb.indeterminate = !allChecked && someChecked;
  });
}

// ==================== Selection ====================
window.toggleCustomer = function (cid, checked) {
  if (checked) selectedIds.add(cid);
  else selectedIds.delete(cid);
  updateBatchUI();
};

window.toggleAll = function (checked) {
  if (checked) currentCustomers.forEach(c => selectedIds.add(c.id));
  else currentCustomers.forEach(c => selectedIds.delete(c.id));
  // Update all row checkboxes
  document.querySelectorAll('.cb-customer').forEach(cb => { cb.checked = checked; });
  syncHeaderCheckboxes();
  updateBatchUI();
};

window.selectAll = function () {
  currentCustomers.forEach(c => selectedIds.add(c.id));
  document.querySelectorAll('.cb-customer').forEach(cb => { cb.checked = true; });
  [el('checkAll'), el('checkAll2')].forEach(cb => { if (cb) cb.checked = true; });
  updateBatchUI();
};

window.deselectAll = function () {
  currentCustomers.forEach(c => selectedIds.delete(c.id));
  selectedIds.clear();
  document.querySelectorAll('.cb-customer').forEach(cb => { cb.checked = false; });
  [el('checkAll'), el('checkAll2')].forEach(cb => { if (cb) { cb.checked = false; cb.indeterminate = false; } });
  updateBatchUI();
};

function updateBatchUI() {
  const cnt = el('batchCount');
  const hint = el('batchHint');
  const batchSel = el('batchSalesperson');
  const checkAll = el('checkAll');
  const checkAll2 = el('checkAll2');

  const n = selectedIds.size;
  if (cnt) cnt.textContent = n;

  if (n > 0) {
    if (hint) hint.style.display = 'none';
    if (batchSel) batchSel.style.opacity = '1';
  } else {
    if (hint) hint.style.display = '';
    if (batchSel) batchSel.style.opacity = '0.6';
  }

  if (currentCustomers.length > 0) {
    const allSel = currentCustomers.every(c => selectedIds.has(c.id));
    const someSel = currentCustomers.some(c => selectedIds.has(c.id));
    [checkAll, checkAll2].forEach(cb => {
      if (!cb) return;
      cb.checked = allSel;
      cb.indeterminate = !allSel && someSel;
    });
  }
}

// ==================== Quick Assign (single row) ====================
window.quickAssign = async function (customerId, salespersonId) {
  const fd = new FormData();
  fd.set('salesperson_id', salespersonId || '');
  try {
    const r = await apiFetch('/crm/api/customers/' + customerId + '/assign', { method: 'PUT', body: fd });
    if (r.ok) {
      showToast(salespersonId ? '分配成功' : '已取消分配', 'info');
      // Update the in-memory customer data
      const cust = currentCustomers.find(c => c.id === customerId);
      if (cust) {
        cust.assigned_salesperson_id = salespersonId ? parseInt(salespersonId) : null;
        const sp = salespersonCache.find(s => s.id === (salespersonId ? parseInt(salespersonId) : -1));
        cust.salesperson_name = sp ? sp.name : null;
      }
      // Refresh stats
      loadStats();
    } else {
      showToast('分配失败 (HTTP ' + r.status + ')', 'error');
    }
  } catch (e) {
    console.error('quickAssign error:', e);
    showToast('分配异常: ' + e.message, 'error');
  }
};

// ==================== Batch Assign ====================
window.batchAssign = async function (salespersonId) {
  if (!salespersonId) return;
  const ids = Array.from(selectedIds);
  if (!ids.length) {
    showToast('请先勾选客户', 'error');
    // Reset dropdown
    const sel = el('batchSalesperson');
    if (sel) sel.value = '';
    return;
  }

  const statusEl = el('batchStatus');
  if (statusEl) { statusEl.textContent = '分配中...'; statusEl.style.color = 'var(--text-muted)'; }

  try {
    const r = await apiFetch('/crm/api/customers/batch-assign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_ids: ids, salesperson_id: parseInt(salespersonId) }),
    });
    if (r.ok) {
      const data = await r.json();
      const spName = salespersonCache.find(s => s.id === parseInt(salespersonId))?.name || '';
      showToast('已将 ' + data.assigned_count + ' 位客户分配给 ' + spName, 'info');
      selectedIds.clear();
      const sel = el('batchSalesperson');
      if (sel) sel.value = '';
      loadCustomers();
    } else {
      const txt = await r.text();
      showToast('分配失败: ' + txt.slice(0, 200), 'error');
    }
  } catch (e) {
    console.error('batchAssign error:', e);
    showToast('分配异常: ' + e.message, 'error');
  }
  if (statusEl) statusEl.textContent = '';
};

// ==================== Pagination ====================
function renderPagination(data) {
  const pg = el('pagination');
  if (!pg) return;
  if (data.total_pages <= 1) { pg.style.display = 'none'; return; }
  pg.style.display = 'flex';

  let html = '';
  html += `<button ${data.page <= 1 ? 'disabled' : ''} onclick="goPage(${data.page - 1})">&#8249; 上一页</button>`;

  const total = data.total_pages;
  const curr = data.page;

  // First page
  html += `<button class="${curr === 1 ? 'pg-active' : ''}" onclick="goPage(1)">1</button>`;

  if (curr > 4) html += '<button disabled>...</button>';

  // Pages around current
  for (let i = Math.max(2, curr - 1); i <= Math.min(total - 1, curr + 1); i++) {
    if (i === 1 || i === total) continue;
    html += `<button class="${i === curr ? 'pg-active' : ''}" onclick="goPage(${i})">${i}</button>`;
  }

  if (curr < total - 3) html += '<button disabled>...</button>';

  // Last page
  if (total > 1) {
    html += `<button class="${curr === total ? 'pg-active' : ''}" onclick="goPage(${total})">${total}</button>`;
  }

  html += `<button ${data.page >= total ? 'disabled' : ''} onclick="goPage(${data.page + 1})">下一页 &#8250;</button>`;
  pg.innerHTML = html;
}

window.goPage = function (p) {
  currentPage = p;
  loadCustomers();
  window.scrollTo({ top: 300, behavior: 'smooth' });
};

// ==================== Export ====================
window.exportData = async function () {
  const rec = el('filterRec')?.value || '';
  const params = new URLSearchParams();
  if (rec) params.set('deal_recommendation', rec);
  try {
    const r = await apiFetch('/crm/api/customers/export?' + params.toString());
    if (!r.ok) { showToast('导出失败', 'error'); return; }
    const data = await r.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'customers_export.json';
    a.click();
    URL.revokeObjectURL(url);
    showToast('已导出 ' + data.length + ' 条记录', 'info');
  } catch (e) {
    console.error('exportData error:', e);
    showToast('导出异常: ' + e.message, 'error');
  }
};

// ==================== Smart Search ====================
window.showSmartSearch = function () {
  const modal = el('smartSearchModal');
  if (modal) modal.style.display = 'flex';
  setTimeout(() => {
    const ta = el('smartQuery');
    if (ta) ta.focus();
  }, 100);
};

window.closeSmartSearch = function () {
  const modal = el('smartSearchModal');
  if (modal) modal.style.display = 'none';
  const expl = el('smartExplanation');
  if (expl) expl.textContent = '';
};

window.doSmartSearch = async function () {
  const q = el('smartQuery')?.value?.trim();
  if (!q) { showToast('请输入搜索内容', 'error'); return; }

  const btn = el('smartSearchBtn');
  const expl = el('smartExplanation');
  if (btn) { btn.disabled = true; btn.textContent = '搜索中...'; }
  if (expl) expl.textContent = '';

  const fd = new FormData();
  fd.append('q', q);

  try {
    const r = await apiFetch('/crm/api/smart-search', { method: 'POST', body: fd });
    if (!r.ok) {
      const txt = await r.text();
      showToast('搜索失败: ' + txt.slice(0, 200), 'error');
      return;
    }
    const data = await r.json();
    if (expl && data.explanation) expl.textContent = data.explanation;
    if (data.customers && data.customers.length > 0) {
      currentCustomers = data.customers;
      renderTable(data.customers);
      el('pagination').style.display = 'none';
      updateStats(data.count || data.customers.length);
      showToast('找到 ' + data.count + ' 条结果', 'info');
      closeSmartSearch();
    } else {
      showToast('未找到匹配结果，请换个说法试试', 'info');
    }
  } catch (e) {
    console.error('doSmartSearch error:', e);
    showToast('搜索异常: ' + e.message, 'error');
  }
  if (btn) { btn.disabled = false; btn.textContent = '搜索'; }
};

// Allow Enter key in smart search textarea
document.addEventListener('keydown', function (e) {
  if (e.key === 'Enter' && e.ctrlKey) {
    const modal = el('smartSearchModal');
    if (modal && modal.style.display === 'flex') {
      e.preventDefault();
      doSmartSearch();
    }
  }
});

// ==================== Salesperson Data ====================
async function loadSalespersonData() {
  try {
    const r = await apiFetch('/crm/api/salespersons');
    if (!r.ok) {
      console.error('loadSalespersonData: HTTP', r.status);
      return;
    }
    const list = await r.json();
    if (!Array.isArray(list)) {
      console.error('loadSalespersonData: unexpected response', list);
      return;
    }
    salespersonCache = list;
    console.log('Loaded', list.length, 'salespersons');

    // Populate filter dropdowns
    for (const selId of ['filterSalesperson', 'afSalesperson']) {
      const filterSel = el(selId);
      if (filterSel) {
        // Keep default options, append salespersons
        list.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.id;
          opt.textContent = s.name + (s.is_active ? '' : ' (停用)');
          filterSel.appendChild(opt);
        });
      }
    }

    // Populate batch assign dropdown
    const batchSel = el('batchSalesperson');
    if (batchSel) {
      list.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = s.name + (s.is_active ? '' : ' (停用)');
        batchSel.appendChild(opt);
      });
    }
  } catch (e) {
    console.error('loadSalespersonData error:', e);
  }
}

// ==================== Advanced Filters ====================
window.toggleAdvanced = function() {
  const panel = el('advancedPanel');
  if (panel) {
    panel.style.display = panel.style.display === 'none' ? '' : 'none';
  }
};

window.onAdvancedChange = function() {
  // Debounced reload for text/number inputs
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentPage = 1; loadCustomers(); }, 500);
};

window.clearAdvanced = function() {
  const ids = ['afCountry', 'afMinScore', 'afEmailStatus', 'afBuyerSeller', 'afEmailEmpty', 'afDataQuality', 'afCreatedFrom', 'afCreatedTo'];
  ids.forEach(id => {
    const e = el(id);
    if (e) e.value = '';
  });
  currentPage = 1;
  loadCustomers();
};

function updateFilterBadge() {
  const badge = el('filterBadge');
  if (!badge) return;

  const ids = ['afCountry', 'afMinScore', 'afEmailStatus', 'afBuyerSeller', 'afEmailEmpty', 'afDataQuality', 'afCreatedFrom', 'afCreatedTo'];
  let count = 0;
  ids.forEach(id => {
    const e = el(id);
    if (e && e.value && e.value.trim()) count++;
  });

  if (count > 0) {
    badge.style.display = 'inline-flex';
    badge.textContent = count;
  } else {
    badge.style.display = 'none';
  }
}

// ==================== Batch Delete ====================
window.batchDelete = async function() {
  const ids = Array.from(selectedIds);
  if (!ids.length) { showToast('请先勾选要删除的客户', 'error'); return; }
  if (!confirm('确定要删除选中的 ' + ids.length + ' 位客户吗？\n\n此操作不可撤销，客户的邮件追踪数据也会一并删除。')) return;

  const statusEl = el('batchStatus');
  if (statusEl) { statusEl.textContent = '删除中...'; statusEl.style.color = 'var(--color-danger)'; }

  try {
    const r = await apiFetch('/crm/api/customers/batch-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_ids: ids }),
    });
    if (r.ok) {
      const data = await r.json();
      showToast('已删除 ' + data.deleted_count + ' 位客户', 'info');
      selectedIds.clear();
      currentPage = 1;
      loadCustomers();
    } else {
      const txt = await r.text();
      showToast('删除失败: ' + txt.slice(0, 200), 'error');
    }
  } catch(e) {
    console.error('batchDelete error:', e);
    showToast('删除异常: ' + e.message, 'error');
  }
  if (statusEl) { statusEl.textContent = ''; }
};

// ==================== Init ====================
function el(id) {
  return document.getElementById(id);
}

document.addEventListener('DOMContentLoaded', async function () {
  console.log('CRM: DOM ready, loading data...');

  // 1. Load salespersons first (needed for table rendering)
  await loadSalespersonData();

  // 2. Load customers
  const urlParams = new URLSearchParams(window.location.search);
  const batchId = urlParams.get('batch');
  if (batchId) {
    // Filter by batch
    el('searchInput').value = '';
    await loadCustomersByBatch(batchId);
  } else {
    await loadCustomers();
  }
  // 3. Load stats
  loadStats();

  console.log('CRM: Init complete. Cache:', salespersonCache.length, 'salespersons,', currentCustomers.length, 'customers on page');
});

async function loadCustomersByBatch(batchId) {
  const params = new URLSearchParams();
  params.set('batch_id', batchId);
  params.set('sort', '-overall_score_computed');
  params.set('page_size', '200');
  try {
    const r = await apiFetch('/crm/api/customers?' + params.toString());
    if (!r.ok) return;
    const data = await r.json();
    currentCustomers = data.customers || [];
    renderTable(currentCustomers);
    el('pagination').style.display = 'none';
    updateStats(data.total || currentCustomers.length);
  } catch (e) {
    console.error('loadCustomersByBatch error:', e);
  }
}
