import numpy as np
import pandas as pd

from src.interpolation import ColumnDiagnostics
from src.risk import HAZARDS, PRECIPITATION_TYPES, assess_risk, exposed_segments


def _diagnostics(**overrides) -> ColumnDiagnostics:
    # Four columns at one time step:
    # 0 classic freezing rain, 1 cold and dry, 2 windy above freezing, 3 wet snow.
    base = {
        "near_ground_temperature_c": np.array([[-3.0, -12.0, 4.0, -1.5]]),
        "near_ground_humidity": np.array([[92.0, 55.0, 70.0, 97.0]]),
        "near_ground_wind_ms": np.array([[8.0, 5.0, 26.0, 6.0]]),
        "warm_layer_max_c": np.array([[2.5, -6.0, 5.0, -2.0]]),
        "low_level_humidity": np.array([[90.0, 50.0, 65.0, 92.0]]),
    }
    base.update(overrides)
    base["near_ground_dewpoint_c"] = base["near_ground_temperature_c"] - 1.0
    return ColumnDiagnostics(**base)


def test_hazards_are_bounded_and_dominant_hazard_is_physical() -> None:
    risk = assess_risk(_diagnostics())

    assert np.nanmin(risk.score) >= 0 and np.nanmax(risk.score) <= 100
    dominant = [HAZARDS[index] if index >= 0 else None for index in risk.dominant[0]]
    assert dominant[0] == "verglas"
    assert risk.score[0, 0] >= 50
    assert risk.score[0, 1] < 25
    assert dominant[2] == "vent"
    assert dominant[3] == "givrage"


def test_precipitation_type_diagnostic() -> None:
    risk = assess_risk(_diagnostics())
    types = [PRECIPITATION_TYPES[code] for code in risk.precipitation[0]]
    assert types == ["Verglas", "Sec", "Sec", "Neige"]

    pellets = assess_risk(_diagnostics(near_ground_temperature_c=np.array([[-10.0, -12.0, 4.0, -1.5]])))
    assert PRECIPITATION_TYPES[pellets.precipitation[0, 0]] == "Grésil"


def test_exposed_segments_groups_contiguous_points_and_reports_peak_time() -> None:
    scores = np.array([[10.0, 60.0, 70.0, 20.0, 80.0], [15.0, 30.0, 40.0, 10.0, 90.0]])
    diagnostics = ColumnDiagnostics(
        near_ground_temperature_c=np.full(scores.shape, -2.0),
        near_ground_dewpoint_c=np.full(scores.shape, -3.0),
        near_ground_humidity=np.full(scores.shape, 90.0),
        near_ground_wind_ms=np.full(scores.shape, 5.0),
        warm_layer_max_c=np.full(scores.shape, 2.0),
        low_level_humidity=np.full(scores.shape, 90.0),
    )
    risk = assess_risk(diagnostics)
    object.__setattr__(risk, "score", scores)
    times = pd.date_range("1942-12-29", periods=2, freq="h").values

    table = exposed_segments(np.array([0.0, 10.0, 20.0, 30.0, 40.0]), risk, times)

    assert len(table) == 2
    assert table.iloc[0]["km_debut"] == 40.0 and table.iloc[0]["pic_index"] == 1
    assert table.iloc[1]["km_debut"] == 10.0 and table.iloc[1]["km_fin"] == 20.0
