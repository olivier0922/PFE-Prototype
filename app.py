from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update

from src.config import LOCAL_UTC_OFFSET_HOURS, MIN_CORRIDOR_LENGTH_KM
from src.data import DataRepository
from src.figures import (
    VARIABLES,
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


# --------------------------------------------------------------------- données

repository = DataRepository()
print("Classement de l'ensemble du réseau…", flush=True)
overview = compute_network_overview(repository)
CORRIDORS = repository.corridors
LABELS = dict(zip(CORRIDORS["corridor_id"], CORRIDORS["label"]))
TIMES = repository.times
PEAK_SCORE, PEAK_TIME = overview.peak()


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


def corridor_options(voltages: list[int] | None, region: str | None) -> list[dict[str, str]]:
    frame = repository.filter_corridors(voltages, region)
    ids = frame["corridor_id"].tolist()
    peaks = PEAK_SCORE[[repository.corridor_index[cid] for cid in ids]] if ids else np.array([])
    options = []
    for cid, label, length, peak in zip(ids, frame["label"], frame["length_km"], peaks):
        suffix = f" · pic {peak:.0f}" if np.isfinite(peak) and peak >= 0 else ""
        options.append({"label": f"{label} · {length:.0f} km{suffix}", "value": cid})
    return options


# ---------------------------------------------------------------------- layout

app = Dash(__name__, title="Météo sur le réseau de transport", update_title=None)
server = app.server

time_marks = {
    index: f"{(pd.Timestamp(value) + pd.Timedelta(hours=LOCAL_UTC_OFFSET_HOURS)).strftime('%Hh')}"
    for index, value in enumerate(TIMES)
    if index % 12 == 0
}


def field(label: str, component: object, hint: str | None = None) -> html.Div:
    children: list[object] = [html.Label(label), component]
    if hint:
        children.append(html.Small(hint, className="hint"))
    return html.Div(children, className="field")


sidebar = html.Aside(
    [
        html.Div(
            [
                html.P("Hydro-Québec · TransÉnergie", className="eyebrow"),
                html.H1("Météo sur le réseau"),
                html.P("Coupe atmosphérique et indice d'aléa le long des corridors de transport.", className="lede"),
            ],
            className="brand",
        ),
        html.Section(
            [
                html.H2("Corridor"),
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
                    "Région (direction territoriale)",
                    dcc.Dropdown(
                        id="region-filter",
                        options=[{"label": r, "value": r} for r in repository.regions],
                        value=None,
                        placeholder="Toutes les régions",
                    ),
                ),
                field(
                    "Corridor",
                    dcc.Dropdown(
                        id="corridor-selector",
                        options=corridor_options(None, None),
                        value=DEFAULT_CORRIDOR,
                        clearable=False,
                        searchable=True,
                        placeholder="Rechercher un numéro de ligne…",
                    ),
                    f"{len(CORRIDORS)} corridors aériens de {MIN_CORRIDOR_LENGTH_KM:.0f} km et plus dans le domaine ERA5. "
                    "« pic » = indice maximal sur 48 h. Cliquez aussi un corridor sur la carte.",
                ),
                html.Button("Aller au corridor le plus exposé à cette heure", id="most-exposed", className="button ghost"),
            ],
            className="sidebar-section",
        ),
        html.Section(
            [
                html.H2("Affichage"),
                field(
                    "Variable de la coupe",
                    dcc.RadioItems(
                        id="variable-selector",
                        options=[{"label": meta["label"], "value": key} for key, meta in VARIABLES.items()],
                        value="temperature",
                        className="radio",
                    ),
                ),
                field(
                    "Plafond de la coupe",
                    dcc.RadioItems(
                        id="cap-selector",
                        options=[{"label": "3 km", "value": 3}, {"label": "6 km", "value": 6}, {"label": "10 km", "value": 10}],
                        value=6,
                        inline=True,
                        className="radio inline",
                    ),
                    "L'isotherme 0 °C reste affichée quelle que soit la variable.",
                ),
                field(
                    "Fond de carte",
                    dcc.RadioItems(
                        id="basemap-selector",
                        options=[
                            {"label": "Carte (en ligne)", "value": "online"},
                            {"label": "Schéma (hors ligne)", "value": "offline"},
                        ],
                        value="online",
                        inline=True,
                        className="radio inline",
                    ),
                    "Le schéma n'utilise aucun serveur externe.",
                ),
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
                        html.Button("◀ 1 h", id="time-prev", className="button"),
                        html.Button("▶ Lecture", id="time-play", className="button primary"),
                        html.Button("1 h ▶", id="time-next", className="button"),
                    ],
                    className="button-row",
                ),
                dcc.Interval(id="time-ticker", interval=900, disabled=True),
                html.Small(
                    "Analyses ERA5 horaires du 29 au 30 décembre 1942 (heure locale HNE).",
                    className="hint",
                ),
            ],
            className="sidebar-section",
        ),
        html.Details(
            [
                html.Summary("Comment lire cette vue"),
                html.Ul(
                    [
                        html.Li("La coupe suit le corridor de A vers B; l'axe horizontal est le kilométrage réel."),
                        html.Li("Le relief brun est l'altitude ETOPO. La ligne pointillée noire est l'isotherme 0 °C."),
                        html.Li("Les tirets gris sont les niveaux ERA5 (1000, 850, 700 hPa…). Entre eux, les valeurs sont interpolées linéairement."),
                        html.Li("La bande « Précip. » diagnostique le type probable à partir de la structure thermique."),
                        html.Li("L'indice 0–100 suit l'aléa dominant: pluie verglaçante, givrage, vent, froid."),
                        html.Li("Cliquez sur la coupe pour afficher le sondage vertical d'un point."),
                    ]
                ),
            ],
            className="help",
        ),
    ],
    className="sidebar",
)

