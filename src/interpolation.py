"""Projection of ERA5 pressure-level fields onto a transmission corridor.

Pipeline for one corridor:

1. sample the corridor at regular geodesic distances;
2. bilinear (latitude/longitude) interpolation of every ERA5 field to the
   sampled points, for all time steps at once;
3. convert geopotential to height and interpolate **linearly between the
   pressure levels** onto a regular altitude grid. Because the height of each
   level is itself linear in pressure between two consecutive levels, linear
   interpolation in pressure and linear interpolation in height give exactly
   the same values on a bracket; the altitude grid is therefore a faithful
   rendering of the requested linear pressure interpolation;
4. mask the atmosphere below the ETOPO terrain.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import xarray as xr

from .config import (
    ALTITUDE_STEP_M,
    GRAVITY_M_S2,
    NEAR_GROUND_OFFSET_M,
    STANDARD_LAPSE_RATE_K_PER_M,
)
from .data import DataRepository
from .network import Corridor, sample_line_geometry, track_bearing_deg
from .variables import Variable, available_variables, dewpoint_from_relative_humidity


@dataclass(frozen=True)
class TrackSamples:
    distance_km: np.ndarray
    longitude: np.ndarray
    latitude: np.ndarray
    terrain_m: np.ndarray
    bearing_deg: np.ndarray


@dataclass(frozen=True)
class ColumnDiagnostics:
    """Per-column quantities feeding the hazard model, shape ``(time, point)``."""

    near_ground_temperature_c: np.ndarray
    near_ground_dewpoint_c: np.ndarray
    near_ground_humidity: np.ndarray
    near_ground_wind_ms: np.ndarray
    warm_layer_max_c: np.ndarray  # warmest temperature 300–3000 m above ground
    low_level_humidity: np.ndarray  # mean relative humidity 0–1500 m above ground


@dataclass(frozen=True)
class LineProfiles:
    """Atmospheric columns along a corridor for every ERA5 time step.

    Gridded arrays have shape ``(time, altitude, point)``. ``fields`` holds
    every variable of :mod:`src.variables` available for this dataset, in
    display units.
    """

    corridor: Corridor
    track: TrackSamples
    times: np.ndarray
    altitude_m: np.ndarray
    level_hpa: np.ndarray
    level_height_m: np.ndarray  # (time, level, point)
    variables: dict[str, Variable]
    fields: dict[str, np.ndarray]
    u_ms: np.ndarray
    v_ms: np.ndarray
    pressure_hpa: np.ndarray
    diagnostics: ColumnDiagnostics

    def field(self, name: str) -> np.ndarray:
        return self.fields[name]

    @property
    def temperature_c(self) -> np.ndarray:
        return self.fields["temperature"]

    @property
    def dewpoint_c(self) -> np.ndarray:
        return self.fields["dewpoint"]


def interpolate_columns(
    heights: np.ndarray,
    values: np.ndarray,
    targets: np.ndarray,
    below_ground: str = "hold",
) -> np.ndarray:
    """Vectorised linear interpolation of vertical columns onto target heights.

    ``heights`` and ``values`` have shape ``(level, ...)`` with heights
    increasing along axis 0. ``targets`` broadcasts to ``(target, ...)``.
    Above the top level the result is NaN. Below the lowest level (ERA5's
    1000 hPa surface floats 100–300 m above sea level) values are either held
    constant (``"hold"``) or, for temperature, extrapolated with the standard
    lapse rate (``"lapse"``).
    """
    heights = np.asarray(heights, dtype=float)
    values = np.asarray(values, dtype=float)
    level_count = heights.shape[0]
    trailing = heights.shape[1:]
    targets = np.asarray(targets, dtype=float)
    if targets.ndim == 1:
        targets = targets.reshape((-1,) + (1,) * len(trailing))
    target_count = targets.shape[0]
    target_shape = (target_count,) + trailing
    target_grid = np.broadcast_to(targets, target_shape)

    below_count = (heights[None, ...] < target_grid[:, None, ...]).sum(axis=1)
    upper = np.clip(below_count, 1, level_count - 1)
    lower = upper - 1

    def gather(source: np.ndarray, index: np.ndarray) -> np.ndarray:
        expanded = np.broadcast_to(source[None, ...], (target_count,) + source.shape)
        return np.take_along_axis(expanded, index[:, None], axis=1)[:, 0]

    h_lo, h_hi = gather(heights, lower), gather(heights, upper)
    v_lo, v_hi = gather(values, lower), gather(values, upper)

    with np.errstate(invalid="ignore", divide="ignore"):
        weight = (target_grid - h_lo) / (h_hi - h_lo)
        result = v_lo + weight * (v_hi - v_lo)

    lowest = heights[0][None, ...]
    if below_ground == "lapse":
        extrapolated = values[0][None, ...] + STANDARD_LAPSE_RATE_K_PER_M * (lowest - target_grid)
    else:
        extrapolated = np.broadcast_to(values[0][None, ...], target_shape)
    result = np.where(target_grid < lowest, extrapolated, result)
    return np.where(target_grid > heights[-1][None, ...], np.nan, result)


def column_diagnostics(
    level_height: np.ndarray,
    temperature: np.ndarray,
    humidity: np.ndarray,
    u_wind: np.ndarray,
    v_wind: np.ndarray,
    terrain_m: np.ndarray,
) -> ColumnDiagnostics:
    """Hazard inputs from level data of shape ``(time, level, point)``."""
    heights = np.moveaxis(level_height, 1, 0)
    t_levels = np.moveaxis(temperature, 1, 0)
    rh_levels = np.moveaxis(humidity, 1, 0)

    near = (terrain_m + NEAR_GROUND_OFFSET_M)[None, None, :]
    near_t = interpolate_columns(heights, t_levels, near, "lapse")[0]
    near_rh = interpolate_columns(heights, rh_levels, near, "hold")[0]
    near_u = interpolate_columns(heights, np.moveaxis(u_wind, 1, 0), near, "hold")[0]
    near_v = interpolate_columns(heights, np.moveaxis(v_wind, 1, 0), near, "hold")[0]

    warm_targets = terrain_m[None, None, :] + np.arange(300.0, 3001.0, 150.0)[:, None, None]
    low_targets = terrain_m[None, None, :] + np.arange(0.0, 1501.0, 250.0)[:, None, None]
    with np.errstate(all="ignore"):
        warm_max = np.nanmax(interpolate_columns(heights, t_levels, warm_targets, "lapse"), axis=0)
        low_mean = np.nanmean(interpolate_columns(heights, rh_levels, low_targets, "hold"), axis=0)
    return ColumnDiagnostics(
        near_ground_temperature_c=near_t,
        near_ground_dewpoint_c=dewpoint_from_relative_humidity(near_t, near_rh),
        near_ground_humidity=near_rh,
        near_ground_wind_ms=np.hypot(near_u, near_v),
        warm_layer_max_c=warm_max,
        low_level_humidity=low_mean,
    )


def spatial_columns(
    repository: DataRepository,
    longitude: np.ndarray,
    latitude: np.ndarray,
    extra: tuple[str, ...] = (),
) -> dict[str, np.ndarray]:
    """Bilinearly interpolate ERA5 to points; arrays of shape ``(time, level, point)``.

    ``extra`` ERA5 variables are returned under their short name, unconverted.
    """
    points_lon = xr.DataArray(np.asarray(longitude, dtype=float), dims="point")
    points_lat = xr.DataArray(np.asarray(latitude, dtype=float), dims="point")
    names = sorted({"z", "t", "r", "u", "v", *extra})
    columns = repository.weather[names].interp(longitude=points_lon, latitude=points_lat, method="linear")
    columns = columns.transpose("time", "level", "point")
    result = {
        "height": np.asarray(columns["z"], dtype=float) / GRAVITY_M_S2,
        "temperature": np.asarray(columns["t"], dtype=float) - 273.15,
        "humidity": np.clip(np.asarray(columns["r"], dtype=float), 0.0, 100.0),
        "u": np.asarray(columns["u"], dtype=float),
        "v": np.asarray(columns["v"], dtype=float),
    }
    for name in extra:
        result[name] = np.asarray(columns[name], dtype=float)
    return result


def build_line_profiles(repository: DataRepository, corridor_id: str) -> LineProfiles:
    corridor = repository.get_corridor(corridor_id)
    distance_km, longitude, latitude = sample_line_geometry(corridor.geometry)
    terrain_m = repository.sample_terrain(longitude, latitude)
    bearing_deg = track_bearing_deg(longitude, latitude)
    variables = available_variables(repository.weather.data_vars)
    extra = tuple(sorted(set().union(*(variable.requires for variable in variables.values()))))
    levels = spatial_columns(repository, longitude, latitude, extra)

    level_hpa = np.asarray(repository.weather.level.values, dtype=float)
    level_height = levels["height"]
    pressure = np.broadcast_to(level_hpa[None, :, None], level_height.shape)

    top = float(np.nanmax(level_height))
    altitude_m = np.arange(0.0, np.ceil(top / ALTITUDE_STEP_M) * ALTITUDE_STEP_M + 1.0, ALTITUDE_STEP_M)
    targets = altitude_m.reshape(-1, 1, 1)
    heights = np.moveaxis(level_height, 1, 0)
    under_terrain = altitude_m[None, :, None] < terrain_m[None, None, :]

    def onto_grid(values: np.ndarray, mode: str) -> np.ndarray:
        gridded = interpolate_columns(heights, np.moveaxis(values, 1, 0), targets, below_ground=mode)
        return np.where(under_terrain, np.nan, np.moveaxis(gridded, 0, 1))

    grid = {
        "t": onto_grid(levels["temperature"], "lapse"),
        "r": onto_grid(levels["humidity"], "hold"),
        "u": onto_grid(levels["u"], "hold"),
        "v": onto_grid(levels["v"], "hold"),
        "p": onto_grid(pressure, "hold"),
        "bearing": np.radians(bearing_deg),
    }
    for name in extra:
        grid[name] = onto_grid(levels[name], "hold")
    with np.errstate(invalid="ignore", divide="ignore"):
        fields = {key: np.asarray(variable.compute(grid), dtype=float) for key, variable in variables.items()}

    return LineProfiles(
        corridor=corridor,
        track=TrackSamples(
            distance_km=distance_km,
            longitude=longitude,
            latitude=latitude,
            terrain_m=terrain_m,
            bearing_deg=bearing_deg,
        ),
        times=repository.times,
        altitude_m=altitude_m,
        level_hpa=level_hpa,
        level_height_m=level_height,
        variables=variables,
        fields=fields,
        u_ms=grid["u"],
        v_ms=grid["v"],
        pressure_hpa=grid["p"],
        diagnostics=column_diagnostics(
            level_height, levels["temperature"], levels["humidity"], levels["u"], levels["v"], terrain_m
        ),
    )
