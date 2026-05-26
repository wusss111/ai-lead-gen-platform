// mail.js — Inquiry email generation, draft/confirm flow, batch send
import { apiFetch, apiPost } from '/static/js/api.js';
import { badgeForRecommendation, badgeForEmailStatus, badgeForReadStatus, showToast, emailToggle, emailPick } from '/static/js/utils.js';

// H5: Email dropdown functions (shared with CRM)
window._emailToggle = emailToggle;
window._emailPick = emailPick;

// ---- State ----
let searchTimer = null;
let currentJobId = null;
let selectedCustomerIds = new Set();
let generatedCount = 0;  // how many emails were generated in current job
const STORAGE_JOB_KEY = 'mail_current_job';
let mailPage = 0;
const MAIL_PAGE_SIZE = 50;

// ---- DOM helpers ----
function el(id) { return document.getElementById(id); }

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
  checkSmtp();
  loadSalespersons();
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

  currentJobId = savedJobId;

  // Check if there are saved emails in the database
  try {
    const r = await apiFetch('/inquiry-mail/api/emails/saved');
    if (r.ok) {
      const emails = await r.json();
      if (emails && emails.length > 0) {
        generatedCount = emails.length;
        el('stepPreview').style.display = '';
        el('previewDesc').textContent = '已恢复 ' + emails.length + ' 封邮件（'
          + emails.filter(e => e.email_status === 'draft' || e.email_status === 'generated').length + ' 封草稿, '
          + emails.filter(e => e.email_status === 'confirmed').length + ' 封已确认'
          + '）';
        el('genSummary').innerHTML = buildSummaryHtml(emails);
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
      }
    }
  } catch(e) {
    console.error('restoreState send status error:', e);
  }
}

function buildSummaryHtml(emails) {
  // emails from JSON (just generated) don't have email_status — treat all as draft
  // emails from DB (saved endpoint) have email_status
  const draft = emails.filter(e => !e.email_status || e.email_status === 'draft' || e.email_status === 'generated').length;
  const confirmed = emails.filter(e => e.email_status === 'confirmed').length;
  const sent = emails.filter(e => e.email_status === 'sent').length;
  const failed = emails.filter(e => e.email_status === 'failed').length;
  let parts = [];
  if (draft > 0) parts.push('<span class="summary-stat"><span class="badge badge-yellow">' + draft + ' 草稿</span></span>');
  if (confirmed > 0) parts.push('<span class="summary-stat"><span class="badge badge-blue">' + confirmed + ' 已确认</span></span>');
  if (sent > 0) parts.push('<span class="summary-stat"><span class="badge badge-green">' + sent + ' 已发送</span></span>');
  if (failed > 0) parts.push('<span class="summary-stat"><span class="badge badge-red">' + failed + ' 失败</span></span>');
  return parts.length > 0 ? parts.join(' ') : '<span style="color:var(--text-muted)">无邮件</span>';
}

// ---- SMTP check ----
async function checkSmtp() {
  try {
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
  } catch (e) {
    console.error('SMTP check failed:', e);
  }
}

// ---- Load emailable customers ----
function debounceLoad() {
  clearTimeout(searchTimer);
  mailPage = 0;  // 筛选条件变化时重置到第一页
  searchTimer = setTimeout(loadEmailable, 300);
}
window.debounceLoad = debounceLoad;

async function loadEmailable() {
  try {
    const search = el('searchInput')?.value?.trim() || '';
    const rec = el('filterRec')?.value || '';
    const mailStatus = el('filterMailStatus')?.value || '';
    const readStatus = el('filterReadStatus')?.value || '';
    const spId = el('filterSalesperson')?.value || '';

    // Advanced filters
    const emailEmpty = el('afEmailEmpty')?.value || '';
    const country = el('afCountry')?.value?.trim() || '';
    const minScore = el('afMinScore')?.value || '';
    const role = el('afBuyerSeller')?.value || '';
    const pri = el('afPriority')?.value || '';
    const dq = el('afDataQuality')?.value || '';
    const review = el('afReviewFlag')?.value || '';
    const from = el('afCreatedFrom')?.value || '';
    const to = el('afCreatedTo')?.value || '';

    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (rec) params.set('deal_recommendation', rec);
    if (mailStatus) params.set('email_status', mailStatus);
    if (readStatus) params.set('read_status', readStatus);
    if (spId) params.set('salesperson_id', spId);
    if (emailEmpty) params.set('email_empty', emailEmpty);
    if (country) params.set('country', country);
    if (minScore) params.set('min_score', minScore);
    if (role) params.set('buyer_seller_role', role);
    if (pri) params.set('priority', pri);
    if (dq) params.set('data_quality', dq);
    if (review) params.set('review_flag', review);
    if (from) params.set('created_from', from);
    if (to) params.set('created_to', to);
    params.set('offset', String(mailPage * MAIL_PAGE_SIZE));
    params.set('limit', String(MAIL_PAGE_SIZE));

    updateFilterBadge();

    const r = await apiFetch('/inquiry-mail/api/customers/emailable?' + params.toString());
    if (!r.ok) { showToast('加载客户失败: ' + r.status, 'error'); return; }
    const customers = await r.json();
    renderCustomerTable(customers);
    renderMailPagination();
  } catch (e) {
    console.error('loadEmailable error:', e);
    showToast('加载客户列表异常', 'error');
  }
}
window.loadEmailable = loadEmailable;

