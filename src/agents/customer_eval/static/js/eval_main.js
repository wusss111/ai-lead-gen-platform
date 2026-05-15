// eval_main.js — Customer evaluation page logic
import { apiFetch, apiPost } from '/static/js/api.js';
import { ProgressBar, normJobStatus, showToast } from '/static/js/utils.js';

// ---- File selection ----
const uploadZone = document.getElementById('uploadZone');
const fileInput = document.getElementById('fileInput');
const fileNameHint = document.getElementById('fileNameHint');
const progressCard = document.getElementById('progressCard');
const progressBar = document.getElementById('progressBar');
const progressLabel = document.getElementById('progressLabel');
const progressPct = document.getElementById('progressPct');
const progressPhase = document.getElementById('progressPhase');
const resultArea = document.getElementById('resultArea');
const submitBtn = document.getElementById('submitBtn');
const btnText = document.getElementById('btnText');
const btnSpinner = document.getElementById('btnSpinner');

// Drag & drop
uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    onFileSelected(fileInput);
  }
});

window.onFileSelected = function(input) {
  if (input.files && input.files[0]) {
    fileNameHint.textContent = '已选择: ' + input.files[0].name;
    fileNameHint.style.color = 'var(--brand-color)';
  }
};

// ---- Update progress ----
function updateProgress(j, status) {
  const p = j.progress;
  progressCard.style.display = 'block';
  if (!p || typeof p !== 'object') {
    if (status === 'queued') { updateBar(6, '排队中...', '排队中'); }
    else if (status === 'started') { updateBar(12, '处理中（等待进度）...', '启动中'); }
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

// ---- Polling ----
async function pollJob(jobId, isContinue) {
  let lastSt = null;
  const poll = async () => {
    const r = await apiFetch('/customer-eval/api/jobs/' + jobId);
    if (!r.ok) { showToast('查询失败: ' + r.status, 'error'); return; }
    const j = await r.json();
    const st = normJobStatus(j.status);
    updateProgress(j, st);
    if (st !== lastSt) {
      lastSt = st;
      if (st === 'started' && !isContinue) {
        showToast('任务已开始，AI 正在评估客户数据...', 'info');
      }
    }
    if (st === 'finished') {
      updateBar(100, '已完成', '完成');
      showResult(j, jobId);
      return;
    }
    if (st === 'failed') {
      progressCard.style.display = 'block';
      const err = j.error || '无详细信息';
      resultArea.innerHTML = '<div class="result-item error"><strong>评估失败</strong><br>' + escapeHtml(err.slice(0, 500)) + '</div>';
      resetBtn();
      return;
    }
    setTimeout(poll, 2000);
  };
  poll();
}

function showResult(j, jobId) {
  const r = j.result || {};
  let html = '<div class="result-item success"><strong>评估完成</strong> — ' + (r.rows || '?') + ' 行已处理';
  html += ' · <a href="/customer-eval/api/jobs/' + jobId + '/download">下载 Excel</a>';
  html += ' · <a href="/crm/?batch=' + jobId + '">查看客户资源</a>';
  html += '</div>';

  if (r.has_more) {
    html += '<div class="result-item" style="margin-top:0.5rem">';
    html += '<button class="btn-primary-full" onclick="continueBatch(\'' + jobId + '\')" style="font-size:0.85rem;padding:0.5rem">继续下一批 (' + r.total_rows + ' 行总计)</button>';
    html += '</div>';
  }
  resultArea.innerHTML = html;
  resetBtn();
}

function showResultError(jobId) {
  resultArea.innerHTML =
    '<div class="result-item error"><strong>评估失败</strong><br>请检查 Worker 日志</div>';
  resetBtn();
}

function resetBtn() {
  submitBtn.disabled = false;
  btnText.style.display = '';
  btnSpinner.style.display = 'none';
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ---- Submit ----
window.startEval = async function() {
  const form = document.getElementById('evalForm');
  const file = fileInput.files[0];
  if (!file) { showToast('请先选择 .xlsx 文件', 'error'); return; }

  submitBtn.disabled = true;
  btnText.style.display = 'none';
  btnSpinner.style.display = 'inline-block';
  resultArea.innerHTML = '';
  progressCard.style.display = 'block';
  updateBar(4, '上传文件中...', '上传');

  const fd = new FormData(form);
  try {
    const r = await apiPost('/customer-eval/api/jobs', fd);
    if (!r.ok) {
      const txt = await r.text();
      showToast('上传失败: ' + r.status + ' ' + txt.slice(0, 200), 'error');
      resetBtn();
      return;
    }
    const data = await r.json();
    updateBar(6, '已入队，等待 Worker...', '排队');
    await pollJob(data.job_id, false);
  } catch (err) {
    showToast('请求失败: ' + err.message, 'error');
    resetBtn();
  }
};

// ---- Continue batch ----
window.continueBatch = async function(jobId) {
  resultArea.innerHTML = '';
  updateBar(6, '提交下一批...', '排队');
  const dryRun = document.querySelector('#evalForm [name=dry_run]');
  const noFetch = document.querySelector('#evalForm [name=no_fetch]');
  const fd = new FormData();
  if (dryRun && dryRun.checked) fd.append('dry_run', 'on');
  if (noFetch && noFetch.checked) fd.append('no_fetch', 'on');

  const r = await apiPost('/customer-eval/api/jobs/' + jobId + '/continue', fd);
  if (!r.ok) {
    showToast('继续失败: ' + r.status, 'error');
    return;
  }
  await pollJob(jobId, true);
};
