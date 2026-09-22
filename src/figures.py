"""Plotly figures for the corridor dashboard."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .config import LOCAL_TZ_LABEL, LOCAL_UTC_OFFSET_HOURS
from .interpolation import LineProfiles
from .overview import NetworkOverview
from .risk import (
    CATEGORY_COLORS,
    HAZARD_LABELS,
    PRECIPITATION_COLORS,
    PRECIPITATION_TYPES,
    RiskAssessment,
    categorize,
    hazard_names,
)


FONT = "Inter, 'Segoe UI', system-ui, sans-serif"
INK = "#1b2733"
GRID = "#e3e8ee"
TERRAIN_FILL = "#8c6a4a"
TERRAIN_LINE = "#4b3621"

VARIABLES = {
    "temperature": {
        "label": "Température",
        "unit": "°C",
        "colorscale": "RdBu_r",
        "zmin": -24,
        "zmax": 8,
        "zmid": 0,
    },
    "dewpoint": {
        "label": "Point de rosée",
        "unit": "°C",
        "colorscale": "RdBu_r",
        "zmin": -30,
        "zmax": 6,
        "zmid": 0,
    },
    "humidity": {
        "label": "Humidité relative",
        "unit": "%",
        "colorscale": "YlGnBu",
        "zmin": 20,
        "zmax": 100,
    },
    "wind": {
        "label": "Vent",
        "unit": "km/h",
        "colorscale": "Turbo",
        "zmin": 0,
        "zmax": 120,
        "factor": 3.6,
    },
}

RISK_COLORSCALE = [
    [0.00, CATEGORY_COLORS["Faible"]],
    [0.25, CATEGORY_COLORS["Faible"]],
    [0.25, CATEGORY_COLORS["Modéré"]],
    [0.50, CATEGORY_COLORS["Modéré"]],
    [0.50, CATEGORY_COLORS["Élevé"]],
    [0.75, CATEGORY_COLORS["Élevé"]],
    [0.75, CATEGORY_COLORS["Critique"]],
    [1.00, CATEGORY_COLORS["Critique"]],
]

# Hard colour steps: each precipitation type occupies an equal interval of [0, 1].
PRECIPITATION_COLORSCALE: list[list[object]] = []
for index, name in enumerate(PRECIPITATION_TYPES):
    PRECIPITATION_COLORSCALE.append([index / len(PRECIPITATION_TYPES), PRECIPITATION_COLORS[name]])
    PRECIPITATION_COLORSCALE.append([(index + 1) / len(PRECIPITATION_TYPES), PRECIPITATION_COLORS[name]])


def format_time(value: np.datetime64 | pd.Timestamp, short: bool = False) -> str:
    utc = pd.Timestamp(value)
    local = utc + pd.Timedelta(hours=LOCAL_UTC_OFFSET_HOURS)
    months = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
    if short:
        return f"{local.day} {months[local.month - 1]} {local:%H}h"
    return (
        f"{local.day} {months[local.month - 1]} {local.year}, {local:%H:%M} {LOCAL_TZ_LABEL}"
        f" ({utc:%H:%M} UTC)"
    )


def _style(figure: go.Figure, height: int) -> go.Figure:
    figure.update_layout(
        height=height,
        margin={"l": 56, "r": 24, "t": 30, "b": 44},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#ffffff",
        font={"family": FONT, "color": INK, "size": 12},
        hoverlabel={"font": {"family": FONT, "size": 12}},
        hovermode="closest",
    )
    figure.update_xaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    figure.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    return figure


def empty_figure(message: str, height: int = 360) -> go.Figure:
    figure = go.Figure()
    figure.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
    figure.update_layout(xaxis={"visible": False}, yaxis={"visible": False})
    return _style(figure, height)


# --------------------------------------------------------------------------- map


def _geo_trace(basemap: bool, lon, lat, **options) -> go.Scattermap | go.Scatter:
    """Build a map trace, or an equivalent Cartesian trace for the offline schematic."""
    if basemap:
        return go.Scattermap(lon=lon, lat=lat, **options)
    marker = options.get("marker")
    if isinstance(marker, dict) and "symbol" in marker:
        marker = {key: value for key, value in marker.items() if key != "symbol"}
        options["marker"] = marker
    return go.Scatter(x=lon, y=lat, **options)


def make_map_figure(
    overview: NetworkOverview,
    labels: dict[str, str],
    time_index: int,
    profiles: LineProfiles,
    risk: RiskAssessment,
    selected_point: int | None,
    basemap: bool = True,
) -> go.Figure:
    """Network coloured by the indicator at ``time_index`` with the selected corridor highlighted.

    ``basemap=False`` draws the same content on plain axes (no external tiles),
    for environments without access to map servers.
    """
    figure = go.Figure()
    scores = overview.score[time_index]
    categories = categorize(scores)
    selected_id = profiles.corridor.corridor_id
    track = profiles.track

    for category in ("Indisponible", "Faible", "Modéré", "Élevé", "Critique"):
        lons: list[float | None] = []
        lats: list[float | None] = []
        texts: list[str | None] = []
        ids: list[str | None] = []
        for index, corridor_id in enumerate(overview.corridor_ids):
            if categories[index] != category or corridor_id == selected_id:
                continue
            lon, lat = overview.map_paths[corridor_id]
            score = scores[index]
            hover = (
                f"{labels[corridor_id]}<br>Indice: {score:.0f}/100 · {category}"
                if np.isfinite(score)
                else labels[corridor_id]
            )
            lons.extend(lon.tolist() + [None])
            lats.extend(lat.tolist() + [None])
            texts.extend([hover] * len(lon) + [None])
            ids.extend([corridor_id] * len(lon) + [None])
        if not lons:
            continue
        strong = category in ("Élevé", "Critique")
        figure.add_trace(
            _geo_trace(
                basemap,
                lons,
                lats,
                mode="lines",
                line={"width": 3.5 if strong else 1.6, "color": CATEGORY_COLORS[category]},
                opacity=0.95 if strong else 0.65,
                name=category,
                hovertext=texts,
                hoverinfo="text",
                customdata=ids,
                legendgroup="network",
            )
        )

    point_scores = risk.score[time_index]
    point_categories = risk.category[time_index]
    hazards = [HAZARD_LABELS[str(name)] for name in hazard_names(risk.dominant[time_index])]
    hover = [
        f"<b>{labels[selected_id]}</b><br>km {km:.1f}<br>Indice: {score:.0f}/100 · {category}<br>{hazard}"
        if np.isfinite(score)
        else f"<b>{labels[selected_id]}</b><br>km {km:.1f}"
        for km, score, category, hazard in zip(track.distance_km, point_scores, point_categories, hazards)
    ]
    figure.add_trace(
        _geo_trace(
            basemap,
            track.longitude,
            track.latitude,
            mode="lines",
            line={"width": 7, "color": "#111827"},
            hoverinfo="skip",
            name="Corridor sélectionné",
            showlegend=False,
        )
    )
    figure.add_trace(
        _geo_trace(
            basemap,
            track.longitude,
            track.latitude,
            mode="markers",
            marker={
                "size": 9,
                "color": np.nan_to_num(point_scores, nan=0.0),
                "cmin": 0,
                "cmax": 100,
                "colorscale": RISK_COLORSCALE,
                "showscale": False,
            },
            hovertext=hover,
            hoverinfo="text",
            customdata=[selected_id] * len(track.distance_km),
            name="Indice le long du corridor",
            showlegend=False,
        )
    )
    figure.add_trace(
        _geo_trace(
            basemap,
            [track.longitude[0], track.longitude[-1]],
            [track.latitude[0], track.latitude[-1]],
            mode="markers+text",
            marker={"size": 22, "color": "#111827"},
            text=["A", "B"],
            textfont={"color": "#ffffff", "size": 12, "family": FONT},
            textposition="middle center",
            hovertext=["Début du corridor (km 0)", f"Fin du corridor (km {track.distance_km[-1]:.0f})"],
            hoverinfo="text",
            showlegend=False,
        )
    )
    if selected_point is not None and 0 <= selected_point < len(track.distance_km):
        figure.add_trace(
            _geo_trace(
                basemap,
                [track.longitude[selected_point]],
                [track.latitude[selected_point]],
                mode="markers",
                marker={"size": 15, "color": "#ffffff", "opacity": 1.0},
                hovertext=[f"Point inspecté · km {track.distance_km[selected_point]:.1f}"],
                hoverinfo="text",
                showlegend=False,
            )
        )

    center_lon = float(np.mean(track.longitude))
    center_lat = float(np.mean(track.latitude))
    legend = {
        "orientation": "h",
        "y": 0.01,
        "x": 0.01,
        "yanchor": "bottom",
        "xanchor": "left",
        "bgcolor": "rgba(255,255,255,0.85)",
        "title": {"text": "Réseau à cette heure"},
        "font": {"size": 11},
    }
    if basemap:
        span = max(float(np.ptp(track.longitude)), float(np.ptp(track.latitude)) * 1.4, 0.15)
        zoom = float(np.clip(8.6 - np.log2(span * 8.0), 4.5, 10.5))
        figure.update_layout(
            map={"style": "carto-positron", "center": {"lon": center_lon, "lat": center_lat}, "zoom": zoom},
            legend=legend,
            showlegend=True,
        )
        return _style(figure, 440).update_layout(margin={"l": 0, "r": 0, "t": 0, "b": 0})

    half_lon = max(float(np.ptp(track.longitude)) * 0.75, 1.2)
    half_lat = max(float(np.ptp(track.latitude)) * 0.75, 0.8)
    aspect = 1.0 / np.cos(np.radians(center_lat))
    figure.update_xaxes(
        title_text="Longitude",
        range=[center_lon - half_lon, center_lon + half_lon],
        showgrid=True,
        constrain="domain",
    )
    figure.update_yaxes(
        title_text="Latitude",
        range=[center_lat - half_lat, center_lat + half_lat],
        scaleanchor="x",
        scaleratio=aspect,
        showgrid=True,
    )
    figure.update_layout(legend=legend, showlegend=True, plot_bgcolor="#f6f8fa")
    return _style(figure, 440).update_layout(margin={"l": 48, "r": 12, "t": 8, "b": 40})


# ----------------------------------------------------------------- cross-section


def make_cross_section_figure(
    profiles: LineProfiles,
    risk: RiskAssessment,
    time_index: int,
    variable: str,
    cap_km: float,
    selected_point: int | None,
) -> go.Figure:
    metadata = VARIABLES[variable]
    factor = metadata.get("factor", 1.0)
    track = profiles.track
    distance = track.distance_km
    altitude_km = profiles.altitude_m / 1000.0
    values = profiles.field(variable)[time_index] * factor
    temperature = profiles.temperature_c[time_index]

    figure = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.64, 0.09, 0.27],
        vertical_spacing=0.035,
    )

    figure.add_trace(
        go.Heatmap(
            x=distance,
            y=altitude_km,
            z=values,
            customdata=profiles.pressure_hpa[time_index],
            colorscale=metadata["colorscale"],
            zmin=metadata.get("zmin"),
            zmax=metadata.get("zmax"),
            zmid=metadata.get("zmid"),
            colorbar={
                "title": {"text": f"{metadata['label']}<br>({metadata['unit']})", "side": "right"},
                "thickness": 14,
                "len": 0.62,
                "y": 1.0,
                "yanchor": "top",
                "x": 1.005,
            },
            hovertemplate=(
                "km %{x:.1f} · %{y:.2f} km d'altitude<br>"
                f"{metadata['label']}: %{{z:.1f}} {metadata['unit']}<br>"
                "Pression ≈ %{customdata:.0f} hPa<extra></extra>"
            ),
            connectgaps=False,
            hoverongaps=False,
            name=metadata["label"],
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Contour(
            x=distance,
            y=altitude_km,
            z=temperature,
            contours={"start": 0, "end": 0, "size": 1, "coloring": "lines", "showlabels": False},
            line={"color": "#111827", "width": 2.2, "dash": "dot"},
            showscale=False,
            hoverinfo="skip",
            name="Isotherme 0 °C",
            showlegend=True,
            connectgaps=False,
        ),
        row=1,
        col=1,
    )
    for level_index, level in enumerate(profiles.level_hpa):
        heights = profiles.level_height_m[time_index, level_index] / 1000.0
        if np.nanmedian(heights) > cap_km:
            continue
        figure.add_trace(
            go.Scatter(
                x=distance,
                y=heights,
                mode="lines",
                line={"color": "rgba(17,24,39,0.45)", "width": 1, "dash": "dash"},
                hovertemplate=f"Niveau ERA5 {level:.0f} hPa · %{{y:.2f}} km<extra></extra>",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
        figure.add_annotation(
            x=distance[-1],
            y=float(heights[-1]),
            text=f"{level:.0f} hPa",
            showarrow=False,
            xanchor="right",
            yanchor="bottom",
            font={"size": 10, "color": "rgba(17,24,39,0.7)"},
            bgcolor="rgba(255,255,255,0.6)",
            row=1,
            col=1,
        )
    figure.add_trace(
        go.Scatter(
            x=distance,
            y=track.terrain_m / 1000.0,
            mode="lines",
            line={"color": TERRAIN_LINE, "width": 1.5},
            fill="tozeroy",
            fillcolor=TERRAIN_FILL,
            name="Relief (ETOPO)",
            hovertemplate="km %{x:.1f} · relief %{y:.2f} km<extra></extra>",
        ),
        row=1,
        col=1,
    )

    precipitation = risk.precipitation[time_index]
    figure.add_trace(
        go.Heatmap(
            x=distance,
            y=[0],
            z=precipitation[None, :],
            zmin=0,
            zmax=len(PRECIPITATION_TYPES),
            colorscale=PRECIPITATION_COLORSCALE,
            showscale=False,
            hovertext=[[f"km {km:.1f}: {PRECIPITATION_TYPES[code]}" for km, code in zip(distance, precipitation)]],
            hoverinfo="text",
            name="Type de précipitation",
            xgap=0,
        ),
        row=2,
        col=1,
    )

    score = risk.score[time_index]
    peak = risk.peak_score_by_point
    diagnostics = risk.diagnostics
    hover_risk = [
        f"<b>km {km:.1f} · {category}</b><br>Indice: {value:.0f}/100 · {HAZARD_LABELS[str(hazard)]}<br>"
        f"Près du sol: {t:.1f} °C, HR {rh:.0f} %, vent {wind * 3.6:.0f} km/h<br>"
        f"Couche la plus chaude 0,3–3 km: {warm:.1f} °C<br>Type: {PRECIPITATION_TYPES[code]}"
        for km, category, value, hazard, t, rh, wind, warm, code in zip(
            distance,
            risk.category[time_index],
            np.nan_to_num(score),
            hazard_names(risk.dominant[time_index]),
            diagnostics.near_ground_temperature_c[time_index],
            diagnostics.near_ground_humidity[time_index],
            diagnostics.near_ground_wind_ms[time_index],
            diagnostics.warm_layer_max_c[time_index],
            precipitation,
        )
    ]
    for lower, upper, color in ((0, 25, "Faible"), (25, 50, "Modéré"), (50, 75, "Élevé"), (75, 100, "Critique")):
        figure.add_hrect(
            y0=lower,
            y1=upper,
            fillcolor=CATEGORY_COLORS[color],
            opacity=0.10,
            line_width=0,
            row=3,
            col=1,
        )
    figure.add_trace(
        go.Scatter(
            x=distance,
            y=peak,
            mode="lines",
            line={"color": "rgba(17,24,39,0.35)", "width": 1.5, "dash": "dot"},
            name="Maximum sur 48 h",
            hovertemplate="km %{x:.1f} · max 48 h: %{y:.0f}<extra></extra>",
        ),
        row=3,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=distance,
            y=score,
            mode="lines+markers",
            line={"color": "#111827", "width": 2},
            marker={
                "size": 7,
                "color": np.nan_to_num(score),
                "cmin": 0,
                "cmax": 100,
                "colorscale": RISK_COLORSCALE,
                "line": {"color": "#ffffff", "width": 1},
            },
            fill="tozeroy",
            fillcolor="rgba(17,24,39,0.06)",
            name="Indice à cette heure",
            hovertext=hover_risk,
            hoverinfo="text",
        ),
        row=3,
        col=1,
    )

    if selected_point is not None and 0 <= selected_point < len(distance):
        figure.add_vline(
            x=float(distance[selected_point]),
            line={"color": "#111827", "width": 1.5, "dash": "solid"},
            opacity=0.8,
        )

    figure.update_yaxes(title_text="Altitude (km)", range=[0, cap_km], row=1, col=1)
    figure.update_yaxes(title_text="Précip.", showticklabels=False, row=2, col=1)
    figure.update_yaxes(title_text="Indice", range=[0, 102], row=3, col=1, dtick=25)
    figure.update_xaxes(title_text="Distance le long du corridor (km) — A → B", row=3, col=1)
    figure.update_xaxes(range=[float(distance[0]), float(distance[-1])])
    figure.update_layout(
        legend={
            "orientation": "h",
            "y": 1.04,
            "x": 0,
            "yanchor": "bottom",
            "font": {"size": 11},
        },
        clickmode="event",
    )
    return _style(figure, 640).update_layout(margin={"l": 56, "r": 90, "t": 36, "b": 48})


# ------------------------------------------------------------------- hovmöller


def make_hovmoller_figure(profiles: LineProfiles, risk: RiskAssessment, time_index: int) -> go.Figure:
    distance = profiles.track.distance_km
    labels = [format_time(value, short=True) for value in profiles.times]
    hours = np.arange(len(profiles.times))
    categories = risk.category
    hazards = hazard_names(risk.dominant)
    hover = [
        [
            f"{labels[t]} · km {km:.1f}<br>Indice: {value:.0f}/100 · {categories[t, i]}<br>{HAZARD_LABELS[str(hazards[t, i])]}"
            for i, (km, value) in enumerate(zip(distance, np.nan_to_num(risk.score[t])))
        ]
        for t in range(len(hours))
    ]
    figure = go.Figure(
        go.Heatmap(
            x=distance,
            y=hours,
            z=risk.score,
            zmin=0,
            zmax=100,
            colorscale=RISK_COLORSCALE,
            colorbar={"title": {"text": "Indice"}, "thickness": 12, "tickvals": [12.5, 37.5, 62.5, 87.5],
                      "ticktext": ["Faible", "Modéré", "Élevé", "Critique"]},
            hovertext=hover,
            hoverinfo="text",
        )
    )
    figure.add_hline(y=time_index, line={"color": "#111827", "width": 2})
    figure.add_annotation(
        x=float(distance[0]),
        y=time_index,
        text="heure affichée",
        showarrow=False,
        xanchor="left",
        yanchor="bottom",
        font={"size": 10, "color": "#111827"},
        bgcolor="rgba(255,255,255,0.75)",
    )
    tick_indices = list(range(0, len(hours), 6))
    figure.update_yaxes(
        title_text="Échéance (heure locale)",
        tickvals=tick_indices,
        ticktext=[labels[i] for i in tick_indices],
        autorange="reversed",
    )
    figure.update_xaxes(title_text="Distance le long du corridor (km)")
    return _style(figure, 400)


# ------------------------------------------------------------- vertical profile


def make_profile_figure(profiles: LineProfiles, time_index: int, point_index: int, cap_km: float) -> go.Figure:
    altitude_km = profiles.altitude_m / 1000.0
    temperature = profiles.temperature_c[time_index, :, point_index]
    dewpoint = profiles.dewpoint_c[time_index, :, point_index]
    wind = profiles.wind_speed_ms[time_index, :, point_index] * 3.6
    terrain_km = profiles.track.terrain_m[point_index] / 1000.0
    km = profiles.track.distance_km[point_index]

    figure = make_subplots(specs=[[{"secondary_y": False}]])
    figure.add_hrect(y0=0, y1=terrain_km, fillcolor=TERRAIN_FILL, opacity=0.35, line_width=0)
    figure.add_vline(x=0, line={"color": "#111827", "width": 1.5, "dash": "dot"})
    figure.add_trace(
        go.Scatter(
            x=temperature,
            y=altitude_km,
            mode="lines",
            line={"color": "#c0392b", "width": 2.5},
            name="Température",
            hovertemplate="%{y:.2f} km · %{x:.1f} °C<extra>Température</extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=dewpoint,
            y=altitude_km,
            mode="lines",
            line={"color": "#2e86c1", "width": 2, "dash": "dash"},
            name="Point de rosée",
            hovertemplate="%{y:.2f} km · %{x:.1f} °C<extra>Point de rosée</extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=wind,
            y=altitude_km,
            mode="lines",
            line={"color": "#7d8c99", "width": 1.5},
            name="Vent (km/h)",
            xaxis="x2",
            hovertemplate="%{y:.2f} km · %{x:.0f} km/h<extra>Vent</extra>",
        )
    )
    for level_index, level in enumerate(profiles.level_hpa):
        height = profiles.level_height_m[time_index, level_index, point_index] / 1000.0
        if height <= cap_km:
            figure.add_hline(y=height, line={"color": "rgba(17,24,39,0.25)", "width": 1, "dash": "dash"})
            figure.add_annotation(
                x=1,
                xref="paper",
                y=height,
                text=f"{level:.0f} hPa",
                showarrow=False,
                xanchor="right",
                yanchor="bottom",
                font={"size": 10, "color": "rgba(17,24,39,0.6)"},
            )
    figure.update_layout(
        xaxis={"title": "Température / point de rosée (°C)", "range": [-40, 12]},
        xaxis2={
            "title": "Vent (km/h)",
            "overlaying": "x",
            "side": "top",
            "range": [0, 150],
            "showgrid": False,
        },
        yaxis={"title": "Altitude (km)", "range": [0, cap_km]},
        legend={"orientation": "h", "y": -0.22, "x": 0},
        title={"text": f"Sondage au km {km:.1f}", "x": 0.02, "font": {"size": 13}},
    )
    return _style(figure, 400).update_layout(margin={"l": 56, "r": 24, "t": 70, "b": 60})
