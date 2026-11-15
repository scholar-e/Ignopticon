// Ignopticon History page: burning-activity calendar from precomputed regional JSON
// (scripts/history_calendar.py -> /data/calendar/REGION.json) plus daily layers from /data/history/.
const $ = (s) => document.querySelector(s);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const SEQ = [[30, 28, 30], [110, 42, 24], [178, 54, 22], [232, 98, 32], [250, 160, 60], [255, 226, 150]];
const NOT_OBSERVED = 35;       // weekly mean clear percent below which a cell is hatched
const DEMO = {year: 2026, week: 30, label: "Aug 2, 2026"};
const Y0 = 2000, Y1 = 2026, YEARS = Array.from({length: Y1 - Y0 + 1}, (_, i) => Y0 + i);
const state = {region: "us", mode: "harm", data: null, week: null, year: null, day: null};
const cache = {};

const ramp = (t) => {
  t = Math.max(0, Math.min(1, t)) * (SEQ.length - 1);
  const i = Math.min(SEQ.length - 2, Math.floor(t)), f = t - i;
  return SEQ[i].map((v, k) => Math.round(v + f * (SEQ[i + 1][k] - v)));
};
const rgb = (c, a = 1) => `rgba(${c.join(",")},${a})`;
const fmt = (v) => v == null ? "none" : Math.round(v).toLocaleString("en-US");
const dateOf = (y, doy) => new Date(Date.UTC(y, 0, doy)).toISOString().slice(0, 10);
const weekStart = (w) => new Date(Date.UTC(2001, 0, 1 + 7 * w)).toUTCString().slice(5, 11);  // "30 Jul"
const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs, text) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; };

async function load(region) {
  if (!cache[region]) cache[region] = await (await fetch(`/data/calendar/${region}.json`)).json();
  return cache[region];
}

// ---- Heatmap ----
const H = {cw: 17, ch: 15, left: 40, top: 18};
function weekVal(y, w, mode = state.mode) { const a = state.data.weekly[y]; return a && a[mode][w] != null ? a[mode][w] : null; }
function heatScale() {
  const all = [];
  for (const y of YEARS) (state.data.weekly[y]?.raw || []).forEach((v) => v != null && all.push(v));
  all.sort((a, b) => a - b);
  return all[Math.floor(all.length * 0.985)] || 1;   // same scale for Raw and Harmonized, so the 2012 step is comparable
}
function drawHeat() {
  const c = $("#heat"), dpr = window.devicePixelRatio || 1, w = H.left + 52 * H.cw + 4, h = H.top + YEARS.length * H.ch + 4;
  c.width = w * dpr; c.height = h * dpr; c.style.width = w + "px"; c.style.height = h + "px";
  const g = c.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
  const vmax = heatScale();
  g.font = "11px system-ui"; g.fillStyle = "#B4BEC9";
  YEARS.forEach((y, r) => { g.textAlign = "right"; g.fillText(y, H.left - 6, H.top + r * H.ch + 11); });
  g.textAlign = "left";
  MONTHS.forEach((m, i) => g.fillText(m, H.left + Math.floor((new Date(Date.UTC(2001, i, 1)) - Date.UTC(2001, 0, 1)) / 864e5 / 7) * H.cw, 12));
  YEARS.forEach((y, r) => {
    for (let wk = 0; wk < 52; wk++) {
      const x = H.left + wk * H.cw, yy = H.top + r * H.ch, v = weekVal(y, wk), clr = state.data.weekly[y]?.clear[wk];
      if (v == null || clr == null) { g.fillStyle = "#101a16"; g.fillRect(x, yy, H.cw - 1, H.ch - 1); continue; }
      g.fillStyle = rgb(ramp(Math.sqrt(v / vmax))); g.fillRect(x, yy, H.cw - 1, H.ch - 1);
      if (clr < NOT_OBSERVED) {
        g.fillStyle = "rgba(14,17,22,.78)"; g.fillRect(x, yy, H.cw - 1, H.ch - 1);
        g.save(); g.beginPath(); g.rect(x, yy, H.cw - 1, H.ch - 1); g.clip(); g.strokeStyle = "#8a968f"; g.lineWidth = 1; g.beginPath();
        for (let k = -H.ch; k < H.cw; k += 4) { g.moveTo(x + k, yy + H.ch); g.lineTo(x + k + H.ch, yy); }
        g.stroke(); g.restore();
      }
    }
  });
  const box = (y, wk, color, lw) => { const r = YEARS.indexOf(y); if (r < 0 || wk == null) return; g.strokeStyle = color; g.lineWidth = lw; g.strokeRect(H.left + wk * H.cw - .5, H.top + r * H.ch - .5, H.cw, H.ch); };
  box(DEMO.year, DEMO.week, "#fff", 1.5);
  if (state.week != null) box(state.year, state.week, "#5AB0FF", 2);
}
function heatHit(e) {
  const b = $("#heat").getBoundingClientRect(), x = e.clientX - b.left - H.left, y = e.clientY - b.top - H.top;
  const wk = Math.floor(x / H.cw), r = Math.floor(y / H.ch);
  return wk >= 0 && wk < 52 && r >= 0 && r < YEARS.length ? {year: YEARS[r], week: wk} : null;
}
function setupHeat() {
  const c = $("#heat"), tip = $("#tip");
  c.addEventListener("mousemove", (e) => {
    const h = heatHit(e);
    if (!h || weekVal(h.year, h.week) == null) { tip.hidden = true; return; }
    const clr = state.data.weekly[h.year].clear[h.week], lab = state.mode === "harm" ? "VIIRS-equivalent" : "raw";
    tip.hidden = false;
    tip.textContent = `${h.year}, week of ${weekStart(h.week)}: ${fmt(weekVal(h.year, h.week))} ${lab} cell-days, ${clr}% of land seen` + (clr < NOT_OBSERVED ? " (not observed)" : "");
    const b = c.parentElement.getBoundingClientRect();
    tip.style.left = Math.min(e.clientX - b.left + 12 + c.parentElement.scrollLeft, c.parentElement.clientWidth - 280 + c.parentElement.scrollLeft) + "px"; tip.style.top = e.clientY - b.top + 14 + "px";
  });
  c.addEventListener("mouseleave", () => { tip.hidden = true; });
  c.addEventListener("click", (e) => { const h = heatHit(e); if (h && weekVal(h.year, h.week) != null) selectWeek(h.year, h.week); });
}

