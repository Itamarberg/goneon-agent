/* neon-agent — the planner's page.
 *
 * All planning logic lives in the API. This file keeps the plan state, draws it,
 * and calls six endpoints. It holds no thresholds and computes no verdicts: a
 * number shown here came back from a tool (ADR 0001).
 *
 * There is no server session, so this state *is* the plan (docs/PLAN.md §7).
 */

const API = window.NEON_API_BASE;

const state = {
  area: null, // null = the whole study area
  areaPolygon: null, // LV95 polygon when the planner picked one
  geometry: "point", // point | line
  objectKind: "tree",
  count: 20,
  start: null,
  end: null,
  picked: [], // the map clicks behind start/end, kept for redrawing
  constraints: new Map(), // id -> {constraint, hard}
  catalog: [],
  variants: [],
  selectedVariant: null,
  drawing: null, // "area" | "line" | null
  chat: [],
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
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/* ------------------------------------------------------------------ map --- */

const LAYER_STYLE = {
  building: "#8d99ae", sidewalk: "#c9ada7", road: "#adb5bd", green_space: "#52b788",
  water: "#4cc9f0", tree: "#2d6a4f", school: "#e76f51", kindergarten: "#f4a261",
  hydrant: "#e63946", transit_stop: "#7209b7", power_line_hv: "#ffb703",
};
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
      { id: "background", type: "background", paint: { "background-color": "#f6f5f3" } },
      { id: "basemap", type: "raster", source: "swisstopo", paint: { "raster-opacity": 0.45 } },
    ],
  },
  center: [8.52, 47.39],
  zoom: 14,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }));

const EMPTY = { type: "FeatureCollection", features: [] };
const setData = (id, data) => map.getSource(id) && map.getSource(id).setData(data || EMPTY);

function addOverlaySources() {
  // Drawn on top of the data layers: what the constraints forbid, what they
  // leave, the chosen plan, and anything a check flagged.
  for (const id of ["zone-forbidden", "zone-allowed", "plan", "findings", "picked"]) {
    map.addSource(id, { type: "geojson", data: EMPTY });
  }
  map.addLayer({
    id: "zone-allowed-fill", type: "fill", source: "zone-allowed",
    paint: { "fill-color": "#1f9d55", "fill-opacity": 0.14 },
  });
  map.addLayer({
    id: "zone-forbidden-fill", type: "fill", source: "zone-forbidden",
    paint: { "fill-color": "#c0392b", "fill-opacity": 0.18 },
  });
  map.addLayer({
    id: "plan-line", type: "line", source: "plan",
    paint: { "line-color": "#1f6feb", "line-width": 4 },
  });
  map.addLayer({
    id: "plan-point", type: "circle", source: "plan",
    filter: ["==", ["geometry-type"], "Point"],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 4, 18, 8],
      "circle-color": "#1f6feb", "circle-stroke-width": 2, "circle-stroke-color": "#fff",
    },
  });
  map.addLayer({
    id: "findings-line", type: "line", source: "findings",
    paint: { "line-color": "#c0392b", "line-width": 2, "line-dasharray": [2, 1] },
  });
  map.addLayer({
    id: "picked-point", type: "circle", source: "picked",
    paint: { "circle-radius": 6, "circle-color": "#ffb703", "circle-stroke-width": 2,
             "circle-stroke-color": "#fff" },
  });
}

function addDataLayer(info) {
  const colour = LAYER_STYLE[info.name] || "#888";
  const visible = LAYERS_ON.includes(info.name);
  const vis = { visibility: visible ? "visible" : "none" };
  map.addSource(info.name, { type: "geojson", data: `${API}/api/layers/${info.name}` });

  if (info.geometry_type === "Polygon") {
    map.addLayer({ id: `${info.name}-fill`, type: "fill", source: info.name, layout: vis,
      paint: { "fill-color": colour, "fill-opacity": 0.4 } }, "zone-allowed-fill");
  } else if (info.geometry_type === "LineString") {
    map.addLayer({ id: `${info.name}-line`, type: "line", source: info.name, layout: vis,
      paint: { "line-color": colour, "line-width": 3 } }, "zone-allowed-fill");
  } else {
    map.addLayer({ id: `${info.name}-point`, type: "circle", source: info.name, layout: vis,
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 2.5, 18, 5.5],
               "circle-color": colour, "circle-stroke-width": 1,
               "circle-stroke-color": "rgba(255,255,255,.75)" } }, "zone-allowed-fill");
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

/* ------------------------------------------------------------- constraints - */

