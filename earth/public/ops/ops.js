import {registerOffline, saveArea, listAreas} from "./offline.js";
// Ignopticon Ops: fire list, map, forecast and timeline from /api/fires (dev-server.js).
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
const num = (n) => Math.round(n).toLocaleString("en-US");
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

const params = new URLSearchParams(location.search);
const BASEMAPS = ["satellite", "imagerytopo", "topo"];
const state = {
  t: params.get("t"), fire: +params.get("fire") || null, view: params.get("view") === "3d" ? "3d" : "2d",
  base: BASEMAPS.includes(params.get("base")) ? params.get("base") : "satellite", labels: params.get("labels") === "on",
  sort: "distance", filter: "", mode: "progression", step: 0, compare: false,
  layers: {current: true, previous: true, forecast: true, arrows: true, hotspots: false, interior: false, detached: false},
};
let fires = [], byId = new Map(), at = null, map = null, cur = null, playing = null;


let gps = null, gpsMarker = null, gpsWatch = null, gpsMessage = "Waiting for GPS";
let connected = navigator.onLine;
function freshness() {
  const stamp = Date.parse(at?.snapshot);
  const age = Math.max(0, Math.floor((Date.now() - stamp) / 1000));
  $("#fresh").textContent = (Number.isFinite(age) ? "Updated " + age.toLocaleString() + " seconds ago" : "Snapshot unavailable") + (connected ? "" : " · OFFLINE");
}
registerOffline({onStatus: status => { connected = status.online; freshness(); }}).catch(() => {});
setInterval(freshness, 1000);
function matchesFire(f) {
  const q = state.filter.trim().toLowerCase();
  return [label(f), f.place, f.state].join(" ").toLowerCase().includes(q);
}
function distance(f) {
  if (!gps) return Infinity;
  const radians = Math.PI / 180;
  const [lon, lat] = f.centroid;
  const term = Math.sin((lat-gps[1])*radians/2)**2 +
    Math.cos(gps[1]*radians)*Math.cos(lat*radians)*Math.sin((lon-gps[0])*radians/2)**2;
  return 12742000 * Math.asin(Math.sqrt(Math.min(1,term)));
}
function showPosition() {
  if (!map || !gps) return;
  if (!gpsMarker) {
    const dot = document.createElement("div");
    dot.className = "gpsdot"; dot.title = "Your GPS position";
    gpsMarker = new maplibregl.Marker({element: dot});
  }
  gpsMarker.setLngLat(gps).addTo(map);
}
function requestGps() {
  if (gpsWatch !== null) navigator.geolocation.clearWatch(gpsWatch);
  if (!navigator.geolocation) { gpsMessage = "GPS unavailable"; if (at) renderList(); return; }
  gpsWatch = navigator.geolocation.watchPosition(({coords}) => {
    gps = [coords.longitude, coords.latitude]; showPosition(); if (at) renderList();
  }, () => { gps = null; gpsMarker?.remove(); gpsMessage = "GPS unavailable"; if (at) renderList(); },
  {enableHighAccuracy: true, maximumAge: 10000, timeout: 15000});
}
const saves = new Map();
async function cacheFire(f) {
  const key = [f.id, at.snapshot, state.base, state.t || "default"].join("-");
  const report = msg => { if (state.fire === f.id) $("#saved").textContent = msg; };
  if (!saves.has(key)) {
    const task = (async () => {
      if (!window.caches || !navigator.serviceWorker) throw new Error("Offline storage requires HTTPS");
      const cached = (await listAreas()).some(area => area.id === key && area.complete);
      if (!cached) {
      if (!navigator.onLine) throw new Error("Reconnect to finish saving");
      const result = await saveArea({id:key, label:label(f), bbox:fireBounds(f), bufferKm:3,
        sources:{[state.base]:[7,13]}, urls:["/api/fires"+query(), "/api/fires/"+f.id+query()],
        onProgress: p => report("Saving incident map · " + p.done + "/" + p.total)});
      if (!result.complete) throw new Error("Some tiles are missing. Reopen while online to retry");
      }
      if (!navigator.serviceWorker.controller) await Promise.race([navigator.serviceWorker.ready, new Promise((_, reject) => setTimeout(() => reject(new Error("Offline app shell is not ready")), 15000))]);
    })();
    saves.set(key,task);
  }
  report("Preparing offline incident map…");
  try { await saves.get(key); report("Incident data and area map saved offline"); }
  catch (e) { saves.delete(key); report("Offline map incomplete · " + e.message); }
}

const fmts = {};
function parts(iso, zone) {
  const f = fmts[zone] ||= new Intl.DateTimeFormat("en-US", {timeZone: zone, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "short"});
  const p = Object.fromEntries(f.formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return {time: `${p.hour}:${p.minute}`, tz: p.timeZoneName, day: `${p.month} ${p.day}`};
}
function when(iso, zone) { // "06:00 MDT Aug 2 · 12:00 UTC"
  const u = parts(iso, "UTC");
  if (!zone) return `${u.day} ${u.time} UTC`;
  const l = parts(iso, zone);
  return `${l.time} ${l.tz} ${l.day} · ${u.time} UTC${u.day !== l.day ? " " + u.day : ""}`;
}
const day = (iso) => iso ? new Date(iso).toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric", timeZone: "UTC"}) : "date unknown";
const short = (iso, zone) => { const l = parts(iso, zone || "UTC"); return `${l.day} ${l.time}`; };
const zoneOf = (f) => f && f.tz;
const hoursBetween = (a, b) => (new Date(b) - new Date(a)) / 3600e3;
const COMPASS = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split(" ");
const compass = (deg) => COMPASS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16];
const miles = (m) => { const v = m / 1609.344; return v < 10 ? v.toFixed(1) : num(v); };
const chains = (v) => v < 1 ? v.toFixed(1) : num(v);
const label = (f) => f.name || `Unnamed fire, ${f.place || "unknown place"}`;

