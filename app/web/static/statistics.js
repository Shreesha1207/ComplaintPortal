/* ==========================================================================
   Public statistics page.

   Reads only /api/analytics/public/*, which is why it needs no account. Those
   endpoints are hand-picked projections: no funding figure, and nothing at
   district x sector grain, where 39% of cells hold one or two requests and the
   public request feed already publishes enough to link against.

   The one thing this page must never lose is the demonstration-data notice. It
   used to be rendered only by the admin dashboard, which would have made it
   disappear at exactly the moment these figures became the public product.
   ========================================================================== */
import {
  api, fmt, hbar, hexMap, hideTip, pluralize, priorityPill, renderNav, seqLegend,
} from './viz.js';
import { choroLegend, choropleth, domainOf } from './geomap.js';

const $ = (id) => document.getElementById(id);

const state = {
  country: 'IN', view: 'geo', measure: 'priority_index',
  region: '', district: '',
  pack: null, geo: null, summary: null, regions: [], districts: [],
};

/* The measures a citizen can colour the map by. `format` is what the tooltip
   and legend print; `higherIsWorse` decides the sentence under the map, because
   a dark shape means "worst" for need and "most" for requests, and conflating
   the two is how a choropleth misleads. */
const MEASURES = {
  priority_index: {
    label: 'Unmet need (priority index)',
    unit: '0–100',
    format: (v) => fmt.n1(v),
    note: 'Darker means greater unmet need: more requests than the area\'s ' +
          'infrastructure and circumstances would predict, against a bigger ' +
          'measured shortfall.',
  },
  request_count: {
    label: 'Citizen requests received',
    unit: 'requests',
    format: (v) => fmt.n(v),
    note: 'Darker means more requests were received. This is participation, ' +
          'not need — an area that sends few requests may be well served or ' +
          'may simply have no way to reach us.',
  },
  population: {
    label: 'Population',
    unit: 'people',
    format: (v) => fmt.compact(v),
    note: 'Darker means more people live there. Shown so the need map can be ' +
          'read against it: a small dark district and a large one are not the ' +
          'same finding.',
  },
  silent: {
    label: 'Silent sectors (need, no requests)',
    unit: 'sectors',
    format: (v) => fmt.n(v),
    note: 'Darker means more sectors where the measured shortfall is severe ' +
          'and yet no citizen request arrived. Silence is a finding, not an ' +
          'absence of one.',
  },
};

const busy = (on) => { $('busy').hidden = !on; };

/* ------------------------------------------------------------------ boot */
async function boot() {
  await renderNav($('nav'), '/statistics');

  const countries = await api('/api/countries');
  $('country').innerHTML = countries
    .map(c => `<option value="${c.code}">${c.name}</option>`).join('');
  $('country').value = state.country;
  $('measure').innerHTML = Object.entries(MEASURES)
    .map(([k, m]) => `<option value="${k}">${m.label}</option>`).join('');

  $('country').onchange = async (e) => {
    state.country = e.target.value;
    state.region = state.district = '';
    await loadCountry();
  };
  $('measure').onchange = (e) => { state.measure = e.target.value; drawMap(); };
  $('v-geo').onclick = () => setView('geo');
  $('v-hex').onclick = () => setView('hex');
  $('clearsel').onclick = () => selectRegion(null);

  $('staffline').innerHTML =
    'Staff of a participating department can <a href="/login">sign in</a> to the ' +
    'verification queue. Funding figures and the budget tools are administrators only.';

  await loadCountry();
}

function setView(v) {
  state.view = v;
  $('v-geo').setAttribute('aria-pressed', String(v === 'geo'));
  $('v-hex').setAttribute('aria-pressed', String(v === 'hex'));
  drawMap();
}

