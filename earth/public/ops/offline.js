// Offline support for the ops page: registers sw.js and saves map areas (tiles + data) into Cache Storage.
//   registerOffline({onStatus})  -> reports {online, fromCacheSince, updateAvailable, persisted} on every change
//   applyUpdate()                -> activates a waiting new version, then reloads the page
//   estimateArea({bbox, bufferKm, sources}) -> {tiles, mb, bySource}
//   saveArea({id, label, bbox, bufferKm, sources, urls, onProgress, signal}) -> the saved area's record
//   listAreas(), deleteArea(id), clearAll(), storageInfo()
// bbox is [west, south, east, north] in degrees; sources maps a /tiles/ source to [minZoom, maxZoom].

// Defaults for one fire: about 130 MB for a 60 km box plus the 10 km buffer (from AVG_KB); topo is left out.
export const DEFAULT_SOURCES = {s2cloudless: [8, 14], imagerytopo: [8, 14], hydro: [8, 14], terrain: [8, 12]};
export const DEFAULT_BUFFER_KM = 10;
// Mean tile size in KB, measured over Widemouth 2 (UT) at z8-15 in Nov 2026; terrain is the z8-12 mean.
const AVG_KB = {satellite: 20, imagerytopo: 23, topo: 17, hydro: 2, terrain: 100};
const CONCURRENCY = 6;
const SAVE = "X-Ops-Save";  // tells sw.js not to copy the tile into the passive cache too
const INDEX = "ops-index";
const indexKey = (id) => `/ops/offline-areas/${encodeURIComponent(id)}`;
const areaCache = (id) => `ops-area-${id}`;

let registration = null, updateRequested = false;

export async function registerOffline({onStatus = () => {}} = {}) {
  const status = {online: navigator.onLine, fromCacheSince: null, updateAvailable: false, persisted: null};
  const emit = (change) => { Object.assign(status, change); onStatus({...status}); };
  // navigator.onLine stays true when the server is down, so also ask the server (HEAD passes sw.js untouched).
  const probe = () => fetch(new URL("sw.js", import.meta.url), {method: "HEAD", cache: "no-store"})
    .then((r) => emit({online: r.status < 500}), () => emit({online: false}));
  addEventListener("online", probe);
  addEventListener("offline", () => emit({online: false}));
  probe();
  if (!("serviceWorker" in navigator)) return emit({}), status;  // needs HTTPS (or localhost)

  // sw.js reports each API answer: ok from the network, or a cached copy with the time it was saved.
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data?.type !== "ops-net") return;
    if (e.data.ok) return emit({online: navigator.onLine, fromCacheSince: null});
    const at = e.data.cachedAt, old = status.fromCacheSince;
    emit({online: false, fromCacheSince: at && (!old || at < old) ? at : old});  // the oldest data on screen
  });
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (updateRequested) location.reload(); });

  // Scope / so the page works offline at / as well as /ops/ (dev-server.js sends Service-Worker-Allowed: /).
  registration = await navigator.serviceWorker.register(new URL("sw.js", import.meta.url), {scope: "/"});
  // A new version only counts as an update when an old one controls the page (not on first install).
  const check = () => emit({updateAvailable: !!(registration.waiting && navigator.serviceWorker.controller)});
  registration.addEventListener("updatefound", () => {
    const w = registration.installing;
    w?.addEventListener("statechange", () => { if (w.state === "installed") check(); });
  });
  check();

  if (navigator.storage?.persist) {
    // Browsers may refuse (Chrome grants it to installed or often-used sites); best-effort storage can be evicted.
    emit({persisted: (await navigator.storage.persisted()) || (await navigator.storage.persist())});
  }
  return status;
}

export function applyUpdate() {
  if (!registration?.waiting) return false;
  updateRequested = true;
  registration.waiting.postMessage({type: "skip-waiting"});
  return true;
}

// ---- Tile math (Web Mercator, 256 px tiles) ----
const tileX = (lon, z) => Math.min(2 ** z - 1, Math.max(0, Math.floor((lon + 180) / 360 * 2 ** z)));
const tileY = (lat, z) => {
  const r = Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI / 180;
  return Math.min(2 ** z - 1, Math.max(0, Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * 2 ** z)));
};

function buffered([w, s, e, n], km) {
  const dLat = km / 111.32, dLon = km / (111.32 * Math.cos((s + n) / 2 * Math.PI / 180));
  return [w - dLon, s - dLat, e + dLon, n + dLat];
}

