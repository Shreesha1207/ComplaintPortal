/* ==========================================================================
   Visualisation primitives — hand-rolled SVG, zero external libraries.

   No CDN on purpose. The dashboard has to render in a ministry office behind a
   restrictive proxy and in a demo room with no uplink, so every mark is drawn
   from data we already hold.

   Conventions follow the dataviz reference: thin marks, 4px rounded data-ends
   anchored to the baseline, a 2px surface gap between adjacent fills, recessive
   axes, a legend whenever two or more series share a plot, direct value labels
   (which is also the required relief for the light-mode contrast warning), and
   colour that is never the only channel carrying meaning.
   ========================================================================== */

export const fmt = {
  n: (v) => (v ?? 0).toLocaleString(),
  n1: (v) => (v ?? 0).toLocaleString(undefined, { maximumFractionDigits: 1 }),
  compact: (v) => {
    const a = Math.abs(v ?? 0);
    if (a >= 1e9) return (v / 1e9).toFixed(1) + 'B';
    if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k';
    return String(Math.round(v ?? 0));
  },
  pct: (v, d = 0) => `${(v ?? 0).toFixed(d)}%`,
};

const NS = 'http://www.w3.org/2000/svg';
export const el = (tag, attrs = {}, parent = null) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
};

/* ---- sequential scale -------------------------------------------------
   Seven steps of one hue. Bucketed rather than continuous so the legend is
   readable and so every fill is a step the validator actually checked.     */
export const SEQ_STEPS = 7;
// `min` stretches the ramp across the range actually observed. Without it a
// measure that occupies a narrow band (regional scores sit around 39–67 of a
// nominal 0–100) collapses into two or three steps and the map carries no
// information. The legend always prints the real domain, so stretching
// sharpens the contrast without misrepresenting the numbers.
export function seqBucket(value, max = 100, min = 0) {
  if (value == null || !isFinite(value) || value <= 0) return 0;
  const span = (max - min) || 1;
  const t = Math.max(0, Math.min(1, (value - min) / span));
  return Math.min(SEQ_STEPS, Math.max(1, Math.ceil(t * SEQ_STEPS) || 1));
}
export const seqVar = (b) => (b === 0 ? 'var(--seq-0)' : `var(--seq-${b})`);
// Steps 1–3 are pale on light / deep on dark; 4–7 the reverse. Label ink flips
// with the step so text never sits at low contrast on its own fill.
export const seqInk = (b) => (b >= 4 ? 'var(--seq-ink-dark)' : 'var(--seq-ink-light)');

/* ---- shared tooltip --------------------------------------------------- */
let tipEl = null;
function tip() {
  if (!tipEl) {
    tipEl = document.createElement('div');
    tipEl.className = 'tooltip';
    tipEl.setAttribute('role', 'status');
    document.body.appendChild(tipEl);
  }
  return tipEl;
}
export function showTip(html, ev) {
  const t = tip();
  t.innerHTML = html;
  t.classList.add('show');
  const pad = 14, r = t.getBoundingClientRect();
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - pad;
  t.style.left = Math.max(8, x) + 'px';
  t.style.top = Math.max(8, y) + 'px';
}
export const hideTip = () => tipEl && tipEl.classList.remove('show');

/* ======================================================================
   Hex cartogram.

   Equal-area tiles rather than true geographic polygons. That is a
   deliberate cartographic choice, not a shortcut: on a real map, visual
   weight tracks land area, so vast sparsely-populated districts dominate
   the eye while dense small ones vanish. For a demand map that inverts the
   signal. One tile per administrative unit gives every unit equal visual
   weight — and it renders identically for any country pack that supplies
   hex coordinates.
   ====================================================================== */
