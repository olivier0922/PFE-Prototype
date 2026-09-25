from __future__ import annotations

import shutil
import zipfile
from functools import cached_property
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import xarray as xr
from rasterio.windows import from_bounds
from shapely.geometry import box

from .config import (
    ERA5_ARCHIVE,
    ERA5_DIR,
    EXPECTED_ERA5_VARIABLES,
    LINES_PATH,
    TERRAIN_PATH,
)
from .network import Corridor, build_corridors


class DataValidationError(RuntimeError):
    """Raised when a supplied source cannot support the prototype."""


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            member_path = (destination / member.filename).resolve()
            if destination not in member_path.parents and member_path != destination:
                raise DataValidationError(f"Chemin non sécuritaire dans l'archive: {member.filename}")
        zipped.extractall(destination)


def prepare_data(archive: Path = ERA5_ARCHIVE, zarr_dir: Path = ERA5_DIR) -> Path:
    """Extract the supplied Zarr archive once and return its directory."""
    metadata = zarr_dir / ".zmetadata"
    if metadata.exists():
        return zarr_dir
    if not archive.exists():
        raise DataValidationError(f"Archive ERA5 introuvable: {archive}")
    if zarr_dir.exists():
        shutil.rmtree(zarr_dir)
    _safe_extract(archive, archive.parent)
    if not metadata.exists():
        raise DataValidationError(
            f"L'archive ne contient pas le groupe Zarr attendu ({zarr_dir.name}/.zmetadata)."
        )
    return zarr_dir


def validate_sources() -> dict[str, object]:
    """Validate the three supplied data sources and summarize their contents."""
    zarr_dir = prepare_data()
    for path in (LINES_PATH, TERRAIN_PATH):
        if not path.exists():
            raise DataValidationError(f"Fichier introuvable: {path}")

    dataset = xr.open_zarr(zarr_dir, consolidated=True, chunks=None)
    missing = EXPECTED_ERA5_VARIABLES.difference(dataset.data_vars)
    if missing:
        raise DataValidationError(f"Variables ERA5 manquantes: {sorted(missing)}")
    if not {"time", "latitude", "longitude", "level"}.issubset(dataset.dims):
        raise DataValidationError(f"Dimensions ERA5 invalides: {dict(dataset.sizes)}")

    lines = gpd.read_file(LINES_PATH)
    if lines.empty or not lines.geometry.geom_type.isin(["LineString", "MultiLineString"]).all():
        raise DataValidationError("Le GeoJSON doit contenir des LineString.")
    if lines.crs is None:
        raise DataValidationError("Le GeoJSON ne déclare aucun système de coordonnées.")

    with rasterio.open(TERRAIN_PATH) as terrain:
        if terrain.crs is None:
            raise DataValidationError("Le GeoTIFF ne déclare aucun système de coordonnées.")
        terrain_shape = (terrain.height, terrain.width)
        terrain_crs = terrain.crs.to_string()

    summary = {
        "era5_dimensions": dict(dataset.sizes),
        "era5_variables": sorted(dataset.data_vars),
        "levels_hpa": [int(value) for value in dataset.level.values],
        "segment_count": len(lines),
        "line_count": int(lines["LIGNE"].nunique()),
        "terrain_shape": terrain_shape,
        "terrain_crs": terrain_crs,
    }
    dataset.close()
    return summary


class TerrainSampler:
    """In-memory window of the ETOPO raster covering the weather domain."""

    def __init__(self, path: Path, bounds: tuple[float, float, float, float], margin: float = 0.5) -> None:
        lon_min, lat_min, lon_max, lat_max = bounds
        with rasterio.open(path) as source:
            left = max(lon_min - margin, source.bounds.left)
            right = min(lon_max + margin, source.bounds.right)
            bottom = max(lat_min - margin, source.bounds.bottom)
            top = min(lat_max + margin, source.bounds.top)
            window = from_bounds(left, bottom, right, top, source.transform).round_offsets().round_lengths()
            self.elevation = source.read(1, window=window, masked=True).filled(np.nan).astype(float)
            self.transform = source.window_transform(window)
            nodata = source.nodata
        if nodata is not None:
            self.elevation[self.elevation == nodata] = np.nan

    def sample(self, longitudes: np.ndarray, latitudes: np.ndarray) -> np.ndarray:
        inverse = ~self.transform
        lon = np.asarray(longitudes, dtype=float)
        lat = np.asarray(latitudes, dtype=float)
        cols = inverse.a * lon + inverse.b * lat + inverse.c
        rows = inverse.d * lon + inverse.e * lat + inverse.f
        rows = np.clip(np.floor(rows).astype(int), 0, self.elevation.shape[0] - 1)
        cols = np.clip(np.floor(cols).astype(int), 0, self.elevation.shape[1] - 1)
        values = self.elevation[rows, cols]
        # Water surfaces (negative bathymetry) are represented at mean sea level.
        return np.where(np.isfinite(values), np.maximum(values, 0.0), 0.0)


