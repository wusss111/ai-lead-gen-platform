/* Social Media Management — JS */
import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/utils.js';

// ── State ──
let currentPage = 1;
let pageSize = 24;
let searchTimer = null;

// ── DOM helpers ──
function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

// ── Avatar colors ──
const AVATAR_COLORS = ['av-teal', 'av-ocean', 'av-coral', 'av-plum', 'av-gold', 'av-moss', 'av-slate', 'av-amber'];
function avatarClass(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) { h = name.charCodeAt(i) + ((h << 5) - h); }
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}
function nameInitial(name) {
  const s = String(name || '?').trim();
  const m = s.match(/[一-鿿]/);
  if (m) return m[0];
  return s.charAt(0).toUpperCase();
}

// ── Email status display ──
const EMAIL_STATUS = {
  'draft':     ['draft', '草稿'],
  'generated': ['draft', '草稿'],
  'confirmed': ['confirmed', '已确认'],
  'sent':      ['sent', '已发送'],
  'failed':    ['failed', '发送失败'],
};
function emailBadge(st) {
  const def = EMAIL_STATUS[st] || ['none', '未生成'];
  return `<span class="email-badge ${def[0]}">${def[1]}</span>`;
}

// ── Platform SVG icons ──
const PF_ICONS = {
  facebook:  `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>`,
  twitter:   `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>`,
  instagram: `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="2" y="2" width="20" height="20" rx="5" ry="5"/><circle cx="12" cy="12" r="5"/><circle cx="17.5" cy="6.5" r="1.5"/></svg>`,
  youtube:   `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>`,
  linkedin:  `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 0 1-2.063-2.065 2.064 2.064 0 1 1 2.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>`,
  tiktok:    `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></svg>`,
  pinterest: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 0a12 12 0 0 0-4.74 23.04c-.07-.75-.01-1.65.19-2.46l1.37-5.8s-.34-.69-.34-1.7c0-1.6.93-2.79 2.08-2.79 1 0 1.46.73 1.46 1.6 0 .98-.62 2.44-.94 3.8-.27 1.13.57 2.05 1.68 2.05 2.02 0 3.57-2.13 3.57-5.2 0-2.72-1.95-4.62-4.74-4.62-3.23 0-5.13 2.43-5.13 4.93 0 .98.38 2.03.85 2.6.09.11.1.21.08.32l-.3 1.25c-.05.21-.18.25-.4.15-1.47-.68-2.4-2.83-2.4-4.56 0-3.71 2.7-7.12 7.78-7.12 4.08 0 7.26 2.9 7.26 6.8 0 4.05-2.56 7.31-6.1 7.31-1.2 0-2.32-.62-2.7-1.35l-.74 2.81c-.26 1.03-.99 2.33-1.47 3.12A12 12 0 1 0 12 0z"/></svg>`,
};

const PF_PILL = {
  facebook: 'pill-fb', twitter: 'pill-tw', instagram: 'pill-ig',
  youtube: 'pill-yt', linkedin: 'pill-li', tiktok: 'pill-tt', pinterest: 'pill-pn',
};
const PF_LABEL = {
  facebook: 'Facebook', twitter: 'X/Twitter', instagram: 'Instagram',
  youtube: 'YouTube', linkedin: 'LinkedIn', tiktok: 'TikTok', pinterest: 'Pinterest',
};

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  loadStats();
  loadCustomers();

  // 加载销售筛选下拉（仅管理员）
  if (window.__currentUser && window.__currentUser.role === 'admin') {
    loadSalespersonFilter();
  }
  // 销售角色隐藏销售筛选
  if (window.__currentUser && window.__currentUser.role === 'salesperson') {
    const spFilter = document.getElementById('filterSalesperson');
    if (spFilter) spFilter.style.display = 'none';
  }
});