// ---- Annual totals ----
function line(svg, pts, color, hollow, dash) {
  let d = "", pen = false;
  pts.forEach((p) => { if (!p) { pen = false; return; } d += (pen ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); pen = true; });
  svg.append(el("path", {d, fill: "none", stroke: color, "stroke-width": 2, ...(dash ? {"stroke-dasharray": dash} : {})}));
  if (hollow) pts.forEach((p, i) => p && hollow[i] && svg.append(el("circle", {cx: p[0], cy: p[1], r: 3.5, fill: "#15231f", stroke: color, "stroke-width": 1.8})));
}
function drawAnnual() {
  const svg = $("#annual"), W = 520, Hh = 260, m = {l: 52, r: 10, t: 10, b: 24};
  svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`); svg.replaceChildren();
  const A = state.data.annual, vals = YEARS.flatMap((y) => [A[y].raw, A[y].harm]).filter((v) => v != null), max = niceMax(Math.max(...vals));
  const X = (y) => m.l + (y - Y0) / (Y1 - Y0) * (W - m.l - m.r), Y = (v) => Hh - m.b - v / max * (Hh - m.t - m.b);
  for (let i = 0; i <= 4; i++) {
    const v = max * i / 4; svg.append(el("line", {x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: "#2c3d36"}), el("text", {x: m.l - 6, y: Y(v) + 4, "text-anchor": "end"}, fmt(v)));
  }
  for (let y = Y0; y <= Y1; y += 4) svg.append(el("text", {x: X(y), y: Hh - 6, "text-anchor": "middle"}, y));
  svg.append(el("line", {x1: X(2011.5), x2: X(2011.5), y1: m.t, y2: Hh - m.b, stroke: "#8a968f", "stroke-dasharray": "3 3"}), el("text", {x: X(2011.5) + 4, y: m.t + 10}, "MODIS | VIIRS"));
  const short = (y) => A[y].days < A[y].of - 5;
  line(svg, YEARS.map((y) => [X(y), Y(A[y].raw)]), "#FFB020", YEARS.map(short));
  line(svg, YEARS.map((y) => A[y].harm == null ? null : [X(y), Y(A[y].harm)]), "#5AC8FA", YEARS.map(short));
}
const niceMax = (v) => { const p = Math.pow(10, Math.floor(Math.log10(v))); return Math.ceil(v / p * 2) / 2 * p; };

// ---- This year vs normal ----
function drawThisYear() {
  const svg = $("#thisyear"), W = 520, Hh = 260, m = {l: 52, r: 10, t: 10, b: 24}, mode = state.mode, d = state.data;
  svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`); svg.replaceChildren();
  const cl = d.climatology[mode], cur = d.weekly[2026][mode], max = niceMax(Math.max(...cl.p90, ...cur.filter((v) => v != null)));
  const X = (w) => m.l + (w + 0.5) / 52 * (W - m.l - m.r), Y = (v) => Hh - m.b - v / max * (Hh - m.t - m.b);
  for (let i = 0; i <= 4; i++) { const v = max * i / 4; svg.append(el("line", {x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: "#2c3d36"}), el("text", {x: m.l - 6, y: Y(v) + 4, "text-anchor": "end"}, fmt(v))); }
  MONTHS.forEach((mo, i) => svg.append(el("text", {x: X(Math.floor(i * 30.4 / 7)), y: Hh - 6, "text-anchor": "middle"}, mo)));
  const wk = DEMO.week, bw = (W - m.l - m.r) / 52;
  svg.append(el("rect", {x: X(wk) - bw / 2, y: m.t, width: bw, height: Hh - m.t - m.b, fill: "#fff", opacity: .16}));
  const up = cl.p90.map((v, w) => `${X(w)},${Y(v)}`), dn = cl.p10.map((v, w) => `${X(w)},${Y(v)}`).reverse();
  svg.append(el("polygon", {points: up.concat(dn).join(" "), fill: "#8a968f", opacity: .32}));
  line(svg, cl.median.map((v, w) => [X(w), Y(v)]), "#B4BEC9", null, "4 3");
  line(svg, cur.map((v, w) => v == null ? null : [X(w), Y(v)]), "#FF6B4A");
  if (cur[wk] != null) svg.append(el("circle", {cx: X(wk), cy: Y(cur[wk]), r: 5, fill: "#FF6B4A", stroke: "#fff", "stroke-width": 1.5}));
  svg.append(el("text", {x: W - m.r, y: m.t + 10, "text-anchor": "end", fill: "#FF6B4A"}, "2026"),
    el("text", {x: W - m.r, y: m.t + 24, "text-anchor": "end"}, "median and P10-P90, 2003-2025"));
  const pct = d.pct2026[mode][wk], clr = d.weekly[2026].clear[wk], med = cl.median[wk];
  $("#ty-sub").textContent = d.name;
  $("#ty-text").innerHTML = cur[wk] == null ? "No data for that week." :
    `<b>Week of ${DEMO.label}</b> (7 days from Jul 30): <b>${fmt(cur[wk])}</b> ${mode === "harm" ? "VIIRS-equivalent" : "raw"} cell-days against a normal of ${fmt(med)}: ` +
    `<b>percentile ${pct}</b> (busier than ${pct}% of the same week in 2003-2025). ` + (clr < NOT_OBSERVED ? `Only ${clr}% of the land was seen, so treat it as a floor.` : `${clr}% of the region's land cells were seen clearly.`);
}

