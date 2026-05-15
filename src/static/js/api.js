// api.js — Shared fetch wrapper with auth handling
const AUTH_KEY = 'platform_basic_auth';

function getCreds() {
  try {
    return JSON.parse(sessionStorage.getItem(AUTH_KEY));
  } catch { return null; }
}

export async function apiFetch(url, opts = {}) {
  const creds = getCreds();
  const headers = { ...opts.headers };
  if (creds) {
    headers['Authorization'] = 'Basic ' + btoa(
      decodeURIComponent(encodeURIComponent(creds.user + ':' + creds.pass))
    );
  }
  const r = await fetch(url, { ...opts, headers });
  if (r.status === 401) {
    const u = prompt('内部站点 — 用户名');
    if (u === null) throw new Error('cancel');
    const p = prompt('密码');
    if (p === null) throw new Error('cancel');
    sessionStorage.setItem(AUTH_KEY, JSON.stringify({ user: u, pass: p }));
    headers['Authorization'] = 'Basic ' + btoa(
      decodeURIComponent(encodeURIComponent(u + ':' + p))
    );
    return fetch(url, { ...opts, headers });
  }
  return r;
}

export async function apiGet(url) {
  return apiFetch(url);
}

export async function apiPost(url, body, isJson = false) {
  const opts = { method: 'POST' };
  if (isJson) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  } else {
    opts.body = body; // FormData
  }
  return apiFetch(url, opts);
}
