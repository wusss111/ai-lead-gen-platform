// eval_main.js — Customer evaluation page logic (v3)
import { apiFetch, apiPost } from '/static/js/api.js';
import { normJobStatus, showToast } from '/static/js/utils.js';

// ---- DOM refs ----
const el = (id) => document.getElementById(id);
const progressCard = el('progressCard');
const progressBar = el('progressBar');
const progressLabel = el('progressLabel');
const progressPct = el('progressPct');
const progressPhase = el('progressPhase');
const resultArea = el('resultArea');
const submitBtn = el('submitBtn');
const batchSizeGroup = el('batchSizeGroup');

let activeTab = 'xlsx';
let _currentPollingJobId = null;

// ---- Tab switching ----
window.switchTab = function (tabName) {
  activeTab = tabName;
  document.querySelectorAll('.eval-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.eval-tab[data-tab="${tabName}"]`).classList.add('active');
  ['tabXlsx', 'tabCsv', 'tabUrl'].forEach(id => {
    const p = el(id); if (p) p.style.display = 'none';
  });
  const panelId = 'tab' + tabName.charAt(0).toUpperCase() + tabName.slice(1);
  const panel = el(panelId);
  if (panel) panel.style.display = '';
  if (batchSizeGroup) batchSizeGroup.style.display = tabName === 'url' ? 'none' : '';
};

document.querySelector('.eval-tabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.eval-tab');
  if (!tab) return;
  switchTab(tab.dataset.tab);
});

// ---- File selection ----
function setupUploadZone(zoneId, inputId, hintId) {
  const zone = el(zoneId);
  const input = el(inputId);
  const hint = el(hintId);
  if (!zone || !input) return;
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    if (e.dataTransfer.files.length) {
      input.files = e.dataTransfer.files;
      if (hint) { hint.textContent = '已选择: ' + input.files[0].name; hint.style.color = 'var(--color-accent)'; }
    }
  });
  input.addEventListener('change', () => {
    if (input.files && input.files[0] && hint) {
      hint.textContent = '已选择: ' + input.files[0].name;
      hint.style.color = 'var(--color-accent)';
    }
  });
}
setupUploadZone('uploadPromptXlsx', 'fileInputXlsx', 'hintXlsx');
setupUploadZone('uploadPromptCsv', 'fileInputCsv', 'hintCsv');

// ---- Submit ----
window.startEvalV2 = async function () {
  if (activeTab === 'url') {
    await submitUrlEval();
  } else {
    await submitFileEval();
  }
};

async function submitFileEval() {
  const fileInput = activeTab === 'csv' ? el('fileInputCsv') : el('fileInputXlsx');
  const file = fileInput && fileInput.files ? fileInput.files[0] : null;
  const label = activeTab === 'csv' ? '.csv' : '.xlsx';
  if (!file) { showToast('请先选择 ' + label + ' 文件', 'error'); return; }

  setLoading(true);
  resultArea.innerHTML = '';
  progressCard.style.display = 'block';
  updateBar(4, '上传文件中...', '上传');

  const fd = new FormData();
  fd.append('file', file);
  if (el('cbDryRun').checked) fd.append('dry_run', 'on');
  if (el('cbNoFetch').checked) fd.append('no_fetch', 'on');
  const bs = el('inputBatchSize').value.trim();
  if (bs) fd.append('batch_size', bs);

  try {
    const r = await apiPost('/customer-eval/api/jobs', fd);
    if (!r.ok) { const txt = await r.text(); showToast('上传失败: ' + txt.slice(0, 200), 'error'); setLoading(false); return; }
    const data = await r.json();
    if (window.__trackJob) window.__trackJob(data.job_id, file.name, 'customer-eval');
    updateBar(6, '已入队，等待 Worker...', '排队');
    await pollJob(data.job_id);
  } catch (err) {
    showToast('请求失败: ' + err.message, 'error');
    setLoading(false);
  }
}

