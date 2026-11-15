import {registerOffline, saveArea, listAreas} from "./offline.js";
import {earthLayers} from "./earthlayers.js";
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
let fires = [], byId = new Map(), at = null, map = null, cur = null, playing = null, earth = null;
// Weather & satellite layers from the URL: wind=surface|1000|...|10, flow=smoke, allhot=on, heat=on, overlay=fuel|heat-day|...
const earthOpts = {wind: params.get("wind") || "off", flow: params.get("flow") === "smoke" ? "smoke" : "wind",
  hotspots: params.get("allhot") === "on", heat: params.get("heat") === "on", overlay: params.get("overlay") || "none"};


let gps = null, gpsMarker = null, gpsWatch = null;
let connected = navigator.onLine;
function ago(ms) {
  const m = Math.max(0, ms) / 60000;
  return m < 1 ? "just now" : m < 60 ? `${Math.floor(m)} min ago` : m < 2880 ? `${Math.floor(m / 60)} h ago` : `${Math.floor(m / 1440)} d ago`;
}
function freshness() {  // live view: how old the perimeters are; replay shows its view time instead (#replay)
  const stamp = Date.parse(at?.snapshot), replay = at && hoursBetween(at.time, new Date()) > 24;
  const text = replay || !Number.isFinite(stamp) ? "" : "Updated " + ago(Date.now() - stamp);
  $("#fresh").textContent = connected ? text : "Offline" + (text ? " · " + text : "");
}
registerOffline({onStatus: status => { connected = status.online; freshness(); }}).catch(() => {});
setInterval(freshness, 30000);
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
  if (!navigator.geolocation) { if (at) renderList(); return; }
  gpsWatch = navigator.geolocation.watchPosition(({coords}) => {
    gps = [coords.longitude, coords.latitude]; showPosition(); if (at) renderList();
  }, () => { gps = null; gpsMarker?.remove(); if (at) renderList(); },
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
        onProgress: p => report("Saving for offline… " + p.done + "/" + p.total)});
      if (!result.complete) throw new Error("some tiles missing; reopen online to retry");
      }
      if (!navigator.serviceWorker.controller) await Promise.race([navigator.serviceWorker.ready, new Promise((_, reject) => setTimeout(() => reject(new Error("Offline app shell is not ready")), 15000))]);
    })();
    saves.set(key,task);
  }
  report("Saving for offline…");
  try { await saves.get(key); report("Saved for offline use"); }
  catch (e) { saves.delete(key); report("Not saved offline: " + e.message); }
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
  const e = earth ? earth.opts : earthOpts;
  if (e.wind !== "off") p.set("wind", e.wind);
  if (e.flow === "smoke") p.set("flow", "smoke");
  if (e.hotspots) p.set("allhot", "on");
  if (e.heat) p.set("heat", "on");
  if (e.overlay !== "none") p.set("overlay", e.overlay);
  history.replaceState(null, "", "?" + p);
}

const SORTS = {
  distance: (a,b) => distance(a) - distance(b) || label(a).localeCompare(label(b)),
  growth: (a, b) => (b.growth_acres ?? -1e12) - (a.growth_acres ?? -1e12) || b.acres - a.acres,
  size: (a, b) => b.acres - a.acres,
  name: (a, b) => label(a).localeCompare(label(b)),
};
// Short growth for list rows: "+16%" (12 h), "New", or nothing when quiet.
function growthShort(f) {
  if (f.growth_acres == null) return {text: "New", cls: "new"};
  const before = f.acres - f.growth_acres;
  const pct = before > 0 ? Math.round(f.growth_acres / before * 100) : 0;  // under 0.5% reads as quiet
  if (f.perimeter_time !== at.snapshot || pct <= 0) return {text: "", cls: ""};
  return {text: `+${num(pct)}% 12 h`, cls: "up"};
}
function renderList() {
  if (!at) return;
  const rows = fires.filter(matchesFire).sort(SORTS[state.sort]);
  $("#list").innerHTML = rows.map((f) => {
    const g = growthShort(f);
    const where = [f.place || f.state, gps ? miles(distance(f)) + " mi" : null, f.forecast ? '<span class="badge">Forecast</span>' : null].filter(Boolean);
    return `<li><button type="button" class="fire" data-id="${f.id}"${f.id === state.fire ? ' aria-current="true"' : ""}>
<span class="r1"><span class="nm">${esc(label(f))}</span><span class="ac">${num(f.acres)} ac</span></span>
<span class="r2"><span>${where.map((x) => x.startsWith("<") ? x : esc(x)).join(" · ")}</span><span class="gr ${g.cls}">${g.text}</span></span></button></li>`;
  }).join("");
  $("#count").textContent = rows.length === fires.length ? `${fires.length} fires` : `${rows.length} of ${fires.length} fires`;
}

