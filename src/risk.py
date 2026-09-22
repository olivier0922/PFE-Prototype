"""Synthetic weather-hazard indicator for transmission corridors.

The indicator is deliberately simple and transparent. Every hazard is scored
between 0 and 1 from quantities that the supplied ERA5 pressure-level file can
actually provide (temperature, humidity, wind, geopotential). It is an
*atmospheric potential*, not a validated forecast of ice accretion: the data
contain neither precipitation nor liquid water. Thresholds live here so that
Hydro-Québec meteorologists can calibrate them against observations and
outage history.

All functions are shape-agnostic: they accept arrays of any common shape.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .interpolation import ColumnDiagnostics


HAZARDS = ("verglas", "givrage", "vent", "froid")
HAZARD_LABELS = {
    "verglas": "Pluie verglaçante",
    "givrage": "Givrage / neige collante",
    "vent": "Vent fort",
    "froid": "Froid extrême",
    "aucun": "Aucun aléa notable",
}

CATEGORY_BOUNDS = ((0, 25, "Faible"), (25, 50, "Modéré"), (50, 75, "Élevé"), (75, 101, "Critique"))
CATEGORY_COLORS = {
    "Faible": "#4f9d69",
    "Modéré": "#e0b53a",
    "Élevé": "#e07b39",
    "Critique": "#b83232",
    "Indisponible": "#9aa5b1",
}

PRECIPITATION_TYPES = ("Sec", "Neige", "Neige mouillée", "Grésil", "Verglas", "Pluie")
PRECIPITATION_COLORS = {
    "Sec": "#e6ebf0",
    "Neige": "#a9d3f5",
    "Neige mouillée": "#4c8fd6",
    "Grésil": "#8f6bd9",
    "Verglas": "#d63b3b",
    "Pluie": "#3f9c62",
}


def _ramp(values: np.ndarray, zero_at: float, one_at: float) -> np.ndarray:
    """Linear ramp from 0 at ``zero_at`` to 1 at ``one_at`` (either direction)."""
    with np.errstate(invalid="ignore"):
        return np.clip((values - zero_at) / (one_at - zero_at), 0.0, 1.0)


def hazard_scores(d: ColumnDiagnostics) -> dict[str, np.ndarray]:
    t = d.near_ground_temperature_c
    warm = d.warm_layer_max_c
    wind_kmh = d.near_ground_wind_ms * 3.6

    # Freezing rain: sub-zero air near the conductors, a melting layer aloft and
    # a moist lower troposphere. Very cold surface layers favour ice pellets.
    surface_cold = np.minimum(_ramp(t, 0.5, -1.0), _ramp(t, -14.0, -8.0))
    warm_nose = _ramp(warm, 0.0, 3.0)
    moisture = _ramp(d.low_level_humidity, 60.0, 85.0)
    verglas = surface_cold * warm_nose * moisture

    # Rime icing / sticky snow: in-cloud, near-saturated air close to 0 °C.
    # Accretion is slower than freezing rain, so the hazard is capped at 0.6
    # (at most "Élevé" on its own) unless wind adds to the load.
    thermal_window = np.minimum(_ramp(t, -7.0, -3.0), _ramp(t, 2.0, 0.5))
    saturation = _ramp(d.near_ground_humidity, 85.0, 98.0)
    givrage = 0.6 * thermal_window * saturation

    # Strong wind on the conductors (≈100 m above ground). Pressure-level winds
    # under-represent gusts, hence the fairly low thresholds.
    vent = _ramp(wind_kmh, 40.0, 90.0)

    # Extreme cold affects conductors, hardware and electrical demand.
    froid = _ramp(t, -20.0, -35.0)

    return {"verglas": verglas, "givrage": givrage, "vent": vent, "froid": froid}


def combine_hazards(scores: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(score 0–100, dominant hazard index)``.

    The combined index follows the dominant hazard and is slightly increased
    when several hazards coincide (for instance wind on iced conductors).
    """
    stacked = np.stack([scores[name] for name in HAZARDS], axis=0)
    with np.errstate(invalid="ignore"):
        maximum = np.nanmax(stacked, axis=0)
        others = np.nansum(stacked, axis=0) - maximum
        score = 100.0 * np.clip(maximum + 0.15 * others, 0.0, 1.0)
        dominant = np.nanargmax(np.where(np.isnan(stacked), -1.0, stacked), axis=0)
    invalid = ~np.isfinite(maximum)
    score = np.where(invalid, np.nan, score)
    dominant = np.where(invalid | (maximum < 0.05), -1, dominant)
    return score, dominant


