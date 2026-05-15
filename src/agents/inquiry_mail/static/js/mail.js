// mail.js — Inquiry email generation and sending
import { apiFetch, apiPost } from '/static/js/api.js';
import { badgeForRecommendation, badgeForEmailStatus, showToast } from '/static/js/utils.js';

let searchTimer = null;
let currentJobId = null;
let selectedCustomerIds = new Set();
let _previewEmails = [];  // stored for send mapping

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
  checkSmtp();
  loadEmailable();
});

async function checkSmtp() {
  const r = await apiFetch('/inquiry-mail/api/smtp-check');
  const d = await r.json();
  const banner = document.getElementById('smtpBanner');
  if (!d.configured) {
    banner.className = 'smtp-banner warn';
    banner.innerHTML = '<strong>SMTP 未配置</strong> — 邮件生成后不会实际发送。请在 .env 中设置 SMTP_HOST / SMTP_FROM_EMAIL 等变量。';
  } else {
    banner.className = 'smtp-banner ok';
    banner.textContent = 'SMTP 已配置：' + d.host + ' (' + d.from + ')';
  }
  banner.style.display = 'block';
}

// ---- Load emailable customers ----
function debounceLoad() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadEmailable, 300);
}
window.debounceLoad = debounceLoad;

async function loadEmailable() {
  const search = document.getElementById('searchInput').value.trim();
  const rec = document.getElementById('filterRec').value;
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  if (rec) params.set('deal_recommendation', rec);
  params.set('limit', '100');

  const r = await apiFetch('/inquiry-mail/api/customers/emailable?' + params.toString());
  if (!r.ok) return;
  const customers = await r.json();
  renderCustomerTable(customers);
}
window.loadEmailable = loadEmailable;

function renderCustomerTable(customers) {
  const tbody = document.getElementById('customerTableBody');
  if (!customers.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state"><p>暂无有邮箱的客户。请先在客户评估中上传数据。</p></td></tr>';
    return;
  }
  tbody.innerHTML = customers.map(c => `
    <tr>
      <td><input type="checkbox" value="${c.id}" class="customer-check"
            ${selectedCustomerIds.has(c.id) ? 'checked' : ''}
            onchange="toggleCustomer(${c.id}, this.checked)" /></td>
      <td>${esc(c.company_name) || '-'}</td>
      <td>${esc(c.contact_name) || '-'}</td>
      <td>${esc(c.contact_email) || '-'}</td>
      <td><strong>${c.overall_score_computed != null ? c.overall_score_computed.toFixed(1) : '-'}</strong></td>
      <td>${badgeForRecommendation(c.deal_recommendation)}</td>
      <td>${badgeForEmailStatus(c.email_status)}</td>
    </tr>
  `).join('');
  updateSelectCount();
}

window.toggleCustomer = function(id, checked) {
  if (checked) selectedCustomerIds.add(id);
  else selectedCustomerIds.delete(id);
  updateSelectCount();
};

window.toggleAll = function(el) {
  document.querySelectorAll('.customer-check').forEach(cb => {
    cb.checked = el.checked;
    if (el.checked) selectedCustomerIds.add(parseInt(cb.value));
    else selectedCustomerIds.delete(parseInt(cb.value));
  });
  updateSelectCount();
};

window.selectAll = function() {
  document.querySelectorAll('.customer-check').forEach(cb => {
    cb.checked = true;
    selectedCustomerIds.add(parseInt(cb.value));
  });
  document.getElementById('checkAll').checked = true;
  updateSelectCount();
};

window.deselectAll = function() {
  document.querySelectorAll('.customer-check').forEach(cb => {
    cb.checked = false;
  });
  selectedCustomerIds.clear();
  document.getElementById('checkAll').checked = false;
  updateSelectCount();
};

function updateSelectCount() {
  document.getElementById('selectCount').textContent = '已选 ' + selectedCustomerIds.size + ' 位';
}

// ---- Generate ----
window.startGenerate = async function() {
  if (selectedCustomerIds.size === 0) {
    showToast('请先选择客户', 'error');
    return;
  }

  document.getElementById('stepProgress').style.display = 'block';
  document.getElementById('btnGenerate').disabled = true;
  updateProgress(4, '提交生成任务...', '提交');

  const lang = document.getElementById('langSelect')?.value || 'auto';
  const fd = new FormData();
  fd.append('customer_ids', Array.from(selectedCustomerIds).join(','));
  fd.append('language', lang);

  const r = await apiPost('/inquiry-mail/api/generate', fd);
  if (!r.ok) { showToast('生成失败: ' + r.status, 'error'); return; }
  const data = await r.json();
  currentJobId = data.job_id;
  updateProgress(6, '已入队...', '排队');
  pollGenerate();
};

async function pollGenerate() {
  const poll = async () => {
    const r = await apiFetch('/inquiry-mail/api/emails/' + currentJobId);
    const d = await r.json();
    const p = d.progress || {};
    const st = d.rq_status;

    let pct = 6;
    if (p.current && p.total) pct = Math.min(98, Math.round(100 * p.current / p.total));
    updateProgress(pct, p.message || st, '生成中...');

    if (st === 'finished') {
      updateProgress(100, '生成完成', '完成');
      renderPreview(d.emails);
      document.getElementById('btnGenerate').disabled = false;
      return;
    }
    if (st === 'failed') {
      updateProgress(100, '生成失败', '失败');
      document.getElementById('btnGenerate').disabled = false;
      return;
    }
    setTimeout(poll, 2000);
  };
  poll();
}