function growthText(f) {
  if (f.growth_acres == null) return {text: "New in FEDS", cls: "new"};
  if (f.perimeter_time !== at.snapshot) return {text: "No detections in last 12 h", cls: ""};
  if (f.growth_acres === 0) return {text: "No growth in 12 h", cls: ""};
  const g = f.growth_acres, sign = g > 0 ? "+" : "−", before = f.acres - g;
  return {text: `${sign}${num(Math.abs(g))} ac${before > 0 ? ` (${sign}${num(Math.abs(g) / before * 100)}%)` : ""} in 12 h`, cls: g > 0 ? "up" : ""};
}

const api = (path) => fetch(path).then((r) => r.json().then((j) => r.ok ? j : Promise.reject(new Error(j.error || r.statusText))));
const query = () => state.t ? `?time=${encodeURIComponent(state.t)}` : "";
const details = new Map();
function detail(id) {
  if (!details.has(id)) details.set(id, api(`/api/fires/${id}${query()}`).catch((e) => { details.delete(id); throw e; }));
  return details.get(id);
}
const listReady = api(`/api/fires${query()}`);

function writeUrl() {
  const p = new URLSearchParams();
  if (state.t) p.set("t", state.t);
  if (state.fire) p.set("fire", state.fire);
  if (state.view === "3d") p.set("view", "3d");
  if (state.base !== "satellite") p.set("base", state.base);
  if (state.labels) p.set("labels", "on");
  history.replaceState(null, "", "?" + p);
}

const SORTS = {
  distance: (a,b) => distance(a) - distance(b) || label(a).localeCompare(label(b)),
  growth: (a, b) => (b.growth_acres ?? -1e12) - (a.growth_acres ?? -1e12) || b.acres - a.acres,
  size: (a, b) => b.acres - a.acres,
  name: (a, b) => label(a).localeCompare(label(b)),
};
function renderList() {
  if (!at) return;
  const rows = fires.filter(matchesFire).sort(SORTS[state.sort]);
  $("#list").innerHTML = rows.map((f) => {
    const g = growthText(f);
    return `<li><button type="button" class="fire" data-id="${f.id}"${f.id === state.fire ? ' aria-current="true"' : ""}>
<span class="r1"><span class="nm">${esc(label(f))}</span><span class="st">${gps ? miles(distance(f)) + " mi · " : ""}${esc(f.state || "")}</span></span>
<span class="r2"><span class="ac">${num(f.acres)}<small> ac</small></span><span class="gr ${g.cls}">${g.text}</span></span>
<span class="r3">${f.forecast ? '<span class="badge">Forecast</span>' : ""}${f.contained != null && f.incident_as_of && f.incident_as_of <= at.time ? `<span>${f.contained}% contained (WFIGS)</span>` : ""}</span></button></li>`;
  }).join("");
  $("#count").textContent = `${rows.length} of ${fires.length} fires · ${gps ? "Distance to fire center" : gpsMessage + " · Alphabetical"} · FEDS ${when(at.snapshot)}`;
}

function renderStatus() {
  if (!at) return;
  const f = byId.get(state.fire), zone = zoneOf(f), perimeter = f ? f.perimeter_time : at.snapshot;
  const age = hoursBetween(perimeter, at.time), old = age > 12;
  const fc = f ? (f.forecast ? `Forecast: issued ${when(f.forecast.issued, zone)}` : "Forecast: none for this fire")
    : `Forecasts: ${fires.filter((x) => x.forecast).length} fire(s)`;
  $("#status").className = "status" + (old ? " warn" : "");
  $("#status").innerHTML = `<span>Perimeters: ${when(perimeter, zone)}</span><span>${fc}</span>` +
    `<span class="age">${old ? "⚠ " : ""}Data age ${num(age)} h at view time${old ? " (over 12 h old)" : ""}</span>`;
}

const where = (x) => x.properties.where || "front";  // files without classes are all front
const bandAcres = (fc, h, w) => fc.features.filter((x) => x.properties.kind === "band" && (h == null || x.properties.hours === h) && where(x) === w)
  .reduce((s, x) => s + (x.properties.acres || 0), 0);
function summary(fc, zone) {
  let total = 0;
  const rows = fc.hours.map((h) => {
    total += bandAcres(fc, h, "front");
    return `<li>By +${h} h (${short(new Date(Date.parse(fc.issued) + h * 3600e3).toISOString(), zone)}): <b>+${num(total)} ac</b></li>`;
  });
  const a = fc.features.filter((x) => x.properties.kind === "arrow" && where(x) === "front").map((x) => x.properties).reduce((m, p) => !m || p.distance_m > m.distance_m ? p : m, null);
  const start = fc.features.find((x) => x.properties.kind === "start");
  return `<p class="big">+${num(total)} ac predicted by +${fc.hours[fc.hours.length - 1]} h</p><ul>${rows.join("")}</ul>` +
    (a ? `<p>Largest advance: <b>${miles(a.distance_m)} mi ${compass(a.bearing_deg)} at ${chains(a.rate_ch_per_h)} ch/h</b></p>` : "") +
    (start && start.properties.acres != null ? `<p class="muted">Model start area ${num(start.properties.acres)} ac</p>` : "") +
    (bandAcres(fc, null, "interior") + bandAcres(fc, null, "detached") ? `<p>Also predicted: ${num(bandAcres(fc, null, "interior"))} ac interior burnout${state.layers.interior ? "" : " (hidden)"}, ` +
      `${num(bandAcres(fc, null, "detached"))} ac on detached hotspots${state.layers.detached ? "" : " (hidden)"}</p>` : "");
}
function legendForecast(fc) {
  $("#fc-legend").textContent = fc || !cur ? "Predicted front growth +6 / +12 / +24 h (hatched)" : "No forecast for this fire";
  $("#fc-legend").closest("li").classList.toggle("none", !fc);
}
function renderDetail(f, d) {
  const zone = zoneOf(f), g = growthText(f), fc = d && d.forecast;
  const fcHtml = !d ? `<p class="muted">Loading forecast…</p>` : fc ? `<div class="fc"><h3>Spread forecast</h3>${summary(fc, zone)}<p>Model: ${esc(fc.model)}</p><p>Issued ${when(fc.issued, zone)}</p>
<p class="disclaimer">Experimental research forecast, not an official forecast. Do not use it alone for safety decisions.</p></div>` : `<div class="nofc"><h3>No spread forecast</h3><p>This fire has no forecast. The map shows observed perimeters only.</p></div>`;
  const hs = d && d.hotspots;
  $("#detail").innerHTML = `<button type="button" class="back" id="back">← All fires (Esc)</button>
<h2 id="detail-name">${esc(label(f))}</h2><p class="muted">${esc(f.place || "")} · FEDS fire ${f.id}</p>
<dl><div><dt>Mapped area</dt><dd class="big">${num(f.acres)} ac</dd></div><div><dt>12 h growth</dt><dd class="${g.cls}">${g.text}</dd></div>
${f.incident_acres != null ? `<div><dt>Incident record (WFIGS, as of ${day(f.incident_as_of)})</dt><dd>${num(f.incident_acres)} ac${f.contained != null ? `, ${f.contained}% contained` : ""}${f.incident_state && f.incident_state !== f.state ? `. Incident registered in ${esc(f.incident_state)}` : ""}</dd></div>` : ""}
<div><dt>Burning</dt><dd>${num(f.days)} days</dd></div></dl>${fcHtml}
<details><summary>Data sources</summary><ul><li>Perimeters: NASA FEDS (VIIRS 375 m), ${when(f.perimeter_time, zone)}</li>
<li>Hotspots: VIIRS S-NPP, NOAA-20, NOAA-21: ${hs ? `${num(hs.length)} detections in the 12 h to view time` : "none downloaded for this time"}</li>
${fc ? `<li>Forecast: ${esc(fc.source || fc.model)}</li>` : ""}<li>Map: USGS The National Map; 3D terrain: AWS Terrain Tiles</li></ul></details>`;
  $("#detail").hidden = false;
  if (d) legendForecast(fc);
  $("#back").onclick = overview;
}

