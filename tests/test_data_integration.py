import numpy as np
import pytest

from src.data import DataRepository, validate_sources
from src.interpolation import build_line_profiles
from src.overview import compute_network_overview
from src.risk import assess_risk


@pytest.fixture(scope="module")
def repository() -> DataRepository:
    repo = DataRepository()
    yield repo
    repo.close()


def test_supplied_sources_are_valid() -> None:
    summary = validate_sources()
    assert summary["era5_dimensions"]["time"] == 48
    assert summary["levels_hpa"] == [1000, 850, 700, 500, 250]
    assert summary["segment_count"] == 3867


def test_corridors_are_continuous_and_inside_domain(repository: DataRepository) -> None:
    corridors = repository.corridors
    assert len(corridors) > 500
    assert (corridors["length_km"] >= 5).all()
    assert corridors.geometry.geom_type.eq("LineString").all()
    assert corridors["corridor_id"].is_unique


def test_profiles_and_risk_for_default_corridor(repository: DataRepository) -> None:
    corridor_id = repository.corridors.iloc[0]["corridor_id"]
    profiles = build_line_profiles(repository, corridor_id)
    risk = assess_risk(profiles.diagnostics)

    time_count, altitude_count, point_count = profiles.temperature_c.shape
    assert time_count == 48
    assert point_count == len(profiles.track.distance_km)
    assert altitude_count == len(profiles.altitude_m)
    # Below the terrain the atmosphere is masked; above it there is data.
    surface_index = np.searchsorted(profiles.altitude_m, profiles.track.terrain_m[0] + 150.0)
    assert np.isfinite(profiles.temperature_c[0, surface_index, 0])
    assert np.isnan(profiles.temperature_c[:, 0, profiles.track.terrain_m > 200.0]).all()
    assert risk.score.shape == (time_count, point_count)
    assert np.isfinite(risk.score).all()


def test_network_overview_ranks_every_corridor(repository: DataRepository) -> None:
    overview = compute_network_overview(repository)
    assert overview.score.shape == (48, len(repository.corridors))
    peak, when = overview.peak()
    assert peak.max() > 50  # the December 1942 event produces a clear signal
    assert 0 <= when.min() and when.max() < 48