// ---- Day strip and map ----
const map = {obj: null, ready: false, pending: null};
function selectWeek(y, w) {
  state.year = y; state.week = w; drawHeat();
  const doys = Array.from({length: 7}, (_, i) => 7 * w + 1 + i).filter((d) => d <= (y === Y1 ? 7 * w + 7 : (state.data.daily[y].raw.length)));
  const dd = state.data.daily[y], mode = state.mode;
  const val = (d) => { const a = dd[mode][d - 1], b = dd.raw[d - 1]; return a == null ? b : a; };
  const days = $("#days"); days.replaceChildren();
  let best = doys[0];
  doys.forEach((d) => { if ((val(d) || 0) > (val(best) || 0)) best = d; });
  doys.forEach((d) => {
    const b = document.createElement("button"); b.type = "button"; b.dataset.doy = d;
    const none = dd.raw[d - 1] == null; b.textContent = `${dateOf(y, d).slice(5)}: ${none ? "no data" : fmt(val(d))}`; b.title = none ? "No satellite data for this day (granules missing)" : `${dateOf(y, d)}: ${fmt(val(d))} fire cells, ${dd.clear[d - 1]}% seen`;
    b.addEventListener("click", () => showDay(y, d)); days.append(b);
  });
  showDay(y, best);
}
function showDay(y, doy) {
  state.day = doy;
  document.querySelectorAll("#days button").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.doy === doy)));
  const date = dateOf(y, doy);
  $("#day-sub").textContent = `${date} harmonized layer`;
  drawDay(date);
}
// Overlay: the day's PNG (R fire pixels, A observed) drawn into a Web Mercator canvas so it sits correctly on the basemap.
const LAT0 = 24, LAT1 = 50, LON0 = -125, LON1 = -66;
const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
async function drawDay(date) {
  const msg = $("#map-msg"); msg.hidden = false; msg.textContent = "Loading " + date + "...";
  const img = new Image(); img.src = `/data/history/harmonized/${date}.png`;
  try { await img.decode(); } catch { msg.textContent = `No layer for ${date}.`; drawFirms(date); return; }
  const src = document.createElement("canvas"); src.width = img.width; src.height = img.height;
  const sg = src.getContext("2d", {willReadFrequently: true}); sg.drawImage(img, 0, 0);
  const sd = sg.getImageData(0, 0, img.width, img.height).data;
  const cw = img.width * 2, y0 = merc(LAT0), y1 = merc(LAT1), ch = Math.round(cw * (y1 - y0) / ((LON1 - LON0) * Math.PI / 180));
  const out = document.createElement("canvas"); out.width = cw; out.height = ch;
  const og = out.getContext("2d"), od = og.createImageData(cw, ch);
  for (let r = 0; r < ch; r++) {
    const my = y1 - (r + 0.5) / ch * (y1 - y0), lat = (2 * Math.atan(Math.exp(my)) - Math.PI / 2) * 180 / Math.PI;
    const row = Math.min(img.height - 1, Math.max(0, Math.floor((LAT1 - lat) / 0.1)));
    for (let x = 0; x < cw; x++) {
      const i = (row * img.width + Math.floor(x / 2)) * 4, o = (r * cw + x) * 4, fire = sd[i], seen = sd[i + 3] > 0;
      if (fire > 0) { const c = ramp(0.25 + 0.75 * Math.sqrt(Math.min(fire, 25) / 25)); od.data.set([c[0], c[1], c[2], 235], o); }
      else if (!seen) od.data.set([10, 12, 16, 70], o);
    }
  }
  og.putImageData(od, 0, 0);
  const url = out.toDataURL("image/png");
  msg.hidden = true;
  const coords = [[LON0, LAT1], [LON1, LAT1], [LON1, LAT0], [LON0, LAT0]];
  const apply = () => {
    const s = map.obj.getSource("day");
    if (s) s.updateImage({url, coordinates: coords});
    else { map.obj.addSource("day", {type: "image", url, coordinates: coords}); map.obj.addLayer({id: "day", type: "raster", source: "day", paint: {"raster-opacity": 1, "raster-resampling": "nearest", "raster-fade-duration": 0}}); }
  };
  initMap(); if (map.ready) apply(); else map.pending = apply;
  drawFirms(date);
}