export function hexMap(container, rows, opts = {}) {
  const { size = 26, max = 100, min = 0, onSelect = null, selected = null,
          valueKey = 'priority_index', labelKey = 'code',
          tooltip = null } = opts;
  container.innerHTML = '';
  if (!rows.length) { container.innerHTML = '<div class="empty">No map data</div>'; return; }

  const W = Math.sqrt(3) * size, VS = 1.5 * size, pad = size * 0.9;
  const cols = Math.max(...rows.map(r => r.hex[0])) + 1;
  const maxRow = Math.max(...rows.map(r => r.hex[1]));
  const width = cols * W + W / 2 + pad * 2;
  const height = maxRow * VS + 2 * size + pad * 2;

  const svg = el('svg', {
    viewBox: `0 0 ${width.toFixed(1)} ${height.toFixed(1)}`,
    role: 'img', 'aria-label': opts.ariaLabel || 'Regional priority map',
  }, container);

  const pts = (cx, cy) => Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 180) * (60 * i - 30);
    return `${(cx + size * Math.cos(a)).toFixed(2)},${(cy + size * Math.sin(a)).toFixed(2)}`;
  }).join(' ');

  for (const r of rows) {
    const [c, rw] = r.hex;
    const cx = pad + c * W + (rw % 2 ? W / 2 : 0) + W / 2;
    const cy = pad + rw * VS + size;
    const v = r[valueKey];
    const b = seqBucket(v, max, min);

    const g = el('g', {
      class: 'hex' + (selected === r.code ? ' sel' : ''),
      tabindex: '0', role: 'button',
      'aria-label': `${r.name}: ${fmt.n1(v)}`,
    }, svg);
    // 2px surface-coloured stroke = the required gap between adjacent fills.
    el('polygon', { points: pts(cx, cy), fill: seqVar(b),
                    stroke: 'var(--surface-1)', 'stroke-width': 2 }, g);
    const t = el('text', {
      x: cx.toFixed(2), y: (cy + 3.2).toFixed(2), class: 'hexlabel',
      'text-anchor': 'middle', fill: b === 0 ? 'var(--text-muted)' : seqInk(b),
    }, g);
    t.textContent = r[labelKey] ?? '';

    const show = (ev) => showTip(tooltip ? tooltip(r) :
      `<b>${r.name}</b><div class="row"><span>Priority</span><span>${fmt.n1(v)}</span></div>`, ev);
    g.addEventListener('mousemove', show);
    g.addEventListener('mouseenter', show);
    g.addEventListener('mouseleave', hideTip);
    const pick = () => onSelect && onSelect(selected === r.code ? null : r.code);
    g.addEventListener('click', pick);
    g.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
    });
  }
  return svg;
}

export function seqLegend(container, { max = 100, min = 0, label = 'Priority index' } = {}) {
  const steps = Array.from({ length: SEQ_STEPS }, (_, i) =>
    `<span class="sw" style="background:${seqVar(i + 1)}"></span>`).join('');
  container.innerHTML =
    `<span>${label}</span><span class="swatches">${steps}</span>` +
    `<span class="mono">${fmt.n1(min)} – ${fmt.n1(max)}</span>` +
    `<span style="margin-left:10px"><span class="sw" style="background:var(--seq-0);` +
    `border:1px solid var(--border-strong);display:inline-block;vertical-align:-1px"></span> no data</span>`;
}

/* ======================================================================
   Horizontal bar ranking. One measure, categories on the axis, so a single
   hue is correct — colour carries no identity here and a legend would be
   noise. Values are always written next to the bar.
   ====================================================================== */