// Only speaks up when the perimeter is stale; the details live under "About this data".
function renderStatus() {
  if (!at) return;
  const f = byId.get(state.fire), perimeter = f ? f.perimeter_time : at.snapshot;
  const age = hoursBetween(perimeter, at.time);
  $("#status").textContent = age > 12 ? `⚠ Perimeter is ${num(age)} h old` : "";
}

const where = (x) => x.properties.where || "front";  // files without classes are all front
const bandAcres = (fc, h, w) => fc.features.filter((x) => x.properties.kind === "band" && (h == null || x.properties.hours === h) && where(x) === w)
  .reduce((s, x) => s + (x.properties.acres || 0), 0);
function forecastFacts(fc) {
  const total = fc.hours.reduce((s, h) => s + bandAcres(fc, h, "front"), 0);
  const a = fc.features.filter((x) => x.properties.kind === "arrow" && where(x) === "front").map((x) => x.properties).reduce((m, p) => !m || p.distance_m > m.distance_m ? p : m, null);
  const start = fc.features.find((x) => x.properties.kind === "start");
  return {total, hours: fc.hours[fc.hours.length - 1], advance: a, start: start && start.properties.acres};
}
function legendForecast(fc) {
  $("#fc-legend").textContent = fc || !cur ? "Forecast" : "Forecast (none)";
  $("#fc-legend").closest("li").classList.toggle("none", !fc);
}
// The card keeps a few numbers; everything else sits under "About this data".
function renderDetail(f, d) {
  const zone = zoneOf(f), g = growthShort(f), fc = d && d.forecast, x = fc && forecastFacts(fc);
  const stats = [[`${num(f.acres)} ac`, "Area"], g.text ? [g.text.replace(" 12 h", ""), "Last 12 h"] : null,
    f.contained != null ? [`${f.contained}%`, "Contained"] : null, [`${num(f.days)} d`, "Burning"]].filter(Boolean);
  $("#detail").innerHTML = `<h2 id="detail-name">${esc(label(f))}</h2><p class="sub">${esc(f.place || f.state || "")}</p>
<div class="stats">${stats.map(([v, k], i) => `<div><b${i === 1 && g.cls ? ` class="${g.cls}"` : ""}>${esc(v)}</b><span>${k}</span></div>`).join("")}</div>`;
  $("#fcbox").innerHTML = !d ? `<p class="muted">Loading forecast…</p>` : fc
    ? `<h3>Spread forecast <span class="tag" title="Experimental research forecast, not an official forecast. Do not use it alone for safety decisions.">Experimental · not official</span></h3>
<p class="big">+${num(x.total)} ac <small>in ${x.hours} h</small></p>` +
      (x.advance ? `<p>Fastest: ${miles(x.advance.distance_m)} mi ${compass(x.advance.bearing_deg)} · ${chains(x.advance.rate_ch_per_h)} ch/h</p>` : "")
    : `<p class="muted">No spread forecast for this fire</p>`;
  const hs = d && d.hotspots, wf = f.incident_acres != null;
  $("#about-list").innerHTML = [
    `Perimeter: NASA FEDS fire ${f.id}, ${when(f.perimeter_time, zone)}`,
    wf ? `Incident record (WFIGS, ${day(f.incident_as_of)}): ${num(f.incident_acres)} ac${f.contained != null ? `, ${f.contained}% contained` : ""}${f.incident_state && f.incident_state !== f.state ? `, registered in ${esc(f.incident_state)}` : ""}` : null,
    `Hotspots: VIIRS S-NPP, NOAA-20, NOAA-21, ${hs ? `${num(hs.length)} in the last 12 h` : "none downloaded"}`,
    fc ? `Forecast: ${esc(fc.model)}, issued ${when(fc.issued, zone)}${x.start != null ? `; start area ${num(x.start)} ac` : ""}` : null,
    fc && (bandAcres(fc, null, "interior") + bandAcres(fc, null, "detached")) ? `Also predicted: ${num(bandAcres(fc, null, "interior"))} ac burnout inside, ${num(bandAcres(fc, null, "detached"))} ac spot growth (Layers)` : null,
    fc ? "The forecast is experimental research, not an official forecast. Do not use it alone for safety decisions." : null,
    "Map: USGS The National Map; 3D terrain: AWS Terrain Tiles",
  ].filter(Boolean).map((s) => `<li>${s}</li>`).join("");
  $("#detail").hidden = false;
  if (d) legendForecast(fc);
}

