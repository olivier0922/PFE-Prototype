from __future__ import annotations

import math
import re
from functools import lru_cache

import numpy as np
import pandas as pd
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update

from src.config import LOCAL_UTC_OFFSET_HOURS, MIN_CORRIDOR_LENGTH_KM
from src.data import DataRepository
from src.figures import (
    DEFAULT_OVERLAYS,
    OVERLAYS,
    empty_figure,
    format_time,
    make_cross_section_figure,
    make_hovmoller_figure,
    make_map_figure,
    make_profile_figure,
)
from src.interpolation import LineProfiles, build_line_profiles
from src.overview import compute_network_overview
from src.risk import (
    CATEGORY_COLORS,
    HAZARD_LABELS,
    PRECIPITATION_COLORS,
    PRECIPITATION_TYPES,
    RiskAssessment,
    assess_risk,
    categorize,
    exposed_segments,
    hazard_names,
)
from src.variables import available_variables


# --------------------------------------------------------------------- données

repository = DataRepository()
print("Classement de l'ensemble du réseau…", flush=True)
overview = compute_network_overview(repository)
CORRIDORS = repository.corridors
LABELS = dict(zip(CORRIDORS["corridor_id"], CORRIDORS["label"]))
TIMES = repository.times
PEAK_SCORE, PEAK_TIME = overview.peak()
VARIABLES = available_variables(repository.weather.data_vars)
MIN_SEGMENT_KM = 5.0


def _default_corridor() -> str:
    """Open on a long, high-voltage corridor that is strongly affected by the event."""
    voltages = CORRIDORS["voltage_kv"].to_numpy()
    lengths = CORRIDORS["length_km"].to_numpy()
    for min_voltage, min_length in ((315, 150), (230, 100), (120, 50), (0, 0)):
        candidates = np.flatnonzero((voltages >= min_voltage) & (lengths >= min_length))
        if candidates.size and np.nanmax(PEAK_SCORE[candidates]) >= 50:
            break
    best = candidates[np.argmax(PEAK_SCORE[candidates])]
    return str(CORRIDORS.iloc[best]["corridor_id"])


DEFAULT_CORRIDOR = _default_corridor()
DEFAULT_TIME = int(PEAK_TIME[repository.corridor_index[DEFAULT_CORRIDOR]])


@lru_cache(maxsize=48)
def corridor_analysis(corridor_id: str) -> tuple[LineProfiles, RiskAssessment]:
    profiles = build_line_profiles(repository, corridor_id)
    return profiles, assess_risk(profiles.diagnostics)


def corridor_length(corridor_id: str) -> float:
    """Length rounded up to 0.1 km so the full slider range always covers the last sample."""
    return math.ceil(float(CORRIDORS.iloc[repository.corridor_index[corridor_id]]["length_km"]) * 10) / 10


def corridor_options(voltages: list[int] | None, region: str | None) -> list[dict[str, str]]:
    frame = repository.filter_corridors(voltages, region)
    ids = frame["corridor_id"].tolist()
    peaks = PEAK_SCORE[[repository.corridor_index[cid] for cid in ids]] if ids else np.array([])
    options = []
    for cid, label, length, peak in zip(ids, frame["label"], frame["length_km"], peaks):
        suffix = f" · pic {peak:.0f}" if np.isfinite(peak) and peak >= 0 else ""
        options.append({"label": f"{label} · {length:.0f} km{suffix}", "value": cid})
    return options


def km_marks(length: float) -> dict[float, str]:
    step = next((s for s in (10, 25, 50, 100, 200, 500) if length / s <= 6), 1000)
    marks = {float(k): f"{k:.0f}" for k in np.arange(0.0, length, step) if length - k > 0.4 * step}
    marks[round(length, 1)] = f"{length:.0f} km"
    return marks


