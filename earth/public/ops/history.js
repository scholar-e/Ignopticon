// Fire history 2000-2026: the challenge's burning-activity calendar and the daily MODIS/VIIRS/harmonized layers
// (scripts/export_ui.py -> /data/history, server sums in earth/history.js).
// - Calendar: years x months of fire activity in the area in view (or a selected fire's surroundings), as totals
//   or against the median of the same month over all years; click a month to see its busiest day on the map.
// - Map layer: one day's 0.1 degree cells for one product, coloured by activity; unobserved cells are left clear.
// - CSV: monthly calendar, or daily values, for the area and product.
const PRODUCTS = {
  harmonized: {label: "Harmonized (VIIRS-equivalent)", short: "Harmonized"},
  MOD14A1: {label: "MODIS Terra (raw)", short: "MODIS Terra"},
  MYD14A1: {label: "MODIS Aqua (raw)", short: "MODIS Aqua"},
  VNP14A1: {label: "VIIRS S-NPP (raw)", short: "VIIRS S-NPP"},
  VJ114A1: {label: "VIIRS NOAA-20 (raw)", short: "VIIRS NOAA-20"},
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Sequential (low = dark ember, high = pale gold) and diverging (below median blue, above red) ramps.
const SEQ = [[42, 36, 33], [110, 42, 24], [178, 54, 22], [232, 98, 32], [250, 160, 60], [255, 222, 140]];
const DIV = [[60, 120, 200], [130, 170, 220], [200, 205, 212], [235, 150, 110], [215, 60, 45]];
const ramp = (stops, t) => {
  t = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(t)), f = t - i;
  return stops[i].map((v, k) => Math.round(v + f * (stops[i + 1][k] - v)));
};
const rgb = (c) => `rgb(${c.join(",")})`;
const fmt = (v) => v >= 100 ? Math.round(v).toLocaleString("en-US") : v >= 10 ? v.toFixed(0) : v.toFixed(1);