const RED = "#FF453A", CASE = "#0B0D10", BANDS = ["#FF45C6", "#C75AF5", "#9464FF"], BAND_OPACITY = [0.3, 0.24, 0.18];
const PAST = ["#995B30", "#FFD649"], OBSERVED = "#F0A8FF", HOT = "#FFFFFF", FONT = ["Noto Sans Bold"];
const EMPTY = {type: "FeatureCollection", features: []};
const origin = location.origin;
const byRank = (v) => ["match", ["get", "rank"], 0, v[0], 1, v[1], v[2]];
const pastColor = ["interpolate", ["linear"], ["get", "age"], 0, PAST[0], 1, PAST[1]];
const halo = (color, w = 2) => ({"text-color": color, "text-halo-color": CASE, "text-halo-width": w});
const text = (field, size, extra) => ({"text-field": ["get", field], "text-font": FONT, "text-size": size, ...extra});
const kind = (k) => ["==", ["get", "kind"], k];
const front = ["==", ["get", "where"], "front"];
// "base-low" is the same basemap capped at zoom 8 (USGS has imagery everywhere up to there) and drawn under "base", so
// where USGS has no closer tiles (outside the US) the map shows blurry imagery instead of empty squares.
// USGS imagery fills everything outside its coverage with opaque white, also inside tiles that straddle the edge
// (borders, coasts), so the imagery basemaps load through "keyed://", which makes pure white transparent.
const keyed = (b, maxzoom) => maxzoom > 8 && b !== "topo";
const baseSource = (b, maxzoom = 16) => ({type: "raster", tiles: [`${keyed(b, maxzoom) ? "keyed://" : origin + "/"}tiles/${b}/{z}/{x}/{y}`], tileSize: 256, maxzoom, attribution: "USGS The National Map"});
async function keyedTile(params, abort) {
  const res = await fetch(params.url.replace("keyed://", origin + "/"), {signal: abort.signal});
  if (!res.ok) throw new Error(`Tile ${res.status}`);
  const bytes = await res.arrayBuffer(), bmp = await createImageBitmap(new Blob([bytes]));
  const c = new OffscreenCanvas(bmp.width, bmp.height), g = c.getContext("2d");
  g.drawImage(bmp, 0, 0);
  const img = g.getImageData(0, 0, c.width, c.height), d = img.data;
  let keyedOut = 0;
  for (let i = 0; i < d.length; i += 4) if (d[i] >= 253 && d[i + 1] >= 253 && d[i + 2] >= 253) { d[i + 3] = 0; keyedOut++; }
  if (!keyedOut) return {data: bytes};
  // JPEG smears the fill into a fringe of near-white pixels; clear those too, but only where they touch cleared ones.
  const w = c.width;
  for (let pass = 0; pass < 3; pass++) {
    const clear = [];
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] === 0 || d[i] < 215 || d[i + 1] < 215 || d[i + 2] < 215) continue;
      const p = i / 4, x = p % w;
      if ((x > 0 && d[i - 1] === 0) || (x < w - 1 && d[i + 7] === 0) || d[i - 4 * w + 3] === 0 || d[i + 4 * w + 3] === 0) clear.push(i);
    }
    for (const i of clear) d[i + 3] = 0;
  }
  g.putImageData(img, 0, 0);
  return {data: await (await c.convertToBlob({type: "image/png"})).arrayBuffer()};
}
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
    sources: {"base-low": baseSource(state.base, 8), base: baseSource(state.base), hydro: {type: "raster", tiles: [`${origin}/tiles/hydro/{z}/{x}/{y}`], tileSize: 256, maxzoom: 16},
      fires: geo(), points: geo(), hist: geo(), fc: geo(), heads: geo(), hot: geo(), obs: geo(), access: geo()},
    layers: [
      L("base-low", "raster", "base-low", {}),
      L("base", "raster", "base", {}),
      L("hydro", "raster", "hydro", {}, {visibility: state.labels ? "visible" : "none"}),
      // Translucent fills that overlap (forecast under current under past), thin solid borders, the current one bold.
      L("fires-fill", "fill", "fires", {"fill-color": RED, "fill-opacity": 0.15}),
      L("fc-fill", "fill", "fc", {"fill-color": byRank(BANDS), "fill-opacity": ["case", front, byRank(BAND_OPACITY), 0.1]}),
      L("sel-fill", "fill", "fires", {"fill-color": RED, "fill-opacity": 0.18}),
      L("hist-fill", "fill", "hist", {"fill-color": pastColor, "fill-opacity": 0.07}),
      L("fires-line", "line", "fires", {"line-color": RED, "line-width": 1.5, "line-opacity": 0.8}),
      L("fc-line", "line", "fc", {"line-color": byRank(BANDS), "line-width": 1.25, "line-opacity": ["case", front, 0.9, 0.4]}),
      L("hist-line", "line", "hist", {"line-color": pastColor, "line-width": 2, "line-opacity": 1}),
      L("sel-line", "line", "fires", {"line-color": RED, "line-width": 3}),
      L("obs-line", "line", "obs", {"line-color": OBSERVED, "line-width": 2, "line-dasharray": [0.5, 1.5]}, {"line-cap": "round"}),
      ...cased("access", "access", 4, 7, {"line-color": "#59DAD0"}),
      L("hot", "circle", "hot", {"circle-color": HOT, "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 1.5, 15, 5], "circle-stroke-color": CASE, "circle-stroke-width": 1}),
      ...cased("arrow", "fc", 3.5, 8, {"line-color": "#FFFFFF"}, {"line-cap": "round"}),
      L("arrow-head", "symbol", "heads", {}, {"icon-image": "head", "icon-rotate": ["get", "bearing"], "icon-rotation-alignment": "map",
        "icon-anchor": "top", "icon-allow-overlap": true, "icon-ignore-placement": true}),
      L("hist-label", "symbol", "hist", halo(pastColor), text("label", 12, alongLine)),
      L("obs-label", "symbol", "obs", halo(OBSERVED), text("label", 12, alongLine)),
      L("fc-label", "symbol", "heads", halo(byRank(BANDS), 2), text("label", 14, {"text-anchor": "left", "text-offset": [0.6, 0], "text-allow-overlap": true})),
      // Distance/rate labels only once zoomed in, so the fire overview stays uncluttered.
      {...L("arrow-label", "symbol", "heads", halo("#FFFFFF", 2), text("label", 13, {"text-variable-anchor": ["left", "right", "top", "bottom"], "text-radial-offset": 1.6})), minzoom: 12},
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
  filter(["fires-fill", "fires-line"], ["!=", ["get", "id"], sel]);
  filter(["sel-fill", "sel-line"], ["==", ["get", "id"], sel]);
  filter(["hist-fill", "hist-line", "hist-label"], histK);
  const shown = ["in", ["get", "where"], ["literal", ["front", ...["interior", "detached"].filter((w) => L[w])]]];
  filter(["fc-fill", "fc-line"], ["all", kind("band"), ["<=", ["get", "hours"], horizon], shown]);
  filter(["fc-label"], ["all", kind("label"), ["<=", ["get", "hours"], horizon]]);
  const arrows = ["all", kind("arrow"), ["<=", ["get", "band"], horizon], shown];
  filter(["arrow-case", "arrow-line", "arrow-head"], arrows);
  filter(["arrow-label"], [...arrows, front]);
  filter(["fire-label"], ["!=", ["get", "id"], sel]);
  filter(["obs-line", "obs-label"], step ? ["==", ["get", "hours"], horizon] : ["<=", ["get", "hours"], horizon]);
  vis(["fires-fill", "fires-line", "points", "fire-label"], L.current);
  vis(["sel-fill", "sel-line"], showCur);
  vis(["hist-fill", "hist-line", "hist-label"], L.previous);
  vis(["fc-fill", "fc-line", "fc-label"], L.forecast);
  vis(["arrow-case", "arrow-line", "arrow-head", "arrow-label"], L.arrows);
  vis(["hot"], L.hotspots && !(step && step.kind === "past"));
  vis(["obs-line", "obs-label"], state.compare);
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
    const head = s.kind === "now" ? "Now" : s.kind === "fc" ? `+${s.hours} h` : short(s.time, zone);
    const title = `${s.kind === "fc" ? "Predicted" : "Observed"} ${when(s.time, zone)}`;
    const progress = i / Math.max(1, cur.detail.history.length - 2);
    const color = s.kind === "past" ? `color-mix(in srgb, ${PAST[0]}, ${PAST[1]} ${Math.round(100 * progress)}%)` : s.kind === "fc" ? BANDS[Math.min(2, fc.hours.indexOf(s.hours))] : RED;
    const stop = `<li class="stop ${s.kind}" style="--stop-color:${color}"><button type="button" data-step="${i}" aria-pressed="${pressed}" title="${title}"><span class="k">${head}</span>` +
      `<span class="a">${s.kind === "fc" ? "+" : ""}${num(s.acres)} ac</span></button></li>`;
    return s.kind === "now" ? stop + '<li class="divider" aria-hidden="true"></li>' : stop;
  }).join("");
  for (const b of document.querySelectorAll("[data-mode]")) b.setAttribute("aria-pressed", b.dataset.mode === state.mode);
  $("#play").hidden = state.mode !== "step";
  $("#play").textContent = playing ? "❚❚" : "▶";
  $("#play").setAttribute("aria-label", playing ? "Pause" : "Play");
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

const metric = (n, unit, digits = 0) => Number.isFinite(n) ? n.toFixed(digits) + unit : "–";
function fuelName(code) {
  if (!Number.isInteger(code)) return "–";
  const groups = [[101,109,"Grass"],[121,124,"Grass–shrub"],[141,149,"Shrub"],
    [161,165,"Timber–understory"],[181,189,"Timber litter"],[201,204,"Slash / blowdown"]];
  const group = groups.find(([lo,hi]) => code >= lo && code <= hi);
  const nonburnable = {91:"Urban",92:"Snow / ice",93:"Agriculture",98:"Water",99:"Barren"};
  return group?.[2] || nonburnable[code] || "Unclassified";
}
function readConditions(f) {
  const active = () => state.fire === f.id;
  for (const id of ["fuel","wind","weather"]) $("#"+id).textContent = "…";
  $("#wx-source").textContent = "";
  const [lon,lat] = f.centroid;
  api("/identify/fuel?lon="+lon+"&lat="+lat).then(data => {
    if (!active()) return;
    $("#fuel").textContent = fuelName(data.value);
    $("#fuel").title = Number.isInteger(data.value) ? "LANDFIRE FBFM40 " + data.value + " at the fire center" : "";
  }).catch(() => { if (active()) $("#fuel").textContent = "–"; });
  api("/api/fires/"+f.id+"/weather?time="+encodeURIComponent(at.time)).then(wx => {
    if (!active()) return;
    $("#wind").textContent = metric(wx.wind_mph," mph") + (Number.isFinite(wx.wind_from) && wx.wind_mph > 0 ? " "+compass(wx.wind_from) : "");
    $("#weather").textContent = metric(wx.temp_f,"°F")+" · "+metric(wx.humidity,"% RH")+(wx.rain_mm > 0 ? " · rain" : "");
    $("#wx-source").textContent = "Fuel: LANDFIRE FBFM40. Wind (10 m, from) and air: " + wx.source + ", " + when(wx.time,zoneOf(f)) + ", at the fire center.";
  }).catch(() => { if (active()) { $("#wind").textContent = "–"; $("#weather").textContent = "–"; } });
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
  $("#pick-access").textContent = active ? "Cancel" : "Access point";
  $("#access-note").textContent = active ? "Tap the map at the access point." : "";
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
    link.title = accessPoint ? "Road directions to the access point (Google Maps)" : "Road directions to the route start (Google Maps)";
    if (map) {
      const dot = document.createElement("div");
      dot.className = "access-dot"; dot.title = "Incident access point";
      accessMarker = new maplibregl.Marker({element:dot}).setLngLat(destination).addTo(map);
    }
    link.href = "https://www.google.com/maps/dir/?" + new URLSearchParams({api:"1",destination:lat+","+lon,travelmode:"driving"});
  } else link.removeAttribute("href");
  $("#route-note").textContent = accessRoute ? accessRoute.properties.name + " (teal line; road status, slope and radio coverage not checked)" : "";
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
  document.body.classList.add("selected"); $(".sidebar").scrollTop = 0; map?.resize();
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
  document.body.classList.remove("selected"); map?.resize();
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
  for (const id of ["base", "base-low"]) { map.removeLayer(id); map.removeSource(id); }
  map.addSource("base-low", baseSource(b, 8)); map.addSource("base", baseSource(b));
  map.addLayer({id: "base-low", type: "raster", source: "base-low"}, "hydro");
  map.addLayer({id: "base", type: "raster", source: "base"}, "hydro");
}
function setLabels(on) {
  state.labels = on; writeUrl();
  $("#labels").setAttribute("aria-pressed", on);
  if (map && map.getLayer("hydro")) map.setLayoutProperty("hydro", "visibility", on ? "visible" : "none");
}

