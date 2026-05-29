// utils.js — Shared DOM utilities

// ---- Toast notifications ----
export function showToast(msg, type = 'info') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 3500);
}

// ---- Progress bar ----
export class ProgressBar {
  constructor(wrapEl, barEl, textEl) {
    this.wrap = wrapEl;
    this.bar = barEl;
    this.text = textEl;
  }
  show() { this.wrap.style.display = 'block'; }
  hide() { this.wrap.style.display = 'none'; }
  update(pct, label) {
    this.bar.style.width = Math.min(100, Math.max(0, pct)) + '%';
    if (label !== undefined) this.text.textContent = label;
  }
}

// ---- RQ Job status normalizer ----
export function normJobStatus(raw) {
  const s = String(raw || '');
  const m = s.match(/JobStatus\.(\w+)/i);
  return m ? m[1].toLowerCase() : s.toLowerCase();
}

// ---- Polling helper ----
export function pollUntil(fn, interval = 2000) {
  return new Promise((resolve, reject) => {
    const check = async () => {
      try {
        const result = await fn();
        if (result !== undefined) resolve(result);
        else setTimeout(check, interval);
      } catch (e) { reject(e); }
    };
    check();
  });
}

// ---- Format helpers ----
export function scoreClass(score) {
  if (score == null) return '';
  if (score >= 4) return 'score-high';
  if (score >= 2.5) return 'score-medium';
  return 'score-low';
}

export function badgeForRecommendation(rec) {
  const map = {
    'high_intent': ['badge-green', '高意向'],
    'watch': ['badge-yellow', '观察'],
    'no': ['badge-gray', '不建议'],
  };
  const def = map[rec] || ['badge-gray', rec || '-'];
  return '<span class="badge ' + def[0] + '">' + def[1] + '</span>';
}

export function badgeForReview(flag) {
  if (flag === 'YES') return '<span class="badge badge-red">需复核</span>';
  return '<span class="badge badge-green">正常</span>';
}

export function badgeForEmailStatus(st) {
  const map = {
    'draft': ['badge-yellow', '草稿'],
    'generated': ['badge-yellow', '草稿'],
    'confirmed': ['badge-blue', '已确认'],
    'sent': ['badge-green', '已发送'],
    'failed': ['badge-red', '发送失败'],
  };
  const def = map[st] || ['badge-gray', st || '未生成'];
  return '<span class="badge ' + def[0] + '">' + def[1] + '</span>';
}

export function badgeForReadStatus(customer) {
  if (!customer.email_status || customer.email_status === 'draft' || customer.email_status === 'generated' || customer.email_status === 'confirmed') {
    return '<span class="badge badge-gray">未发送</span>';
  }
  if (customer.email_status === 'failed') {
    return '<span class="badge badge-red">发送失败</span>';
  }
  if (customer.tracking_last_opened_at) {
    return '<span class="badge badge-green">已读</span>';
  }
  return '<span class="badge badge-yellow">未读</span>';
}

// ---- Email dropdown toggle (shared across agents) ----
let _emailDropdownOpen = null;

export function emailToggle(toggleEl) {
  var dd = toggleEl.parentElement.querySelector('.email-dropdown');
  if (!dd) return;
  if (_emailDropdownOpen && _emailDropdownOpen !== dd) {
    _emailDropdownOpen.classList.remove('open');
  }
  dd.classList.toggle('open');
  if (dd.classList.contains('open')) {
    _emailDropdownOpen = dd;
  } else {
    _emailDropdownOpen = null;
  }
}

export function emailPick(itemEl, email) {
  var dd = itemEl.parentElement;
  dd.classList.remove('open');
  _emailDropdownOpen = null;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(email).catch(function(){});
  }
  // Update the primary email display
  var cell = dd.parentElement;
  var primary = cell.querySelector('.email-primary');
  if (primary) {
    primary.textContent = email;
    primary.title = email;
  }
  // Unselect all items, select this one
  dd.querySelectorAll('.email-item').forEach(function(el) { el.classList.remove('email-selected'); });
  itemEl.classList.add('email-selected');
  // Dispatch custom event so calling code can update state
  cell.dispatchEvent(new CustomEvent('email-picked', { bubbles: true, detail: { email: email } }));
}

// ---- Download blob as file ----
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