def categorize(score: np.ndarray) -> np.ndarray:
    category = np.full(np.shape(score), "Indisponible", dtype=object)
    for lower, upper, label in CATEGORY_BOUNDS:
        category[np.isfinite(score) & (score >= lower) & (score < upper)] = label
    return category


def hazard_names(dominant: np.ndarray) -> np.ndarray:
    names = np.array(["aucun", *HAZARDS], dtype=object)
    return names[np.asarray(dominant) + 1]


def precipitation_type(d: ColumnDiagnostics) -> np.ndarray:
    """Simplified precipitation-type diagnostic from the thermal structure.

    Returns integer codes indexing :data:`PRECIPITATION_TYPES`. A column is
    considered *dry* when the lower troposphere is far from saturation.
    """
    t = d.near_ground_temperature_c
    warm = d.warm_layer_max_c
    code = np.full(np.shape(t), 0, dtype=int)
    wet = d.low_level_humidity >= 75.0
    with np.errstate(invalid="ignore"):
        rain = wet & (t > 1.0)
        mixed = wet & (t > 0.0) & (t <= 1.0)
        freezing = wet & (t <= 0.0) & (warm >= 1.0) & (t >= -8.0)
        pellets = wet & (t <= 0.0) & (warm > 0.0) & ~freezing
        snow = wet & (t <= 0.0) & (warm <= 0.0)
    code[snow] = 1
    code[mixed] = 2
    code[pellets] = 3
    code[freezing] = 4
    code[rain] = 5
    return np.where(np.isfinite(t), code, 0)


@dataclass(frozen=True)
class RiskAssessment:
    """Hazard indicator along a corridor, arrays of shape ``(time, point)``."""

    score: np.ndarray
    category: np.ndarray
    dominant: np.ndarray  # -1 for none, else index into HAZARDS
    precipitation: np.ndarray  # codes into PRECIPITATION_TYPES
    components: dict[str, np.ndarray]
    diagnostics: ColumnDiagnostics

    @property
    def peak_score_by_point(self) -> np.ndarray:
        with np.errstate(all="ignore"):
            return np.nanmax(np.where(np.isnan(self.score), -1.0, self.score), axis=0)

    @property
    def peak_time_index_by_point(self) -> np.ndarray:
        return np.argmax(np.where(np.isnan(self.score), -1.0, self.score), axis=0)


def assess_risk(diagnostics: ColumnDiagnostics) -> RiskAssessment:
    components = hazard_scores(diagnostics)
    score, dominant = combine_hazards(components)
    return RiskAssessment(
        score=score,
        category=categorize(score),
        dominant=dominant,
        precipitation=precipitation_type(diagnostics),
        components=components,
        diagnostics=diagnostics,
    )


def exposed_segments(
    distance_km: np.ndarray,
    risk: RiskAssessment,
    times: np.ndarray,
    threshold: float = 50.0,
    max_rows: int = 12,
) -> pd.DataFrame:
    """Contiguous stretches whose peak indicator over the period exceeds ``threshold``."""
    peak = risk.peak_score_by_point
    peak_time = risk.peak_time_index_by_point
    active = peak >= threshold
    if not active.any():
        threshold = 25.0
        active = peak >= threshold
    rows: list[dict[str, object]] = []
    index = 0
    while index < len(active):
        if not active[index]:
            index += 1
            continue
        start = index
        while index + 1 < len(active) and active[index + 1]:
            index += 1
        end = index
        worst = start + int(np.argmax(peak[start : end + 1]))
        worst_time = int(peak_time[worst])
        rows.append(
            {
                "km_debut": float(distance_km[start]),
                "km_fin": float(distance_km[end]),
                "longueur_km": float(distance_km[end] - distance_km[start]),
                "risque_max": float(peak[worst]),
                "categorie": str(risk.category[worst_time, worst]),
                "alea": HAZARD_LABELS[str(hazard_names(risk.dominant[worst_time, worst]))],
                "precipitation": PRECIPITATION_TYPES[int(risk.precipitation[worst_time, worst])],
                "temperature_c": float(risk.diagnostics.near_ground_temperature_c[worst_time, worst]),
                "vent_kmh": float(risk.diagnostics.near_ground_wind_ms[worst_time, worst] * 3.6),
                "pic_index": worst_time,
                "pic_time": pd.Timestamp(times[worst_time]),
            }
        )
        index += 1
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values("risque_max", ascending=False).head(max_rows).reset_index(drop=True)