// ---- FIRMS hotspot points for the day (/data/history/firms/SENSOR/daily/DATE.json), one source per sensor family ----
// Colour by FRP (same classes as the fire watch page); radius is half the pixel footprint at high zoom (0.375 km VIIRS, 1 km MODIS).
const FRP_STEP = ["step", ["get", "frp"], "#FFE08A", 10, "#FFA230", 50, "#FF4A2A", 200, "#C2185B"];
const FAMILY = {modis: {sensors: ["modis"], km: 1, name: "MODIS"}, viirs: {sensors: ["viirs-snpp", "viirs-noaa20", "viirs-noaa21"], km: 0.375, name: "VIIRS"}};
const firmsShow = {modis: true, viirs: true};
const radius = (km) => ["interpolate", ["exponential", 2], ["zoom"], 3, 1.5, 9, 2.5, 16, Math.max(2.5, km * 500 * 65536 / 119900)];
async function drawFirms(date) {
  state.firmsDate = date; initMap();
  const box = $("#firms"), counts = {};
  const geo = {};
  for (const [fam, F] of Object.entries(FAMILY)) {
    const feats = []; counts[fam] = [];
    for (const s of F.sensors) {
      const j = await fetch(`/data/history/firms/${s}/daily/${date}.json`).then((r) => r.ok ? r.json() : null).catch(() => null);
      if (state.firmsDate !== date) return;
      if (!j || !j.rows) continue;
      const i = (n) => j.columns.indexOf(n);
      for (const r of j.rows) feats.push({type: "Feature", geometry: {type: "Point", coordinates: [r[i("longitude")], r[i("latitude")]]}, properties: {frp: +r[i("frp")] || 0}});
      counts[fam].push(`${s}: ${j.rows.length.toLocaleString("en-US")}`);
    }
    geo[fam] = {type: "FeatureCollection", features: feats};
  }
  const any = counts.modis.length + counts.viirs.length > 0;
  box.replaceChildren();
  for (const [fam, F] of Object.entries(FAMILY)) {
    const lab = document.createElement("label"), cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = firmsShow[fam];
    cb.addEventListener("change", () => { firmsShow[fam] = cb.checked; if (map.ready) map.obj.setLayoutProperty("firms-" + fam, "visibility", cb.checked ? "visible" : "none"); });
    lab.append(cb, ` FIRMS ${F.name} hotspots (${geo[fam].features.length.toLocaleString("en-US")}${counts[fam].length > 1 ? "; " + counts[fam].join(", ") : ""}) `);
    box.append(lab);
  }
  const note = document.createElement("span"); note.className = "muted";
  note.textContent = any ? "colour = FRP: yellow <10, orange 10-50, red 50-200, magenta >200 MW; zoom in to see pixel footprints" : "no FIRMS data for this day";
  box.append(note);
  const apply = () => {
    for (const fam of Object.keys(FAMILY)) {
      const s = map.obj.getSource("firms-" + fam);
      if (s) s.setData(geo[fam]);
      else {
        map.obj.addSource("firms-" + fam, {type: "geojson", data: geo[fam]});
        map.obj.addLayer({id: "firms-" + fam, type: "circle", source: "firms-" + fam, layout: {visibility: firmsShow[fam] ? "visible" : "none"},
          paint: {"circle-color": FRP_STEP, "circle-radius": radius(FAMILY[fam].km), "circle-opacity": 0.75, "circle-stroke-color": "#101418", "circle-stroke-width": 0.5}});
      }
    }
  };
  if (map.ready) apply(); else { const p = map.pending; map.pending = () => { if (p) p(); apply(); }; }
}
function initMap() {
  if (map.obj || !window.maplibregl) return;
  map.obj = new maplibregl.Map({container: "map", bounds: [[LON0, LAT0], [LON1, LAT1]], fitBoundsOptions: {padding: 8}, attributionControl: {compact: true},
    style: {version: 8, sources: {base: {type: "raster", tiles: [location.origin + "/tiles/s2cloudless/{z}/{x}/{y}"], tileSize: 256, maxzoom: 14,
      attribution: "EOxCloudless 2024 by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2024)"}},
      layers: [{id: "bg", type: "background", paint: {"background-color": "#1B2027"}}, {id: "base", type: "raster", source: "base", paint: {"raster-brightness-max": 0.6}}]}});
  map.obj.on("load", () => { map.ready = true; if (map.pending) { map.pending(); map.pending = null; } });
}