function renderCatalog() {
  const host = $("catalog");
  host.innerHTML = "";
  const relevant = state.catalog.filter(
    (c) => !c.applies_to || c.applies_to === state.objectKind,
  );

  for (const c of relevant) {
    const chosen = state.constraints.get(c.id);
    const el = document.createElement("div");
    el.className = "rule";
    el.dataset.on = String(Boolean(chosen));

    const kind = c.source.kind;
    const badge = !c.evaluable
      ? `<span class="badge blocked" title="${c.not_evaluable_reason}">cannot be checked</span>`
      : `<span class="badge ${kind}">${kind}</span>`;
    const hard = chosen ? chosen.hard : c.hard;

    el.innerHTML = `
      <div class="rule-top">
        <input type="checkbox" ${chosen ? "checked" : ""}>
        <div style="flex:1">
          <div class="rule-title">${c.title}</div>
          <div class="rule-desc">${c.description}</div>
          <div class="rule-meta">
            ${badge}
            <button class="hardness">${hard ? "must hold" : "preference"}</button>
            ${c.verified ? "" : '<span class="badge" title="The source sentence has not been quoted yet">draft</span>'}
          </div>
          <div class="source">${c.source.url ? `<a href="${c.source.url}" target="_blank" rel="noopener">${c.source.text}</a>` : c.source.text}</div>
          ${c.note ? `<div class="source">${c.note}</div>` : ""}
          ${!c.evaluable ? `<div class="blocked-note">${c.not_evaluable_reason}</div>` : ""}
        </div>
      </div>`;

    el.querySelector("input").addEventListener("change", (e) => {
      if (e.target.checked) state.constraints.set(c.id, { constraint: c, hard });
      else state.constraints.delete(c.id);
      renderCatalog();
      refreshZones();
    });
    // Hard vs soft is the planner's call, not the catalog's: the same rule is a
    // requirement in one project and a preference in another.
    el.querySelector(".hardness").addEventListener("click", () => {
      const entry = state.constraints.get(c.id);
      if (!entry) return;
      entry.hard = !entry.hard;
      renderCatalog();
      refreshZones();
    });
    host.append(el);
  }
  markSteps();
}

function chosenConstraints() {
  return [...state.constraints.values()].map(({ constraint, hard }) => {
    const { description, evaluable, not_evaluable_reason, ...rest } = constraint;
    return { ...rest, hard };
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
    const zones = await post("/api/zones", { area: state.areaPolygon, constraints });
    if (ticket !== zoneRequest) return; // a newer tick won
    setData("zone-forbidden", zones.forbidden);
    setData("zone-allowed", zones.allowed);
    const share = Math.round((zones.allowed_area_m2 / zones.area_m2) * 100);
    const skipped = Object.entries(zones.skipped || {});
    summary.innerHTML = `
      <div class="bar"><i style="width:${share}%"></i></div>
      <b>${zones.allowed_area_m2.toLocaleString()} m²</b> available — ${share}% of the area.
      ${skipped.length ? `<div class="hint">Not shown as a zone: ${skipped
        .map(([id, why]) => `${id} (${why})`).join("; ")}</div>` : ""}`;
  } catch (e) {
    if (ticket === zoneRequest) summary.textContent = `Could not compute zones: ${e.message}`;
  }
}

/* --------------------------------------------------------------- generate - */

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
      area: state.areaPolygon,
      object: spec,
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
    return;
  }
  $("infeasible").hidden = true;

  for (const v of result.variants) {
    const el = document.createElement("div");
    el.className = "variant";
    const metrics = v.metrics.length_m
      ? `${Math.round(v.metrics.length_m)} m · ${v.metrics.vertices} points`
      : `${v.features.length} objects`;
    const blocked = v.findings.filter((f) => f.severity === "not_evaluable");

    el.innerHTML = `
      <div class="variant-top">
        <span class="variant-label">${v.label}</span>
        <span class="variant-metrics">${metrics}</span>
      </div>
      ${v.tradeoffs.length
        ? v.tradeoffs.map((t) => `<div class="tradeoff">${t.count} × ${t.title}${
            t.worst_measured_m != null ? ` — worst ${t.worst_measured_m} m (asked ${t.required_m} m)` : ""
          }</div>`).join("")
        : '<div class="clean">Meets every constraint that could be checked.</div>'}
      ${blocked.map((f) => `<div class="blocked-note">${f.message}</div>`).join("")}`;

    el.addEventListener("click", () => selectVariant(v.id));
    host.append(el);
  }
  if (result.variants.length) selectVariant(result.variants[0].id);
  markSteps();
}

