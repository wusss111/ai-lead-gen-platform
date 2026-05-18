// mail.js — Inquiry email generation, inline editing, send with cross-page persistence
import { apiFetch, apiPost } from '/static/js/api.js';
import { badgeForRecommendation, badgeForEmailStatus, showToast } from '/static/js/utils.js';

// ---- State ----
let searchTimer = null;
let currentJobId = null;
let selectedCustomerIds = new Set();
let _previewEmails = [];  // { customer_id, company_name, contact_email, subject, body, body_html, email_status }
const STORAGE_JOB_KEY = 'mail_current_job';

// ---- DOM helpers ----
function el(id) { return document.getElementById(id); }

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
  checkSmtp();
  loadEmailable();
  restoreState();
});

function saveJobId(jobId) {
  if (jobId) {
    try { localStorage.setItem(STORAGE_JOB_KEY, jobId); } catch(e) {}
  }
}

function clearJobId() {
  try { localStorage.removeItem(STORAGE_JOB_KEY); } catch(e) {}
}

function getStoredJobId() {
  try { return localStorage.getItem(STORAGE_JOB_KEY); } catch(e) { return null; }
}

// ---- Restore state on page load ----
async function restoreState() {
  const savedJobId = getStoredJobId();
  if (!savedJobId) return;

  // Check if there are saved emails in the database
  try {
    const r = await apiFetch('/inquiry-mail/api/emails/saved');
    if (r.ok) {
      const emails = await r.json();
      if (emails && emails.length > 0) {
        currentJobId = savedJobId;
        _previewEmails = emails.map(e => ({
          customer_id: e.id,
          company_name: e.company_name,
          contact_name: e.contact_name,
          contact_email: e.contact_email,
          subject: e.email_subject || '',
          body: e.email_body || '',
          body_html: e.email_body_html || '',
          email_status: e.email_status,
          sent_at: e.email_sent_at,
          tracking_last_opened_at: e.tracking_last_opened_at,
        }));
        renderPreview(_previewEmails);
        el('stepPreview').style.display = '';
        el('previewDesc').textContent = '已恢复 ' + emails.length + ' 封已生成的邮件';
        showToast('已恢复 ' + emails.length + ' 封邮件', 'info');
      }
    }
  } catch(e) {
    console.error('restoreState saved emails error:', e);
  }

  // Check if send job is still active
  try {
    const sr = await apiFetch('/inquiry-mail/api/send-status/' + savedJobId);
    if (sr.ok) {
      const d = await sr.json();
      if (d.status === 'started' || d.status === 'queued' || d.status === 'deferred') {
        el('stepResult').style.display = '';
        el('sendResults').innerHTML = '<p>恢复发送任务追踪...</p>';
        el('sendResultDesc').textContent = '发送进行中';
        pollSend();
        showToast('已恢复发送任务追踪', 'info');
      } else if (d.status === 'finished' && d.result) {
        el('stepResult').style.display = '';
        renderSendResults(d.result);
        el('btnSend').disabled = false;
      }
    }
  } catch(e) {
    console.error('restoreState send status error:', e);
  }
}