async function loadCountry() {
  busy(true);
  state.pack = await api(`/api/countries/${state.country}`);
  // The geographic view is optional per pack: a pack with no `.geo.json` still
  // works, it just opens on the cartogram instead of offering a broken map.
  state.geo = null;
  if (state.pack.has_geometry) {
    try { state.geo = await api(`/api/countries/${state.country}/geometry`); }
    catch { state.geo = null; }
  }
  $('v-geo').disabled = !state.geo;
  if (!state.geo) setView('hex'); else setView('geo');

  $('notice').innerHTML =
    `<span aria-hidden="true">⚠</span><span><b>Demonstration data.</b> ` +
    `${escapeHtml(state.pack.data_notice)} Names and populations are real; ` +
    `every index in these figures is synthetic. Do not cite them.</span>`;

  const [summary, regions, districts] = await Promise.all([
    api(`/api/analytics/public/summary?country=${state.country}`),
    api(`/api/analytics/public/regions?country=${state.country}`),
    api(`/api/analytics/public/districts?country=${state.country}&limit=2000`),
  ]);
  state.summary = summary;
  state.regions = regions.items;
  state.districts = districts.items;
  busy(false);

  $('herotitle').textContent = `What ${summary.country_name} has asked for`;
  drawStats();
  drawBreakdowns();
  drawCaveats();
  drawMap();
  drawRanking();
  // Open on the district with the greatest unmet need rather than on an empty
  // panel: the page has already said what the headline is, and the first thing
  // a reader wants is an example of what it means.
  if (state.districts.length) selectDistrict(state.districts[0].district_code);
}

/* -------------------------------------------------------------- headline */
function drawStats() {
  const s = state.summary;
  const level = pluralize(state.pack.admin_levels[1]);
  $('stats').innerHTML = [
    ['Citizen requests', fmt.n(s.requests_total),
     `in ${s.languages_seen} languages, across ${Object.keys(s.by_channel).length} channels`, ''],
    ['Districts covered', fmt.n(s.districts), `across ${fmt.n(s.regions)} ${level}`, ''],
    ['Silent sectors', fmt.n(s.silent_districts),
     'severe shortfall, no request received', 'warn'],
    // Published rather than buried: without it, "requests received" and the sum
    // of the district counts below disagree by 10% with nothing explaining it.
    ['Not yet placed', fmt.n(s.requests_unclassified),
     `outside the ${fmt.n(s.requests_counted_in_rollups)} counted by district`, ''],
    ['Awaiting a person', fmt.n(s.requests_in_review),
     'the AI was not confident enough to act alone', ''],
  ].map(([k, v, d, cls]) =>
    `<div class="stat ${cls}"><div class="k">${k}</div><div class="v">${v}</div>
     <div class="d">${d}</div></div>`).join('');
}

function drawBreakdowns() {
  const s = state.summary;
  // "other" is not a sector, it is the classifier's "I could not place this".
  // Left as the raw key it reads like an eleventh sector sitting third in the
  // ranking, which is the opposite of what it means.
  bars('chSect', s.by_sector,
       (k) => (k === 'other' ? 'Not yet placed' : s.sector_names[k] || k));
  bars('chLang', s.by_language, (k) => s.language_names[k] || k.toUpperCase());
  bars('chChan', s.by_channel, (k) => k.replace(/_/g, ' '));
}

function bars(id, obj, label) {
  const rows = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]).slice(0, 10)
    .map(([k, v]) => ({ label: label(k), value: v }));
  hbar($(id), rows, {
    labelW: 124, rowH: 24, fmtValue: fmt.n, ariaLabel: 'Requests breakdown',
    tooltip: (r) => `<b>${escapeHtml(r.label)}</b><div class="row"><span>Requests</span>` +
                    `<span>${fmt.n(r.value)}</span></div>`,
  });
}

