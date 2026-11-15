import {createNav} from "./nav.js";
import {createHistory} from "./history.js";
import {registerOffline, applyUpdate, saveArea, listAreas} from "./offline.js";
import {setActivity} from "./predictions.js";
import {windLayer} from "./wind.js";
// Ignopticon Ops: fire list, map, forecast and timeline from /api/fires (dev-server.js).
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
const num = (n) => Math.round(n).toLocaleString("en-US");
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

const params = new URLSearchParams(location.search);
const BASEMAPS = ["satellite"];
// Demo day: the page opens on Aug 2, 2026 18Z, the day with ELMFIRE forecasts for its fires. ?t=now shows the latest.
const DEMO_DAY = "2026-08-02T18:00Z";
// Demo default fire, used only when no ?fire= is given on the demo day: SINLAHEKIN (FEDS 66433, WA). A real wildfire (WFIGS incident),
// uncontained at issue, with bounded forecasts and FEDS truth at +12 h; the observed +12 h growth (38.9 km2) lies between the most
// likely (11.3 km2) and worst case (45.7 km2) areas (data/bounds/heldout_per_fire.csv). A demo pick with visible growth, not a claim about accuracy.
const DEMO_FIRE = 66433;
const demoOpen = !params.get("fire") && (!params.get("t") || params.get("t") === DEMO_DAY);
const state = {
  t: params.get("t") || DEMO_DAY, fire: +params.get("fire") || (demoOpen ? DEMO_FIRE : null), view: params.get("view") === "3d" ? "3d" : "2d",
  base: BASEMAPS.includes(params.get("base")) ? params.get("base") : "satellite", labels: params.get("labels") !== "off",
  filter: "", sort: ["closest", "size", "growth", "newest", "name"].includes(params.get("sort")) ? params.get("sort") : "closest",
  mode: "progression", step: 0, fc: null, compare: false,  // fc: forecast hour shown, or null (off)
  layers: {current: true, previous: true, forecast: true, worst: true, physics: false, hotspots: false, interior: false, detached: false, activity: false},
  arm: params.get("arm") || "harm-all",  // which next-day prediction model (data/samples/predictions)
  wind: params.get("wind") || "surface",  // wind particle height (wind.js): off, surface, or hPa
};
let fires = [], byId = new Map(), at = null, map = null, cur = null;


let gps = null, gpsMarker = null, gpsWatch = null, gpsMessage = "Waiting for GPS";
let connected = navigator.onLine;
function freshness() {
  $("#fresh").textContent = "Updated every 12 h" + (connected ? "" : " · OFFLINE");
  $("#fresh").classList.toggle("offline", !connected);
}
// A new version of the page waits in the service worker until told to take over. Switch at once (one reload) when it
// is found as the page opens; one found later waits for the next open, so the page never reloads under someone.
const opened = Date.now();
registerOffline({onStatus: status => {
  connected = status.online; freshness();
  if (status.updateAvailable && Date.now() - opened < 15000) applyUpdate();
}}).catch(() => {});
freshness();
let refreshNotice = null;
function offlineRefreshNotice() {
  const button = $("#refresh-data"), note = $("#refresh-note");
  button.classList.add("offline"); note.textContent = "OFFLINE";
  clearTimeout(refreshNotice);
  refreshNotice = setTimeout(() => { button.classList.remove("offline"); note.textContent = ""; }, 5000);
}
$("#refresh-data").addEventListener("click", () => {
  if (!connected || !navigator.onLine) return offlineRefreshNotice();
  $("#refresh-data").setAttribute("aria-busy", "true");
  location.reload();
});
// Chrome on Android supplies this event only when this exact app can be installed.  Keeping the
// browser's prompt preserves its consent step, while giving responders a clear in-app entry point.
let deferredInstall = null;
const installButton = $("#install-app");
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstall = event;
  installButton.hidden = false;
});
installButton.addEventListener("click", async () => {
  if (!deferredInstall) return;
  installButton.disabled = true;
  deferredInstall.prompt();
  try { await deferredInstall.userChoice; } finally {
    deferredInstall = null;
    installButton.hidden = true;
    installButton.disabled = false;
  }
});
window.addEventListener("appinstalled", () => {
  deferredInstall = null;
  installButton.hidden = true;
});
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
    nav.gps(coords.longitude, coords.latitude, coords.accuracy);
  }, () => { gps = null; gpsMarker?.remove(); gpsMessage = "GPS unavailable"; if (at) renderList(); nav.gpsError(); },
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
        sources:{[BASE_TILES[state.base].src]:[7,13]}, urls:["/api/fires"+query(), "/api/fires/"+f.id+query()],
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

// "This week vs 2003-2025": percentile of the fire's week (and region box, as scripts/history_calendar.py) from /data/calendar.
const WEEK_BOXES = [["california", [[-124.5, -120, 39, 42], [-124.5, -118.4, 35, 39], [-121, -114.3, 32.4, 35]]], ["pacific-nw", [[-125, -116.5, 42, 49.6]]], ["great-basin", [[-120, -111, 36.5, 42]]],
  ["southwest", [[-120, -103, 31, 37]]], ["rockies", [[-117, -104, 37, 49.6]]], ["southeast", [[-94.6, -75, 24, 36.5], [-82, -75, 36.5, 37.5]]], ["plains", [[-104, -93, 26, 49.6]]], ["northeast", [[-93, -66, 36.5, 49.6]]]];
const regionOf = ([lon, lat]) => (WEEK_BOXES.find(([, bs]) => bs.some(([w, e, s, n]) => lon >= w && lon < e && lat >= s && lat < n)) || ["us"])[0];
async function weekLine(f) {
  const day = String((at && at.snapshot) || state.t).slice(0, 10), m = /^(\d{4})-(\d\d)-(\d\d)/.exec(day);
  if (!m || +m[1] !== 2026 || !f.centroid) return "";
  const doy = Math.round((Date.UTC(2026, +m[2] - 1, +m[3]) - Date.UTC(2026, 0, 0)) / 864e5), wk = Math.min(51, Math.floor((doy - 1) / 7)), reg = regionOf(f.centroid);
  const j = await fetch(`/data/calendar/${reg}.json`).then((r) => r.ok ? r.json() : null).catch(() => null), p = j && j.pct2026.harm[wk];
  if (p == null) return "";
  const sfx = (n) => n + (n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10 > 3 ? 0 : n % 10]);
  return `<p class="muted" id="weekline">This week vs 2003-2025: ${sfx(p)} percentile (${esc(j.name)})</p>`;
}
const api = (path) => fetch(path).then((r) => r.json().then((j) => r.ok ? j : Promise.reject(new Error(j.error || r.statusText))));
const query = () => state.t ? `?time=${encodeURIComponent(state.t)}` : "";
const details = new Map();
function detail(id) {
  if (!details.has(id)) details.set(id, api(`/api/fires/${id}${query()}`).catch((e) => { details.delete(id); throw e; }));
  return details.get(id);
}
const listReady = api(`/api/fires${query()}`);
// #count only shows load errors.
const showError = (msg) => $("#count").insertAdjacentHTML("beforeend", `<span class="error" role="alert">${esc(msg)}</span> `);
// Date picker: Year, then Month, then Day, each listing only dates with a FEDS snapshot; a day opens at its latest
// snapshot (12:00 UTC). Changing the year or month moves to the latest available day in it. "Today" is t=now.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const go = (t) => { const p = new URLSearchParams(location.search); p.set("t", t); location.search = "?" + p; };
// Date: one button showing the page's day; it opens the fire-history calendar, where a month opens its days and a
// day sets the page's date. Fire perimeters (FEDS) exist from 2026; earlier days show that day's satellite fire layer.
let fedsDays = {}, firstFeds = null;  // "YYYY-MM-DD" -> its latest FEDS snapshot
const pageDay = () => (state.t === "now" ? new Date().toISOString() : state.t || DEMO_DAY).slice(0, 10);
function setDateButton() {
  const [y, m, d] = pageDay().split("-");
  $("#d-pick").textContent = `${MONTHS[+m - 1]} ${+d}, ${y}`;  // the date itself; the pressed Today button says it is today
  $("#d-today").setAttribute("aria-pressed", state.t === "now");
}
function goDay(day) {  // a FEDS day opens at its latest snapshot; any other day at 18:00 UTC (afternoon passes), with no fire open
  const p = new URLSearchParams(location.search);
  p.set("t", fedsDays[day] || `${day}T18:00:00Z`);
  if (!fedsDays[day]) p.delete("fire");
  location.search = "?" + p;
}
api("/data/fires/feds/index.json").then((index) => {
  for (const s of index.snapshots) fedsDays[s.slice(0, 10)] = s + "Z";
  firstFeds = Object.keys(fedsDays).sort()[0];
}).catch(() => {});
setDateButton();
$("#d-pick").onclick = () => fireHistory.open({pick: pageDay()});
$("#d-today").onclick = () => go("now");
$("#sort").value = state.sort;
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; writeUrl(); renderList(); });