// ---- SMTP check ----
async function checkSmtp() {
  const r = await apiFetch('/inquiry-mail/api/smtp-check');
  const d = await r.json();
  const banner = el('smtpBanner');
  if (!d.configured) {
    banner.className = 'smtp-banner warn';
    banner.innerHTML = '<strong>SMTP 未配置</strong> — 邮件生成后不会实际发送。请在 .env 中设置 SMTP_HOST / SMTP_FROM_EMAIL。';
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
  const search = el('searchInput')?.value?.trim() || '';
  const rec = el('filterRec')?.value || '';
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
  const tbody = el('customerTableBody');
  if (!customers.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-cell"><p>无匹配记录</p></td></tr>';
    return;
  }
  tbody.innerHTML = customers.map(c => {
    const sel = selectedCustomerIds.has(c.id);
    const hasEmail = c.email_status === 'generated' || c.email_status === 'sent' || c.email_status === 'failed';
    const actionBtn = hasEmail
      ? `<button class="btn-sm btn-outline" onclick="window.showEmailModal(${c.id})" title="查看/编辑邮件">&#128065; 查看</button>`
      : '<span style="color:var(--text-muted);font-size:0.78rem">-</span>';
    return `
    <tr class="${sel ? 'selected-row' : ''}">
      <td class="col-cb"><input type="checkbox" ${sel ? 'checked' : ''} onchange="window.toggleCustomer(${c.id}, this.checked)" /></td>
      <td>${esc(c.company_name) || '-'}</td>
      <td>${esc(c.contact_name) || '-'}</td>
      <td>${esc(c.contact_email) || '-'}</td>
      <td>${c.overall_score_computed != null ? c.overall_score_computed.toFixed(1) : '-'}</td>
      <td>${badgeForRecommendation(c.deal_recommendation)}</td>
      <td>${badgeForEmailStatus(c.email_status)}</td>
      <td class="col-action">${actionBtn}</td>
    </tr>`;
  }).join('');
  updateSelectCount();
}

// ---- Selection ----
window.toggleCustomer = function(cid, checked) {
  if (checked) selectedCustomerIds.add(cid);
  else selectedCustomerIds.delete(cid);
  updateSelectCount();
};

window.toggleAll = function(cb) {
  document.querySelectorAll('#customerTableBody input[type="checkbox"]').forEach(b => {
    b.checked = cb.checked;
    const cid = parseInt(b.getAttribute('onchange')?.match(/(\d+)/)?.[1]);
    if (cid) {
      if (cb.checked) selectedCustomerIds.add(cid);
      else selectedCustomerIds.delete(cid);
    }
  });
  updateSelectCount();
};

window.selectAll = function() {
  document.querySelectorAll('#customerTableBody input[type="checkbox"]').forEach(b => {
    b.checked = true;
    const cid = parseInt(b.getAttribute('onchange')?.match(/(\d+)/)?.[1]);
    if (cid) selectedCustomerIds.add(cid);
  });
  updateSelectCount();
};

window.deselectAll = function() {
  selectedCustomerIds.clear();
  document.querySelectorAll('#customerTableBody input[type="checkbox"]').forEach(b => { b.checked = false; });
  updateSelectCount();
};

function updateSelectCount() {
  const el2 = el('selectCount');
  if (el2) el2.textContent = '已选 ' + selectedCustomerIds.size + ' 位';
}

// ---- Generate ----
window.startGenerate = async function() {
  if (selectedCustomerIds.size === 0) { showToast('请先选择客户', 'error'); return; }

  el('btnGenerate').disabled = true;
  el('stepProgress').style.display = '';
  el('stepPreview').style.display = 'none';
  el('stepResult').style.display = 'none';
  updateProgress(4, '提交生成任务...', '提交');

  const lang = el('langSelect')?.value || 'auto';
  const fd = new FormData();
  fd.append('customer_ids', Array.from(selectedCustomerIds).join(','));
  fd.append('language', lang);

  const r = await apiPost('/inquiry-mail/api/generate', fd);
  if (!r.ok) { showToast('生成失败: ' + r.status, 'error'); el('btnGenerate').disabled = false; return; }
  const data = await r.json();
  currentJobId = data.job_id;
  saveJobId(currentJobId);
  if (window.__trackJob) window.__trackJob(data.job_id, '生成邮件', 'inquiry-mail');
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
      if (window.__removeJob) window.__removeJob(currentJobId);
      updateProgress(100, '生成完成', '完成');
      // Map emails for preview
      _previewEmails = (d.emails || []).map(e => ({
        customer_id: e.customer_id,
        company_name: e.company_name || '',
        contact_name: e.contact_name || '',
        contact_email: e.contact_email || '',
        subject: e.subject || '',
        body: e.body || '',
        body_html: e.body_html || '',
        email_status: 'generated',
      }));
      renderPreview(_previewEmails);
      el('btnGenerate').disabled = false;
      return;
    }
    if (st === 'failed') {
      if (window.__removeJob) window.__removeJob(currentJobId);
      updateProgress(100, '生成失败', '失败');
      el('btnGenerate').disabled = false;
      return;
    }
    setTimeout(poll, 2000);
  };
  poll();
}

function updateProgress(pct, label, phase) {
  const bar = el('progressBar');
  const lbl = el('progressLabel');
  const pctEl = el('progressPct');
  const phaseEl = el('progressPhase');
  if (bar) bar.style.width = pct + '%';
  if (lbl) lbl.textContent = label;
  if (pctEl) pctEl.textContent = pct + '%';
  if (phaseEl) phaseEl.textContent = phase;
}

