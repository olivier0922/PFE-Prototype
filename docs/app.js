const FONT = "Inter, 'Segoe UI', system-ui, sans-serif";
const INK = "#1b2733";
const GRID = "#e3e8ee";
const TERRAIN_FILL = "#8c6a4a";
const TERRAIN_LINE = "#4b3621";
const LAPSE = 0.0065;

const VARIABLES = {
  temperature: { label: "Température", unit: "°C", colorscale: "RdBu_r", zmin: -24, zmax: 8, zmid: 0 },
  dewpoint: { label: "Point de rosée", unit: "°C", colorscale: "RdBu_r", zmin: -30, zmax: 6, zmid: 0 },
  humidity: { label: "Humidité relative", unit: "%", colorscale: "YlGnBu", zmin: 20, zmax: 100 },
  wind: { label: "Vent", unit: "km/h", colorscale: "Turbo", zmin: 0, zmax: 120, factor: 3.6 },
};

const CATEGORY_COLORS = {
  Faible: "#4f9d69",
  Modéré: "#e0b53a",
  Élevé: "#e07b39",
  Critique: "#b83232",
  Indisponible: "#9aa5b1",
};

const PRECIPITATION_TYPES = ["Sec", "Neige", "Neige mouillée", "Grésil", "Verglas", "Pluie"];
const PRECIPITATION_COLORS = {
  Sec: "#e6ebf0",
  Neige: "#a9d3f5",
  "Neige mouillée": "#4c8fd6",
  Grésil: "#8f6bd9",
  Verglas: "#d63b3b",
  Pluie: "#3f9c62",
};

const HAZARD_LABELS = ["Aucun aléa notable", "Pluie verglaçante", "Givrage / neige collante", "Vent fort", "Froid extrême"];

const RISK_COLORSCALE = [
  [0, CATEGORY_COLORS.Faible],
  [0.25, CATEGORY_COLORS.Faible],
  [0.25, CATEGORY_COLORS["Modéré"]],
  [0.5, CATEGORY_COLORS["Modéré"]],
  [0.5, CATEGORY_COLORS["Élevé"]],
  [0.75, CATEGORY_COLORS["Élevé"]],
  [0.75, CATEGORY_COLORS.Critique],
  [1, CATEGORY_COLORS.Critique],
];

const PRECIPITATION_COLORSCALE = PRECIPITATION_TYPES.flatMap((name, index) => [
  [index / PRECIPITATION_TYPES.length, PRECIPITATION_COLORS[name]],
  [(index + 1) / PRECIPITATION_TYPES.length, PRECIPITATION_COLORS[name]],
]);

const state = {
  network: null,
  corridor: null,
  corridorId: null,
  time: 0,
  variable: "temperature",
  cap: 6,
  basemap: "online",
  selection: null,
  playing: false,
  timer: null,
};

