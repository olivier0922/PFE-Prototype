import numpy as np

from src.config import STANDARD_LAPSE_RATE_K_PER_M
from src.interpolation import dewpoint_from_relative_humidity, interpolate_columns


def _columns():
    # Two columns, three levels; heights increase along axis 0 (level).
    heights = np.array([[200.0, 300.0], [1500.0, 1500.0], [3000.0, 3200.0]])
    values = np.array([[10.0, 0.0], [-3.0, -6.0], [-16.0, -20.0]])
    return heights, values


def test_interpolation_is_linear_between_levels() -> None:
    heights, values = _columns()

    result = interpolate_columns(heights, values, np.array([850.0]))

    assert result.shape == (1, 2)
    assert np.isclose(result[0, 0], 10.0 + (-3.0 - 10.0) * (850.0 - 200.0) / 1300.0)
    assert np.isclose(result[0, 1], 0.0 + (-6.0 - 0.0) * (850.0 - 300.0) / 1200.0)


def test_extrapolation_below_lowest_level_hold_and_lapse() -> None:
    heights, values = _columns()
    targets = np.array([50.0])

    held = interpolate_columns(heights, values, targets, below_ground="hold")
    lapsed = interpolate_columns(heights, values, targets, below_ground="lapse")

    assert np.allclose(held[0], values[0])
    assert np.isclose(lapsed[0, 0], 10.0 + STANDARD_LAPSE_RATE_K_PER_M * 150.0)
    assert np.isclose(lapsed[0, 1], 0.0 + STANDARD_LAPSE_RATE_K_PER_M * 250.0)


def test_targets_above_top_level_are_nan_and_grid_targets_broadcast() -> None:
    heights, values = _columns()
    grid = np.array([1500.0, 3100.0, 5000.0]).reshape(-1, 1)

    result = interpolate_columns(heights, values, grid)

    assert result.shape == (3, 2)
    assert np.allclose(result[0], [-3.0, -6.0])
    assert np.isnan(result[1, 0]) and np.isfinite(result[1, 1])
    assert np.isnan(result[2]).all()


def test_dewpoint_equals_temperature_when_saturated() -> None:
    temperature = np.array([-5.0, 0.0, 10.0])
    assert np.allclose(dewpoint_from_relative_humidity(temperature, np.full(3, 100.0)), temperature, atol=0.05)
    assert np.all(dewpoint_from_relative_humidity(temperature, np.full(3, 50.0)) < temperature)