const RED = "#FF453A", CASE = "#0B0D10", BANDS = ["#FF45C6", "#C75AF5", "#9464FF"], BAND_OPACITY = [0.5, 0.4, 0.3];
const PAST = ["#995B30", "#FFD649"], OBSERVED = "#F0A8FF", HOT = "#FFFFFF", FONT = ["Noto Sans Bold"];
const EMPTY = {type: "FeatureCollection", features: []};
const origin = location.origin;
const byRank = (v) => ["match", ["get", "rank"], 0, v[0], 1, v[1], v[2]];
const pastColor = ["interpolate", ["linear"], ["get", "age"], 0, PAST[0], 1, PAST[1]];
const halo = (color, w = 2) => ({"text-color": color, "text-halo-color": CASE, "text-halo-width": w});
const text = (field, size, extra) => ({"text-field": ["get", field], "text-font": FONT, "text-size": size, ...extra});
const kind = (k) => ["==", ["get", "kind"], k];
const front = ["==", ["get", "where"], "front"];
const baseSource = (b) => ({type: "raster", tiles: [`${origin}/tiles/${b}/{z}/{x}/{y}`], tileSize: 256, maxzoom: 16, attribution: "USGS The National Map"});
// Point markers show only while a fire is narrower than ~12 px (zh = that zoom, from the API's bbox).
const smallOnly = ["step", ["zoom"], ...Array.from({length: 17}, (_, z) => [z, ["case", [">", ["get", "zh"], z + 0.5], 1, 0]]).flat().slice(1)];