// ---- Wiring ----
async function render() {
  state.data = await load(state.region);
  $("#cal-sub").textContent = `${state.data.name}: ${state.mode === "harm" ? "VIIRS-equivalent" : "raw"} fire-active cell-days per week`;
  $("#mode-note").textContent = state.mode === "harm"
    ? "Harmonized: before 2012 this is the expected number of cells VIIRS would have flagged, so the 2012 step is gone. 2000-2002 are blank (no Aqua to train on)."
    : "Raw: MODIS Terra and Aqua cells before 2012, VIIRS S-NPP after. The jump in 2012 is the sensor change. White outline: week of Aug 2, 2026.";
  drawHeat(); drawAnnual(); drawThisYear();
}
async function main() {
  const regions = await (await fetch("/data/calendar/regions.json")).json();
  $("#region").replaceChildren(...regions.map((r) => Object.assign(document.createElement("option"), {value: r.id, textContent: r.name})));
  $("#ramp").style.background = `linear-gradient(90deg,${SEQ.map((c, i) => rgb(c) + " " + i * 20 + "%").join(",")})`;
  $("#region").addEventListener("change", async (e) => { state.region = e.target.value; await render(); if (state.week != null) selectWeek(state.year, state.week); });
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", async () => {
    state.mode = b.dataset.mode; document.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    await render(); if (state.week != null) selectWeek(state.year, state.week);
  }));
  setupHeat();
  const q = new URLSearchParams(location.search);
  if (q.get("region")) { state.region = q.get("region"); $("#region").value = state.region; }
  if (q.get("mode")) { state.mode = q.get("mode"); document.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.mode === state.mode))); }
  await render();
  const dq = /^(\d{4})-(\d\d)-(\d\d)$/.exec(q.get("date") || "");
  if (dq) {   // ?date=YYYY-MM-DD opens that day
    const y = +dq[1], doy = Math.round((Date.UTC(y, +dq[2] - 1, +dq[3]) - Date.UTC(y, 0, 0)) / 864e5);
    selectWeek(y, Math.min(51, Math.floor((doy - 1) / 7))); showDay(y, doy);
  } else selectWeek(DEMO.year, DEMO.week);
}
main();
