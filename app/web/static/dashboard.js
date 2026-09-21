import {
  api, fmt, hbar, hexMap, hideTip, pluralize, priorityPill, seqLegend, stackedBar,
  renderNav, urgencyChip, CAT,
} from './viz.js';

const $ = (id) => document.getElementById(id);
const state = {
  country: 'IN', sector: '', region: '', lens: '',
  selectedCell: null, pack: null, summary: null,
  weights: { demand: 0.30, gap: 0.25, people: 0.15, severity: 0.15, vulnerability: 0.15 },
};

const WEIGHT_HELP = {
  demand: 'How loudly citizens have asked, corrected for who is able to ask at all.',
  gap: 'Distance between measured infrastructure and the national benchmark.',
  people: 'How many people the deficit affects (log-scaled).',
  severity: 'How acute the reported need is.',
  vulnerability: 'Poverty, low literacy and digital exclusion.',
};

const busy = (on) => { $('busy').hidden = !on; };
const qs = () => {
  const p = new URLSearchParams({ country: state.country });
  for (const [k, v] of Object.entries(state.weights)) p.set(k, v.toFixed(3));
  if (state.sector) p.set('sector', state.sector);
  if (state.region) p.set('region', state.region);
  if (state.lens === 'unmet') p.set('unmet_needs_only', 'true');
  if (state.lens === 'silent') p.set('silent_only', 'true');
  return p;
};

/* ---------------------------------------------------------------- boot */
async function boot() {
  await renderNav($('nav'), '/dashboard');
  const countries = await api('/api/countries');
  $('country').innerHTML = countries
    .map(c => `<option value="${c.code}">${c.name}</option>`).join('');
  $('country').value = state.country;

  $('country').onchange = async (e) => {
    state.country = e.target.value;
    state.sector = state.region = ''; state.selectedCell = null;
    await loadPack(); refreshAll();
  };
  for (const id of ['sector', 'region', 'lens']) {
    $(id).onchange = (e) => { state[id] = e.target.value; state.selectedCell = null; refreshAll(); };
  }
  $('reset').onclick = () => {
    state.sector = state.region = state.lens = ''; state.selectedCell = null;
    $('sector').value = $('region').value = $('lens').value = '';
    refreshAll();
  };
  await loadPack();
  buildWeightControls();
  refreshAll();
  api('/api/review/queue?limit=1').then(q => {
    if (q.count) $('qbadge').textContent = q.count;
  }).catch(() => {});
}

async function loadPack() {
  state.pack = await api(`/api/countries/${state.country}`);
  $('sector').innerHTML = '<option value="">All sectors</option>' +
    state.pack.sectors.map(s => `<option value="${s.code}">${s.name}</option>`).join('');
  $('region').innerHTML = '<option value="">All regions</option>' +
    state.pack.regions.slice().sort((a, b) => a.name.localeCompare(b.name))
      .map(r => `<option value="${r.code}">${r.name}</option>`).join('');
  $('notice').innerHTML =
    `<span aria-hidden="true">⚠</span><span><b>Demonstration data.</b> ${state.pack.data_notice}
     Do not cite these figures.</span>`;
  $('footer').textContent =
    `${state.pack.name} · ${state.pack.regions.length} ${pluralize(state.pack.admin_levels[1])} · ` +
    `${state.pack.languages.length} languages · weights are tunable and travel with every API response.`;
}

function refreshAll() {
  $('csv').href = `/api/export/priorities.csv?country=${state.country}`;
  loadSummary(); loadMap(); loadRecs();
}

/* ------------------------------------------------------------- summary */
async function loadSummary() {
  const s = await api(`/api/analytics/summary?country=${state.country}`);
  state.summary = s;
  $('stats').innerHTML = [
    ['Citizen requests', fmt.n(s.requests_total), `${s.languages_seen} languages · ${s.by_channel ? Object.keys(s.by_channel).length : 0} channels`, ''],
    ['Unmet needs', fmt.n(s.unmet_needs), 'Strong citizen demand against a severe deficit', 'alert'],
    ['Silent districts', fmt.n(s.silent_districts), 'Severe deficit, no citizen signal received', 'warn'],
    ['Districts covered', fmt.n(s.districts), `across ${fmt.n(s.regions)} regions`, ''],
    ['Awaiting human review', fmt.n(s.requests_in_review), 'AI confidence below threshold', ''],
  ].map(([k, v, d, cls]) =>
    `<div class="stat ${cls}"><div class="k">${k}</div><div class="v">${v}</div>
     <div class="d">${d}</div></div>`).join('');

  const names = state.pack.sectors.reduce((a, x) => (a[x.code] = x.name, a), {});
  const langNames = state.pack.languages.reduce((a, l) => (a[l.code] = l.native, a), {});
  drawBars('chLang', s.by_language, (k) => langNames[k] || k.toUpperCase(), 'requests');
  drawBars('chChan', s.by_channel, (k) => k.replace('_', ' '), 'requests');
  drawBars('chSect', s.by_sector, (k) => names[k] || k, 'requests');
}

