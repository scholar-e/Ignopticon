// Navigation for the ops page: routes from you to a fire over roads and trails that avoid the active fire edge
// and keep radio/phone coverage near the fire. Everything runs on the device from the fire's navigation package
// (/data/nav/FIRE.json, scripts/nav_prep.py), cached by the service worker, so routing works without signal.
//
// Rules (agreed design, see memory/PLAN.md):
// - Travel through the black (inside the perimeter) is allowed; nothing passes within 500 m of a VIIRS hotspot from
//   the last 12 h (the active edge); the +6 h forecast front band costs 4x its travel time.
// - Coverage counts only within 5 km of the perimeter: minutes in "none" count fully, "marginal" half.
//   Whole-route minutes without coverage are still reported.
// - Two routes: Fastest, and Best coverage = least time without coverage among routes at most 1.5x the fastest
//   (found by sweeping a time/exposure trade-off). A loaded dispatch route is a third, estimated option.
// - Drive then walk by default (switching costs 1 min to park); up to 1 km off-trail at each end at 60% of
//   Tobler's hiking pace; restricted roads allowed with warnings; rough 4x4 tracks only when allowed.
// - Starts outside the routing area: an online road route toward the fire (/api/longroute) is joined to the
//   on-device search, which may leave it at any of its junctions more than 10 km from the fire.

const CELL_COVERED = 2, CELL_MARGINAL = 1, NO_DATA = 255;
const NEAR_FIRE_M = 5000, HOT_BUFFER_M = 500, BAND_PENALTY = 4, PARK_S = 60, XC_MAX_M = 1000, XC_FACTOR = 0.6;
const F_WALK = 1, F_PRIVATE = 2, F_GATE = 4, F_4X4 = 8, F_TRAIL = 16, F_ONEWAY = 32;
const LAMBDAS = [0, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64];
const JOIN_FIRE_M = 10000, JOIN_SNAP_M = 200;
const PRESETS = {
  vhf: {name: "NIRSC portable VHF repeater", freq: 168, watts: 5, mast: 6},
  uhf: {name: "NIRSC UHF repeater", freq: 410, watts: 5, mast: 6},
  vehicle: {name: "Vehicle repeater", freq: 155, watts: 25, mast: 2},
  custom: {name: "Custom repeater", freq: 160, watts: 5, mast: 6},
};
const RADIO = {covered: -105, marginal: -115};  // hand-held radio thresholds (dBm)
const ADVISORY = "Advisory routing from satellite data up to 12 h old and estimated coverage. Follow incident assignments and your supervisor's instructions.";

function decode(s, T) {
  const bin = atob(s), b = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i);
  return new T(b.buffer);
}
const metres = (lon0, lat0, lon1, lat1) => {
  const p0 = lat0 * Math.PI / 180, p1 = lat1 * Math.PI / 180, dl = (lon1 - lon0) * Math.PI / 180;
  const a = Math.sin((p1 - p0) / 2) ** 2 + Math.cos(p0) * Math.cos(p1) * Math.sin(dl / 2) ** 2;
  return 12742000 * Math.asin(Math.sqrt(Math.min(1, a)));
};
const tobler = (slope) => 6000 * Math.exp(-3.5 * Math.abs(Math.max(-1, Math.min(1, slope)) + 0.05)) / 3600;  // m/s, slope capped at 45 degrees
const fmtMin = (s) => s < 3600 ? `${Math.max(1, Math.round(s / 60))} min` : `${Math.floor(s / 3600)} h ${Math.round(s % 3600 / 60)} min`;
const fmtMi = (m) => { const v = m / 1609.344; return v < 10 ? v.toFixed(1) + " mi" : Math.round(v) + " mi"; };

// Binary min-heap of (key, value) on typed arrays.
class Heap {
  constructor() { this.k = new Float64Array(1024); this.v = new Int32Array(1024); this.n = 0; }
  push(key, val) {
    if (this.n === this.k.length) { const k = new Float64Array(this.n * 2), v = new Int32Array(this.n * 2); k.set(this.k); v.set(this.v); this.k = k; this.v = v; }
    let i = this.n++;
    while (i > 0) { const p = (i - 1) >> 1; if (this.k[p] <= key) break; this.k[i] = this.k[p]; this.v[i] = this.v[p]; i = p; }
    this.k[i] = key; this.v[i] = val;
  }
  pop() {
    const key = this.k[0], val = this.v[0], lk = this.k[--this.n], lv = this.v[this.n];
    let i = 0;
    for (;;) {
      let c = 2 * i + 1;
      if (c >= this.n) break;
      if (c + 1 < this.n && this.k[c + 1] < this.k[c]) c++;
      if (this.k[c] >= lk) break;
      this.k[i] = this.k[c]; this.v[i] = this.v[c]; i = c;
    }
    this.k[i] = lk; this.v[i] = lv;
    this.topKey = key; return val;
  }
}

