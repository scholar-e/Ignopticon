// The globe's wind particles on the ops map: GFS wind (earth/public/data/weather, the files the globe at / uses) at a
// chosen height, drawn as fading white streaks on a canvas over the map. Particles move in lon/lat, so the field
// stays put while you pan and zoom; brighter streaks are faster wind. Dates follow the view time.
const LEVELS = {surface: "surface-level", 1000: "isobaric-1000hPa", 850: "isobaric-850hPa", 700: "isobaric-700hPa",
  500: "isobaric-500hPa", 250: "isobaric-250hPa", 70: "isobaric-70hPa", 10: "isobaric-10hPa"};
const live = (iso) => Date.now() - Date.parse(iso) < 24 * 3600e3;
const files = new Map();
const load = (url) => {
  if (!files.has(url)) files.set(url, fetch(url).then((r) => r.ok ? r.json() : Promise.reject(new Error(r.status === 404 ? "no wind data for this day" : r.statusText)))
    .catch((e) => { files.delete(url); throw e; }));
  return files.get(url);
};

export function windLayer(map) {
  const canvas = Object.assign(document.createElement("canvas"), {className: "wind-canvas"});
  map.getContainer().appendChild(canvas);
  const ctx = canvas.getContext("2d");
  let field = null, particles = [], frame = null, request = 0;
  const box = () => map.getContainer().getBoundingClientRect();

  function resize() {
    const r = box(), dpr = devicePixelRatio || 1;
    canvas.width = r.width * dpr; canvas.height = r.height * dpr;
    canvas.style.width = r.width + "px"; canvas.style.height = r.height + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function sample(lon, lat) {  // bilinear on the 0.5° global grid (lon 0..359.5 east, lat 90..-90)
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
    p.lon = b.getWest() + Math.random() * (b.getEast() - b.getWest());
    p.lat = b.getSouth() + Math.random() * (b.getNorth() - b.getSouth());
    p.age = Math.floor(Math.random() * 100);
    return p;
  }
  function restart() {
    const r = box();
    ctx.clearRect(0, 0, r.width, r.height);
    particles = Array.from({length: Math.min(5000, Math.round(r.width * r.height / 220))}, () => spawn({}));
  }
  function step() {
    frame = requestAnimationFrame(step);
    const r = box();
    ctx.globalCompositeOperation = "destination-in"; ctx.fillStyle = "rgba(0,0,0,0.9)"; ctx.fillRect(0, 0, r.width, r.height);
    ctx.globalCompositeOperation = "source-over";
    const k = 0.25 / Math.pow(2, map.getZoom());  // degrees per (m/s) per frame: the same screen speed at any zoom
    for (const p of particles) {
      const w = sample(p.lon, p.lat);
      if (!w || ++p.age > 100) { spawn(p); p.age = 0; continue; }
      const from = map.project([p.lon, p.lat]);
      p.lon += w[0] * k / Math.cos(p.lat * Math.PI / 180); p.lat += w[1] * k;
      const to = map.project([p.lon, p.lat]);
      if (to.x < -50 || to.y < -50 || to.x > r.width + 50 || to.y > r.height + 50) { spawn(p); continue; }
      const v = Math.round(130 + 125 * Math.min(1, Math.hypot(w[0], w[1]) / 25));
      // A faint dark edge under each streak keeps it visible on bright desert as well as dark forest.
      ctx.lineWidth = 3; ctx.strokeStyle = "rgba(0,0,0,0.35)";
      ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y); ctx.stroke();
      ctx.lineWidth = 1.3; ctx.strokeStyle = `rgb(${v},${v},${v})`; ctx.stroke();
    }
  }
  function stop() { cancelAnimationFrame(frame); frame = null; ctx.clearRect(0, 0, canvas.width, canvas.height); }
  map.on("resize", () => { resize(); if (frame) restart(); });
  // Panning or zooming keeps every particle (they live in lon/lat); only the on-screen trails are cleared so they do not
  // smear. Particles that end up off screen are respawned in view one by one by step(), as aged ones are.
  map.on("move", () => { if (frame) ctx.clearRect(0, 0, canvas.width, canvas.height); });

  // Shows the wind at level ("off", "surface", or a pressure in hPa) for the view time; returns a note for the Key.
  return async function setWind(level, time) {
    const mine = ++request;
    stop();
    if (!LEVELS[level]) return "";
    const day = time.slice(0, 10).replace(/-/g, "/"), lev = LEVELS[level];
    const url = live(time) ? `/data/weather/current/current-wind-${lev}-gfs-1.0.json` : `/data/weather/${day}/1800-wind-${lev}-gfs-1.0.json`;
    let u, v;
    try { [u, v] = await load(url); } catch (e) { return "Wind: " + e.message; }
    if (mine !== request) return "";  // a newer choice arrived while this one loaded
    field = {h: u.header, u: u.data, v: v.data};
    resize(); restart(); step();
    return `GFS ${u.header.refTime.slice(0, 13).replace("T", " ")}Z · brighter = faster`;
  };
}
