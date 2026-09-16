// P1: prove the real layers land in the right place on a real basemap.
// The planner stepper, constraints and generation arrive in later steps.

const API = window.NEON_API_BASE;

// One colour per layer, so the map legend and the panel agree.
const STYLE = {
  building:      { color: "#8d99ae", fill: true },
  sidewalk:      { color: "#c9ada7", fill: true },
  road:          { color: "#adb5bd", fill: true },
  green_space:   { color: "#52b788", fill: true },
  water:         { color: "#4cc9f0", fill: true },
  tree:          { color: "#2d6a4f", fill: false },
  school:        { color: "#e76f51", fill: false },
  kindergarten:  { color: "#f4a261", fill: false },
  hydrant:       { color: "#e63946", fill: false },
  transit_stop:  { color: "#7209b7", fill: false },
  power_line_hv: { color: "#ffb703", fill: false },
};
// Heavy polygon layers stay off until asked for; the map should open legible.
const ON_BY_DEFAULT = ["building", "tree", "school", "power_line_hv", "transit_stop"];

// swisstopo's grey base map, faded: it gives Swiss context (street names, rail,
// river) while leaving enough contrast for the planning layers drawn on top.
const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      swisstopo: {
        type: "raster",
        tiles: [
          "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-grau/default/current/3857/{z}/{x}/{y}.jpeg",
        ],
        tileSize: 256,
        maxzoom: 19,
        attribution:
          '<a href="https://www.geo.admin.ch/">© swisstopo</a> · ' +
          '<a href="https://data.stadt-zuerich.ch/">Stadt Zürich OGD</a>',
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#f6f5f3" } },
      { id: "basemap", type: "raster", source: "swisstopo", paint: { "raster-opacity": 0.45 } },
    ],
  },
  center: [8.52, 47.39], // replaced by fitBounds once the study area loads
  zoom: 14,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }));

const setState = (text, cls) => {
  const el = document.getElementById("api-state");
  el.textContent = text;
  el.className = `pill ${cls || ""}`;
};

function addLayer(info) {
  const style = STYLE[info.name] || { color: "#888", fill: true };
  const visible = ON_BY_DEFAULT.includes(info.name);

  map.addSource(info.name, { type: "geojson", data: `${API}/api/layers/${info.name}` });

  const vis = { visibility: visible ? "visible" : "none" };
  if (info.geometry_type === "Polygon") {
    map.addLayer({
      id: `${info.name}-fill`, type: "fill", source: info.name, layout: vis,
      paint: { "fill-color": style.color, "fill-opacity": 0.45 },
    });
    map.addLayer({
      id: `${info.name}-line`, type: "line", source: info.name, layout: vis,
      paint: { "line-color": style.color, "line-width": 0.6 },
    });
  } else if (info.geometry_type === "LineString") {
    map.addLayer({
      id: `${info.name}-line`, type: "line", source: info.name, layout: vis,
      paint: { "line-color": style.color, "line-width": 3 },
    });
  } else {
    map.addLayer({
      id: `${info.name}-point`, type: "circle", source: info.name, layout: vis,
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 2.5, 18, 6],
        "circle-color": style.color,
        "circle-stroke-width": 1,
        "circle-stroke-color": "rgba(255,255,255,0.8)",
      },
    });
  }

  // Click anything to see what the open data actually says about it.
  const ids = ["-fill", "-line", "-point"].map((s) => info.name + s).filter((id) => map.getLayer(id));
  for (const id of ids) {
    map.on("click", id, (e) => {
      const p = e.features[0].properties || {};
      const rows = Object.entries(p)
        .filter(([k]) => k !== "source")
        .map(([k, v]) => `<div><b>${k}</b> ${v}</div>`)
        .join("");
      new maplibregl.Popup({ closeButton: false })
        .setLngLat(e.lngLat)
        .setHTML(`<strong>${info.title}</strong>${rows}<div class="src">${p.source || ""}</div>`)
        .addTo(map);
    });
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }
  return visible;
}

function addToggle(info, visible) {
  const style = STYLE[info.name] || { color: "#888" };
  const row = document.createElement("label");
  row.className = "layer";
  row.innerHTML = `
    <input type="checkbox" ${visible ? "checked" : ""}>
    <span class="swatch" style="background:${style.color}"></span>
    <span title="${info.source}">${info.title}</span>
    <span class="count">${info.feature_count}</span>`;
  row.querySelector("input").addEventListener("change", (e) => {
    const v = e.target.checked ? "visible" : "none";
    for (const suffix of ["-fill", "-line", "-point"]) {
      const id = info.name + suffix;
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", v);
    }
  });
  document.getElementById("layers").append(row);
}

map.on("load", async () => {
  try {
    const area = await fetch(`${API}/api/area`).then((r) => r.json());
    setState(`API ok · ${area.name}`, "ok");
    document.getElementById("area-desc").textContent = area.description;

    const ring = area.polygon_wgs84.coordinates[0];
    const bounds = ring.reduce(
      (b, c) => b.extend(c), new maplibregl.LngLatBounds(ring[0], ring[0]));
    map.fitBounds(bounds, { padding: 24, duration: 0 });

    map.addSource("study-area", { type: "geojson", data: area.polygon_wgs84 });
    map.addLayer({
      id: "study-area-line", type: "line", source: "study-area",
      paint: { "line-color": "#1f6feb", "line-width": 1.5, "line-dasharray": [3, 2] },
    });

    for (const info of area.layers) addToggle(info, addLayer(info));

    const gaps = document.getElementById("gaps");
    for (const g of area.unavailable_layers) {
      const li = document.createElement("li");
      li.innerHTML = `<b>${g.name}</b> — ${g.reason}`;
      gaps.append(li);
    }
  } catch (e) {
    setState(`API unreachable: ${e}`, "bad");
  }
});
