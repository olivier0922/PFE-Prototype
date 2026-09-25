"""Build continuous transmission corridors from the Hydro-Québec GIS segments.

The GeoJSON describes 3 867 segments. Operators reason in terms of *lines*
(``LIGNE`` attribute). Segments of a line are merged topologically, and pieces
separated by a small gap (missing vertex, station crossing) are chained so that
a cross-section can be drawn along the whole corridor.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Geod
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge

from .config import (
    CORRIDOR_GAP_TOLERANCE_M,
    MAX_TRACK_POINTS,
    MIN_CORRIDOR_LENGTH_KM,
    MIN_TRACK_POINTS,
    TRACK_SPACING_KM,
)


GEOD = Geod(ellps="WGS84")
EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lon1: np.ndarray, lat1: np.ndarray, lon2: np.ndarray, lat2: np.ndarray) -> np.ndarray:
    lon1, lat1, lon2, lat2 = (np.radians(np.asarray(v, dtype=float)) for v in (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _as_parts(geometry: object) -> list[LineString]:
    if geometry.geom_type == "LineString":
        return [geometry]
    return [part for part in geometry.geoms if part.geom_type == "LineString" and not part.is_empty]


def chain_parts(parts: list[LineString], tolerance_m: float = CORRIDOR_GAP_TOLERANCE_M) -> list[LineString]:
    """Greedily join line pieces whose end points lie within ``tolerance_m``.

    Two pieces are connected by a straight bridge segment. The result is a list
    of independent corridors (one when every piece could be chained).
    """
    pieces = [np.asarray(part.coords, dtype=float)[:, :2] for part in parts]
    while len(pieces) > 1:
        ends = np.array([[piece[0], piece[-1]] for piece in pieces])  # (n, 2, 2)
        flat = ends.reshape(-1, 2)  # index = 2*i + side
        distance = haversine_m(
            flat[:, None, 0], flat[:, None, 1], flat[None, :, 0], flat[None, :, 1]
        )
        owner = np.repeat(np.arange(len(pieces)), 2)
        distance[owner[:, None] == owner[None, :]] = np.inf
        best = int(np.argmin(distance))
        first, second = divmod(best, len(flat))
        if not np.isfinite(distance[first, second]) or distance[first, second] > tolerance_m:
            break
        i, side_i = divmod(first, 2)
        j, side_j = divmod(second, 2)
        left = pieces[i] if side_i == 1 else pieces[i][::-1]  # ends at its tail
        right = pieces[j] if side_j == 0 else pieces[j][::-1]  # starts at its head
        merged = np.vstack((left, right))
        pieces = [piece for k, piece in enumerate(pieces) if k not in (i, j)] + [merged]
    return [LineString(piece) for piece in pieces if len(piece) >= 2]


@dataclass(frozen=True)
class Corridor:
    corridor_id: str
    line_number: str
    voltage_kv: int
    part_index: int
    part_count: int
    length_km: float
    region: str
    direction: str
    label: str
    geometry: LineString


def build_corridors(segments: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Merge aerial segments into continuous corridors, one row per corridor."""
    frame = segments.copy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty]
    frame = frame[frame["TYPE"].astype(str).str.lower().str.startswith("a")]  # aérien
    frame["LIGNE"] = frame["LIGNE"].astype(str)

    records: list[dict[str, object]] = []
    for line_number, group in frame.groupby("LIGNE", sort=False):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            merged = linemerge(MultiLineString([geom for geom in group.geometry]))
        parts = chain_parts(_as_parts(merged))
        parts.sort(key=lambda part: -part.length)
        voltage = int(pd.to_numeric(group["TENSION_EXP"], errors="coerce").max() or 0)
        region = "/".join(sorted({str(v) for v in group["RESPONSA"].dropna()}))
        direction = "/".join(sorted({str(v) for v in group["DIRECTION_TE"].dropna()}))
        for index, part in enumerate(parts, start=1):
            coordinates = np.asarray(part.coords, dtype=float)
            length_km = float(
                np.sum(
                    GEOD.inv(
                        coordinates[:-1, 0], coordinates[:-1, 1], coordinates[1:, 0], coordinates[1:, 1]
                    )[2]
                )
                / 1000.0
            )
            if length_km < MIN_CORRIDOR_LENGTH_KM:
                continue
            corridor_id = f"{line_number}#{index}"
            label = f"Ligne {line_number} · {voltage} kV"
            if len(parts) > 1:
                label += f" · tronçon {index}/{len(parts)}"
            records.append(
                {
                    "corridor_id": corridor_id,
                    "line_number": line_number,
                    "voltage_kv": voltage,
                    "part_index": index,
                    "part_count": len(parts),
                    "length_km": length_km,
                    "region": region,
                    "direction": direction,
                    "label": label,
                    "geometry": part,
                }
            )
    corridors = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    return corridors.sort_values(["voltage_kv", "length_km"], ascending=[False, False]).reset_index(drop=True)