const alongLine = {"symbol-placement": "line", "symbol-spacing": 400};
function style() {
  const geo = (data = EMPTY) => ({type: "geojson", data});
  const L = (id, type, source, paint, layout = {}) => ({id, type, source, paint, layout});
  // A coloured line over a dark casing, so it reads on imagery and on the light topo map.
  const cased = (id, source, w, cw, paint, layout) => [L(id + "-case", "line", source, {"line-color": CASE, "line-width": cw}, layout), L(id + "-line", "line", source, {"line-width": w, ...paint}, layout)];
  return {
    version: 8, glyphs: new URL("vendor/fonts/", import.meta.url).href + "{fontstack}/{range}.pbf",
    sky: {"sky-color": "#6E9CC8", "horizon-color": "#D9E4EE", "fog-color": "#D9E4EE"},
    sources: {base: baseSource(state.base), hydro: {type: "raster", tiles: [`${origin}/tiles/hydro/{z}/{x}/{y}`], tileSize: 256, maxzoom: 16},
      fires: geo(), points: geo(), hist: geo(), fc: geo(), heads: geo(), hot: geo(), obs: geo(), access: geo()},
    layers: [
      L("base", "raster", "base", {}),
      L("hydro", "raster", "hydro", {}, {visibility: state.labels ? "visible" : "none"}),
      L("fires-fill", "fill", "fires", {"fill-color": RED, "fill-opacity": 0.1}),
      L("sel-fill", "fill", "fires", {"fill-color": RED, "fill-opacity": 0.12}),
      L("fc-fill", "fill", "fc", {"fill-color": byRank(BANDS), "fill-opacity": ["case", front, byRank(BAND_OPACITY), 0.15]}),
      L("fc-hatch", "fill", "fc", {"fill-pattern": "hatch", "fill-opacity": ["case", front, 1, 0.35]}),
      ...cased("hist", "hist", 2.5, 4.5, {"line-color": pastColor, "line-dasharray": [3, 1.5]}),
      ...cased("fc", "fc", 2, 4.5, {"line-color": byRank(BANDS), "line-opacity": ["case", front, 1, 0.5]}),
      ...cased("fires", "fires", 2, 4, {"line-color": RED}),
      ...cased("sel", "fires", 3.5, 7, {"line-color": RED}),
      ...cased("obs", "obs", 2.5, 5, {"line-color": OBSERVED, "line-dasharray": [0.5, 1.5]}, {"line-cap": "round"}),
      ...cased("access", "access", 4, 7, {"line-color": "#59DAD0"}),
      L("hot", "circle", "hot", {"circle-color": HOT, "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 1.5, 15, 5], "circle-stroke-color": CASE, "circle-stroke-width": 1}),
      ...cased("arrow", "fc", 3.5, 8, {"line-color": "#FFFFFF"}, {"line-cap": "round"}),
      L("arrow-head", "symbol", "heads", {}, {"icon-image": "head", "icon-rotate": ["get", "bearing"], "icon-rotation-alignment": "map",
        "icon-anchor": "top", "icon-allow-overlap": true, "icon-ignore-placement": true}),
      L("hist-label", "symbol", "hist", halo(pastColor), text("label", 14, alongLine)),
      L("obs-label", "symbol", "obs", halo(OBSERVED), text("label", 14, alongLine)),
      L("fc-label", "symbol", "heads", halo(byRank(BANDS), 2.5), text("label", 17, {"text-anchor": "left", "text-offset": [0.6, 0], "text-allow-overlap": true})),
      L("arrow-label", "symbol", "heads", halo("#FFFFFF", 2.5), text("label", 15, {"text-variable-anchor": ["left", "right", "top", "bottom"], "text-radial-offset": 1.6})),
      L("points", "circle", "points", {"circle-color": RED, "circle-radius": 6, "circle-stroke-color": CASE, "circle-stroke-width": 2,
        "circle-opacity": smallOnly, "circle-stroke-opacity": smallOnly}),
      {id: "fire-label", type: "symbol", source: "points", maxzoom: 11, paint: halo("#FFFFFF"), layout: text("label", 13, {"text-anchor": "top", "text-offset": [0, 0.9], "text-optional": true})},
    ],
  };
}

function image(size, draw) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  draw(g);
  return g.getImageData(0, 0, size, size);
}
function addImages() {
  // Diagonal hatch on every forecast fill so predicted never looks like observed.
  map.addImage("hatch", image(24, (g) => {
    g.strokeStyle = "rgba(0,0,0,0.55)"; g.lineWidth = 3; g.beginPath();
    for (let o = -24; o <= 24; o += 12) { g.moveTo(o, 24); g.lineTo(o + 24, 0); }
    g.stroke();
  }), {pixelRatio: 2});
  map.addImage("head", image(44, (g) => { // points up; the tip is the icon's top anchor
    g.beginPath(); g.moveTo(22, 3); g.lineTo(39, 40); g.lineTo(5, 40); g.closePath();
    g.lineJoin = "round"; g.lineWidth = 4; g.strokeStyle = CASE; g.fillStyle = "#FFFFFF"; g.fill(); g.stroke();
  }), {pixelRatio: 2});
}

// Fit inside what the status bar, map controls and the bottom legend/timeline stack leave visible.
function fitPadding() {
  const height = $("#map").clientHeight;
  return {top: Math.min(68,height/4), bottom: Math.min(52,height/5), left: 20, right: 52};
}
function fit(b) {
  if (!map) return;
  const three = state.view === "3d", cam = map.cameraForBounds([[b[0], b[1]], [b[2], b[3]]], {padding: fitPadding(), bearing: 0});
  // shortcut: the 3D view zooms out a fixed 0.8 so the near and far sides of the pitched fit stay on screen.
  if (cam) map.easeTo({...cam, zoom: Math.min(cam.zoom - (three ? 0.8 : 0), 15), pitch: three ? 60 : 0, bearing: 0, duration: reduced ? 0 : 600});
}
const union = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
const fireBounds = (f) => union(f.bbox, f.forecast && f.forecast.bbox || f.bbox);
const allBounds = () => fires.length ? fires.map((f) => f.bbox).reduce(union) : [-125,24,-66,50];

function setFires() {
  const feature = (f, geometry, props) => ({type: "Feature", geometry, properties: {id: f.id, ...props}});
  map.getSource("fires").setData({type: "FeatureCollection", features: fires.filter(matchesFire).map((f) => feature(f, f.geometry, {}))});
  map.getSource("points").setData({type: "FeatureCollection", features: fires.filter(matchesFire).map((f) => feature(f, {type: "Point", coordinates: f.centroid},
    {label: f.name || f.place || "", zh: Math.log2(4320 / (512 * Math.max(f.bbox[2] - f.bbox[0], 1e-4)))}))});
}

function setDetail(d) {
  drawRoute();
  const zone = zoneOf(d.fire), fc = d.forecast, n = d.history.length;
  const collection = (features) => ({type: "FeatureCollection", features});
  const ft = (geometry, properties) => ({type: "Feature", geometry, properties});
  map.getSource("hist").setData(collection(d.history.map((h, k) => ft(h.geometry, {k, age: n > 2 ? k / (n - 2) : 1, label: short(h.time, zone)}))));
  const hours = fc ? fc.hours : [];
  const rank = (h) => Math.min(Math.max(hours.indexOf(h), 0), 2);
  const bandOf = (h) => hours.find((x) => x >= h) ?? hours[hours.length - 1];  // arrows show from the band they reach into
  const fcFeatures = fc ? fc.features.filter((x) => x.properties.kind !== "start" && x.geometry).map((x) => ft(x.geometry,
    {...x.properties, where: where(x), rank: rank(x.properties.hours), label: x.properties.kind === "band" ? `+${x.properties.hours} h` : "", band: bandOf(x.properties.hours)})) : [];
  map.getSource("fc").setData(collection(fcFeatures));
  const heads = fcFeatures.filter((x) => x.properties.kind === "arrow").map((x) => {
    const p = x.properties, c = x.geometry.coordinates;
    return ft({type: "Point", coordinates: c[c.length - 1]}, {kind: "arrow", where: p.where, band: p.band, bearing: p.bearing_deg, label: `${miles(p.distance_m)} mi · ${chains(p.rate_ch_per_h)} ch/h`});
  });
  // One label per band, on the outer edge of its largest piece (labelling every piece is noise).
  const area = (r) => Math.abs(r.reduce((a, p, i) => { const q = r[(i + 1) % r.length]; return a + p[0] * q[1] - q[0] * p[1]; }, 0));
  const c0 = d.fire.centroid, far = (p) => (p[0] - c0[0]) ** 2 + (p[1] - c0[1]) ** 2;
  for (const x of fcFeatures.filter((x) => x.properties.kind === "band" && x.properties.where === "front")) {
    const polys = x.geometry.type === "Polygon" ? [x.geometry.coordinates] : x.geometry.coordinates;
    const ring = polys.map((p) => p[0]).sort((a, b) => area(b) - area(a))[0];
    if (ring) heads.push(ft({type: "Point", coordinates: ring.reduce((a, b) => far(b) > far(a) ? b : a)}, {kind: "label", hours: x.properties.hours, rank: x.properties.rank, label: x.properties.label}));
  }
  map.getSource("heads").setData(collection(heads));
  map.getSource("hot").setData(collection((d.hotspots || []).map((h) => ft({type: "Point", coordinates: [h[0], h[1]]}, {}))));
  map.getSource("obs").setData(collection((d.observed_after || []).map((o) => ft(o.geometry, {hours: o.hours, label: `Observed ${short(o.time, zone)} (+${o.hours} h)`}))));
  applyView();
}
function clearDetail() {
  for (const s of ["hist", "fc", "heads", "hot", "obs", "access"]) map.getSource(s).setData(EMPTY);
}

// Which stop and layers show. Switching stops only changes filters and visibility.
function applyView() {
  if (!map || !map.getLayer("sel-line")) return;
  const L = state.layers, sel = state.fire ?? -1, d = cur && cur.detail;
  const step = state.mode === "step" && cur && cur.stops ? cur.stops[state.step] : null;
  const nowK = d ? d.history.length - 1 : -1;
  const histK = !step ? ["<", ["get", "k"], nowK] : step.kind === "past" ? ["==", ["get", "k"], step.k] : ["==", ["get", "k"], -9];
  const horizon = !step ? 1e9 : step.kind === "fc" ? step.hours : -1;
  const showCur = L.current && !(step && step.kind === "past");
  const vis = (ids, on) => ids.forEach((id) => map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  const filter = (ids, f) => ids.forEach((id) => map.setFilter(id, f));
  filter(["fires-fill", "fires-case", "fires-line"], ["!=", ["get", "id"], sel]);
  filter(["sel-fill", "sel-case", "sel-line"], ["==", ["get", "id"], sel]);
  filter(["hist-case", "hist-line", "hist-label"], histK);
  const shown = ["in", ["get", "where"], ["literal", ["front", ...["interior", "detached"].filter((w) => L[w])]]];
  filter(["fc-fill", "fc-hatch", "fc-case", "fc-line"], ["all", kind("band"), ["<=", ["get", "hours"], horizon], shown]);
  filter(["fc-label"], ["all", kind("label"), ["<=", ["get", "hours"], horizon]]);
  const arrows = ["all", kind("arrow"), ["<=", ["get", "band"], horizon], shown];
  filter(["arrow-case", "arrow-line", "arrow-head"], arrows);
  filter(["arrow-label"], [...arrows, front]);
  filter(["fire-label"], ["!=", ["get", "id"], sel]);
  filter(["obs-case", "obs-line", "obs-label"], step ? ["==", ["get", "hours"], horizon] : ["<=", ["get", "hours"], horizon]);
  vis(["fires-fill", "fires-case", "fires-line", "points", "fire-label"], L.current);
  vis(["sel-fill", "sel-case", "sel-line"], showCur);
  vis(["hist-case", "hist-line", "hist-label"], L.previous);
  vis(["fc-fill", "fc-hatch", "fc-case", "fc-line", "fc-label"], L.forecast);
  vis(["arrow-case", "arrow-line", "arrow-head", "arrow-label"], L.arrows);
  vis(["hot"], L.hotspots && !(step && step.kind === "past"));
  vis(["obs-case", "obs-line", "obs-label"], state.compare);
}

function buildStops(d) {
  const fc = d.forecast, n = d.history.length;
  const stops = d.history.map((h, k) => ({kind: k === n - 1 ? "now" : "past", k, time: h.time, acres: h.acres}));
  if (fc) {
    let acres = 0;  // predicted growth since issue; the model's start area can differ from FEDS's
    for (const h of fc.hours) {
      acres += bandAcres(fc, h, "front");
      stops.push({kind: "fc", hours: h, time: new Date(Date.parse(fc.issued) + h * 3600e3).toISOString(), acres});
    }
  }
  return stops;
}
function renderTimeline() {
  const tl = $("#timeline");
  if (!cur) { tl.hidden = true; return; }
  tl.hidden = false;
  if (!cur.stops) { $("#stops").innerHTML = '<li class="muted">Loading timeline…</li>'; return; }
  const zone = zoneOf(cur.fire), fc = cur.detail.forecast;
  $("#stops").innerHTML = cur.stops.map((s, i) => {
    const pressed = state.mode === "step" && i === state.step;
    const head = s.kind === "now" ? "Now" : s.kind === "fc" ? `+${s.hours} h` : "Observed";
    const progress = i / Math.max(1, cur.detail.history.length - 2);
    const color = s.kind === "past" ? `color-mix(in srgb, ${PAST[0]}, ${PAST[1]} ${Math.round(100 * progress)}%)` : s.kind === "fc" ? BANDS[Math.min(2, fc.hours.indexOf(s.hours))] : RED;
    const stop = `<li class="stop ${s.kind}" style="--stop-color:${color}"><button type="button" data-step="${i}" aria-pressed="${pressed}"><span class="k">${head}</span>` +
      `<span>${short(s.time, zone)}</span><span class="a">${s.kind === "fc" ? "+" : ""}${num(s.acres)} ac</span></button></li>`;
    return s.kind === "now" ? stop + '<li class="divider" aria-hidden="true">NOW<small>observed | predicted</small></li>' : stop;
  }).join("") + (fc ? "" : `<li class="stop none"><button type="button" disabled><span class="k">+6 / +12 / +24 h</span><span>No forecast</span></button></li>`);
  for (const b of document.querySelectorAll("[data-mode]")) b.setAttribute("aria-pressed", b.dataset.mode === state.mode);
  $("#play").hidden = state.mode !== "step";
  $("#play").textContent = playing ? "Pause" : "Play";
  $("#compare").hidden = !(cur.detail.observed_after && cur.detail.observed_after.length);
  $("#compare").setAttribute("aria-pressed", state.compare);
}
function setStep(i, announce = true) {
  if (!cur || !cur.stops) return;
  state.mode = "step";
  state.step = Math.max(0, Math.min(cur.stops.length - 1, i));
  renderTimeline(); applyView();
  if (announce) {
    const s = cur.stops[state.step];
    say(`${s.kind === "fc" ? `Predicted +${s.hours} h` : s.kind === "now" ? "Now" : "Observed"}, ${short(s.time, zoneOf(cur.fire))}, ${s.kind === "fc" ? "plus " : ""}${num(s.acres)} acres`);
  }
}
function play() {
  if (playing) { clearTimeout(playing); playing = null; renderTimeline(); return; }
  if (reduced) return setStep(cur.stops.length - 1);
  if (state.step >= cur.stops.length - 1) setStep(0, false);
  const tick = () => {
    if (state.step >= cur.stops.length - 1) { playing = null; renderTimeline(); return; }
    setStep(state.step + 1, false);
    playing = setTimeout(tick, 1200);
  };
  playing = setTimeout(tick, 1200);
  renderTimeline();
}

const say = (msg) => { $("#announce").textContent = msg; };

const metric = (n, unit, digits = 0) => Number.isFinite(n) ? n.toFixed(digits) + unit : "Unavailable";
function fuelName(code) {
  if (!Number.isInteger(code)) return "Unavailable";
  const groups = [[101,109,"Grass"],[121,124,"Grass–shrub"],[141,149,"Shrub"],
    [161,165,"Timber–understory"],[181,189,"Timber litter"],[201,204,"Slash / blowdown"]];
  const group = groups.find(([lo,hi]) => code >= lo && code <= hi);
  const nonburnable = {91:"Urban / developed",92:"Snow / ice",93:"Agriculture",98:"Open water",99:"Barren"};
  return (group?.[2] || nonburnable[code] || "Unclassified") + " · FBFM " + code;
}
function readConditions(f) {
  const active = () => state.fire === f.id;
  for (const id of ["fuel","wind","weather"]) $("#"+id).textContent = "Loading…";
  $("#wx-source").textContent = "Sampled at the mapped fire center.";
  const [lon,lat] = f.centroid;
  api("/identify/fuel?lon="+lon+"&lat="+lat).then(data => {
    if (active()) $("#fuel").textContent = fuelName(data.value) + " · LANDFIRE";
  }).catch(() => { if (active()) $("#fuel").textContent = "Unavailable"; });
  api("/api/fires/"+f.id+"/weather?time="+encodeURIComponent(at.time)).then(wx => {
    if (!active()) return;
    $("#wind").textContent = metric(wx.wind_mph," mph") + (Number.isFinite(wx.wind_from) && wx.wind_mph > 0 ? " from "+compass(wx.wind_from) : "");
    $("#weather").textContent = "Air "+metric(wx.temp_f," °F")+" · Humidity "+metric(wx.humidity,"%")+" · Precipitation "+metric(wx.rain_mm," mm",1);
    $("#wx-source").textContent = wx.source + " · " + when(wx.time,zoneOf(f)) + " · Fire center";
  }).catch(() => { if (active()) { $("#wind").textContent = "Unavailable"; $("#weather").textContent = "Unavailable for this incident hour"; } });
}
let accessRoute = null, accessPoint = null, accessMarker = null, pickingAccess = false;
function routeKey() { return "ops.route."+at.snapshot+"."+state.fire; }
function parseRoute(value) {
  const feature = value?.type === "FeatureCollection" && value.features?.length === 1 ? value.features[0] : value;
  const geometry = feature?.type === "Feature" ? feature.geometry : feature;
  const points = geometry?.coordinates;
  if (geometry?.type !== "LineString" || !Array.isArray(points) || points.length < 2 || points.length > 20000 ||
      !points.every(p => Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]) && Math.abs(p[0]) <= 180 && Math.abs(p[1]) <= 85))
    throw new Error("Choose a GeoJSON file containing one route LineString.");
  const props = feature.properties || {};
  if (props.fire_id != null && String(props.fire_id) !== String(state.fire)) throw new Error("This route belongs to another fire.");
  return {type:"Feature",geometry:{type:"LineString",coordinates:points.map(p=>p.slice(0,2))},properties:{name:String(props.name || "Incident access route").slice(0,120),fire_id:state.fire}};
}
function drawRoute() {
  map?.getSource("access")?.setData(accessRoute || EMPTY);
}
function pickMode(active) {
  pickingAccess = active;
  $("#pick-access").setAttribute("aria-pressed", String(active));
  $("#pick-access").textContent = active ? "Cancel point selection" : "Choose access point on map";
  $("#access-note").textContent = active ? "Tap the dispatch-designated road access point." : "";
  if (map) map.getCanvas().style.cursor = active ? "crosshair" : "";
}
$("#pick-access").onclick = () => {
  if (!map) { $("#access-note").textContent = "Map unavailable."; return; }
  pickMode(!pickingAccess);
};
function showRoute() {
  drawRoute();
  $("#fit-route").hidden = !accessRoute;
  const link = $("#road-directions");
  const destination = accessPoint || accessRoute?.geometry.coordinates[0];
  link.hidden = !destination;
  accessMarker?.remove();
  if (destination) {
    const [lon,lat] = destination;
    link.textContent = accessPoint ? "Road directions to access point ↗" : "Road directions to route start ↗";
    if (map) {
      const dot = document.createElement("div");
      dot.className = "access-dot"; dot.title = "Incident access point";
      accessMarker = new maplibregl.Marker({element:dot}).setLngLat(destination).addTo(map);
    }
    link.href = "https://www.google.com/maps/dir/?" + new URLSearchParams({api:"1",destination:lat+","+lon,travelmode:"driving"});
  } else link.removeAttribute("href");
  $("#route-note").textContent = accessRoute ? accessRoute.properties.name + " · Follow the teal line. Road status, slope and radio coverage are unverified." :
    "No incident access route supplied. Load a dispatch GeoJSON route to display it alongside your GPS position.";
}
function restoreRoute(provided) {
  let saved;
  try { saved = localStorage.getItem(routeKey()); } catch {}
  accessRoute = null;
  for (const source of [provided,saved]) {
    if (!source) continue;
    try { accessRoute = parseRoute(typeof source === "string" ? JSON.parse(source) : source); break; } catch {}
  }
  showRoute();
}
$("#fit-route").onclick = () => {
  if (!accessRoute) return;
  const coords = accessRoute.geometry.coordinates;
  fit(coords.map(([x,y])=>[x,y,x,y]).reduce(union));
};
$("#route-file").onchange = async e => {
  const file = e.target.files[0], key = routeKey();
  e.target.value = "";
  if (!file) return;
  try {
    if (file.size > 2000000) throw new Error("Route file exceeds 2 MB.");
    const value = JSON.parse(await file.text());
    if (!state.fire || routeKey() !== key) return;
    accessRoute = parseRoute(value);
    accessPoint = null; pickMode(false);
    try { localStorage.removeItem(key+".point"); } catch {}
    showRoute(); $("#fit-route").click();
    try { localStorage.setItem(key,JSON.stringify(accessRoute)); }
    catch { $("#route-note").textContent += " Device storage unavailable; route is open for this session only."; }
  } catch (err) { if (state.fire && routeKey() === key) $("#route-note").textContent = err.message; }
};

