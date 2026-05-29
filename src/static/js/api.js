// api.js — Shared fetch wrapper (Session auth via Cookie)
export async function apiFetch(url, opts = {}) {
  const r = await fetch(url, { ...opts, credentials: 'same-origin' });
  if (r.status === 401) {
    window.location.href = '/login?redirect=' + encodeURIComponent(window.location.pathname);
    throw new Error('auth_required');
  }
  if (r.status === 403) {
    throw new Error('forbidden');
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
    opts.body = body;
  }
  return apiFetch(url, opts);
}