function writeUrl() {
  const p = new URLSearchParams();
  if (state.t) p.set("t", state.t);
  if (state.fire) p.set("fire", state.fire);
  if (state.view === "3d") p.set("view", "3d");
  if (state.base !== "satellite") p.set("base", state.base);
  if (!state.labels) p.set("labels", "off");
  if (state.sort !== "closest") p.set("sort", state.sort);
  if (state.wind !== "surface") p.set("wind", state.wind);
  history.replaceState(null, "", "?" + p);
}

// List order. Closest needs a GPS position; without one every distance is Infinity, so it falls back to A-Z.
const byName = (a, b) => label(a).localeCompare(label(b));
const SORTS = {
  closest: (a, b) => distance(a) - distance(b) || byName(a, b),
  size: (a, b) => b.acres - a.acres || byName(a, b),
  growth: (a, b) => (b.growth_acres ?? -Infinity) - (a.growth_acres ?? -Infinity) || byName(a, b),
  newest: (a, b) => (a.days ?? Infinity) - (b.days ?? Infinity) || b.acres - a.acres,
  name: byName,
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
}


const where = (x) => x.properties.where || "front";  // files without classes are all front
const bandAcres = (fc, h, w) => fc.features.filter((x) => x.properties.kind === "band" && (h == null || x.properties.hours === h) && where(x) === w)
  .reduce((s, x) => s + (x.properties.acres || 0), 0);
function summary(fc, zone) {
  // Same per-hour figures as the forecast bar.
  const rows = fcStops(fc).map((s) => `<li>By +${s.hours} h (${short(s.time, zone)}): <b>+${num(s.acres)} ac</b></li>`);
  const a = fc.features.filter((x) => x.properties.kind === "arrow" && where(x) === "front").map((x) => x.properties).reduce((m, p) => !m || p.distance_m > m.distance_m ? p : m, null);
  const start = fc.features.find((x) => x.properties.kind === "start");
  return `<p>${fc.bound ? "Most likely growth" : "Predicted growth"}:</p><ul>${rows.join("")}</ul>` +
    (a ? `<p>Largest advance: <b>${miles(a.distance_m)} mi ${compass(a.bearing_deg)} at ${chains(a.rate_ch_per_h)} ch/h</b></p>` : "") +
    (start && start.properties.acres != null ? `<p class="muted">Model start area ${num(start.properties.acres)} ac</p>` : "") +
    (bandAcres(fc, null, "interior") + bandAcres(fc, null, "detached") ? `<p>Also predicted: ${num(bandAcres(fc, null, "interior"))} ac interior burnout${state.layers.interior ? "" : " (hidden)"}, ` +
      `${num(bandAcres(fc, null, "detached"))} ac on detached hotspots${state.layers.detached ? "" : " (hidden)"}</p>` : "");
}
const bounded = (d) => !!(d && d.forecast_upper);
function legendForecast(fc, d) {
  document.querySelectorAll(".legend .bnd").forEach((li) => { li.hidden = !bounded(d); });
  $("#fc-name").textContent = bounded(d) ? "Most likely" : "Forecast";
  $("#fc-legend").textContent = bounded(d) ? "Most likely growth (hatched, +3/+6/+12 h)" : fc || !cur ? "Predicted growth at the chosen hour (hatched)" : "No forecast for this fire";
  $("#fc-legend").closest("li").classList.toggle("none", !fc);
}
function containmentHtml(c, zone) {
  if (!c) return "";
  const txt = c.status === "contained" ? `contained since ${c.contained_since ? when(c.contained_since, zone) : "before the forecast"}`
    : c.status === "uncontained" ? "not contained" : "no incident match";
  return `<p class="muted" title="From WFIGS dates at the forecast issue time${c.percent_contained_latest != null ? `; latest reported ${c.percent_contained_latest}% contained, not as of issue` : ""}">Containment as of forecast: ${txt}${c.status === "unmatched" ? "" : " (WFIGS)"}</p>`;
}
function sumHtml(f, d, g) {
  const fc = d && d.forecast, row = (k, v) => `<li><span>${k}</span><b>${v}</b></li>`, rows = [row("Size now", `${num(f.acres)} ac · day ${num(f.days)}`)];
  const gt = /^[+−]/.test(g.text) ? g.text.replace(/ \([^)]*\)/, "").replace(/ in 12 h$/, "") : "";
  if (gt) rows.push(row("Past 12 h", `<span class="${g.cls}">${gt}</span>`));
  return `<ul class="dsum">${rows.join("")}</ul>`;
}
// Forecast numbers (likely / worst case, and what actually happened), shown inside the forecast box.
function fcRowsHtml(d) {
  const fc = d.forecast, row = (k, v) => `<li><span>${k}</span><b>${v}</b></li>`, rows = [];
  const h = fc.hours[fc.hours.length - 1], t = fcStops(fc).at(-1).acres;
  if (d.forecast_upper) rows.push(row(`Next ${h} h, forecast`, `+${num(t)} ac likely<br><span class="muted">+${num(Math.round(d.forecast_upper.bound.area_km2 * 247.105))} ac worst case</span>`));
  else rows.push(row(`Next ${h} h, forecast`, `+${num(t)} ac`));
  const o = (d.observed_after || []).find((x) => x.hours === h), now = d.history[d.history.length - 1].acres;
  if (o) rows.push(row(`Next ${h} h, actual`, `${o.acres >= now ? "+" : "−"}${num(Math.abs(o.acres - now))} ac`));
  return `<ul class="dsum fcsum">${rows.join("")}</ul>`;
}
function renderDetail(f, d) {
  const zone = zoneOf(f), g = growthText(f), fc = d && d.forecast;
  const fcHtml = !d ? `<p class="muted">Loading forecast…</p>` : fc ? `<div class="fc"><details><summary>Forecast details</summary>${fcRowsHtml(d)}${summary(fc, zone)}<p>Model: ${esc(fc.model)}</p><p>Issued ${when(fc.issued, zone)}</p></details>
<p class="muted">Unsuppressed spread potential — firefighting not modelled</p>
<p class="disclaimer">Experimental research forecast, not an official forecast. Do not use it alone for safety decisions.</p></div>` : `<div class="nofc"><h3>No spread forecast</h3><p>This fire has no forecast. The map shows observed perimeters only.</p></div>`;
  const hs = d && d.hotspots;
  $("#detail").innerHTML = `<button type="button" class="back" id="back">← All fires (Esc)</button>
<h2 id="detail-name">${esc(label(f))}</h2><p class="muted">${esc(f.place || "")} · FEDS fire ${f.id}</p>
${d ? containmentHtml(d.containment, zone) : ""}${sumHtml(f, d, g)}<button type="button" class="nav-open" data-nav-open><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 4.5 20.3l.7.7L12 18l6.8 3 .7-.7z" fill="currentColor"/></svg>Navigate to this fire</button>${fcHtml}
`;
  $("#sources").innerHTML = `<details><summary>Data sources</summary><ul><li>Perimeters: NASA FEDS (VIIRS 375 m), ${when(f.perimeter_time, zone)}</li>
<li>Hotspots: VIIRS S-NPP, NOAA-20, NOAA-21: ${hs ? `${num(hs.length)} detections in the 12 h to view time` : "none downloaded for this time"}</li>
${fc ? `<li>Forecast: ELMFIRE + fire-history growth model · ${esc(fc.source || fc.model)}</li>` : ""}${d && d.containment ? "<li>Containment: NIFC WFIGS incident records</li>" : ""}<li id="nav-src"${nav.sourceText() ? "" : " hidden"}>${esc(nav.sourceText())}</li><li id="wind-src"${windSource ? "" : " hidden"}>${esc(windSource)}</li>${conditionSources()}<li>Map: Sentinel-2 cloudless 2024 (EOX); labels: Natural Earth; 3D terrain: AWS Terrain Tiles</li></ul></details>`;
  $("#detail").hidden = false; $("#sources").hidden = false;
  weekLine(f).then((h) => { const p = $("#detail-name"); if (h && p && p.textContent === label(f) && !$("#weekline")) p.nextElementSibling.insertAdjacentHTML("afterend", h); }).catch(() => {});
  if (d) legendForecast(fc, d);
  $("#back").onclick = overview;
}

