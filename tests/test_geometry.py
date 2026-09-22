import numpy as np
from shapely.geometry import LineString

from src.network import chain_parts, sample_line_geometry


def test_sample_line_geometry_uses_monotonic_geodesic_distance() -> None:
    line = LineString([(-74.0, 45.0), (-74.0, 46.0)])

    distance, longitude, latitude = sample_line_geometry(line, spacing_km=10, min_points=2)

    assert 110 < distance[-1] < 112  # one degree of latitude
    assert np.all(np.diff(distance) > 0)
    assert len(distance) == len(longitude) == len(latitude)
    assert longitude[0] == -74.0 and latitude[0] == 45.0
    assert longitude[-1] == -74.0 and latitude[-1] == 46.0


def test_chain_parts_bridges_small_gaps_and_keeps_distant_pieces_apart() -> None:
    first = LineString([(-73.0, 45.0), (-73.0, 45.1)])
    close = LineString([(-73.0, 45.105), (-73.0, 45.2)])  # ~550 m gap
    far = LineString([(-72.0, 46.0), (-72.0, 46.1)])  # ~130 km away

    corridors = chain_parts([first, close, far], tolerance_m=1500)

    assert len(corridors) == 2
    longest = max(corridors, key=lambda part: part.length)
    assert len(longest.coords) == 4
    assert longest.coords[0] == (-73.0, 45.0) and longest.coords[-1] == (-73.0, 45.2)
