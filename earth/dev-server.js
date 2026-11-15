/**
 * dev-server - serves static resources for developing "earth" locally
 */

"use strict";

console.log("============================================================");
console.log(new Date().toISOString() + " - Starting");

var util = require("util");

// Express 3 (send, connect, fresh) reads the private res._headers, which newer Node no longer has.
// Restore it as a read-only view of the public getHeaders(), same lowercase-keyed shape.
var http = require("http");
if (!("_headers" in http.OutgoingMessage.prototype)) {
    Object.defineProperty(http.OutgoingMessage.prototype, "_headers", {
        get: function() { return this.getHeaders(); }
    });
}

/**
 * Returns true if the response should be compressed.
 */
function compressionFilter(req, res) {
    return (/json|text|javascript|font/).test(res.getHeader('Content-Type'));
}

/**
 * Adds headers to a response to enable caching.
 */
function cacheControl() {
    return function(req, res, next) {
        // Generated tiles live under dated paths and do not change; browsers may keep them a day.
        var maxAge = /^\/data\/thermal\/i4\//.test(req.path) ? 86400 : 300;
        res.setHeader("Cache-Control", "public, max-age=" + maxAge);
        return next();
    };
}

function logger() {
    express.logger.token("date", function() {
        return new Date().toISOString();
    });
    express.logger.token("response-all", function(req, res) {
        return (res._header ? res._header : "").trim();
    });
    express.logger.token("request-all", function(req, res) {
        return util.inspect(req.headers);
    });
    return express.logger(
        ':date - info: :remote-addr :req[cf-connecting-ip] :req[cf-ipcountry] :method :url HTTP/:http-version ' +
        '":user-agent" :referrer :req[cf-ray] :req[accept-encoding]\\n:request-all\\n\\n:response-all\\n');
}

var fs = require("fs");
var path = require("path");
var zlib = require("zlib");

/**
 * Upstream map tile sources for the underlay, all 256 px Web Mercator tiles. {z}/{x}/{y} are tile coordinates and
 * {bbox} is the tile's extent in EPSG:3857 meters, for services that render an arbitrary box.
 */
var USGS = "https://basemap.nationalmap.gov/arcgis/rest/services/";
var LANDFIRE_FBFM40 = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2025/LF2025_FBFM40_CONUS/ImageServer";
var TILE_SOURCES = {
    relief: USGS + "USGSShadedReliefOnly/MapServer/tile/{z}/{y}/{x}",
    hydro: USGS + "USGSHydroCached/MapServer/tile/{z}/{y}/{x}",  // transparent, drawn over relief
    satellite: USGS + "USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
    // EOX Sentinel-2 cloudless 2024, 10 m, global and seamless; CC BY-NC-SA 4.0 (non-commercial), credit EOX + Copernicus.
    s2cloudless: "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg",
    imagerytopo: USGS + "USGSImageryTopo/MapServer/tile/{z}/{y}/{x}",  // imagery with contours, names, roads
    topo: USGS + "USGSTopo/MapServer/tile/{z}/{y}/{x}",
    terrain: "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",  // AWS Terrain Tiles, to z15
    fuel: LANDFIRE_FBFM40 + "/exportImage?bbox={bbox}&bboxSR=3857&imageSR=3857&size=256,256&format=png32&f=image"
};
// Tiles are kept here, so viewed areas also work offline. TILE_CACHE points it at a persistent volume when deployed.
var TILE_CACHE = process.env.TILE_CACHE || path.join(__dirname, "tile-cache");
var MISSING = ".missing";                             // marks a tile the upstream service does not have

function tileBbox(z, x, y) {
    var half = Math.PI * 6378137, size = 2 * half / Math.pow(2, z);
    return [-half + x * size, half - (y + 1) * size, -half + (x + 1) * size, half - y * size].join(",");
}

/**
 * VIIRS I5 (11.45 µm) brightness temperature from NASA GIBS, 375 m, one daily mosaic per satellite for day and for
 * night passes. Tiles are 8-bit palette PNGs whose palette index is the value: 0 is no data and index i spans
 * 180 + (i - 1) * 160 / 255 to 180 + i * 160 / 255 K. They are served as one layer, NOAA-20 with S-NPP filling its
 * gaps, recolored with THERMAL_SCALE.
 */
var GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/";
var THERMAL_SATELLITES = ["NOAA20", "SNPP"];  // the first with data at a pixel wins

function thermalLayers(pass) {
    return THERMAL_SATELLITES.map(function(satellite) {
        return "VIIRS_" + satellite + "_Brightness_Temp_BandI5_" + pass;
    });
}
var THERMAL_MAX_ZOOM = 9;
// [kelvin, r, g, b, alpha] stops, shared with the client's legend.
var THERMAL_SCALE = JSON.parse(fs.readFileSync(path.join(__dirname, "public/data/thermal/brightness-temp-scale.json")));

function thermalUrl(layer, date) {
    return GIBS + layer + "/default/" + date + "/GoogleMapsCompatible_Level" + THERMAL_MAX_ZOOM + "/{z}/{y}/{x}.png";
}

function thermalKelvin(index) {
    return index === 0 ? null : 180 + (index - 0.5) * 160 / 255;
}

function isTile(z, x, y) {
    return z >= 0 && z <= 23 && x >= 0 && y >= 0 && x < Math.pow(2, z) && y < Math.pow(2, z);
}

function sendTile(res, body) {
    var isPng = body[0] === 0x89 && body[1] === 0x50;
    res.setHeader("Content-Type", isPng ? "image/png" : "image/jpeg");
    res.setHeader("Cache-Control", "public, max-age=86400");
    res.end(body);
}

/**
 * @returns {Promise} the tile from the cache file, fetched from url the first time, or null if upstream has none.
 *          Unless transient is set, a tile upstream does not have is remembered as missing.
 */
