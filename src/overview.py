"""Network-wide ranking of corridors by the hazard indicator."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import OVERVIEW_SPACING_KM
from .data import DataRepository
from .interpolation import column_diagnostics, spatial_columns
from .network import sample_line_geometry
from .risk import assess_risk


@dataclass(frozen=True)
class NetworkOverview:
    corridor_ids: list[str]
    score: np.ndarray  # (time, corridor) maximum indicator along each corridor
    dominant: np.ndarray  # (time, corridor) hazard index at the maximum
    map_paths: dict[str, tuple[np.ndarray, np.ndarray]]  # simplified lon/lat per corridor

    def peak(self) -> tuple[np.ndarray, np.ndarray]:
        """Peak score over the whole period and the time index of that peak."""
        filled = np.where(np.isnan(self.score), -1.0, self.score)
        return filled.max(axis=0), filled.argmax(axis=0)


def compute_network_overview(repository: DataRepository) -> NetworkOverview:
    corridors = repository.corridors
    owners: list[int] = []
    lons: list[np.ndarray] = []
    lats: list[np.ndarray] = []
    map_paths: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for index, row in enumerate(corridors.itertuples()):
        _, lon, lat = sample_line_geometry(row.geometry, spacing_km=OVERVIEW_SPACING_KM, min_points=3, max_points=80)
        owners.append(np.full(len(lon), index))
        lons.append(lon)
        lats.append(lat)
        simplified = np.asarray(row.geometry.simplify(0.01, preserve_topology=False).coords, dtype=float)
        map_paths[str(row.corridor_id)] = (simplified[:, 0], simplified[:, 1])

    owner = np.concatenate(owners)
    longitude = np.concatenate(lons)
    latitude = np.concatenate(lats)
    terrain = repository.sample_terrain(longitude, latitude)
    levels = spatial_columns(repository, longitude, latitude)
    diagnostics = column_diagnostics(
        levels["height"], levels["temperature"], levels["humidity"], levels["u"], levels["v"], terrain
    )
    risk = assess_risk(diagnostics)

    corridor_count = len(corridors)
    time_count = risk.score.shape[0]
    filled = np.where(np.isnan(risk.score), -1.0, risk.score)
    score = np.full((time_count, corridor_count), np.nan)
    dominant = np.full((time_count, corridor_count), -1, dtype=int)
    for index in range(corridor_count):
        columns = np.flatnonzero(owner == index)
        best = columns[np.argmax(filled[:, columns], axis=1)]
        score[:, index] = np.where(filled[np.arange(time_count), best] < 0, np.nan, filled[np.arange(time_count), best])
        dominant[:, index] = risk.dominant[np.arange(time_count), best]
    return NetworkOverview(
        corridor_ids=[str(cid) for cid in corridors["corridor_id"]],
        score=score,
        dominant=dominant,
        map_paths=map_paths,
    )