main = html.Main(
    [
        html.Div(id="corridor-header", className="corridor-header"),
        html.Section(id="kpi-cards", className="kpi-grid"),
        html.Section(
            [
                html.Div(
                    [
                        html.H3("Coupe atmosphérique le long du corridor"),
                        html.Span("Cliquez sur la coupe pour inspecter un point", className="panel-hint"),
                    ],
                    className="panel-head",
                ),
                dcc.Loading(
                    dcc.Graph(id="cross-section", config={"displaylogo": False, "displayModeBar": "hover"}),
                    type="dot",
                    color="#1f4b6e",
                    delay_show=300,
                ),
                html.Div(
                    [html.Span("Bande « Précip. » :", className="legend-title")]
                    + [
                        html.Span(
                            [html.I(style={"background": PRECIPITATION_COLORS[name]}), name],
                            className="legend-item",
                        )
                        for name in PRECIPITATION_TYPES
                    ]
                    + [
                        html.Span("Indice :", className="legend-title"),
                    ]
                    + [
                        html.Span(
                            [html.I(style={"background": CATEGORY_COLORS[name]}), f"{name} {bounds}"],
                            className="legend-item",
                        )
                        for name, bounds in (("Faible", "< 25"), ("Modéré", "25–49"), ("Élevé", "50–74"), ("Critique", "≥ 75"))
                    ],
                    className="legend-row",
                ),
            ],
            className="panel",
        ),
        html.Section(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.H3("Réseau et tracé"),
                                html.Span("Couleur = indice à l'heure affichée", className="panel-hint"),
                            ],
                            className="panel-head",
                        ),
                        dcc.Graph(id="network-map", config={"displaylogo": False, "scrollZoom": True}),
                    ],
                    className="panel",
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                html.H3("Évolution sur 48 h"),
                                html.Span("Où et quand l'indice culmine", className="panel-hint"),
                            ],
                            className="panel-head",
                        ),
                        dcc.Graph(id="hovmoller", config={"displaylogo": False}),
                    ],
                    className="panel",
                ),
            ],
            className="two-columns",
        ),
        html.Section(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.H3("Sondage vertical"),
                                html.Span(id="profile-hint", className="panel-hint"),
                            ],
                            className="panel-head",
                        ),
                        dcc.Graph(id="vertical-profile", config={"displaylogo": False}),
                    ],
                    className="panel",
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                html.H3("Segments les plus exposés sur 48 h"),
                                html.Span("Tronçons contigus dont l'indice atteint « Élevé »", className="panel-hint"),
                            ],
                            className="panel-head",
                        ),
                        html.Div(id="segments-table", className="table-wrap"),
                    ],
                    className="panel",
                ),
            ],
            className="two-columns",
        ),
        html.Aside(
            [
                html.Strong("Portée de l'indice. "),
                "Il mesure un potentiel atmosphérique calculé à partir de la température, de l'humidité, du vent et "
                "du géopotentiel sur cinq niveaux de pression. Les données fournies ne contiennent ni précipitations ni "
                "eau liquide: il ne s'agit pas d'une prévision validée d'accumulation de glace. Les seuils sont "
                "documentés dans src/risk.py et doivent être calibrés avec les observations et l'historique des interruptions.",
            ],
            className="notice",
        ),
        dcc.Store(id="selected-point"),
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
    State("time-selector", "value"),
    prevent_initial_call=True,
)
def step_time(_ticks, _prev, _next, value):
    value = int(value or 0)
    if ctx.triggered_id == "time-prev":
        return max(0, value - 1)
    return (value + 1) % len(TIMES)