// ── Stats ──
async function loadStats() {
  try {
    const r = await apiFetch('/social-media/api/stats');
    if (!r.ok) return;
    const d = await r.json();
    el('statTotal').textContent = d.total_with_social;
    for (const [k, v] of Object.entries(d.platform_counts || {})) {
      const cap = k.charAt(0).toUpperCase() + k.slice(1);
      const span = el('stat' + cap);
      if (span) span.textContent = v;
    }
  } catch (_) { /* silent */ }
}

// ── Customers ──
async function loadCustomers() {
  const params = new URLSearchParams();
  const search = el('searchInput').value.trim();
  const platform = el('filterPlatform').value;
  const hasSocial = el('filterHasSocial').value;
  const country = el('filterCountry').value.trim();
  const minScore = el('filterMinScore').value.trim();
  const sort = el('filterSort').value;

  if (search) params.set('search', search);
  if (platform) params.set('platform', platform);
  if (hasSocial) params.set('has_social', hasSocial);
  if (country) params.set('country', country);
  if (minScore) params.set('min_score', minScore);
  const spId = el('filterSalesperson') ? el('filterSalesperson').value : '';
  if (spId) params.set('salesperson_id', spId);
  params.set('sort', sort);
  params.set('page', currentPage);
  params.set('page_size', pageSize);

  try {
    const r = await apiFetch('/social-media/api/customers?' + params.toString());
    if (!r.ok) { showToast('加载失败 (HTTP ' + r.status + ')', 'error'); return; }
    const data = await r.json();
    renderCards(data.customers || []);
    renderPagination(data);
    el('filterCount').textContent = '共 ' + data.total + ' 条';
  } catch (e) {
    showToast('网络错误', 'error');
  }
}

function renderCards(customers) {
  const grid = el('socialGrid');
  if (!customers.length) {
    grid.innerHTML = '<div class="loading-placeholder"><span>暂无匹配的客户数据</span></div>';
    return;
  }

  grid.innerHTML = customers.map(c => {
    const profiles = c.social_profiles || [];
    const score = c.overall_score_computed;
    let scoreCls = 'low';
    if (score >= 4) scoreCls = 'high';
    else if (score >= 3) scoreCls = 'mid';

    const avatarCls = avatarClass(c.company_name);
    const initial = nameInitial(c.company_name);

    const platformsHtml = profiles.length
      ? profiles.map(p => {
          const icon = PF_ICONS[p.platform] || '';
          const pill = PF_PILL[p.platform] || '';
          const label = PF_LABEL[p.platform] || p.platform;
          return `<a href="${esc(p.url)}" target="_blank" rel="noopener" class="platform-pill ${pill}" title="${label}: ${esc(p.handle)}">${icon} ${esc(p.handle)}</a>`;
        }).join('')
      : '<span class="no-platforms">暂无社媒账号</span>';

    return `
    <div class="social-card">
      <div class="card-header-row">
        <div class="company-avatar ${avatarCls}">${initial}</div>
        <div class="card-info">
          <a href="/crm/${c.id}" class="card-company">${esc(c.company_name)}</a>
          <div class="card-meta">
            <span class="country-tag">${esc(c.country_region || '-')}</span>
          </div>
        </div>
      </div>
      ${c.contact_email ? `<div class="card-contact">${esc(c.contact_email)}</div>` : ''}
      <div class="card-badges">
        <span class="score-pill ${scoreCls}">${score != null ? Number(score).toFixed(1) : '-'}</span>
        ${emailBadge(c.email_status)}
      </div>
      <div class="card-platforms">${platformsHtml}</div>
    </div>`;
  }).join('');
}