// ---- Preview with inline editing ----
function renderPreview(emails) {
  const list = el('previewList');
  if (!list) return;
  el('stepPreview').style.display = '';

  list.innerHTML = emails.map((e, i) => {
    const isSent = e.email_status === 'sent';
    const disabled = isSent ? 'disabled' : '';
    const sentBadge = isSent ? '<span class="badge badge-green">已发送</span>' : '';
    const statusClass = isSent ? 'email-sent' : '';
    return `
    <div class="preview-card ${statusClass}" id="previewCard${i}">
      <div class="preview-top">
        <label class="preview-check-label ${isSent ? 'sent-label' : ''}">
          <input type="checkbox" class="preview-check" value="${i}" ${disabled} onchange="window.updateSendBtn()" />
          <strong>${esc(e.company_name || '?')}</strong>
          <span style="color:var(--text-muted);font-size:0.8rem">${esc(e.contact_email || '')}</span>
        </label>
        <div class="preview-badges">${sentBadge}</div>
      </div>

      <div class="preview-field">
        <span class="field-label">主题</span>
        <div class="field-display" id="subjectDisplay${i}" ondblclick="window.startEdit(${i}, 'subject')">${esc(e.subject || '')}</div>
        <div class="field-edit" id="subjectEdit${i}" style="display:none">
          <input type="text" id="subjectInput${i}" class="edit-input" value="${escAttr(e.subject || '')}" />
          <button class="btn-sm btn-outline" onclick="window.saveEdit(${i}, 'subject')">保存</button>
          <button class="btn-sm btn-outline" onclick="window.cancelEdit(${i}, 'subject', '${escAttr(e.subject || '')}')">取消</button>
        </div>
      </div>

      <div class="preview-field">
        <span class="field-label">正文 <small style="font-weight:400">(双击编辑)</small></span>
        <div class="field-display body-preview" id="bodyDisplay${i}" ondblclick="window.startEdit(${i}, 'body')">${e.body_html || esc(e.body || '')}</div>
        <div class="field-edit" id="bodyEdit${i}" style="display:none">
          <textarea id="bodyInput${i}" class="edit-textarea" rows="8">${escHtml(e.body || '')}</textarea>
          <div style="display:flex;gap:0.35rem;margin-top:0.35rem">
            <button class="btn-sm btn-outline" onclick="window.saveEdit(${i}, 'body')">保存修改</button>
            <button class="btn-sm btn-outline" onclick="window.cancelEdit(${i}, 'body', '${escAttr(e.body || '')}')">取消</button>
          </div>
        </div>
      </div>
    </div>`;
  }).join('');
}

// ---- Inline editing ----
window.startEdit = function(index, field) {
  // Don't allow editing sent emails
  if (_previewEmails[index]?.email_status === 'sent') return;
  const display = el(field + 'Display' + index);
  const edit = el(field + 'Edit' + index);
  if (display) display.style.display = 'none';
  if (edit) edit.style.display = '';
  // Focus the input
  const input = el(field + 'Input' + index);
  if (input) setTimeout(() => input.focus(), 50);
};

window.cancelEdit = function(index, field, originalValue) {
  const display = el(field + 'Display' + index);
  const edit = el(field + 'Edit' + index);
  const input = el(field + 'Input' + index);
  if (input) input.value = originalValue || '';
  if (display) display.style.display = '';
  if (edit) edit.style.display = 'none';
};

window.saveEdit = async function(index, field) {
  const email = _previewEmails[index];
  if (!email || email.email_status === 'sent') return;
  if (!email.customer_id) return;

  const input = el(field + 'Input' + index);
  const newValue = input ? input.value.trim() : '';

  // Build form data with both fields (to preserve the other)
  const fd = new FormData();
  if (field === 'subject') {
    fd.append('subject', newValue);
    fd.append('body', email.body || '');
  } else {
    fd.append('subject', email.subject || '');
    fd.append('body', newValue);
  }

  try {
    const r = await apiFetch('/inquiry-mail/api/emails/' + email.customer_id, {
      method: 'PUT',
      body: fd,
    });
    if (!r.ok) {
      const txt = await r.text();
      showToast('保存失败: ' + txt.slice(0, 200), 'error');
      return;
    }
    // Update local state
    if (field === 'subject') {
      email.subject = newValue;
      const display = el('subjectDisplay' + index);
      if (display) display.textContent = newValue;
    } else {
      email.body = newValue;
      const display = el('bodyDisplay' + index);
      if (display) display.innerHTML = esc(newValue);
    }
    // Hide editor
    const display = el(field + 'Display' + index);
    const edit = el(field + 'Edit' + index);
    if (display) display.style.display = '';
    if (edit) edit.style.display = 'none';
    showToast('已保存', 'info');
  } catch(e) {
    console.error('saveEdit error:', e);
    showToast('保存异常: ' + e.message, 'error');
  }
};