/* ------------------------------------------------------------------- map */
function drawMap() {
  const m = MEASURES[state.measure];
  const rows = state.regions;
  const dom = domainOf(rows, state.measure);
  const byCode = new Map(rows.map(r => [r.region_code, r]));
  const level = pluralize(state.pack.admin_levels[1]);

  $('mapsub').textContent = `${rows.length} ${level}`;
  $('clearsel').hidden = !state.region;
  $('maptitle').textContent = state.region
    ? `${byCode.get(state.region)?.region_name ?? state.region}`
    : 'Where the need is';

  const tip = (name, row) => {
    if (!row) return `<b>${escapeHtml(name)}</b><div class="row"><span>` +
      `No requests recorded</span><span>—</span></div>`;
    // The chosen measure leads, then the constant context rows -- minus whichever
    // of them the chosen measure already is, so the tooltip never prints the
    // same number twice under two names.
    const lead = state.measure === 'priority_index' ? '' :
      `<div class="row"><span>${escapeHtml(m.label)}</span>
        <span>${m.format(row[state.measure])}</span></div>`;
    const ctx = [
      ['Unmet need', fmt.n1(row.priority_index), 'priority_index'],
      ['Citizen requests', fmt.n(row.request_count), 'request_count'],
      ['Population', fmt.compact(row.population), 'population'],
    ].filter(([, , key]) => key !== state.measure || key === 'priority_index')
     .map(([k, v]) => `<div class="row"><span>${k}</span><span>${v}</span></div>`)
     .join('');
    return `<b>${escapeHtml(row.region_name)}</b>
      ${lead}
      ${ctx}
      <div class="row"><span>Districts covered</span><span>${fmt.n(row.districts)}</span></div>
      <div class="row"><span>Greatest need in</span>
        <span>${escapeHtml(row.top_district)}</span></div>`;
  };

  if (state.view === 'geo' && state.geo) {
    choropleth($('map'), state.geo, byCode, {
      valueKey: state.measure, min: dom.min, max: dom.max, selected: state.region,
      onSelect: selectRegion,
      ariaLabel: `${state.pack.name}: ${m.label} by ${state.pack.admin_levels[1]}`,
      tooltip: (region, row) => tip(region.name, row),
      noDataLabel: 'no requests recorded',
    });
    $('mapnote').innerHTML = `${escapeHtml(m.note)} Click a ${
      escapeHtml(state.pack.admin_levels[1].toLowerCase())} to filter the list; ` +
      `click it again to clear. Outlines are ${escapeHtml(state.geo.level.toLowerCase())}` +
      ` boundaries from Natural Earth, which is in the public domain. The platform ` +
      `covers a sample of districts, so there is no district-boundary map — a district ` +
      `map would show gaps that look like silence.`;
  } else {
    const hexRows = state.pack.regions.map(r => ({
      code: r.code, name: r.name, hex: r.hex,
      value: byCode.get(r.code)?.[state.measure] ?? 0,
      meta: byCode.get(r.code),
    }));
    hexMap($('map'), hexRows, {
      valueKey: 'value', size: hexRows.length > 30 ? 24 : 32,
      min: dom.min, max: dom.max, selected: state.region, onSelect: selectRegion,
      ariaLabel: `${state.pack.name}: ${m.label}, equal-area tiles`,
      tooltip: (r) => tip(r.name, r.meta),
    });
    $('mapnote').innerHTML = `${escapeHtml(m.note)} One equal-area tile per ` +
      `${escapeHtml(state.pack.admin_levels[1].toLowerCase())}, so colour reflects the ` +
      `measure rather than land area — on a true map a vast, thinly populated ` +
      `${escapeHtml(state.pack.admin_levels[1].toLowerCase())} dominates the eye and a ` +
      `dense small one disappears.`;
  }

  const legendOpts = { min: dom.min, max: dom.max, label: m.label, unit: m.unit,
                       noDataLabel: 'no requests recorded' };
  if (state.view === 'geo' && state.geo) choroLegend($('maplegend'), legendOpts);
  else seqLegend($('maplegend'), legendOpts);
}

function selectRegion(code) {
  state.region = code || '';
  state.district = '';
  drawMap();
  drawRanking();
  const first = state.region
    ? state.districts.find(d => d.region_code === state.region)
    : state.districts[0];
  if (first) selectDistrict(first.district_code);
}

