"""Registry of the atmospheric variables that can be drawn on a cross-section.

Adding a variable is a single :class:`Variable` entry in :data:`REGISTRY`:
the dashboard menus, the background, the isoline overlays and the hover
labels are all built from it.

``compute`` receives the fields interpolated onto the (time, altitude, point)
grid of a corridor:

- ``t`` temperature (°C), ``r`` relative humidity (%), ``u``/``v`` wind (m/s),
  ``p`` pressure (hPa);
- ``bearing`` azimuth of the corridor at each point (radians clockwise from
  north, A → B), shape ``(point,)``;
- any other ERA5 variable listed in ``requires`` under its short name, in the
  units of the source file.

A variable whose ``requires`` are missing from the loaded ERA5 dataset is
silently left out of the menus.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

import numpy as np


def dewpoint_from_relative_humidity(temperature_c: np.ndarray, relative_humidity: np.ndarray) -> np.ndarray:
    """Magnus formula (Alduchov & Eskridge 1996)."""
    a, b = 17.625, 243.04
    humidity = np.clip(np.asarray(relative_humidity, dtype=float), 0.5, 100.0)
    with np.errstate(invalid="ignore"):
        gamma = np.log(humidity / 100.0) + a * temperature_c / (b + temperature_c)
        return b * gamma / (a - gamma)


def along_track_wind(u: np.ndarray, v: np.ndarray, bearing: np.ndarray) -> np.ndarray:
    """Wind component along the corridor, positive when blowing from A towards B."""
    return u * np.sin(bearing) + v * np.cos(bearing)


def cross_track_wind(u: np.ndarray, v: np.ndarray, bearing: np.ndarray) -> np.ndarray:
    """Wind component perpendicular to the corridor, positive towards the right of A → B."""
    return u * np.cos(bearing) - v * np.sin(bearing)


@dataclass(frozen=True)
class Variable:
    key: str
    label: str
    unit: str
    compute: Callable[[Mapping[str, np.ndarray]], np.ndarray]
    requires: frozenset[str] = frozenset()  # ERA5 short names beyond t, r, u, v, z
    colorscale: str = "Viridis"
    zmin: float | None = None
    zmax: float | None = None
    zmid: float | None = None
    step: float = 1.0  # spacing of the isolines
    digits: int = 1


REGISTRY: dict[str, Variable] = {
    variable.key: variable
    for variable in (
        Variable(
            key="temperature",
            label="Température",
            unit="°C",
            compute=lambda f: f["t"],
            colorscale="RdBu_r",
            zmin=-24,
            zmax=8,
            zmid=0,
            step=2,
        ),
        Variable(
            key="theta",
            label="Température potentielle",
            unit="K",
            compute=lambda f: (f["t"] + 273.15) * (1000.0 / f["p"]) ** 0.2857,
            colorscale="Plasma",
            zmin=255,
            zmax=305,
            step=3,
            digits=0,
        ),
        Variable(
            key="dewpoint",
            label="Point de rosée",
            unit="°C",
            compute=lambda f: dewpoint_from_relative_humidity(f["t"], f["r"]),
            colorscale="RdBu_r",
            zmin=-30,
            zmax=6,
            zmid=0,
            step=2,
        ),
        Variable(
            key="humidity",
            label="Humidité relative",
            unit="%",
            compute=lambda f: f["r"],
            colorscale="YlGnBu",
            zmin=20,
            zmax=100,
            step=10,
            digits=0,
        ),
        Variable(
            key="wind",
            label="Vent",
            unit="km/h",
            compute=lambda f: np.hypot(f["u"], f["v"]) * 3.6,
            colorscale="Viridis",
            zmin=0,
            zmax=120,
            step=20,
            digits=0,
        ),
        Variable(
            key="wind_cross",
            label="Vent perpendiculaire à la ligne",
            unit="km/h",
            compute=lambda f: np.abs(cross_track_wind(f["u"], f["v"], f["bearing"])) * 3.6,
            colorscale="Viridis",
            zmin=0,
            zmax=100,
            step=20,
            digits=0,
        ),
        Variable(
            key="wind_along",
            label="Vent le long de la ligne (+ vers B)",
            unit="km/h",
            compute=lambda f: along_track_wind(f["u"], f["v"], f["bearing"]) * 3.6,
            colorscale="PuOr",
            zmin=-80,
            zmax=80,
            zmid=0,
            step=20,
            digits=0,
        ),
        Variable(
            key="vorticity",
            label="Tourbillon relatif",
            unit="10⁻⁵ s⁻¹",
            compute=lambda f: f["vo"] * 1e5,
            requires=frozenset({"vo"}),
            colorscale="RdBu_r",
            zmin=-30,
            zmax=30,
            zmid=0,
            step=5,
            digits=0,
        ),
    )
}


def available_variables(era5_variables: Iterable[str]) -> dict[str, Variable]:
    """Registry entries whose ERA5 inputs are present in the loaded dataset."""
    present = set(era5_variables)
    return {key: variable for key, variable in REGISTRY.items() if variable.requires <= present}