function drawBars(id, obj, label, noun) {
  const rows = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]).slice(0, 9)
    .map(([k, v]) => ({ label: label(k), value: v, key: k }));
  hbar($(id), rows, {
    labelW: 118, rowH: 24, fmtValue: fmt.n, ariaLabel: `Requests by ${noun}`,
    tooltip: (r) => `<b>${r.label}</b><div class="row"><span>Requests</span>
      <span>${fmt.n(r.value)}</span></div>`,
  });
}

/* ----------------------------------------------------------------- map */
async function loadMap() {
  const d = await api(`/api/analytics/regions?country=${state.country}`);
  const byCode = d.items.reduce((a, r) => (a[r.region_code] = r, a), {});
  const rows = state.pack.regions.map(r => ({
    code: r.code, name: r.name, hex: r.hex,
    priority_index: byCode[r.code]?.priority_index ?? 0,
    meta: byCode[r.code],
  }));
  // Stretch the colour domain over the range actually present, padded a little
  // so the darkest step is reserved for the genuine worst.
  const vals = rows.map(r => r.priority_index).filter(v => v > 0);
  const lo = vals.length ? Math.floor(Math.min(...vals)) : 0;
  const hi = vals.length ? Math.ceil(Math.max(...vals)) : 100;
  $('mapsub').textContent = `${rows.length} ${pluralize(state.pack.admin_levels[1])}`;

  hexMap($('map'), rows, {
    size: rows.length > 30 ? 24 : 32, max: hi, min: lo, selected: state.region,
    ariaLabel: `${state.pack.name}: development priority by region`,
    onSelect: (code) => {
      state.region = code || ''; $('region').value = state.region;
      state.selectedCell = null; refreshAll();
    },
    tooltip: (r) => {
      const m = r.meta;
      if (!m) return `<b>${r.name}</b><div class="row"><span>No data</span><span>—</span></div>`;
      return `<b>${r.name}</b>
        <div class="row"><span>Priority index</span><span>${fmt.n1(m.priority_index)}</span></div>
        <div class="row"><span>Population</span><span>${fmt.compact(m.population)}</span></div>
        <div class="row"><span>Citizen requests</span><span>${fmt.n(m.request_count)}</span></div>
        <div class="row"><span>Unmet needs</span><span>${m.unmet_needs}</span></div>
        <div class="row"><span>Silent districts</span><span>${m.silent}</span></div>
        <div class="row"><span>Worst district</span><span>${m.top_district}</span></div>`;
    },
  });
  seqLegend($('maplegend'), { max: hi, min: lo, label: 'Priority index' });
  $('maptitle').textContent = state.region
    ? `${byCode[state.region]?.region_name || state.region} selected`
    : 'Demand & deficit by region';
}

/* ------------------------------------------------------- recommendations */
async function loadRecs() {
  busy(true);
  const p = qs(); p.set('limit', '120');
  const d = await api(`/api/analytics/priorities?${p}`);
  busy(false);
  $('recsub').textContent = `${fmt.n(d.count)} matching · showing ${d.returned}`;

  if (!d.items.length) {
    $('recbody').innerHTML = '<tr><td colspan="6"><div class="empty">' +
      'Nothing matches these filters.</div></td></tr>';
    return;
  }
  $('recbody').innerHTML = d.items.map((r, i) => {
    const flags = [
      r.unmet_need ? '<span class="flag unmet">unmet need</span>' : '',
      r.silent_district ? '<span class="flag silent">silent</span>' : '',
    ].join(' ');
    const key = `${r.district_code}|${r.sector}`;
    return `<tr data-key="${key}" class="${state.selectedCell === key ? 'sel' : ''}">
      <td class="rank">${i + 1}</td>
      <td><b>${r.district_name}</b><div class="xs muted">${r.region_name}</div></td>
      <td class="small">${r.sector_name}</td>
      <td class="num">${priorityPill(r.priority_index)}</td>
      <td class="num">${fmt.pct(r.gap_pct)}</td>
      <td>${flags}</td></tr>`;
  }).join('');

  for (const tr of $('recbody').querySelectorAll('tr[data-key]')) {
    tr.onclick = () => selectCell(tr.dataset.key);
  }
  if (!state.selectedCell && d.items.length) selectCell(
    `${d.items[0].district_code}|${d.items[0].sector}`);
}