window.updateSendBtn = function() {
  // Just a visual update — actual check happens in startSend
};

// ---- Send ----
window.startSend = async function() {
  const checks = document.querySelectorAll('.preview-check:checked');
  const idxs = Array.from(checks).map(cb => parseInt(cb.value));

  if (idxs.length === 0) { showToast('请勾选要发送的邮件', 'error'); return; }

  el('btnSend').disabled = true;
  const respectTz = el('chkTimezone')?.checked ?? true;

  const fd = new FormData();
  fd.append('job_id', currentJobId);
  fd.append('respect_tz', respectTz ? '1' : '0');
  const cids = idxs.map(i => _previewEmails[i]?.customer_id).filter(Boolean);
  fd.append('customer_ids', cids.join(','));

  const r = await apiPost('/inquiry-mail/api/send', fd);
  if (!r.ok) {
    const txt = await r.text();
    showToast('发送失败: ' + txt.slice(0, 200), 'error');
    el('btnSend').disabled = false;
    return;
  }
  const data = await r.json();
  if (window.__trackJob) window.__trackJob(currentJobId + '_send', '发送邮件', 'inquiry-mail');
  showToast('发送任务已提交', 'info');
  el('stepResult').style.display = '';
  el('sendResults').innerHTML = '<p>正在连接发送队列...</p>';
  el('sendResultDesc').textContent = '0 发送, 0 失败';
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
      el('sendResults').innerHTML =
        '<p><strong>发送中</strong> — ' + esc(d.progress.message || '') + '</p>' +
        '<div class="progress-wrap"><div class="progress-bar" style="width:' + pct + '%"></div></div>' +
        '<div style="display:flex;justify-content:space-between;font-size:0.82rem;color:var(--text-muted);margin-top:0.3rem">' +
          '<span>' + current + ' / ' + total + '</span>' +
          '<span>' + pct + '%</span>' +
        '</div>';
      el('sendResultDesc').textContent = '处理中: ' + current + '/' + total;
    }

    if (d.status === 'finished' && d.result) {
      if (window.__removeJob) window.__removeJob(currentJobId + '_send');
      clearJobId();
      renderSendResults(d.result);
      el('btnSend').disabled = false;
      // Update preview emails status
      if (d.result.emails) {
        _previewEmails = d.result.emails.map(e => ({
          customer_id: e.customer_id,
          company_name: e.company_name || '',
          contact_name: e.contact_name || '',
          contact_email: e.contact_email || '',
          subject: e.subject || '',
          body: e.body || '',
          body_html: e.body_html || '',
          email_status: e.email_status || 'sent',
        }));
        renderPreview(_previewEmails);
      }
      return;
    }
    if (d.status === 'failed') {
      if (window.__removeJob) window.__removeJob(currentJobId + '_send');
      clearJobId();
      el('sendResults').innerHTML = '<p style="color:var(--color-danger)">发送任务失败</p>';
      el('sendResultDesc').textContent = '发送失败';
      el('btnSend').disabled = false;
      return;
    }
    setTimeout(poll, 2000);
  };
  poll();
}

function renderSendResults(result) {
  const sent = result.sent || 0;
  const failed = result.failed || 0;
  el('sendResultDesc').textContent = sent + ' 发送, ' + failed + ' 失败';
  const div = el('sendResults');
  div.innerHTML = '<p><strong>发送完成</strong> — 成功 ' + sent + ' 封，失败 ' + failed + ' 封</p>';
}

window.updateSendMode = function() {
  // Visual feedback handled by CSS
};