def segment_mask(profiles: LineProfiles, km_range: list[float] | None) -> np.ndarray:
    distance = profiles.track.distance_km
    if not km_range:
        return np.ones(len(distance), dtype=bool)
    lo, hi = sorted(float(v) for v in km_range)
    mask = (distance >= lo) & (distance <= hi)
    if not mask.any():
        mask[int(np.argmin(np.abs(distance - (lo + hi) / 2)))] = True
    return mask


# ---------------------------------------------------------------------- layout

app = Dash(__name__, title="Météo sur le réseau de transport", update_title=None)
server = app.server

time_marks = {
    index: f"{(pd.Timestamp(value) + pd.Timedelta(hours=LOCAL_UTC_OFFSET_HOURS)).strftime('%Hh')}"
    for index, value in enumerate(TIMES)
    if index % 12 == 0
}
DEFAULT_LENGTH = corridor_length(DEFAULT_CORRIDOR)

GRAPH_CONFIG = {
    "displaylogo": False,
    "displayModeBar": "hover",
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
}


def field(label: str, component: object, hint: str | None = None) -> html.Div:
    children: list[object] = [html.Label(label), component]
    if hint:
        children.append(html.Small(hint, className="hint"))
    return html.Div(children, className="field")


def graph(graph_id: str, size: str, **config: object) -> dcc.Graph:
    return dcc.Graph(
        id=graph_id,
        className=f"graph graph-{size}",
        responsive=True,
        config={**GRAPH_CONFIG, **config},
    )


def menu(summary_id: str, body: object) -> html.Details:
    """Compact drop-down built on <details>: no portal, so it also works in full screen."""
    return html.Details([html.Summary(id=summary_id), html.Div(body, className="menu-body")], className="menu")


def panel(title: str, body: list[object], hint: object = None, tools: list[object] | None = None, **props) -> html.Section:
    fullscreen = html.Button("⤢", className="icon-button", title="Plein écran (Échap pour quitter)", **{"data-fullscreen": "1"})
    head = html.Div(
        [
            html.Div([html.H3(title), hint] if hint is not None else [html.H3(title)], className="panel-title"),
            html.Div([*(tools or []), fullscreen], className="toolbar"),
        ],
        className="panel-head",
    )
    return html.Section([head, *body], className="panel", **props)


def pills(component_id: str, options: list[dict[str, object]], value: object) -> dcc.RadioItems:
    return dcc.RadioItems(id=component_id, options=options, value=value, inline=True, className="pills")