export function hbar(container, rows, opts = {}) {
  const { valueKey = 'value', labelKey = 'label', max = null, fmtValue = fmt.n1,
          rowH = 26, labelW = 150, onClick = null, tooltip = null,
          color = 'var(--seq-5)' } = opts;
  container.innerHTML = '';
  if (!rows.length) { container.innerHTML = '<div class="empty">No data</div>'; return; }

  const W = 640, valueW = 62, barW = W - labelW - valueW - 12;
  const top = 2, H = rows.length * rowH + top + 4;
  const hi = max ?? Math.max(...rows.map(r => +r[valueKey] || 0), 1);
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img',
                          'aria-label': opts.ariaLabel || 'Ranked bar chart' }, container);

  rows.forEach((r, i) => {
    const y = top + i * rowH;
    const v = +r[valueKey] || 0;
    const w = Math.max(2, (v / hi) * barW);
    const g = el('g', { class: onClick ? 'hex' : null, tabindex: onClick ? '0' : null,
                        role: onClick ? 'button' : null,
                        'aria-label': `${r[labelKey]}: ${fmtValue(v)}` }, svg);
    if (onClick) el('rect', { x: 0, y, width: W, height: rowH - 2, fill: 'transparent' }, g);

    const lab = el('text', { x: labelW - 9, y: y + rowH / 2 + 4, class: 'bar-label',
                             'text-anchor': 'end' }, g);
    lab.textContent = String(r[labelKey]).length > 24
      ? String(r[labelKey]).slice(0, 23) + '…' : r[labelKey];

    // 4px rounded data-end, square against the baseline.
    el('rect', { x: labelW, y: y + 5, width: w, height: rowH - 12, rx: 4,
                 fill: r.color || color }, g);
    const val = el('text', { x: labelW + w + 8, y: y + rowH / 2 + 4, class: 'bar-value' }, g);
    val.textContent = fmtValue(v);

    if (tooltip) {
      const show = (ev) => showTip(tooltip(r), ev);
      g.addEventListener('mousemove', show);
      g.addEventListener('mouseleave', hideTip);
    }
    if (onClick) {
      g.addEventListener('click', () => onClick(r));
      g.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(r); }
      });
    }
  });
  el('line', { x1: labelW, y1: top, x2: labelW, y2: H - 4, class: 'axis-line' }, svg);
  return svg;
}

/* ======================================================================
   Stacked contribution bar — how one priority score decomposes.
   Five categorical slots (validated, adjacent-pair mode). Segments carry a
   2px surface gap; every segment is direct-labelled and a legend is always
   present, so identity never rests on colour alone.
   ====================================================================== */
export const CAT = ['var(--cat-1)', 'var(--cat-2)', 'var(--cat-3)', 'var(--cat-4)', 'var(--cat-5)'];

export function stackedBar(container, segments, opts = {}) {
  const { total = null, height = 34, labels = true } = opts;
  container.innerHTML = '';
  const sum = total ?? segments.reduce((a, s) => a + s.value, 0);
  if (!sum) { container.innerHTML = '<div class="empty">No contribution data</div>'; return; }

  const W = 640, GAP = 2;
  const svg = el('svg', { viewBox: `0 0 ${W} ${height}`, role: 'img',
                          'aria-label': opts.ariaLabel || 'Score composition' }, container);
  let x = 0;
  segments.forEach((s, i) => {
    const w = Math.max(0, (s.value / sum) * W - GAP);
    if (w <= 0) { x += (s.value / sum) * W; return; }
    const g = el('g', {}, svg);
    el('rect', { x: x.toFixed(2), y: 0, width: w.toFixed(2), height, rx: 4,
                 fill: s.color || CAT[i % CAT.length] }, g);
    if (labels && w > 34) {
      const t = el('text', {
        x: (x + w / 2).toFixed(2), y: height / 2 + 4, 'text-anchor': 'middle',
        style: 'font:700 12px var(--sans);fill:#fff;paint-order:stroke;' +
               'stroke:rgba(0,0,0,.28);stroke-width:2.5px',
      }, g);
      t.textContent = s.value.toFixed(1);
    }
    const show = (ev) => showTip(
      `<b>${s.label}</b><div class="row"><span>Contribution</span>` +
      `<span>${s.value.toFixed(2)} pts</span></div>` +
      (s.detail ? `<div class="row"><span>Factor</span><span>${s.detail}</span></div>` : ''), ev);
    g.addEventListener('mousemove', show);
    g.addEventListener('mouseleave', hideTip);
    x += (s.value / sum) * W;
  });

  const leg = document.createElement('div');
  leg.className = 'chartlegend';
  leg.innerHTML = segments.map((s, i) =>
    `<span class="it"><span class="sw" style="background:${s.color || CAT[i % CAT.length]}"></span>` +
    `${s.label} <b class="mono">${s.value.toFixed(1)}</b></span>`).join('');
  container.appendChild(leg);
  return svg;
}

