/** 知识库管理页面交互 */

let currentCollection = '产品信息';
const collections = ['产品信息', '公司文档', '采购表单'];

// -- Init --

async function init() {
  await loadStats();
  await loadDocuments();
  switchTab(currentCollection);
}

// -- Tab 切换 --

window.switchTab = function(name) {
  currentCollection = name;
  document.querySelectorAll('.kb-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.collection === name);
  });
  document.getElementById('docListTitle').textContent = name + ' — 文档列表';
  loadDocuments();
};

// -- 统计 --

async function loadStats() {
  try {
    const r = await fetch('/knowledge-base/api/kb/collections');
    const data = await r.json();
    const container = document.getElementById('kbStats');
    let html = '';
    for (const c of data) {
      html += `<div class="kb-stat-card">
        <div class="stat-name">${esc(c.name)}</div>
        <div class="stat-num">${c.parent_count || 0} 文档</div>
        <div class="stat-sub">${c.chunk_count || 0} chunks</div>
      </div>`;
    }
    container.innerHTML = html;
  } catch (e) {
    console.error('加载统计失败', e);
  }
}

// -- 文档列表 --

async function loadDocuments() {
  const tbody = document.getElementById('docTableBody');
  const countEl = document.getElementById('docListCount');
  tbody.innerHTML = '<tr><td colspan="4" class="empty-cell">加载中...</td></tr>';

  try {
    const r = await fetch('/knowledge-base/api/kb/documents?collection=' + encodeURIComponent(currentCollection));
    const docs = await r.json();

    countEl.textContent = docs.length + ' 个文件';

    if (!docs.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty-cell">暂无文档。导入文档或粘贴文本开始。</td></tr>';
      return;
    }

    let html = '';
    for (const d of docs) {
      html += `<tr>
        <td title="${escAttr(d.source_file || '')}">${esc(d.source_file || d.title || '')}</td>
        <td><span class="badge badge-gray">${esc(d.collection || currentCollection)}</span></td>
        <td>${d.chunk_count || 0}</td>
        <td class="col-action">
          <button class="btn-sm btn-outline" onclick="window.previewDoc('${escAttr(d.source_file)}')">预览</button>
          <button class="btn-sm btn-outline" onclick="window.deleteDoc('${escAttr(d.source_file)}')" style="color:var(--danger)">删除</button>
        </td>
      </tr>`;
    }
    tbody.innerHTML = html;
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-cell">加载失败</td></tr>';
    console.error(e);
  }
}

// -- 导入 --