function selectVariant(id) {
  const variant = state.variants.find((v) => v.id === id);
  if (!variant) return;
  state.selectedVariant = variant;

  for (const el of document.querySelectorAll(".variant")) {
    el.dataset.active = String(el.querySelector(".variant-label").textContent === variant.label);
  }
  setData("plan", {
    type: "FeatureCollection",
    features: variant.features.map((f) => ({
      type: "Feature", id: f.id, geometry: f.geometry, properties: { kind: f.kind },
    })),
  });
  setData("findings", {
    type: "FeatureCollection",
    features: variant.findings
      .filter((f) => f.geometry)
      .map((f) => ({ type: "Feature", geometry: f.geometry, properties: { message: f.message } })),
  });
  setExportEnabled(true);
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
  box.innerHTML = `
    <h4>No plan is possible here</h4>
    <p>${report.reason}</p>
    ${items.length ? `<ul>${items.join("")}</ul>` : ""}`;
  $("variants").innerHTML = "";
}

/* ----------------------------------------------------------------- export - */

function setExportEnabled(on) {
  $("export-geojson").disabled = !on;
  $("export-report").disabled = !on;
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
  download(
    `neon-plan-${v.id}.geojson`,
    JSON.stringify(
      {
        type: "FeatureCollection",
        properties: {
          variant: v.id,
          label: v.label,
          metrics: v.metrics,
          constraints: chosenConstraints(),
          findings: v.findings,
          generated_by: "neon-agent",
        },
        features: v.features.map((f) => ({
          type: "Feature", id: f.id, geometry: f.geometry, properties: f.properties,
        })),
      },
      null,
      2,
    ),
    "application/geo+json",
  );
}

function exportReport() {
  const v = state.selectedVariant;
  const rules = chosenConstraints()
    .map((c) => `- ${c.title} (${c.hard ? "must hold" : "preference"}) — source: ${c.source.text}`)
    .join("\n");
  const notEvaluable = v.findings
    .filter((f) => f.severity === "not_evaluable")
    .map((f) => `- ${f.constraint_id}: ${f.message}`)
    .join("\n");
  const tradeoffs = v.tradeoffs
    .map((t) => `- ${t.count} × ${t.title} (worst ${t.worst_measured_m} m, asked ${t.required_m} m)`)
    .join("\n");

  download(
    `neon-report-${v.id}.md`,
    `# Plan report — ${v.label}

Area: ${state.areaPolygon ? "planner-selected area" : "Zürich Kreis 5 (study area)"}
Object: ${state.objectKind}
Generated: ${new Date().toISOString()}

## Constraints applied
${rules || "(none)"}

## Result
${v.features.length} object(s). Metrics: ${JSON.stringify(v.metrics)}

## Trade-offs
${tradeoffs || "None: every constraint that could be checked is met."}

## Not evaluated
${notEvaluable || "None."}

This is decision support, not an approval. Every number above came from a
deterministic check against open data; the sources are listed with each rule.
`,
    "text/markdown",
  );
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
      object: state.geometry === "point"
        ? { kind: state.objectKind, geometry: "point", count: Number(state.count) }
        : { kind: state.objectKind, geometry: "line", start: state.start, end: state.end },
      constraints: chosenConstraints(),
    });
    state.chat.push({ role: "assistant", content: result.reply });
    addMessage(
      "assistant",
      result.reply,
      result.tool_calls.length ? `tools: ${result.tool_calls.join(", ")}` : "",
    );
    for (const w of result.warnings) addMessage("error", w);

    // Anything the agent generated goes on the map like a manual generation.
    if (result.variants.length) {
      state.variants = result.variants;
      renderVariants({ variants: result.variants, infeasibility: null });
    }
    if (result.zones) {
      setData("zone-forbidden", result.zones.forbidden);
      setData("zone-allowed", result.zones.allowed);
    }
    for (const p of result.proposals) {
      addMessage(
        "assistant",
        `Proposed constraint (not applied until you confirm): ${p.proposal.title}` +
          (p.problems.length ? `\n${p.problems.join("\n")}` : ""),
      );
    }
  } catch (e) {
    addMessage("error", e.message);
  }
}

/* ------------------------------------------------------------------- wire - */

function markSteps() {
  const done = {
    1: true,
    2: state.geometry === "point" ? Boolean(state.count) : Boolean(state.start && state.end),
    3: state.constraints.size > 0,
    4: state.variants.length > 0,
    5: Boolean(state.selectedVariant),
  };
  for (const [step, isDone] of Object.entries(done)) {
    document.querySelector(`.step[data-step="${step}"]`).dataset.done = String(isDone);
  }
  $("generate").disabled = state.geometry === "line" && !(state.start && state.end);
}