async function submitUrlEval() {
  const url = (el('urlInput').value || '').trim();
  if (!url) { showToast('请输入网站 URL', 'error'); return; }

  setLoading(true);
  resultArea.innerHTML = '';
  progressCard.style.display = 'block';
  updateBar(4, '提交URL评估...', '启动');

  const fd = new FormData();
  fd.append('url', url);
  fd.append('company_name', (el('urlCompanyName').value || '').trim());
  fd.append('country', (el('urlCountry').value || '').trim() || 'US');
  fd.append('target_products', (el('urlProducts').value || '').trim());
  fd.append('notes', (el('urlNotes').value || '').trim());
  if (el('cbDryRun').checked) fd.append('dry_run', 'on');
  if (el('cbNoFetch').checked) fd.append('no_fetch', 'on');

  try {
    const r = await apiPost('/customer-eval/api/url-eval', fd);
    if (!r.ok) { const txt = await r.text(); showToast('提交失败: ' + txt.slice(0, 200), 'error'); setLoading(false); return; }
    const data = await r.json();
    if (window.__trackJob) window.__trackJob(data.job_id, url, 'customer-eval');
    updateBar(6, '已入队，等待 Worker...', '排队');
    await pollJob(data.job_id);
  } catch (err) {
    showToast('请求失败: ' + err.message, 'error');
    setLoading(false);
  }
}

// ---- Polling ----
async function pollJob(jobId) {
  _currentPollingJobId = jobId;
  el('progressActions').style.display = 'flex';
  el('btnPause').disabled = false;
  el('btnCancel').disabled = false;

  let lastSt = null;
  const poll = async () => {
    try {
      const r = await apiFetch('/customer-eval/api/jobs/' + jobId);
      if (!r.ok) { showToast('查询失败: ' + r.status, 'error'); setLoading(false); return; }
      const j = await r.json();
      const st = normJobStatus(j.status);
      updateProgress(j, st);
      if (st !== lastSt) {
        lastSt = st;
        if (st === 'started') showToast('AI 正在评估客户数据...', 'info');
      }
      if (st === 'finished') { updateBar(100, '已完成', '完成'); showResult(j, jobId); return; }
      if (j.progress && j.progress.control === 'pause') {
        updateBar(100, '已暂停 · ' + (j.progress.message || ''), '暂停');
        resultArea.innerHTML = '<div class="result-item warn"><strong>已暂停</strong> — 已处理的数据已保存。可点击下方继续处理剩余行。</div>';
        el('btnPause').disabled = true;
        el('progressActions').style.display = 'none';
        _currentPollingJobId = null;
        setLoading(false);
        if (j.result && j.result.batch_end_exclusive) {
          // Show continue button
          const nextRow = j.result.batch_end_exclusive;
          resultArea.innerHTML += '<div class="result-item" style="margin-top:0.5rem"><button class="btn-submit" onclick="window.continueBatchV2(\'' + jobId + '\')">继续处理（从第 ' + (nextRow + 1) + ' 行开始）</button></div>';
        }
        return;
      }
      if (j.progress && j.progress.control === 'cancel') {
        updateBar(100, '已取消', '取消');
        resultArea.innerHTML = '<div class="result-item error"><strong>已取消</strong> — 已处理的数据已保存。剩余行未处理。</div>';
        el('progressActions').style.display = 'none';
        _currentPollingJobId = null;
        setLoading(false);
        if (window.__removeJob) window.__removeJob(jobId);
        return;
      }
      if (st === 'failed') {
        if (window.__removeJob) window.__removeJob(jobId);
        progressCard.style.display = 'block';
        const err = j.error || '无详细信息';
        resultArea.innerHTML = '<div class="result-item error"><strong>评估失败</strong><br>' + escapeHtml(String(err).slice(0, 500)) + '</div>';
        setLoading(false); return;
      }
    } catch (e) {
      showToast('查询异常: ' + e.message, 'error');
      setLoading(false); return;
    }
    setTimeout(poll, 2000);
  };
  poll();
}

function updateProgress(j, status) {
  const p = j.progress;
  progressCard.style.display = 'block';
  if (!p || typeof p !== 'object') {
    if (status === 'queued') updateBar(6, '排队中...', '排队中');
    else if (status === 'started') updateBar(12, '处理中...', '启动中');
    return;
  }
  const cur = Number(p.current) || 0;
  const tot = Number(p.total) || 0;
  let pct = 0;
  const phase = p.phase || '';
  if (phase === 'write' || phase === 'done') pct = 100;
  else if (tot > 0) pct = Math.min(99, Math.round((100 * cur) / tot));
  updateBar(pct, (p.message || phase) + (tot ? ' · ' + cur + '/' + tot + ' 行' : ''), phaseLabel(phase));
}

function updateBar(pct, label, phase) {
  progressBar.style.width = pct + '%';
  progressLabel.textContent = label;
  progressPct.textContent = pct + '%';
  if (phase) progressPhase.textContent = phase;
}

function phaseLabel(p) {
  const map = { fetch: '抓取网站信息', eval: 'AI 评估中', score: '计算评分', write: '写入结果', done: '完成' };
  return map[p] || p || '处理中';
}

