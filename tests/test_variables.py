import numpy as np

from src.network import track_bearing_deg
from src.variables import REGISTRY, along_track_wind, available_variables, cross_track_wind


def test_wind_components_relative_to_the_corridor() -> None:
    east, north = np.radians(90.0), np.radians(0.0)
    # A westerly wind (blowing towards the east) along an eastbound corridor.
    assert np.isclose(along_track_wind(10.0, 0.0, east), 10.0)
    assert np.isclose(cross_track_wind(10.0, 0.0, east), 0.0, atol=1e-9)
    # The same wind crosses a northbound corridor from left to right.
    assert np.isclose(along_track_wind(10.0, 0.0, north), 0.0, atol=1e-9)
    assert np.isclose(cross_track_wind(10.0, 0.0, north), 10.0)


def test_track_bearing_follows_the_line() -> None:
    eastbound = track_bearing_deg(np.array([-72.0, -71.9, -71.8]), np.array([46.0, 46.0, 46.0]))
    northbound = track_bearing_deg(np.array([-72.0, -72.0, -72.0]), np.array([46.0, 46.1, 46.2]))
    assert np.allclose(eastbound, 90.0, atol=0.2)
    assert np.allclose(np.mod(northbound + 180.0, 360.0) - 180.0, 0.0, atol=1e-6)


def test_optional_variables_depend_on_the_dataset() -> None:
    base = available_variables(["t", "r", "u", "v", "z"])
    assert "temperature" in base and "wind_cross" in base
    assert "vorticity" not in base
    assert "vorticity" in available_variables(["t", "r", "u", "v", "z", "vo"])


def test_registry_computes_on_a_grid() -> None:
    shape = (2, 3, 4)
    grid = {
        "t": np.full(shape, -2.0),
        "r": np.full(shape, 100.0),
        "u": np.full(shape, 3.0),
        "v": np.full(shape, 4.0),
        "p": np.full(shape, 1000.0),
        "bearing": np.zeros(shape[-1]),
        "vo": np.full(shape, 2e-5),
    }
    for key, variable in REGISTRY.items():
        values = variable.compute(grid)
        assert np.shape(values) == shape, key
    assert np.allclose(REGISTRY["wind"].compute(grid), 18.0)  # 5 m/s
    assert np.allclose(REGISTRY["dewpoint"].compute(grid), -2.0, atol=0.05)
    assert np.allclose(REGISTRY["theta"].compute(grid), 271.15)
