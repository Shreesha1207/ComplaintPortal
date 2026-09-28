/* ==========================================================================
   Geographic choropleth.

   WHY THIS EXISTS ALONGSIDE THE HEX CARTOGRAM
   -------------------------------------------
   The cartogram in viz.js gives every region equal visual weight, which is the
   honest way to read *priority*: on a true map, visual weight tracks land area,
   so Rajasthan shouts and Delhi disappears. That argument still holds, and the
   cartogram is still here.

   But it costs a reader who is looking for their own state. Nobody knows where
   Manipur sits on a hex grid, and a citizen arriving at a statistics page is
   looking for somewhere they live. So the two views answer two questions and
   the page offers both, geographic first: "where am I" before "who is worst".

   NO EXTERNAL LIBRARY, AND NO RASTER TILES
   ----------------------------------------
   Same constraint as the rest of the dashboard: this has to render in a
   ministry office behind a restrictive proxy and in a demo room with no uplink.
   So the outlines are vector data this deployment already serves
   (/api/countries/{code}/geometry), projected here, and there is no basemap to
   fetch. A choropleth does not need one -- the administrative outlines *are*
   the map.

   Geometry is region-level only, and the page says so. The packs carry a sample
   of districts, so district polygons would draw a map full of holes that a
   reader would read as "nothing reported here".
   ========================================================================== */
import { el, fmt, hideTip, seqBucket, seqInk, seqVar, showTip, SEQ_STEPS } from './viz.js';

const RAD = Math.PI / 180;

/* ---- projection ------------------------------------------------------
   Equirectangular with the horizontal axis scaled by cos(parallel), which is
   the cheap standard correction for the east-west stretch a raw lon/lat plot
   gives you. Across one country's extent it is visually indistinguishable from
   a conic projection, and it is eight lines instead of a dependency.        */
function projector(geo, width) {
  const [w, s, e, n] = geo.bounds;
  const k = Math.cos((geo.projection?.parallel ?? (s + n) / 2) * RAD);
  const scale = width / ((e - w) * k);
  return {
    height: (n - s) * scale,
    x: (lon) => (lon - w) * k * scale,
    y: (lat) => (n - lat) * scale,
  };
}

function ringPath(ring, p) {
  let d = '';
  for (let i = 0; i < ring.length; i++) {
    d += (i ? 'L' : 'M') + p.x(ring[i][0]).toFixed(1) + ' ' + p.y(ring[i][1]).toFixed(1);
  }
  return d + 'Z';
}

/* Polygon centroid of the largest ring, used to place a label. The shoelace
   centroid sits outside concave shapes often enough to matter, so it falls back
   to the bounding-box centre when it lands outside the ring's own box. */
function labelPoint(rings, p) {
  const ring = rings[0];
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [x0, y0] = ring[i], [x1, y1] = ring[i + 1];
    const f = x0 * y1 - x1 * y0;
    a += f; cx += (x0 + x1) * f; cy += (y0 + y1) * f;
  }
  const xs = ring.map(q => q[0]), ys = ring.map(q => q[1]);
  const bx = [Math.min(...xs), Math.max(...xs)], by = [Math.min(...ys), Math.max(...ys)];
  let lon, lat;
  if (Math.abs(a) < 1e-9) { lon = (bx[0] + bx[1]) / 2; lat = (by[0] + by[1]) / 2; }
  else {
    lon = cx / (3 * a); lat = cy / (3 * a);
    if (lon < bx[0] || lon > bx[1] || lat < by[0] || lat > by[1]) {
      lon = (bx[0] + bx[1]) / 2; lat = (by[0] + by[1]) / 2;
    }
  }
  // Width of the largest ring in projected px, which decides whether a label
  // fits inside the shape at all.
  const span = p.x(bx[1]) - p.x(bx[0]);
  return { x: p.x(lon), y: p.y(lat), span };
}

/**
 * Draw a choropleth.
 *
 * @param container  element to render into
 * @param geo        the /api/countries/{code}/geometry payload
 * @param values     Map of region code -> row, or a plain object
 * @param opts       valueKey, max, min, selected, onSelect, tooltip, label,
 *                   width, ariaLabel, noDataLabel
 */