async function select(id, {fitMap = true} = {}) {
  const f = byId.get(id);
  if (!f) return;
  if (playing) play();
  document.body.classList.add("selected"); placeLegend(true); $(".sidebar").scrollTop = 0; map?.resize();
  state.fire = id; pickMode(false); accessPoint = null;
  try {
    const point = JSON.parse(localStorage.getItem(routeKey()+".point"));
    if (Array.isArray(point) && point.length === 2 && point.every(Number.isFinite)) accessPoint = point;
  } catch {}
  restoreRoute(null); readConditions(f); cacheFire(f); state.mode = "progression"; cur = {fire: f};
  writeUrl(); renderList(); renderStatus(); renderDetail(f, null); renderTimeline();
  if (fitMap) fit(fireBounds(f));
  if (map && map.getSource("fires")) { clearDetail(); drawRoute(); applyView(); }
  try {
    const d = await detail(id);
    if (state.fire !== id) return;
    cur = {fire: f, detail: d, stops: buildStops(d)};
    restoreRoute(d.access_route);
    state.step = d.history.length - 1;
    renderDetail(f, d); renderTimeline();
    if (map && map.getSource("fires")) setDetail(d);
    say(`Selected ${label(f)}, ${num(f.acres)} acres. ${d.forecast ? "Spread forecast available." : "No spread forecast."}`);
  } catch (e) {
    if (state.fire !== id) return;
    $("#detail").insertAdjacentHTML("beforeend", `<p class="error" role="alert">Could not load fire details: ${esc(e.message)}</p>`);
  }
}
function overview() {
  if (playing) play();
  document.body.classList.remove("selected"); placeLegend(false); setLegend(false); map?.resize();
  state.fire = null; cur = null; accessRoute = null; accessPoint = null; accessMarker?.remove(); pickMode(false);
  $("#detail").hidden = true; $("#timeline").hidden = true;
  writeUrl(); renderList(); renderStatus(); legendForecast(null);
  if (map && map.getSource("fires")) { clearDetail(); applyView(); }
  if (fires.length) fit(allBounds());
  say("All fires");
}