sidebar = html.Aside(
    [
        html.Div(
            [
                html.P("Hydro-Québec · TransÉnergie", className="eyebrow"),
                html.H1("Météo sur le réseau"),
            ],
            className="brand",
        ),
        html.Section(
            [
                html.H2("Corridor"),
                dcc.Dropdown(
                    id="corridor-selector",
                    options=corridor_options(None, None),
                    value=DEFAULT_CORRIDOR,
                    clearable=False,
                    searchable=True,
                    placeholder="Rechercher un numéro de ligne…",
                ),
                html.Details(
                    [
                        html.Summary("Filtres"),
                        field(
                            "Tension",
                            dcc.Dropdown(
                                id="voltage-filter",
                                options=[{"label": f"{v} kV", "value": v} for v in repository.voltages],
                                value=[],
                                multi=True,
                                placeholder="Toutes les tensions",
                            ),
                        ),
                        field(
                            "Région",
                            dcc.Dropdown(
                                id="region-filter",
                                options=[{"label": r, "value": r} for r in repository.regions],
                                value=None,
                                placeholder="Toutes les régions",
                            ),
                        ),
                        html.Small(
                            f"{len(CORRIDORS)} corridors aériens de {MIN_CORRIDOR_LENGTH_KM:.0f} km et plus. "
                            "« pic » = indice maximal sur 48 h.",
                            className="hint",
                        ),
                    ],
                    className="filters",
                ),
                html.Button("Corridor le plus exposé à cette heure", id="most-exposed", className="button ghost"),
            ],
            className="sidebar-section",
        ),
        html.Section(
            [
                html.H2("Échéance"),
                html.Strong(id="time-label", className="time-label"),
                dcc.Slider(
                    id="time-selector",
                    min=0,
                    max=len(TIMES) - 1,
                    step=1,
                    value=DEFAULT_TIME,
                    marks=time_marks,
                    tooltip={"placement": "bottom", "always_visible": False},
                    updatemode="drag",
                ),
                html.Div(
                    [
                        html.Button("◀", id="time-prev", className="button", title="Heure précédente"),
                        html.Button("▶ Lecture", id="time-play", className="button primary"),
                        html.Button("▶", id="time-next", className="button", title="Heure suivante"),
                    ],
                    className="button-row",
                ),
                dcc.Interval(id="time-ticker", interval=900, disabled=True),
                html.Small("ERA5 horaire, 29–30 décembre 1942 (heure locale HNE).", className="hint"),
            ],
            className="sidebar-section",
        ),
        html.Details(
            [
                html.Summary("Comment lire le tableau de bord"),
                html.Ul(
                    [
                        html.Li("La coupe suit le corridor de A vers B; l'axe horizontal est le kilométrage réel."),
                        html.Li("Fond: isolignes remplies de la variable choisie (isothermes pour la température)."),
                        html.Li("Calques: isotherme 0 °C, couche de fonte, vecteurs de vent, isolignes d'autres variables."),
                        html.Li("Flèches de vent: vent horizontal vu du dessus, nord en haut; la taille suit la vitesse."),
                        html.Li("Glisser sur la coupe pour zoomer, molette pour zoomer, double-clic pour tout revoir."),
                        html.Li("Un tronçon choisi (curseur, zoom ou tableau) filtre les indicateurs et recentre la carte."),
                        html.Li("Cliquez la coupe ou le diagramme 48 h pour afficher le sondage d'un point."),
                        html.Li("⤢ agrandit un panneau en plein écran; Échap pour revenir."),
                    ]
                ),
            ],
            className="help",
        ),
    ],
    className="sidebar",
)

