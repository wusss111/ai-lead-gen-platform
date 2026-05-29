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
let _totalAccumulated = 0;   // 跨批次累计已处理行数
let _grandTotal = 0;         // 总行数（从第一批获取）
let _batchIndex = 1;         // 当前第几批（后备）
let _batchSize = 100;        // 批大小（首批 progress 回来后更新）
let _seenTransition = new Set();  // 已处理的批次切换（防重复累加）

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
    if (data.total_rows) { _grandTotal = data.total_rows; _batchIndex = 1; }
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
  _lastActivePhase = '';  // 重置步骤条状态
  el('progressActions').style.display = 'flex';
  el('btnPause').disabled = false;
  el('btnCancel').disabled = false;

  let lastSt = null;
  let _pollStuck = 0;
  const poll = async () => {
    try {
      const r = await apiFetch('/customer-eval/api/jobs/' + jobId);
      if (!r.ok) {
        showToast('任务已结束', 'info');
        setLoading(false);
        _currentPollingJobId = null;
        el('progressActions').style.display = 'none';
        if (window.__removeJob) window.__removeJob(jobId);
        return;
      }
      const j = await r.json();
      // 批次切换检测：API 注入了 next_job_id 说明上一批已完成
      const transId = j.next_job_id || (j.progress && j.progress.next_job_id);
      if (transId && !_seenTransition.has(transId)) {
        _seenTransition.add(transId);
        const rows = j.batch_rows || (j.progress && j.progress.batch_rows) || 0;
        if (rows > 0) {
          _totalAccumulated += rows;
        }
        if (j.total_rows) _grandTotal = j.total_rows;
      }
      // DB rows_completed 作为回退：即使 Redis job_next 过期也能正确显示批次号
      // （rows_completed 表示所有已完成批次的总行数，比 _totalAccumulated 更权威）
      if (j.rows_completed && j.rows_completed > _totalAccumulated) {
        _totalAccumulated = j.rows_completed;
        if (j.total_rows) _grandTotal = j.total_rows;
      }
      const st = normJobStatus(j.status);
      updateProgress(j, st);
      if (st !== lastSt) {
        lastSt = st;
        _pollStuck = 0;
        if (st === 'started') showToast('AI 正在评估客户数据...', 'info');
      } else {
        _pollStuck++;
      }
      // H1: 超时保护 — 如果 job 长时间无变化，停止轮询
      if (_pollStuck > 180) {
        showToast('任务可能已中断（长时间无响应），请刷新页面检查', 'error');
        setLoading(false);
        _currentPollingJobId = null;
        el('progressActions').style.display = 'none';
        if (window.__removeJob) window.__removeJob(jobId);
        return;
      }
      // C3: 先检查取消/暂停状态，再检查 finished（cancel 后 RQ 会标记为 finished）
      if (j.progress && j.progress.control === 'cancel') {
        updateBar(100, '已取消', '取消');
        resultArea.innerHTML = '<div class="result-item error"><strong>已取消</strong> — 已处理的数据已保存。剩余行未处理。</div>';
        el('progressActions').style.display = 'none';
        _currentPollingJobId = null;
        if (el('btnCancel')._cancelTimeout) { clearTimeout(el('btnCancel')._cancelTimeout); }
        setLoading(false);
        if (window.__removeJob) window.__removeJob(jobId);
        return;
      }
      if (st === 'finished') {
        // 检查是否是错误暂停（非用户主动暂停）
        if (j.result && j.result.error && j.result.paused) {
          updateBar(100, '自动恢复中…', '恢复');
          resultArea.innerHTML = '<div class="result-item warn"><strong>评估意外中断，自动恢复中…</strong></div>';
          _currentPollingJobId = null; setLoading(false);
          el('progressActions').style.display = 'none';
          setTimeout(() => window.continueBatchV2(jobId), 1500);
          return;
        }
        // 检查是否是被取消后结束的
        if (j.result && j.result.control === 'cancel') {
          updateBar(100, '已取消', '取消');
          resultArea.innerHTML = '<div class="result-item error"><strong>已取消</strong> — ' + (j.result.rows || '?') + ' 行已保存。</div>';
          el('progressActions').style.display = 'none';
          _currentPollingJobId = null; _totalAccumulated = 0;
          if (el('btnCancel')._cancelTimeout) { clearTimeout(el('btnCancel')._cancelTimeout); }
          setLoading(false);
          if (window.__removeJob) window.__removeJob(jobId);
          return;
        }
        // 自动续跑：从 progress meta（优先）或 result 中读取 next_job_id
        const nextId = (j.progress && j.progress.next_job_id)
                    || j.next_job_id
                    || (j.result && j.result.next_job_id);
        if (nextId) {
          const batchRows = (j.progress && j.progress.batch_rows)
                         || (j.result && j.result.rows)
                         || 0;
          _totalAccumulated += batchRows;
          if (!_grandTotal && j.result && j.result.total_rows) {
            _grandTotal = j.result.total_rows;
          }
          _batchIndex += 1;
          if (window.__trackJob) window.__trackJob(nextId, '评估续批', 'customer-eval');
          setTimeout(() => pollJob(nextId), 500);
          return;
        }
        _totalAccumulated = 0; _grandTotal = 0; _batchIndex = 1;
        updateBar(100, '已完成', '完成'); showResult(j, jobId); return;
      }
      if (j.progress && j.progress.control === 'pause') {
        updateBar(100, '已暂停 · ' + (j.progress.message || ''), '暂停');
        resultArea.innerHTML = '<div class="result-item warn"><strong>已暂停</strong> — 已处理的数据已保存。可点击下方继续处理剩余行。</div>';
        el('btnPause').disabled = true;
        el('progressActions').style.display = 'none';
        _currentPollingJobId = null;
        setLoading(false);
        if (j.result && j.result.batch_end_exclusive) {
          const nextRow = j.result.batch_end_exclusive;
          resultArea.innerHTML += '<div class="result-item" style="margin-top:0.5rem"><button class="btn-submit" onclick="window.continueBatchV2(\'' + jobId + '\')">继续处理（从第 ' + (nextRow + 1) + ' 行开始）</button></div>';
        }
        return;
      }
      if (st === 'failed') {
        if (window.__removeJob) window.__removeJob(jobId);
        _currentPollingJobId = null;
        el('progressActions').style.display = 'none';
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
  // 从 API 提前获取总行数，不等第一批结束就能显示 "批次 1/N"
  if (!_grandTotal && j.total_rows) {
    _grandTotal = j.total_rows;
  }
  const p = j.progress;
  progressCard.style.display = 'block';
  if (!p || typeof p !== 'object') {
    // 新批次无进度数据时，用缓存的 _batchSize + _totalAccumulated 拼批次号
    const curBatch = _grandTotal > 0 ? Math.floor(_totalAccumulated / _batchSize) + 1 : _batchIndex;
    const totBatches = _grandTotal > 0 ? Math.ceil(_grandTotal / _batchSize) : 0;
    const prefix = totBatches > 1 ? '批次 ' + curBatch + '/' + totBatches + '  ' : '';
    if (status === 'queued') updateBar(6, prefix + '排队中...', '排队中');
    else if (status === 'started') { updateBar(12, prefix + '处理中...', '启动中'); updatePhaseStepper('ready', 0, 0); }
    return;
  }
  const cur = Number(p.current) || 0;
  const tot = Number(p.total) || 0;
  const phase = p.phase || '';
  // 只用 eval 阶段的 total 更新批大小（prefetch 的 tot 是网站数，不能用）
  if (tot > 0 && (phase === 'eval' || phase === 'write' || phase === 'batch_done')) {
    _batchSize = tot;
  }
  let pct = 0;

  if (phase === 'write' || phase === 'done') {
    pct = 100;
  } else if (phase === 'prefetch') {
    if (tot > 1) {
      pct = Math.min(15, Math.round((8 * cur) / tot) + 7);
    } else {
      pct = cur > 0 ? 15 : 8;
    }
  } else if (tot > 0) {
    pct = Math.min(99, Math.round((85 * cur) / tot) + 14);
    if (cur > 0 && pct < 15) pct = 15;
    pct = Math.min(99, pct);
  }

  // 跨批显示标签（不影响进度条计算）
  let label;
  const totalCur = _totalAccumulated + cur;
  const totalTot = _grandTotal || tot;
  // 批号计算：优先用缓存的 _batchSize（eval 阶段写入），prefetch 的 tot 是网站数不可用
  const realBatchSize = (_batchSize > 0 && _grandTotal > 0)
    ? _batchSize
    : (tot > 0 && (phase === 'eval' || phase === 'write') ? tot : 100);
  const currentBatch = realBatchSize > 0 ? Math.floor(_totalAccumulated / realBatchSize) + 1 : _batchIndex;
  const batchTotal = _grandTotal > 0 && realBatchSize > 0 ? Math.ceil(_grandTotal / realBatchSize) : 0;
  const batchPrefix = batchTotal > 1 ? ('批次 ' + currentBatch + '/' + batchTotal + '  ') : '';
  if (phase === 'prefetch') {
    if (tot > 1) label = batchPrefix + (p.message || '') + ' · ' + cur + '/' + tot + ' 网站';
    else label = batchPrefix + (cur > 0 ? (p.message || '网站抓取完成') : '并行抓取网站中...');
  } else {
    label = batchPrefix + (p.message || p.label || '') + (tot > 1 ? ' · ' + cur + '/' + tot + ' 行' : '');
  }
  if (_grandTotal > 0) {
    label = '累计 ' + totalCur + '/' + totalTot + ' · ' + label;
  }
  updateBar(pct, label, phaseLabel(phase));
  updatePhaseStepper(phase, cur, tot, p);
}

function updateBar(pct, label, phase) {
  progressBar.style.width = pct + '%';
  progressLabel.textContent = label;
  progressPct.textContent = pct + '%';
}

	const _PHASE_ORDER = ['ready', 'prefetch', 'fetch', 'eval', 'write', 'done'];
let _procStartTs = 0;
let _lastActivePhase = '';

function _fmtTime(sec) {
  if (sec < 60) return Math.round(sec) + '秒';
  if (sec < 3600) return Math.floor(sec / 60) + '分' + Math.round(sec % 60) + '秒';
  const h = Math.floor(sec / 3600);
  return h + '时' + Math.floor((sec % 3600) / 60) + '分';
}

function updatePhaseStepper(phase, cur, tot, p) {
  if (!el('phaseStepper')) return;
  const now = Date.now();
  if (!_procStartTs && (cur > 0 || phase === 'prefetch' || phase === 'fetch' || phase === 'eval')) _procStartTs = now;

  // 确定激活的步骤：prefetch 激活第一步，fetch/eval 激活第二步
  let activePhase = phase;
  if (phase === 'fetch' || phase === 'eval') activePhase = 'eval';
  const idx = _PHASE_ORDER.indexOf(activePhase);
  if (idx < 0) return;

  const steps = document.querySelectorAll('.phase-step');
  steps.forEach(step => {
    const stepPhase = step.dataset.phase;
    const pIdx = _PHASE_ORDER.indexOf(stepPhase);
    step.classList.remove('active', 'done');
    if (pIdx < idx) step.classList.add('done');
    else if (pIdx === idx) step.classList.add('active');
  });

  document.querySelectorAll('.phase-line').forEach(line => {
    line.classList.remove('done');
    const prevStep = line.previousElementSibling;
    if (prevStep && prevStep.classList.contains('done')) line.classList.add('done');
  });

  const pfStat = el('phaseStat-prefetch');
  const evalStat = el('phaseStat-eval');

  if (phase === 'done' || phase === 'write') {
    if (pfStat) { pfStat.classList.add('done'); pfStat.textContent = '已完成'; }
    if (evalStat) { evalStat.classList.add('done'); evalStat.textContent = '已完成'; }
    const elapsed = _procStartTs ? (now - _procStartTs) / 1000 : 0;
    const writeStat = el('phaseStat-write');
    if (writeStat) {
      writeStat.textContent = elapsed > 0 ? '耗时 ' + _fmtTime(elapsed) : '写入中...';
      if (phase === 'done') writeStat.classList.add('done');
    }
    return;
  }

  if (phase === 'ready') {
    if (pfStat) pfStat.textContent = '准备中...';
    if (evalStat) evalStat.textContent = '等待中';
    return;
  }

  // Phase: prefetch — 网站抓取进度
  if (phase === 'prefetch') {
    if (tot > 1) {
      // 增量进度：显示 x/y 网站
      const elapsed = _procStartTs ? (now - _procStartTs) / 1000 : 1;
      if (pfStat) pfStat.textContent = cur + '/' + tot + ' 个网站';
    } else if (cur > 0) {
      if (pfStat) { pfStat.classList.add('done'); pfStat.textContent = p && p.message ? p.message : '已完成'; }
    } else {
      if (pfStat) pfStat.textContent = '并行抓取中...';
    }
    if (evalStat) evalStat.textContent = '等待中';
    return;
  }

  // Phase: fetch / eval — 并行评估进度
  if (tot > 0 && cur > 0) {
    const elapsed = _procStartTs ? (now - _procStartTs) / 1000 : 1;
    const rate = cur / Math.max(1, elapsed);
    const remaining = Math.max(0, tot - cur);
    const eta = rate > 0 ? remaining / rate : 0;
    if (pfStat) { pfStat.classList.add('done'); pfStat.textContent = '已完成'; }
    if (evalStat) evalStat.textContent = cur + '/' + tot + ' · 约剩' + _fmtTime(eta);
  } else if (tot > 0) {
    if (pfStat) { pfStat.classList.add('done'); pfStat.textContent = '已完成'; }
    if (evalStat) evalStat.textContent = '0/' + tot + ' · 等待中...';
  }
}

function phaseLabel(p) {
  const map = {
    ready: '准备中', prefetch: '网站抓取', fetch: '逐行处理', eval: 'AI评估',
    classify: '处理中', write: '写入结果', done: '完成'
  };
  return map[p] || p;
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
async function checkCrashedBatches() {
  try {
    const r = await apiFetch('/customer-eval/api/batches');
    if (!r.ok) return;
    const batches = await r.json();
    const running = batches.filter(b => b.status === 'started' && b.rq_status === 'started');
    const crashed = batches.filter(b => b.status === 'started' && b.rq_status !== 'started');

    // 有正在运行的批次：自动显示进度条，但不锁定 UI（不禁用上传按钮）
    if (running.length > 0) {
      progressCard.style.display = 'block';
      el('progressActions').style.display = 'flex';
      el('btnPause').disabled = false;
      el('btnCancel').disabled = false;
      _currentPollingJobId = running[0].id;
      // 注意：不调 setLoading(true)，上传按钮保持可用
      updateBar(6, '恢复追踪: ' + escapeHtml(running[0].original_filename || running[0].id), '追踪中');
      setTimeout(() => pollJob(running[0].id), 500);
    }

    // 暂停/中断的批次：自动恢复（不用手动点击）
    const paused = batches.filter(b => b.status === 'paused' && b.rq_status !== 'started');
    if (paused.length > 0 && running.length === 0) {
      paused.forEach(b => {
        resultArea.innerHTML = '<div class="result-item warn">检测到中断任务（已完成 ' + (b.rows_completed || 0) + ' 行），自动恢复中…</div>';
        setTimeout(() => window.continueBatchV2(b.id), 800);
      });
    }

    if (crashed.length > 0) {
      let html = resultArea.innerHTML;
      html += '<div class="result-item warn"><strong>发现中断的评估任务</strong><br>以下批次未完成，可以继续：</div>';
      crashed.forEach(b => {
        html += '<div class="result-item" style="margin-top:0.5rem"><strong>' + escapeHtml(b.original_filename || '') + '</strong> — 已完成 ' + (b.rows_completed || 0) + ' 行' +
          ' <button class="btn-submit" onclick="window.resumeBatch(\'' + b.id + '\', ' + (b.rows_completed || 0) + ')" style="font-size:0.85rem;padding:0.3rem 0.8rem;margin-left:0.5rem">▶ 继续</button>' +
          ' <button class="btn-cancel" onclick="window.forceCancelBatch(\'' + b.id + '\')" style="font-size:0.85rem;padding:0.3rem 0.8rem;margin-left:0.25rem;background:#6c757d;color:#fff;border:none;border-radius:4px;cursor:pointer">清除</button></div>';
      });
      resultArea.innerHTML = html;
    }
  } catch(e) {}
}

// 用户手动点击查看运行中的任务
window.trackRunningBatch = function(batchId) {
  resultArea.innerHTML = '';
  progressCard.style.display = 'block';
  setLoading(true);
  pollJob(batchId);
};

// 强制取消（不需要 Worker 响应，直接更新 DB）
window.forceCancelBatch = async function(batchId) {
  if (!confirm('确定要强制取消这个任务吗？已处理的数据会保留。')) return;
  try {
    const r = await apiFetch('/customer-eval/api/jobs/' + batchId + '/cancel', { method: 'POST' });
    if (r.ok) {
      resultArea.innerHTML = '';
      showToast('已取消，刷新页面即可', 'info');
    } else {
      showToast('取消失败: ' + r.status, 'error');
    }
  } catch(e) {
    showToast('取消失败: ' + e.message, 'error');
  }
};

window.resumeBatch = async function(batchId, fromRow) {
  setLoading(true);
  progressCard.style.display = 'block';
  resultArea.innerHTML = '<div class="result-item"><p>正在从第 ' + (fromRow + 1) + ' 行恢复评估...</p></div>';
  try {
    const r = await apiFetch('/customer-eval/api/jobs/' + batchId + '/resume', { method: 'POST' });
    if (!r.ok) { showToast('恢复失败: ' + r.status, 'error'); setLoading(false); return; }
    const data = await r.json();
    pollJob(data.job_id);
  } catch(e) { showToast('恢复异常: ' + e.message, 'error'); setLoading(false); }
};

function restoreActiveJobs() {
  const jobs = window.__getTrackedJobs ? window.__getTrackedJobs() : [];
  if (!jobs.length) {
    checkCrashedBatches();
    return;
  }
  const evalJobs = jobs.filter(j => j.agent === 'customer-eval');
  if (evalJobs.length) {
    // 自动恢复进度：显示进度卡片 + 轮询，不禁用上传按钮
    progressCard.style.display = 'block';
    el('progressActions').style.display = 'flex';
    el('btnPause').disabled = false;
    el('btnCancel').disabled = false;
    _currentPollingJobId = evalJobs[0].jobId;
    updateBar(6, '恢复追踪: ' + escapeHtml(evalJobs[0].label || evalJobs[0].jobId), '追踪中');
    setTimeout(() => pollJob(evalJobs[0].jobId), 500);
    return;
  }
  checkCrashedBatches();
}

// Run restoration on page load
document.addEventListener('DOMContentLoaded', () => {
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
      // 超时保护：15 秒后如果还没停，强制释放 UI（Worker 可能已死）
      const _cancelTimeout = setTimeout(() => {
        if (_currentPollingJobId === jid) {
          _currentPollingJobId = null;
          setLoading(false);
          el('progressActions').style.display = 'none';
          updateBar(100, '已取消（强制）', '取消');
          resultArea.innerHTML = '<div class="result-item warn"><strong>已强制取消</strong> — 任务可能未响应，已处理数据已保存。<br>如需彻底清除，请刷新页面。</div>';
          showToast('取消超时，已强制释放', 'error');
        }
      }, 15000);
      // 保存 timeout ID 以便在正常取消时清除
      el('btnCancel')._cancelTimeout = _cancelTimeout;
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
  try {
    resultArea.innerHTML = '';
    updateBar(6, '提交下一批...', '排队');
    const fd = new FormData();
    if (el('cbDryRun').checked) fd.append('dry_run', 'on');
    if (el('cbNoFetch').checked) fd.append('no_fetch', 'on');
    const r = await apiPost('/customer-eval/api/jobs/' + jobId + '/continue', fd);
    if (!r.ok) { showToast('继续失败: ' + r.status, 'error'); return; }
    if (window.__trackJob) window.__trackJob(jobId, '续批', 'customer-eval');
    await pollJob(jobId);
  } catch (e) {
    showToast('继续请求失败: ' + e.message, 'error');
  }
};
