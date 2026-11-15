// The earth globe's weather and satellite layers on the ops map: wind particles (any height, wind or smoke flow),
// all VIIRS hotspots, the fire-heat (FRP) overlay, and one satellite underlay (fuel, I5 heat, I4 fire band).
// Data files are the globe's own (earth/public/data, dev-server.js tiles); dates follow the view time.
const origin = location.origin;
const LEVELS = {surface: "surface-level", 1000: "isobaric-1000hPa", 850: "isobaric-850hPa", 700: "isobaric-700hPa",
  500: "isobaric-500hPa", 250: "isobaric-250hPa", 70: "isobaric-70hPa", 10: "isobaric-10hPa"};
export const OVERLAYS = {
  fuel: {tiles: "/tiles/fuel/{z}/{x}/{y}", maxzoom: 15, label: "Fuel (LANDFIRE FBFM40)"},
  "heat-day": {tiles: "/tiles/thermal/Day/{date}/{z}/{x}/{y}", maxzoom: 9, label: "Heat, day (VIIRS I5)"},
  "heat-night": {tiles: "/tiles/thermal/Night/{date}/{z}/{x}/{y}", maxzoom: 9, label: "Heat, night (VIIRS I5)"},
  "band-day": {tiles: "/data/thermal/i4/{date}/N21-day/{z}/{x}/{y}.png", maxzoom: 9, label: "Fire band, day (NOAA-21 I4)"},
  "band-night": {tiles: "/data/thermal/i4/{date}/N21-night/{z}/{x}/{y}.png", maxzoom: 9, label: "Fire band, night (NOAA-21 I4)"},
};

// FRP (MW) colors, log scale from dull red to white-hot: the globe's FIRE_HEAT_COLORS (products.js).
const HEAT_STOPS = [[-1, [90, 0, 0]], [0, [170, 20, 0]], [1, [240, 80, 0]], [2, [255, 170, 0]], [3, [255, 235, 120]], [3.6, [255, 255, 235]]];
export function frpColor(mw) {
  const x = Math.log10(Math.max(mw, 0.1));
  for (let i = 1; i < HEAT_STOPS.length; i++) {
    const [x0, c0] = HEAT_STOPS[i - 1], [x1, c1] = HEAT_STOPS[i];
    if (x <= x1 || i === HEAT_STOPS.length - 1) {
      const f = Math.max(0, Math.min(1, (x - x0) / (x1 - x0)));
      return c0.map((v, k) => Math.round(v + f * (c1[k] - v)));
    }
  }
}
const FRP_CLASSES = [1, 3, 10, 30, 100, 300];  // one point color per half decade, as on the globe
const rgb = (c) => `rgb(${c.join(",")})`;

const live = (iso) => Date.now() - Date.parse(iso) < 24 * 3600e3;
const ymd = (iso) => iso.slice(0, 10);
const dayPath = (iso) => ymd(iso).replace(/-/g, "/");
const json = (url) => fetch(url).then((r) => r.ok ? r.json() : Promise.reject(new Error(r.status === 404 ? "no data for this day" : r.statusText)));