const RED = "#FF453A", CASE = "#0B0D10", BANDS = ["#FF45C6", "#C75AF5", "#9464FF"], BAND_OPACITY = [0.5, 0.4, 0.3];
const PAST = ["#995B30", "#FFD649"], OBSERVED = "#F0A8FF", HOT = "#FFFFFF", FONT = ["Noto Sans Bold"];
const EMPTY = {type: "FeatureCollection", features: []};
const FOG = "#D9E4EE";  // 3D horizon and fog
const origin = location.origin;
const byRank = (v) => ["match", ["get", "rank"], 0, v[0], 1, v[1], v[2]];
const pastColor = ["interpolate", ["linear"], ["get", "age"], 0, PAST[0], 1, PAST[1]];
const halo = (color, w = 2) => ({"text-color": color, "text-halo-color": CASE, "text-halo-width": w});
const text = (field, size, extra) => ({"text-field": ["get", field], "text-font": FONT, "text-size": size, ...extra});
const kind = (k) => ["==", ["get", "kind"], k];
const front = ["==", ["get", "where"], "front"];
// "base-low" is the same basemap capped at zoom 8 and drawn under "base". USGS has imagery everywhere to zoom 8, but
// over the ocean and outside the US its closer tiles are empty or missing, so the stretched zoom-8 image shows there.
// Tiles on the edge of USGS's coverage (borders, coasts) are part imagery, part opaque white, so the imagery basemaps
// load through "keyed://", which makes pure white (and the near-white JPEG fringe touching it) transparent.
// Map types and their dev-server tile sources. Satellite is EOX's Sentinel-2 cloudless mosaic: one colour-matched,
// cloud-free 10 m image of the whole world (land and sea), free for non-commercial use (CC BY-NC-SA 4.0).
const BASE_TILES = {
  satellite: {src: "s2cloudless", maxzoom: 14, attribution: "EOxCloudless 2024 by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2024)"},
};
const keyed = () => false;  // USGS imagery (which has the white fill) is no longer a basemap; keyedTile stays for reuse
const baseSource = (b, maxzoom = 16) => {
  const t = BASE_TILES[b];
  return {type: "raster", tiles: [`${keyed(b, maxzoom) ? "keyed://" : origin + "/"}tiles/${t.src}/{z}/{x}/{y}`], tileSize: 256,
    maxzoom: Math.min(maxzoom, t.maxzoom), attribution: t.attribution};
};
// 1024 px images on logical 512 px tiles retain mobile detail. They are an overlay, never the only map coverage.
const naipSource = {type: "raster", tiles: [`${origin}/tiles/naip/{z}/{x}/{y}`], tileSize: 512, minzoom: 15, maxzoom: 20,
  attribution: "USGS NAIP Plus / The National Map"};