/* ------------------------------------------------------------- detail */
async function selectCell(key) {
  state.selectedCell = key;
  for (const tr of $('recbody').querySelectorAll('tr[data-key]')) {
    tr.classList.toggle('sel', tr.dataset.key === key);
  }
  const [district, sector] = key.split('|');
  const c = await api(`/api/analytics/cell/${district}/${sector}?country=${state.country}`);

  $('dtitle').innerHTML =
    `${c.sector_name} — ${c.district_name} <span class="muted">· ${c.region_name}</span>`;
  $('dsub').innerHTML =
    `Priority ${priorityPill(c.priority_index)} · recommendation confidence ` +
    `<b>${fmt.pct(c.confidence * 100)}</b>`;

  const segs = Object.entries(c.contributions)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v], i) => ({
      label: c.factor_labels[k] || k, value: v, color: CAT[i % CAT.length],
      detail: `${(c.factors[k] * 100).toFixed(0)}% × weight ${(c.weights[k] * 100).toFixed(0)}%`,
    }));

  const reqs = c.requests.slice(0, 6).map(r => `
    <div style="padding:8px 0;border-bottom:1px solid var(--border)">
      <div class="row xs muted" style="gap:7px;margin-bottom:3px">
        ${urgencyChip(r.urgency)}
        <span class="chip">${r.language.toUpperCase()}</span>
        <span class="chip">${r.channel.replace('_', ' ')}</span>
        <span class="chip">AI ${fmt.pct(r.ai_confidence * 100)}</span>
        ${r.status === 'review' ? '<span class="flag silent">in review</span>' : ''}
      </div>
      <div style="font-size:13.5px">${escapeHtml(r.text_redacted)}</div>
      ${r.translated && r.text_en ? `
        <div class="xs sec" style="margin-top:4px;padding-left:9px;
             border-left:2px solid var(--border-strong)">
          ${escapeHtml(r.text_en)}
          ${r.text_local && r.text_local !== r.text_en
            ? `<div style="margin-top:2px">${escapeHtml(r.text_local)}</div>` : ''}
        </div>` : ''}
    </div>`).join('') ||
    '<div class="empty">No citizen requests received for this district and sector.</div>';

  $('detail').innerHTML = `
    <div class="rationale">${escapeHtml(c.rationale)}</div>

    <h3 style="margin:18px 0 8px">How this score was built</h3>
    <div class="chart" id="stack"></div>
    <p class="xs muted" style="margin:8px 0 0">
      Segments sum to the priority index. Each is
      <span class="mono">factor × weight × 100</span>.
    </p>

    <div style="margin-top:18px">
      <div>
        <h3 style="margin:0 0 8px">Evidence</h3>
        <dl class="kv">
          <dt>Citizen requests</dt><dd>${fmt.n(c.request_count)}
            ${c.critical_count ? `(${c.critical_count} critical)` : ''}</dd>
          <dt>Languages</dt><dd>${c.languages.map(l => l.toUpperCase()).join(', ') || '—'}</dd>
          <dt>Channels</dt><dd>${c.channels.join(', ') || '—'}</dd>
          <dt>Participation index</dt><dd>${c.participation_index.toFixed(2)}
            <span class="xs muted">(expected propensity to file)</span></dd>
          <dt>Infrastructure</dt><dd>${c.infra_index} / ${c.benchmark} benchmark</dd>
          <dt>Population</dt><dd>${fmt.n(c.population)}</dd>
        </dl>
      </div>
    </div>

    <h3 style="margin:18px 0 8px">Citizen requests behind this
      <span class="muted xs">(${c.requests.length} total, showing up to 6)</span></h3>
    ${reqs}`;

  stackedBar($('stack'), segs, { ariaLabel: 'Priority score composition' });
}

/* ------------------------------------------------------------ weights */
function buildWeightControls() {
  const rows = Object.keys(state.weights).map(k => `
    <div class="field">
      <label for="w_${k}">${state.pack.factor_labels[k]}
        <b class="mono" id="wv_${k}">${(state.weights[k] * 100).toFixed(0)}%</b></label>
      <input type="range" id="w_${k}" min="0" max="60" step="1"
             value="${Math.round(state.weights[k] * 100)}">
      <span class="xs muted">${WEIGHT_HELP[k]}</span>
    </div>`).join('');
  $('weights').innerHTML = rows +
    '<button class="btn" id="wreset">Reset to defaults</button>';

  for (const k of Object.keys(state.weights)) {
    $(`w_${k}`).oninput = (e) => {
      state.weights[k] = +e.target.value / 100;
      $(`wv_${k}`).textContent = `${e.target.value}%`;
      debounceRecs();
    };
  }
  $('wreset').onclick = () => {
    state.weights = { demand: 0.30, gap: 0.25, people: 0.15, severity: 0.15, vulnerability: 0.15 };
    state.selectedCell = null;
    buildWeightControls(); loadRecs();
  };
}
function debounceRecs() {
  clearTimeout(window._wt);
  window._wt = setTimeout(() => { state.selectedCell = null; loadRecs(); }, 260);
}

const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

addEventListener('scroll', hideTip, { passive: true });
boot().catch(e => {
  document.querySelector('main').innerHTML =
    `<div class="card"><div class="card-b"><h2>Could not load dashboard</h2>
     <p class="sec">${escapeHtml(e.message)}</p></div></div>`;
});