export function earthLayers(map, {time, beforeId, note}) {
  const opts = {wind: "off", flow: "wind", hotspots: false, heat: false, overlay: "none"};
  const cache = new Map();
  const load = (url) => { if (!cache.has(url)) cache.set(url, json(url).catch((e) => { cache.delete(url); throw e; })); return cache.get(url); };
  const notes = {};
  const say = (k, msg) => { notes[k] = msg; note(Object.values(notes).filter(Boolean).join(" · ")); };

  // ---- Raster underlay (one at a time), above the basemap and below the fire layers.
  function setOverlay() {
    if (map.getLayer("earth-overlay")) { map.removeLayer("earth-overlay"); map.removeSource("earth-overlay"); }
    const o = OVERLAYS[opts.overlay];
    if (!o) return;
    map.addSource("earth-overlay", {type: "raster", tileSize: 256, maxzoom: o.maxzoom,
      tiles: [origin + o.tiles.replace("{date}", ymd(time()))], attribution: o.label});
    map.addLayer({id: "earth-overlay", type: "raster", source: "earth-overlay", paint: {"raster-opacity": 0.8}}, beforeId);
  }

  // ---- All VIIRS hotspots of the view day, up to the view time, colored by FRP; zoomed in, a point is its footprint.
  let hot = null;
  async function setHotspots() {
    const src = map.getSource("earth-hot");
    if (!opts.hotspots) { if (src) map.setLayoutProperty("earth-hot", "visibility", "none"); return; }
    const t = time(), url = live(t) ? "/data/fires/viirs/current.json" : `/data/fires/viirs/${dayPath(t)}.json`;
    try { hot = await load(url); say("hot", ""); } catch (e) { say("hot", "Hotspots: " + e.message); hot = null; }
    const until = Date.parse(t) / 1000, features = [];
    if (hot) for (const [sensor, rows] of Object.entries(hot.sensors)) for (const [lon, lat, frp, when, , scan] of rows) {
      if (when > until) continue;  // replay: nothing detected after the view time
      // Footprint radius in pixels at zoom 16: scan (km) across, Web Mercator meters per pixel at this latitude.
      const r16 = scan * 500 / (156543.03 * Math.cos(lat * Math.PI / 180) / 65536);
      features.push({type: "Feature", geometry: {type: "Point", coordinates: [lon, lat]}, properties: {frp, sensor, r16}});
    }
    const data = {type: "FeatureCollection", features};
    if (src) { src.setData(data); map.setLayoutProperty("earth-hot", "visibility", "visible"); return; }
    map.addSource("earth-hot", {type: "geojson", data});
    const steps = ["step", ["get", "frp"], rgb(frpColor(0.5))];
    for (const c of FRP_CLASSES) steps.push(c, rgb(frpColor(c)));
    map.addLayer({id: "earth-hot", type: "circle", source: "earth-hot", paint: {"circle-color": steps, "circle-opacity": 0.9,
      "circle-radius": ["interpolate", ["exponential", 2], ["zoom"], 3, 1.5, 8, 2.5, 16, ["max", 2.5, ["get", "r16"]]]}}, beforeId);
  }

  // ---- Fire heat: the day's smoothed FRP grid, reprojected to Web Mercator rows and drawn as an image.
  async function setHeat() {
    if (map.getLayer("earth-heat")) { map.removeLayer("earth-heat"); map.removeSource("earth-heat"); }
    if (!opts.heat) return say("heat", "");
    const t = time(), url = live(t) ? "/data/fires/viirs/current-heat.json" : `/data/fires/viirs/${dayPath(t)}-heat.json`;
    let grid;
    try { [grid] = await load(url); say("heat", ""); } catch (e) { return say("heat", "Fire heat: " + e.message); }
    if (!opts.heat || map.getLayer("earth-heat")) return;
    const {nx, ny, lo1, la1, dx, dy} = grid.header, la2 = la1 - (ny - 1) * dy;
    const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
    const top = merc(la1), bottom = merc(la2), H = ny * 2;
    const c = Object.assign(document.createElement("canvas"), {width: nx, height: H}), g = c.getContext("2d"), img = g.createImageData(nx, H);
    for (let row = 0; row < H; row++) {  // each image row is evenly spaced in Mercator y; find its grid row
      const lat = (2 * Math.atan(Math.exp(top + (bottom - top) * (row + 0.5) / H)) - Math.PI / 2) * 180 / Math.PI;
      const j = Math.round((la1 - lat) / dy);
      if (j < 0 || j >= ny) continue;
      for (let i = 0; i < nx; i++) {
        const mw = grid.data[j * nx + i];
        if (!(mw > 0.3)) continue;
        const [r, gg, b] = frpColor(mw), k = (row * nx + i) * 4;
        img.data.set([r, gg, b, Math.round(255 * Math.min(1, mw / 3) * 0.85)], k);
      }
    }
    g.putImageData(img, 0, 0);
    const lo2 = lo1 + (nx - 1) * dx;
    map.addSource("earth-heat", {type: "image", url: c.toDataURL(), coordinates: [[lo1, la1], [lo2, la1], [lo2, la2], [lo1, la2]]});
    map.addLayer({id: "earth-heat", type: "raster", source: "earth-heat", paint: {"raster-opacity": 0.9, "raster-resampling": "linear"}}, beforeId);
  }

  // ---- Wind particles on a canvas over the map, moving in lon/lat so panning and zooming need no restart.
  const canvas = Object.assign(document.createElement("canvas"), {className: "earth-wind"});
  map.getContainer().appendChild(canvas);
  let field = null, particles = [], frame = null, sources = [];
  function resize() {
    const r = map.getContainer().getBoundingClientRect(), dpr = devicePixelRatio || 1;
    canvas.width = r.width * dpr; canvas.height = r.height * dpr;
    canvas.style.width = r.width + "px"; canvas.style.height = r.height + "px";
    canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function sample(lon, lat) {
    const {lo1, la1, dx, dy, nx, ny} = field.h;
    const x = (((lon - lo1) % 360) + 360) % 360 / dx, y = (la1 - lat) / dy;
    if (y < 0 || y > ny - 1) return null;
    const x0 = Math.floor(x), y0 = Math.floor(y), x1 = (x0 + 1) % nx, y1 = Math.min(y0 + 1, ny - 1), fx = x - x0, fy = y - y0;
    const at = (a, i, j) => a[j * nx + i];
    const lerp = (a) => (at(a, x0, y0) * (1 - fx) + at(a, x1, y0) * fx) * (1 - fy) + (at(a, x0, y1) * (1 - fx) + at(a, x1, y1) * fx) * fy;
    return [lerp(field.u), lerp(field.v)];
  }
  function spawn(p) {
    const b = map.getBounds();
    if (opts.flow === "smoke" && sources.length) {  // smoke starts at a hotspot in view, more often at stronger ones
      const s = sources[Math.floor(Math.random() * sources.length)];
      p.lon = s[0] + (Math.random() - 0.5) * 0.01; p.lat = s[1] + (Math.random() - 0.5) * 0.01;
    } else { p.lon = b.getWest() + Math.random() * (b.getEast() - b.getWest()); p.lat = b.getSouth() + Math.random() * (b.getNorth() - b.getSouth()); }
    p.age = Math.floor(Math.random() * (opts.flow === "smoke" ? 300 : 100));
    return p;
  }
  function smokeSources() {
    sources = [];
    if (!hot) return;
    const b = map.getBounds(), until = Date.parse(time()) / 1000;
    for (const rows of Object.values(hot.sensors)) for (const [lon, lat, frp, when] of rows) {
      if (when > until || !b.contains([lon, lat])) continue;
      for (let k = 0; k < Math.max(1, Math.round(Math.sqrt(frp))); k++) sources.push([lon, lat]);
    }
  }
  function restart() {
    const g = canvas.getContext("2d"), r = map.getContainer().getBoundingClientRect();
    g.clearRect(0, 0, r.width, r.height);
    if (opts.flow === "smoke") smokeSources();
    const n = Math.min(5000, Math.round(r.width * r.height / (opts.flow === "smoke" ? 300 : 220)));
    particles = Array.from({length: n}, () => spawn({}));
  }
  function step() {
    frame = requestAnimationFrame(step);
    const g = canvas.getContext("2d"), r = map.getContainer().getBoundingClientRect(), smoke = opts.flow === "smoke";
    g.globalCompositeOperation = "destination-in"; g.fillStyle = `rgba(0,0,0,${smoke ? 0.96 : 0.9})`; g.fillRect(0, 0, r.width, r.height);
    g.globalCompositeOperation = "source-over";
    const k = 0.25 / Math.pow(2, map.getZoom()), maxAge = smoke ? 300 : 100;  // degrees per (m/s) per frame: same screen speed at any zoom
    g.lineWidth = 1.1;
    for (const p of particles) {
      const w = sample(p.lon, p.lat);
      if (!w || ++p.age > maxAge) { spawn(p); p.age = 0; continue; }
      const from = map.project([p.lon, p.lat]);
      p.lon += w[0] * k / Math.cos(p.lat * Math.PI / 180); p.lat += w[1] * k;
      const to = map.project([p.lon, p.lat]);
      if (to.x < -50 || to.y < -50 || to.x > r.width + 50 || to.y > r.height + 50) { spawn(p); continue; }
      const speed = Math.hypot(w[0], w[1]);
      if (smoke) { const a = p.age / maxAge; g.lineWidth = 2 + 14 * a; g.strokeStyle = `rgba(215,215,210,${0.18 * (1 - a)})`; }
      else { const v = Math.round(85 + 170 * Math.min(1, speed / 25)); g.strokeStyle = `rgb(${v},${v},${v})`; }
      g.beginPath(); g.moveTo(from.x, from.y); g.lineTo(to.x, to.y); g.stroke();
    }
  }
  async function setWind() {
    cancelAnimationFrame(frame); frame = null;
    canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    if (opts.wind === "off") return say("wind", "");
    const t = time(), lev = LEVELS[opts.wind];
    const url = live(t) ? `/data/weather/current/current-wind-${lev}-gfs-1.0.json` : `/data/weather/${dayPath(t)}/1800-wind-${lev}-gfs-1.0.json`;
    try {
      const [u, v] = await load(url);
      field = {h: u.header, u: u.data, v: v.data};
      say("wind", `Wind: GFS ${u.header.refTime.slice(0, 13).replace("T", " ")}Z`);
    } catch (e) { return say("wind", "Wind: " + e.message); }
    if (opts.flow === "smoke" && !hot) { try { hot = await load(live(t) ? "/data/fires/viirs/current.json" : `/data/fires/viirs/${dayPath(t)}.json`); } catch {} }
    if (frame || opts.wind === "off") return;
    resize(); restart(); step();
  }
  map.on("resize", () => { resize(); if (frame) restart(); });
  map.on("move", () => { if (frame) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height); });
  map.on("moveend", () => { if (frame) restart(); });

  // The map layers live in the style: apply what changed once it is ready (and everything again after a reload).
  // map.isStyleLoaded() is false while any tiles load, so readiness is tracked here instead.
  let ready = false, applied = {};
  map.on("style.load", () => { ready = true; applied = {}; });
  function apply() {
    if (ready) {
      if (applied.overlay !== opts.overlay) setOverlay();
      if (applied.hotspots !== opts.hotspots) setHotspots();
      if (applied.heat !== opts.heat) setHeat();
      Object.assign(applied, {overlay: opts.overlay, hotspots: opts.hotspots, heat: opts.heat});
    }
    if (applied.wind !== opts.wind || applied.flow !== opts.flow) { applied.wind = opts.wind; applied.flow = opts.flow; setWind(); }
  }

  return {
    opts,
    set(change) { Object.assign(opts, change); apply(); },
    refresh() { applied = {}; field = null; apply(); },  // view time changed: reload every layer for the new date
  };
}
