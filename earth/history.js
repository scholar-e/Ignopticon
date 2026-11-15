/**
 * Daily MODIS/VIIRS/harmonized fire layers 2000-2026 (scripts/export_ui.py; docs/DATASETS.md "UI export format").
 *
 * Files: HISTORY_DIR/index.json and HISTORY_DIR/PRODUCT/YYYY-MM-DD.{png,json}, products MOD14A1, MYD14A1, VNP14A1,
 * VJ114A1 and harmonized, on the 0.1 degree lower-48 grid (590 x 260, row 0 north). In production the export is
 * mounted at public/data/history; locally HISTORY_DIR, ../data/history, or the August 2013 samples serve instead.
 *
 * GET /api/history?product=&west=&south=&east=&north=[&daily=1]
 *   -> {product, unit, first, last, months: [[ "YYYY-MM", value, daysWithData ]...], days?: [[date, value]...]}
 * Value per day = fire cell-days in the box: raw products count cells with any fire pixel; harmonized sums p, the
 * probability VIIRS S-NPP saw fire (VIIRS-equivalent fire cell-days, also before 2012). The first request for a
 * product reads its daily JSONs once (~10k files) into a compact index, cached in CACHE_DIR/history.
 */
var fs = require("fs");
var path = require("path");

var COLS = 590, ROWS = 260, WEST = -125, NORTH = 50, CELL = 0.1;
var EPOCH = Date.UTC(2000, 0, 1);

module.exports = function history(options) {
    var root = [process.env.HISTORY_DIR, path.join(__dirname, "public", "data", "history"), path.join(__dirname, "..", "data", "history"),
        path.join(__dirname, "..", "data", "samples", "ui")].filter(function(d) { return d && fs.existsSync(path.join(d, "index.json")); })[0];
    var cacheDir = path.join(options.cacheDir, "history");
    fs.mkdirSync(cacheDir, {recursive: true});
    var built = {};  // product -> Promise<{day: Uint16Array, cell: Uint32Array, value: Float32Array, dates}>

    function readIndex() { return JSON.parse(fs.readFileSync(path.join(root, "index.json"), "utf8")); }

    function build(product) {
        if (built[product]) return built[product];
        var info = readIndex().products[product];
        if (!info) return Promise.reject(Object.assign(new Error("Unknown product " + product), {status: 404}));
        var stamp = info.first + "_" + info.last + "_" + info.dates.length, file = path.join(cacheDir, product + "_" + stamp + ".bin");
        if (fs.existsSync(file)) {
            var buf = fs.readFileSync(file), n = buf.readUInt32LE(0), o = 4;
            var copy = function(T, len) { var a = new T(len); Buffer.from(a.buffer).set(buf.subarray(o, o + a.byteLength)); o += a.byteLength; return a; };
            var day = copy(Uint16Array, n), cell = copy(Uint32Array, n), value = copy(Float32Array, n);
            return built[product] = Promise.resolve({day: day, cell: cell, value: value, dates: info.dates});
        }
        var days = [], cells = [], values = [], i = 0, harmonized = product === "harmonized";
        built[product] = new Promise(function(resolve, reject) {
            function next() {
                if (i >= info.dates.length) {
                    var out = {day: Uint16Array.from(days), cell: Uint32Array.from(cells), value: Float32Array.from(values), dates: info.dates};
                    var head = Buffer.alloc(4); head.writeUInt32LE(days.length, 0);
                    fs.writeFileSync(file, Buffer.concat([head, Buffer.from(out.day.buffer), Buffer.from(out.cell.buffer), Buffer.from(out.value.buffer)]));
                    return resolve(out);
                }
                var date = info.dates[i++], d = Math.round((Date.parse(date + "T00:00:00Z") - EPOCH) / 864e5);
                fs.readFile(path.join(root, product, date + ".json"), "utf8", function(err, text) {
                    if (!err) {
                        try {
                            JSON.parse(text).fires.forEach(function(f) {
                                var v = harmonized ? f[2] : (f[2] > 0 ? 1 : 0);
                                if (v > 0) { days.push(d); cells.push(f[1] * COLS + f[0]); values.push(v); }
                            });
                        } catch (e) {}
                    }
                    setImmediate(next);
                });
            }
            next();
        });
        built[product].catch(function() { delete built[product]; });
        return built[product];
    }

    function api(req, res) {
        if (!root) return res.json(404, {error: "No daily fire layers on this server"});
        var q = req.query, product = String(q.product || "harmonized");
        var w = +q.west, s = +q.south, e = +q.east, n = +q.north;
        if (![w, s, e, n].every(isFinite)) { w = WEST; e = WEST + COLS * CELL; n = NORTH; s = NORTH - ROWS * CELL; }
        var c0 = Math.max(0, Math.floor((w - WEST) / CELL)), c1 = Math.min(COLS - 1, Math.floor((e - WEST) / CELL - 1e-9));
        var r0 = Math.max(0, Math.floor((NORTH - n) / CELL)), r1 = Math.min(ROWS - 1, Math.floor((NORTH - s) / CELL - 1e-9));
        build(product).then(function(idx) {
            var perDay = new Map(), have = {};
            idx.dates.forEach(function(date) { have[date.slice(0, 7)] = (have[date.slice(0, 7)] || 0) + 1; });
            for (var k = 0; k < idx.day.length; k++) {
                var cell = idx.cell[k], r = Math.floor(cell / COLS), c = cell % COLS;
                if (r < r0 || r > r1 || c < c0 || c > c1) continue;
                perDay.set(idx.day[k], (perDay.get(idx.day[k]) || 0) + idx.value[k]);
            }
            var months = {};
            perDay.forEach(function(v, d) { var ym = new Date(EPOCH + d * 864e5).toISOString().slice(0, 7); months[ym] = (months[ym] || 0) + v; });
            var out = {product: product, unit: product === "harmonized" ? "VIIRS-equivalent fire cell-days" : "fire cell-days",
                first: idx.dates[0], last: idx.dates[idx.dates.length - 1], box: [WEST + c0 * CELL, NORTH - (r1 + 1) * CELL, WEST + (c1 + 1) * CELL, NORTH - r0 * CELL],
                months: Object.keys(have).sort().map(function(ym) { return [ym, Math.round((months[ym] || 0) * 100) / 100, have[ym]]; })};
            if (q.daily) out.days = Array.from(perDay.keys()).sort(function(a, b) { return a - b; })
                .map(function(d) { return [new Date(EPOCH + d * 864e5).toISOString().slice(0, 10), Math.round(perDay.get(d) * 1000) / 1000]; });
            res.json(out);
        }, function(err) { res.json(err.status || 500, {error: String(err.message || err)}); });
    }
    return {root: root, api: api};
};