// USGS answers 200 with this solid-white JPEG where it has no imagery (outside the US at zooms 9-11). Treated as
// missing (404), so the map draws nothing there instead of white squares.
var USGS_BLANK_MD5 = "5a2a693b2622558ab8514f95f63d7ebd";
function isBlank(body) {
    return body.length === 2419 && require("crypto").createHash("md5").update(body).digest("hex") === USGS_BLANK_MD5;
}

function cachedTile(file, url, transient) {
    if (fs.existsSync(file)) { var kept = fs.readFileSync(file); return Promise.resolve(isBlank(kept) ? null : kept); }
    if (fs.existsSync(file + MISSING)) return Promise.resolve(null);
    return fetch(url).then(function(upstream) {
        return upstream.arrayBuffer().then(function(buffer) {
            fs.mkdirSync(path.dirname(file), {recursive: true});
            var type = upstream.headers.get("content-type") || "";
            if (!upstream.ok || !/^image\//.test(type)) {
                // Beyond the source's zoom range or dates.
                if (!transient && (upstream.status === 404 || upstream.status === 400)) {
                    fs.writeFileSync(file + MISSING, "");
                }
                return null;
            }
            var body = Buffer.from(buffer);
            if (isBlank(body)) {
                fs.writeFileSync(file + MISSING, "");
                return null;
            }
            fs.writeFileSync(file, body);
            return body;
        });
    });
}

function tileError(res, url) {
    return function(err) {
        console.log(new Date().toISOString() + " - tile error: " + url + " " + err);
        res.send(502);
    };
}

/**
 * Serves a tile from the cache, fetching it upstream the first time.
 */
function tileProxy(req, res) {
    var source = TILE_SOURCES[req.params.source];
    var z = +req.params.z, x = +req.params.x, y = +req.params.y;
    if (!source || !isTile(z, x, y)) return res.send(404);
    if (req.params.source === "s2cloudless") warmS2();
    sourceTile(req.params.source, z, x, y).then(function(body) {
        if (body) sendTile(res, body); else res.send(404);
    }).catch(tileError(res, req.params.source + "/" + z + "/" + x + "/" + y));
}

function sourceTile(source, z, x, y) {
    var file = path.join(TILE_CACHE, source, String(z), String(x), y + ".img");
    var url = TILE_SOURCES[source].replace("{z}", z).replace("{x}", x).replace("{y}", y).replace("{bbox}", tileBbox(z, x, y));
    return cachedTile(file, url);
}

/**
 * EOX answers a tile it has not served recently in ~0.3 s, and the browser asks this server for only 6 at a time, so
 * the first look at an area waits seconds. The first Sentinel-2 request therefore fetches, in the background, the
 * lower 48 to zoom 8 and the area around every fire listed at the demo time and now to zoom 13; later views of
 * them come from tile-cache in ~0.03 s. About 8,000 tiles, ~100 MB, a few minutes; tiles already cached are skipped.
 */
var S2_FIRE_ZOOMS = [9, 13], S2_FIRE_BUFFER = 0.08, s2Warming = false;  // buffer in degrees (~7-9 km)

function tileRange(bbox, z) {
    var n = Math.pow(2, z), col = function(λ) { return Math.floor((λ + 180) / 360 * n); };
    var row = function(φ) { return Math.floor((1 - Math.log(Math.tan(Math.PI / 4 + φ * Math.PI / 360)) / Math.PI) / 2 * n); };
    var tiles = [];
    for (var x = col(bbox[0]); x <= col(bbox[2]); x++) for (var y = row(bbox[3]); y <= row(bbox[1]); y++) tiles.push([z, x, y]);
    return tiles;
}

/**
 * Background warming fetches thousands of upstream tiles per job. On a public server anyone can start a job (one per
 * heat-layer date asked for), so WARM_MAX_JOBS caps the jobs per process; 0 turns warming off. Unset: no cap.
 */
var WARM_MAX_JOBS = process.env.WARM_MAX_JOBS === undefined ? Infinity : +process.env.WARM_MAX_JOBS, warmJobs = 0;

function mayWarm() {
    if (warmJobs >= WARM_MAX_JOBS) return false;
    warmJobs++;
    return true;
}

function warmS2() {
    if (s2Warming) return;
    s2Warming = true;
    if (!mayWarm()) return;
    var seen = new Set(), queue = [];
    function add(tiles) {
        tiles.forEach(function(t) {
            var key = t.join("/");
            if (seen.has(key) || fs.existsSync(path.join(TILE_CACHE, "s2cloudless", String(t[0]), String(t[1]), t[2] + ".img"))) return;
            seen.add(key); queue.push(t);
        });
    }
    for (var z = 0; z <= WARM_ZOOM; z++) add(tileRange(WARM_BOUNDS, z));
    var fires = [];
    [new Date(DEFAULT_TIME), new Date()].forEach(function(t) { var at = firesAt(t); if (at) fires = fires.concat(at.fires); });
    for (z = S2_FIRE_ZOOMS[0]; z <= S2_FIRE_ZOOMS[1]; z++) {  // coarse first everywhere, then finer
        fires.forEach(function(f) {
            var b = f.bbox, d = S2_FIRE_BUFFER;
            add(tileRange([b[0] - d, b[1] - d, b[2] + d, b[3] + d], z));
        });
    }
    var started = Date.now(), total = queue.length, running = WARM_CONCURRENCY;
    function next() {
        var t = queue.shift();
        if (!t) {
            if (--running === 0) console.log(new Date().toISOString() + " - warmed s2cloudless: " + total + " tiles in " + Math.round((Date.now() - started) / 1000) + " s");
            return;
        }
        sourceTile("s2cloudless", t[0], t[1], t[2]).catch(function() {}).then(next);
    }
    console.log(new Date().toISOString() + " - warming s2cloudless: " + total + " tiles (lower 48 to z" + WARM_ZOOM + ", " + fires.length + " fire areas to z" + S2_FIRE_ZOOMS[1] + ")");
    for (var i = 0; i < WARM_CONCURRENCY; i++) next();
}