window.importDirectory = async function() {
  const dir = document.getElementById('importDir').value.trim();
  if (!dir) { alert('请输入目录路径'); return; }

  const ocr = document.getElementById('chkOcr').checked ? '1' : '0';
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '入队中...';

  try {
    const form = new FormData();
    form.append('directory', dir);
    form.append('collection', currentCollection);
    form.append('ocr_cleanup', ocr);

    const r = await fetch('/knowledge-base/api/kb/import', { method: 'POST', body: form });
    const data = await r.json();

    if (data.status === 'error') {
      alert(data.error);
    } else {
      alert(`已入队 ${data.file_count} 个文件。\n\n请确保 RQ Worker 正在运行:\nrq worker -u redis://127.0.0.1:6379/0 knowledge_base:default --worker-class rq.SimpleWorker`);
      showJobs(data.jobs);
      // 定期刷新
      pollJobs(data.jobs);
    }
  } catch (e) {
    alert('导入失败: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '导入';
  }
};

// -- 文本粘贴入库 --

window.ingestText = async function() {
  const text = document.getElementById('pasteText').value.trim();
  const title = document.getElementById('pasteTitle').value.trim() || '粘贴文本';

  if (!text) { alert('请输入文本内容'); return; }

  try {
    const form = new FormData();
    form.append('text', text);
    form.append('title', title);
    form.append('collection', currentCollection);

    const r = await fetch('/knowledge-base/api/kb/ingest-text', { method: 'POST', body: form });
    const data = await r.json();

    if (data.status === 'ok') {
      alert(`入库成功: ${data.parents} 个父文档, ${data.children} 个 chunks`);
      document.getElementById('pasteText').value = '';
      document.getElementById('pasteTitle').value = '';
      loadStats();
      loadDocuments();
    } else {
      alert('入库失败: ' + (data.error || ''));
    }
  } catch (e) {
    alert('入库失败: ' + e.message);
  }
};

// -- 搜索测试 --

window.testSearch = async function() {
  const query = document.getElementById('searchQuery').value.trim();
  const mode = document.getElementById('searchMode').value;
  const container = document.getElementById('searchResults');

  if (!query) { container.innerHTML = ''; return; }

  container.innerHTML = '<p class="search-loading">检索中...</p>';

  try {
    const params = new URLSearchParams({ query, mode, collection: currentCollection, top_k: '5' });
    const r = await fetch('/knowledge-base/api/kb/search?' + params);
    const data = await r.json();

    if (!data.results.length) {
      container.innerHTML = '<p class="search-empty">未找到相关结果</p>';
      return;
    }

    let html = `<p class="search-info">找到 ${data.count} 条结果（模式: ${data.mode}）</p>`;
    for (const item of data.results) {
      const meta = item.metadata || {};
      const source = item.source_doc || meta.source_file || '';
      const section = meta.section || '';
      const score = item.rerank_score || item.score || 0;
      const chunkPreview = (item.chunk || '').substring(0, 300);

      html += `<div class="search-item">
        <div class="search-item-header">
          <strong>${esc(source)}</strong>
          ${section ? `<span class="search-section">${esc(section)}</span>` : ''}
          <span class="search-score">${score.toFixed(3)}</span>
        </div>
        <div class="search-item-body">${esc(chunkPreview)}...</div>
      </div>`;
    }
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<p class="search-error">检索失败</p>';
  }
};

// -- 预览文档 --

window.previewDoc = async function(sourceFile) {
  try {
    const r = await fetch(`/knowledge-base/api/kb/doc/${encodeURIComponent(sourceFile)}/preview?collection=${encodeURIComponent(currentCollection)}`);
    const data = await r.json();
    const preview = data.preview || '(无内容)';
    alert('文档: ' + sourceFile + '\n父文档数: ' + data.parent_count + '\n\n' + preview.substring(0, 800));
  } catch (e) {
    alert('加载失败');
  }
};

// -- 删除文档 --

window.deleteDoc = async function(sourceFile) {
  if (!confirm('确定删除文档 "' + sourceFile + '" 及其所有 chunks？此操作不可恢复。')) return;

  try {
    const r = await fetch(`/knowledge-base/api/kb/documents/${encodeURIComponent(sourceFile)}?collection=${encodeURIComponent(currentCollection)}`, { method: 'DELETE' });
    const data = await r.json();
    alert('已删除 ' + data.deleted + ' 个 chunks');
    loadStats();
    loadDocuments();
  } catch (e) {
    alert('删除失败');
  }
};

// -- Job 轮询 --

function showJobs(jobs) {
  const card = document.getElementById('kbJobsCard');
  const list = document.getElementById('kbJobsList');
  card.style.display = 'block';
  list.innerHTML = jobs.map(j => `<div class="job-item" id="job-${j.job_id.slice(0,8)}">${esc(j.file)} — <span class="job-status">处理中...</span></div>`).join('');
}

async function pollJobs(jobs) {
  const interval = setInterval(async () => {
    let allDone = true;
    for (const j of jobs) {
      try {
        const r = await fetch('/knowledge-base/api/kb/jobs/' + j.job_id);
        const d = await r.json();
        const el = document.getElementById('job-' + j.job_id.slice(0,8));
        if (el && d.status === 'finished') {
          el.querySelector('.job-status').textContent = d.result?.status === 'ok' ? '✓ 完成' : '✗ ' + (d.result?.error || '失败');
          el.querySelector('.job-status').className = 'job-status ' + (d.result?.status === 'ok' ? 'job-ok' : 'job-err');
        } else if (d.status !== 'finished') {
          allDone = false;
        }
      } catch(e) {}
    }
    if (allDone) {
      clearInterval(interval);
      loadStats();
      loadDocuments();
    }
  }, 3000);
}

// -- Utils --

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escAttr(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// -- Start --

init();