function setView(v) {
  state.view = v; writeUrl();
  $("#view3d").setAttribute("aria-pressed", v === "3d");
  if (!map || !map.getLayer("base")) return;  // style.load applies the URL's view later
  if (v === "3d") {
    map.addSource("dem", {type: "raster-dem", tiles: [`${origin}/tiles/terrain/{z}/{x}/{y}`], encoding: "terrarium", tileSize: 256, maxzoom: 15, attribution: "Terrain: AWS Terrain Tiles"});
    map.setTerrain({source: "dem", exaggeration: 1.5});
    if (cur) fit(fireBounds(cur.fire)); else map.easeTo({pitch: 60, duration: reduced ? 0 : 500});
  } else {
    map.setTerrain(null);
    map.removeSource("dem");  // 2D pays nothing for terrain
    if (cur) fit(fireBounds(cur.fire)); else map.easeTo({pitch: 0, bearing: 0, duration: reduced ? 0 : 500});
  }
  map.once("idle", () => performance.mark("ops-view-" + v));
}
function setBase(b) {
  state.base = b; writeUrl();
  for (const x of document.querySelectorAll("[data-base]")) x.setAttribute("aria-pressed", x.dataset.base === b);
  if (!map || !map.getLayer("base")) return;
  map.removeLayer("base"); map.removeSource("base");
  map.addSource("base", baseSource(b));
  map.addLayer({id: "base", type: "raster", source: "base"}, "hydro");
}
function setLabels(on) {
  state.labels = on; writeUrl();
  $("#labels").setAttribute("aria-pressed", on);
  if (map && map.getLayer("hydro")) map.setLayoutProperty("hydro", "visibility", on ? "visible" : "none");
}