/* ---- small helpers used across pages ---------------------------------- */
export function pluralize(label) {
  return String(label).split('/').map((part) => {
    const t = part.trim();
    if (!t) return part;
    const w = t.split(' ');
    const last = w[w.length - 1];
    let pl;
    if (/^[A-Z]{2,}$/.test(last)) pl = last + 's';
    else if (/[^aeiou]y$/i.test(last)) pl = last.slice(0, -1) + 'ies';
    else if (/(s|x|z|ch|sh)$/i.test(last)) pl = last + 'es';
    else pl = last + 's';
    w[w.length - 1] = pl;
    return w.join(' ');
  }).join(' / ');
}

export const priorityPill = (v) => {
  const b = seqBucket(v, 100);
  return `<span class="pill" style="background:${seqVar(b)};color:${seqInk(b)}">${fmt.n1(v)}</span>`;
};
export const urgencyChip = (u) =>
  `<span class="chip u-${u}"><span class="dot"></span>${u}</span>`;


/* ---- identity & role-aware navigation ---------------------------------
   Every page renders its own nav from the server's capability map rather
   than from a hard-coded role check. The UI hiding a link is a courtesy,
   not a control: the endpoints enforce the boundary themselves, so a
   stale or tampered nav grants nothing.                                  */
export async function whoami() {
  try {
    return await api('/api/auth/me');
  } catch {
    return { authenticated: false, user: null,
             can: { submit_requests: true, review_queue: false,
                    view_analytics: false, view_funding: false, export_data: false } };
  }
}

export async function renderNav(el, current = '') {
  const me = await whoami();
  const link = (href, label) =>
    `<a href="${href}"${href === current ? ' aria-current="page"' : ''}>${label}</a>`;
  // A signed-out visitor is a citizen, and a citizen is shown nothing about
  // signing in: no sign-in link, no staff destinations they cannot open, and
  // no hint that the page they are on is the lesser half of something. Staff
  // reach their own entrance from the footer or by going to /login directly.
  const parts = [];
  if (me.authenticated) {
    parts.push(link('/citizen', 'Submit a request'));
    if (me.can.view_analytics) parts.push(link('/dashboard', 'Dashboard'));
    if (me.can.review_queue) parts.push(link('/review', 'Review queue'));
    parts.push(`<span class="chip" title="Signed in as ${me.user.username}">` +
               `${me.user.display_name || me.user.username} · ${me.user.role}</span>`);
    parts.push('<button class="btn" id="logout" style="padding:6px 10px">Sign out</button>');
  }
  parts.push('<button class="btn" id="theme" style="padding:6px 10px" ' +
             'aria-label="Toggle colour theme">\u25D0</button>');
  el.innerHTML = parts.join('');

  const out = document.getElementById('logout');
  if (out) {
    out.onclick = async () => {
      try { await apiPost('/api/auth/logout', {}); } catch { /* already gone */ }
      location.href = '/';
    };
  }
  themeToggle(document.getElementById('theme'));
  return me;
}

export async function api(path) {
  const r = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${path}`);
  return r.json();
}
export async function apiPost(path, body) {
  const r = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `${r.status} ${r.statusText}`);
  return data;
}
export function themeToggle(btn) {
  const KEY = 'theme';
  const apply = (t) => {
    if (t) document.documentElement.setAttribute('data-theme', t);
    else document.documentElement.removeAttribute('data-theme');
    btn.textContent = t === 'dark' ? '☀' : t === 'light' ? '☾' : '◐';
    btn.title = `Theme: ${t || 'system'}`;
  };
  let cur = null;
  try { cur = localStorage.getItem(KEY); } catch { /* private mode */ }
  apply(cur);
  btn.addEventListener('click', () => {
    cur = cur === 'dark' ? 'light' : cur === 'light' ? null : 'dark';
    try { cur ? localStorage.setItem(KEY, cur) : localStorage.removeItem(KEY); } catch { /* ignore */ }
    apply(cur);
  });
}