section_panel = panel(
    "Coupe le long du corridor",
    [
        dcc.Loading(graph("cross-section", "section", scrollZoom=True, doubleClick="autosize"), type="dot", color="#1f4b6e", delay_show=400),
        html.Div(
            [
                html.Span("Tronçon", className="segment-title"),
                html.Div(
                    dcc.RangeSlider(
                        id="km-range",
                        min=0,
                        max=round(DEFAULT_LENGTH, 1),
                        step=0.5,
                        value=[0, round(DEFAULT_LENGTH, 1)],
                        marks=km_marks(DEFAULT_LENGTH),
                        allowCross=False,
                        updatemode="mouseup",
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                    className="segment-slider",
                ),
                html.Span(id="segment-label", className="segment-label"),
                html.Button("Corridor entier", id="segment-reset", className="chip-button"),
            ],
            className="segment-bar",
        ),
        html.Div(
            [html.Span("Précip.", className="legend-title")]
            + [
                html.Span([html.I(style={"background": PRECIPITATION_COLORS[name]}), name], className="legend-item")
                for name in PRECIPITATION_TYPES
            ]
            + [html.Span("Indice", className="legend-title")]
            + [
                html.Span([html.I(style={"background": CATEGORY_COLORS[name]}), f"{name} {bounds}"], className="legend-item")
                for name, bounds in (("Faible", "< 25"), ("Modéré", "25–49"), ("Élevé", "50–74"), ("Critique", "≥ 75"))
            ],
            className="legend-row",
        ),
    ],
    tools=[
        menu(
            "background-summary",
            dcc.RadioItems(
                id="background-selector",
                options=[{"label": v.label, "value": key} for key, v in VARIABLES.items()],
                value="temperature",
                className="menu-options",
            ),
        ),
        menu(
            "overlay-summary",
            [
                html.P("Repères", className="menu-heading"),
                dcc.Checklist(
                    id="overlay-selector",
                    options=[{"label": label, "value": key} for key, label in OVERLAYS.items()],
                    value=DEFAULT_OVERLAYS,
                    className="menu-options",
                ),
                html.P("Isolignes par-dessus le fond", className="menu-heading"),
                dcc.Checklist(
                    id="isoline-selector",
                    options=[{"label": f"{v.label} ({v.unit})", "value": f"iso:{key}"} for key, v in VARIABLES.items()],
                    value=[],
                    className="menu-options",
                ),
            ],
        ),
        pills("cap-selector", [{"label": f"{v} km", "value": v} for v in (3, 6, 10)], 6),
    ],
    id="panel-section",
)

main = html.Main(
    [
        html.Div(id="corridor-header", className="corridor-header"),
        html.Section(id="kpi-cards", className="kpi-grid"),
        section_panel,
        html.Div(
            [
                panel(
                    "Réseau",
                    [graph("network-map", "map", scrollZoom=True)],
                    hint=html.Span("Couleur = indice à l'heure affichée", className="panel-hint"),
                    tools=[
                        pills(
                            "basemap-selector",
                            [{"label": "Carte", "value": "online"}, {"label": "Schéma hors ligne", "value": "offline"}],
                            "online",
                        )
                    ],
                ),
                panel(
                    "Évolution sur 48 h",
                    [graph("hovmoller", "medium")],
                    hint=html.Span("Cliquez pour aller à cette heure et ce point", className="panel-hint"),
                ),
            ],
            className="two-columns",
        ),
        html.Div(
            [
                panel(
                    "Sondage vertical",
                    [graph("vertical-profile", "medium")],
                    hint=html.Span(id="profile-hint", className="panel-hint"),
                ),
                panel(
                    "Tronçons exposés sur 48 h",
                    [html.Div(id="segments-table", className="table-wrap")],
                    hint=html.Span("Cliquez une ligne pour isoler le tronçon", className="panel-hint"),
                ),
            ],
            className="two-columns",
        ),
        html.Aside(
            [
                html.Strong("Portée de l'indice. "),
                "Potentiel atmosphérique calculé à partir de la température, de l'humidité, du vent et du "
                "géopotentiel sur cinq niveaux de pression. Sans précipitations ni eau liquide dans les données, "
                "ce n'est pas une prévision validée d'accumulation de glace; les seuils (src/risk.py) doivent être "
                "calibrés avec les observations et l'historique des interruptions.",
            ],
            className="notice",
        ),
        dcc.Store(id="selected-point"),
        dcc.Store(id="segment-rows"),
    ],
    className="main",
)

app.layout = html.Div([sidebar, main], className="app-shell")


# ------------------------------------------------------------------- callbacks


@app.callback(
    Output("corridor-selector", "options"),
    Output("corridor-selector", "value"),
    Input("voltage-filter", "value"),
    Input("region-filter", "value"),
    Input("network-map", "clickData"),
    Input("most-exposed", "n_clicks"),
    State("corridor-selector", "value"),
    State("time-selector", "value"),
)
def update_corridor_choices(voltages, region, click_data, _clicks, current, time_index):
    options = corridor_options(voltages, region)
    allowed = {option["value"] for option in options}
    trigger = ctx.triggered_id

    if trigger == "network-map" and click_data:
        clicked = click_data["points"][0].get("customdata")
        if clicked in LABELS:
            return options, clicked
        return options, no_update

    if trigger == "most-exposed":
        candidates = [repository.corridor_index[cid] for cid in allowed] if allowed else list(range(len(LABELS)))
        if candidates:
            scores = np.nan_to_num(overview.score[int(time_index or 0), candidates], nan=-1.0)
            return options, str(CORRIDORS.iloc[candidates[int(np.argmax(scores))]]["corridor_id"])

    if current in allowed:
        return options, current
    return options, (options[0]["value"] if options else no_update)


@app.callback(
    Output("time-ticker", "disabled"),
    Output("time-play", "children"),
    Input("time-play", "n_clicks"),
    State("time-ticker", "disabled"),
    prevent_initial_call=True,
)
def toggle_playback(_clicks, disabled):
    playing = disabled
    return (not playing), ("❚❚ Pause" if playing else "▶ Lecture")


@app.callback(
    Output("time-selector", "value"),
    Input("time-ticker", "n_intervals"),
    Input("time-prev", "n_clicks"),
    Input("time-next", "n_clicks"),
    Input("hovmoller", "clickData"),
    State("time-selector", "value"),
    prevent_initial_call=True,
)
def step_time(_ticks, _prev, _next, hovmoller_click, value):
    value = int(value or 0)
    if ctx.triggered_id == "hovmoller":
        if not hovmoller_click:
            return no_update
        return int(np.clip(round(float(hovmoller_click["points"][0]["y"])), 0, len(TIMES) - 1))
    if ctx.triggered_id == "time-prev":
        return max(0, value - 1)
    return (value + 1) % len(TIMES)


@app.callback(
    Output("background-summary", "children"),
    Output("overlay-summary", "children"),
    Input("background-selector", "value"),
    Input("overlay-selector", "value"),
    Input("isoline-selector", "value"),
)
def summarize_menus(background, overlays, isolines):
    label = VARIABLES[background].label if background in VARIABLES else "—"
    count = len(overlays or []) + len([key for key in isolines or [] if key != f"iso:{background}"])
    return [html.Span("Fond", className="menu-key"), label], [html.Span("Calques", className="menu-key"), str(count)]


def _relayout_range(relayout: dict | None, length: float) -> list[float] | None:
    """x range requested by a zoom, pan or double-click on the cross-section."""
    if not relayout:
        return None
    for key, value in relayout.items():
        if re.fullmatch(r"xaxis\d*\.autorange", key) and value:
            return [0.0, length]
    for key, value in relayout.items():
        match = re.fullmatch(r"(xaxis\d*)\.range\[0\]", key)
        if match and f"{match[1]}.range[1]" in relayout:
            return [float(value), float(relayout[f"{match[1]}.range[1]"])]
        if re.fullmatch(r"xaxis\d*\.range", key) and isinstance(value, list) and len(value) == 2:
            return [float(value[0]), float(value[1])]
    return None


@app.callback(
    Output("km-range", "max"),
    Output("km-range", "value"),
    Output("km-range", "marks"),
    Output("segment-label", "children"),
    Output("segment-reset", "style"),
    Input("corridor-selector", "value"),
    Input("km-range", "value"),
    Input("cross-section", "relayoutData"),
    Input({"type": "segment-row", "index": ALL}, "n_clicks"),
    Input("segment-reset", "n_clicks"),
    State("segment-rows", "data"),
)
def sync_segment(corridor_id, value, relayout, _row_clicks, _reset, rows):
    """Single source of truth for the selected stretch of the corridor."""
    corridor_id = str(corridor_id or DEFAULT_CORRIDOR)
    length = corridor_length(corridor_id)
    trigger = ctx.triggered_id
    unchanged = (no_update,) * 5
    requested: list[float] | None

    if trigger == "km-range":
        requested = value
    elif trigger == "cross-section":
        requested = _relayout_range(relayout, length)
        if requested is None:
            return unchanged
    elif isinstance(trigger, dict):
        index = int(trigger["index"])
        if not ctx.triggered[0]["value"] or not rows or index >= len(rows):
            return unchanged
        lo, hi = rows[index]
        margin = max(2.5, 0.15 * (hi - lo))
        requested = [lo - margin, hi + margin]
    else:
        requested = [0.0, length]

    lo, hi = sorted(float(v) for v in (requested or [0.0, length]))
    lo, hi = max(0.0, lo), min(length, hi)
    width = min(MIN_SEGMENT_KM, length)
    if hi - lo < width:
        center = min(max((lo + hi) / 2, width / 2), length - width / 2)
        lo, hi = center - width / 2, center + width / 2
    lo, hi = round(lo, 1), round(hi, 1)

    full = lo <= 0.05 and hi >= length - 0.05
    label = "" if full else f"{hi - lo:.0f} km"
    reset_style = {"visibility": "hidden"} if full else {}
    return length, [lo, hi], km_marks(length), label, reset_style


@app.callback(
    Output("segments-table", "children"),
    Output("segment-rows", "data"),
    Input("corridor-selector", "value"),
    Input("km-range", "value"),
)
def update_segments_table(corridor_id, km_range):
    try:
        profiles, risk = corridor_analysis(str(corridor_id or DEFAULT_CORRIDOR))
    except Exception as error:  # keep the server alive on a malformed corridor
        return html.P(f"Impossible d'analyser ce corridor: {error}", className="empty"), []
    frame = exposed_segments(profiles.track.distance_km, risk, TIMES)
    if frame.empty:
        return html.P("Aucun tronçon n'atteint le niveau « Modéré » sur la période.", className="empty"), []
    lo, hi = sorted(km_range) if km_range else (0.0, np.inf)
    full = lo <= 0.05 and hi >= profiles.track.distance_km[-1] - 0.05
    header = html.Tr([html.Th(name) for name in ("Tronçon", "Indice", "Aléa dominant", "Précip.", "Près du sol", "Pic")])
    rows, spans = [], []
    for index, row in enumerate(frame.itertuples()):
        overlaps = not full and row.km_fin >= lo and row.km_debut <= hi
        rows.append(
            html.Tr(
                [
                    html.Td([f"km {row.km_debut:.0f} → {row.km_fin:.0f}", html.Small(f"{max(row.longueur_km, 1):.0f} km")]),
                    html.Td(
                        html.Span(
                            f"{row.risque_max:.0f} · {row.categorie}",
                            className="badge",
                            style={"background": CATEGORY_COLORS[row.categorie]},
                        )
                    ),
                    html.Td(row.alea),
                    html.Td(row.precipitation),
                    html.Td(f"{row.temperature_c:.1f} °C · {row.vent_kmh:.0f} km/h"),
                    html.Td(format_time(row.pic_time, short=True)),
                ],
                id={"type": "segment-row", "index": index},
                n_clicks=0,
                className="active" if overlaps else None,
                title="Isoler ce tronçon dans la coupe",
            )
        )
        spans.append([float(row.km_debut), float(row.km_fin)])
    return html.Table([html.Thead(header), html.Tbody(rows)], className="segments"), spans


@app.callback(
    Output("selected-point", "data"),
    Input("cross-section", "clickData"),
    Input("hovmoller", "clickData"),
    Input("network-map", "clickData"),
    State("corridor-selector", "value"),
)
def remember_selected_point(section_click, hovmoller_click, map_click, corridor_id):
    if ctx.triggered_id == "cross-section" and section_click:
        return {"corridor": corridor_id, "km": float(section_click["points"][0]["x"])}
    if ctx.triggered_id == "hovmoller" and hovmoller_click:
        return {"corridor": corridor_id, "km": float(hovmoller_click["points"][0]["x"])}
    if ctx.triggered_id == "network-map" and map_click:
        point = map_click["points"][0]
        lon = point.get("lon", point.get("x"))
        lat = point.get("lat", point.get("y"))
        if point.get("customdata") == corridor_id and lon is not None and lat is not None:
            return {"corridor": corridor_id, "lon": float(lon), "lat": float(lat)}
    return no_update


def _kpi(label: str, value: str, note: str, tone: str | None = None) -> html.Div:
    style = {"borderTopColor": tone} if tone else {}
    return html.Div([html.Span(label), html.Strong(value), html.Small(note)], className="kpi", style=style)


def _range_text(values: np.ndarray, unit: str, digits: int = 1) -> str:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return "n/d"
    lo, hi = float(finite.min()), float(finite.max())
    if abs(hi - lo) < 10 ** (-digits):
        return f"{hi:.{digits}f} {unit}"
    return f"{lo:.{digits}f} à {hi:.{digits}f} {unit}"


def _header(profiles: LineProfiles, time_index: int, mask: np.ndarray, km_range: list[float] | None) -> list[object]:
    corridor = profiles.corridor
    network_scores = overview.score[time_index]
    alerts = int(np.sum(np.nan_to_num(network_scores, nan=-1) >= 50))
    chips = [
        html.Span(f"{corridor.voltage_kv} kV", className="chip strong"),
        html.Span(f"{corridor.length_km:.0f} km", className="chip"),
        html.Span(corridor.region.replace("/", " · "), className="chip"),
    ]
    if corridor.part_count > 1:
        chips.append(html.Span(f"tronçon {corridor.part_index}/{corridor.part_count}", className="chip"))
    if not mask.all() and km_range:
        lo, hi = sorted(km_range)
        chips.append(html.Span(f"vue: km {lo:.0f} → {hi:.0f}", className="chip focus"))
    return [
        html.Div([html.H2(f"Ligne {corridor.line_number}"), html.Div(chips, className="chips")]),
        html.Div(
            [
                html.Span("Réseau à cette heure", className="eyebrow"),
                html.Strong(f"{alerts} corridor{'s' if alerts > 1 else ''} « Élevé » ou plus"),
                html.Small(f"sur {len(LABELS)} corridors analysés"),
            ],
            className="network-status",
        ),
    ]


def _kpis(profiles: LineProfiles, risk: RiskAssessment, time_index: int, mask: np.ndarray) -> list[html.Div]:
    points = np.flatnonzero(mask)
    distance = profiles.track.distance_km
    score_now = risk.score[time_index, points]
    finite = np.isfinite(score_now)
    diagnostics = risk.diagnostics
    scope = "sur le tronçon" if not mask.all() else "à cette heure"

    if finite.any():
        worst = int(points[np.nanargmax(np.where(finite, score_now, -1))])
        category = str(risk.category[time_index, worst])
        hazard = HAZARD_LABELS[str(hazard_names(risk.dominant[time_index, worst]))]
        now_value = f"{risk.score[time_index, worst]:.0f} · {category}"
        now_note = f"km {distance[worst]:.0f} · {hazard}"
        now_tone = CATEGORY_COLORS[category]
    else:
        now_value, now_note, now_tone = "n/d", "hors domaine", None

    peak_by_point = risk.peak_score_by_point
    peak_point = int(points[np.argmax(peak_by_point[points])])
    peak_time = int(risk.peak_time_index_by_point[peak_point])
    peak_category = str(categorize(np.array([peak_by_point[peak_point]]))[0])
    peak_value = f"{peak_by_point[peak_point]:.0f} · {peak_category}"
    peak_note = f"{format_time(TIMES[peak_time], short=True)} · km {distance[peak_point]:.0f}"

    codes = risk.precipitation[time_index, points]
    spacing = float(np.mean(np.diff(distance))) if len(distance) > 1 else 0.0
    kilometres = {name: float(np.sum(codes == index)) * spacing for index, name in enumerate(PRECIPITATION_TYPES)}
    wet = {name: km for name, km in kilometres.items() if name != "Sec" and km > 0}
    if wet:
        dominant_type = max(wet, key=wet.get)
        type_value = f"{dominant_type} sur {wet[dominant_type]:.0f} km"
        others = ", ".join(f"{name} {km:.0f} km" for name, km in sorted(wet.items(), key=lambda item: -item[1]) if name != dominant_type)
        type_note = others or "aucun autre type diagnostiqué"
    else:
        type_value = "Sec"
        type_note = "basse troposphère loin de la saturation"

    conditions_value = _range_text(diagnostics.near_ground_temperature_c[time_index, points], "°C")
    conditions_note = (
        f"vent {_range_text(diagnostics.near_ground_wind_ms[time_index, points] * 3.6, 'km/h', 0)} · "
        f"HR {_range_text(diagnostics.near_ground_humidity[time_index, points], '%', 0)}"
    )
    return [
        _kpi(f"Indice maximal {scope}", now_value, now_note, now_tone),
        _kpi("Pic sur 48 h", peak_value, peak_note, CATEGORY_COLORS[peak_category]),
        _kpi("Température ≈ 100 m du sol", conditions_value, conditions_note),
        _kpi("Précipitation diagnostiquée", type_value, type_note),
    ]


def _resolve_point(
    profiles: LineProfiles, selection: dict | None, risk: RiskAssessment, time_index: int, mask: np.ndarray
) -> tuple[int, str]:
    track = profiles.track
    if selection and selection.get("corridor") == profiles.corridor.corridor_id:
        index = None
        if "km" in selection:
            index = int(np.argmin(np.abs(track.distance_km - float(selection["km"]))))
        elif "lon" in selection:
            index = int(np.argmin(np.hypot(track.longitude - selection["lon"], track.latitude - selection["lat"])))
        if index is not None and mask[index]:
            return index, f"Point choisi · km {track.distance_km[index]:.1f}"
    scores = np.where(mask & np.isfinite(risk.score[time_index]), risk.score[time_index], -1)
    index = int(np.argmax(scores))
    return index, f"Point le plus exposé · km {track.distance_km[index]:.1f}"


@app.callback(
    Output("corridor-header", "children"),
    Output("kpi-cards", "children"),
    Output("cross-section", "figure"),
    Output("network-map", "figure"),
    Output("hovmoller", "figure"),
    Output("vertical-profile", "figure"),
    Output("profile-hint", "children"),
    Output("time-label", "children"),
    Input("corridor-selector", "value"),
    Input("background-selector", "value"),
    Input("overlay-selector", "value"),
    Input("isoline-selector", "value"),
    Input("cap-selector", "value"),
    Input("time-selector", "value"),
    Input("selected-point", "data"),
    Input("basemap-selector", "value"),
    Input("km-range", "value"),
)
def update_dashboard(corridor_id, background, overlays, isolines, cap_km, time_index, selection, basemap, km_range):
    overlays = [*(overlays or []), *(isolines or [])]
    time_index = int(time_index or 0)
    time_label = format_time(TIMES[time_index])
    corridor_id = str(corridor_id or DEFAULT_CORRIDOR)
    try:
        profiles, risk = corridor_analysis(corridor_id)
        mask = segment_mask(profiles, km_range)
        point, hint = _resolve_point(profiles, selection, risk, time_index, mask)
        section = make_cross_section_figure(
            profiles, risk, time_index, background, overlays, float(cap_km), point, km_range
        ).update_layout(uirevision=f"{corridor_id}|{cap_km}")
        network = make_map_figure(
            overview, LABELS, time_index, profiles, risk, point, basemap=(basemap != "offline"), km_range=km_range
        ).update_layout(uirevision=f"{corridor_id}|{basemap}")
        hovmoller = make_hovmoller_figure(profiles, risk, time_index, km_range).update_layout(uirevision=corridor_id)
        return (
            _header(profiles, time_index, mask, km_range),
            _kpis(profiles, risk, time_index, mask),
            section,
            network,
            hovmoller,
            make_profile_figure(profiles, time_index, point, float(cap_km)),
            hint,
            time_label,
        )
    except Exception as error:  # keep the server alive on a malformed corridor
        message = f"Impossible d'analyser ce corridor: {error}"
        figure = empty_figure(message)
        return (
            [html.H2("Corridor indisponible")],
            [html.Div(message, className="error")],
            figure,
            figure,
            figure,
            figure,
            "",
            time_label,
        )


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=8050)
