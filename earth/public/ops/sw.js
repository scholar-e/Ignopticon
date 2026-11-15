// Ignopticon Ops service worker (scope /; the ops page is at / and /ops/, and the globe's requests pass through): keeps the ops page, its data and saved map areas usable offline.
// Caches: ops-shell-VERSION (app shell, cache-first), ops-api (network-first), ops-tiles (cache-first, trimmed),
// ops-area-{id} (tiles and data of areas saved by offline.js, never trimmed), ops-data (/data/, stale-while-revalidate).
// Bump VERSION whenever a shell file changes: the new worker precaches, then waits until the page asks it to take over.
const VERSION = "v59";
const SHELL = "ops-shell-" + VERSION;
const SHELL_FILES = [
  "/", "/ops/", "/ops/index.html", "/ops/ops.css", "/ops/ops.js", "/ops/nav.js", "/ops/history.js", "/ops/predictions.js", "/ops/wind.js", "/ops/offline.js", "/ops/storage.html",
  "/ops/manifest.webmanifest", "/ops/icons/icon-192.png", "/ops/icons/icon-512.png", "/ops/icons/maskable-512.png",
  "/ops/vendor/maplibre-gl.js", "/ops/vendor/maplibre-gl.css", "/ops/vendor/fonts/Noto%20Sans%20Bold/0-255.pbf",
];
const API_TIMEOUT_MS = 3000;
const MAX_PASSIVE_TILES = 3000;  // ~60 MB at the measured ~20 KB per imagery tile
const MAX_API_ENTRIES = 200;     // hovering the fire list prefetches details, so this grows while browsing
const STAMP = "X-Ops-From-Cache";  // ISO time the cached copy was fetched; only cached copies carry it
const SAVE = "X-Ops-Save";         // set by offline.js saveArea: the tile goes to an area cache, not ops-tiles

// Requests seen per category and outcome, for testing ({type: "stats"} message).
const stats = {};
const count = (cat, how) => { stats[cat] = stats[cat] || {}; stats[cat][how] = (stats[cat][how] || 0) + 1; };

self.addEventListener("install", (event) => {
  // reload: skip the HTTP cache (the server allows 5 min), so a new version never precaches stale files.
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES.map((u) => new Request(u, {cache: "reload"})))));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("ops-shell-") && k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "skip-waiting") self.skipWaiting();
  if (event.data?.type === "stats") event.source.postMessage({type: "ops-stats", version: VERSION, stats});
});

self.addEventListener("fetch", (event) => {
  const req = event.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return count("other", "passthrough");
  const p = url.pathname;
  if (req.mode === "navigate") return opsPage(p) ? event.respondWith(shell(req)) : count("other", "passthrough");
  if (p.startsWith("/ops/")) return event.respondWith(shell(req));
  if (!(p.startsWith("/api/") || p === "/identify/fuel" || p.startsWith("/tiles/") || p.startsWith("/data/"))) return count("other", "passthrough");
  // The globe (/index.html, /globe) shares /data/ and /tiles/: its requests go straight to the network.
  event.respondWith(self.clients.get(event.clientId).then((c) => {
    if (c && !opsPage(new URL(c.url).pathname)) return count("other", "passthrough"), fetch(req);
    if (p.startsWith("/tiles/")) return tile(event);
    if (p.startsWith("/data/")) return staleWhileRevalidate(event);
    return networkFirst(event);
  }));
});
const opsPage = (p) => p === "/" || p.startsWith("/ops/");

// Tells the open pages whether the network worked, and how old the cached data they now show is.
async function notify(msg) {
  for (const c of await self.clients.matchAll({type: "window"})) c.postMessage({type: "ops-net", ...msg});
}

async function stamped(res) {
  const headers = new Headers(res.headers);
  if (!headers.has(STAMP)) headers.set(STAMP, new Date().toISOString());
  return new Response(await res.blob(), {status: res.status, statusText: res.statusText, headers});
}

async function trim(cacheName, max) {
  const cache = await caches.open(cacheName), keys = await cache.keys();  // keys come back oldest first
  await Promise.all(keys.slice(0, Math.max(0, keys.length - max)).map((k) => cache.delete(k)));
}
let puts = 0;  // trim after every 50 writes rather than every write

async function put(cacheName, req, res) {
  await (await caches.open(cacheName)).put(req, res);
  if (++puts % 50 === 0) await Promise.all([trim("ops-tiles", MAX_PASSIVE_TILES), trim("ops-api", MAX_API_ENTRIES)]);
}

async function shell(req) {
  const nav = req.mode === "navigate";
  const cache = await caches.open(SHELL);  // not caches.match: a waiting new version's shell is in Cache Storage too
  const hit = await cache.match(req, {ignoreSearch: nav});  // /ops/?fire=1 opens the cached /ops/
  if (hit) return count("shell", "cache"), hit;
  try {
    const res = await fetch(req);
    if (res.ok) await cache.put(req, res.clone());  // e.g. a glyph range not precached
    return count("shell", "network"), res;
  } catch (e) {
    count("shell", "failed");
    if (nav) return cache.match("/ops/index.html");
    throw e;
  }
}

// API: network first; after API_TIMEOUT_MS or a failure, the newest saved copy (ops-api, else a saved area's).
async function networkFirst(event) {
  const req = event.request;
  const net = fetch(req).then(async (res) => {
    if (res.status >= 500) throw new Error("server error " + res.status);  // e.g. a proxy with the origin down
    if (res.ok) await put("ops-api", req, await stamped(res.clone()));
    notify({ok: true});
    return res;
  });
  event.waitUntil(net.catch(() => {}));  // a slow answer still refreshes the cache after we served the old copy
  try {
    const res = await Promise.race([net, new Promise((_, reject) => setTimeout(reject, API_TIMEOUT_MS, new Error("timeout")))]);
    return count("api", "network"), res;
  } catch (e) {
    const hit = (await (await caches.open("ops-api")).match(req)) || (await caches.match(req));
    if (hit) {
      notify({ok: false, cachedAt: hit.headers.get(STAMP)});
      return count("api", "cache"), hit;
    }
    count("api", "failed");
    return net.catch(() => {
      notify({ok: false});
      return Response.json({error: "Offline, and this was not saved for offline use"}, {status: 503});
    });
  }
}

// Tiles: any cache (saved areas, then the passive ops-tiles) before the network; only real image tiles are stored.
async function tile(event) {
  const req = event.request;
  const hit = await caches.match(req);
  if (hit) return count("tiles", "cache"), hit;
  try {
    const res = await fetch(req);
    // The proxy answers 404 for tiles upstream does not have and 502 on upstream errors: never cache those.
    if (res.status === 200 && /^image\//.test(res.headers.get("content-type") || "") && !req.headers.has(SAVE)) {
      event.waitUntil(put("ops-tiles", req, res.clone()));
    }
    return count("tiles", "network"), res;
  } catch (e) {
    count("tiles", "failed");
    notify({ok: false});
    return new Response(null, {status: 504, statusText: "Offline, tile not saved"});  // the map leaves it blank
  }
}

async function staleWhileRevalidate(event) {
  const req = event.request, cache = await caches.open("ops-data");
  const hit = await cache.match(req);
  const net = fetch(req).then(async (res) => {
    if (res.ok) await cache.put(req, res.clone());
    return res;
  });
  event.waitUntil(net.catch(() => {}));
  if (hit) return count("data", "cache"), hit;
  return net.then((res) => (count("data", "network"), res), (e) => { count("data", "failed"); throw e; });
}