function wireControls() {
  const pick = (a, b, value) => {
    a.dataset.active = String(value);
    b.dataset.active = String(!value);
  };

  $("kind-point").addEventListener("click", () => {
    state.geometry = "point";
    state.objectKind = $("object-kind").value;
    pick($("kind-point"), $("kind-line"), true);
    $("point-options").hidden = false;
    $("line-options").hidden = true;
    state.drawing = null;
    renderCatalog();
    refreshZones();
  });
  $("kind-line").addEventListener("click", () => {
    state.geometry = "line";
    state.objectKind = $("line-kind").value;
    pick($("kind-point"), $("kind-line"), false);
    $("point-options").hidden = true;
    $("line-options").hidden = false;
    state.drawing = "line";
    state.start = state.end = null;
    state.picked = [];
    setData("picked", null);
    $("line-endpoints").textContent = "Click the map to set the start.";
    renderCatalog();
    refreshZones();
  });

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
    markSteps();
  });

  $("area-whole").addEventListener("click", () => {
    state.areaPolygon = null;
    state.drawing = null;
    pick($("area-whole"), $("area-draw"), true);
    $("area-hint").textContent = "Zürich Kreis 5.";
    refreshZones();
  });
  $("area-draw").addEventListener("click", () => {
    state.drawing = "area";
    state.areaCorner = null;
    pick($("area-whole"), $("area-draw"), false);
    $("area-hint").textContent = "Click two opposite corners on the map.";
  });

  $("generate").addEventListener("click", generate);
  $("export-geojson").addEventListener("click", exportGeoJSON);
  $("export-report").addEventListener("click", exportReport);
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
    state.areaPolygon = {
      type: "Polygon",
      coordinates: [[
        [Math.min(x1, x2), Math.min(y1, y2)], [Math.max(x1, x2), Math.min(y1, y2)],
        [Math.max(x1, x2), Math.max(y1, y2)], [Math.min(x1, x2), Math.max(y1, y2)],
        [Math.min(x1, x2), Math.min(y1, y2)],
      ]],
    };
    state.areaCorner = null;
    state.drawing = null;
    setData("picked", null);
    const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    $("area-hint").textContent = `Area of ${Math.round(w)} × ${Math.round(h)} m selected.`;
    refreshZones();
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
    setData("picked", {
      type: "FeatureCollection",
      features: state.picked.map(pointAt),
    });
    markSteps();
  }
}

const pointAt = (lngLat) => ({
  type: "Feature",
  geometry: { type: "Point", coordinates: [lngLat.lng, lngLat.lat] },
  properties: {},
});

/* ------------------------------------------------------------------- boot - */

map.on("load", async () => {
  addOverlaySources();
  try {
    const area = await api("/api/area");
    $("api-state").className = "pill ok";
    $("api-state").textContent = `API ok · ${area.name}`;
    $("area-desc").textContent = area.description;

    const ring = area.polygon_wgs84.coordinates[0];
    map.fitBounds(
      ring.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(ring[0], ring[0])),
      { padding: 24, duration: 0 },
    );
    map.addSource("study-area", { type: "geojson", data: area.polygon_wgs84 });
    map.addLayer({
      id: "study-area-line", type: "line", source: "study-area",
      paint: { "line-color": "#1f6feb", "line-width": 1.5, "line-dasharray": [3, 2] },
    });

    for (const info of area.layers) addDataLayer(info);
    $("area-counts").innerHTML = area.layers
      .map((l) => `<span class="count-chip"><b>${l.feature_count}</b> ${l.title.toLowerCase()}</span>`)
      .join("");
    $("gaps").innerHTML = area.unavailable_layers
      .map((g) => `<li><b>${g.name}</b> — ${g.reason}</li>`).join("");

    const catalog = await api("/api/catalog");
    state.catalog = catalog.constraints;
    renderCatalog();

    const chat = await api("/api/chat/status");
    $("chat-state").textContent = chat.available ? chat.model : "no API key";
    $("chat-state").className = `pill small ${chat.available ? "ok" : "bad"}`;
    if (!chat.available) {
      addMessage("error", "Chat is off: the server has no ANTHROPIC_API_KEY. " +
        "Everything else — layers, constraints, generation, checks, export — still works.");
    }
  } catch (e) {
    $("api-state").className = "pill bad";
    $("api-state").textContent = `API unreachable: ${e.message}`;
  }
  wireControls();
  markSteps();
});