function renderPagination(data) {
  const wrap = el('pagination');
  const tp = data.total_pages;
  const cp = data.page;
  const total = data.total;

  if (total === 0) {
    wrap.innerHTML = '<span class="page-info">暂无数据</span>';
    return;
  }

  let html = '<div class="page-btns">';
  html += `<button ${cp <= 1 ? 'disabled' : ''} onclick="goPage(${cp - 1})" title="上一页">&laquo;</button>`;

  const start = Math.max(1, cp - 2);
  const end = Math.min(tp, cp + 2);
  if (start > 1) {
    html += `<button onclick="goPage(1)">1</button>`;
    if (start > 2) html += `<button disabled>&hellip;</button>`;
  }
  for (let p = start; p <= end; p++) {
    html += `<button class="${p === cp ? 'active' : ''}" onclick="goPage(${p})">${p}</button>`;
  }
  if (end < tp) {
    if (end < tp - 1) html += `<button disabled>&hellip;</button>`;
    html += `<button onclick="goPage(${tp})">${tp}</button>`;
  }
  html += `<button ${cp >= tp ? 'disabled' : ''} onclick="goPage(${cp + 1})" title="下一页">&raquo;</button>`;
  html += '</div>';

  const from = (cp - 1) * pageSize + 1;
  const to = Math.min(cp * pageSize, total);
  html += `<span class="page-info">${from}-${to} / ${total} 条</span>`;

  html += `<select class="page-size-select" onchange="changePageSize(this.value)">`;
  for (const s of [24, 48, 96]) {
    html += `<option value="${s}" ${s === pageSize ? 'selected' : ''}>${s}条/页</option>`;
  }
  html += '</select>';

  wrap.innerHTML = html;
}

window.goPage = function(p) {
  currentPage = p;
  loadCustomers();
  window.scrollTo({ top: 0, behavior: 'smooth' });
};
window.changePageSize = function(sz) {
  pageSize = parseInt(sz);
  currentPage = 1;
  loadCustomers();
};

// ── Search debounce ──
window.onSearchInput = function() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentPage = 1; loadCustomers(); }, 350);
};
window.reloadCustomers = function() {
  currentPage = 1;
  loadCustomers();
};

// ── Export ──
window.exportCSV = async function() {
  try {
    const r = await apiFetch('/social-media/api/customers?page_size=10000&has_social=');
    if (!r.ok) { showToast('导出失败', 'error'); return; }
    const data = await r.json();
    const rows = data.customers || [];
    const header = ['公司名称', '国家', '综合评分', '邮件状态', 'Facebook', 'Twitter', 'Instagram', 'YouTube', 'LinkedIn', 'TikTok', 'Pinterest'];
    const lines = [header.join(',')];

    for (const c of rows) {
      const profiles = c.social_profiles || [];
      const byPlat = {};
      for (const p of profiles) { byPlat[p.platform] = p.url; }
      const row = [
        csvCell(c.company_name),
        csvCell(c.country_region),
        c.overall_score_computed != null ? Number(c.overall_score_computed).toFixed(1) : '',
        csvCell({draft:'草稿',generated:'草稿',confirmed:'已确认',sent:'已发送',failed:'发送失败'}[c.email_status] || '未生成'),
        csvCell(byPlat.facebook || ''),
        csvCell(byPlat.twitter || ''),
        csvCell(byPlat.instagram || ''),
        csvCell(byPlat.youtube || ''),
        csvCell(byPlat.linkedin || ''),
        csvCell(byPlat.tiktok || ''),
        csvCell(byPlat.pinterest || ''),
      ];
      lines.push(row.join(','));
    }

    const bom = '﻿';
    const blob = new Blob([bom + lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'social_media_export.csv';
    a.click();
    URL.revokeObjectURL(url);
    showToast('导出完成');
  } catch (_) { showToast('导出出错', 'error'); }
};

function csvCell(s) {
  const v = String(s || '');
  if (v.includes(',') || v.includes('"') || v.includes('\n')) {
    return '"' + v.replace(/"/g, '""') + '"';
  }
  return v;
}

async function loadSalespersonFilter() {
  try {
    const r = await apiFetch('/crm/api/salespersons');
    if (!r.ok) return;
    const list = await r.json();
    const sel = document.getElementById('filterSalesperson');
    if (!sel) return;
    list.forEach(function(sp) {
      const opt = document.createElement('option');
      opt.value = sp.id;
      opt.textContent = sp.name;
      sel.appendChild(opt);
    });
  } catch (_) {}
}