async function keyedTile(params, abort) {
  const res = await fetch(params.url.replace("keyed://", origin + "/"), {signal: abort.signal});
  if (!res.ok) throw new Error(`Tile ${res.status}`);
  const bytes = await res.arrayBuffer(), bmp = await createImageBitmap(new Blob([bytes]));
  const c = new OffscreenCanvas(bmp.width, bmp.height), g = c.getContext("2d");
  g.drawImage(bmp, 0, 0);
  const img = g.getImageData(0, 0, c.width, c.height), d = img.data, w = c.width;
  let cleared = 0;
  for (let i = 0; i < d.length; i += 4) if (d[i] >= 253 && d[i + 1] >= 253 && d[i + 2] >= 253) { d[i + 3] = 0; cleared++; }
  if (!cleared) return {data: bytes};
  for (let pass = 0; pass < 3; pass++) {  // JPEG smears the fill into a near-white fringe: clear it where it touches cleared pixels
    const fringe = [];
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] === 0 || d[i] < 215 || d[i + 1] < 215 || d[i + 2] < 215) continue;
      const x = (i / 4) % w;
      if ((x > 0 && d[i - 1] === 0) || (x < w - 1 && d[i + 7] === 0) || d[i - 4 * w + 3] === 0 || d[i + 4 * w + 3] === 0) fringe.push(i);
    }
    for (const i of fringe) d[i + 3] = 0;
  }
  g.putImageData(img, 0, 0);
  return {data: await (await c.convertToBlob({type: "image/png"})).arrayBuffer()};
}
const lowVisible = (b) => BASE_TILES[b].src !== "s2cloudless";  // Sentinel-2 has no gaps to fill
// State, city and river names (data/labels/na-labels.json, scripts/make_labels.py): white text on a dark halo, no
// fills or lines, so nothing competes with the imagery or the fire colours. Natural Earth's z says from which zoom a
// name is worth showing; there is one layer per whole zoom because filters cannot compare a feature with the zoom.
function labelLayers() {
  const vis = state.labels ? "visible" : "none", kind = (k) => ["==", ["get", "k"], k];
  const text = (size, extra = {}) => ({"text-field": ["get", "n"], "text-font": FONT, "text-size": size, visibility: vis, ...extra});
  const paint = (color = "#FFFFFF") => ({"text-color": color, "text-halo-color": "rgba(0,0,0,0.75)", "text-halo-width": 1.4});
  // State and country borders: thin, uncoloured lines (so they never read as fire outlines), toggled with the names.
  const border = (id, k, paint) => ({id, type: "line", source: "borders", filter: kind(k), layout: {visibility: vis, "line-join": "round"}, paint});
  const layers = [
    border("lbl-border-state", "state", {"line-color": "rgba(255,255,255,0.45)", "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.6, 10, 1.4]}),
    border("lbl-border-country", "country", {"line-color": "rgba(255,255,255,0.8)", "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1, 10, 2.2]}),
    {id: "lbl-state", type: "symbol", source: "labels", minzoom: 3, maxzoom: 8, filter: kind("state"), paint: paint("rgba(255,255,255,0.85)"),
    layout: text(["interpolate", ["linear"], ["zoom"], 3, 10, 7, 14], {"text-transform": "uppercase", "text-letter-spacing": 0.15})}];
  for (let m = 2; m <= 8; m++) {
    const band = m === 8 ? [">", ["get", "z"], 7] : ["all", [">", ["get", "z"], m - 1], ["<=", ["get", "z"], m]];
    layers.push({id: `lbl-city-${m}`, type: "symbol", source: "labels", minzoom: m, filter: ["all", kind("city"), band], paint: paint(),
      layout: text(["interpolate", ["linear"], ["zoom"], 4, 11, 10, 14], {"symbol-sort-key": ["-", 0, ["get", "p"]]})});
    layers.push({id: `lbl-river-${m}`, type: "symbol", source: "labels", minzoom: m + 2, filter: ["all", kind("river"), band], paint: paint("#DCEBFF"),
      layout: text(11, {"symbol-placement": "line", "symbol-spacing": 400, "text-max-angle": 70})});
  }
  return layers;
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
    sky: {"sky-color": "#6E9CC8", "horizon-color": FOG, "fog-color": FOG},
    sources: {"base-low": baseSource(state.base, 8), base: baseSource(state.base), naip: naipSource,
      labels: {type: "geojson", data: new URL("../data/labels/na-labels.json", import.meta.url).href, attribution: "Natural Earth"},
      borders: {type: "geojson", data: new URL("../data/labels/na-borders.json", import.meta.url).href},
      fires: geo(), points: geo(), hist: geo(), fc: geo(), fcu: geo(), fcp: geo(), heads: geo(), hot: geo(), obs: geo(), access: geo()},
    layers: [
      // Sentinel-2 has no gaps, so the fallback would only take request slots from the real tiles.
      L("base-low", "raster", "base-low", {}, {visibility: lowVisible(state.base) ? "visible" : "none"}),
      L("base", "raster", "base", {}),
      L("naip", "raster", "naip", {"raster-resampling": "linear", "raster-fade-duration": 150}, {visibility: state.base === "satellite" ? "visible" : "none"}),
      ...labelLayers(),
      L("fires-fill", "fill", "fires", {"fill-color": RED, "fill-opacity": 0.1}),
      L("sel-fill", "fill", "fires", {"fill-color": RED, "fill-opacity": 0.12}),
      L("fc-fill", "fill", "fc", {"fill-color": byRank(BANDS), "fill-opacity": ["case", front, byRank(BAND_OPACITY), 0.15]}),
      L("fc-hatch", "fill", "fc", {"fill-pattern": "hatch", "fill-opacity": ["case", front, 1, 0.35]}),
      L("fcu-fill", "fill", "fcu", {"fill-color": BANDS[0], "fill-opacity": 0.08}),
      ...cased("hist", "hist", 2.5, 4.5, {"line-color": pastColor, "line-dasharray": [3, 1.5]}),
      ...cased("fc", "fc", 2, 4.5, {"line-color": byRank(BANDS), "line-opacity": ["case", front, 1, 0.5]}),
      L("fcu-line", "line", "fcu", {"line-color": "#FFB3E8", "line-width": 2, "line-dasharray": [4, 2]}),
      L("fcp-line", "line", "fcp", {"line-color": "#B8C0CC", "line-width": 2, "line-dasharray": [1, 2]}),
      ...cased("fires", "fires", 2, 4, {"line-color": RED}),
      ...cased("sel", "fires", 3.5, 7, {"line-color": RED}),
      ...cased("obs", "obs", 2.5, 5, {"line-color": OBSERVED, "line-dasharray": [0.5, 1.5]}, {"line-cap": "round"}),
      ...cased("access", "access", 4, 7, {"line-color": "#59DAD0"}),
      L("hot", "circle", "hot", {"circle-color": HOT, "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 1.5, 15, 5], "circle-stroke-color": CASE, "circle-stroke-width": 1}),
      L("hist-label", "symbol", "hist", halo(pastColor), text("label", 14, alongLine)),
      L("obs-label", "symbol", "obs", halo(OBSERVED), text("label", 14, alongLine)),
      L("fc-label", "symbol", "heads", halo(byRank(BANDS), 2.5), text("label", 17, {"text-anchor": "left", "text-offset": [0.6, 0], "text-allow-overlap": true})),
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
  const zone = zoneOf(d.fire), fc = d.forecast, n = d.history.length;
  const collection = (features) => ({type: "FeatureCollection", features});
  const ft = (geometry, properties) => ({type: "Feature", geometry, properties});
  map.getSource("hist").setData(collection(d.history.map((h, k) => ft(h.geometry, {k, age: n > 2 ? k / (n - 2) : 1, label: short(h.time, zone)}))));
  const hours = fc ? fc.hours : [];
  const rank = (h) => Math.min(Math.max(hours.indexOf(h), 0), 2);
  const fcFeatures = fc ? fc.features.filter((x) => !["start", "arrow"].includes(x.properties.kind) && x.geometry).map((x) => ft(x.geometry,
    {...x.properties, where: where(x), rank: rank(x.properties.hours), label: x.properties.kind === "band" ? `+${x.properties.hours} h` : ""})) : [];
  map.getSource("fc").setData(collection(fcFeatures));
  const extents = (f) => collection(f ? f.features.filter((x) => x.properties.kind === "extent" && x.geometry).map((x) => ft(x.geometry, x.properties)) : []);
  map.getSource("fcu").setData(extents(d.forecast_upper));
  map.getSource("fcp").setData(extents(d.forecast_physics));
  const heads = [];
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
  for (const s of ["hist", "fc", "fcu", "fcp", "heads", "hot", "obs", "access"]) map.getSource(s).setData(EMPTY);
}

// What the map shows. Observed strip: one past perimeter, Now (the current one), or Show all (every perimeter).
// Forecast bar, separately: one hour's predicted extent (its outline, with all growth since now filled in that hour's
// colour), drawn over Now; a past perimeter turns it off. Switching only changes filters and visibility.
function applyView() {
  if (!map || !map.getLayer("sel-line")) return;
  const L = state.layers, sel = state.fire ?? -1, d = cur && cur.detail;
  const step = state.mode === "step" && cur && cur.stops ? cur.stops[state.step] : null;
  const past = !!(step && step.kind === "past"), nowK = d ? d.history.length - 1 : -1;
  const histK = !step ? ["<", ["get", "k"], nowK] : past ? ["==", ["get", "k"], step.k] : ["==", ["get", "k"], -9];
  const h = d && d.forecast && !past && state.fc != null ? state.fc : -1;
  const r = h < 0 ? 0 : Math.min(Math.max(d.forecast.hours.indexOf(h), 0), 2);
  const vis = (ids, on) => ids.forEach((id) => map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  const filter = (ids, f) => ids.forEach((id) => map.setFilter(id, f));
  const atHour = ["==", ["get", "hours"], h];
  filter(["fires-fill", "fires-case", "fires-line"], ["!=", ["get", "id"], sel]);
  filter(["sel-fill", "sel-case", "sel-line"], ["==", ["get", "id"], sel]);
  filter(["hist-case", "hist-line", "hist-label"], histK);
  const shown = ["in", ["get", "where"], ["literal", ["front", ...["interior", "detached"].filter((w) => L[w])]]];
  filter(["fc-fill", "fc-hatch"], ["all", kind("band"), ["<=", ["get", "hours"], h], shown]);
  map.setPaintProperty("fc-fill", "fill-color", BANDS[r]);
  map.setPaintProperty("fc-fill", "fill-opacity", ["case", front, BAND_OPACITY[r], 0.15]);
  filter(["fc-case", "fc-line"], ["all", kind("extent"), atHour]);
  filter(["fc-label"], ["all", kind("label"), atHour]);
  filter(["fcu-fill", "fcu-line", "fcp-line"], ["all", kind("extent"), atHour]);
  filter(["fire-label"], ["!=", ["get", "id"], sel]);
  filter(["obs-case", "obs-line", "obs-label"], atHour);
  vis(["fires-fill", "fires-case", "fires-line", "points", "fire-label"], L.current);
  vis(["sel-fill", "sel-case", "sel-line"], L.current && !past);
  vis(["hist-case", "hist-line", "hist-label"], L.previous);
  vis(["fc-fill", "fc-hatch", "fc-case", "fc-line", "fc-label"], L.forecast);
  vis(["fcu-fill", "fcu-line"], L.worst && L.forecast && !!(d && d.forecast_upper));
  vis(["fcp-line"], L.physics && !!(d && d.forecast_physics));
  vis(["hot"], L.hotspots && !past);
  vis(["obs-case", "obs-line", "obs-label"], state.compare && h >= 0);
  updateActivity();
}
// Eric's next-day fire activity prediction (predictions.js), shown only for the dates it covers.
let setWind = null, windSource = "";  // the wind file in use, listed under Data sources
// Wind particles (wind.js) for the view date, at the chosen height.
function updateWind() {
  if (!setWind || !at) return;
  // Particles are drawn in 2D screen space and do not follow 3D terrain, so wind pauses in 3D (the choice is kept).
  const flat = state.view !== "3d";
  $("#wind-level").disabled = !flat;
  setWind(flat ? state.wind : "off", at.time).then((note) => {
    if (!flat) note = state.wind === "off" ? "" : "Wind is paused in 3D";
    // Success ("GFS ...") goes to Data sources; only problems stay under the Wind selector.
    const ok = note.startsWith("GFS");
    windSource = ok ? `Wind particles: ${note}` : "";
    $("#wind-note").textContent = ok ? "" : note;
    const li = $("#wind-src");
    if (li) { li.textContent = windSource; li.hidden = !windSource; }
  });
}
function updateActivity() {
  if (!map || !at) return;
  setActivity(map, {on: state.layers.activity, date: at.time.slice(0, 10), arm: state.arm, beforeId: "fires-fill"})
    .then((note) => { $("#activity-note").textContent = note; }).catch(() => { $("#activity-note").textContent = "Predictions failed to load"; });
}

// Observed perimeters only; forecast hours are a separate bar (fcStops).
function buildStops(d) {
  const n = d.history.length;
  return d.history.map((h, k) => ({kind: k === n - 1 ? "now" : "past", k, time: h.time, acres: h.acres}));
}
function fcStops(fc) {
  let acres = 0;  // predicted front growth since issue; the model's start area can differ from FEDS's
  // Bounded forecasts: "most likely" growth, the same figure as the fire panel. +12 h is the area-constrained bound; earlier hours
  // are the drawn extent's acres minus the start area (what the map shows), never above the +12 h figure.
  const last = fc.hours[fc.hours.length - 1], at = (kind, h) => (fc.features.find((x) => x.properties.kind === kind && (h == null || x.properties.hours === h)) || {properties: {}}).properties.acres;
  const likely = (h) => {
    if (h === last) return Math.round(fc.bound.area_km2 * 247.105);
    const e = at("extent", h), s = at("start");
    return e != null && s != null ? Math.min(Math.max(0, Math.round(e - s)), Math.round(fc.bound.area_km2 * 247.105)) : acres;
  };
  return fc.hours.map((h) => ({hours: h, time: new Date(Date.parse(fc.issued) + h * 3600e3).toISOString(), acres: (acres += bandAcres(fc, h, "front"), fc.bound ? likely(h) : acres)}));
}
function renderTimeline() {
  const tl = $("#timeline");
  if (!cur) { tl.hidden = true; $("#forecast").hidden = true; return; }
  tl.hidden = false;
  if (!cur.stops) { $("#stops").innerHTML = '<li class="muted">…</li>'; $("#forecast").hidden = true; return; }
  const zone = zoneOf(cur.fire), n = cur.stops.length;
  $("#stops").innerHTML = cur.stops.map((s, i) => {
    const pressed = state.mode === "step" && i === state.step;
    const label = s.kind === "now" ? "Now" : short(s.time, zone);
    const color = s.kind === "past" ? `color-mix(in srgb, ${PAST[0]}, ${PAST[1]} ${Math.round(100 * i / Math.max(1, n - 2))}%)` : RED;
    return `<li class="stop ${s.kind}" style="--stop-color:${color}"><button type="button" data-step="${i}" aria-pressed="${pressed}" title="Observed ${when(s.time, zone)} · ${num(s.acres)} ac">${label}</button></li>`;
  }).join("");
  $("#tl-all").setAttribute("aria-pressed", state.mode !== "step");
  renderForecast();
}
function renderForecast() {
  const fc = cur.detail.forecast, bar = $("#forecast");
  bar.hidden = !fc;
  if (!fc) return;
  const zone = zoneOf(cur.fire), stops = fcStops(fc);
  $("#fc-stops").innerHTML = [{hours: null}, ...stops].map((s, i) => {
    const label = s.hours == null ? "Off" : `+${s.hours} h`, color = s.hours == null ? "transparent" : BANDS[Math.min(2, i - 1)];
    const title = s.hours == null ? "Prediction off: show the fire as of the forecast issue time" : `${fc.bound ? "Most likely" : "Predicted"} by ${when(s.time, zone)} · +${num(s.acres)} ac`;
    return `<li class="stop" style="--stop-color:${color}"><button type="button" data-fc="${s.hours ?? "off"}" aria-pressed="${state.fc === s.hours}" title="${title}">${label}</button></li>`;
  }).join("");
  const s = stops.find((x) => x.hours === state.fc), obs = s && (cur.detail.observed_after || []).find((o) => o.hours === s.hours);
  const compare = $("#compare");
  compare.disabled = !obs;
  compare.title = !s ? "Choose a forecast hour first" : obs ? `Draw the perimeter observed ${when(obs.time, zone)}` : `No perimeter mapped at ${when(s.time, zone)} (FEDS maps every 12 h)`;
  compare.setAttribute("aria-pressed", state.compare && !!obs);
  const nowAcres = cur.detail.history[cur.detail.history.length - 1].acres;
  $("#fc-read").innerHTML = !s ? "" : `<span>${fc.bound ? "Most likely" : "Predicted"} by ${short(s.time, zone)}: <b>+${num(s.acres)} ac</b></span>` +
    (state.compare && obs ? `<span class="obs">Observed by ${short(obs.time, zone)}: <b>${obs.acres >= nowAcres ? "+" : "−"}${num(Math.abs(obs.acres - nowAcres))} ac</b></span>` : "");
}
function setStep(i, announce = true) {
  if (!cur || !cur.stops) return;
  state.mode = "step";
  state.step = Math.max(0, Math.min(cur.stops.length - 1, i));
  const s = cur.stops[state.step];
  if (s.kind === "past") state.fc = null;  // a forecast starts from now, not from an earlier perimeter
  renderTimeline(); applyView();
  if (announce) say(`${s.kind === "now" ? "Now" : "Observed"}, ${short(s.time, zoneOf(cur.fire))}, ${num(s.acres)} acres`);
}
function setForecast(h) {
  if (!cur || !cur.detail || !cur.detail.forecast) return;
  state.fc = h;
  if (h != null && state.mode === "step" && cur.stops[state.step].kind === "past") state.step = cur.stops.length - 1;
  renderTimeline(); applyView();
  const s = h != null && fcStops(cur.detail.forecast).find((x) => x.hours === h);
  say(s ? `Predicted +${h} h, ${short(s.time, zoneOf(cur.fire))}, plus ${num(s.acres)} acres` : "Forecast off");
}

const say = (msg) => { $("#announce").textContent = msg; };

const metric = (n, unit, digits = 0) => Number.isFinite(n) ? n.toFixed(digits) + unit : "Unavailable";
function fuelName(code) {
  if (!Number.isInteger(code)) return "Unavailable";
  const groups = [[101,109,"Grass"],[121,124,"Grass–shrub"],[141,149,"Shrub"],
    [161,165,"Timber–understory"],[181,189,"Timber litter"],[201,204,"Slash / blowdown"]];
  const group = groups.find(([lo,hi]) => code >= lo && code <= hi);
  const nonburnable = {91:"Urban / developed",92:"Snow / ice",93:"Agriculture",98:"Open water",99:"Barren"};
  return (group?.[2] || nonburnable[code] || "Unclassified") + " (FBFM " + code + ")";
}
// Conditions at the mapped fire center: one short line each; where they come from goes under Data sources.
let wxSource = "";
const conditionSources = () => `<li>Fuel: LANDFIRE LF2025 FBFM40 · Fire center</li><li id="wx-src"${wxSource ? "" : " hidden"}>${esc(wxSource)}</li>`;
function readConditions(f) {
  const active = () => state.fire === f.id;
  for (const id of ["fuel","wind","weather"]) $("#"+id).textContent = "Loading…";
  $("#wx-time").textContent = ""; wxSource = "";
  const [lon,lat] = f.centroid;
  api("/identify/fuel?lon="+lon+"&lat="+lat).then(data => {
    if (active()) $("#fuel").textContent = fuelName(data.value);
  }).catch(() => { if (active()) $("#fuel").textContent = "Unavailable"; });
  api("/api/fires/"+f.id+"/weather?time="+encodeURIComponent(at.time)).then(wx => {
    if (!active()) return;
    const zone = zoneOf(f), rain = Number.isFinite(wx.rain_mm) ? (wx.rain_mm > 0 ? `rain ${wx.rain_mm.toFixed(1)} mm` : "no rain") : "rain unavailable";
    $("#wx-time").textContent = "· " + when(wx.time, zone).split(" · ")[0];
    $("#wind").textContent = metric(wx.wind_mph," mph") + (Number.isFinite(wx.wind_from) && wx.wind_mph > 0 ? " from "+compass(wx.wind_from) : "") + " (10 m)";
    $("#weather").textContent = metric(wx.temp_f," °F") + " · " + metric(wx.humidity,"% RH") + " · " + rain;
    wxSource = `Weather: ${wx.source} · ${when(wx.time, zone)} · Fire center`;
    const li = $("#wx-src");
    if (li) { li.textContent = wxSource; li.hidden = false; }
  }).catch(() => { if (active()) { $("#wind").textContent = "Unavailable"; $("#weather").textContent = "Unavailable for this hour"; } });
}
// Navigation (nav.js) replaces the old access-route box: the access point is its destination, a loaded dispatch
// route is drawn and offered as a route option.
function routeKey() { return "ops.route." + (at && at.snapshot) + "." + state.fire; }
function parseRoute(value) {
  const feature = value?.type === "FeatureCollection" && value.features?.length === 1 ? value.features[0] : value;
  const geometry = feature?.type === "Feature" ? feature.geometry : feature;
  const points = geometry?.coordinates;
  if (geometry?.type !== "LineString" || !Array.isArray(points) || points.length < 2 || points.length > 20000 ||
      !points.every(p => Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]) && Math.abs(p[0]) <= 180 && Math.abs(p[1]) <= 85))
    throw new Error("Choose a GeoJSON file containing one route LineString.");
  const props = feature.properties || {};
  if (props.fire_id != null && String(props.fire_id) !== String(state.fire)) throw new Error("This route belongs to another fire.");
  return {type:"Feature",geometry:{type:"LineString",coordinates:points.map(p=>p.slice(0,2))},properties:{name:String(props.name || "Dispatch route").slice(0,120),fire_id:state.fire}};
}
function savedDispatch(provided) {
  let saved; try { saved = localStorage.getItem(routeKey()); } catch {}
  for (const source of [provided, saved]) {
    if (!source) continue;
    try { return parseRoute(typeof source === "string" ? JSON.parse(source) : source); } catch {}
  }
  return null;
}
const fireHistory = createHistory({$, esc, getMap: () => map, getFire: () => cur && cur.fire, goDay: (d) => goDay(d)});
const nav = createNav({$, esc, say: (m) => say(m), fit: (b) => fit(b), getMap: () => map, getFire: () => cur && cur.fire, getDetail: () => cur && cur.detail,
  query: () => query(), requestGps: () => requestGps(),
  accessPoint: () => { try { const p = JSON.parse(localStorage.getItem(routeKey() + ".point")); return Array.isArray(p) && p.length === 2 && p.every(Number.isFinite) ? p : null; } catch { return null; } },
  saveAccessPoint: (p) => { try { localStorage.setItem(routeKey() + ".point", JSON.stringify(p)); } catch {} }});
// The navigation panel is a popup over the map, opened from the fire card's "Navigate to this fire" button.
function navPopup(open) { $("#navpanel").hidden = !open; if (open) { nav.opened(); $("#nav-close").focus(); } }
$("#detail").addEventListener("click", (e) => { if (e.target.closest("[data-nav-open]")) navPopup(true); });
$("#nav-close").onclick = () => navPopup(false);
$("#nav-start").addEventListener("click", () => navPopup(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#navpanel").hidden) { e.stopImmediatePropagation(); navPopup(false); } }, true);
$("#route-file").onchange = async e => {
  const file = e.target.files[0], key = routeKey();
  e.target.value = "";
  if (!file) return;
  try {
    if (file.size > 2000000) throw new Error("Route file exceeds 2 MB.");
    const route = parseRoute(JSON.parse(await file.text()));
    if (!state.fire || routeKey() !== key) return;
    try { localStorage.setItem(key, JSON.stringify(route)); } catch {}
    nav.dispatch(route);
    fit(route.geometry.coordinates.map(([x,y])=>[x,y,x,y]).reduce(union));
  } catch (err) { if (state.fire && routeKey() === key) $("#nav-note").textContent = err.message; }
};

async function select(id, {fitMap = true} = {}) {
  const f = byId.get(id);
  if (!f) return;
  document.body.classList.add("selected"); $(".sidebar").scrollTop = 0; map?.resize();
  state.fire = id;
  nav.select(f); readConditions(f); cacheFire(f); state.mode = "progression"; state.fc = null; cur = {fire: f};
  writeUrl(); renderList(); renderDetail(f, null); renderTimeline();
  if (fitMap) fit(fireBounds(f));
  if (map && map.getSource("fires")) { clearDetail(); applyView(); }
  try {
    const d = await detail(id);
    if (state.fire !== id) return;
    cur = {fire: f, detail: d, stops: buildStops(d)};
    nav.dispatch(savedDispatch(d.access_route)); nav.detail(d);
    state.step = d.history.length - 1;
    if (d.forecast && state.fc == null) { const hs = d.forecast.hours; state.fc = hs.includes(12) ? 12 : hs[hs.length - 1]; }
    renderDetail(f, d); renderTimeline();
    if (map && map.getSource("fires")) setDetail(d);
    say(`Selected ${label(f)}, ${num(f.acres)} acres. ${d.forecast ? "Spread forecast available." : "No spread forecast."}`);
  } catch (e) {
    if (state.fire !== id) return;
    $("#detail").insertAdjacentHTML("beforeend", `<p class="error" role="alert">Could not load fire details: ${esc(e.message)}</p>`);
  }
}
function listSources() {
  if (!at) return;
  $("#sources").innerHTML = `<details><summary>Data sources</summary><ul><li>Perimeters: NASA FEDS (VIIRS 375 m), ${when(at.time)}</li>
<li>Fire names: NIFC WFIGS incidents, else county</li><li id="wind-src"${windSource ? "" : " hidden"}>${esc(windSource)}</li>
<li>Map: Sentinel-2 cloudless 2024 (EOX); labels: Natural Earth; 3D terrain: AWS Terrain Tiles</li></ul></details>`;
  $("#sources").hidden = false;
}
function overview() {
  document.body.classList.remove("selected"); map?.resize();
  state.fire = null; cur = null; nav.clear(); $("#navpanel").hidden = true;
  $("#detail").hidden = true; $("#timeline").hidden = true; $("#forecast").hidden = true;
  writeUrl(); renderList(); legendForecast(null); listSources();
  if (map && map.getSource("fires")) { clearDetail(); applyView(); }
  if (fires.length) fit(allBounds());
  say("All fires");
}

// Rotation and tilt only make sense in 3D: in 2D the map stays north-up and flat, and the compass is hidden.
function allowRotation(on) {
  if (!map) return;
  for (const h of [map.dragRotate, map.touchPitch]) on ? h.enable() : h.disable();
  on ? map.touchZoomRotate.enableRotation() : map.touchZoomRotate.disableRotation();
  on ? map.keyboard.enableRotation() : map.keyboard.disableRotation();
  map.getContainer().classList.toggle("flat", !on);
}
function setView(v) {
  state.view = v; writeUrl(); allowRotation(v === "3d"); updateWind();
  for (const b of document.querySelectorAll("[data-view]")) b.setAttribute("aria-pressed", b.dataset.view === v);
  if (!map || !map.getLayer("base")) return;  // style.load applies the URL's view later
  // Past the terrain's far edge (and between terrain tiles still loading) the page shows through: in 3D make that
  // the fog colour, so it reads as haze instead of a black streak or spike.
  map.getContainer().style.background = v === "3d" ? FOG : "";
  if (v === "3d") {
    if (!map.getSource("dem")) map.addSource("dem", {type: "raster-dem", tiles: [`${origin}/tiles/terrain/{z}/{x}/{y}`], encoding: "terrarium", tileSize: 256, maxzoom: 15, attribution: "Terrain: AWS Terrain Tiles"});
    map.setTerrain({source: "dem", exaggeration: 1.5});
    if (cur) fit(fireBounds(cur.fire)); else map.easeTo({pitch: 60, duration: reduced ? 0 : 500});
  } else {
    map.setTerrain(null);
    if (map.getSource("dem")) map.removeSource("dem");  // 2D pays nothing for terrain
    if (cur) fit(fireBounds(cur.fire)); else map.easeTo({pitch: 0, bearing: 0, duration: reduced ? 0 : 500});
  }
  map.once("idle", () => performance.mark("ops-view-" + v));
}
function setLabels(on) {
  state.labels = on; writeUrl();
  $("#labels").setAttribute("aria-pressed", on);
  if (!map) return;
  for (const l of map.getStyle().layers) if (l.id.startsWith("lbl-")) map.setLayoutProperty(l.id, "visibility", on ? "visible" : "none");
}

function initMap() {
  if (!window.maplibregl) { showError("Map library failed to load"); return; }
  maplibregl.addProtocol("keyed", keyedTile);
  map = new maplibregl.Map({container: "map", style: style(), bounds: [[-125, 24], [-66, 50]], fitBoundsOptions: {padding: 40}, maxZoom: 20,
    attributionControl: {compact: true}, maxPitch: 70, fadeDuration: reduced ? 0 : 300});
  window.opsMap = map; setWind = windLayer(map); allowRotation(state.view === "3d"); showPosition();
  if (at) fit(state.fire && byId.has(state.fire) ? fireBounds(byId.get(state.fire)) : allBounds());
  map.addControl(new maplibregl.NavigationControl({visualizePitch: true}), "top-right");
  // "My location" (Google-Maps-style crosshair) sits in the zoom group: under + / −, above the compass (3D only).
  $(".maplibregl-ctrl-zoom-out").after($("#locate"));
  map.addControl(new maplibregl.ScaleControl({unit: "imperial"}), "bottom-right");
  // The 2D/3D switch and Labels join MapLibre's bottom-right row with the scale bar and the credit.
  const control = (el) => ({onAdd: () => { el.classList.add("maplibregl-ctrl"); return el; }, onRemove: () => {}});
  map.addControl(control($("#viewtoggle")), "bottom-right");
  map.addControl(control($("#labels")), "bottom-right");
  map.on("style.load", () => {
    addImages();
    listReady.then(() => {
      setFires(); applyView(); updateWind(); nav.styleLoaded(); fireHistory.styleLoaded();
      if (cur && cur.detail) setDetail(cur.detail);
      if (state.view === "3d") setView("3d");
    }, () => { updateWind(); fireHistory.styleLoaded(); if (state.view === "3d") setView("3d"); });  // no fire list (e.g. before 2026)
  });
  map.once("idle", () => performance.mark("ops-map-idle"));
  // The compact credit opens on load; start it closed (just the i button).
  map.once("load", () => $(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show"));
  const pick = (e) => {
    if (state.fire && nav.click(e.lngLat.wrap())) return;
    const hit = map.queryRenderedFeatures(e.point, {layers: ["sel-fill", "fires-fill", "points"]}).find((x) => x.layer.id !== "points" || map.getZoom() < x.properties.zh);
    if (hit) select(hit.properties.id, {fitMap: hit.properties.id !== state.fire});
  };
  map.on("click", pick);
}

$("#filter").addEventListener("input", (e) => { state.filter = e.target.value; renderList(); if (map?.getSource("fires")) setFires();
  const matches = fires.filter(matchesFire); if (matches.length) fit(matches.map(f => f.bbox).reduce(union)); });
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
$("#wind-level").value = state.wind;
$("#wind-level").addEventListener("change", (e) => { state.wind = e.target.value; writeUrl(); updateWind(); });
$("#activity-arm").value = state.arm;
$("#activity-arm").addEventListener("change", (e) => { state.arm = e.target.value; updateActivity(); });
document.querySelector(".legend").addEventListener("click", (e) => {
  const b = e.target.closest("[data-layer]");
  if (!b) return;
  state.layers[b.dataset.layer] = !state.layers[b.dataset.layer];
  b.setAttribute("aria-pressed", state.layers[b.dataset.layer]);
  applyView();
  if (cur && cur.detail && /interior|detached/.test(b.dataset.layer)) renderDetail(cur.fire, cur.detail);
});
$("#wind-toggle").addEventListener("click", (e) => {
  const on = state.wind === "off"; state.wind = on ? "surface" : "off"; $("#wind-level").value = state.wind;
  e.currentTarget.setAttribute("aria-pressed", on); writeUrl(); updateWind();
});
$("#wind-toggle").setAttribute("aria-pressed", state.wind !== "off");
$("#viewtoggle").addEventListener("click", (e) => { const b = e.target.closest("[data-view]"); if (b && b.dataset.view !== state.view) setView(b.dataset.view); });
$("#labels").addEventListener("click", () => setLabels(!state.labels));
$("#timeline").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.step) setStep(+b.dataset.step);
  else if (b.id === "tl-all") { state.mode = "progression"; renderTimeline(); applyView(); say("All observed perimeters"); }
});
$("#forecast").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b || b.disabled) return;
  if (b.dataset.fc) setForecast(b.dataset.fc === "off" ? null : +b.dataset.fc);
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
    const hours = cur.detail.forecast ? cur.detail.forecast.hours : [], now = cur.stops.length - 1;
    const pos = state.fc != null ? now + 1 + hours.indexOf(state.fc) : state.mode === "step" ? state.step : now;
    const next = Math.max(0, Math.min(now + hours.length, pos + (e.key === "ArrowRight" ? 1 : -1)));
    if (next > now) { if (state.mode === "step") state.step = now; setForecast(hours[next - now - 1]); }
    else if (next === now && state.fc != null) setForecast(null);
    else setStep(next);
  }
});

$("#home").onclick = () => { state.filter = ""; $("#filter").value = ""; if (map?.getSource("fires")) setFires(); overview(); };
$("#return").onclick = () => { overview(); const matches = fires.filter(matchesFire); if (matches.length) fit(matches.map(f=>f.bbox).reduce(union)); };
$("#locate").onclick = () => { if (gps && map) map.easeTo({center:gps, zoom:12}); else requestGps(); };
requestGps();
// Location switched on after the page opened: ask again instead of staying on the old denial.
navigator.permissions?.query({name: "geolocation"}).then(p => { p.onchange = () => { if (p.state === "granted") requestGps(); }; }).catch(() => {});
// The list renders from the API without waiting for the map.
$("#labels").setAttribute("aria-pressed", state.labels);
for (const b of document.querySelectorAll("[data-view]")) b.setAttribute("aria-pressed", b.dataset.view === state.view);
listReady.then((data) => {
  if (data.snapshot && data.snapshot.slice(0, 10) !== pageDay() && state.t !== "now") noOutlines(data);
  at = data; fires = data.fires; byId = new Map(fires.map((f) => [f.id, f]));
  renderList(); if (!state.fire) listSources();
  performance.mark("ops-list");
  if (state.fire && byId.has(state.fire)) select(state.fire);
  else if (map) { state.fire = null; fit(allBounds()); }
}).catch((e) => {
  if (/No FEDS snapshot/.test(e.message)) return noOutlines(null);
  showError("Could not load fires: " + e.message);
});
// Days without FEDS perimeters (before 2026): say so, and map that day's satellite fire cells instead.
function noOutlines(data) {
  const day = pageDay();
  $("#count").innerHTML = `<span class="hint">No fire outlines for ${esc(day)} yet: perimeters start in ${firstFeds ? firstFeds.slice(0, 4) : "2026"}, and outlines for earlier years are being prepared. The map shows the satellite fire detections for that day.</span>`;
  if (!data) { $("#list").innerHTML = ""; listSources && listSources(); }
  const show = () => fireHistory.showDay(day);
  firmsArchive(day);
  if (map && map.isStyleLoaded()) show(); else (function wait() { map ? map.once("idle", show) : setTimeout(wait, 300); })();
}
// FIRMS archive points (/data/history/firms/SENSOR/daily/DAY.json) for the UTC day and the day before: MODIS 1 km and VIIRS
// 375 m, coloured by FRP. Sensors or days that are missing (404) are skipped.
async function firmsArchive(day) {
  const prev = new Date(Date.parse(day) - 864e5).toISOString().slice(0, 10), feats = [], n = {};
  const js = await Promise.all(["modis", "viirs-snpp", "viirs-noaa20", "viirs-noaa21"].flatMap((s) => [day, prev].map((x) =>
    fetch(`/data/history/firms/${s}/daily/${x}.json`).then((r) => r.ok ? r.json() : null).catch(() => null))));
  for (const j of js) {
    if (!j || !j.rows) continue;
    const i = (c) => j.columns.indexOf(c), fam = j.sensor === "modis" ? "MODIS" : "VIIRS";
    n[fam] = (n[fam] || 0) + j.rows.length;
    for (const r of j.rows) feats.push({type: "Feature", geometry: {type: "Point", coordinates: [r[i("longitude")], r[i("latitude")]]}, properties: {frp: +r[i("frp")] || 0}});
  }
  const note = feats.length ? `FIRMS archive (MODIS/VIIRS): ${Object.entries(n).map(([k, v]) => `${num(v)} ${k}`).join(", ")} detections, ${esc(prev)} to ${esc(day)} UTC. Colour = FRP: yellow under 10, orange 10-50, red 50-200, magenta over 200 MW.` : `No FIRMS archive data for ${esc(day)}.`;
  $("#count").insertAdjacentHTML("beforeend", `<span class="hint"><br>${note}</span>`);
  if (!feats.length) return;
  while (!(map && map.getLayer("fires-fill"))) await new Promise((r) => setTimeout(r, 300));   // wait for the map's own layers
  const data = {type: "FeatureCollection", features: feats};
  if (map.getSource("firms")) return map.getSource("firms").setData(data);
  map.addSource("firms", {type: "geojson", data});
  map.addLayer({id: "firms", type: "circle", source: "firms", paint: {"circle-color": ["step", ["get", "frp"], "#FFE08A", 10, "#FFA230", 50, "#FF4A2A", 200, "#C2185B"],
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 1.2, 9, 2.5, 15, 7], "circle-opacity": 0.8}}, map.getLayer("fires-fill") ? "fires-fill" : undefined);
}
// The map library is a deferred script after this module; start the map once it has run.
if (window.maplibregl) initMap(); else $('script[src$="maplibre-gl.js"]').addEventListener("load", initMap);