def sample_line_geometry(
    geometry: LineString,
    spacing_km: float = TRACK_SPACING_KM,
    min_points: int = MIN_TRACK_POINTS,
    max_points: int = MAX_TRACK_POINTS,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample a WGS84 LineString at regular geodesic distances.

    Returns ``(distance_km, longitude, latitude)``. Distances are cumulative
    along the ellipsoid, so the x-axis of a cross-section is a true kilometre
    count along the corridor.
    """
    coordinates = np.asarray(geometry.coords, dtype=float)[:, :2]
    if len(coordinates) < 2:
        raise ValueError("Une ligne doit contenir au moins deux sommets.")

    lon1, lat1 = coordinates[:-1, 0], coordinates[:-1, 1]
    lon2, lat2 = coordinates[1:, 0], coordinates[1:, 1]
    azimuth, _, lengths_m = GEOD.inv(lon1, lat1, lon2, lat2)
    lengths_m = np.maximum(np.asarray(lengths_m, dtype=float), 0.0)
    cumulative_m = np.concatenate(([0.0], np.cumsum(lengths_m)))
    total_m = float(cumulative_m[-1])
    if total_m <= 0:
        raise ValueError("La ligne a une longueur géodésique nulle.")

    point_count = int(np.ceil(total_m / (spacing_km * 1000.0))) + 1
    point_count = int(np.clip(point_count, min_points, max_points))
    targets_m = np.linspace(0.0, total_m, point_count)
    segment_indices = np.searchsorted(cumulative_m, targets_m, side="right") - 1
    segment_indices = np.clip(segment_indices, 0, len(lengths_m) - 1)
    offsets_m = targets_m - cumulative_m[segment_indices]

    sample_lon, sample_lat, _ = GEOD.fwd(
        coordinates[segment_indices, 0],
        coordinates[segment_indices, 1],
        azimuth[segment_indices],
        offsets_m,
    )
    sample_lon = np.asarray(sample_lon, dtype=float)
    sample_lat = np.asarray(sample_lat, dtype=float)
    sample_lon[0], sample_lat[0] = coordinates[0]
    sample_lon[-1], sample_lat[-1] = coordinates[-1]
    return targets_m / 1000.0, sample_lon, sample_lat


def track_bearing_deg(longitude: np.ndarray, latitude: np.ndarray) -> np.ndarray:
    """Local azimuth of a sampled track (degrees clockwise from north, towards the end)."""
    lon = np.asarray(longitude, dtype=float)
    lat = np.asarray(latitude, dtype=float)
    count = len(lon)
    if count < 2:
        return np.zeros(count)
    before = np.r_[0, np.arange(count - 1)]
    after = np.r_[np.arange(1, count), count - 1]
    azimuth, _, _ = GEOD.inv(lon[before], lat[before], lon[after], lat[after])
    return np.mod(np.asarray(azimuth, dtype=float), 360.0)