function b64bytes(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function unpack(value, kind) {
  const bytes = b64bytes(value);
  if (kind === "f32") return Array.from(new Float32Array(bytes.buffer));
  if (kind === "f16") return Array.from(new Float32Array(new Float16Array(bytes.buffer)));
  if (kind === "i8") return Array.from(new Int8Array(bytes.buffer));
  return Array.from(new Uint8Array(bytes.buffer));
}

function at2(flat, time, point, nPoint) {
  return flat[time * nPoint + point];
}

function column(flat, time, level, nLevel, nPoint) {
  const start = (time * nLevel + level) * nPoint;
  return flat.slice(start, start + nPoint);
}

function category(score) {
  if (!Number.isFinite(score)) return "Indisponible";
  if (score < 25) return "Faible";
  if (score < 50) return "Modéré";
  if (score < 75) return "Élevé";
  return "Critique";
}

function hazardLabel(code) {
  return HAZARD_LABELS[code + 1] || HAZARD_LABELS[0];
}

function finite(values) {
  return values.filter((value) => Number.isFinite(value));
}

function rangeText(values, unit, digits) {
  const ok = finite(values);
  if (!ok.length) return "n/d";
  const lo = Math.min(...ok);
  const hi = Math.max(...ok);
  if (Math.abs(hi - lo) < 10 ** -digits) return `${hi.toFixed(digits)} ${unit}`;
  return `${lo.toFixed(digits)} à ${hi.toFixed(digits)} ${unit}`;
}

function ptp(values) {
  const ok = finite(values);
  return ok.length ? Math.max(...ok) - Math.min(...ok) : 0;
}

function mean(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function style(layout, height) {
  return Object.assign(layout, {
    height,
    margin: layout.margin || { l: 56, r: 24, t: 30, b: 44 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: layout.plot_bgcolor || "#ffffff",
    font: { family: FONT, color: INK, size: 12 },
    hoverlabel: { font: { family: FONT, size: 12 } },
    hovermode: "closest",
  });
}

function dewpoint(temperature, humidity) {
  const a = 17.625;
  const b = 243.04;
  const h = Math.min(100, Math.max(0.5, humidity));
  const gamma = Math.log(h / 100) + (a * temperature) / (b + temperature);
  return (b * gamma) / (a - gamma);
}

function interpColumn(heights, values, target, mode) {
  const n = heights.length;
  if (target > heights[n - 1]) return NaN;
  if (target < heights[0]) {
    return mode === "lapse" ? values[0] + LAPSE * (heights[0] - target) : values[0];
  }
  let below = 0;
  for (let i = 0; i < n; i += 1) if (heights[i] < target) below += 1;
  const upper = Math.min(n - 1, Math.max(1, below));
  const lower = upper - 1;
  const span = heights[upper] - heights[lower];
  const weight = span === 0 ? 0 : (target - heights[lower]) / span;
  return values[lower] + weight * (values[upper] - values[lower]);
}

function sliceLevels(corridor, time) {
  const nLevel = corridor.levels.length;
  const nPoint = corridor.n;
  const byPoint = [];
  for (let point = 0; point < nPoint; point += 1) {
    const height = [];
    const temperature = [];
    const humidity = [];
    const u = [];
    const v = [];
    for (let level = 0; level < nLevel; level += 1) {
      const offset = (time * nLevel + level) * nPoint + point;
      height.push(corridor.height[offset]);
      temperature.push(corridor.temperature[offset]);
      humidity.push(corridor.humidity[offset]);
      u.push(corridor.u[offset]);
      v.push(corridor.v[offset]);
    }
    byPoint.push({ height, temperature, humidity, u, v });
  }
  return byPoint;
}

function grids(corridor, time, capKm) {
  const columns = sliceLevels(corridor, time);
  const step = 100;
  const top = capKm * 1000;
  const altitude = [];
  for (let z = 0; z <= top + 0.1; z += step) altitude.push(z);
  const nZ = altitude.length;
  const nPoint = corridor.n;
  const blank = () => Array.from({ length: nZ }, () => new Array(nPoint).fill(NaN));
  const temperature = blank();
  const dew = blank();
  const humidity = blank();
  const wind = blank();
  const pressure = blank();
  for (let point = 0; point < nPoint; point += 1) {
    const col = columns[point];
    const terrain = corridor.terrain[point];
    for (let z = 0; z < nZ; z += 1) {
      if (altitude[z] < terrain) continue;
      const t = interpColumn(col.height, col.temperature, altitude[z], "lapse");
      const rh = interpColumn(col.height, col.humidity, altitude[z], "hold");
      const u = interpColumn(col.height, col.u, altitude[z], "hold");
      const v = interpColumn(col.height, col.v, altitude[z], "hold");
      const p = interpColumn(col.height, corridor.levels, altitude[z], "hold");
      temperature[z][point] = t;
      humidity[z][point] = rh;
      dew[z][point] = dewpoint(t, rh);
      wind[z][point] = Math.hypot(u, v);
      pressure[z][point] = p;
    }
  }
  return { altitude, temperature, dewpoint: dew, humidity, wind, pressure, columns };
}

function profileColumn(corridor, time, point, capKm) {
  const columns = sliceLevels(corridor, time)[point];
  const terrain = corridor.terrain[point];
  const altitude = [];
  const temperature = [];
  const dew = [];
  const wind = [];
  for (let z = 0; z <= capKm * 1000 + 0.1; z += 100) {
    altitude.push(z / 1000);
    if (z < terrain) {
      temperature.push(NaN);
      dew.push(NaN);
      wind.push(NaN);
      continue;
    }
    const t = interpColumn(columns.height, columns.temperature, z, "lapse");
    const rh = interpColumn(columns.height, columns.humidity, z, "hold");
    temperature.push(t);
    dew.push(dewpoint(t, rh));
    const u = interpColumn(columns.height, columns.u, z, "hold");
    const v = interpColumn(columns.height, columns.v, z, "hold");
    wind.push(Math.hypot(u, v) * 3.6);
  }
  return { altitude, temperature, dewpoint: dew, wind, columns };
}

function peakByPoint(corridor) {
  const peaks = new Array(corridor.n).fill(-1);
  const when = new Array(corridor.n).fill(0);
  for (let point = 0; point < corridor.n; point += 1) {
    for (let time = 0; time < state.network.nTime; time += 1) {
      const score = at2(corridor.score, time, point, corridor.n);
      const value = Number.isFinite(score) ? score : -1;
      if (value > peaks[point]) {
        peaks[point] = value;
        when[point] = time;
      }
    }
  }
  return { peaks, when };
}

function resolvePoint(corridor, time) {
  const selection = state.selection;
  if (selection && selection.corridor === corridor.id) {
    if (selection.km != null) {
      let best = 0;
      let gap = Infinity;
      for (let i = 0; i < corridor.n; i += 1) {
        const delta = Math.abs(corridor.distance[i] - selection.km);
        if (delta < gap) {
          gap = delta;
          best = i;
        }
      }
      return { index: best, hint: `Point choisi sur la coupe · km ${corridor.distance[best].toFixed(1)}` };
    }
    if (selection.lon != null) {
      let best = 0;
      let gap = Infinity;
      for (let i = 0; i < corridor.n; i += 1) {
        const delta = Math.hypot(corridor.lon[i] - selection.lon, corridor.lat[i] - selection.lat);
        if (delta < gap) {
          gap = delta;
          best = i;
        }
      }
      return { index: best, hint: `Point choisi sur la carte · km ${corridor.distance[best].toFixed(1)}` };
    }
  }
  let best = 0;
  let score = -1;
  for (let i = 0; i < corridor.n; i += 1) {
    const value = at2(corridor.score, time, i, corridor.n);
    const filled = Number.isFinite(value) ? value : -1;
    if (filled > score) {
      score = filled;
      best = i;
    }
  }
  return { index: best, hint: `Point le plus exposé à cette heure · km ${corridor.distance[best].toFixed(1)}` };
}

function baseLayout(extra) {
  const layout = style(extra, extra.height);
  for (const key of Object.keys(layout)) {
    if (key.startsWith("xaxis") || key.startsWith("yaxis")) {
      layout[key] = Object.assign({ gridcolor: GRID, zeroline: false, linecolor: GRID }, layout[key]);
    }
  }
  return layout;
}

function drawCrossSection(corridor, time, point) {
  const cap = state.cap;
  const meta = VARIABLES[state.variable];
  const factor = meta.factor || 1;
  const grid = grids(corridor, time, cap);
  const altitudeKm = grid.altitude.map((z) => z / 1000);
  const values = grid[state.variable].map((row) => row.map((value) => value * factor));
  const distance = corridor.distance;
  const precip = [];
  for (let i = 0; i < corridor.n; i += 1) precip.push(at2(corridor.precip, time, i, corridor.n));
  const score = [];
  for (let i = 0; i < corridor.n; i += 1) score.push(at2(corridor.score, time, i, corridor.n));
  const peaks = peakByPoint(corridor).peaks.map((value) => (value < 0 ? NaN : value));
  const hoverRisk = distance.map((km, i) => {
    const value = score[i];
    return `<b>km ${km.toFixed(1)} · ${category(value)}</b><br>Indice: ${(Number.isFinite(value) ? value : 0).toFixed(0)}/100 · ${hazardLabel(at2(corridor.dominant, time, i, corridor.n))}<br>Près du sol: ${at2(corridor.nearT, time, i, corridor.n).toFixed(1)} °C, HR ${at2(corridor.nearRh, time, i, corridor.n).toFixed(0)} %, vent ${(at2(corridor.nearWind, time, i, corridor.n) * 3.6).toFixed(0)} km/h<br>Couche la plus chaude 0,3–3 km: ${at2(corridor.warm, time, i, corridor.n).toFixed(1)} °C<br>Type: ${PRECIPITATION_TYPES[precip[i]]}`;
  });
  const traces = [
    {
      type: "heatmap",
      x: distance,
      y: altitudeKm,
      z: values,
      customdata: grid.pressure,
      colorscale: meta.colorscale,
      zmin: meta.zmin,
      zmax: meta.zmax,
      zmid: meta.zmid,
      colorbar: { title: { text: `${meta.label}<br>(${meta.unit})`, side: "right" }, thickness: 14, len: 0.62, y: 1, yanchor: "top", x: 1.005 },
      hovertemplate: `km %{x:.1f} · %{y:.2f} km d'altitude<br>${meta.label}: %{z:.1f} ${meta.unit}<br>Pression ≈ %{customdata:.0f} hPa<extra></extra>`,
      connectgaps: false,
      hoverongaps: false,
      name: meta.label,
      xaxis: "x",
      yaxis: "y",
    },
    {
      type: "contour",
      x: distance,
      y: altitudeKm,
      z: grid.temperature,
      contours: { start: 0, end: 0, size: 1, coloring: "lines", showlabels: false },
      line: { color: "#111827", width: 2.2, dash: "dot" },
      showscale: false,
      hoverinfo: "skip",
      name: "Isotherme 0 °C",
      showlegend: true,
      connectgaps: false,
      xaxis: "x",
      yaxis: "y",
    },
  ];
  const annotations = [];
  corridor.levels.forEach((level, levelIndex) => {
    const heights = column(corridor.height, time, levelIndex, corridor.levels.length, corridor.n).map((value) => value / 1000);
    const mid = heights.slice().sort((a, b) => a - b)[Math.floor(heights.length / 2)];
    if (mid > cap) return;
    traces.push({
      type: "scatter",
      x: distance,
      y: heights,
      mode: "lines",
      line: { color: "rgba(17,24,39,0.45)", width: 1, dash: "dash" },
      hovertemplate: `Niveau ERA5 ${level.toFixed(0)} hPa · %{y:.2f} km<extra></extra>`,
      showlegend: false,
      xaxis: "x",
      yaxis: "y",
    });
    annotations.push({
      x: distance[distance.length - 1],
      y: heights[heights.length - 1],
      text: `${level.toFixed(0)} hPa`,
      showarrow: false,
      xanchor: "right",
      yanchor: "bottom",
      font: { size: 10, color: "rgba(17,24,39,0.7)" },
      bgcolor: "rgba(255,255,255,0.6)",
      xref: "x",
      yref: "y",
    });
  });
  traces.push(
    {
      type: "scatter",
      x: distance,
      y: corridor.terrain.map((value) => value / 1000),
      mode: "lines",
      line: { color: TERRAIN_LINE, width: 1.5 },
      fill: "tozeroy",
      fillcolor: TERRAIN_FILL,
      name: "Relief (ETOPO)",
      hovertemplate: "km %{x:.1f} · relief %{y:.2f} km<extra></extra>",
      xaxis: "x",
      yaxis: "y",
    },
    {
      type: "heatmap",
      x: distance,
      y: [0],
      z: [precip],
      zmin: 0,
      zmax: PRECIPITATION_TYPES.length,
      colorscale: PRECIPITATION_COLORSCALE,
      showscale: false,
      hovertext: [distance.map((km, i) => `km ${km.toFixed(1)}: ${PRECIPITATION_TYPES[precip[i]]}`)],
      hoverinfo: "text",
      name: "Type de précipitation",
      xaxis: "x2",
      yaxis: "y2",
    },
    {
      type: "scatter",
      x: distance,
      y: peaks,
      mode: "lines",
      line: { color: "rgba(17,24,39,0.35)", width: 1.5, dash: "dot" },
      name: "Maximum sur 48 h",
      hovertemplate: "km %{x:.1f} · max 48 h: %{y:.0f}<extra></extra>",
      xaxis: "x3",
      yaxis: "y3",
    },
    {
      type: "scatter",
      x: distance,
      y: score,
      mode: "lines+markers",
      line: { color: "#111827", width: 2 },
      marker: {
        size: 7,
        color: score.map((value) => (Number.isFinite(value) ? value : 0)),
        cmin: 0,
        cmax: 100,
        colorscale: RISK_COLORSCALE,
        line: { color: "#ffffff", width: 1 },
      },
      fill: "tozeroy",
      fillcolor: "rgba(17,24,39,0.06)",
      name: "Indice à cette heure",
      hovertext: hoverRisk,
      hoverinfo: "text",
      xaxis: "x3",
      yaxis: "y3",
    },
  );
  const shapes = [
    ["Faible", 0, 25],
    ["Modéré", 25, 50],
    ["Élevé", 50, 75],
    ["Critique", 75, 100],
  ].map(([name, y0, y1]) => ({
    type: "rect",
    xref: "x3 domain",
    yref: "y3",
    x0: 0,
    x1: 1,
    y0,
    y1,
    fillcolor: CATEGORY_COLORS[name],
    opacity: 0.1,
    line: { width: 0 },
  }));
  shapes.push({
    type: "line",
    xref: "x",
    yref: "paper",
    x0: distance[point],
    x1: distance[point],
    y0: 0,
    y1: 1,
    line: { color: "#111827", width: 1.5 },
    opacity: 0.8,
  });
  const layout = baseLayout({
    height: 640,
    margin: { l: 56, r: 90, t: 36, b: 48 },
    annotations,
    shapes,
    legend: { orientation: "h", y: 1.04, x: 0, yanchor: "bottom", font: { size: 11 } },
    clickmode: "event",
    xaxis: { domain: [0, 1], anchor: "y", showticklabels: false, range: [distance[0], distance[distance.length - 1]] },
    yaxis: { domain: [0.4048, 1], anchor: "x", title: { text: "Altitude (km)" }, range: [0, cap] },
    xaxis2: { domain: [0, 1], anchor: "y2", matches: "x", showticklabels: false },
    yaxis2: { domain: [0.2861, 0.3698], anchor: "x2", title: { text: "Précip." }, showticklabels: false },
    xaxis3: { domain: [0, 1], anchor: "y3", matches: "x", title: { text: "Distance le long du corridor (km) — A → B" } },
    yaxis3: { domain: [0, 0.2511], anchor: "x3", title: { text: "Indice" }, range: [0, 102], dtick: 25 },
  });
  Plotly.react("cross-section", traces, layout, { displaylogo: false, displayModeBar: "hover", responsive: true });
}

function pathArrays(encoded) {
  return { lon: unpack(encoded[0], "f32"), lat: unpack(encoded[1], "f32") };
}

function geo(basemap, lon, lat, options) {
  if (basemap) return Object.assign({ type: "scattermap", lon, lat }, options);
  const marker = options.marker && options.marker.symbol ? Object.assign({}, options.marker) : options.marker;
  if (marker && marker.symbol) delete marker.symbol;
  return Object.assign({}, options, { type: "scatter", x: lon, y: lat, marker });
}

function drawMap(corridor, time, point) {
  const network = state.network;
  const basemap = state.basemap === "online";
  const traces = [];
  const scores = [];
  for (let i = 0; i < network.corridors.length; i += 1) scores.push(at2(network.score, time, i, network.corridors.length));
  for (const name of ["Indisponible", "Faible", "Modéré", "Élevé", "Critique"]) {
    const lon = [];
    const lat = [];
    const text = [];
    const ids = [];
    network.corridors.forEach((item, index) => {
      if (category(scores[index]) !== name || item.id === corridor.id) return;
      const path = pathArrays(network.paths[item.id]);
      const score = scores[index];
      const hover = Number.isFinite(score) ? `${item.label}<br>Indice: ${score.toFixed(0)}/100 · ${name}` : item.label;
      lon.push(...path.lon, null);
      lat.push(...path.lat, null);
      for (let i = 0; i < path.lon.length; i += 1) {
        text.push(hover);
        ids.push(item.id);
      }
      text.push(null);
      ids.push(null);
    });
    if (!lon.length) continue;
    const strong = name === "Élevé" || name === "Critique";
    traces.push(geo(basemap, lon, lat, {
      mode: "lines",
      line: { width: strong ? 3.5 : 1.6, color: CATEGORY_COLORS[name] },
      opacity: strong ? 0.95 : 0.65,
      name,
      hovertext: text,
      hoverinfo: "text",
      customdata: ids,
      legendgroup: "network",
    }));
  }
  const pointScores = [];
  const hover = [];
  for (let i = 0; i < corridor.n; i += 1) {
    const score = at2(corridor.score, time, i, corridor.n);
    pointScores.push(Number.isFinite(score) ? score : 0);
    hover.push(Number.isFinite(score)
      ? `<b>${corridor.label}</b><br>km ${corridor.distance[i].toFixed(1)}<br>Indice: ${score.toFixed(0)}/100 · ${category(score)}<br>${hazardLabel(at2(corridor.dominant, time, i, corridor.n))}`
      : `<b>${corridor.label}</b><br>km ${corridor.distance[i].toFixed(1)}`);
  }
  traces.push(
    geo(basemap, corridor.lon, corridor.lat, {
      mode: "lines",
      line: { width: 7, color: "#111827" },
      hoverinfo: "skip",
      name: "Corridor sélectionné",
      showlegend: false,
    }),
    geo(basemap, corridor.lon, corridor.lat, {
      mode: "markers",
      marker: { size: 9, color: pointScores, cmin: 0, cmax: 100, colorscale: RISK_COLORSCALE, showscale: false },
      hovertext: hover,
      hoverinfo: "text",
      customdata: corridor.lon.map(() => corridor.id),
      name: "Indice le long du corridor",
      showlegend: false,
    }),
    geo(basemap, [corridor.lon[0], corridor.lon[corridor.n - 1]], [corridor.lat[0], corridor.lat[corridor.n - 1]], {
      mode: "markers+text",
      marker: { size: 22, color: "#111827" },
      text: ["A", "B"],
      textfont: { color: "#ffffff", size: 12, family: FONT },
      textposition: "middle center",
      hovertext: ["Début du corridor (km 0)", `Fin du corridor (km ${corridor.distance[corridor.n - 1].toFixed(0)})`],
      hoverinfo: "text",
      showlegend: false,
    }),
    geo(basemap, [corridor.lon[point]], [corridor.lat[point]], {
      mode: "markers",
      marker: { size: 15, color: "#ffffff" },
      hovertext: [`Point inspecté · km ${corridor.distance[point].toFixed(1)}`],
      hoverinfo: "text",
      showlegend: false,
    }),
  );
  const centerLon = mean(corridor.lon);
  const centerLat = mean(corridor.lat);
  const legend = {
    orientation: "h", y: 0.01, x: 0.01, yanchor: "bottom", xanchor: "left",
    bgcolor: "rgba(255,255,255,0.85)", title: { text: "Réseau à cette heure" }, font: { size: 11 },
  };
  let layout;
  if (basemap) {
    const span = Math.max(ptp(corridor.lon), ptp(corridor.lat) * 1.4, 0.15);
    const zoom = Math.min(10.5, Math.max(4.5, 8.6 - Math.log2(span * 8)));
    layout = baseLayout({
      height: 440,
      margin: { l: 0, r: 0, t: 0, b: 0 },
      map: { style: "carto-positron", center: { lon: centerLon, lat: centerLat }, zoom },
      legend,
      showlegend: true,
    });
  } else {
    const halfLon = Math.max(ptp(corridor.lon) * 0.75, 1.2);
    const halfLat = Math.max(ptp(corridor.lat) * 0.75, 0.8);
    layout = baseLayout({
      height: 440,
      margin: { l: 48, r: 12, t: 8, b: 40 },
      plot_bgcolor: "#f6f8fa",
      legend,
      showlegend: true,
      xaxis: { title: { text: "Longitude" }, range: [centerLon - halfLon, centerLon + halfLon] },
      yaxis: {
        title: { text: "Latitude" },
        range: [centerLat - halfLat, centerLat + halfLat],
        scaleanchor: "x",
        scaleratio: 1 / Math.cos((centerLat * Math.PI) / 180),
      },
    });
  }
  Plotly.react("network-map", traces, layout, { displaylogo: false, scrollZoom: true, responsive: true });
}

function drawHovmoller(corridor, time) {
  const labels = state.network.timesShort;
  const z = [];
  const hover = [];
  for (let t = 0; t < state.network.nTime; t += 1) {
    const row = [];
    const text = [];
    for (let i = 0; i < corridor.n; i += 1) {
      const value = at2(corridor.score, t, i, corridor.n);
      row.push(value);
      text.push(`${labels[t]} · km ${corridor.distance[i].toFixed(1)}<br>Indice: ${(Number.isFinite(value) ? value : 0).toFixed(0)}/100 · ${category(value)}<br>${hazardLabel(at2(corridor.dominant, t, i, corridor.n))}`);
    }
    z.push(row);
    hover.push(text);
  }
  const hours = labels.map((_, index) => index);
  const ticks = hours.filter((index) => index % 6 === 0);
  const layout = baseLayout({
    height: 400,
    shapes: [{ type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: time, y1: time, line: { color: "#111827", width: 2 } }],
    annotations: [{
      x: corridor.distance[0], y: time, text: "heure affichée", showarrow: false,
      xanchor: "left", yanchor: "bottom", font: { size: 10, color: "#111827" }, bgcolor: "rgba(255,255,255,0.75)",
    }],
    yaxis: { title: { text: "Échéance (heure locale)" }, tickvals: ticks, ticktext: ticks.map((index) => labels[index]), autorange: "reversed" },
    xaxis: { title: { text: "Distance le long du corridor (km)" } },
  });
  Plotly.react("hovmoller", [{
    type: "heatmap",
    x: corridor.distance,
    y: hours,
    z,
    zmin: 0,
    zmax: 100,
    colorscale: RISK_COLORSCALE,
    colorbar: { title: { text: "Indice" }, thickness: 12, tickvals: [12.5, 37.5, 62.5, 87.5], ticktext: ["Faible", "Modéré", "Élevé", "Critique"] },
    hovertext: hover,
    hoverinfo: "text",
  }], layout, { displaylogo: false, responsive: true });
}

function drawProfile(corridor, time, point) {
  const cap = state.cap;
  const series = profileColumn(corridor, time, point, cap);
  const terrainKm = corridor.terrain[point] / 1000;
  const shapes = [{ type: "rect", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 0, y1: terrainKm, fillcolor: TERRAIN_FILL, opacity: 0.35, line: { width: 0 } }];
  const annotations = [];
  series.columns.height.forEach((height, index) => {
    const km = height / 1000;
    if (km > cap) return;
    shapes.push({ type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: km, y1: km, line: { color: "rgba(17,24,39,0.25)", width: 1, dash: "dash" } });
    annotations.push({
      x: 1, xref: "paper", y: km, text: `${corridor.levels[index].toFixed(0)} hPa`,
      showarrow: false, xanchor: "right", yanchor: "bottom", font: { size: 10, color: "rgba(17,24,39,0.6)" },
    });
  });
  shapes.push({ type: "line", xref: "x", yref: "paper", x0: 0, x1: 0, y0: 0, y1: 1, line: { color: "#111827", width: 1.5, dash: "dot" } });
  const layout = baseLayout({
    height: 400,
    margin: { l: 56, r: 24, t: 70, b: 60 },
    shapes,
    annotations,
    title: { text: `Sondage au km ${corridor.distance[point].toFixed(1)}`, x: 0.02, font: { size: 13 } },
    legend: { orientation: "h", y: -0.22, x: 0 },
    xaxis: { title: { text: "Température / point de rosée (°C)" }, range: [-40, 12] },
    xaxis2: { title: { text: "Vent (km/h)" }, overlaying: "x", side: "top", range: [0, 150], showgrid: false, zeroline: false },
    yaxis: { title: { text: "Altitude (km)" }, range: [0, cap] },
  });
  Plotly.react("vertical-profile", [
    { type: "scatter", x: series.temperature, y: series.altitude, mode: "lines", line: { color: "#c0392b", width: 2.5 }, name: "Température", hovertemplate: "%{y:.2f} km · %{x:.1f} °C<extra>Température</extra>" },
    { type: "scatter", x: series.dewpoint, y: series.altitude, mode: "lines", line: { color: "#2e86c1", width: 2, dash: "dash" }, name: "Point de rosée", hovertemplate: "%{y:.2f} km · %{x:.1f} °C<extra>Point de rosée</extra>" },
    { type: "scatter", x: series.wind, y: series.altitude, mode: "lines", line: { color: "#7d8c99", width: 1.5 }, name: "Vent (km/h)", xaxis: "x2", hovertemplate: "%{y:.2f} km · %{x:.0f} km/h<extra>Vent</extra>" },
  ], layout, { displaylogo: false, responsive: true });
}

function row(label, value, note, tone) {
  return `<div class="kpi" style="border-top-color:${tone || "#1f4b6e"}"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`;
}

function renderHeader(corridor, time) {
  const network = state.network;
  let alerts = 0;
  for (let i = 0; i < network.corridors.length; i += 1) {
    const score = at2(network.score, time, i, network.corridors.length);
    if (Number.isFinite(score) && score >= 50) alerts += 1;
  }
  const chips = [
    `<span class="chip strong">${corridor.voltage} kV</span>`,
    `<span class="chip">${corridor.length.toFixed(0)} km</span>`,
    `<span class="chip">${corridor.region.replaceAll("/", " · ")}</span>`,
  ];
  if (corridor.parts > 1) chips.push(`<span class="chip">tronçon ${corridor.part}/${corridor.parts}</span>`);
  document.getElementById("corridor-header").innerHTML = `
    <div><h2>Ligne ${corridor.line}</h2><div class="chips">${chips.join("")}</div></div>
    <div class="network-status">
      <span class="eyebrow">Réseau à cette heure</span>
      <strong>${alerts} corridor${alerts > 1 ? "s" : ""} « Élevé » ou plus</strong>
      <small>sur ${network.corridors.length} corridors analysés</small>
    </div>`;
}

function renderKpis(corridor, time) {
  const n = corridor.n;
  let worst = 0;
  let worstScore = -1;
  const temps = [];
  const winds = [];
  const humid = [];
  const spacing = n > 1 ? (corridor.distance[n - 1] - corridor.distance[0]) / (n - 1) : 0;
  const kilometres = Object.fromEntries(PRECIPITATION_TYPES.map((name) => [name, 0]));
  for (let i = 0; i < n; i += 1) {
    const score = at2(corridor.score, time, i, n);
    if (Number.isFinite(score) && score > worstScore) {
      worstScore = score;
      worst = i;
    }
    temps.push(at2(corridor.nearT, time, i, n));
    winds.push(at2(corridor.nearWind, time, i, n) * 3.6);
    humid.push(at2(corridor.nearRh, time, i, n));
    kilometres[PRECIPITATION_TYPES[at2(corridor.precip, time, i, n)]] += spacing;
  }
  const nowCat = category(worstScore);
  const { peaks, when } = peakByPoint(corridor);
  let peakPoint = 0;
  for (let i = 1; i < n; i += 1) if (peaks[i] > peaks[peakPoint]) peakPoint = i;
  const peakCat = category(peaks[peakPoint]);
  const wet = Object.entries(kilometres).filter(([name, km]) => name !== "Sec" && km > 0).sort((a, b) => b[1] - a[1]);
  let typeValue = "Sec";
  let typeNote = "basse troposphère loin de la saturation";
  if (wet.length) {
    typeValue = `${wet[0][0]} sur ${wet[0][1].toFixed(0)} km`;
    const others = wet.slice(1).map(([name, km]) => `${name} ${km.toFixed(0)} km`).join(", ");
    typeNote = others || "aucun autre type diagnostiqué";
  }
  document.getElementById("kpi-cards").innerHTML = [
    row("Indice maximal à cette heure", `${worstScore.toFixed(0)} · ${nowCat}`, `km ${corridor.distance[worst].toFixed(0)} · ${hazardLabel(at2(corridor.dominant, time, worst, n))}`, CATEGORY_COLORS[nowCat]),
    row("Pic sur 48 h", `${peaks[peakPoint].toFixed(0)} · ${peakCat}`, `${state.network.timesShort[when[peakPoint]]} · km ${corridor.distance[peakPoint].toFixed(0)}`, CATEGORY_COLORS[peakCat]),
    row("Température ≈ 100 m au-dessus du sol", rangeText(temps, "°C", 1), `vent ${rangeText(winds, "km/h", 0)} · HR ${rangeText(humid, "%", 0)}`),
    row("Type de précipitation diagnostiqué", typeValue, typeNote),
  ].join("");
}

function renderSegments(corridor) {
  const host = document.getElementById("segments-table");
  if (!corridor.segments.length) {
    host.innerHTML = `<p class="empty">Aucun tronçon n'atteint le niveau « Modéré » sur la période.</p>`;
    return;
  }
  const rows = corridor.segments.map((item) => `
    <tr>
      <td>km ${item.km0.toFixed(0)} → ${item.km1.toFixed(0)}</td>
      <td>${item.len.toFixed(0)} km</td>
      <td><span class="badge" style="background:${CATEGORY_COLORS[item.cat]}">${item.score.toFixed(0)} · ${item.cat}</span></td>
      <td>${item.hazard}</td>
      <td>${item.precip}</td>
      <td>${item.temp.toFixed(1)} °C · ${item.wind.toFixed(0)} km/h</td>
      <td>${item.when}</td>
    </tr>`).join("");
  host.innerHTML = `<table class="segments"><thead><tr><th>Tronçon</th><th>Longueur</th><th>Indice</th><th>Aléa dominant</th><th>Précip.</th><th>Près du sol</th><th>Pic</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderLegend() {
  const precip = PRECIPITATION_TYPES.map((name) => `<span class="legend-item"><i style="background:${PRECIPITATION_COLORS[name]}"></i>${name}</span>`).join("");
  const bands = [["Faible", "< 25"], ["Modéré", "25–49"], ["Élevé", "50–74"], ["Critique", "≥ 75"]]
    .map(([name, bounds]) => `<span class="legend-item"><i style="background:${CATEGORY_COLORS[name]}"></i>${name} ${bounds}</span>`).join("");
  document.getElementById("legend").innerHTML = `<span class="legend-title">Bande « Précip. » :</span>${precip}<span class="legend-title">Indice :</span>${bands}`;
}

function filteredCorridors() {
  const voltages = Array.from(document.getElementById("voltage-filter").selectedOptions).map((option) => Number(option.value));
  const region = document.getElementById("region-filter").value;
  return state.network.corridors.filter((item) => {
    if (voltages.length && !voltages.includes(item.voltage)) return false;
    if (region && item.region !== region) return false;
    return true;
  });
}

function fillCorridors(preferred) {
  const select = document.getElementById("corridor-selector");
  const items = filteredCorridors();
  select.innerHTML = items.map((item) => {
    const peak = item.peak != null && item.peak >= 0 ? ` · pic ${item.peak.toFixed(0)}` : "";
    return `<option value="${item.id}">${item.label} · ${item.length.toFixed(0)} km${peak}</option>`;
  }).join("");
  const ids = new Set(items.map((item) => item.id));
  const next = ids.has(preferred) ? preferred : (items[0] ? items[0].id : null);
  if (next) select.value = next;
  return next;
}

function hydrate(raw) {
  const corridor = {
    id: raw.id,
    line: raw.line,
    label: raw.label,
    voltage: raw.voltage,
    length: raw.length,
    region: raw.region,
    part: raw.part,
    parts: raw.parts,
    n: raw.n,
    levels: raw.levels,
    segments: raw.segments,
    distance: unpack(raw.distance, "f32"),
    lon: unpack(raw.lon, "f32"),
    lat: unpack(raw.lat, "f32"),
    terrain: unpack(raw.terrain, "f32"),
    height: unpack(raw.height, "f16"),
    temperature: unpack(raw.temperature, "f16"),
    humidity: unpack(raw.humidity, "f16"),
    u: unpack(raw.u, "f16"),
    v: unpack(raw.v, "f16"),
    score: unpack(raw.score, "f16"),
    nearT: unpack(raw.nearT, "f16"),
    nearRh: unpack(raw.nearRh, "f16"),
    nearWind: unpack(raw.nearWind, "f16"),
    warm: unpack(raw.warm, "f16"),
    dominant: unpack(raw.dominant, "i8"),
    precip: unpack(raw.precip, "u8"),
  };
  return corridor;
}

async function loadCorridor(id) {
  if (state.corridor && state.corridor.id === id) return state.corridor;
  const response = await fetch(`data/corridors/${id.replaceAll("#", "_")}.json`);
  if (!response.ok) throw new Error(`Corridor ${id} introuvable`);
  state.corridor = hydrate(await response.json());
  state.corridorId = id;
  return state.corridor;
}

function render() {
  const corridor = state.corridor;
  const time = state.time;
  if (!corridor) return;
  document.getElementById("time-label").textContent = state.network.times[time];
  document.getElementById("time-selector").value = String(time);
  const resolved = resolvePoint(corridor, time);
  document.getElementById("profile-hint").textContent = resolved.hint;
  renderHeader(corridor, time);
  renderKpis(corridor, time);
  renderSegments(corridor);
  drawCrossSection(corridor, time, resolved.index);
  drawMap(corridor, time, resolved.index);
  drawHovmoller(corridor, time);
  drawProfile(corridor, time, resolved.index);
  attachPlotClicks();
}

function attachPlotClicks() {
  const listen = (id, handler) => {
    const node = document.getElementById(id);
    if (node.dataset.bound) return;
    node.dataset.bound = "1";
    node.on("plotly_click", handler);
  };
  listen("cross-section", (event) => {
    const point = event.points && event.points[0];
    if (!point || point.x == null) return;
    state.selection = { corridor: state.corridor.id, km: Number(point.x) };
    render();
  });
  listen("network-map", (event) => {
    const point = event.points && event.points[0];
    if (!point) return;
    const clicked = point.customdata;
    const known = state.network.corridors.some((item) => item.id === clicked);
    if (clicked && clicked !== state.corridor.id && known) {
      if (!filteredCorridors().some((item) => item.id === clicked)) {
        document.getElementById("voltage-filter").selectedIndex = -1;
        document.getElementById("region-filter").value = "";
      }
      fillCorridors(clicked);
      document.getElementById("corridor-selector").value = clicked;
      loadCorridor(clicked).then(render);
      return;
    }
    if (clicked === state.corridor.id && (point.lon != null || point.x != null)) {
      state.selection = {
        corridor: state.corridor.id,
        lon: Number(point.lon != null ? point.lon : point.x),
        lat: Number(point.lat != null ? point.lat : point.y),
      };
      render();
    }
  });
}

function radios(host, name, options, current, onChange) {
  host.innerHTML = options.map(([label, value]) => `
    <label><input type="radio" name="${name}" value="${value}" ${String(value) === String(current) ? "checked" : ""}> ${label}</label>
  `).join("");
  host.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => onChange(input.value));
  });
}

function step(delta) {
  const count = state.network.nTime;
  state.time = delta < 0 ? Math.max(0, state.time - 1) : (state.time + 1) % count;
  render();
}

function bind() {
  const network = state.network;
  const voltage = document.getElementById("voltage-filter");
  voltage.innerHTML = network.voltages.map((value) => `<option value="${value}">${value} kV</option>`).join("");
  const region = document.getElementById("region-filter");
  region.insertAdjacentHTML("beforeend", network.regions.map((value) => `<option value="${value}">${value}</option>`).join(""));
  document.getElementById("corridor-hint").textContent = `${network.corridors.length} corridors aériens de 5 km et plus dans le domaine ERA5. « pic » = indice maximal sur 48 h. Cliquez aussi un corridor sur la carte.`;
  radios(document.getElementById("variable-selector"), "variable", Object.entries(VARIABLES).map(([key, meta]) => [meta.label, key]), state.variable, (value) => {
    state.variable = value;
    render();
  });
  radios(document.getElementById("cap-selector"), "cap", [["3 km", 3], ["6 km", 6], ["10 km", 10]], state.cap, (value) => {
    state.cap = Number(value);
    render();
  });
  radios(document.getElementById("basemap-selector"), "basemap", [["Carte (en ligne)", "online"], ["Schéma (hors ligne)", "offline"]], state.basemap, (value) => {
    state.basemap = value;
    render();
  });
  const slider = document.getElementById("time-selector");
  slider.max = String(network.nTime - 1);
  slider.addEventListener("input", () => {
    state.time = Number(slider.value);
    render();
  });
  const marks = document.getElementById("time-marks");
  marks.innerHTML = [0, 12, 24, 36].filter((index) => index < network.nTime).map((index) => `<span>${network.timesShort[index].slice(-3)}</span>`).join("");
  document.getElementById("time-prev").addEventListener("click", () => step(-1));
  document.getElementById("time-next").addEventListener("click", () => step(1));
  document.getElementById("time-play").addEventListener("click", () => {
    state.playing = !state.playing;
    document.getElementById("time-play").textContent = state.playing ? "❚❚ Pause" : "▶ Lecture";
    if (state.playing) state.timer = setInterval(() => step(1), 900);
    else clearInterval(state.timer);
  });
  const choose = async (id) => {
    if (!id) return;
    await loadCorridor(id);
    render();
  };
  voltage.addEventListener("change", () => choose(fillCorridors(document.getElementById("corridor-selector").value)));
  region.addEventListener("change", () => choose(fillCorridors(document.getElementById("corridor-selector").value)));
  document.getElementById("corridor-selector").addEventListener("change", (event) => choose(event.target.value));
  document.getElementById("most-exposed").addEventListener("click", () => {
    const items = filteredCorridors();
    let best = items[0];
    let bestScore = -1;
    items.forEach((item) => {
      const index = network.corridors.findIndex((candidate) => candidate.id === item.id);
      const score = at2(network.score, state.time, index, network.corridors.length);
      const value = Number.isFinite(score) ? score : -1;
      if (value > bestScore) {
        bestScore = value;
        best = item;
      }
    });
    if (!best) return;
    document.getElementById("corridor-selector").value = best.id;
    choose(best.id);
  });
  renderLegend();
}

async function main() {
  const response = await fetch("data/network.json");
  if (!response.ok) {
    document.getElementById("corridor-header").innerHTML = `<h2>Données absentes</h2><p>Le site statique est produit par le workflow GitHub Pages.</p>`;
    return;
  }
  const network = await response.json();
  network.score = unpack(network.score, "f16");
  state.network = network;
  state.time = network.defaultTime;
  bind();
  fillCorridors(network.defaultCorridor);
  await loadCorridor(network.defaultCorridor);
  render();
}

main().catch((error) => {
  document.getElementById("corridor-header").innerHTML = `<h2>Chargement impossible</h2><p class="error">${error.message}</p>`;
});
