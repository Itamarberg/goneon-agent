/* neon-agent — the planner's page.
 *
 * All planning logic lives in the API. This file keeps the plan state, draws it,
 * and calls the endpoints. It holds no thresholds and computes no verdicts: every
 * number it shows came back from a tool (ADR 0001).
 *
 * There is no server session, so this state *is* the plan (docs/PLAN.md §7).
 */

const API = window.NEON_API_BASE;

// Importance, as a planner would say it, mapped to the weight the generator uses.
// A preference marked "critical" outweighs three normal ones when they conflict.
const IMPORTANCE = [
  { label: "nice to have", weight: 0.3 },
  { label: "normal", weight: 1 },
  { label: "important", weight: 3 },
  { label: "critical", weight: 8 },
];

const state = {
  areaId: null,
  areas: [],
  areaPolygon: null, // set only when the planner narrows the quarter down
  areaCorner: null,
  geometry: "point",
  objectKind: "tree",
  count: 20,
  start: null,
  end: null,
  picked: [],
  constraints: new Map(), // id -> { constraint, hard, weight }
  catalog: [],
  variants: [],
  selectedVariant: null,
  drawing: null,
  chat: [],
  step: 1,
};

const $ = (id) => document.getElementById(id);
const api = async (path, options) => {
  const r = await fetch(API + path, options);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail ? JSON.stringify(body.detail) : `${r.status} ${r.statusText}`);
  }
  return r.json();
};
const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/* ------------------------------------------------------------------ map --- */

// Context layers read as a muted neutral-to-warm ramp so the two brand accents
// stay reserved for what the planner is deciding: the zones and the plan.
const LAYER_STYLE = {
  building: "#8A8A8A", sidewalk: "#B8A99A", road: "#9AA3AB", green_space: "#3A9E6B",
  water: "#4CC9F0", tree: "#8CFF9E", school: "#FF8A5B", kindergarten: "#FFC857",
  hydrant: "#FF4D5E", transit_stop: "#B57BFF", power_line_hv: "#FFD166",
};

// The brand's two accents carry the meaning: cyan is what you may do, pink is
// what you may not. Everything else on the map is context.
const OK = "#3EFFF1";
const NO = "#F23093";
const LAYERS_ON = ["building", "school", "transit_stop", "power_line_hv"];

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      swisstopo: {
        type: "raster",
        tiles: ["https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-grau/default/current/3857/{z}/{x}/{y}.jpeg"],
        tileSize: 256,
        maxzoom: 19,
        attribution:
          '<a href="https://www.geo.admin.ch/">© swisstopo</a> · ' +
          '<a href="https://data.stadt-zuerich.ch/">Stadt Zürich OGD</a>',
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#000000" } },
      // Swapping raster-brightness min and max inverts the light swisstopo
      // sheet into a dark one, so the basemap sits under the black canvas
      // instead of fighting it.
      { id: "basemap", type: "raster", source: "swisstopo",
        paint: { "raster-opacity": 0.42, "raster-brightness-min": 1,
                 "raster-brightness-max": 0, "raster-saturation": -1,
                 "raster-contrast": 0.2 } },
    ],
  },
  center: [8.52, 47.39],
  zoom: 14,
  // Required to read the canvas back for the printed report; without it the
  // browser is free to discard the drawing buffer after each frame.
  preserveDrawingBuffer: true,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }));

const EMPTY = { type: "FeatureCollection", features: [] };
const setData = (id, data) => map.getSource(id) && map.getSource(id).setData(data || EMPTY);

function addOverlaySources() {
  for (const id of ["zone-forbidden", "zone-allowed", "plan", "findings", "picked", "study-area"]) {
    map.addSource(id, { type: "geojson", data: EMPTY });
  }
  map.addLayer({ id: "zone-allowed-fill", type: "fill", source: "zone-allowed",
    paint: { "fill-color": OK, "fill-opacity": 0.10 } });
  map.addLayer({ id: "zone-forbidden-fill", type: "fill", source: "zone-forbidden",
    paint: { "fill-color": NO, "fill-opacity": 0.16 } });
  map.addLayer({ id: "study-area-line", type: "line", source: "study-area",
    paint: { "line-color": NO, "line-width": 1.5, "line-dasharray": [3, 2] } });
  map.addLayer({ id: "plan-line", type: "line", source: "plan",
    paint: { "line-color": OK, "line-width": 4 } });
  map.addLayer({ id: "plan-point", type: "circle", source: "plan",
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 4, 18, 8],
             "circle-color": OK, "circle-stroke-width": 2, "circle-stroke-color": "#000" } });
  map.addLayer({ id: "findings-line", type: "line", source: "findings",
    paint: { "line-color": NO, "line-width": 2, "line-dasharray": [2, 1] } });
  map.addLayer({ id: "picked-point", type: "circle", source: "picked",
    paint: { "circle-radius": 6, "circle-color": "#FFFFFF", "circle-stroke-width": 2,
             "circle-stroke-color": "#000" } });
}

