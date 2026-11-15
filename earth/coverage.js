/**
 * Radio coverage from SPLAT! (https://www.qsl.net/kd2bd/splat.html) for the ops navigation feature.
 *
 * SPLAT! predicts received signal power around one transmitter with the ITWOM terrain model, from SPLAT Data
 * Files (SDF): 1x1 degree tiles of 1200x1200 elevations (3 arc-seconds). This module writes those tiles on demand
 * from AWS Terrain Tiles (terrarium PNGs, through the server's tile cache), runs SPLAT! for a transmitter, and bins
 * its alphanumeric output (-ano: lat, lon west, azimuth, elevation, dBm) onto a caller's lat/lon grid.
 *
 * GET /api/coverage?lat=&lon=&height=&freq=&erp=&radius=&west=&north=&dlon=&dlat=&w=&h=
 *   lat/lon: transmitter (degrees), height: antenna above ground (m), freq: MHz, erp: watts,
 *   radius: km, grid: west/north edges and cell size (degrees), w x h cells (row 0 = north).
 *   -> {dbm: base64 Int8Array of w*h (received power, dBm, rounded; -128 = not computed), ms, cached}
 * Receivers are assumed at 1.5 m above ground (a hand-held radio or phone).
 */
var fs = require("fs");
var path = require("path");
var zlib = require("zlib");
var crypto = require("crypto");
var childProcess = require("child_process");

var SDF_ZOOM = 11;   // terrarium zoom for the 3 arc-second (~90 m) SDF grid: ~50 m pixels at mid-latitudes
var RX_HEIGHT_M = 1.5;
// SDF names are "min_north:max_north:min_west:max_west.sdf"; Windows forbids ":", so the local Windows build of SPLAT!
// (tools/splat-src) is patched to use "_" instead.
var SEP = process.platform === "win32" ? "_" : ":";