export function choropleth(container, geo, values, opts = {}) {
  const {
    valueKey = 'priority_index', max = 100, min = 0, selected = null,
    onSelect = null, tooltip = null, width = 900,
    ariaLabel = 'Choropleth map', noDataLabel = 'no data reported',
  } = opts;
  container.innerHTML = '';
  if (!geo?.regions?.length) {
    container.innerHTML = '<div class="empty">No map geometry for this country</div>';
    return null;
  }
  const get = (code) => (values instanceof Map ? values.get(code) : values?.[code]);

  const p = projector(geo, width);
  const pad = 6;
  const svg = el('svg', {
    viewBox: `${-pad} ${-pad} ${(width + pad * 2).toFixed(1)} ${(p.height + pad * 2).toFixed(1)}`,
    class: 'geomap', role: 'img', 'aria-label': ariaLabel,
  }, container);

  // A hatch for "no data". Colour alone must never carry the distinction
  // between "scored lowest" and "we heard nothing", because those are opposite
  // claims and the palest step of a ramp reads as the former.
  const defs = el('defs', {}, svg);
  const hatch = el('pattern', {
    id: 'nodata', width: 6, height: 6, patternUnits: 'userSpaceOnUse',
    patternTransform: 'rotate(45)',
  }, defs);
  el('rect', { width: 6, height: 6, fill: 'var(--seq-0)' }, hatch);
  el('line', { x1: 0, y1: 0, x2: 0, y2: 6, stroke: 'var(--border-strong)',
               'stroke-width': 1.4 }, hatch);

  // Painted in two passes so the selected outline and every label sit above all
  // the fills — otherwise a neighbour drawn later clips the highlight.
  const shapes = el('g', { class: 'geo-shapes' }, svg);
  const labels = el('g', { class: 'geo-labels', 'aria-hidden': 'true' }, svg);
  const anySelected = Boolean(selected);

  for (const region of geo.regions) {
    const row = get(region.code);
    const v = row ? row[valueKey] : null;
    const has = v != null && isFinite(v) && v > 0;
    const b = has ? seqBucket(v, max, min) : 0;
    const isSel = selected === region.code;

    const g = el('g', {
      class: 'geo' + (isSel ? ' sel' : '') + (anySelected && !isSel ? ' dim' : ''),
      tabindex: onSelect ? '0' : null, role: onSelect ? 'button' : null,
      'aria-label': `${region.name}: ${has ? fmt.n1(v) : noDataLabel}`,
      'aria-pressed': onSelect ? String(isSel) : null,
    }, shapes);

    const d = region.rings.map(r => ringPath(r, p)).join(' ');
    el('path', {
      d, class: 'geo-fill', fill: has ? seqVar(b) : 'url(#nodata)',
      // The 0.9px surface-coloured stroke is the gap between adjacent fills
      // that keeps two neighbouring steps of the same ramp legible as two.
      stroke: 'var(--surface-1)', 'stroke-width': 0.9,
      'stroke-linejoin': 'round', 'vector-effect': 'non-scaling-stroke',
    }, g);

    const lp = labelPoint(region.rings, p);
    // Only label a shape wide enough to hold its code without overlapping its
    // neighbour. The rest are reachable by hover, keyboard and the ranked list
    // beside the map, so nothing is only-on-the-map.
    if (lp.span > 26) {
      const t = el('text', {
        x: lp.x.toFixed(1), y: (lp.y + 3).toFixed(1), class: 'geo-label',
        'text-anchor': 'middle', fill: has ? seqInk(b) : 'var(--text-muted)',
      }, labels);
      t.textContent = region.code;
    }

    const show = (ev) => showTip(
      tooltip ? tooltip(region, row)
              : `<b>${region.name}</b><div class="row"><span>Value</span>` +
                `<span>${has ? fmt.n1(v) : '—'}</span></div>`, ev);
    g.addEventListener('mousemove', show);
    g.addEventListener('mouseenter', show);
    g.addEventListener('mouseleave', hideTip);
    if (onSelect) {
      const pick = () => onSelect(isSel ? null : region.code);
      g.addEventListener('click', pick);
      g.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
      });
    }
  }
  // Selected shape last, so its heavier outline is never overpainted.
  const sel = shapes.querySelector('.geo.sel');
  if (sel) shapes.appendChild(sel);
  svg.appendChild(labels);
  return svg;
}

/**
 * Legend for a choropleth: the ramp, the real domain at both ends, and the
 * no-data hatch. The domain is printed rather than implied, because the ramp is
 * stretched over the range actually observed.
 */
export function choroLegend(container, { max = 100, min = 0, label = 'Priority index',
                                         unit = '', noDataLabel = 'no data' } = {}) {
  const steps = Array.from({ length: SEQ_STEPS }, (_, i) =>
    `<span class="sw" style="background:${seqVar(i + 1)}"></span>`).join('');
  container.innerHTML =
    `<span class="lg-label">${label}${unit ? ` <span class="muted">(${unit})</span>` : ''}</span>` +
    `<span class="lg-ramp"><span class="mono lg-end">${fmt.n1(min)}</span>` +
    `<span class="swatches">${steps}</span>` +
    `<span class="mono lg-end">${fmt.n1(max)}</span></span>` +
    `<span class="lg-nodata"><span class="sw hatch"></span>${noDataLabel}</span>`;
}

/** Stretch the ramp over the range actually present, so a measure that occupies
 *  a narrow band still reads. Returns whole numbers, because the legend prints
 *  them and a legend that says 38.7194 is noise. */
export function domainOf(rows, key) {
  const vals = rows.map(r => +r[key]).filter(v => isFinite(v) && v > 0);
  if (!vals.length) return { min: 0, max: 100 };
  const lo = Math.floor(Math.min(...vals)), hi = Math.ceil(Math.max(...vals));
  return { min: lo, max: hi > lo ? hi : lo + 1 };
}