function thermalTile(layer, date, z, x, y) {
    var file = path.join(TILE_CACHE, "thermal", layer, date, String(z), String(x), y + ".img");
    // Not remembered as missing: a day's mosaic fills in as the passes are processed.
    var url = thermalUrl(layer, date).replace("{z}", z).replace("{x}", x).replace("{y}", y);
    return cachedTile(file, url, true);
}

function validThermal(pass, date) {
    return (pass === "Day" || pass === "Night") && /^\d{4}-\d{2}-\d{2}$/.test(date);
}

/**
 * Serves the day's brightness temperature tile for the "Day" or "Night" passes: NOAA-20's pixels where it has
 * data and S-NPP's elsewhere, colored by THERMAL_SCALE.
 */
function thermalTileProxy(req, res) {
    var pass = req.params.pass, date = req.params.date;
    var z = +req.params.z, x = +req.params.x, y = +req.params.y;
    if (!validThermal(pass, date) || !isTile(z, x, y) || z > THERMAL_MAX_ZOOM) return res.send(404);
    warmThermal(pass, date);
    mergedThermalTile(pass, date, z, x, y).then(function(body) {
        if (body) sendTile(res, body); else res.send(404);
    }).catch(tileError(res, pass + "/" + date));
}

/**
 * @returns {Promise} the merged, colored tile, from the cache once every satellite's tile was there to make it (a
 *          day's mosaic fills in as its passes are processed), or null if no satellite has data there.
 */
function mergedThermalTile(pass, date, z, x, y) {
    var file = path.join(TILE_CACHE, "thermal-merged", pass, date, String(z), String(x), y + ".png");
    if (fs.existsSync(file)) return Promise.resolve(fs.readFileSync(file));
    return Promise.all(thermalLayers(pass).map(function(layer) {
        return thermalTile(layer, date, z, x, y);
    })).then(function(bodies) {
        var merged = null;
        bodies.forEach(function(body) {
            var image = body && paletteIndices(body);
            if (!image) return;
            if (!merged) return merged = image;
            for (var i = 0; i < merged.pixels.length; i++) {
                if (merged.pixels[i] === 0) merged.pixels[i] = image.pixels[i];
            }
        });
        if (!merged) return null;
        var png = thermalPng(merged);
        if (bodies.every(Boolean)) {
            fs.mkdirSync(path.dirname(file), {recursive: true});
            fs.writeFileSync(file, png);
        }
        return png;
    });
}

/**
 * The first time a day's tiles for a pass are asked for, fetches and merges all of them over the lower 48 down to
 * WARM_ZOOM in the background, so panning and zooming there seldom waits on GIBS.
 */
var WARM_BOUNDS = [-126, 23, -65, 51], WARM_ZOOM = 8, WARM_CONCURRENCY = 6, warmed = {};

function warmThermal(pass, date) {
    var key = pass + "/" + date;
    if (warmed[key]) return;
    warmed[key] = true;
    if (!mayWarm()) return;
    var queue = [];
    for (var z = 0; z <= WARM_ZOOM; z++) {
        var n = Math.pow(2, z), lat = function(φ) {
            return Math.floor((1 - Math.log(Math.tan(Math.PI / 4 + φ * Math.PI / 360)) / Math.PI) / 2 * n);
        };
        for (var x = Math.floor((WARM_BOUNDS[0] + 180) / 360 * n); x <= Math.floor((WARM_BOUNDS[2] + 180) / 360 * n); x++) {
            for (var y = lat(WARM_BOUNDS[3]); y <= lat(WARM_BOUNDS[1]); y++) queue.push([z, x, y]);
        }
    }
    var started = Date.now(), done = 0;
    function next() {
        var tile = queue.shift();
        if (!tile) {
            if (++done === WARM_CONCURRENCY) {
                console.log(new Date().toISOString() + " - warmed " + key + " in " + (Date.now() - started) + " ms");
            }
            return;
        }
        mergedThermalTile(pass, date, tile[0], tile[1], tile[2]).catch(function() {}).then(next);
    }
    console.log(new Date().toISOString() + " - warming " + key + ": " + queue.length + " tiles");
    for (var i = 0; i < WARM_CONCURRENCY; i++) next();
}

/**
 * Returns the brightness temperature at ?lon=&lat= on ?date= for ?pass= as {"value": 291.4} (K), from the same
 * satellite the tiles show there, or {"value": null}.
 */