function updateProgress(pct, label, phase) {
  document.getElementById('progressBar').style.width = pct + '%';
  document.getElementById('progressLabel').textContent = label;
  document.getElementById('progressPct').textContent = pct + '%';
  document.getElementById('progressPhase').textContent = phase;
}

// ---- Preview ----
function renderPreview(emails) {
  _previewEmails = emails;
  document.getElementById('stepPreview').style.display = 'block';
  const list = document.getElementById('previewList');
  list.innerHTML = emails.map((e, i) => `
    <div class="email-preview ${e.skip ? 'skipped' : ''}">
      <div class="email-preview-header">
        <input type="checkbox" class="preview-check" value="${i}" ${e.skip ? '' : 'checked'} />
        <strong>${esc(e.company_name)}</strong>
        <span style="font-size:0.8rem;color:var(--pico-muted-color)">${esc(e.contact_email)}</span>
        ${e.skip ? '<span class="badge badge-gray">跳过: ' + esc(e.skip_reason) + '</span>' : badgeForRecommendation(e.deal_recommendation)}
        ${e.send_success ? '<span class="send-ok">已发送</span>' : ''}
        ${e.send_error && !e.send_success ? '<span class="send-fail">发送失败</span>' : ''}
      </div>
      ${!e.skip ? `
        <div style="margin-bottom:0.4rem"><strong>主题：</strong>${esc(e.subject)}</div>
        <div class="email-preview-body">${esc(e.body_text)}</div>
      ` : ''}
    </div>
  `).join('');
}

// ---- Send ----
window.startSend = async function() {
  const checks = document.querySelectorAll('.preview-check:checked');
  const idxs = Array.from(checks).map(cb => parseInt(cb.value));

  if (idxs.length === 0) { showToast('请勾选要发送的邮件', 'error'); return; }

  document.getElementById('btnSend').disabled = true;

  const respectTz = document.getElementById('chkTimezone')?.checked ?? true;

  const fd = new FormData();
  fd.append('job_id', currentJobId);
  fd.append('respect_tz', respectTz ? '1' : '0');
  const cids = idxs.map(i => _previewEmails[i]?.customer_id).filter(Boolean);
  fd.append('customer_ids', cids.join(','));

  const r = await apiPost('/inquiry-mail/api/send', fd);
  if (!r.ok) {
    const txt = await r.text();
    showToast('发送失败: ' + txt.slice(0, 200), 'error');
    document.getElementById('btnSend').disabled = false;
    return;
  }
  const data = await r.json();
  showToast('发送任务已提交', 'info');
  document.getElementById('stepResult').style.display = 'block';
  document.getElementById('sendResults').innerHTML = '<p>正在连接发送队列...</p>';
  document.getElementById('sendResultDesc').textContent = '0 发送, 0 失败';
  pollSend();
};

async function pollSend() {
  const poll = async () => {
    const r = await apiFetch('/inquiry-mail/api/send-status/' + currentJobId);
    const d = await r.json();

    if (d.progress) {
      const current = d.progress.current || 0;
      const total = d.progress.total || 1;
      const pct = Math.min(98, Math.round(100 * current / total));
      document.getElementById('sendResults').innerHTML =
        '<p><strong>发送中</strong> — ' + esc(d.progress.message) + '</p>' +
        '<div class="progress-wrap"><div class="progress-bar" style="width:' + pct + '%"></div></div>' +
        '<div style="display:flex;justify-content:space-between;font-size:0.85rem;color:var(--pico-muted-color);margin-top:0.3rem">' +
          '<span>' + current + ' / ' + total + '</span>' +
          '<span>' + pct + '%</span>' +
        '</div>';
      document.getElementById('sendResultDesc').textContent = '处理中: ' + current + '/' + total;
    }

    if (d.status === 'finished' && d.result) {
      renderSendResults(d.result);
      document.getElementById('btnSend').disabled = false;
      return;
    }
    if (d.status === 'failed') {
      document.getElementById('sendResults').innerHTML = '<p style="color:var(--pico-del-color)">发送任务失败</p>';
      document.getElementById('sendResultDesc').textContent = '发送失败';
      document.getElementById('btnSend').disabled = false;
      return;
    }
    setTimeout(poll, 2000);
  };
  poll();
}

function renderSendResults(result) {
  const sent = result.sent || 0;
  const failed = result.failed || 0;
  document.getElementById('sendResultDesc').textContent = sent + ' 发送, ' + failed + ' 失败';
  const div = document.getElementById('sendResults');
  div.innerHTML = `
    <p><strong>发送完成</strong> — 成功 ${sent} 封，失败 ${failed} 封</p>
  `;
  // Refresh preview with send results
  if (result.emails) renderPreview(result.emails);
}

window.updateSendMode = function() {
  const el = document.getElementById('chkTimezone');
  const label = el?.parentElement;
  if (label) {
    label.style.opacity = el.checked ? '1' : '0.5';
  }
};

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}