function initMap() {
  if (!window.maplibregl) { $("#count").textContent += " · Map library failed to load"; return; }
  map = new maplibregl.Map({container: "map", style: style(), bounds: [[-125, 24], [-66, 50]], fitBoundsOptions: {padding: 40},
    attributionControl: {compact: false}, maxPitch: 70, fadeDuration: reduced ? 0 : 300});
  window.opsMap = map; showPosition();
  if (at) fit(state.fire && byId.has(state.fire) ? fireBounds(byId.get(state.fire)) : allBounds());
  map.addControl(new maplibregl.NavigationControl({visualizePitch: true}), "top-right");
  map.addControl(new maplibregl.ScaleControl({unit: "imperial"}), "bottom-right");
  map.on("style.load", () => {
    addImages();
    listReady.then(() => {
      setFires(); applyView();
      if (cur && cur.detail) setDetail(cur.detail);
      if (state.view === "3d") setView("3d");
    });
  });
  map.once("idle", () => performance.mark("ops-map-idle"));
  const pick = (e) => {
    if (pickingAccess && state.fire) {
      accessPoint = [e.lngLat.wrap().lng,e.lngLat.lat];
      pickMode(false); showRoute();
      try { localStorage.setItem(routeKey()+".point",JSON.stringify(accessPoint)); } catch {}
      $("#access-note").textContent = "Selected access point: "+accessPoint[1].toFixed(5)+", "+accessPoint[0].toFixed(5)+". Road directions open in Google Maps; slope and radio coverage are unverified.";
      return;
    }
    const hit = map.queryRenderedFeatures(e.point, {layers: ["sel-fill", "fires-fill", "points"]}).find((x) => x.layer.id !== "points" || map.getZoom() < x.properties.zh);
    if (hit) select(hit.properties.id, {fitMap: hit.properties.id !== state.fire});
  };
  map.on("click", pick);
}