class DataRepository:
    """Lazy access to ERA5, corridors and terrain."""

    def __init__(self) -> None:
        prepare_data()

    @cached_property
    def weather(self) -> xr.Dataset:
        dataset = xr.open_zarr(ERA5_DIR, consolidated=True, chunks=None)
        # Ascending latitude/longitude make xarray interpolation deterministic;
        # descending pressure means ascending altitude along the level axis.
        # Every variable of the file is kept: optional ones (vorticity, …)
        # become available in the cross-section through src/variables.py.
        dataset = dataset.sortby(["latitude", "longitude"]).sortby("level", ascending=False)
        dims = {"time", "level", "latitude", "longitude"}
        names = [name for name, array in dataset.data_vars.items() if set(array.dims) == dims]
        return dataset[names].load()

    @cached_property
    def domain_bounds(self) -> tuple[float, float, float, float]:
        weather = self.weather
        return (
            float(weather.longitude.min()),
            float(weather.latitude.min()),
            float(weather.longitude.max()),
            float(weather.latitude.max()),
        )

    @cached_property
    def terrain(self) -> TerrainSampler:
        return TerrainSampler(TERRAIN_PATH, self.domain_bounds)

    @cached_property
    def corridors(self) -> gpd.GeoDataFrame:
        segments = gpd.read_file(LINES_PATH).to_crs(4326)
        corridors = build_corridors(segments)
        domain = box(*self.domain_bounds)
        inside = corridors.geometry.within(domain)
        return corridors[inside].reset_index(drop=True)

    @cached_property
    def corridor_index(self) -> dict[str, int]:
        return {cid: idx for idx, cid in enumerate(self.corridors["corridor_id"])}

    @cached_property
    def voltages(self) -> list[int]:
        return sorted({int(v) for v in self.corridors["voltage_kv"]}, reverse=True)

    @cached_property
    def regions(self) -> list[str]:
        names: set[str] = set()
        for value in self.corridors["region"]:
            names.update(part for part in str(value).split("/") if part)
        return sorted(names)

    def get_corridor(self, corridor_id: str) -> Corridor:
        try:
            row = self.corridors.iloc[self.corridor_index[str(corridor_id)]]
        except KeyError as error:
            raise KeyError(f"Corridor inconnu: {corridor_id}") from error
        return Corridor(
            corridor_id=str(row["corridor_id"]),
            line_number=str(row["line_number"]),
            voltage_kv=int(row["voltage_kv"]),
            part_index=int(row["part_index"]),
            part_count=int(row["part_count"]),
            length_km=float(row["length_km"]),
            region=str(row["region"]),
            direction=str(row["direction"]),
            label=str(row["label"]),
            geometry=row.geometry,
        )

    def filter_corridors(self, voltages: list[int] | None, region: str | None) -> gpd.GeoDataFrame:
        frame = self.corridors
        if voltages:
            frame = frame[frame["voltage_kv"].isin([int(v) for v in voltages])]
        if region:
            frame = frame[frame["region"].astype(str).str.contains(region, regex=False)]
        return frame

    def sample_terrain(self, longitudes: np.ndarray, latitudes: np.ndarray) -> np.ndarray:
        return self.terrain.sample(longitudes, latitudes)

    @property
    def times(self) -> np.ndarray:
        return self.weather.time.values

    def close(self) -> None:
        if "weather" in self.__dict__:
            self.weather.close()