export function createHistory(ctx) {
  const {$, esc} = ctx;
  let index = null, product = "harmonized", mode = "total", data = null, box = null, boxLabel = "", layerDate = null, loading = 0;

  async function getIndex() {
    if (!index) index = fetch("/data/history/index.json").then((r) => r.ok ? r.json() : null).catch(() => null);
    return index;
  }

  // ---- Calendar ----
  function collapse(on) {
    $("#hist").classList.toggle("min", on);
    $("#hist-min").setAttribute("aria-expanded", String(!on));
    $("#hist-min").textContent = on ? "Show calendar" : "Hide calendar";
  }
  async function open() {
    $("#hist").hidden = false; collapse(false);
    const idx = await getIndex();
    if (!idx) { $("#hist-body").innerHTML = `<p class="hist-msg">Daily fire layers are not available on this server.</p>`; return; }
    const sel = $("#hist-product");
    sel.innerHTML = Object.keys(PRODUCTS).filter((p) => idx.products[p]).map((p) =>
      `<option value="${p}">${PRODUCTS[p].label} · ${idx.products[p].first.slice(0, 4)}–${idx.products[p].last.slice(0, 4)}</option>`).join("");
    if (!idx.products[product]) product = sel.options[0]?.value;
    sel.value = product;
    useView();
  }
  function useView() {
    const f = ctx.getFire(), m = ctx.getMap();
    if (f) {  // a selected fire: its surroundings, at least 1 degree across, where history is meaningful
      const [w, s, e, n] = f.bbox, cx = (w + e) / 2, cy = (s + n) / 2, hw = Math.max(0.5, (e - w) / 2 + 0.3), hh = Math.max(0.5, (n - s) / 2 + 0.3);
      box = [cx - hw, cy - hh, cx + hw, cy + hh]; boxLabel = `around ${f.name || "the selected fire"}`;
    } else if (m) {
      const b = m.getBounds(); box = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]; boxLabel = "in the map view";
    } else { box = [-125, 24, -66, 50]; boxLabel = "lower 48"; }
    load();
  }
  async function load() {
    const mine = ++loading;
    $("#hist-body").innerHTML = `<p class="hist-msg">Summing ${PRODUCTS[product].short} days ${boxLabel}…</p>`;
    try {
      const q = new URLSearchParams({product, west: box[0], south: box[1], east: box[2], north: box[3]});
      const r = await fetch(`/api/history?${q}`), d = await r.json();
      if (!r.ok) throw new Error(d.error);
      if (mine !== loading) return;
      data = d; drawBox(); render();
    } catch (e) { if (mine === loading) $("#hist-body").innerHTML = `<p class="hist-msg error">Could not load history: ${esc(e.message)}</p>`; }
  }
  function render() {
    const byYm = new Map(data.months.map(([ym, v, n]) => [ym, {v, n}]));
    const years = [...new Set(data.months.map(([ym]) => +ym.slice(0, 4)))].sort((a, b) => b - a);
    const median = MONTHS.map((_, m) => {
      const vals = years.map((y) => byYm.get(`${y}-${String(m + 1).padStart(2, "0")}`)).filter((x) => x && x.n >= 20).map((x) => x.v).sort((a, b) => a - b);
      return vals.length ? vals[Math.floor(vals.length / 2)] : 0;
    });
    const max = Math.max(1, ...data.months.map(([, v]) => v));
    const cell = (y, m) => {
      const ym = `${y}-${String(m + 1).padStart(2, "0")}`, x = byYm.get(ym);
      if (!x) return `<td class="na"></td>`;
      let bg, label;
      if (mode === "total") { bg = rgb(x.v ? ramp(SEQ, Math.log1p(x.v) / Math.log1p(max)) : [30, 32, 34]); label = fmt(x.v); }
      else {
        const med = median[m], ratio = med > 0 ? x.v / med : x.v > 0 ? 4 : 1;
        bg = rgb(ramp(DIV, 0.5 + Math.max(-0.5, Math.min(0.5, Math.log2(Math.max(ratio, 1 / 16)) / 4))));
        label = med > 0 ? `${Math.round(ratio * 100)}%` : x.v > 0 ? "new" : "–";
      }
      const tip = `${MONTHS[m]} ${y}: ${fmt(x.v)} ${data.unit}${mode === "anomaly" ? ` (median ${fmt(median[m])})` : ""}${x.n < 28 ? ` · ${x.n} days of data` : ""}`;
      return `<td><button type="button" data-ym="${ym}" style="background:${bg}" title="${esc(tip)}" aria-label="${esc(tip)}"><span>${label}</span></button></td>`;
    };
    const total = (y) => MONTHS.reduce((s, _, m) => s + (byYm.get(`${y}-${String(m + 1).padStart(2, "0")}`)?.v || 0), 0);
    $("#hist-body").innerHTML = `<p class="hist-sub">${esc(PRODUCTS[product].label)} · ${esc(data.unit)} ${esc(boxLabel)} · ${mode === "total" ? "monthly totals" : "% of the median for that month"}</p>
<div class="hist-scroll"><table class="hist-cal ${mode}"><thead><tr><th></th>${MONTHS.map((m) => `<th>${m}</th>`).join("")}<th>Year</th></tr></thead><tbody>
${years.map((y) => `<tr><th>${y}</th>${MONTHS.map((_, m) => cell(y, m)).join("")}<td class="yt">${fmt(total(y))}</td></tr>`).join("")}
${mode === "anomaly" ? `<tr class="med"><th>Median</th>${median.map((v) => `<td>${fmt(v)}</td>`).join("")}<td></td></tr>` : ""}
</tbody></table></div>
<div class="hist-legend">${mode === "total"
      ? `<span>Less</span><i style="background:linear-gradient(90deg,${SEQ.map(rgb).join(",")})"></i><span>More</span>`
      : `<span>Below median</span><i style="background:linear-gradient(90deg,${DIV.map(rgb).join(",")})"></i><span>Above</span>`}
<span class="hist-note">Click a month to map its busiest day.</span></div>`;
  }

  // ---- Map layer ----
  async function showDay(date) {
    layerDate = date; $("#hist-day").value = date;
    const map = ctx.getMap(); if (!map) return;
    const idx = await getIndex(); const g = idx.grid;
    const img = new Image(); img.src = `/data/history/${product}/${date}.png`;
    try { await img.decode(); } catch { $("#hist-daynote").textContent = `No ${PRODUCTS[product].short} layer for ${date}.`; hideDay(); return; }
    const src = Object.assign(document.createElement("canvas"), {width: g.cols, height: g.rows}).getContext("2d");
    src.drawImage(img, 0, 0);
    const d = src.getImageData(0, 0, g.cols, g.rows).data;
    // Rows evenly spaced in Web Mercator y, so cells land at the right latitude when MapLibre stretches the image.
    const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)), top = merc(g.north), bottom = merc(g.south), H = g.rows * 4, W = g.cols;
    const out = Object.assign(document.createElement("canvas"), {width: W, height: H}), oc = out.getContext("2d"), o = oc.createImageData(W, H);
    let fires = 0;
    for (let row = 0; row < H; row++) {
      const lat = (2 * Math.atan(Math.exp(top + (bottom - top) * (row + 0.5) / H)) - Math.PI / 2) * 180 / Math.PI;
      const j = Math.floor((g.north - lat) / g.cell_deg); if (j < 0 || j >= g.rows) continue;
      for (let i = 0; i < W; i++) {
        const k = (j * W + i) * 4;
        if (!d[k + 3]) continue;  // not observed (cloud, no pass)
        let t = -1;
        if (product === "harmonized") { if (d[k] >= 13) t = d[k] / 255; }          // p >= 0.05
        else if (d[k] > 0) t = Math.min(1, Math.log1p(d[k]) / Math.log1p(40));   // fire pixels in the cell
        if (t < 0) continue;
        fires++;
        o.data.set([...ramp(SEQ.slice(2), t), 230], (row * W + i) * 4);
      }
    }
    oc.putImageData(o, 0, 0);
    const coords = [[g.west, g.north], [g.east, g.north], [g.east, g.south], [g.west, g.south]];
    if (map.getSource("hist-day")) map.getSource("hist-day").updateImage({url: out.toDataURL(), coordinates: coords});
    else {
      map.addSource("hist-day", {type: "image", url: out.toDataURL(), coordinates: coords});
      map.addLayer({id: "hist-day", type: "raster", source: "hist-day", paint: {"raster-resampling": "nearest", "raster-opacity": 0.9}}, "fires-fill");
    }
    map.setLayoutProperty("hist-day", "visibility", "visible");
    $("#hist-daynote").textContent = `${PRODUCTS[product].short}, ${date}: ${fires.toLocaleString("en-US")} cells with fire${product === "harmonized" ? " (p ≥ 0.05)" : ""}; clear where not observed.`;
    $("#hist-clear").hidden = false;
  }
  function hideDay() {
    layerDate = null; $("#hist-clear").hidden = true;
    const map = ctx.getMap(); if (map?.getLayer("hist-day")) map.setLayoutProperty("hist-day", "visibility", "none");
  }
  function drawBox() {
    const map = ctx.getMap(); if (!map || !data) return;
    const [w, s, e, n] = data.box, geo = {type: "Feature", geometry: {type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}};
    if (map.getSource("hist-box")) map.getSource("hist-box").setData(geo);
    else {
      map.addSource("hist-box", {type: "geojson", data: geo});
      map.addLayer({id: "hist-box", type: "line", source: "hist-box", paint: {"line-color": "#FFDE8C", "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": 0.8}});
    }
    map.setLayoutProperty("hist-box", "visibility", "visible");
  }
  async function busiestDay(ym) {
    const q = new URLSearchParams({product, west: box[0], south: box[1], east: box[2], north: box[3], daily: 1});
    const d = await fetch(`/api/history?${q}`).then((r) => r.json());
    const days = (d.days || []).filter(([date]) => date.startsWith(ym));
    return days.length ? days.reduce((p, x) => x[1] > p[1] ? x : p)[0] : `${ym}-15`;
  }

  // ---- CSV ----
  async function csv(daily) {
    let rows;
    if (daily) {
      const q = new URLSearchParams({product, west: box[0], south: box[1], east: box[2], north: box[3], daily: 1});
      const d = await fetch(`/api/history?${q}`).then((r) => r.json());
      rows = [["date", "value"], ...d.days];
    } else rows = [["month", "value", "days_with_data"], ...data.months];
    const head = `# ${PRODUCTS[product].label}; ${data.unit}; box W,S,E,N = ${data.box.map((v) => v.toFixed(1)).join(",")}; source: Ignopticon (MODIS MOD14A1/MYD14A1, VIIRS VNP14A1/VJ114A1, harmonized)\n`;
    const blob = new Blob([head + rows.map((r) => r.join(",")).join("\n") + "\n"], {type: "text/csv"});
    const a = Object.assign(document.createElement("a"), {href: URL.createObjectURL(blob), download: `fire-history-${product}-${daily ? "daily" : "monthly"}.csv`});
    document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
  }

  // ---- Wiring ----
  $("#hist-open").addEventListener("click", open);
  $("#hist-close").addEventListener("click", () => { $("#hist").hidden = true; hideDay(); const m = ctx.getMap(); if (m?.getLayer("hist-box")) m.setLayoutProperty("hist-box", "visibility", "none"); });
  $("#hist-product").addEventListener("change", (e) => { product = e.target.value; load(); if (layerDate) showDay(layerDate); });
  $("#hist-view").addEventListener("click", useView);
  $("#hist").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.mode) { mode = b.dataset.mode; for (const x of document.querySelectorAll("[data-mode]")) x.setAttribute("aria-pressed", x.dataset.mode === mode); if (data) render(); }
    else if (b.dataset.ym) { collapse(true); showDay(await busiestDay(b.dataset.ym)); }
    else if (b.id === "hist-min") collapse(!$("#hist").classList.contains("min"));
    else if (b.id === "hist-csv") csv(false);
    else if (b.id === "hist-csv-daily") csv(true);
    else if (b.id === "hist-clear") hideDay();
  });
  $("#hist-day").addEventListener("change", (e) => { if (e.target.value) { collapse(true); showDay(e.target.value); } });
  return {styleLoaded() { if (data) drawBox(); if (layerDate) showDay(layerDate); }};
}
