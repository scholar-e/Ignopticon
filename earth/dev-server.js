/**
 * dev-server - serves static resources for developing "earth" locally
 */

"use strict";

console.log("============================================================");
console.log(new Date().toISOString() + " - Starting");

var util = require("util");

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
    fuel: LANDFIRE_FBFM40 + "/exportImage?bbox={bbox}&bboxSR=3857&imageSR=3857&size=256,256&format=png32&f=image"
};
var TILE_CACHE = path.join(__dirname, "tile-cache");  // tiles are kept here, so viewed areas also work offline
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
function cachedTile(file, url, transient) {
    if (fs.existsSync(file)) return Promise.resolve(fs.readFileSync(file));
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
    var file = path.join(TILE_CACHE, req.params.source, String(z), String(x), y + ".img");
    var url = source.replace("{z}", z).replace("{x}", x).replace("{y}", y).replace("{bbox}", tileBbox(z, x, y));
    cachedTile(file, url).then(function(body) {
        if (body) sendTile(res, body); else res.send(404);
    }).catch(tileError(res, url));
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
    var z = info.max_zoom, n = Math.pow(2, z);
    var fx = (lon + 180) / 360 * n, fy = (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2 * n;
    var x = Math.floor(fx), y = Math.floor(fy), file = path.join(dir, String(z), String(x), y + ".png");
    if (!fs.existsSync(file)) return res.json({value: null});
    var image = paletteIndices(fs.readFileSync(file));
    var index = image ? image.pixels[Math.floor((fy - y) * image.height) * image.width + Math.floor((fx - x) * image.width)] : 0;
    res.json({value: index ? +(info.kelvin_min + (index - 0.5) * info.kelvin_step).toFixed(1) : null});
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
        pngChunk("IHDR", ihdr),
        pngChunk("PLTE", thermalPalette[0]),
        pngChunk("tRNS", thermalPalette[1]),
        pngChunk("IDAT", zlib.deflateSync(raw)),
        pngChunk("IEND", Buffer.alloc(0))
    ]);
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

var port = process.argv[2];
var express = require("express");
var app = express();

app.use(cacheControl());
app.use(express.compress({filter: compressionFilter}));
app.use(logger());
app.get("/tiles/:source/:z/:x/:y", tileProxy);
app.get("/identify/fuel", fuelIdentify);
app.get("/tiles/thermal/:pass/:date/:z/:x/:y", thermalTileProxy);
app.get("/identify/thermal", thermalIdentify);
app.get("/identify/i4", i4Identify);
app.use(express.static("public"));

app.listen(port);
console.log("Listening on port " + port + "...");