$("#filter").addEventListener("input", (e) => { state.filter = e.target.value; renderList(); if (map?.getSource("fires")) setFires();
  const matches = fires.filter(matchesFire); if (matches.length) fit(matches.map(f => f.bbox).reduce(union)); });
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; renderList(); });
$("#list").addEventListener("click", (e) => { const b = e.target.closest("button.fire"); if (b) select(+b.dataset.id); });
const prefetch = (e) => { const b = e.target.closest("button.fire"); if (b) detail(+b.dataset.id).catch(() => {}); };
$("#list").addEventListener("mouseover", prefetch);
$("#list").addEventListener("focusin", prefetch);
$("#list").addEventListener("keydown", (e) => {
  const buttons = [...document.querySelectorAll("#list button.fire")], i = buttons.indexOf(document.activeElement);
  const to = {ArrowDown: i + 1, ArrowUp: i - 1}[e.key];
  if (to === undefined || i < 0) return;
  e.preventDefault();
  buttons[Math.max(0, Math.min(buttons.length - 1, to))].focus();
});
document.querySelector(".legend").addEventListener("click", (e) => {
  const b = e.target.closest("[data-layer]");
  if (!b) return;
  state.layers[b.dataset.layer] = !state.layers[b.dataset.layer];
  b.setAttribute("aria-pressed", state.layers[b.dataset.layer]);
  applyView();
  if (cur && cur.detail && /interior|detached/.test(b.dataset.layer)) renderDetail(cur.fire, cur.detail);
});
function setLegend(open) {
  $("#legend").classList.toggle("open", open);
  $("#legend-toggle").setAttribute("aria-expanded", open);
  $("#legend-toggle").textContent = open ? "Key ▾" : "Key";
  try { localStorage.setItem("ops.legend", open ? "open" : ""); } catch {}
}
// On phones, the selected-fire sheet is the useful place for layer controls:
// the map remains clear, while the key stays adjacent to that fire's forecast.
function placeLegend(selected) {
  const bottom = $(".bottom"), sidebar = $(".sidebar"), detail = $("#detail");
  if (selected && window.matchMedia("(max-width: 1099px)").matches) sidebar.insertBefore(bottom, detail);
  else $(".mapwrap").append(bottom);
}
setLegend(false);
window.addEventListener("resize", () => placeLegend(document.body.classList.contains("selected")));
$("#legend-toggle").addEventListener("click", () => setLegend(!$("#legend").classList.contains("open")));
$("#basemaps").addEventListener("click", (e) => { const b = e.target.closest("[data-base]"); if (b) setBase(b.dataset.base); });
$("#view3d").addEventListener("click", () => setView(state.view === "3d" ? "2d" : "3d"));
$("#labels").addEventListener("click", () => setLabels(!state.labels));
$("#timeline").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.step) setStep(+b.dataset.step);
  else if (b.dataset.mode === "step") setStep(state.step);
  else if (b.dataset.mode === "progression") { if (playing) play(); state.mode = "progression"; renderTimeline(); applyView(); say("Progression: all perimeters"); }
  else if (b.id === "play") play();
  else if (b.id === "compare") { state.compare = !state.compare; renderTimeline(); applyView(); }
});
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName);
  if (e.key === "Escape") { if (typing) e.target.blur(); if (state.fire) overview(); return; }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); $("#filter").focus(); }
  else if (e.key === "3") setView(state.view === "3d" ? "2d" : "3d");
  else if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && cur && cur.stops && !e.target.closest(".maplibregl-canvas-container")) {
    e.preventDefault();
    setStep((state.mode === "step" ? state.step : cur.stops.findIndex((s) => s.kind === "now")) + (e.key === "ArrowRight" ? 1 : -1));
  }
});

$("#home").onclick = () => { state.filter = ""; $("#filter").value = ""; if (map?.getSource("fires")) setFires(); overview(); };
$("#return").onclick = () => { overview(); const matches = fires.filter(matchesFire); if (matches.length) fit(matches.map(f=>f.bbox).reduce(union)); };
$("#locate").onclick = () => { if (gps && map) map.easeTo({center:gps, zoom:12}); else requestGps(); };
requestGps();
// The list renders from the API without waiting for the map.
for (const x of document.querySelectorAll("[data-base]")) x.setAttribute("aria-pressed", x.dataset.base === state.base);
$("#labels").setAttribute("aria-pressed", state.labels);
$("#view3d").setAttribute("aria-pressed", state.view === "3d");
listReady.then((data) => {
  at = data; fires = data.fires; byId = new Map(fires.map((f) => [f.id, f]));
  renderList(); renderStatus();
  performance.mark("ops-list");
  if (hoursBetween(at.time, new Date()) > 24) $("#replay").textContent = `Replay: view time ${when(at.time)}`;
  if (state.fire && byId.has(state.fire)) select(state.fire);
  else if (map) { state.fire = null; fit(allBounds()); }
}).catch((e) => {
  $("#count").innerHTML = `<span class="error" role="alert">Could not load fires: ${esc(e.message)}</span>`;
});
// The map library is a deferred script after this module; start the map once it has run.
if (window.maplibregl) initMap(); else $('script[src$="maplibre-gl.js"]').addEventListener("load", initMap);