export function createNav(ctx) {
  const {$, esc, say} = ctx;
  let pkg = null, pkgFor = null, hz = null, view = null;
  let start = null, startAcc = null, startManual = false, dest = null, destSuggested = false;
  let mode = "both", allow4x4 = false, picking = null, routes = [], chosen = null;
  let navigating = null, offSince = 0, repeaters = [], dispatch = null, refreshTimer = null, longCache = new Map();

  // ---------- Package ----------
  async function load(fire) {
    pkgFor = fire.id; pkg = null; hz = null;
    status(""); note("Loading route data…");
    try {
      const t0 = performance.now();
      const r = await fetch(`/data/nav/${fire.id}.json`);
      if (!r.ok) throw new Error(r.status === 404 ? "missing" : r.statusText);
      const raw = await r.json();
      if (pkgFor !== fire.id) return;
      const g = raw.graph, gr = raw.grid;
      const nodes = decode(g.nodes, Int32Array), lon = new Float64Array(g.n), lat = new Float64Array(g.n);
      for (let i = 0; i < g.n; i++) { lon[i] = nodes[2 * i] / 1e6; lat[i] = nodes[2 * i + 1] / 1e6; }
      pkg = {raw, n: g.n, m: g.m, lon, lat, a: decode(g.a, Uint32Array), b: decode(g.b, Uint32Array), len: decode(g.len, Float32Array),
        drive: decode(g.drive, Uint8Array), flags: decode(g.flags, Uint8Array), walkF: decode(g.walkF, Float32Array), walkB: decode(g.walkB, Float32Array),
        gs: decode(g.geomStart, Uint32Array), geom: decode(g.geom, Int32Array),
        grid: {...gr, elev: decode(gr.elev, Int16Array), cell: decode(gr.cell, Uint8Array)}};
      buildAdjacency();
      repeaters = loadRepeaters();
      const s = performance.now() - t0;
      note(""); status(`Navigation: roads and trails © OpenStreetMap contributors; route data prepared for the ${raw.fire.snapshot.slice(5, 16).replace("T", " ")}Z perimeter; cell coverage SPLAT! estimates from FCC tower sites`);
      if (ctx.getDetail()) prepareHazards(ctx.getDetail());
    } catch (e) {
      if (pkgFor !== fire.id) return;
      note(e.message === "missing" ? "Navigation is not prepared for this fire yet." : `Route data unavailable: ${e.message}`);
    }
    render();
  }

  function buildAdjacency() {
    const {n, m, a, b, drive, flags} = pkg, deg = new Uint32Array(n + 1);
    for (let e = 0; e < m; e++) { deg[a[e] + 1]++; deg[b[e] + 1]++; }
    for (let i = 0; i < n; i++) deg[i + 1] += deg[i];
    const adj = new Int32Array(2 * m), fill = deg.slice(0, n);
    for (let e = 0; e < m; e++) { adj[fill[a[e]]++] = e << 1; adj[fill[b[e]]++] = (e << 1) | 1; }
    const drivable = new Uint8Array(n);
    for (let e = 0; e < m; e++) if (drive[e]) { drivable[a[e]] = 1; drivable[b[e]] = 1; }
    // Spatial buckets (0.01 degree) for snapping points to the network.
    const buckets = new Map();
    for (let i = 0; i < n; i++) {
      const k = Math.floor(pkg.lon[i] * 100) * 100000 + Math.floor(pkg.lat[i] * 100);
      let list = buckets.get(k); if (!list) buckets.set(k, list = []); list.push(i);
    }
    Object.assign(pkg, {off: deg, adj, drivable, buckets});
  }
  function near(lon, lat, maxM, pred) {
    const out = [], r = Math.ceil(maxM / 1000) + 1, bx = Math.floor(lon * 100), by = Math.floor(lat * 100);
    for (let dx = -r; dx <= r; dx++) for (let dy = -r; dy <= r; dy++) {
      for (const i of pkg.buckets.get((bx + dx) * 100000 + by + dy) || []) {
        if (pred && !pred(i)) continue;
        const d = metres(lon, lat, pkg.lon[i], pkg.lat[i]);
        if (d <= maxM) out.push([i, d]);
      }
    }
    return out.sort((p, q) => p[1] - q[1]);
  }
  const inArea = (lon, lat) => { const [w, s, e, n] = pkg.raw.area; return lon >= w && lon <= e && lat >= s && lat <= n; };

  // ---------- Grid ----------
  function cellIndex(lon, lat) {
    const g = pkg.grid, c = Math.floor((lon - g.west) / g.dlon), r = Math.floor((g.north - lat) / g.dlat);
    return c < 0 || r < 0 || c >= g.w || r >= g.h ? -1 : r * g.w + c;
  }
  const elevAt = (lon, lat) => { const k = cellIndex(lon, lat); return k < 0 ? 0 : pkg.grid.elev[k]; };
  function rasterize(geometries, mask) {  // even-odd fill of polygons into a grid mask (cell centres)
    const g = pkg.grid, rings = [];
    for (const geo of geometries) {
      if (!geo) continue;
      const polys = geo.type === "Polygon" ? [geo.coordinates] : geo.type === "MultiPolygon" ? geo.coordinates : [];
      for (const p of polys) for (const ring of p) rings.push(ring);
    }
    for (let r = 0; r < g.h; r++) {
      const y = g.north - (r + 0.5) * g.dlat, xs = [];
      for (const ring of rings) for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [x0, y0] = ring[j], [x1, y1] = ring[i];
        if ((y0 > y) !== (y1 > y)) xs.push(x0 + (y - y0) * (x1 - x0) / (y1 - y0));
      }
      xs.sort((p, q) => p - q);
      for (let k = 0; k + 1 < xs.length; k += 2) {
        const c0 = Math.max(0, Math.ceil((xs[k] - g.west) / g.dlon - 0.5)), c1 = Math.min(g.w - 1, Math.floor((xs[k + 1] - g.west) / g.dlon - 0.5));
        for (let c = c0; c <= c1; c++) mask[r * g.w + c] = 1;
      }
    }
    return mask;
  }
  function distanceTo(mask) {  // chamfer distance (m) from cells where mask=1
    const g = pkg.grid, dx = g.dlon * 111320 * Math.cos((g.north - g.h * g.dlat / 2) * Math.PI / 180), dy = g.dlat * 110540, dd = Math.hypot(dx, dy);
    const D = new Float32Array(g.w * g.h).fill(1e9);
    for (let i = 0; i < D.length; i++) if (mask[i]) D[i] = 0;
    for (let r = 0; r < g.h; r++) for (let c = 0; c < g.w; c++) {
      const i = r * g.w + c; let v = D[i];
      if (c > 0) v = Math.min(v, D[i - 1] + dx);
      if (r > 0) { v = Math.min(v, D[i - g.w] + dy); if (c > 0) v = Math.min(v, D[i - g.w - 1] + dd); if (c < g.w - 1) v = Math.min(v, D[i - g.w + 1] + dd); }
      D[i] = v;
    }
    for (let r = g.h - 1; r >= 0; r--) for (let c = g.w - 1; c >= 0; c--) {
      const i = r * g.w + c; let v = D[i];
      if (c < g.w - 1) v = Math.min(v, D[i + 1] + dx);
      if (r < g.h - 1) { v = Math.min(v, D[i + g.w] + dy); if (c < g.w - 1) v = Math.min(v, D[i + g.w + 1] + dd); if (c > 0) v = Math.min(v, D[i + g.w - 1] + dd); }
      D[i] = v;
    }
    return D;
  }

  // ---------- Hazards and coverage on the grid, then per network segment ----------
  function prepareHazards(d) {
    if (!pkg) return;
    const g = pkg.grid, N = g.w * g.h, fire = ctx.getFire();
    const inside = rasterize([fire && fire.geometry], new Uint8Array(N));
    const toFire = distanceTo(inside);
    const blocked = new Uint8Array(N), band = new Uint8Array(N);
    const rCells = Math.ceil(HOT_BUFFER_M / (g.dlat * 110540)) + 1;
    for (const h of d.hotspots || []) {
      const k = cellIndex(h[0], h[1]); if (k < 0) continue;
      const r0 = Math.floor(k / g.w), c0 = k % g.w;
      for (let dr = -rCells; dr <= rCells; dr++) for (let dc = -rCells * 2; dc <= rCells * 2; dc++) {
        const r = r0 + dr, c = c0 + dc;
        if (r < 0 || c < 0 || r >= g.h || c >= g.w) continue;
        const lon = g.west + (c + 0.5) * g.dlon, lat = g.north - (r + 0.5) * g.dlat;
        if (metres(h[0], h[1], lon, lat) <= HOT_BUFFER_M) blocked[r * g.w + c] = 1;
      }
    }
    const fc = d.forecast;
    if (fc && fc.features) rasterize(fc.features.filter((x) => x.properties.kind === "band" && x.properties.hours <= 6 && (x.properties.where || "front") === "front").map((x) => x.geometry), band);
    hz = {inside, toFire, blocked, band, hotspots: (d.hotspots || []).length, snapshot: d.snapshot};
    applyCoverage();
  }
  function coverageGrid() {  // the best class per cell across cell towers and the user's repeaters
    const cls = pkg.grid.cell.slice();
    for (const r of repeaters) if (r.grid) for (let i = 0; i < cls.length; i++) if (r.grid[i] !== NO_DATA && r.grid[i] > cls[i]) cls[i] = r.grid[i];
    return cls;
  }
  function applyCoverage() {
    if (!pkg || !hz) return;
    const cls = coverageGrid(), {m, gs, geom} = pkg;
    const blockedE = new Uint8Array(m), bandF = new Float32Array(m), noneF = new Float32Array(m), margF = new Float32Array(m);
    const noneAll = new Float32Array(m);
    for (let e = 0; e < m; e++) {
      let samples = 0, bandN = 0, noneN = 0, margN = 0, noneAllN = 0, blockedN = 0;
      for (let p = gs[e]; p < gs[e + 1] - 1; p++) {
        const x0 = geom[2 * p] / 1e6, y0 = geom[2 * p + 1] / 1e6, x1 = geom[2 * p + 2] / 1e6, y1 = geom[2 * p + 3] / 1e6;
        const steps = Math.max(1, Math.ceil(metres(x0, y0, x1, y1) / 75));
        for (let s = 0; s < steps; s++) {
          const k = cellIndex(x0 + (x1 - x0) * s / steps, y0 + (y1 - y0) * s / steps);
          samples++;
          if (k < 0) continue;
          if (hz.blocked[k]) blockedN++;
          if (hz.band[k]) bandN++;
          if (cls[k] === 0) noneAllN++;
          if (hz.toFire[k] <= NEAR_FIRE_M) { if (cls[k] === 0) noneN++; else if (cls[k] === CELL_MARGINAL) margN++; }
        }
      }
      blockedE[e] = blockedN > 0 ? 1 : 0;
      bandF[e] = bandN / samples; noneF[e] = noneN / samples; margF[e] = margN / samples; noneAll[e] = noneAllN / samples;
    }
    Object.assign(hz, {cls, blockedE, bandF, noneF, margF, noneAll});
    drawCoverage();
    if (routes.length) findRoutes(true);
  }

  // ---------- Routing ----------
  function xcTime(lon0, lat0, lon1, lat1) {
    const d = metres(lon0, lat0, lon1, lat1);
    return d === 0 ? 0 : d / (tobler((elevAt(lon1, lat1) - elevAt(lon0, lat0)) / d) * XC_FACTOR);
  }
  function edgeTime(e, dir, drive) {
    if (drive) return pkg.len[e] / (pkg.drive[e] / 3.6);
    return dir ? pkg.walkB[e] : pkg.walkF[e];
  }
  function canUse(e, dir, drive) {
    const f = pkg.flags[e];
    if (hz && hz.blockedE[e]) return false;
    if (drive) return pkg.drive[e] > 0 && !(dir && f & F_ONEWAY) && (allow4x4 || !(f & F_4X4));
    return (f & F_WALK) !== 0;
  }
  /** Least-cost route from `from` to `to` (or, with to = null, a full search for the suggestion). */
  function search(from, to, lambda, long) {
    const {n, off, adj, a, b} = pkg, S = 2 * n;
    const cost = new Float64Array(S).fill(Infinity), prev = new Int32Array(S).fill(-1), via = new Int32Array(S).fill(-1);
    const heap = new Heap(), driveOK = mode !== "walk", walkOK = mode !== "drive";
    if (long) {  // arriving by car along the long route: start driving at any of its junctions
      for (const [i, j] of long.joins) { const s = 2 * i; cost[s] = j.t; via[s] = -3; heap.push(j.t, s); }
    }
    const seeds = long ? [] : near(from[0], from[1], XC_MAX_M);
    if (!long && !seeds.length) return null;
    for (const [i, d] of seeds.slice(0, 12)) {
      const t = xcTime(from[0], from[1], pkg.lon[i], pkg.lat[i]);
      if (driveOK && pkg.drivable[i] && d <= 150) { const s = 2 * i; if (t < cost[s]) { cost[s] = t; via[s] = -3; heap.push(t, s); } }
      if (walkOK) { const s = 2 * i + 1; if (t < cost[s]) { cost[s] = t; via[s] = -3; heap.push(t, s); } }
      if (!walkOK && driveOK && pkg.drivable[i]) { const s = 2 * i; if (t < cost[s]) { cost[s] = t; via[s] = -3; heap.push(t, s); } }
    }
    const targets = to ? new Map(near(to[0], to[1], XC_MAX_M).map(([i]) => [i, xcTime(pkg.lon[i], pkg.lat[i], to[0], to[1])])) : null;
    let best = Infinity, bestS = -1;
    while (heap.n) {
      const s = heap.pop(), c = heap.topKey;
      if (c > cost[s] || c >= best) { if (c >= best) break; continue; }
      const u = s >> 1, drive = (s & 1) === 0;
      if (targets && targets.has(u)) { const total = c + targets.get(u); if (total < best) { best = total; bestS = s; } }
      if (drive && walkOK) { const t = s + 1, nc = c + PARK_S; if (nc < cost[t]) { cost[t] = nc; prev[t] = s; via[t] = -2; heap.push(nc, t); } }
      for (let k = off[u]; k < off[u + 1]; k++) {
        const e = adj[k] >> 1, dir = adj[k] & 1;
        if (!canUse(e, dir, drive)) continue;
        const v = dir ? a[e] : b[e], t = edgeTime(e, dir, drive);
        const exposure = t * (hz ? hz.noneF[e] + 0.5 * hz.margF[e] : 0);
        const nc = c + t * (1 + (hz ? (BAND_PENALTY - 1) * hz.bandF[e] : 0)) + lambda * exposure;
        const ns = 2 * v + (drive ? 0 : 1);
        if (nc < cost[ns]) { cost[ns] = nc; prev[ns] = s; via[ns] = e << 1 | dir; heap.push(nc, ns); }
      }
    }
    return {cost, prev, via, bestS};
  }
  function trace(res, from, to, long) {
    if (!res || res.bestS < 0) return null;
    const legs = [], pts = (e, dir) => {
      const out = [];
      for (let p = pkg.gs[e]; p < pkg.gs[e + 1]; p++) out.push([pkg.geom[2 * p] / 1e6, pkg.geom[2 * p + 1] / 1e6]);
      return dir ? out.reverse() : out;
    };
    let s = res.bestS, time = 0, dist = 0, none = 0, marg = 0, noneAll = 0, gates = 0, restricted = 0, trail = 0, rough = 0, band = 0;
    const steps = [];
    while (s >= 0 && res.via[s] !== -3) {
      const v = res.via[s];
      if (v >= 0) steps.push({e: v >> 1, dir: v & 1, drive: (s & 1) === 0});
      s = res.prev[s];
    }
    steps.reverse();
    const first = s >= 0 ? s >> 1 : null, last = res.bestS >> 1;
    const add = (mode, coords) => { const L = legs[legs.length - 1]; if (L && L.mode === mode) L.coords.push(...coords.slice(1)); else legs.push({mode, coords}); };
    let road = null;
    if (first !== null && long) {  // the online road route up to the junction where the search took over
      const j = long.joins.get(first);
      road = {dist: long.cum[j.vi] + j.d, time: j.t, provider: long.provider};
      add("road", long.coords.slice(0, j.vi + 1).concat([[pkg.lon[first], pkg.lat[first]]]));
      time += j.t; dist += road.dist;
    } else if (first !== null) {
      const t = xcTime(from[0], from[1], pkg.lon[first], pkg.lat[first]), d = metres(from[0], from[1], pkg.lon[first], pkg.lat[first]);
      if (d > 15) add("xc", [from, [pkg.lon[first], pkg.lat[first]]]);
      time += t; dist += d;
    }
    for (const {e, dir, drive} of steps) {
      const t = edgeTime(e, dir, drive), f = pkg.flags[e];
      time += t; dist += pkg.len[e];
      none += t * hz.noneF[e]; marg += t * hz.margF[e]; noneAll += t * hz.noneAll[e];
      if (hz.bandF[e] > 0) band++;
      if (f & F_GATE) gates++;
      if (f & F_PRIVATE && drive) restricted++;
      if (f & F_4X4 && drive) rough++;
      if (f & F_TRAIL && !drive) trail += pkg.len[e];
      add(drive ? "drive" : "walk", pts(e, dir));
    }
    const dEnd = metres(pkg.lon[last], pkg.lat[last], to[0], to[1]);
    if (dEnd > 15) add("xc", [[pkg.lon[last], pkg.lat[last]], to]);
    time += xcTime(pkg.lon[last], pkg.lat[last], to[0], to[1]); dist += dEnd;
    const xc = legs.filter((L) => L.mode === "xc").reduce((s2, L) => s2 + metres(...L.coords[0], ...L.coords[L.coords.length - 1]), 0);
    return {legs, time, dist, none, marg, noneAll, gates, restricted, rough, trail, xc, band, road,
      keyCost: none + 0.5 * marg, coords: legs.flatMap((L, i) => i ? L.coords.slice(1) : L.coords)};
  }

  function suggestDestination(from, long) {
    const res = search(from, null, 0, long);
    if (!res) return null;
    let best = Infinity, node = -1;
    for (let i = 0; i < pkg.n; i++) {
      const c = Math.min(res.cost[2 * i], res.cost[2 * i + 1]);
      if (!(c < best)) continue;
      const k = cellIndex(pkg.lon[i], pkg.lat[i]);
      if (k < 0 || hz.inside[k] || hz.toFire[k] < 500 || hz.toFire[k] > 2500 || hz.band[k] || hz.blocked[k]) continue;
      best = c; node = i;
    }
    return node < 0 ? null : [pkg.lon[node], pkg.lat[node]];
  }

  function evaluateDispatch() {
    if (!dispatch || !hz) return null;
    const c = dispatch.geometry.coordinates;
    let dist = 0, none = 0, marg = 0, noneAll = 0, blocked = false;
    for (let i = 1; i < c.length; i++) {
      const d = metres(...c[i - 1], ...c[i]), t = d / (30 / 3.6), k = cellIndex(c[i][0], c[i][1]);
      dist += d;
      if (k >= 0) {
        if (hz.blocked[k]) blocked = true;
        if (hz.cls[k] === 0) noneAll += t;
        if (hz.toFire[k] <= NEAR_FIRE_M) { if (hz.cls[k] === 0) none += t; else if (hz.cls[k] === CELL_MARGINAL) marg += t; }
      }
    }
    return {id: "dispatch", label: "Dispatch route", estimated: true, legs: [{mode: "dispatch", coords: c}], coords: c,
      time: dist / (30 / 3.6), dist, none, marg, noneAll, blocked, gates: 0, restricted: 0, rough: 0, trail: 0, xc: 0, band: 0};
  }

  async function findRoutes(quiet) {
    if (!pkg || !hz) return say && say("Route data is still loading");
    if (!start) return note("Set a start point first (your location, or Set on map).");
    let long = null;
    if (!inArea(...start)) {
      if (!quiet) note("Finding a road route to the fire area…");
      long = await longRoute();
      if (!long) return handOff();
    }
    if (!dest) {
      dest = suggestDestination(start, long); destSuggested = true;
      if (!dest) return note("No reachable point found 0.5–2.5 km outside the fire. Pick a destination on the map.");
    }
    const t0 = performance.now(), results = [];
    for (const lambda of LAMBDAS) { const r = trace(search(start, dest, lambda, long), start, dest, long); if (r) results.push(r); }
    if (!results.length) { routes = []; chosen = null; render(); return note("No route found within the routing area. Try Drive + walk, allow 4×4 roads, or move the destination."); }
    const fastest = results[0], cap = fastest.time * 1.5;
    const best = results.filter((r) => r.time <= cap).reduce((p, q) => q.keyCost < p.keyCost - 1e-6 ? q : p, fastest);
    routes = [{...fastest, id: "fast", label: "Fastest"}];
    if (fastest.keyCost - best.keyCost >= 60 && best !== fastest) routes.push({...best, id: "cover", label: "Best coverage"});
    const dr = evaluateDispatch(); if (dr) routes.push(dr);
    chosen = chosen && routes.find((r) => r.id === chosen.id) || routes[0];
    note(routes.length === 1 + (dr ? 1 : 0) && !quiet ? "The fastest route already has the best coverage available (within 1 min)." : "");
    performance.mark("nav-routed"); console.log(`routes in ${Math.round(performance.now() - t0)} ms`);
    render();
    if (!quiet) { const f = ctx.getFire(), b = bboxOf(chosen.coords.concat([start, dest])); ctx.fit(f ? [Math.min(b[0], f.bbox[0]), Math.min(b[1], f.bbox[1]), Math.max(b[2], f.bbox[2]), Math.max(b[3], f.bbox[3])] : b); }
  }
  /** The online road route from the start toward the destination (or the fire), and the junctions where the
   *  on-device search may take over: points inside the area more than 10 km from the fire, snapped to a road. */
  async function longRoute() {
    const f = ctx.getFire(), to = dest || (f ? [(f.bbox[0] + f.bbox[2]) / 2, (f.bbox[1] + f.bbox[3]) / 2] : null);
    if (!to) return null;
    const key = [...start, ...to].map((v) => v.toFixed(3)).join(",") + pkgFor;
    if (!longCache.has(key)) {
      longCache.set(key, fetch(`/api/longroute?from=${start.join(",")}&to=${to.join(",")}`)
        .then((r) => r.ok ? r.json() : r.json().then((d) => { throw new Error(d.error || r.statusText); }))
        .catch((e) => { longCache.delete(key); console.warn("long route:", e.message); return null; }));
    }
    let r = await longCache.get(key);
    const saveKey = "ops.longroute." + pkgFor;
    if (r) { try { localStorage.setItem(saveKey, JSON.stringify(r)); } catch {} }
    else r = rejoinSaved(saveKey);
    if (!r || !hz) return null;
    const joins = new Map(), cum = cumulative(r.coords);
    r.coords.forEach(([x, y], vi) => {
      const k = cellIndex(x, y);
      if (!inArea(x, y) || k < 0 || hz.toFire[k] < JOIN_FIRE_M) return;
      const hit = near(x, y, JOIN_SNAP_M, (i) => pkg.drivable[i])[0]; if (!hit) return;
      const t = r.t[vi] + hit[1] / (30 / 3.6), old = joins.get(hit[0]);
      if (!old || t < old.t) joins.set(hit[0], {vi, t, d: hit[1]});
    });
    return joins.size ? {...r, cum, joins} : null;
  }
  /** Offline: the last long route for this fire (kept across reloads), from its point nearest the start, if the
   *  start is within 2 km of it. */
  function rejoinSaved(saveKey) {
    let r; try { r = JSON.parse(localStorage.getItem(saveKey)); } catch {}
    if (!r || !r.coords) return null;
    let best = Infinity, vi = -1;
    r.coords.forEach(([x, y], i) => { const d = metres(start[0], start[1], x, y); if (d < best) { best = d; vi = i; } });
    if (best > 2000) return null;
    const t0 = best / (30 / 3.6) - r.t[vi];
    return {...r, provider: r.provider + " (saved)", coords: [start, ...r.coords.slice(vi)], t: [0, ...r.t.slice(vi).map((v) => v + t0)]};
  }
  const bboxOf = (cs) => cs.reduce((b, [x, y]) => [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)], [Infinity, Infinity, -Infinity, -Infinity]);
  function handOff() {
    const entry = (() => {  // the drivable junction nearest to the start
      let best = Infinity, i0 = -1;
      for (let i = 0; i < pkg.n; i += 1) if (pkg.drivable[i]) { const d = metres(start[0], start[1], pkg.lon[i], pkg.lat[i]); if (d < best) { best = d; i0 = i; } }
      return i0 < 0 ? null : [pkg.lon[i0], pkg.lat[i0]];
    })();
    routes = []; chosen = null; render();
    if (!entry) return note("You are outside this fire's routing area.");
    const url = "https://www.google.com/maps/dir/?" + new URLSearchParams({api: 1, origin: `${start[1]},${start[0]}`, destination: `${entry[1]},${entry[0]}`, travelmode: "driving"});
    note(`You are outside this fire's routing area (about 50 km around it) and no online road route could be found (offline, or too far). <a href="${url}" target="_blank" rel="noopener noreferrer">Drive to the entry point in Google Maps ↗</a>, then switch back here.`, true);
  }

  // ---------- Repeaters ----------
  const repKey = () => "ops.rptr." + pkgFor;
  function loadRepeaters() {
    try { return (JSON.parse(localStorage.getItem(repKey())) || []).map((r) => ({...r, grid: null})).map(estimate); } catch { return []; }
  }
  function saveRepeaters() {
    try { localStorage.setItem(repKey(), JSON.stringify(repeaters.map(({grid, source, ...r}) => r))); } catch {}
  }
  /** Instant in-browser estimate: line of sight over the terrain grid (radial sweep), free-space loss, plus a
   *  shadowing loss where the path is blocked. SPLAT! replaces it when the server can be reached. */
  function estimate(r) {
    const g = pkg.grid, N = g.w * g.h, out = new Uint8Array(N).fill(NO_DATA);
    const k0 = cellIndex(r.lon, r.lat); if (k0 < 0) return {...r, grid: out, source: "outside area"};
    const txH = g.elev[k0] + r.mast, eirp = 10 * Math.log10(r.watts * 1000) + 2.15;
    const mx = g.dlon * 111320 * Math.cos(r.lat * Math.PI / 180), my = g.dlat * 110540, R = 30000, step = Math.min(mx, my);
    const best = new Float32Array(N).fill(-999);
    for (let az = 0; az < 1440; az++) {
      const sa = Math.sin(az * Math.PI / 720), ca = Math.cos(az * Math.PI / 720);
      let maxAng = -Infinity;
      for (let d = step; d <= R; d += step) {
        const c = Math.floor((r.lon - g.west) / g.dlon + d * sa / mx), rr = Math.floor((g.north - r.lat) / g.dlat - d * ca / my);
        if (c < 0 || rr < 0 || c >= g.w || rr >= g.h) break;
        const k = rr * g.w + c, curve = d * d / (2 * 8.5e6), ground = g.elev[k] - curve;
        const angRx = (ground + 1.5 - txH) / d, angG = (ground - txH) / d;
        const fspl = 32.45 + 20 * Math.log10(r.freq) + 20 * Math.log10(d / 1000);
        const shadow = angRx >= maxAng ? 0 : 20 + 40 * Math.min(1, (maxAng - angRx) / 0.05);
        const p = eirp - fspl - shadow;
        if (p > best[k]) best[k] = p;
        if (angG > maxAng) maxAng = angG;
      }
    }
    for (let i = 0; i < N; i++) if (best[i] > -999) out[i] = best[i] >= RADIO.covered ? 2 : best[i] >= RADIO.marginal ? 1 : 0;
    return {...r, grid: out, source: "estimate"};
  }
  async function refine(r) {
    const g = pkg.grid, q = new URLSearchParams({lat: r.lat, lon: r.lon, height: r.mast, freq: r.freq, erp: r.watts, radius: 30,
      west: g.west, north: g.north, dlon: g.dlon, dlat: g.dlat, w: g.w, h: g.h});
    try {
      const res = await fetch(`/api/coverage?${q}`);
      if (!res.ok) throw new Error((await res.json()).error);
      const dbm = decode((await res.json()).dbm, Int8Array), out = new Uint8Array(dbm.length).fill(NO_DATA);
      for (let i = 0; i < dbm.length; i++) if (dbm[i] > -128) out[i] = dbm[i] >= RADIO.covered ? 2 : dbm[i] >= RADIO.marginal ? 1 : 0;
      const live = repeaters.find((x) => x.id === r.id); if (!live) return;
      live.grid = out; live.source = "SPLAT!"; applyCoverage(); render();
    } catch (e) { const live = repeaters.find((x) => x.id === r.id); if (live) { live.source = "estimate (SPLAT! unavailable offline)"; render(); } }
  }
  function addRepeater(lon, lat) {
    const p = PRESETS[$("#nav-preset").value] || PRESETS.vhf;
    const r = estimate({id: Date.now(), name: $("#nav-rname").value.trim() || p.name, lon, lat,
      mast: +$("#nav-mast").value || p.mast, freq: +$("#nav-freq").value || p.freq, watts: +$("#nav-watts").value || p.watts});
    repeaters.push(r); saveRepeaters(); applyCoverage(); render(); refine(r);
  }

  // ---------- Map ----------
  function hatch(lines, color) {
    const size = 16, c = document.createElement("canvas"); c.width = c.height = size;
    const g = c.getContext("2d"); g.strokeStyle = color; g.lineWidth = 1.6;
    for (const dir of lines) for (let o = -size; o <= size * 2; o += 8) {
      g.beginPath();
      if (dir > 0) { g.moveTo(o, size); g.lineTo(o + size, 0); } else { g.moveTo(o, 0); g.lineTo(o + size, size); }
      g.stroke();
    }
    return g.getImageData(0, 0, size, size);
  }
  function styleLoaded() {
    const map = ctx.getMap();
    if (!map.hasImage("nav-none")) map.addImage("nav-none", hatch([1, -1], "rgba(20,22,26,0.85)"), {pixelRatio: 2});
    if (!map.hasImage("nav-marg")) map.addImage("nav-marg", hatch([1], "rgba(40,44,52,0.6)"), {pixelRatio: 2});
    const empty = {type: "FeatureCollection", features: []};
    for (const id of ["nav-cov", "nav-routes", "nav-pts", "nav-rptr"]) if (!map.getSource(id)) map.addSource(id, {type: "geojson", data: empty});
    const add = (layer, before) => { if (!map.getLayer(layer.id)) map.addLayer(layer, before); };
    add({id: "nav-cov-fill", type: "fill", source: "nav-cov", paint: {"fill-pattern": ["match", ["get", "c"], 0, "nav-none", "nav-marg"]}, layout: {visibility: "none"}}, "fires-fill");
    add({id: "nav-route-case", type: "line", source: "nav-routes", layout: {"line-join": "round", "line-cap": "round"},
      paint: {"line-color": "#0B0D10", "line-width": ["case", ["get", "on"], 9, 6], "line-opacity": ["case", ["get", "on"], 0.9, 0.5]}});
    add({id: "nav-route-line", type: "line", source: "nav-routes", layout: {"line-join": "round", "line-cap": "round"},
      paint: {"line-color": ["match", ["get", "id"], "fast", "#4FA3FF", "cover", "#3DDC84", "#5FE0D4"],
        "line-width": ["case", ["get", "on"], 5.5, 3], "line-opacity": ["case", ["get", "on"], 1, 0.6],
        "line-dasharray": ["match", ["get", "mode"], "walk", ["literal", [1.4, 1.2]], "xc", ["literal", [0.2, 1.6]], "dispatch", ["literal", [3, 1.5]], ["literal", [1, 0]]]}});
    add({id: "nav-pts", type: "circle", source: "nav-pts", paint: {"circle-radius": 8, "circle-color": ["match", ["get", "k"], "start", "#4FA3FF", "#FFFFFF"],
      "circle-stroke-color": ["match", ["get", "k"], "dest", "#3DDC84", "#0B0D10"], "circle-stroke-width": 3}});
    add({id: "nav-rptr", type: "symbol", source: "nav-rptr", layout: {"icon-image": "nav-tower", "icon-allow-overlap": true, "text-field": ["get", "name"],
      "text-font": ["Noto Sans Bold"], "text-size": 12, "text-offset": [0, 1.4], "text-anchor": "top"}, paint: {"text-color": "#fff", "text-halo-color": "#0B0D10", "text-halo-width": 1.5}});
    if (!map.hasImage("nav-tower")) {
      const c = document.createElement("canvas"); c.width = c.height = 40; const g = c.getContext("2d");
      g.strokeStyle = "#0B0D10"; g.fillStyle = "#FFFFFF"; g.lineWidth = 3;
      g.beginPath(); g.moveTo(20, 6); g.lineTo(30, 34); g.lineTo(10, 34); g.closePath(); g.stroke(); g.fill();
      g.beginPath(); g.arc(20, 8, 4, 0, 7); g.fillStyle = "#3DDC84"; g.fill(); g.stroke();
      map.addImage("nav-tower", g.getImageData(0, 0, 40, 40), {pixelRatio: 2});
    }
    drawCoverage(); drawRoutes();
  }
  function drawCoverage() {  // run-length rectangles of "none" and "marginal" cells within 5 km of the fire
    const map = ctx.getMap(); if (!map || !map.getSource("nav-cov") || !hz) return;
    const g = pkg.grid, polys = [[], []];
    for (let r = 0; r < g.h; r++) {
      const n0 = g.north - r * g.dlat, n1 = n0 - g.dlat;
      let c = 0;
      while (c < g.w) {
        const k = r * g.w + c, cl = hz.cls[k];
        if (hz.toFire[k] > NEAR_FIRE_M || cl > CELL_MARGINAL) { c++; continue; }
        let e = c + 1;
        while (e < g.w && hz.cls[r * g.w + e] === cl && hz.toFire[r * g.w + e] <= NEAR_FIRE_M) e++;
        const w0 = g.west + c * g.dlon, w1 = g.west + e * g.dlon;
        polys[cl].push([[[w0, n0], [w1, n0], [w1, n1], [w0, n1], [w0, n0]]]);
        c = e;
      }
    }
    map.getSource("nav-cov").setData({type: "FeatureCollection", features: polys.map((p, c) => ({type: "Feature", properties: {c}, geometry: {type: "MultiPolygon", coordinates: p}}))});
    showCoverage(navigating || $("#nav-cov-toggle")?.getAttribute("aria-pressed") === "true");
  }
  function showCoverage(on) {
    const map = ctx.getMap(); if (map && map.getLayer("nav-cov-fill")) map.setLayoutProperty("nav-cov-fill", "visibility", on ? "visible" : "none");
    $("#nav-cov-toggle")?.setAttribute("aria-pressed", on);
  }
  function drawRoutes() {
    const map = ctx.getMap(); if (!map || !map.getSource("nav-routes")) return;
    const feats = [];
    for (const r of routes) for (const L of r.legs) feats.push({type: "Feature", properties: {id: r.id, mode: L.mode, on: r === chosen},
      geometry: {type: "LineString", coordinates: L.coords}});
    feats.sort((p, q) => p.properties.on - q.properties.on);
    map.getSource("nav-routes").setData({type: "FeatureCollection", features: feats});
    const pts = [];
    if (start) pts.push({type: "Feature", properties: {k: "start"}, geometry: {type: "Point", coordinates: start}});
    if (dest) pts.push({type: "Feature", properties: {k: "dest"}, geometry: {type: "Point", coordinates: dest}});
    map.getSource("nav-pts").setData({type: "FeatureCollection", features: pts});
    map.getSource("nav-rptr").setData({type: "FeatureCollection", features: repeaters.map((r) => ({type: "Feature", properties: {name: r.name}, geometry: {type: "Point", coordinates: [r.lon, r.lat]}}))});
    map.getSource("access")?.setData(dispatch || {type: "FeatureCollection", features: []});
  }

  // ---------- Panel ----------
  // Where the route data comes from: listed under Data sources in the fire card (#nav-src).
  let sourceText = "";
  const status = (msg) => { sourceText = msg; const el = $("#nav-src"); if (el) { el.textContent = msg; el.hidden = !msg; } };
  function note(msg, html) { const el = $("#nav-note"); if (!el) return; if (html) el.innerHTML = msg; else el.textContent = msg; }
  function render() {
    const p = $("#navpanel"); if (!p) return;
    const fromIn = $("#nav-from-in"), toIn = $("#nav-to-in");
    if (document.activeElement !== fromIn) fromIn.value = picking === "start" ? "Tap the map…" : !start ? (startManual ? "" : "Current location (waiting for GPS)") :
      startManual ? `${start[1].toFixed(5)}, ${start[0].toFixed(5)}` : `Current location (±${Math.round(startAcc || 0)} m)`;
    if (document.activeElement !== toIn) toIn.value = picking === "dest" ? "Tap the map…" : !dest || destSuggested ? "Recommended spot (just outside the fire)" : `${dest[1].toFixed(5)}, ${dest[0].toFixed(5)}`;
    for (const b of document.querySelectorAll("[data-navmode]")) b.setAttribute("aria-pressed", b.dataset.navmode === mode);
    $("#nav-4x4").checked = allow4x4;
    $("#nav-add-rptr").setAttribute("aria-pressed", picking === "repeater");
    $("#nav-rform").hidden = picking !== "repeater";
    $("#nav-repeaters").innerHTML = repeaters.map((r) => `<li><span>${esc(r.name)} · ${r.mast} m · ${r.freq} MHz · ${r.watts} W <small>(${esc(r.source || "")})</small></span><button type="button" data-delrptr="${r.id}" aria-label="Remove ${esc(r.name)}">Remove</button></li>`).join("");
    $("#nav-routes").innerHTML = routes.map((r) => {
      const warn = [r.gates && `${r.gates} gate${r.gates > 1 ? "s" : ""} (may be locked)`, r.restricted && "private or restricted road", r.rough && "rough 4×4 track",
        r.xc > 15 && `${fmtMi(r.xc)} off-trail`, r.band && "crosses the +6 h forecast area", r.blocked && "passes near the active fire edge", r.estimated && "time estimated at 30 km/h",
        r.road && `first ${fmtMi(r.road.dist)} on a ${r.road.provider} road route (no fire or coverage check there)`].filter(Boolean);
      return `<li><button type="button" class="rt rt-${r.id}" data-route="${r.id}" aria-pressed="${r === chosen}">
<span class="rt-head"><b>${r.label}</b><span>${fmtMin(r.time)} · ${fmtMi(r.dist)}</span></span>
<span class="rt-cov">${fmtMin(r.none + r.marg)} without coverage near the fire${r.marg >= 30 ? ` (${fmtMin(r.marg)} marginal)` : ""} · ${fmtMin(r.noneAll)} ${r.road ? "in the fire area" : "on the whole trip"}</span>
${warn.length ? `<span class="rt-warn">${warn.join(" · ")}</span>` : ""}</button></li>`;
    }).join("");
    $("#nav-start").hidden = !chosen || !!navigating;
    drawRoutes();
  }

  // ---------- Navigating ----------
  function startNav() {
    if (!chosen) return;
    alertBox(ADVISORY, [["OK", () => { alertBox(); }]]);
    navigating = {route: chosen, startedAt: Date.now(), cum: cumulative(chosen.coords)};
    showCoverage(true); $("#navbar").hidden = false; render(); updateNav();
    clearInterval(refreshTimer); refreshTimer = setInterval(checkFireData, 10 * 60e3);
    say(`Navigating: ${chosen.label}, ${fmtMin(chosen.time)}`);
  }
  function endNav() {
    navigating = null; clearInterval(refreshTimer); $("#navbar").hidden = true; alertBox(); offSince = 0;
    showCoverage($("#nav-cov-toggle").getAttribute("aria-pressed") === "true"); render();
  }
  function cumulative(cs) { const out = [0]; for (let i = 1; i < cs.length; i++) out.push(out[i - 1] + metres(...cs[i - 1], ...cs[i])); return out; }
  function updateNav() {
    if (!navigating || !start) return;
    const {route, cum} = navigating, cs = route.coords;
    let best = Infinity, at = 0;
    for (let i = 0; i < cs.length - 1; i++) {  // nearest point on the route (projected per segment)
      const [x0, y0] = cs[i], [x1, y1] = cs[i + 1], kx = Math.cos(y0 * Math.PI / 180);
      const vx = (x1 - x0) * kx, vy = y1 - y0, wx = (start[0] - x0) * kx, wy = start[1] - y0;
      const t = Math.max(0, Math.min(1, (vx * wx + vy * wy) / (vx * vx + vy * vy || 1)));
      const d = metres(start[0], start[1], x0 + (x1 - x0) * t, y0 + (y1 - y0) * t);
      if (d < best) { best = d; at = cum[i] + t * (cum[i + 1] - cum[i]); }
    }
    const total = cum[cum.length - 1], left = Math.max(0, total - at), frac = total ? left / total : 0;
    let next = "";
    for (let i = 0; i < cs.length; i++) {
      if (cum[i] < at) continue;
      const k = cellIndex(cs[i][0], cs[i][1]);
      if (k >= 0 && hz.toFire[k] <= NEAR_FIRE_M && hz.cls[k] === 0) { next = `No coverage in ${fmtMi(cum[i] - at)}`; break; }
    }
    $("#nb-left").textContent = `${fmtMin(route.time * frac)} · ${fmtMi(left)} left`;
    $("#nb-next").textContent = left < 30 ? "Arrived" : next || "Coverage ahead near the fire";
    if (best > 50) { offSince = offSince || Date.now(); if (Date.now() - offSince > 20000 && !$("#navalert").dataset.kind) alertBox("You're off route.", [["Reroute", reroute], ["Dismiss", () => alertBox()]], "off"); }
    else { offSince = 0; if ($("#navalert").dataset.kind === "off") alertBox(); }
  }
  async function reroute() { alertBox(); const keep = chosen && chosen.id; await findRoutes(true); chosen = routes.find((r) => r.id === keep) || routes[0]; if (navigating && chosen) navigating = {...navigating, route: chosen, cum: cumulative(chosen.coords)}; render(); updateNav(); }
  async function checkFireData() {
    const f = ctx.getFire(); if (!navigating || !f) return;
    try {
      const d = await fetch(`/api/fires/${f.id}${ctx.query()}`).then((r) => r.json());
      if (!d.snapshot || d.snapshot === hz.snapshot) return;
      prepareHazards(d);
      const k = cellIndex(dest[0], dest[1]), hit = navigating.route.coords.some(([x, y]) => { const c = cellIndex(x, y); return c >= 0 && hz.blocked[c]; });
      if (hit || (k >= 0 && (hz.band[k] || hz.blocked[k]))) alertBox("New fire data: the active edge or forecast now affects your route or destination.", [["Reroute", reroute], ["Dismiss", () => alertBox()]], "fire");
    } catch {}
  }
  function alertBox(msg, actions = [], kind = "") {
    const el = $("#navalert");
    if (!msg) { el.hidden = true; el.dataset.kind = ""; return; }
    el.dataset.kind = kind; el.hidden = false;
    el.innerHTML = `<p>${esc(msg)}</p>` + actions.map(([t], i) => `<button type="button" data-act="${i}">${t}</button>`).join("");
    el.onclick = (e) => { const b = e.target.closest("[data-act]"); if (b) actions[+b.dataset.act][1](); };
  }

  // ---------- Wiring ----------
  $("#navpanel").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.navmode) { mode = b.dataset.navmode; render(); if (routes.length) findRoutes(true); }
    else if (b.dataset.from) { menu("from", false); if (b.dataset.from === "gps") { startManual = false; picking = null; ctx.requestGps(); } else picking = "start"; render(); }
    else if (b.dataset.to) { menu("to", false); if (b.dataset.to === "rec") { dest = null; destSuggested = true; picking = null; ctx.saveAccessPoint(null); } else picking = "dest"; render(); }
    else if (b.id === "nav-add-rptr") { picking = picking === "repeater" ? null : "repeater"; render(); }
    else if (b.id === "nav-go") findRoutes(false);
    else if (b.id === "nav-start") startNav();
    else if (b.id === "nav-cov-toggle") showCoverage(b.getAttribute("aria-pressed") !== "true");
    else if (b.dataset.route) { chosen = routes.find((r) => r.id === b.dataset.route); render(); }
    else if (b.dataset.delrptr) { repeaters = repeaters.filter((r) => String(r.id) !== b.dataset.delrptr); saveRepeaters(); applyCoverage(); render(); }
  });
  $("#nav-4x4").addEventListener("change", (e) => { allow4x4 = e.target.checked; if (routes.length) findRoutes(true); });
  $("#nav-preset").addEventListener("change", (e) => { const p = PRESETS[e.target.value]; $("#nav-mast").value = p.mast; $("#nav-freq").value = p.freq; $("#nav-watts").value = p.watts; });
  // From / To: type "lat, lon", or pick from the menu that opens on focus.
  const parseLatLon = (v) => { const m = v.match(/(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)/); return m && Math.abs(+m[1]) <= 90 && Math.abs(+m[2]) <= 180 ? [+m[2], +m[1]] : null; };
  function menu(which, open) {
    $(`#nav-${which}-menu`).hidden = !open;
    $(`#nav-${which}-in`).setAttribute("aria-expanded", String(open));
    if (!open && document.activeElement === $(`#nav-${which}-in`)) $(`#nav-${which}-in`).blur();  // so render() can show the choice
  }
  for (const which of ["from", "to"]) {
    const input = $(`#nav-${which}-in`);
    input.addEventListener("focus", () => { input.select(); menu(which, true); });
    input.addEventListener("blur", () => setTimeout(() => { menu(which, false); render(); }, 150));
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") { input.blur(); } if (e.key === "Enter") input.blur(); });
    input.addEventListener("change", () => {
      const p = parseLatLon(input.value); if (!p) return;
      if (which === "from") { start = p; startManual = true; } else { dest = p; destSuggested = false; ctx.saveAccessPoint(p); }
      picking = null; render();
    });
  }
  $("#nb-reroute").onclick = reroute;
  $("#nb-end").onclick = endNav;

  return {
    select(fire) { endNav(); routes = []; chosen = null; dispatch = null; dest = ctx.accessPoint(); destSuggested = false; picking = null; note(""); load(fire); render(); },
    detail(d) { if (pkg) prepareHazards(d); render(); },
    clear() { endNav(); pkg = null; pkgFor = null; hz = null; routes = []; chosen = null; picking = null; drawRoutes(); const m = ctx.getMap(); m?.getSource("nav-cov")?.setData({type: "FeatureCollection", features: []}); },
    dispatch(route) { dispatch = route; if (routes.length) findRoutes(true); render(); },
    destination(point) { dest = point; destSuggested = false; render(); },
    click(lngLat) {
      if (!picking) return false;
      const p = [lngLat.lng, lngLat.lat];
      if (picking === "start") { start = p; startManual = true; }
      else if (picking === "dest") { dest = p; destSuggested = false; ctx.saveAccessPoint(p); }
      else if (picking === "repeater" && pkg) addRepeater(p[0], p[1]);
      picking = null; render();
      return true;
    },
    gps(lon, lat, acc) {
      if (startManual && !navigating) return;
      if (acc > 100 && !navigating) { if (!start) note("Your location is approximate (±" + Math.round(acc) + " m): set the start on the map for accurate routes."); return; }
      start = [lon, lat]; startAcc = acc; startManual = false; render(); updateNav();
    },
    styleLoaded,
    sourceText: () => sourceText,
  };
}