function showResult(j, jobId) {
  if (window.__removeJob) window.__removeJob(jobId);
  const r = j.result || {};
  let html = '<div class="result-item success"><strong>评估完成</strong> — ' + (r.rows || '?') + ' 行已处理';
  html += ' &middot; <a href="/customer-eval/api/jobs/' + jobId + '/download">下载 Excel</a>';
  html += ' &middot; <a href="/crm/?batch=' + jobId + '">查看客户资源</a>';
  html += '</div>';
  if (r.has_more) {
    html += '<div class="result-item" style="margin-top:0.5rem">';
    html += '<button class="btn-submit" onclick="window.continueBatchV2(\'' + jobId + '\')" style="font-size:0.85rem;padding:0.5rem">继续下一批 (' + r.total_rows + ' 行总计)</button>';
    html += '</div>';
  }
  resultArea.innerHTML = html;
  setLoading(false);
}

function setLoading(on) {
  if (submitBtn) {
    submitBtn.disabled = on;
    submitBtn.innerHTML = on ? '<span class="spinner" style="display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .6s linear infinite"></span> 处理中...' : '&#9889; 开始评估';
  }
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

// ---- Restore active jobs on page load ----
function restoreActiveJobs() {
  const jobs = window.__getTrackedJobs ? window.__getTrackedJobs() : [];
  if (!jobs.length) return;
  // Find jobs belonging to customer-eval that are still active
  const evalJobs = jobs.filter(j => j.agent === 'customer-eval');
  if (!evalJobs.length) return;

  // Poll the first active job (show progress UI)
  const job = evalJobs[0];
  progressCard.style.display = 'block';
  updateBar(6, '恢复追踪: ' + (job.label || job.jobId), '恢复中');
  setLoading(true);
  pollJob(job.jobId);
}

// Run restoration on page load
document.addEventListener('DOMContentLoaded', () => {
  // Wait a tick for the global tracker to initialize
  setTimeout(restoreActiveJobs, 600);
});

// ---- Pause / Cancel ----
window.pauseJob = async function() {
  if (!_currentPollingJobId) return;
  const jid = _currentPollingJobId;
  el('btnPause').disabled = true;
  el('btnPause').textContent = '暂停中...';
  try {
    const r = await apiFetch('/customer-eval/api/jobs/' + jid + '/pause', { method: 'POST' });
    if (r.ok) {
      showToast('暂停信号已发送，正在保存...', 'info');
      updateBar(50, '正在保存已处理数据...', '暂停');
    } else {
      showToast('暂停失败', 'error');
      el('btnPause').disabled = false;
      el('btnPause').textContent = '⏸ 暂停';
    }
  } catch(e) {
    showToast('暂停请求异常', 'error');
    el('btnPause').disabled = false;
    el('btnPause').textContent = '⏸ 暂停';
  }
};

window.cancelJob = async function() {
  if (!_currentPollingJobId) return;
  if (!confirm('确定要取消当前评估任务吗？\n已处理的数据会自动保存。')) return;
  const jid = _currentPollingJobId;
  el('btnCancel').disabled = true;
  el('btnCancel').textContent = '取消中...';
  try {
    const r = await apiFetch('/customer-eval/api/jobs/' + jid + '/cancel', { method: 'POST' });
    if (r.ok) {
      showToast('取消信号已发送，正在保存...', 'info');
      updateBar(50, '正在保存已处理数据...', '取消');
    } else {
      showToast('取消失败', 'error');
      el('btnCancel').disabled = false;
      el('btnCancel').textContent = '✕ 取消';
    }
  } catch(e) {
    showToast('取消请求异常', 'error');
    el('btnCancel').disabled = false;
    el('btnCancel').textContent = '✕ 取消';
  }
};

// ---- Continue batch ----
window.continueBatchV2 = async function (jobId) {
  resultArea.innerHTML = '';
  updateBar(6, '提交下一批...', '排队');
  const fd = new FormData();
  if (el('cbDryRun').checked) fd.append('dry_run', 'on');
  if (el('cbNoFetch').checked) fd.append('no_fetch', 'on');
  const r = await apiPost('/customer-eval/api/jobs/' + jobId + '/continue', fd);
  if (!r.ok) { showToast('继续失败: ' + r.status, 'error'); return; }
  if (window.__trackJob) window.__trackJob(jobId, '续批', 'customer-eval');
  await pollJob(jobId);
};
