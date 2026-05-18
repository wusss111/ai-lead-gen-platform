// crm_detail.js — Salesperson assignment on customer detail page
import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/utils.js';

async function loadSalespersonSelect() {
  const select = document.getElementById('salespersonSelect');
  if (!select) return;

  try {
    const r = await apiFetch('/crm/api/salespersons');
    if (!r.ok) return;
    const list = await r.json();
    list.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.name + (s.is_active ? '' : ' (已停用)');
      select.appendChild(opt);
    });

    // Pre-select: read from the current page (if rendered by Jinja2 we'd have data)
    // We fetch the customer data to get current assignment
    const custId = window.location.pathname.split('/').filter(Boolean).pop();
    const cr = await apiFetch('/crm/api/customers/' + custId);
    if (cr.ok) {
      const c = await cr.json();
      if (c.assigned_salesperson_id) {
        select.value = c.assigned_salesperson_id;
      }
    }

    // Bind change handler
    select.addEventListener('change', async () => {
      const spId = select.value;
      const status = document.getElementById('assignStatus');
      const fd = new FormData();
      fd.set('salesperson_id', spId);
      const rr = await apiFetch('/crm/api/customers/' + custId + '/assign', {
        method: 'PUT',
        body: fd,
      });
      if (rr.ok) {
        status.textContent = '已更新';
        status.style.color = 'var(--color-success)';
        showToast('分配成功', 'info');
      } else {
        status.textContent = '更新失败';
        status.style.color = 'var(--color-danger)';
        showToast('分配失败', 'error');
      }
      setTimeout(() => { status.textContent = ''; }, 2000);
    });
  } catch (e) {
    console.error('Failed to load salespersons', e);
  }
}

document.addEventListener('DOMContentLoaded', loadSalespersonSelect);