// Data layers belong to a study area, so they are torn down and rebuilt on switch.
let dataLayerIds = [];
function clearDataLayers() {
  for (const id of dataLayerIds) if (map.getLayer(id)) map.removeLayer(id);
  for (const name of new Set(dataLayerIds.map((id) => id.replace(/-(fill|line|point)$/, "")))) {
    if (map.getSource(name)) map.removeSource(name);
  }
  dataLayerIds = [];
  $("layers").innerHTML = "";
}

function addDataLayer(info) {
  const colour = LAYER_STYLE[info.name] || "#888";
  const visible = LAYERS_ON.includes(info.name);
  const vis = { visibility: visible ? "visible" : "none" };
  const url = `${API}/api/layers/${info.name}?area_id=${state.areaId}`;
  map.addSource(info.name, { type: "geojson", data: url });

  const add = (suffix, spec) => {
    const id = info.name + suffix;
    map.addLayer({ id, source: info.name, layout: vis, ...spec }, "zone-allowed-fill");
    dataLayerIds.push(id);
  };
  if (info.geometry_type === "Polygon") {
    add("-fill", { type: "fill", paint: { "fill-color": colour, "fill-opacity": 0.4 } });
  } else if (info.geometry_type === "LineString") {
    add("-line", { type: "line", paint: { "line-color": colour, "line-width": 3 } });
  } else {
    add("-point", { type: "circle",
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 2.5, 18, 5.5],
               "circle-color": colour, "circle-stroke-width": 1,
               "circle-stroke-color": "rgba(0,0,0,.65)" } });
  }

  const row = document.createElement("label");
  row.className = "layer";
  row.innerHTML = `<input type="checkbox" ${visible ? "checked" : ""}>
    <span class="swatch" style="background:${colour}"></span>
    <span title="${info.source}">${info.title}</span>
    <span class="count">${info.feature_count}</span>`;
  row.querySelector("input").addEventListener("change", (e) => {
    const v = e.target.checked ? "visible" : "none";
    for (const suffix of ["-fill", "-line", "-point"]) {
      const id = info.name + suffix;
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", v);
    }
  });
  $("layers").append(row);
}

/* ------------------------------------------------------------- the steps -- */

function openStep(n) {
  state.step = n;
  for (const section of document.querySelectorAll(".step")) {
    section.dataset.open = String(Number(section.dataset.step) === n);
  }
  const open = document.querySelector('.step[data-open="true"]');
  if (open) open.scrollIntoView({ block: "nearest" });
  refreshSummaries();
}

function refreshSummaries() {
  const area = state.areas.find((a) => a.id === state.areaId);
  const summaries = {
    1: area ? area.title + (state.areaPolygon ? " · part of it" : "") : "",
    2: state.geometry === "point"
      ? `${state.count} × ${labelFor(state.objectKind)}`
      : `${labelFor(state.objectKind)}${state.start && state.end ? "" : " — set endpoints"}`,
    3: state.constraints.size
      ? `${[...state.constraints.values()].filter((c) => c.hard).length} must hold, ` +
        `${[...state.constraints.values()].filter((c) => !c.hard).length} preferred`
      : "none yet",
    4: state.variants.length ? `${state.variants.length} variants` : "",
    5: state.selectedVariant ? state.selectedVariant.label : "",
  };
  const done = {
    1: Boolean(state.areaId),
    2: state.geometry === "point" ? Boolean(state.count) : Boolean(state.start && state.end),
    3: state.constraints.size > 0,
    4: state.variants.length > 0,
    5: Boolean(state.selectedVariant),
  };
  for (const section of document.querySelectorAll(".step")) {
    const n = Number(section.dataset.step);
    section.querySelector(".step-summary").textContent = summaries[n] || "";
    section.dataset.done = String(Boolean(done[n]));
  }
  $("generate").disabled = state.geometry === "line" && !(state.start && state.end);
}

const labelFor = (kind) =>
  ({ tree: "street trees", bike_rack: "bike racks", bench: "benches",
     charging_station: "charging stations", power_line: "power line", pipe: "pipe",
     path: "path" }[kind] || kind);

/* -------------------------------------------------------------- step 1 ---- */