module.exports = function coverage(options) {
    var sourceTile = options.sourceTile, cacheDir = options.cacheDir;
    var splatBin = options.splatBin || findSplat();
    var sdfDir = path.join(cacheDir, "sdf"), runDir = path.join(cacheDir, "splat-runs"), resultDir = path.join(cacheDir, "coverage");
    [sdfDir, runDir, resultDir].forEach(function(d) { fs.mkdirSync(d, {recursive: true}); });
    var making = {};  // SDF file name -> Promise, so concurrent requests build a tile once
    var queue = Promise.resolve();  // SPLAT! runs one at a time (it is CPU- and memory-heavy)

    function findSplat() {
        var candidates = [process.env.SPLAT_BIN, "/usr/local/bin/splat",
            path.join(__dirname, "..", "tools", "splat-src", "splat-1.4.2", "splat.exe")];
        for (var i = 0; i < candidates.length; i++) if (candidates[i] && fs.existsSync(candidates[i])) return candidates[i];
        return null;
    }

    // ---- Terrarium PNG decoding (8-bit RGB/RGBA, non-interlaced): elevation = R*256 + G + B/256 - 32768 ----
    function decodePng(buf) {
        var pos = 8, width, height, channels, idat = [];
        while (pos < buf.length) {
            var len = buf.readUInt32BE(pos), type = buf.toString("ascii", pos + 4, pos + 8), data = buf.subarray(pos + 8, pos + 8 + len);
            if (type === "IHDR") { width = data.readUInt32BE(0); height = data.readUInt32BE(4); channels = data[9] === 6 ? 4 : 3; }
            if (type === "IDAT") idat.push(data);
            pos += 12 + len;
        }
        var raw = zlib.inflateSync(Buffer.concat(idat)), stride = width * channels, out = Buffer.alloc(stride * height);
        for (var y = 0; y < height; y++) {
            var f = raw[y * (stride + 1)], src = y * (stride + 1) + 1, dst = y * stride;
            for (var x = 0; x < stride; x++) {
                var a = x >= channels ? out[dst + x - channels] : 0, b = y > 0 ? out[dst - stride + x] : 0;
                var c = x >= channels && y > 0 ? out[dst - stride + x - channels] : 0, v = raw[src + x];
                if (f === 1) v += a; else if (f === 2) v += b; else if (f === 3) v += (a + b) >> 1;
                else if (f === 4) { var p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
                out[dst + x] = v & 255;
            }
        }
        var elev = new Float32Array(width * height);
        for (var i = 0; i < width * height; i++) elev[i] = out[i * channels] * 256 + out[i * channels + 1] + out[i * channels + 2] / 256 - 32768;
        return elev;
    }

    var tiles = new Map();  // "x/y" -> Promise<Float32Array 256x256>, bounded below
    function tile(x, y) {
        var key = x + "/" + y;
        if (!tiles.has(key)) {
            if (tiles.size > 400) tiles.delete(tiles.keys().next().value);
            tiles.set(key, sourceTile("terrain", SDF_ZOOM, x, y).then(function(png) { return png ? decodePng(png) : null; }));
        }
        return tiles.get(key);
    }

    /** Writes the SDF tile whose south-west corner is (minNorth, -maxWest): rows south to north, columns east to west. */
    function makeSdf(minNorth, minWest) {
        var maxWest = minWest + 1, name = [minNorth, minNorth + 1, minWest, maxWest].join(SEP) + ".sdf";
        var file = path.join(sdfDir, name);
        if (fs.existsSync(file)) return Promise.resolve(file);
        if (making[name]) return making[name];
        var n = Math.pow(2, SDF_ZOOM);
        var px = function(lon) { return (lon + 180) / 360 * n * 256; };
        var py = function(lat) { var s = Math.sin(lat * Math.PI / 180); return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n * 256; };
        var west = -maxWest, east = -minWest, south = minNorth, north = minNorth + 1;
        var tx0 = Math.floor(px(west) / 256), tx1 = Math.floor((px(east) - 1e-6) / 256), ty0 = Math.floor(py(north) / 256), ty1 = Math.floor((py(south) - 1e-6) / 256);
        var jobs = [];
        for (var ty = ty0; ty <= ty1 + 1; ty++) for (var tx = tx0; tx <= tx1 + 1; tx++) jobs.push(tile(tx, ty).then(function(k) { return function(t) { return [k, t]; }; }(tx + "/" + ty)));
        return making[name] = Promise.all(jobs).then(function(pairs) {
            var got = new Map(pairs);
            var sample = function(lat, lon) {  // bilinear in tile pixels
                var X = px(lon) - 0.5, Y = py(lat) - 0.5, x0 = Math.floor(X), y0 = Math.floor(Y), fx = X - x0, fy = Y - y0, v = 0, w = 0;
                for (var dy = 0; dy < 2; dy++) for (var dx = 0; dx < 2; dx++) {
                    var gx = x0 + dx, gy = y0 + dy, t = got.get(Math.floor(gx / 256) + "/" + Math.floor(gy / 256));
                    if (!t) continue;
                    var e = t[(gy & 255) * 256 + (gx & 255)], k = (dx ? fx : 1 - fx) * (dy ? fy : 1 - fy);
                    v += e * k; w += k;
                }
                return w ? v / w : 0;
            };
            var lines = [maxWest, minNorth, minWest, minNorth + 1];
            for (var i = 0; i < 1200; i++) {
                var lat = minNorth + i / 1200;
                for (var j = 0; j < 1200; j++) lines.push(Math.round(Math.max(0, sample(lat, -(minWest + (j + 1) / 1200)))));
            }
            fs.writeFileSync(file + ".tmp", lines.join("\n") + "\n");
            fs.renameSync(file + ".tmp", file);
            delete making[name];
            return file;
        });
    }

    /** All SDF tiles a transmitter's coverage radius touches. */
    function sdfFor(lat, lon, radiusKm) {
        var dLat = radiusKm / 111, dLon = radiusKm / (111 * Math.cos(lat * Math.PI / 180)), jobs = [];
        for (var n = Math.floor(lat - dLat); n <= Math.floor(lat + dLat); n++)
            for (var w = Math.floor(-(lon + dLon)); w <= Math.floor(-(lon - dLon)); w++) jobs.push(makeSdf(n, w));
        return Promise.all(jobs);
    }

    function run(q) {
        if (!splatBin) return Promise.reject(Object.assign(new Error("SPLAT! is not installed on this server"), {status: 501}));
        var key = crypto.createHash("sha1").update(JSON.stringify(q)).digest("hex").slice(0, 16);
        var cached = path.join(resultDir, key + ".json");
        if (fs.existsSync(cached)) return Promise.resolve(Object.assign(JSON.parse(fs.readFileSync(cached, "utf8")), {cached: true}));
        var started = Date.now();
        return queue = queue.catch(function() {}).then(function() {
            return sdfFor(q.lat, q.lon, q.radius).then(function() {
                var dir = path.join(runDir, key);
                fs.mkdirSync(dir, {recursive: true});
                fs.writeFileSync(path.join(dir, "tx.qth"), ["tx", q.lat.toFixed(6), (-q.lon).toFixed(6), q.height.toFixed(1) + " meters"].join("\n") + "\n");
                fs.writeFileSync(path.join(dir, "tx.lrp"), [15, 0.005, 301, q.freq, 5, 1, 0.5, 0.9, q.erp].join("\n") + "\n");
                return new Promise(function(resolve, reject) {
                    childProcess.execFile(splatBin, ["-t", "tx.qth", "-L", String(RX_HEIGHT_M), "-R", String(q.radius), "-metric", "-dbm",
                        "-ano", "out.ano", "-d", sdfDir, "-o", "map", "-ngs"], {cwd: dir, timeout: 240000, maxBuffer: 1 << 24},
                        function(err) { return err && !fs.existsSync(path.join(dir, "out.ano")) ? reject(err) : resolve(dir); });
                });
            }).then(function(dir) {
                var dbm = new Int8Array(q.w * q.h).fill(-128), text = fs.readFileSync(path.join(dir, "out.ano"), "utf8").split("\n");
                for (var i = 2; i < text.length; i++) {
                    var parts = text[i].split(",");
                    if (parts.length < 5) continue;
                    var lat = +parts[0], lon = -(+parts[1]), v = parseFloat(parts[4]);
                    var col = Math.floor((lon - q.west) / q.dlon), row = Math.floor((q.north - lat) / q.dlat);
                    if (col < 0 || row < 0 || col >= q.w || row >= q.h || !isFinite(v)) continue;
                    var k = row * q.w + col, d = Math.max(-127, Math.min(127, Math.round(v)));
                    if (d > dbm[k]) dbm[k] = d;
                }
                fs.rmSync(dir, {recursive: true, force: true});
                var result = {dbm: Buffer.from(dbm.buffer).toString("base64"), ms: Date.now() - started};
                fs.writeFileSync(cached, JSON.stringify(result));
                return result;
            });
        });
    }

    /** Express handler. */
    return function apiCoverage(req, res) {
        var q = {}, names = ["lat", "lon", "height", "freq", "erp", "radius", "west", "north", "dlon", "dlat", "w", "h"];
        for (var i = 0; i < names.length; i++) {
            q[names[i]] = +req.query[names[i]];
            if (!isFinite(q[names[i]])) return res.json(400, {error: "Missing or bad " + names[i]});
        }
        if (q.w * q.h > 4e6 || q.radius > 60 || q.radius <= 0 || q.freq < 20 || q.freq > 20000) return res.json(400, {error: "Request out of range"});
        run(q).then(function(r) { res.json(r); }, function(e) { res.json(e.status || 502, {error: String(e.message || e)}); });
    };
};
