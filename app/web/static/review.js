/* Human review — the escalation path that keeps the AI out of the last word. */
import { api, apiPost, fmt, renderNav, urgencyChip } from './viz.js';

const $ = (id) => document.getElementById(id);
const state = { country: 'IN', pack: null, items: [], sel: null };

async function boot() {
  await renderNav($('nav'), '/review');
  const cs = await api('/api/countries');
  $('country').innerHTML = cs.map(c => `<option value="${c.code}">${c.name}</option>`).join('');
  $('country').value = state.country;
  $('country').onchange = async (e) => { state.country = e.target.value; await load(); };
  $('refresh').onclick = load;
  await load();
}

async function load() {
  state.pack = await api(`/api/countries/${state.country}`);
  const q = await api(`/api/review/queue?country=${state.country}&limit=120`);
  state.items = q.items;
  $('qcount').textContent = `${fmt.n(q.count)} awaiting review`;
  $('qsum').innerHTML =
    `Confidence threshold <b class="mono">${q.threshold}</b> — anything below is held.`;

  $('queue').innerHTML = q.items.map(r => `
    <div class="qrow" data-id="${r.id}" tabindex="0" role="button"
         style="padding:11px 15px;border-bottom:1px solid var(--border);cursor:pointer">
      <div class="row xs" style="gap:6px;margin-bottom:5px">
        <span class="chip">${r.language.toUpperCase()}</span>
        <span class="chip">${r.sector}</span>
        ${urgencyChip(r.urgency)}
        <span class="chip">AI ${fmt.pct(r.ai_confidence * 100)}</span>
        <span class="xs muted" style="margin-left:auto">${r.channel.replace('_', ' ')}</span>
      </div>
      <div class="small">${esc(r.text_redacted.slice(0, 120))}</div>
    </div>`).join('') || '<div class="empty">Queue is empty — nothing awaiting review.</div>';

  for (const el of $('queue').querySelectorAll('.qrow')) {
    const pick = () => open(el.dataset.id);
    el.onclick = pick;
    el.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } };
  }
  if (q.items.length) open(q.items[0].id);
  else $('rpanel').innerHTML = '<div class="empty">Nothing to review.</div>';
}

async function open(id) {
  const r = await api(`/api/requests/${id}`);
  state.sel = r;
  for (const el of $('queue').querySelectorAll('.qrow')) {
    el.style.background = el.dataset.id === id
      ? 'color-mix(in srgb,var(--accent) 12%,transparent)' : '';
  }
  $('rtitle').innerHTML = `Review <span class="mono xs muted">${r.id}</span>`;

  const sectors = state.pack.sectors.map(s =>
    `<option value="${s.code}" ${s.code === r.sector ? 'selected' : ''}>${s.name}</option>`).join('');
  const urg = ['critical', 'high', 'medium', 'low'].map(u =>
    `<option value="${u}" ${u === r.urgency ? 'selected' : ''}>${u}</option>`).join('');

  $('rpanel').innerHTML = `
    <div style="font-size:16px;line-height:1.65;padding:12px;background:var(--surface-3);
                border-radius:var(--radius-sm)">${esc(r.text_redacted)}</div>
    ${r.translated && r.text_en ? `
      <div class="small sec" style="margin-top:8px;padding:9px 12px;
           background:var(--surface-3);border-radius:var(--radius-sm);
           border-left:3px solid var(--accent)">
        <div class="xs muted" style="margin-bottom:3px">Translation</div>
        ${esc(r.text_en)}
        ${r.text_local && r.text_local !== r.text_en
          ? `<div style="margin-top:4px">${esc(r.text_local)}</div>` : ''}
      </div>` : `
      <p class="xs muted" style="margin:8px 0 0">No translation stored — this
        request was classified by the offline engine, which categorises but does
        not translate. Configure a model and run
        <code>POST /api/translate/backfill</code>.</p>`}
    <p class="xs muted" style="margin:8px 0 0">
      Stored text only. Any phone number or identifier was removed at intake
      ${r.pii_types.length ? `(<b>${r.pii_types.join(', ')}</b> redacted)` : '(none detected)'}.</p>

    <div class="rationale" style="margin-top:14px">${esc(r.ai_rationale)}</div>

    <dl class="kv" style="margin-top:14px">
      <dt>Detected language</dt><dd>${r.language.toUpperCase()}
        (${fmt.pct(r.language_confidence * 100)})</dd>
      <dt>Channel</dt><dd>${r.channel.replace('_', ' ')}</dd>
      <dt>Affected population</dt><dd>${fmt.n(r.affected_population)}</dd>
      <dt>Engine</dt><dd>${r.ai_engine}</dd>
      <dt>Received</dt><dd class="mono xs">${r.created_at}</dd>
    </dl>

    <h3 style="margin:18px 0 9px">Your decision</h3>
    <div class="controls">
      <div class="field"><label for="f_sector">Sector</label>
        <select id="f_sector">${sectors}<option value="other"
          ${r.sector === 'other' ? 'selected' : ''}>Other / not a development request</option></select></div>
      <div class="field"><label for="f_urg">Urgency</label><select id="f_urg">${urg}</select></div>
      <div class="field"><label for="f_pop">Affected people</label>
        <input type="number" id="f_pop" min="0" value="${r.affected_population}"></div>
      <div class="field" style="flex:1;min-width:200px"><label for="f_note">Note</label>
        <input type="text" id="f_note" placeholder="Optional — recorded in the audit log"></div>
    </div>
    <div class="row" style="margin-top:14px">
      <button class="btn primary" id="b_verify">Confirm &amp; verify</button>
      <button class="btn rec" id="b_reject">Reject — not a development request</button>
      <span class="xs muted" id="rmsg" style="margin-left:auto"></span>
    </div>

    <h3 style="margin:20px 0 8px">Audit trail</h3>
    <div class="tablewrap"><table><thead><tr><th>When</th><th>Actor</th>
      <th>Action</th><th>Detail</th></tr></thead><tbody>
      ${r.audit.map(a => `<tr style="cursor:default"><td class="mono xs">${a.at}</td>
        <td class="small">${esc(a.actor)}</td><td class="small">${a.action}</td>
        <td class="xs muted">${esc(JSON.stringify(a.detail))}</td></tr>`).join('')}
    </tbody></table></div>`;

  $('b_verify').onclick = () => decide('verified');
  $('b_reject').onclick = () => decide('rejected');
}

async function decide(status) {
  const r = state.sel;
  $('rmsg').innerHTML = '<span class="spinner"></span> saving…';
  try {
    await apiPost(`/api/requests/${r.id}/review`, {
      status,
      sector: $('f_sector').value,
      urgency: $('f_urg').value,
      affected_population: +$('f_pop').value,
      note: $('f_note').value || null,
      reviewer: 'demo.reviewer',
    });
    $('rmsg').textContent = `Saved as ${status}.`;
    await load();
  } catch (e) {
    $('rmsg').textContent = 'Failed: ' + e.message;
  }
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

boot().catch(e => {
  $('rpanel').innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`;
});