function renderAreas() {
  const host = $("area-list");
  host.innerHTML = "";
  for (const area of state.areas) {
    const el = document.createElement("button");
    el.className = "card";
    el.dataset.active = String(area.id === state.areaId);
    el.innerHTML = `<strong>${area.title}</strong>
      <span>${area.description}</span>
      <span class="districts">${area.districts.map((d) => `${d.name} ${Math.round(d.share_pct)}%`).join(" · ")}</span>`;
    el.addEventListener("click", () => selectArea(area.id));
    host.append(el);
  }
}

async function selectArea(areaId) {
  if (state.areaId === areaId) return;
  state.areaId = areaId;
  // Switching the quarter invalidates everything downstream.
  state.areaPolygon = null;
  state.variants = [];
  state.selectedVariant = null;
  state.constraints.clear();
  setData("plan", null);
  setData("findings", null);
  setData("zone-forbidden", null);
  setData("zone-allowed", null);
  $("variants").innerHTML = "";
  $("infeasible").hidden = true;
  $("area-reset").hidden = true;
  $("area-hint").textContent = "";
  setExportEnabled(false);
  renderAreas();

  const area = await api(`/api/area?area_id=${areaId}`);
  const ring = area.polygon_wgs84.coordinates[0];
  map.fitBounds(ring.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(ring[0], ring[0])),
    { padding: 30, duration: 600 });
  setData("study-area", area.polygon_wgs84);

  clearDataLayers();
  for (const info of area.layers) addDataLayer(info);
  fillLayerOptions(area.layers);
  $("area-counts").innerHTML = area.layers
    .map((l) => `<span class="count-chip"><b>${l.feature_count}</b> ${l.title.toLowerCase()}</span>`)
    .join("");
  $("gaps").innerHTML = area.unavailable_layers
    .map((g) => `<li><b>${g.name}</b> — ${g.reason}</li>`).join("");

  const catalog = await api(`/api/catalog?area_id=${areaId}`);
  state.catalog = catalog.constraints;
  renderCatalog();
  refreshSummaries();
}

/* -------------------------------------------------------------- step 3 ---- */

function renderCatalog() {
  const host = $("catalog");
  host.innerHTML = "";
  const relevant = state.catalog.filter((c) => !c.applies_to || c.applies_to === state.objectKind);
  if (!relevant.length) {
    host.innerHTML = '<p class="hint">No curated constraints for this object kind yet. ' +
      'Describe your own rule in the chat and the agent will draft it.</p>';
    return;
  }
  for (const c of relevant) host.append(ruleRow(c));
  refreshSummaries();
}

/* One constraint row. It updates itself in place rather than re-rendering the
 * whole catalog: a planner ticking six constraints in a row should not have the
 * list rebuilt under the cursor, losing scroll position and closing dropdowns. */
function ruleRow(c) {
  const chosen = state.constraints.get(c.id);
  const el = document.createElement("div");
  el.className = "rule";
  el.innerHTML = `
    <div class="rule-top">
      <input type="checkbox">
      <div style="flex:1">
        <div class="rule-title">${c.title}</div>
        <div class="rule-desc">${c.description}</div>
        <div class="rule-controls">
          <div class="segmented">
            <button type="button" data-hard="true">must hold</button>
            <button type="button" data-hard="false">preference</button>
          </div>
          <label class="importance">importance
            <select>${IMPORTANCE.map((i) =>
              `<option value="${i.weight}">${i.label}</option>`).join("")}</select>
          </label>
        </div>
        <div class="source">
          <span class="badge ${c.evaluable ? c.source.kind : "blocked"}">${
            c.evaluable ? c.source.kind : "cannot be checked"}</span>
          ${c.source.url
            ? `<a href="${c.source.url}" target="_blank" rel="noopener">${c.source.text}</a>`
            : c.source.text}
        </div>
        ${c.note ? `<div class="source">${c.note}</div>` : ""}
        ${c.evaluable ? "" : `<div class="blocked-note">${c.not_evaluable_reason}</div>`}
      </div>
    </div>`;

  const box = el.querySelector("input");
  const importance = el.querySelector(".importance");
  const select = el.querySelector(".importance select");

  const sync = () => {
    const entry = state.constraints.get(c.id);
    el.dataset.on = String(Boolean(entry));
    box.checked = Boolean(entry);
    const hard = entry ? entry.hard : c.hard;
    for (const b of el.querySelectorAll(".segmented button")) {
      b.dataset.on = String((b.dataset.hard === "true") === hard);
    }
    // Importance only means something for a preference: a rule that must hold
    // is not traded off against anything.
    importance.hidden = hard;
    select.value = String(entry ? entry.weight : 1);
  };

  box.addEventListener("change", () => {
    if (box.checked) {
      state.constraints.set(c.id, { constraint: c, hard: c.hard, weight: 1 });
    } else {
      state.constraints.delete(c.id);
    }
    sync();
    refreshZones();
    refreshSummaries();
  });

  // Hard vs soft is the planner's call, not the catalog's: the same rule is a
  // requirement in one project and a preference in another.
  for (const button of el.querySelectorAll(".segmented button")) {
    button.addEventListener("click", () => {
      const entry = state.constraints.get(c.id) ||
        { constraint: c, hard: c.hard, weight: 1 };
      entry.hard = button.dataset.hard === "true";
      state.constraints.set(c.id, entry);
      sync();
      refreshZones();
      refreshSummaries();
    });
  }

  select.addEventListener("change", () => {
    const entry = state.constraints.get(c.id);
    if (!entry) return;
    entry.weight = Number(select.value);
    refreshSummaries();
  });

  sync();
  return el;
}

