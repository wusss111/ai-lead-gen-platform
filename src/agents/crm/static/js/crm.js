// crm.js — Customer list/filter/search
import { apiFetch } from '/static/js/api.js';
import { badgeForRecommendation, badgeForReview, badgeForEmailStatus, showToast } from '/static/js/utils.js';

let searchTimer = null;
let currentPage = 1;

function debounceSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentPage = 1; loadCustomers(); }, 350);
}
window.debounceSearch = debounceSearch;

async function loadCustomers() {
  const params = new URLSearchParams();
  const search = document.getElementById('searchInput').value.trim();
  const rec = document.getElementById('filterRec').value;
  const review = document.getElementById('filterReview').value;
  const sort = document.getElementById('sortBy').value;

  if (search) params.set('search', search);
  if (rec) params.set('deal_recommendation', rec);
  if (review) params.set('review_flag', review);
  params.set('sort', sort);
  params.set('page', currentPage);
  params.set('page_size', '20');

  try {
    const r = await apiFetch('/crm/api/customers?' + params.toString());
    if (!r.ok) { showToast('加载失败: ' + r.status, 'error'); return; }
    const data = await r.json();
    renderTable(data.customers);
    renderPagination(data);
    renderStats(data);
  } catch (e) {
    showToast('加载异常: ' + e.message, 'error');
  }
}
window.loadCustomers = loadCustomers;

function renderTable(customers) {
  const tbody = document.getElementById('tableBody');
  if (!customers.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-state"><div class="empty-icon">&#128269;</div><p>无匹配记录</p></td></tr>';
    return;
  }
  tbody.innerHTML = customers.map(c => `
    <tr>
      <td><a href="/crm/${c.id}">${esc(c.company_name) || '-'}</a></td>
      <td>${esc(c.country_region) || '-'}</td>
      <td>${esc(c.contact_name) || '-'}</td>
      <td>${esc(c.contact_email) || '-'}</td>
      <td><strong>${c.overall_score_computed != null ? c.overall_score_computed.toFixed(1) : '-'}</strong></td>
      <td>${badgeForRecommendation(c.deal_recommendation)}</td>
      <td>${badgeForReview(c.manual_review_flag)}</td>
      <td>${badgeForEmailStatus(c.email_status)}</td>
    </tr>
  `).join('');
}

function renderPagination(data) {
  const pg = document.getElementById('pagination');
  if (data.total_pages <= 1) { pg.style.display = 'none'; return; }
  pg.style.display = 'flex';
  let html = '';
  html += `<button ${data.page <= 1 ? 'disabled' : ''} onclick="goPage(${data.page - 1})">上一页</button>`;
  for (let i = 1; i <= data.total_pages; i++) {
    if (i === 1 || i === data.total_pages || Math.abs(i - data.page) <= 2) {
      html += `<button class="${i === data.page ? 'active' : ''}" onclick="goPage(${i})">${i}</button>`;
    } else if (i === 2 && data.page > 4) {
      html += '<button disabled>...</button>';
    } else if (i === data.total_pages - 1 && data.page < data.total_pages - 3) {
      html += '<button disabled>...</button>';
    }
  }
  html += `<button ${data.page >= data.total_pages ? 'disabled' : ''} onclick="goPage(${data.page + 1})">下一页</button>`;
  pg.innerHTML = html;
}

function renderStats(data) {
  document.getElementById('statsRow').style.display = 'flex';
  document.getElementById('statTotal').textContent = data.total;
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

window.goPage = function(p) {
  currentPage = p;
  loadCustomers();
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

window.exportData = async function() {
  const rec = document.getElementById('filterRec').value;
  const params = new URLSearchParams();
  if (rec) params.set('deal_recommendation', rec);

  try {
    const r = await apiFetch('/crm/api/customers/export?' + params.toString());
    if (!r.ok) { showToast('导出失败', 'error'); return; }
    const data = await r.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'customers_export.json'; a.click();
    URL.revokeObjectURL(url);
    showToast('已导出 ' + data.length + ' 条记录', 'info');
  } catch (e) {
    showToast('导出异常: ' + e.message, 'error');
  }
};

// Lazy-init: load on page ready
document.addEventListener('DOMContentLoaded', () => {
  const urlParams = new URLSearchParams(window.location.search);
  const batchId = urlParams.get('batch');
  if (batchId) {
    // Pre-filter by batch if coming from eval completion
    document.getElementById('searchInput').value = '';
    // Load with batch filter via API
    loadCustomersByBatch(batchId);
  } else {
    loadCustomers();
  }
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
    renderTable(data.customers);
    renderStats(data);
    document.getElementById('pagination').style.display = 'none';
  } catch (e) { /* ignore */ }
}
