/**
 * Long drives for navigation (public/ops/nav.js): a start outside a fire's ~50 km routing area gets an ordinary
 * road route toward the fire from an online router; the page joins it to its own fire-aware routing where the
 * route enters the area.
 *
 * GET /api/longroute?from=lon,lat&to=lon,lat
 *   -> {provider, coords: [[lon, lat], ...], t: [seconds from the start at each point], dist (m), time (s)}
 *
 * Router: OpenRouteService when ORS_API_KEY is set (free key, 2,000 routes a day), else the public FOSSGIS
 * Valhalla server (fair use; it takes up to ~1,500 km per request, so longer trips are split into legs).
 */
var VALHALLA = "https://valhalla1.openstreetmap.de/route";
var ORS = "https://api.openrouteservice.org/v2/directions/driving-car/geojson";

function decodePolyline6(s) {
    var out = [], i = 0, lat = 0, lon = 0;
    while (i < s.length) {
        for (var k = 0, v = [0, 0]; k < 2; k++) {
            var shift = 0, result = 0, b;
            do { b = s.charCodeAt(i++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
            v[k] = result & 1 ? ~(result >> 1) : result >> 1;
        }
        lat += v[0]; lon += v[1];
        out.push([lon / 1e6, lat / 1e6]);
    }
    return out;
}

function metres(a, b) {
    var p0 = a[1] * Math.PI / 180, p1 = b[1] * Math.PI / 180, dl = (b[0] - a[0]) * Math.PI / 180;
    var h = Math.pow(Math.sin((p1 - p0) / 2), 2) + Math.cos(p0) * Math.cos(p1) * Math.pow(Math.sin(dl / 2), 2);
    return 12742000 * Math.asin(Math.sqrt(Math.min(1, h)));
}

/** Seconds from the start at each point: each step's time spread over its points by distance. */
function times(coords, steps) {
    var t = new Array(coords.length).fill(0), at = 0;
    steps.forEach(function(s) {
        var len = 0, i;
        for (i = s.from; i < s.to; i++) len += metres(coords[i], coords[i + 1]);
        for (i = s.from; i < s.to; i++) {
            t[i] = at;
            at += len ? s.time * metres(coords[i], coords[i + 1]) / len : 0;
        }
        t[s.to] = at;
    });
    return t;
}

async function post(url, body, headers) {
    var r = await fetch(url, {method: "POST", headers: Object.assign({"Content-Type": "application/json"}, headers),
        body: JSON.stringify(body), signal: AbortSignal.timeout(20000)});
    var d = await r.json().catch(function() { return {}; });
    if (!r.ok) throw new Error((d.error && (d.error.message || d.error)) || "router answered " + r.status);
    return d;
}

/** The public server refuses trips over ~1,500 km: those go through a stop at the straight-line midpoint
 *  (the server snaps it to the nearest road), up to 4 legs. */
async function valhalla(from, to, depth) {
    var locs = [{lon: from[0], lat: from[1]}, {lon: to[0], lat: to[1]}];
    if (depth) {
        var mid = [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2];
        var a = await valhalla(from, mid, depth - 1), b = await valhalla(mid, to, depth - 1);
        return {provider: a.provider, coords: a.coords.concat(b.coords.slice(1)), t: a.t.concat(b.t.slice(1).map(function(v) { return v + a.time; })),
            dist: a.dist + b.dist, time: a.time + b.time};
    }
    var d = await post(VALHALLA, {locations: locs, costing: "auto"});
    var leg = d.trip.legs[0], coords = decodePolyline6(leg.shape);
    var steps = leg.maneuvers.map(function(m) { return {from: m.begin_shape_index, to: m.end_shape_index, time: m.time}; });
    return {provider: "Valhalla (FOSSGIS)", coords: coords, t: times(coords, steps), dist: leg.summary.length * 1000, time: leg.summary.time};
}
async function valhallaAny(from, to) {
    var km = metres(from, to) / 1000;
    return valhalla(from, to, km > 2200 ? 2 : km > 1000 ? 1 : 0).catch(function(e) {
        if (!/max distance/.test(e.message) || km > 4400) throw e;
        return valhalla(from, to, km > 1000 ? 2 : 1);  // road distance can be much longer than the straight line
    });
}

async function ors(from, to, key) {
    var d = await post(ORS, {coordinates: [from, to]}, {Authorization: key});
    var f = d.features[0], coords = f.geometry.coordinates, seg = f.properties.segments[0];
    var steps = seg.steps.map(function(s) { return {from: s.way_points[0], to: s.way_points[1], time: s.duration}; });
    return {provider: "openrouteservice", coords: coords, t: times(coords, steps), dist: seg.distance, time: seg.duration};
}

module.exports = function() {
    var key = process.env.ORS_API_KEY, cache = new Map();
    return async function(req, res) {
        var p = function(s) { var v = String(s || "").split(",").map(Number); return v.length === 2 && v.every(isFinite) && Math.abs(v[0]) <= 180 && Math.abs(v[1]) <= 90 ? v : null; };
        var from = p(req.query.from), to = p(req.query.to);
        if (!from || !to) return res.json(400, {error: "from and to must be lon,lat"});
        var k = from.concat(to).map(function(v) { return v.toFixed(4); }).join(",");
        try {
            if (!cache.has(k)) {
                if (cache.size > 200) cache.delete(cache.keys().next().value);
                cache.set(k, key ? ors(from, to, key) : valhallaAny(from, to));
            }
            var r = await cache.get(k);
            res.json({provider: r.provider, dist: Math.round(r.dist), time: Math.round(r.time),
                coords: r.coords.map(function(c) { return [+c[0].toFixed(5), +c[1].toFixed(5)]; }),
                t: r.t.map(function(v) { return Math.round(v); })});
        } catch (e) {
            cache.delete(k);
            res.json(502, {error: "Road router: " + e.message});
        }
    };
};
