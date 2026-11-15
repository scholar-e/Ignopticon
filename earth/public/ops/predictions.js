// Eric's next-day fire activity predictions (data/samples/predictions, served at /data/predictions/ by dev-server.js):
// P(VIIRS S-NPP sees fire in a 0.1° cell on the target date | a clear look), from history through the day before.
// PNG R = round(P * 255), A = 255 on land (README.md there). Shown as a band: the cells at or above the model's
// threshold, merged into one translucent area with a dashed outline. It is a coarse "fire likely here tomorrow" watch
// area (10 km cells), not a perimeter. Only drawn when the view date is a predicted date.
const BASE = "/data/predictions/";
const AMBER = "#FFB020";
let index = null;
const bands = new Map();  // arm -> {fill, line} GeoJSON

export async function predictionIndex() {
  if (!index) index = fetch(BASE + "index.json").then((r) => r.ok ? r.json() : null).catch(() => null);
  return index;
}

async function band(arm, idx) {
  if (bands.has(arm)) return bands.get(arm);
  const {grid, arms} = idx, img = new Image();
  img.src = BASE + arms[arm].png;
  await img.decode();
  const W = grid.width, H = grid.height, r = grid.resolution_degrees, t = arms[arm].threshold;
  const g = Object.assign(document.createElement("canvas"), {width: W, height: H}).getContext("2d");
  g.drawImage(img, 0, 0);
  const d = g.getImageData(0, 0, W, H).data;
  const on = (i, j) => i >= 0 && j >= 0 && i < W && j < H && d[(j * W + i) * 4 + 3] > 0 && d[(j * W + i) * 4] / 255 >= t;
  const lon = (i) => grid.west + i * r, lat = (j) => grid.north - j * r;
  const cells = [], edges = [];
  for (let j = 0; j < H; j++) for (let i = 0; i < W; i++) {
    if (!on(i, j)) continue;
    const [w, e, n, s] = [lon(i), lon(i + 1), lat(j), lat(j + 1)];
    cells.push([[[w, n], [e, n], [e, s], [w, s], [w, n]]]);
    // Outline: the cell sides that face a cell outside the band.
    if (!on(i, j - 1)) edges.push([[w, n], [e, n]]);
    if (!on(i, j + 1)) edges.push([[w, s], [e, s]]);
    if (!on(i - 1, j)) edges.push([[w, n], [w, s]]);
    if (!on(i + 1, j)) edges.push([[e, n], [e, s]]);
  }
  const result = {
    fill: {type: "Feature", geometry: {type: "MultiPolygon", coordinates: cells}, properties: {part: "fill"}},
    line: {type: "Feature", geometry: {type: "MultiLineString", coordinates: edges}, properties: {part: "line"}},
    cells: cells.length,
  };
  bands.set(arm, result);
  return result;
}

// Shows (or hides) the band under the fire layers. Returns a note for the Key: what is shown, or why nothing is.
export async function setActivity(map, {on, date, arm, beforeId}) {
  const ids = ["activity-fill", "activity-line"];
  const hide = () => ids.forEach((id) => map.getLayer(id) && map.setLayoutProperty(id, "visibility", "none"));
  if (!on) { hide(); return ""; }
  const idx = await predictionIndex();
  if (!idx) { hide(); return "Predictions unavailable"; }
  if (!idx.dates.includes(date)) { hide(); return `Predictions only for ${idx.dates.join(", ")}`; }
  arm = idx.arms[arm] ? arm : idx.default_arm;
  const b = await band(arm, idx), data = {type: "FeatureCollection", features: [b.fill, b.line]};
  if (map.getSource("activity")) map.getSource("activity").setData(data);
  else {
    map.addSource("activity", {type: "geojson", data});
    map.addLayer({id: "activity-fill", type: "fill", source: "activity", filter: ["==", ["get", "part"], "fill"],
      paint: {"fill-color": AMBER, "fill-opacity": 0.22, "fill-antialias": false}}, beforeId);
    map.addLayer({id: "activity-line", type: "line", source: "activity", filter: ["==", ["get", "part"], "line"],
      paint: {"line-color": AMBER, "line-width": 2, "line-dasharray": [2, 1.5]}}, beforeId);
  }
  ids.forEach((id) => map.setLayoutProperty(id, "visibility", "visible"));
  return `${arm}: ${b.cells} cells at P ≥ ${idx.arms[arm].threshold.toFixed(2)} (10 km squares)`;
}