/* --------------------------------------------------------------- ranking */
function drawRanking() {
  const rows = state.region
    ? state.districts.filter(d => d.region_code === state.region)
    : state.districts;
  const shown = rows.slice(0, 60);
  $('ranktitle').textContent = state.region
    ? `Districts in ${state.regions.find(r => r.region_code === state.region)?.region_name ?? ''}`
    : 'Districts by need';
  $('ranksub').textContent = rows.length > shown.length
    ? `${fmt.n(rows.length)} districts · showing the first ${shown.length}`
    : `${fmt.n(rows.length)} districts`;

  if (!shown.length) {
    $('ranklist').innerHTML = '<div class="empty">No districts here.</div>';
    return;
  }
  $('ranklist').innerHTML = shown.map((d, i) => `
    <div class="rankrow ${state.district === d.district_code ? 'sel' : ''}"
         data-code="${d.district_code}" tabindex="0" role="button">
      <span class="n">${i + 1}</span>
      <span class="nm"><b>${escapeHtml(d.district_name)}</b>
        <span>${escapeHtml(d.region_name)} · ${fmt.compact(d.population)} people ·
          ${fmt.n(d.request_count)} requests</span></span>
      ${priorityPill(d.priority_index)}
    </div>`).join('');

  for (const row of $('ranklist').querySelectorAll('.rankrow')) {
    const pick = () => selectDistrict(row.dataset.code);
    row.onclick = pick;
    row.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
    };
  }
}

/* ---------------------------------------------------------------- detail */
function selectDistrict(code) {
  state.district = code;
  const d = state.districts.find(x => x.district_code === code);
  drawRanking();
  if (!d) return;

  $('dtitle').innerHTML = `${escapeHtml(d.district_name)} ` +
    `<span class="muted">· ${escapeHtml(d.region_name)}</span>`;
  $('dsub').innerHTML = `Unmet need ${priorityPill(d.priority_index)}`;

  const sectors = d.top_sectors.map(s => `
    <div class="rankrow" style="cursor:default">
      <span class="nm"><b>${escapeHtml(s.sector_name)}</b></span>
      ${priorityPill(s.priority_index)}
    </div>`).join('');

  $('detail').innerHTML = `
    <dl class="kv" style="margin-bottom:16px">
      <dt>Population</dt><dd>${fmt.n(d.population)}</dd>
      <dt>Citizen requests</dt><dd>${fmt.n(d.request_count)}</dd>
      <dt>Silent sectors</dt><dd>${fmt.n(d.silent)}
        <span class="xs muted">(severe shortfall, no request received)</span></dd>
    </dl>
    <h3 style="margin:0 0 6px">Where the greatest needs are</h3>
    <div>${sectors}</div>
    <p class="xs muted" style="margin:12px 0 0">
      Scores per sector, not request counts. At this level a count can be one or
      two requests, and publishing those alongside the public request feed would
      come close to pointing at a person.</p>`;
}

/* -------------------------------------------------------------- caveats */
function drawCaveats() {
  const s = state.summary;
  $('caveats').innerHTML = `
    <p><b>These are demonstration figures.</b> ${escapeHtml(s.data_notice)}
       District and ${escapeHtml(state.pack.admin_levels[1].toLowerCase())} names and
       populations are real. Every index here is generated.</p>
    <p><b>${fmt.n(s.requests_unclassified)} of ${fmt.n(s.requests_total)} requests are not
       counted by district.</b> The classifier could not place them in a sector, so they
       reach no district total and are waiting for a person to read them. That is why
       the district counts sum to ${fmt.n(s.requests_counted_in_rollups)} rather than
       ${fmt.n(s.requests_total)}.</p>
    <p><b>Few requests is not the same as little need.</b> The score corrects for who
       is able to ask at all — literacy, poverty and phone access — but the correction
       is a model, not a measurement. The silent-sector count is published next to it
       for that reason.</p>
    <p><b>Nothing here is at street level.</b> The finest grain published is the
       district. Individual requests appear only in the recent-requests feed on the
       submission page, with personal details removed.</p>
    <p><b>Money is not on this page.</b> What is committed against each need, and how
       a budget would be allocated across them, is restricted to the departments
       accountable for it.</p>`;
}

const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

addEventListener('scroll', hideTip, { passive: true });
// Redraw on resize only when the width bucket actually changes, so dragging a
// window does not rebuild 36 polygons on every frame.
let lastW = innerWidth;
addEventListener('resize', () => {
  if (Math.abs(innerWidth - lastW) < 80) return;
  lastW = innerWidth;
  if (state.summary) drawMap();
}, { passive: true });

boot().catch(e => {
  document.querySelector('main').innerHTML =
    `<div class="card"><div class="card-b"><h2>Could not load the statistics</h2>
     <p class="sec">${escapeHtml(e.message)}</p></div></div>`;
});