// ---- Advanced filter functions ----
window.toggleAdvanced = function() {
  const panel = el('advancedPanel');
  if (panel) panel.style.display = panel.style.display === 'none' ? '' : 'none';
};

window.onAdvancedChange = function() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { mailPage = 0; loadEmailable(); }, 500);
};

window.clearAdvanced = function() {
  const ids = ['afEmailEmpty', 'afCountry', 'afMinScore', 'afBuyerSeller', 'afPriority', 'afDataQuality', 'afReviewFlag', 'afCreatedFrom', 'afCreatedTo'];
  ids.forEach(id => {
    const e = el(id);
    if (e) e.value = '';
  });
  mailPage = 0;
  loadEmailable();
};

function updateFilterBadge() {
  const badge = el('filterBadge');
  if (!badge) return;

  const ids = ['afEmailEmpty', 'afCountry', 'afMinScore', 'afBuyerSeller', 'afPriority', 'afDataQuality', 'afReviewFlag', 'afCreatedFrom', 'afCreatedTo'];
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

async function loadSalespersons() {
  try {
    const r = await apiFetch('/crm/api/salespersons');
    if (!r.ok) return;
    const list = await r.json();
    const sel = el('filterSalesperson');
    if (!sel) return;
    list.forEach(sp => {
      const opt = document.createElement('option');
      opt.value = sp.id;
      opt.textContent = sp.name;
      sel.appendChild(opt);
    });
  } catch(e) { console.error('loadSalespersons error:', e); }
}

function renderMailEmailCell(c) {
  const primary = c.contact_email || '';
  let allEmails = [];
  try { if (c.contact_emails_all) { allEmails = JSON.parse(c.contact_emails_all); } } catch(_) {}
  if (!primary && allEmails.length === 0) return '-';
  if (allEmails.length <= 1) {
    const cls = primary ? '' : ' style="color:var(--text-muted)"';
    return '<span' + cls + '>' + (esc(primary) || '-') + '</span>';
  }
  var items = allEmails.map(function(e, i) {
    var cls = e === primary ? ' email-selected' : '';
    var star = i === 0 ? ' <small style=\"color:var(--text-muted)\">推荐</small>' : '';
    return '<div class=\"email-item' + cls + '\" onclick=\"window._mailEmailPick(this,\'' + escAttr(e) + '\')\">' + esc(e) + star + '</div>';
  }).join('');
  return '<div class=\"email-cell\" onclick=\"event.stopPropagation()\">' +
    '<span class=\"email-primary\" title=\"' + escAttr(primary) + '\">' + esc(primary) + '</span>' +
    '<span class=\"email-toggle\" onclick=\"window._emailToggle(this)\">▾</span>' +
    '<div class=\"email-dropdown\">' + items + '</div>' +
    '</div>';
}
window._mailEmailPick = function(itemEl, email) {
  var dd = itemEl.parentElement;
  dd.style.display = 'none';
  if (window.__onEmailSelect) window.__onEmailSelect(email);
};

window.mailPrevPage = function() {
  if (mailPage > 0) { mailPage--; loadEmailable(); }
};
window.mailNextPage = function() {
  mailPage++; loadEmailable();
};

function renderMailPagination() {
  const wrap = el('mailPagination');
  if (!wrap) return;
  wrap.innerHTML = (
    '<button class="btn-outline btn-sm" onclick="window.mailPrevPage()" ' + (mailPage === 0 ? 'disabled' : '') + '>上一页</button>' +
    '<span style="margin:0 1rem;color:var(--text-muted)">第 ' + (mailPage + 1) + ' 页</span>' +
    '<button class="btn-outline btn-sm" onclick="window.mailNextPage()">下一页</button>'
  );
}

function renderCustomerTable(customers) {
  const tbody = el('customerTableBody');
  if (!customers.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty-cell"><p>无匹配记录</p></td></tr>';
    return;
  }
  tbody.innerHTML = customers.map(c => {
    const sel = selectedCustomerIds.has(c.id);
    const hasEmail = c.email_status && c.email_status !== 'none';
    const actionBtn = hasEmail
      ? `<button class="btn-sm btn-outline" onclick="window.showEmailModal(${c.id})" title="查看/编辑邮件">&#128065; 查看</button>`
      : '<span style="color:var(--text-muted);font-size:0.78rem">-</span>';
    const spName = c.salesperson_name || '';
    return `
    <tr class="${sel ? 'selected-row' : ''}">
      <td class="col-cb"><input type="checkbox" ${sel ? 'checked' : ''} onchange="window.toggleCustomer(${c.id}, this.checked)" /></td>
      <td title="${escAttr(c.company_name || '')}">${esc(c.company_name) || '-'}</td>
      <td title="${escAttr(c.contact_name || '')}">${esc(c.contact_name) || '-'}</td>
      <td>${renderMailEmailCell(c)}</td>
      <td>${c.overall_score_computed != null ? c.overall_score_computed.toFixed(1) : '-'}</td>
      <td>${badgeForRecommendation(c.deal_recommendation)}</td>
      <td>${badgeForEmailStatus(c.email_status)}</td>
      <td class="col-sp" title="${escAttr(spName)}">${esc(spName) || '<span style="color:var(--text-muted);font-size:0.78rem">未分配</span>'}</td>
      <td class="col-read">${badgeForReadStatus(c)}</td>
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

      // Count generated emails
      generatedCount = (d.emails || []).length;
      el('stepPreview').style.display = '';
      el('genSummary').innerHTML = buildSummaryHtml(d.emails || []);

      // Reload table to show updated statuses
      await loadEmailable();

      el('btnGenerate').disabled = false;
      showToast('已生成 ' + generatedCount + ' 封邮件（草稿），请查看并确认后发送', 'info');
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

// ---- Batch Confirm ----
window.batchConfirm = async function() {
  if (selectedCustomerIds.size === 0) {
    showToast('请先勾选需要确认的客户（草稿状态）', 'error');
    return;
  }
  const ids = Array.from(selectedCustomerIds).join(',');
  const fd = new FormData();
  fd.append('customer_ids', ids);

  try {
    const r = await apiPost('/inquiry-mail/api/emails/confirm', fd);
    if (!r.ok) {
      const txt = await r.text();
      showToast('确认失败: ' + txt.slice(0, 200), 'error');
      return;
    }
    const data = await r.json();
    if (data.confirmed_count > 0) {
      showToast('已确认 ' + data.confirmed_count + ' 封邮件', 'info');
      await loadEmailable();
      // Update summary if visible
      await refreshSummary();
    } else {
      showToast('没有可确认的邮件（仅草稿状态的邮件可以确认）', 'warn');
    }
  } catch(e) {
    console.error('batchConfirm error:', e);
    showToast('确认异常: ' + e.message, 'error');
  }
};

// ---- Batch Send ----
window.batchSend = async function() {
  if (selectedCustomerIds.size === 0) {
    showToast('请先勾选已确认的邮件', 'error');
    return;
  }

  const respectTz = el('chkTimezone')?.checked ?? true;
  const sendJobId = currentJobId || crypto.randomUUID();

  const fd = new FormData();
  fd.append('job_id', sendJobId);
  fd.append('respect_tz', respectTz ? '1' : '0');
  fd.append('customer_ids', Array.from(selectedCustomerIds).join(','));

  try {
    const r = await apiPost('/inquiry-mail/api/send', fd);
    if (!r.ok) {
      const txt = await r.text();
      showToast('发送失败: ' + txt.slice(0, 200), 'error');
      return;
    }
    const data = await r.json();
    if (window.__trackJob) window.__trackJob(sendJobId + '_send', '发送邮件', 'inquiry-mail');
    showToast('发送任务已提交', 'info');
    el('stepResult').style.display = '';
    el('sendResults').innerHTML = '<p>正在连接发送队列...</p>';
    el('sendResultDesc').textContent = '0 发送, 0 失败';
    pollSend(sendJobId);
  } catch(e) {
    console.error('batchSend error:', e);
    showToast('发送异常: ' + e.message, 'error');
  }
};

async function refreshSummary() {
  try {
    const r = await apiFetch('/inquiry-mail/api/emails/saved');
    if (r.ok) {
      const emails = await r.json();
      el('genSummary').innerHTML = buildSummaryHtml(emails);
    }
  } catch(e) {}
}

// ---- Send polling ----
async function pollSend(jobId) {
  jobId = jobId || currentJobId;
  const poll = async () => {
    const r = await apiFetch('/inquiry-mail/api/send-status/' + jobId);
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
      if (window.__removeJob) window.__removeJob(jobId + '_send');
      clearJobId();
      renderSendResults(d.result);
      await loadEmailable();
      await refreshSummary();
      return;
    }
    if (d.status === 'failed') {
      if (window.__removeJob) window.__removeJob(jobId + '_send');
      clearJobId();
      el('sendResults').innerHTML = '<p style="color:var(--color-danger)">发送任务失败</p>';
      el('sendResultDesc').textContent = '发送失败';
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

// ---- Email Modal (view + edit + confirm) ----
let _modalCustomerId = null;

window.showEmailModal = async function(customerId) {
  _modalCustomerId = customerId;

  // Fetch from API for fresh data
  try {
    const r = await apiFetch('/crm/api/customers/' + customerId);
    if (!r.ok) { showToast('加载失败', 'error'); return; }
    const c = await r.json();
    const hasEmail = c.email_status === 'draft' || c.email_status === 'generated' || c.email_status === 'confirmed' || c.email_status === 'sent' || c.email_status === 'failed';
    if (!hasEmail) { showToast('该客户尚未生成邮件', 'info'); return; }
    populateModal(c);
  } catch(e) {
    console.error('showEmailModal error:', e);
    showToast('加载异常', 'error');
  }
};

function populateModal(c) {
  el('emailModalTitle').textContent = c.company_name || '客户邮件';
  el('emailModalMeta').innerHTML =
    '<span style="color:var(--text-muted)">' + esc(c.contact_email || '') + '</span>' +
    ' ' + badgeForEmailStatus(c.email_status || '');

  el('emailModalSubjectDisplay').textContent = c.email_subject || '(无主题)';
  el('emailModalBodyDisplay').innerHTML = esc(c.email_body || '') || '<span style="color:var(--text-muted)">(无正文)</span>';

  // Hide editors
  el('emailModalSubjectEdit').style.display = 'none';
  el('emailModalSubjectDisplay').style.display = '';
  el('emailModalBodyEdit').style.display = 'none';
  el('emailModalBodyDisplay').style.display = '';

  // Show/hide confirm button based on status
  const canConfirm = c.email_status === 'draft' || c.email_status === 'generated';
  el('btnModalConfirm').style.display = canConfirm ? '' : 'none';
  el('emailModalStatus').textContent = '';

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

    const display = el('emailModal' + field.charAt(0).toUpperCase() + field.slice(1) + 'Display');
    const edit = el('emailModal' + field.charAt(0).toUpperCase() + field.slice(1) + 'Edit');
    if (field === 'subject') {
      display.textContent = newValue || '(无主题)';
    } else {
      display.innerHTML = esc(newValue) || '<span style="color:var(--text-muted)">(无正文)</span>';
    }
    display.style.display = '';
    edit.style.display = 'none';

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

// ---- Confirm from Modal ----
window.confirmFromModal = async function() {
  if (!_modalCustomerId) return;

  const fd = new FormData();
  fd.append('customer_ids', String(_modalCustomerId));

  try {
    const r = await apiPost('/inquiry-mail/api/emails/confirm', fd);
    if (!r.ok) {
      const txt = await r.text();
      showToast('确认失败: ' + txt.slice(0, 200), 'error');
      return;
    }
    const data = await r.json();
    if (data.confirmed_count > 0) {
      showToast('已确认，可以发送了', 'info');
      el('btnModalConfirm').style.display = 'none';
      el('emailModalStatus').innerHTML = '<span class="badge badge-blue">已确认</span>';
      // Reload table
      await loadEmailable();
      await refreshSummary();
    }
  } catch(e) {
    console.error('confirmFromModal error:', e);
    showToast('确认异常: ' + e.message, 'error');
  }
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
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