function initMap() {
  if (!window.maplibregl) { $("#count").textContent += " · Map library failed to load"; return; }
  maplibregl.addProtocol("keyed", keyedTile);
  map = new maplibregl.Map({container: "map", style: style(), bounds: [[-125, 24], [-66, 50]], fitBoundsOptions: {padding: 40},
    attributionControl: {compact: false}, maxPitch: 70, fadeDuration: reduced ? 0 : 300});
  window.opsMap = map; showPosition();
  earth = earthLayers(map, {time: () => at ? at.time : new Date().toISOString(), beforeId: "fires-fill", note: (s) => { $("#earth-note").textContent = s; }});
  if (at) fit(state.fire && byId.has(state.fire) ? fireBounds(byId.get(state.fire)) : allBounds());
  map.addControl(new maplibregl.NavigationControl({visualizePitch: true}), "top-right");
  map.addControl(new maplibregl.ScaleControl({unit: "imperial"}), "bottom-right");
  // The GPS button joins MapLibre's bottom-right stack, so it sits above the scale bar instead of on it.
  map.addControl({onAdd: () => { const b = $("#locate"); b.classList.add("maplibregl-ctrl"); return b; }, onRemove: () => {}}, "bottom-right");
  map.on("style.load", () => {
    addImages();
    listReady.then(() => {
      setFires(); applyView();
      earth.set(earthOpts); syncEarthControls();
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
      $("#access-note").textContent = "Access point "+accessPoint[1].toFixed(5)+", "+accessPoint[0].toFixed(5);
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
// Layers panel: opens from its button, closes on a second press, Escape, or a click anywhere else.
function setLegend(open) {
  $("#legend").classList.toggle("open", open);
  $("#legend-toggle").setAttribute("aria-expanded", open);
}
$("#legend-toggle").addEventListener("click", () => setLegend(!$("#legend").classList.contains("open")));
document.addEventListener("pointerdown", (e) => { if (!e.target.closest("#legend")) setLegend(false); });
// Weather & satellite controls drive earthlayers.js; the panel shows what is on.
function syncEarthControls() {
  const o = earth ? earth.opts : earthOpts;
  $("#wind-level").value = o.wind; $("#overlay").value = o.overlay;
  for (const b of document.querySelectorAll("[data-flow]")) b.setAttribute("aria-pressed", b.dataset.flow === o.flow);
  for (const b of document.querySelectorAll("[data-earth]")) b.setAttribute("aria-pressed", !!o[b.dataset.earth]);
}
function setEarth(change) { Object.assign(earthOpts, change); if (earth) earth.set(change); syncEarthControls(); writeUrl(); }
$("#wind-level").addEventListener("change", (e) => setEarth({wind: e.target.value}));
$("#overlay").addEventListener("change", (e) => setEarth({overlay: e.target.value}));
$("#wind-flow").addEventListener("click", (e) => { const b = e.target.closest("[data-flow]"); if (b) setEarth({flow: b.dataset.flow, wind: earthOpts.wind === "off" ? "surface" : earthOpts.wind}); });
document.querySelector(".lg-panel").addEventListener("click", (e) => { const b = e.target.closest("[data-earth]"); if (b) setEarth({[b.dataset.earth]: !earthOpts[b.dataset.earth]}); });
syncEarthControls();
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
  if (e.key === "Escape") { if (typing) e.target.blur(); if ($("#legend").classList.contains("open")) setLegend(false); else if (state.fire) overview(); return; }
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
  if (hoursBetween(at.time, new Date()) > 24) $("#replay").textContent = `Replay · ${when(at.time)}`;
  freshness();
  if (state.fire && byId.has(state.fire)) select(state.fire);
  else if (map) { state.fire = null; fit(allBounds()); }
}).catch((e) => {
  $("#count").innerHTML = `<span class="error" role="alert">Could not load fires: ${esc(e.message)}</span>`;
});
// The map library is a deferred script after this module; start the map once it has run.
if (window.maplibregl) initMap(); else $('script[src$="maplibre-gl.js"]').addEventListener("load", initMap);