// ---- Utils ----
function esc(s) {
  if (!s && s !== 0) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function escAttr(s) {
  if (!s && s !== 0) return '';
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/'/g, '&#39;');
}

function escHtml(s) {
  if (!s && s !== 0) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ---- Email Modal (view & edit per customer) ----
let _modalCustomerId = null;

window.showEmailModal = async function(customerId) {
  _modalCustomerId = customerId;

  // First check if we have the email in preview
  const existing = _previewEmails.find(e => e.customer_id === customerId);

  if (existing && existing.subject !== undefined) {
    populateModal(customerId, existing.company_name, existing.contact_email, existing.subject, existing.body, existing.email_status);
    return;
  }

  // Fetch from API
  try {
    const r = await apiFetch('/crm/api/customers/' + customerId);
    if (!r.ok) { showToast('加载失败', 'error'); return; }
    const c = await r.json();
    const hasEmail = c.email_status === 'generated' || c.email_status === 'sent' || c.email_status === 'failed';
    if (!hasEmail) { showToast('该客户尚未生成邮件', 'info'); return; }
    populateModal(customerId, c.company_name, c.contact_email, c.email_subject || '', c.email_body || '', c.email_status);
  } catch(e) {
    console.error('showEmailModal error:', e);
    showToast('加载异常', 'error');
  }
};

function populateModal(customerId, company, email, subject, body, status) {
  el('emailModalTitle').textContent = company || '客户邮件';
  el('emailModalMeta').innerHTML =
    '<span style="color:var(--text-muted)">' + esc(email || '') + '</span>' +
    (status === 'sent' ? ' <span class="badge badge-green">已发送</span>' : status === 'failed' ? ' <span class="badge badge-red">失败</span>' : ' <span class="badge badge-blue">已生成</span>');

  el('emailModalSubjectDisplay').textContent = subject || '(无主题)';
  el('emailModalBodyDisplay').innerHTML = esc(body || '') || '<span style="color:var(--text-muted)">(无正文)</span>';

  // Hide editors
  el('emailModalSubjectEdit').style.display = 'none';
  el('emailModalSubjectDisplay').style.display = '';
  el('emailModalBodyEdit').style.display = 'none';
  el('emailModalBodyDisplay').style.display = '';

  // Show modal
  el('emailModal').style.display = 'flex';
}

window.closeEmailModal = function() {
  el('emailModal').style.display = 'none';
  _modalCustomerId = null;
};

window.editModalField = function(field) {
  if (!_modalCustomerId) return;
  const display = el('emailModal' + field.charAt(0).toUpperCase() + field.slice(1) + 'Display');
  const edit = el('emailModal' + field.charAt(0).toUpperCase() + field.slice(1) + 'Edit');
  const input = el('emailModal' + field.charAt(0).toUpperCase() + field.slice(1) + 'Input');

  if (field === 'subject') {
    input.value = (display.textContent === '(无主题)' ? '' : display.textContent);
  } else {
    // body: get text from display
    input.value = display.textContent === '(无正文)' ? '' : (display.innerText || '');
  }

  display.style.display = 'none';
  edit.style.display = '';
  setTimeout(() => input.focus(), 50);
};

window.saveModalEdit = async function(field) {
  if (!_modalCustomerId) return;
  const input = el('emailModal' + field.charAt(0).toUpperCase() + field.slice(1) + 'Input');
  const newValue = input ? input.value.trim() : '';

  try {
    // Fetch current values to preserve the other field
    const cr = await apiFetch('/crm/api/customers/' + _modalCustomerId);
    if (!cr.ok) { showToast('保存失败', 'error'); return; }
    const c = await cr.json();

    const fd = new FormData();
    if (field === 'subject') {
      fd.append('subject', newValue);
      fd.append('body', c.email_body || '');
    } else {
      fd.append('subject', c.email_subject || '');
      fd.append('body', newValue);
    }

    const r = await apiFetch('/inquiry-mail/api/emails/' + _modalCustomerId, { method: 'PUT', body: fd });
    if (!r.ok) { const txt = await r.text(); showToast('保存失败: ' + txt.slice(0, 200), 'error'); return; }

    // Update display
    const display = el('emailModal' + field.charAt(0).toUpperCase() + field.slice(1) + 'Display');
    const edit = el('emailModal' + field.charAt(0).toUpperCase() + field.slice(1) + 'Edit');
    if (field === 'subject') {
      display.textContent = newValue || '(无主题)';
    } else {
      display.innerHTML = esc(newValue) || '<span style="color:var(--text-muted)">(无正文)</span>';
    }
    display.style.display = '';
    edit.style.display = 'none';

    // Also update preview emails array if it exists
    const pe = _previewEmails.find(e => e.customer_id === _modalCustomerId);
    if (pe) {
      if (field === 'subject') pe.subject = newValue;
      else pe.body = newValue;
    }

    showToast('已保存', 'info');
  } catch(e) {
    console.error('saveModalEdit error:', e);
    showToast('保存异常', 'error');
  }
};

window.cancelModalEdit = function(field) {
  const display = el('emailModal' + field.charAt(0).toUpperCase() + field.slice(1) + 'Display');
  const edit = el('emailModal' + field.charAt(0).toUpperCase() + field.slice(1) + 'Edit');
  display.style.display = '';
  edit.style.display = 'none';
};