function chosenConstraints() {
  return [...state.constraints.values()].map(({ constraint, hard, weight }) => {
    const { description, evaluable, not_evaluable_reason, ...rest } = constraint;
    return { ...rest, hard, weight };
  });
}

let zoneRequest = 0;
async function refreshZones() {
  const constraints = chosenConstraints();
  const summary = $("zone-summary");
  if (!constraints.length) {
    setData("zone-forbidden", null);
    setData("zone-allowed", null);
    summary.textContent = "";
    return;
  }
  const ticket = ++zoneRequest;
  summary.textContent = "Computing…";
  try {
    const zones = await post("/api/zones", {
      area: state.areaPolygon, area_id: state.areaId, constraints,
    });
    if (ticket !== zoneRequest) return; // a newer tick won
    setData("zone-forbidden", zones.forbidden);
    setData("zone-allowed", zones.allowed);
    const share = Math.round((zones.allowed_area_m2 / zones.area_m2) * 100);
    const skipped = Object.entries(zones.skipped || {});
    summary.innerHTML = `
      <div class="bar"><i style="width:${share}%"></i></div>
      <b>${zones.allowed_area_m2.toLocaleString()} m²</b> available — ${share}% of the area.
      ${skipped.length ? `<div class="hint" style="margin-top:6px">Not shown as a zone: ${skipped
        .map(([id, why]) => `${id} (${why})`).join("; ")}</div>` : ""}`;
  } catch (e) {
    if (ticket === zoneRequest) summary.textContent = `Could not compute zones: ${e.message}`;
  }
}

/* -------------------------------------------------------------- step 4 ---- */

async function generate() {
  const button = $("generate");
  button.setAttribute("aria-busy", "true");
  button.textContent = "Generating…";
  $("infeasible").hidden = true;
  try {
    const spec = state.geometry === "point"
      ? { kind: state.objectKind, geometry: "point", count: Number(state.count) }
      : { kind: state.objectKind, geometry: "line", start: state.start, end: state.end };
    const result = await post("/api/generate", {
      area: state.areaPolygon, area_id: state.areaId, object: spec,
      constraints: chosenConstraints(),
    });
    state.variants = result.variants;
    renderVariants(result);
  } catch (e) {
    $("variants").innerHTML = `<p class="blocked-note">${e.message}</p>`;
  } finally {
    button.removeAttribute("aria-busy");
    button.textContent = "Generate plan variants";
  }
}

function renderVariants(result) {
  const host = $("variants");
  host.innerHTML = "";
  if (result.infeasibility) {
    renderInfeasible(result.infeasibility);
    setData("plan", null);
    setData("findings", null);
    state.selectedVariant = null;
    setExportEnabled(false);
    refreshSummaries();
    return;
  }
  $("infeasible").hidden = true;

  for (const v of result.variants) {
    const el = document.createElement("div");
    el.className = "variant";
    el.dataset.variantId = v.id;
    const metrics = v.metrics.length_m
      ? `${Math.round(v.metrics.length_m)} m · ${v.metrics.vertices} points`
      : `${v.features.length} objects · ${v.metrics.achieved_spacing_m} m apart`;
    const blocked = v.findings.filter((f) => f.severity === "not_evaluable");
    el.innerHTML = `
      <div class="variant-top">
        <span class="variant-label">${v.label}</span>
        <span class="variant-metrics">${metrics}</span>
      </div>
      ${v.tradeoffs.length
        ? v.tradeoffs.map((t) => `<div class="tradeoff">${t.count} × ${t.title}${
            tradeoffDetail(t)}<span class="w"> · ${importanceLabel(t.weight)}</span></div>`).join("")
        : '<div class="clean">Meets every constraint that could be checked.</div>'}
      ${blocked.map((f) => `<div class="blocked-note">${f.message}</div>`).join("")}`;
    el.addEventListener("click", () => selectVariant(v.id));
    host.append(el);
  }
  if (result.variants.length) selectVariant(result.variants[0].id);
  refreshSummaries();
}