function thermalIdentify(req, res) {
    var lon = +req.query.lon, lat = +req.query.lat, date = req.query.date, layers = thermalLayers(req.query.pass);
    if (!isFinite(lon) || !isFinite(lat) || Math.abs(lat) > 85 || !validThermal(req.query.pass, date)) {
        return res.send(400);
    }
    var n = Math.pow(2, THERMAL_MAX_ZOOM);
    var fx = (lon + 180) / 360 * n;
    var fy = (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2 * n;
    var x = Math.floor(fx) % n, y = Math.floor(fy);
    var px = Math.floor((fx - Math.floor(fx)) * 256), py = Math.floor((fy - y) * 256);
    (function next(i) {
        if (i === layers.length) return res.json({value: null});
        thermalTile(layers[i], date, THERMAL_MAX_ZOOM, x, y).then(function(body) {
            var image = body && paletteIndices(body), index = image ? image.pixels[py * image.width + px] : 0;
            if (index) return res.json({value: +thermalKelvin(index).toFixed(1), layer: layers[i]});
            next(i + 1);
        }).catch(tileError(res, "identify " + layers[i]));
    })(0);
}

/**
 * Returns the I4 brightness temperature at ?lon=&lat= in the fire-band tiles scripts/viirs_i4_tiles.py made for
 * ?date= and ?pass= (e.g. N21-day), as {"value": 312.4} (K), or {"value": null} where the pass has no data.
 */
var I4_TILES = path.join(__dirname, "public/data/thermal/i4");

function i4Identify(req, res) {
    var lon = +req.query.lon, lat = +req.query.lat, date = String(req.query.date), pass = String(req.query.pass);
    if (!isFinite(lon) || !isFinite(lat) || Math.abs(lat) > 85 || !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
            !/^[A-Za-z0-9-]+$/.test(pass)) {
        return res.send(400);
    }
    var dir = path.join(I4_TILES, date, pass), info;
    try {
        info = JSON.parse(fs.readFileSync(path.join(dir, "info.json")));
    }
    catch (err) {
        return res.json({value: null});
    }
    // The closest zoom present: a copy of the repo may hold only the coarser zooms (see i4Tile).
    for (var z = info.max_zoom; z >= 0; z--) {
        var n = Math.pow(2, z);
        var fx = (lon + 180) / 360 * n, fy = (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2 * n;
        var x = Math.floor(fx), y = Math.floor(fy), file = path.join(dir, String(z), String(x), y + ".png");
        if (fs.existsSync(file)) break;
        if (z === 0) return res.json({value: null});
    }
    var image = paletteIndices(fs.readFileSync(file));
    var index = image ? image.pixels[Math.floor((fy - y) * image.height) * image.width + Math.floor((fx - x) * image.width)] : 0;
    res.json({value: index ? +(info.kelvin_min + (index - 0.5) * info.kelvin_step).toFixed(1) : null});
}

/**
 * Serves a fire-band tile that isn't on disk from the closest coarser tile that is, its quarter (eighth, ...)
 * enlarged with nearest-neighbour pixels. The repo keeps only the coarser zooms (the full set is ~600 MB a day),
 * so a fresh copy shows the fire band at lower resolution instead of nothing. Tiles on disk go to express.static.
 */
var I4_TILE = /^\/data\/thermal\/i4\/(\d{4}-\d{2}-\d{2})\/([A-Za-z0-9-]+)\/(\d+)\/(\d+)\/(\d+)\.png$/;

function i4Tile(req, res, next) {
    var m = I4_TILE.exec(req.path);
    if (!m) return next();
    var dir = path.join(I4_TILES, m[1], m[2]), z = +m[3], x = +m[4], y = +m[5];
    if (!isTile(z, x, y) || fs.existsSync(path.join(dir, String(z), String(x), y + ".png"))) return next();
    for (var d = 1; d <= z && d <= 8; d++) {
        var file = path.join(dir, String(z - d), String(x >> d), (y >> d) + ".png");
        if (!fs.existsSync(file)) continue;
        var png = fs.readFileSync(file), source = paletteIndices(png);
        if (!source) return next();
        var w = source.width, h = source.height, k = 1 << d, pixels = Buffer.alloc(w * h);
        var ox = (x & (k - 1)) * w / k, oy = (y & (k - 1)) * h / k;
        for (var r = 0; r < h; r++) {
            var from = (Math.floor(oy + r / k)) * w;
            for (var c = 0; c < w; c++) pixels[r * w + c] = source.pixels[from + Math.floor(ox + c / k)];
        }
        var palette = pngChunks(png).filter(function(chunk) { return chunk.type === "PLTE" || chunk.type === "tRNS"; });
        return sendTile(res, palettePng({width: w, height: h, pixels: pixels}, palette.map(function(chunk) {
            return pngChunk(chunk.type, chunk.data);
        })));
    }
    next();
}

/**
 * PNG chunks, enough to swap the palette of the 8-bit palette PNGs GIBS serves and to read their pixels.
 */
var CRC_TABLE = (function() {
    var table = [];
    for (var n = 0; n < 256; n++) {
        var c = n;
        for (var k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        table[n] = c >>> 0;
    }
    return table;
})();

function crc32(buffer) {
    var c = 0xffffffff;
    for (var i = 0; i < buffer.length; i++) c = CRC_TABLE[(c ^ buffer[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
}

function pngChunks(png) {
    var chunks = [];
    for (var i = 8; i + 12 <= png.length; ) {
        var length = png.readUInt32BE(i);
        chunks.push({type: png.toString("ascii", i + 4, i + 8), data: png.subarray(i + 8, i + 8 + length)});
        i += 12 + length;
    }
    return chunks;
}

function pngChunk(type, data) {
    var head = Buffer.alloc(8), tail = Buffer.alloc(4);
    head.writeUInt32BE(data.length, 0);
    head.write(type, 4, "ascii");
    tail.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
    return Buffer.concat([head, data, tail]);
}

function isPalettePng(chunks) {
    var ihdr = chunks[0];
    // 8-bit palette, not interlaced.
    return ihdr && ihdr.type === "IHDR" && ihdr.data[8] === 8 && ihdr.data[9] === 3 && ihdr.data[12] === 0;
}

/**
 * @returns {Buffer} [PLTE, tRNS] chunk data coloring each palette index by THERMAL_SCALE, index 0 transparent.
 */
var thermalPalette = (function() {
    var rgb = Buffer.alloc(768), alpha = Buffer.alloc(256), stops = THERMAL_SCALE.stops;
    for (var i = 1; i < 256; i++) {
        var k = thermalKelvin(i), j = 1;
        while (j < stops.length - 1 && stops[j][0] < k) j++;
        var a = stops[j - 1], b = stops[j], t = Math.max(0, Math.min(1, (k - a[0]) / (b[0] - a[0])));
        for (var c = 0; c < 3; c++) rgb[i * 3 + c] = Math.round(a[c + 1] + t * (b[c + 1] - a[c + 1]));
        alpha[i] = Math.round(255 * (a[4] + t * (b[4] - a[4])));
    }
    return [rgb, alpha];
})();

/**
 * @returns {Buffer} a palette PNG of the image's pixels, colored by THERMAL_SCALE.
 */
function thermalPng(image) {
    return palettePng(image, [pngChunk("PLTE", thermalPalette[0]), pngChunk("tRNS", thermalPalette[1])]);
}

/**
 * @returns {Buffer} an 8-bit palette PNG of the image's pixels, with the given PLTE (and tRNS) chunks.
 */
function palettePng(image, paletteChunks) {
    var ihdr = Buffer.alloc(13), stride = image.width + 1, raw = Buffer.alloc(stride * image.height);
    ihdr.writeUInt32BE(image.width, 0);
    ihdr.writeUInt32BE(image.height, 4);
    ihdr[8] = 8;  // bits per pixel
    ihdr[9] = 3;  // palette color
    for (var r = 0; r < image.height; r++) {  // each row with filter 0, none
        image.pixels.copy(raw, r * stride + 1, r * image.width, (r + 1) * image.width);
    }
    return Buffer.concat([
        Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
        pngChunk("IHDR", ihdr)
    ].concat(paletteChunks, [
        pngChunk("IDAT", zlib.deflateSync(raw)),
        pngChunk("IEND", Buffer.alloc(0))
    ]));
}

/**
 * @returns {Object} {width, height, pixels: Buffer of palette indices, row by row}, or null if the PNG is not an
 *          8-bit palette image.
 */
function paletteIndices(png) {
    var chunks = pngChunks(png);
    if (!isPalettePng(chunks)) return null;
    var width = chunks[0].data.readUInt32BE(0), height = chunks[0].data.readUInt32BE(4), stride = width + 1;
    var raw = zlib.inflateSync(Buffer.concat(chunks.filter(function(c) { return c.type === "IDAT"; }).map(function(c) {
        return c.data;
    })));
    // Undo the per-row filters, one byte per pixel.
    var pixels = Buffer.alloc(width * height);
    for (var r = 0; r < height; r++) {
        var filter = raw[r * stride], line = raw.subarray(r * stride + 1, (r + 1) * stride);
        var row = pixels.subarray(r * width, (r + 1) * width);
        var prior = r > 0 ? pixels.subarray((r - 1) * width, r * width) : Buffer.alloc(width);
        for (var i = 0; i < width; i++) {
            var left = i > 0 ? row[i - 1] : 0, up = prior[i], upLeft = i > 0 ? prior[i - 1] : 0, value = line[i];
            switch (filter) {
                case 1: value += left; break;
                case 2: value += up; break;
                case 3: value += (left + up) >> 1; break;
                case 4:
                    var p = left + up - upLeft, pa = Math.abs(p - left), pb = Math.abs(p - up), pc = Math.abs(p - upLeft);
                    value += pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft;
                    break;
            }
            row[i] = value & 0xff;
        }
    }
    return {width: width, height: height, pixels: pixels};
}

/**
 * Returns the LANDFIRE FBFM40 fuel model code at ?lon=&lat= as {"value": 165}, or {"value": null} outside CONUS.
 */
var fuelValues = {};
function fuelIdentify(req, res) {
    var lon = +req.query.lon, lat = +req.query.lat;
    if (!isFinite(lon) || !isFinite(lat)) return res.send(400);
    var key = lon.toFixed(4) + "," + lat.toFixed(4);
    if (key in fuelValues) return res.json({value: fuelValues[key]});
    var geometry = JSON.stringify({x: lon, y: lat, spatialReference: {wkid: 4326}});
    var url = LANDFIRE_FBFM40 + "/identify?geometryType=esriGeometryPoint&returnGeometry=false" +
        "&returnCatalogItems=false&f=json&geometry=" + encodeURIComponent(geometry);
    fetch(url).then(function(upstream) { return upstream.json(); }).then(function(result) {
        var value = parseInt(result.value, 10);
        res.json({value: fuelValues[key] = isFinite(value) ? value : null});
    }).catch(function(err) {
        console.log(new Date().toISOString() + " - identify error: " + err);
        res.send(502);
    });
}

/*
 * Ops API for public/ops/: fires at a moment from the FEDS snapshots, with their forecasts and hotspots.
 */
var DATA = path.join(__dirname, "public/data");
var FORECASTS = process.env.OPS_FORECASTS || path.join(DATA, "forecasts");  // env var only to test with a fixture
var DEFAULT_TIME = "2026-08-02T12:00:00Z";  // the demo moment
var BOUNDS = process.env.OPS_BOUNDS || path.join(DATA, "forecasts_bounds");  // lower/upper cases, scripts/growth_bounds.py
var FORECAST_VALID_HOURS = 24;  // shortcut: assumes every forecast runs 24 h; read its "hours" once horizons differ
var ACRES_PER_KM2 = 247.105;
var jsonFiles = {};
// shortcut: one IANA time zone per state (Idaho's panhandle is Pacific); use the fire's longitude once it matters.
var STATE_ZONES = {};
("America/New_York:CT DE FL GA KY ME MD MA NH NJ NY NC OH PA RI SC VT VA WV DC|America/Chicago:AL AR IL IA KS LA MN MS MO NE ND OK SD TN TX WI|" +
 "America/Denver:CO MT NM UT WY|America/Boise:ID|America/Phoenix:AZ|America/Los_Angeles:CA NV OR WA|America/Detroit:MI|" +
 "America/Indiana/Indianapolis:IN").split("|").forEach(function(group) {
    var parts = group.split(":");
    parts[1].split(" ").forEach(function(state) { STATE_ZONES[state] = parts[0]; });
});

/** @returns {Object} the parsed JSON file, memoised by path and mtime, or null if there is no such file. */
function readJson(file) {
    var stat;
    try { stat = fs.statSync(file); } catch (e) { return null; }
    var hit = jsonFiles[file];
    if (!hit || hit.mtime !== stat.mtimeMs) {
        hit = jsonFiles[file] = {mtime: stat.mtimeMs, value: JSON.parse(fs.readFileSync(file, "utf8"))};
    }
    return hit.value;
}

function apiError(res, status, message) {
    res.json(status, {error: message});
}

function parseTime(query) {
    var time = query === "now" ? new Date() : new Date(query === undefined ? DEFAULT_TIME : query);  // "now": the ops "Today" option
    return isNaN(time) ? null : time;
}

/** Snapshot times in the index are UTC without the Z. */
function utc(iso) {
    return new Date(/Z$/.test(iso) ? iso : iso + "Z");
}

function fedsIndex() {
    var index = readJson(path.join(DATA, "fires/feds/index.json"));
    if (!index) throw new Error("FEDS index missing");
    if (!index.sets) {  // memoised with the parsed file
        index.sets = index.snapshots.map(function(s) { return new Set(index.fires[s]); });
    }
    return index;
}

function snapshotFile(snapshot) {
    var t = utc(snapshot).toISOString();  // 2026-08-02T12:00:00.000Z -> feds/2026/08/02-1200.json
    return path.join(DATA, "fires/feds", t.slice(0, 4), t.slice(5, 7), t.slice(8, 10) + "-" + t.slice(11, 13) + t.slice(14, 16) + ".json");
}

function snapshotFeature(snapshot, id) {
    var collection = readJson(snapshotFile(snapshot));
    if (!collection) return null;
    if (!collection.byId) {
        collection.byId = {};
        collection.features.forEach(function(f) { collection.byId[f.properties.id] = f; });
    }
    return collection.byId[id] || null;
}

/** @returns {Object} {time, feature} of the fire's last listed perimeter at or before snapshot i, or null. */
function lastListed(index, id, i) {
    for (; i >= 0; i--) {
        if (index.sets[i].has(id)) {
            var feature = snapshotFeature(index.snapshots[i], id);
            if (feature) return {i: i, time: utc(index.snapshots[i]).toISOString(), feature: feature};
        }
    }
    return null;
}

function outerRings(geometry) {
    return geometry.type === "Polygon" ? [geometry.coordinates[0]] : geometry.coordinates.map(function(p) { return p[0]; });
}

/** Area-weighted centroid of the outer rings (planar in lon/lat, fine at a fire's scale) and the bbox. */
function centroidBbox(geometry) {
    var a = 0, cx = 0, cy = 0, bbox = [Infinity, Infinity, -Infinity, -Infinity];
    outerRings(geometry).forEach(function(ring) {
        for (var k = 0; k < ring.length; k++) {
            var p = ring[k], q = ring[(k + 1) % ring.length], cross = p[0] * q[1] - q[0] * p[1];
            a += cross; cx += (p[0] + q[0]) * cross; cy += (p[1] + q[1]) * cross;
            bbox = [Math.min(bbox[0], p[0]), Math.min(bbox[1], p[1]), Math.max(bbox[2], p[0]), Math.max(bbox[3], p[1])];
        }
    });
    var round = function(v) { return Math.round(v * 1e4) / 1e4; };
    var centroid = a ? [cx / (3 * a), cy / (3 * a)] : [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2];
    return {centroid: centroid.map(round), bbox: bbox};
}

function acres(feature) {
    return Math.round(feature.properties.area_km2 * ACRES_PER_KM2);
}

/** @returns {Object} the latest forecast index entry for the fire issued at or before time and still running. */
function forecastFor(id, time) {
    var index = readJson(path.join(FORECASTS, "index.json"));
    var best = null;
    ((index && index.forecasts) || []).forEach(function(f) {
        var issued = new Date(f.issued), age = (time - issued) / 3600e3;
        if (f.fire_id === id && age >= 0 && age < FORECAST_VALID_HOURS && (!best || issued > new Date(best.issued))) best = f;
    });
    if (!best) return null;
    var file = readJson(path.join(FORECASTS, best.path)), bbox = [Infinity, Infinity, -Infinity, -Infinity];
    (function extend(c) {
        if (typeof c[0] === "number") bbox = [Math.min(bbox[0], c[0]), Math.min(bbox[1], c[1]), Math.max(bbox[2], c[0]), Math.max(bbox[3], c[1])];
        else c.forEach(extend);
    })(file ? file.features.filter(function(f) {
        return f.geometry && f.properties.kind !== "start" && (f.properties.where || "front") === "front";
    }).map(function(f) { return f.geometry.coordinates; }) : []);
    // bbox: the front forecast's extent, so the map can fit fire and forecast in one move
    return {issued: best.issued, model: best.model, path: best.path, bbox: isFinite(bbox[0]) ? bbox : null};
}

/**
 * @returns {Object} the fires at time: those in the latest snapshot at or before it plus those in the snapshot before
 *          (FEDS leaves out fires without detections, ~half at 12Z), each with its last listed perimeter.
 */
function firesAt(time) {
    var index = fedsIndex(), i = -1;
    index.snapshots.forEach(function(s, k) { if (utc(s) <= time) i = k; });
    if (i < 0) return null;
    var ids = new Set(index.fires[index.snapshots[i]].concat(i > 0 ? index.fires[index.snapshots[i - 1]] : []));
    var fires = [];
    ids.forEach(function(id) {
        var current = lastListed(index, id, i), previous = lastListed(index, id, i - 1);
        if (!current) return;
        var p = current.feature.properties, shape = centroidBbox(current.feature.geometry);
        var state = ((p.place || "").match(/, ([A-Z]{2})$/) || [])[1] || p.state || null;
        fires.push({
            id: id, name: p.name || null, place: p.place || null,
            // The county's state from place; WFIGS's state is where the incident is registered, which can differ.
            state: state, tz: STATE_ZONES[state] || null, incident_state: p.state || null,
            acres: acres(current.feature), incident_acres: p.acres == null ? null : p.acres,
            contained: p.contained == null ? null : p.contained,
            // WFIGS fields carry no record date; they were fetched when the snapshot file was written.
            incident_as_of: p.name ? new Date(jsonFiles[snapshotFile(current.time)].mtime).toISOString() : null,
            growth_acres: previous ? acres(current.feature) - acres(previous.feature) : null,
            days: p.days, perimeter_time: current.time,
            centroid: shape.centroid, bbox: shape.bbox,
            forecast: forecastFor(id, time), geometry: current.feature.geometry,
            _previous: previous && previous.i !== current.i ? {time: previous.time, geometry: previous.feature.geometry} : null
        });
    });
    return {
        _i: i, time: time.toISOString(), snapshot: utc(index.snapshots[i]).toISOString(),
        previous_snapshot: i > 0 ? utc(index.snapshots[i - 1]).toISOString() : null, fires: fires
    };
}

function withoutPrivate(fire) {
    var copy = Object.assign({}, fire);
    delete copy._previous;
    return copy;
}

/** VIIRS detections, [lon, lat, frp (MW), time, sensor], within ~2 km of the fire's bbox in the 12 h up to time, or null without hotspot files. */
function hotspotsFor(fire, time) {
    var from = time.getTime() / 1000 - 12 * 3600, to = time.getTime() / 1000, pad = 0.02, b = fire.bbox;
    var days = [new Date(from * 1000), time].map(function(d) { return d.toISOString().slice(0, 10); });
    var points = null;
    days.filter(function(d, k) { return days.indexOf(d) === k; }).forEach(function(day) {
        var file = readJson(path.join(DATA, "fires/viirs", day.slice(0, 4), day.slice(5, 7), day.slice(8, 10) + ".json"));
        if (!file) return;
        points = points || [];
        Object.keys(file.sensors).forEach(function(sensor) {
            file.sensors[sensor].forEach(function(r) {  // [lon, lat, frp, time, confidence, scan, track]
                if (r[3] > from && r[3] <= to && r[0] >= b[0] - pad && r[0] <= b[2] + pad && r[1] >= b[1] - pad && r[1] <= b[3] + pad) {
                    points.push([r[0], r[1], r[2], new Date(r[3] * 1000).toISOString().slice(0, 16) + "Z", sensor]);
                }
            });
        });
    });
    return points;
}

function apiHandler(handler) {
    return function(req, res) {
        res.setHeader("Cache-Control", "no-cache");
        var time = parseTime(req.query.time);
        if (!time) return apiError(res, 400, "Bad time '" + req.query.time + "': use ISO 8601, e.g. " + DEFAULT_TIME);
        try {
            var at = firesAt(time);
            if (!at) return apiError(res, 404, "No FEDS snapshot at or before " + time.toISOString());
            handler(req, res, at, time);
        } catch (err) {
            console.log(new Date().toISOString() + " - api error: " + req.url + " " + err.stack);
            apiError(res, 500, "Server error: " + err.message);
        }
    };
}

var apiFires = apiHandler(function(req, res, at) {
    res.json({time: at.time, snapshot: at.snapshot, previous_snapshot: at.previous_snapshot, fires: at.fires.map(withoutPrivate)});
});

/** Every listed perimeter of the fire in snapshots first..last, oldest first, skipping unchanged repeats. */
function historyOf(index, id, first, last) {
    var history = [], before = null;
    for (var k = first; k <= last; k++) {
        if (!index.sets[k].has(id)) continue;
        var feature = snapshotFeature(index.snapshots[k], id), json = feature && JSON.stringify(feature.geometry);
        if (!feature || json === before) continue;
        before = json;
        history.push({time: utc(index.snapshots[k]).toISOString(), acres: acres(feature), geometry: feature.geometry});
    }
    return history;
}

/**
 * For "compare with what happened": the fire's observed perimeter at each forecast hour, from snapshots after the
 * page's time (data a forecaster would not have had then). Only hours that fall on a snapshot.
 */
function observedAfter(index, id, forecast, i) {
    return (forecast.hours || []).map(function(h) {
        var at = new Date(new Date(forecast.issued).getTime() + h * 3600e3).getTime();
        var k = index.snapshots.findIndex(function(s) { return utc(s).getTime() === at; });
        var found = k > i ? lastListed(index, id, k) : null;
        return found && found.i > i ? {hours: h, time: found.time, acres: acres(found.feature), geometry: found.feature.geometry} : null;
    }).filter(Boolean);
}

/** @returns {Object} the lower and upper bound forecasts issued with the fire's physics forecast, else null. */
function boundsFor(id, forecast) {
    if (!forecast) return null;
    var index = readJson(path.join(BOUNDS, "index.json")), entry = ((index && index.forecasts) || []).filter(function(f) {
        return f.fire_id === id && new Date(f.issued).getTime() === new Date(forecast.issued).getTime();
    })[0];
    var lower = entry && readJson(path.join(BOUNDS, entry.path_lower)), upper = entry && readJson(path.join(BOUNDS, entry.path_upper));
    return lower && upper ? {lower: lower, upper: upper} : null;
}

/** @returns {Object} the fire's containment at the forecast's issue time from WFIGS dates (data/containment/*_containment.json,
 * scripts/containment_aug2.py), else null. Display only; the percent is the latest value, not as of issue. */
function containmentFor(id, forecast, at) {
    var dir = path.join(DATA, "containment"), day = (forecast ? forecast.issued : at.time).slice(0, 10), found = null;
    var names = []; try { names = fs.readdirSync(dir); } catch (e) {}
    names.filter(function(n) { return /_containment\.json$/.test(n); }).forEach(function(n) {
        var c = readJson(path.join(dir, n));
        if (c && c.issue && c.issue.slice(0, 10) === day && c.fires && c.fires[id]) found = Object.assign({issue: c.issue}, c.fires[id]);
    });
    return found && {issue: found.issue, status: found.status, contained_since: found.contained_since || null, incident: found.incident || null,
        percent_contained_latest: found.percent_contained_latest == null ? null : found.percent_contained_latest, source: "WFIGS"};
}

var apiFire = apiHandler(function(req, res, at, time) {
    var id = +req.params.id, fire = at.fires.filter(function(f) { return f.id === id; })[0];
    if (!fire) return apiError(res, 404, "No fire " + req.params.id + " in the FEDS snapshot of " + at.snapshot);
    var index = fedsIndex(), forecast = fire.forecast ? readJson(path.join(FORECASTS, fire.forecast.path)) : null;
    var bounds = boundsFor(id, forecast);
    res.json({
        time: at.time, snapshot: at.snapshot, fire: withoutPrivate(fire), previous: fire._previous,
        history: historyOf(index, id, 0, at._i),
        // With bounds, forecast is the most likely (lower) case; the unconstrained physics run moves to forecast_physics.
        forecast: bounds ? bounds.lower : forecast, forecast_upper: bounds ? bounds.upper : null,
        forecast_physics: bounds ? forecast : null, observed_after: forecast ? observedAfter(index, id, forecast, at._i) : [],
        containment: containmentFor(id, forecast, at),
        access_route: readJson(path.join(DATA, "fires", "access-routes", at.snapshot.slice(0, 10), id + ".geojson")),
        hotspots: hotspotsFor(fire, time)
    });
});

// Hourly model conditions sampled at the mapped centroid and the requested UTC hour.
var apiWeather = apiHandler(function(req, res, at, time) {
    var fire = at.fires.find(function(item) { return item.id === +req.params.id; });
    if (!fire) return apiError(res, 404, "Unknown fire");
    var hour = time.toISOString().slice(0, 13) + ":00";
    var date = hour.slice(0, 10);
    var archived = Date.now() - time.getTime() > 5 * 86400000;
    var endpoint = archived ? "https://archive-api.open-meteo.com/v1/archive" : "https://api.open-meteo.com/v1/forecast";
    var params = new URLSearchParams({latitude: fire.centroid[1], longitude: fire.centroid[0],
        start_date: date, end_date: date, timezone: "GMT", temperature_unit: "fahrenheit", wind_speed_unit: "mph",
        hourly: "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation"});
    fetch(endpoint + "?" + params, {signal: AbortSignal.timeout(8000)}).then(function(response) {
        if (!response.ok) throw new Error("Weather provider unavailable");
        return response.json();
    }).then(function(payload) {
        var values = payload.hourly, index = values.time.indexOf(hour);
        if (index < 0) return apiError(res, 404, "No weather for this hour");
        function sample(key) {
            var value = values[key] && values[key][index];
            return Number.isFinite(value) ? value : null;
        }
        res.json({time: hour + "Z", source: archived ? "Open-Meteo reanalysis" : "Open-Meteo forecast",
            wind_mph: sample("wind_speed_10m"), wind_from: sample("wind_direction_10m"),
            temp_f: sample("temperature_2m"), humidity: sample("relative_humidity_2m"), rain_mm: sample("precipitation")});
    }).catch(function() { apiError(res, 502, "Weather unavailable"); });
});

var port = process.argv[2] || process.env.PORT || 8080;
var production = process.env.NODE_ENV === "production";
var express = require("express");
var app = express();

app.set("json spaces", 0);
if (production) app.enable("trust proxy");  // behind the host's TLS proxy: remote-addr is the client's
app.use(cacheControl());
app.use(express.compress({filter: compressionFilter}));
// The full logger prints every request's headers; in production one line per request is enough.
app.use(production ? express.logger("short") : logger());
app.get("/tiles/:source/:z/:x/:y", tileProxy);
app.get("/identify/fuel", fuelIdentify);
app.get("/tiles/thermal/:pass/:date/:z/:x/:y", thermalTileProxy);
app.get("/identify/thermal", thermalIdentify);
app.get("/identify/i4", i4Identify);
app.get("/api/fires", apiFires);
app.get("/api/fires/:id", apiFire);
app.get("/api/fires/:id/weather", apiWeather);
// Radio coverage (SPLAT!) for navigation: cell towers in the fire packages and user-added repeaters (coverage.js).
app.get("/api/coverage", require("./coverage")({sourceTile: sourceTile, cacheDir: TILE_CACHE}));
// Daily MODIS/VIIRS/harmonized layers 2000-2026 and their regional calendars (history.js).
var fireHistory = require("./history")({cacheDir: TILE_CACHE});
app.get("/api/history", fireHistory.api);
if (fireHistory.root && fireHistory.root !== path.join(__dirname, "public", "data", "history")) app.use("/data/history", express.static(fireHistory.root));
app.all("/api/*", function(req, res) { apiError(res, 404, "No API endpoint " + req.path); });
app.use(i4Tile);
// The fire-watch (ops) page is the front page, served at / (and still at /ops/); the globe stays at /index.html and
// /globe. The ops service worker lives at /ops/sw.js but controls the whole site, so the page works offline at /.
app.get("/", function(req, res) { res.sendfile(path.join(__dirname, "public", "ops", "index.html")); });
app.get("/ops/sw.js", function(req, res, next) { res.setHeader("Service-Worker-Allowed", "/"); next(); });
app.get("/globe", function(req, res) { res.sendfile(path.join(__dirname, "public", "index.html")); });
// Next-day fire activity predictions (Eric, data/samples/predictions): PNG grids + index.json.
app.use("/data/predictions", express.static(path.join(__dirname, "..", "data", "samples", "predictions")));
app.use(express.static(path.join(__dirname, "public")));

app.listen(port);
console.log("Listening on port " + port + "...");