@app.callback(
    Output("selected-point", "data"),
    Input("cross-section", "clickData"),
    Input("network-map", "clickData"),
    State("corridor-selector", "value"),
)
def remember_selected_point(section_click, map_click, corridor_id):
    if ctx.triggered_id == "cross-section" and section_click:
        return {"corridor": corridor_id, "km": float(section_click["points"][0]["x"])}
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


def _header(profiles: LineProfiles, time_index: int) -> list[object]:
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


def _kpis(profiles: LineProfiles, risk: RiskAssessment, time_index: int) -> list[html.Div]:
    distance = profiles.track.distance_km
    score_now = risk.score[time_index]
    finite = np.isfinite(score_now)
    diagnostics = risk.diagnostics

    if finite.any():
        worst = int(np.nanargmax(np.where(finite, score_now, -1)))
        category = str(risk.category[time_index, worst])
        hazard = HAZARD_LABELS[str(hazard_names(risk.dominant[time_index, worst]))]
        now_value = f"{score_now[worst]:.0f} · {category}"
        now_note = f"km {distance[worst]:.0f} · {hazard}"
        now_tone = CATEGORY_COLORS[category]
    else:
        now_value, now_note, now_tone = "n/d", "hors domaine", None

    peak_by_point = risk.peak_score_by_point
    peak_point = int(np.argmax(peak_by_point))
    peak_time = int(risk.peak_time_index_by_point[peak_point])
    peak_category = str(categorize(np.array([peak_by_point[peak_point]]))[0])
    peak_value = f"{peak_by_point[peak_point]:.0f} · {peak_category}"
    peak_note = f"{format_time(TIMES[peak_time], short=True)} · km {distance[peak_point]:.0f}"

    codes = risk.precipitation[time_index]
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

    conditions_value = _range_text(diagnostics.near_ground_temperature_c[time_index], "°C")
    conditions_note = (
        f"vent {_range_text(diagnostics.near_ground_wind_ms[time_index] * 3.6, 'km/h', 0)} · "
        f"HR {_range_text(diagnostics.near_ground_humidity[time_index], '%', 0)}"
    )
    return [
        _kpi("Indice maximal à cette heure", now_value, now_note, now_tone),
        _kpi("Pic sur 48 h", peak_value, peak_note, CATEGORY_COLORS[peak_category]),
        _kpi("Température ≈ 100 m au-dessus du sol", conditions_value, conditions_note),
        _kpi("Type de précipitation diagnostiqué", type_value, type_note),
    ]