const importanceLabel = (w) =>
  (IMPORTANCE.find((i) => i.weight === w) || { label: "normal" }).label;

// A distance rule has a threshold to quote; a containment rule ("stands on
// pavement") has none, and "asked 0 m" reads like a bug.
function tradeoffDetail(t) {
  if (t.worst_measured_m == null) return "";
  if (!t.required_m) return ` — worst is ${t.worst_measured_m} m off`;
  return ` — worst ${t.worst_measured_m} m (asked ${t.required_m} m)`;
}

function selectVariant(id) {
  const variant = state.variants.find((v) => v.id === id);
  if (!variant) return;
  state.selectedVariant = variant;
  for (const el of document.querySelectorAll(".variant")) {
    el.dataset.active = String(el.dataset.variantId === id);
  }
  setData("plan", {
    type: "FeatureCollection",
    features: variant.features.map((f) => ({
      type: "Feature", id: f.id, geometry: f.geometry, properties: { kind: f.kind },
    })),
  });
  setData("findings", {
    type: "FeatureCollection",
    features: variant.findings.filter((f) => f.geometry)
      .map((f) => ({ type: "Feature", geometry: f.geometry, properties: { message: f.message } })),
  });
  setExportEnabled(true);
  refreshSummaries();
}

function renderInfeasible(report) {
  const box = $("infeasible");
  box.hidden = false;
  const items = report.relaxations
    .filter((r) => r.suggested_required_m != null || r.freed_area_m2 > 0)
    .map((r) => {
      const change = r.suggested_required_m != null
        ? `at <b>${r.suggested_required_m} m</b> instead of ${r.current_required_m} m this works`
        : `removing it frees ${Math.round(r.freed_area_m2).toLocaleString()} m²`;
      const gain = r.positions_gained != null ? ` — ${r.positions_gained} positions` : "";
      return `<li><b>${r.title}</b>: ${change}${gain}</li>`;
    });
  box.innerHTML = `<h4>No plan is possible here</h4><p>${report.reason}</p>
    ${items.length ? `<ul>${items.join("")}</ul>` : ""}`;
  $("variants").innerHTML = "";
}

/* -------------------------------------------------------------- step 5 ---- */