function tileUrls(bbox, bufferKm, sources) {
  const [w, s, e, n] = buffered(bbox, bufferKm), urls = [];
  for (const [source, [z0, z1]] of Object.entries(sources)) {
    for (let z = z0; z <= z1; z++) {
      for (let x = tileX(w, z); x <= tileX(e, z); x++) {
        for (let y = tileY(n, z); y <= tileY(s, z); y++) urls.push({source, url: `/tiles/${source}/${z}/${x}/${y}`});
      }
    }
  }
  return urls;
}

export function estimateArea({bbox, bufferKm = DEFAULT_BUFFER_KM, sources = DEFAULT_SOURCES}) {
  const bySource = {};
  for (const {source} of tileUrls(bbox, bufferKm, sources)) {
    const b = bySource[source] ||= {tiles: 0, mb: 0};
    b.tiles++;
    b.mb += (AVG_KB[source] ?? 20) / 1024;
  }
  const all = Object.values(bySource);
  return {tiles: all.reduce((a, b) => a + b.tiles, 0), mb: Math.round(all.reduce((a, b) => a + b.mb, 0)), bySource};
}

// ---- Saved areas ----
// Downloads the tiles (skipping ones already saved, so a cancelled or failed save resumes) and the data urls
// (always refetched) into ops-area-{id}. Progress: onProgress({done, total, failed, bytes}).
export async function saveArea({id, label, bbox, bufferKm = DEFAULT_BUFFER_KM, sources = DEFAULT_SOURCES, urls = [], onProgress, signal}) {
  const cache = await caches.open(areaCache(id));
  const jobs = [...urls.map((url) => ({url, data: true})), ...tileUrls(bbox, bufferKm, sources)];
  const p = {done: 0, total: jobs.length, failed: 0, bytes: 0};

  async function one({url, data}) {
    try {
      const hit = !data && (await cache.match(url));
      // Not `p.bytes += await ...`: that reads p.bytes before the await and loses the other workers' additions.
      if (hit) { const size = (await hit.blob()).size; return p.bytes += size; }
      const res = await fetch(url, {signal, headers: {[SAVE]: "1"}});
      const ok = res.status === 200 && (data || /^image\//.test(res.headers.get("content-type") || ""));
      if (!ok) return p.failed++;  // e.g. a tile upstream does not have, or no network
      const body = await res.blob();
      const headers = new Headers(res.headers);
      if (data && !headers.has("X-Ops-From-Cache")) headers.set("X-Ops-From-Cache", new Date().toISOString());
      await cache.put(url, new Response(body, {status: 200, headers}));
      p.bytes += body.size;
    } catch (e) {
      if (e.name !== "AbortError") p.failed++;
    } finally {
      if (!signal?.aborted) p.done++, onProgress?.({...p});
    }
  }
  let next = 0;
  await Promise.all(Array.from({length: CONCURRENCY}, async () => {
    while (next < jobs.length && !signal?.aborted) await one(jobs[next++]);
  }));

  const record = {id, label, bbox, bufferKm, sources, urls, tiles: jobs.length - urls.length, failed: p.failed,
    bytes: p.bytes, savedAt: new Date().toISOString(), complete: !signal?.aborted && p.failed === 0};
  await (await caches.open(INDEX)).put(indexKey(id), Response.json(record));
  if (signal?.aborted) throw new DOMException("Save cancelled", "AbortError");
  return record;
}

export async function listAreas() {
  const index = await caches.open(INDEX);
  const areas = await Promise.all((await index.keys()).map(async (k) => (await index.match(k)).json()));
  return areas.sort((a, b) => b.savedAt.localeCompare(a.savedAt));
}

export async function deleteArea(id) {
  await caches.delete(areaCache(id));
  await (await caches.open(INDEX)).delete(indexKey(id));
}

// Deletes every saved area and cached answer; keeps the app shell so the page still opens offline.
export async function clearAll() {
  const names = (await caches.keys()).filter((k) => k.startsWith("ops-") && !k.startsWith("ops-shell-"));
  await Promise.all(names.map((k) => caches.delete(k)));
}

export async function storageInfo() {
  const {usage = 0, quota = 0} = (await navigator.storage?.estimate?.()) || {};
  return {usage, quota, persisted: (await navigator.storage?.persisted?.()) ?? false};
}