def _segments_table(profiles: LineProfiles, risk: RiskAssessment) -> object:
    frame = exposed_segments(profiles.track.distance_km, risk, TIMES)
    if frame.empty:
        return html.P("Aucun tronçon n'atteint le niveau « Modéré » sur la période.", className="empty")
    header = html.Tr(
        [html.Th(name) for name in ("Tronçon", "Longueur", "Indice", "Aléa dominant", "Précip.", "Près du sol", "Pic")]
    )
    rows = []
    for row in frame.itertuples():
        color = CATEGORY_COLORS[row.categorie]
        rows.append(
            html.Tr(
                [
                    html.Td(f"km {row.km_debut:.0f} → {row.km_fin:.0f}"),
                    html.Td(f"{max(row.longueur_km, 1):.0f} km"),
                    html.Td(
                        html.Span(f"{row.risque_max:.0f} · {row.categorie}", className="badge", style={"background": color})
                    ),
                    html.Td(row.alea),
                    html.Td(row.precipitation),
                    html.Td(f"{row.temperature_c:.1f} °C · {row.vent_kmh:.0f} km/h"),
                    html.Td(format_time(row.pic_time, short=True)),
                ]
            )
        )
    return html.Table([html.Thead(header), html.Tbody(rows)], className="segments")


def _resolve_point(profiles: LineProfiles, selection: dict | None, risk: RiskAssessment, time_index: int) -> tuple[int, str]:
    track = profiles.track
    if selection and selection.get("corridor") == profiles.corridor.corridor_id:
        if "km" in selection:
            index = int(np.argmin(np.abs(track.distance_km - float(selection["km"]))))
            return index, f"Point choisi sur la coupe · km {track.distance_km[index]:.1f}"
        if "lon" in selection:
            index = int(np.argmin(np.hypot(track.longitude - selection["lon"], track.latitude - selection["lat"])))
            return index, f"Point choisi sur la carte · km {track.distance_km[index]:.1f}"
    scores = np.where(np.isfinite(risk.score[time_index]), risk.score[time_index], -1)
    index = int(np.argmax(scores))
    return index, f"Point le plus exposé à cette heure · km {track.distance_km[index]:.1f}"


@app.callback(
    Output("corridor-header", "children"),
    Output("kpi-cards", "children"),
    Output("cross-section", "figure"),
    Output("network-map", "figure"),
    Output("hovmoller", "figure"),
    Output("vertical-profile", "figure"),
    Output("profile-hint", "children"),
    Output("segments-table", "children"),
    Output("time-label", "children"),
    Input("corridor-selector", "value"),
    Input("variable-selector", "value"),
    Input("cap-selector", "value"),
    Input("time-selector", "value"),
    Input("selected-point", "data"),
    Input("basemap-selector", "value"),
)
def update_dashboard(corridor_id, variable, cap_km, time_index, selection, basemap):
    time_index = int(time_index or 0)
    time_label = format_time(TIMES[time_index])
    corridor_id = str(corridor_id or DEFAULT_CORRIDOR)
    try:
        profiles, risk = corridor_analysis(corridor_id)
        point, hint = _resolve_point(profiles, selection, risk, time_index)
        return (
            _header(profiles, time_index),
            _kpis(profiles, risk, time_index),
            make_cross_section_figure(profiles, risk, time_index, variable, float(cap_km), point),
            make_map_figure(overview, LABELS, time_index, profiles, risk, point, basemap=(basemap != "offline")),
            make_hovmoller_figure(profiles, risk, time_index),
            make_profile_figure(profiles, time_index, point, float(cap_km)),
            hint,
            _segments_table(profiles, risk),
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
            html.P(message, className="empty"),
            time_label,
        )


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=8050)