function setExportEnabled(on) {
  for (const id of ["export-geojson", "export-pdf"]) $(id).disabled = !on;
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function exportGeoJSON() {
  const v = state.selectedVariant;
  // The constraints travel with the plan: a GeoJSON of points says nothing about
  // what it was checked against.
  download(`neon-plan-${v.id}.geojson`, JSON.stringify({
    type: "FeatureCollection",
    properties: {
      variant: v.id, label: v.label, metrics: v.metrics, study_area: state.areaId,
      constraints: chosenConstraints(), findings: v.findings, generated_by: "neon-agent",
    },
    features: v.features.map((f) => ({
      type: "Feature", id: f.id, geometry: f.geometry, properties: f.properties,
    })),
  }, null, 2), "application/geo+json");
}

/* ------------------------------------------- the planner's own constraint -- */

let constraintTypes = [];

async function loadConstraintForm() {
  const body = await api("/api/constraint-types");
  constraintTypes = body.types;
  $("own-type").innerHTML = constraintTypes
    .map((t) => `<option value="${t.name}">${t.label}</option>`).join("");
  syncOwnForm();
}

function syncOwnForm() {
  const type = constraintTypes.find((t) => t.name === $("own-type").value);
  if (!type) return;
  $("own-layer-field").hidden = !type.needs_layer;
  $("own-distance-field").hidden = !type.needs_distance;
}

function fillLayerOptions(layers) {
  // Only layers this area actually has; a rule against data we do not hold is
  // possible through the agent, but the form should not invite it.
  $("own-layer").innerHTML = layers
    .map((l) => `<option value="${l.name}">${l.title}</option>`).join("");
}

function ownHardness() {
  const on = $("own-hard").querySelector('button[data-on="true"]');
  return on ? on.dataset.hard === "true" : true;
}

async function addOwnConstraint() {
  const feedback = $("own-feedback");
  const title = $("own-title").value.trim();
  const source = $("own-source").value.trim();
  const type = $("own-type").value;
  const spec = constraintTypes.find((t) => t.name === type);
  const distance = $("own-distance").value;

  if (!title || !source) {
    feedback.className = "hint bad";
    feedback.textContent = "A rule needs a title and a source.";
    return;
  }

  const payload = {
    id: slug(title),
    title,
    type,
    source_text: source,
    layer: spec.needs_layer ? $("own-layer").value : null,
    applies_to: state.objectKind,
    d_m: spec.needs_distance && distance !== "" ? Number(distance) : null,
    hard: ownHardness(),
    weight: 1,
    area_id: state.areaId,
  };

  try {
    const result = await post("/api/constraints/draft", payload);
    if (result.problems.length) {
      feedback.className = "hint bad";
      feedback.textContent = result.problems.join(" ");
      return;
    }
    // A proposal only becomes a constraint because the planner added it here.
    const constraint = {
      ...result.proposal,
      description: result.description,
      evaluable: result.evaluable,
      not_evaluable_reason: result.not_evaluable_reason,
    };
    state.catalog = [...state.catalog, constraint];
    state.constraints.set(constraint.id, {
      constraint, hard: constraint.hard, weight: constraint.weight || 1,
    });
    renderCatalog();
    refreshZones();
    feedback.className = "hint ok";
    feedback.textContent = result.evaluable
      ? "Added, and ticked."
      : `Added — but it cannot be checked: ${result.not_evaluable_reason}`;
    $("own-title").value = "";
    $("own-source").value = "";
    $("own-distance").value = "";
  } catch (e) {
    feedback.className = "hint bad";
    feedback.textContent = e.message;
  }
}

const slug = (text) =>
  text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "my-rule";

/* ------------------------------------------------------------------ reset -- */

async function resetAll() {
  state.areaPolygon = null;
  state.areaCorner = null;
  state.geometry = "point";
  state.objectKind = "tree";
  state.count = 20;
  state.start = state.end = null;
  state.picked = [];
  state.constraints.clear();
  state.variants = [];
  state.selectedVariant = null;
  state.drawing = null;
  state.chat = [];

  $("object-kind").value = "tree";
  $("line-kind").value = "power_line";
  $("object-count").value = "20";
  $("kind-point").dataset.active = "true";
  $("kind-line").dataset.active = "false";
  $("point-options").hidden = false;
  $("line-options").hidden = true;
  $("variants").innerHTML = "";
  $("infeasible").hidden = true;
  $("chat-log").innerHTML = "";
  $("own-feedback").textContent = "";
  $("area-reset").hidden = true;
  $("area-hint").textContent = "";
  for (const id of ["plan", "findings", "zone-forbidden", "zone-allowed", "picked"]) setData(id, null);
  setExportEnabled(false);

  // Reload the area from scratch so any constraint the planner added is gone too.
  const areaId = state.areaId;
  state.areaId = null;
  await selectArea(areaId || state.areas[0].id);
  openStep(1);
}

/* ----------------------------------------------------------------- report -- */

function mapImage() {
  // The canvas is read back straight after a render, so the printed map matches
  // what the planner is looking at.
  try {
    map.triggerRepaint();
    return map.getCanvas().toDataURL("image/png");
  } catch {
    return null; // a tainted or unavailable canvas must not stop the report
  }
}

function buildReport() {
  const v = state.selectedVariant;
  const area = state.areas.find((a) => a.id === state.areaId);
  const constraints = chosenConstraints();
  const notEvaluable = v.findings.filter((f) => f.severity === "not_evaluable");
  const violations = v.findings.filter((f) => f.severity === "violation");

  const rows = constraints.map((c) => {
    const kind = c.source.kind === "curated" ? "regulation / guideline"
      : c.source.kind === "convention" ? "convention, no legal basis" : "the planner's own";
    return `<tr>
      <td>${escapeHtml(c.title)}</td>
      <td>${c.hard ? "must hold" : `preference (${importanceLabel(c.weight)})`}</td>
      <td>${escapeHtml(c.source.text)}<div class="note">${kind}${
        c.verified ? "" : " · source sentence not yet quoted"}</div></td>
    </tr>`;
  }).join("");

  const metrics = Object.entries(v.metrics)
    .map(([k, val]) => `<tr><td>${k.replace(/_/g, " ")}</td><td class="num">${val}</td></tr>`)
    .join("");

  const image = mapImage();
  $("report").innerHTML = `
    <h1>${escapeHtml(v.label)}</h1>
    <div class="meta">
      ${escapeHtml(area ? area.title : state.areaId)}${state.areaPolygon ? " (part of it)" : ""}
      · ${v.features.length} × ${escapeHtml(labelFor(state.objectKind))}
      · ${new Date().toLocaleString()}
    </div>

    <section>
      <h2>Plan</h2>
      ${image ? `<img class="map" src="${image}" alt="Map of the plan">` : ""}
      <table>${metrics}</table>
    </section>

    <section>
      <h2>Constraints applied</h2>
      ${constraints.length
        ? `<table><tr><th>Rule</th><th>Counts as</th><th>Source</th></tr>${rows}</table>`
        : '<p class="none">None.</p>'}
    </section>

    <section>
      <h2>Trade-offs</h2>
      ${v.tradeoffs.length
        ? `<ul>${v.tradeoffs.map((t) => `<li>${t.count} × ${escapeHtml(t.title)}${
            tradeoffDetail(t)} — importance: ${importanceLabel(t.weight)}</li>`).join("")}</ul>`
        : '<p class="none">None: every constraint that could be checked is met.</p>'}
    </section>

    <section>
      <h2>Not evaluated</h2>
      ${notEvaluable.length
        ? `<ul>${notEvaluable.map((f) =>
            `<li><b>${escapeHtml(f.constraint_id)}</b> — ${escapeHtml(f.message)}</li>`).join("")}</ul>`
        : '<p class="none">None: every constraint could be checked against open data.</p>'}
    </section>

    ${violations.length ? `<section><h2>Violations</h2><ul>${violations.map((f) =>
      `<li>${escapeHtml(f.feature_id)}: ${escapeHtml(f.message)}</li>`).join("")}</ul></section>` : ""}

    <footer>
      Decision support, not an approval. Every number above was computed by a
      deterministic check against open data — sources are listed with each rule, and
      anything that could not be checked is named rather than assumed to pass.
      Generated by neon-agent. Base map © swisstopo; data © Stadt Zürich / Kanton Zürich OGD.
    </footer>`;
}

function escapeHtml(text) {
  const el = document.createElement("div");
  el.textContent = text == null ? "" : String(text);
  return el.innerHTML;
}

function exportPDF() {
  buildReport();
  // Give the layout a frame before handing over to the print dialog.
  requestAnimationFrame(() => window.print());
}

/* ------------------------------------------------------------------- chat - */

function addMessage(role, text, extra = "") {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  if (extra) {
    const span = document.createElement("span");
    span.className = "tools";
    span.textContent = extra;
    el.append(span);
  }
  $("chat-log").append(el);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}

async function sendChat(event) {
  event.preventDefault();
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMessage("user", text);
  state.chat.push({ role: "user", content: text });
  try {
    const result = await post("/api/chat", {
      messages: state.chat,
      area: state.areaPolygon,
      area_id: state.areaId,
      object: state.geometry === "point"
        ? { kind: state.objectKind, geometry: "point", count: Number(state.count) }
        : { kind: state.objectKind, geometry: "line", start: state.start, end: state.end },
      constraints: chosenConstraints(),
    });
    state.chat.push({ role: "assistant", content: result.reply });
    addMessage("assistant", result.reply,
      result.tool_calls.length ? `tools: ${result.tool_calls.join(", ")}` : "");
    for (const w of result.warnings) addMessage("error", w);
    if (result.variants.length) {
      state.variants = result.variants;
      renderVariants({ variants: result.variants, infeasibility: null });
      openStep(4);
    }
    if (result.zones) {
      setData("zone-forbidden", result.zones.forbidden);
      setData("zone-allowed", result.zones.allowed);
    }
    for (const p of result.proposals) {
      addMessage("assistant",
        `Proposed constraint (not applied until you confirm): ${p.proposal.title}` +
          (p.problems.length ? `\n${p.problems.join("\n")}` : ""));
    }
  } catch (e) {
    addMessage("error", e.message);
  }
}

/* ------------------------------------------------------------------- wire - */

function wireControls() {
  for (const head of document.querySelectorAll(".step-head")) {
    head.addEventListener("click", () =>
      openStep(Number(head.closest(".step").dataset.step)));
  }
  for (const next of document.querySelectorAll(".next")) {
    next.addEventListener("click", () =>
      openStep(Math.min(5, Number(next.closest(".step").dataset.step) + 1)));
  }

  const pickKind = (isPoint) => {
    state.geometry = isPoint ? "point" : "line";
    state.objectKind = isPoint ? $("object-kind").value : $("line-kind").value;
    $("kind-point").dataset.active = String(isPoint);
    $("kind-line").dataset.active = String(!isPoint);
    $("point-options").hidden = !isPoint;
    $("line-options").hidden = isPoint;
    state.drawing = isPoint ? null : "line";
    if (!isPoint) {
      state.start = state.end = null;
      state.picked = [];
      setData("picked", null);
      $("line-endpoints").textContent = "Click the map to set the start.";
    }
    renderCatalog();
    refreshZones();
  };
  $("kind-point").addEventListener("click", () => pickKind(true));
  $("kind-line").addEventListener("click", () => pickKind(false));

  $("object-kind").addEventListener("change", (e) => {
    state.objectKind = e.target.value;
    renderCatalog();
  });
  $("line-kind").addEventListener("change", (e) => {
    state.objectKind = e.target.value;
    renderCatalog();
  });
  $("object-count").addEventListener("change", (e) => {
    state.count = e.target.value;
    refreshSummaries();
  });

  $("area-draw").addEventListener("click", () => {
    state.drawing = "area";
    state.areaCorner = null;
    $("area-hint").textContent = "Click two opposite corners on the map.";
  });
  $("area-reset").addEventListener("click", () => {
    state.areaPolygon = null;
    $("area-reset").hidden = true;
    $("area-hint").textContent = "";
    refreshZones();
    refreshSummaries();
  });

  $("generate").addEventListener("click", generate);
  $("export-geojson").addEventListener("click", exportGeoJSON);
  $("export-pdf").addEventListener("click", exportPDF);
  $("reset").addEventListener("click", resetAll);
  $("own-type").addEventListener("change", syncOwnForm);
  $("own-add").addEventListener("click", addOwnConstraint);
  for (const button of $("own-hard").querySelectorAll("button")) {
    button.addEventListener("click", () => {
      for (const b of $("own-hard").querySelectorAll("button")) {
        b.dataset.on = String(b === button);
      }
    });
  }
  $("chat-form").addEventListener("submit", sendChat);
  $("chat-toggle").addEventListener("click", () => {
    const chat = $("chat");
    const collapsed = chat.dataset.collapsed === "true";
    chat.dataset.collapsed = String(!collapsed);
    $("chat-toggle").textContent = collapsed ? "–" : "+";
  });

  map.on("click", onMapClick);
}

// The browser works in degrees, the API in metres. Conversion happens in one
// place, and only for points the planner clicked.
async function toLV95(lngLat) {
  const r = await post("/api/project", { lon: lngLat.lng, lat: lngLat.lat });
  return [r.x, r.y];
}

const pointAt = (lngLat) => ({
  type: "Feature",
  geometry: { type: "Point", coordinates: [lngLat.lng, lngLat.lat] },
  properties: {},
});

async function onMapClick(e) {
  if (!state.drawing) return;
  const xy = await toLV95(e.lngLat);

  if (state.drawing === "area") {
    if (!state.areaCorner) {
      state.areaCorner = xy;
      $("area-hint").textContent = "Now click the opposite corner.";
      setData("picked", { type: "FeatureCollection", features: [pointAt(e.lngLat)] });
      return;
    }
    const [x1, y1] = state.areaCorner;
    const [x2, y2] = xy;
    state.areaPolygon = { type: "Polygon", coordinates: [[
      [Math.min(x1, x2), Math.min(y1, y2)], [Math.max(x1, x2), Math.min(y1, y2)],
      [Math.max(x1, x2), Math.max(y1, y2)], [Math.min(x1, x2), Math.max(y1, y2)],
      [Math.min(x1, x2), Math.min(y1, y2)],
    ]] };
    state.areaCorner = null;
    state.drawing = null;
    setData("picked", null);
    $("area-reset").hidden = false;
    $("area-hint").textContent =
      `${Math.round(Math.abs(x2 - x1))} × ${Math.round(Math.abs(y2 - y1))} m selected.`;
    refreshZones();
    refreshSummaries();
    return;
  }

  if (state.drawing === "line") {
    state.picked = state.start ? [state.picked[0], e.lngLat] : [e.lngLat];
    if (!state.start) {
      state.start = xy;
      $("line-endpoints").textContent = "Start set. Click the map to set the end.";
    } else {
      state.end = xy;
      state.drawing = null;
      $("line-endpoints").textContent = "Start and end set.";
    }
    setData("picked", { type: "FeatureCollection", features: state.picked.map(pointAt) });
    refreshSummaries();
  }
}

/* ------------------------------------------------------------------- boot - */

map.on("load", async () => {
  addOverlaySources();
  wireControls();
  try {
    const listing = await api("/api/areas");
    state.areas = listing.areas;
    $("api-state").className = "pill ok";
    $("api-state").textContent = `API ok · ${listing.areas.length} areas`;
    renderAreas();
    await loadConstraintForm();
    await selectArea(listing.default_area_id);

    const chat = await api("/api/chat/status");
    $("chat-state").textContent = chat.available ? chat.model : "no API key";
    $("chat-state").className = `pill small ${chat.available ? "ok" : "bad"}`;
    if (!chat.available) {
      addMessage("error", "Chat is off: the server has no ANTHROPIC_API_KEY. Everything " +
        "else — layers, constraints, generation, checks, export — still works.");
    }
  } catch (e) {
    $("api-state").className = "pill bad";
    $("api-state").textContent = `API unreachable: ${e.message}`;
  }
  openStep(1);
});
