"""Write a fictitious transmission network for demos.

The paths are random polylines inside the ERA5 domain. Line numbers, regions
and geometry do not come from the Hydro-Québec geographic database.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "lignes_demo.geojson"

# Kept inside the weather grid so corridors are not dropped.
LON_MIN, LON_MAX = -78.0, -66.5
LAT_MIN, LAT_MAX = 46.0, 52.0

VOLTAGES = (735, 315, 230, 120, 69)
REGIONS = (
    ("Démo-Nord", "DEMO-N"),
    ("Démo-Centre", "DEMO-C"),
    ("Démo-Est", "DEMO-E"),
    ("Démo-Ouest", "DEMO-O"),
)


def move(lon: float, lat: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    bearing = math.radians(bearing_deg)
    lat2 = lat + (distance_km * math.cos(bearing)) / 111.0
    lon2 = lon + (distance_km * math.sin(bearing)) / (111.0 * math.cos(math.radians(lat)))
    return lon2, lat2


def clamp_bearing(lon: float, lat: float, bearing: float) -> float:
    if lon < LON_MIN + 0.4:
        bearing = 90.0
    elif lon > LON_MAX - 0.4:
        bearing = 270.0
    if lat < LAT_MIN + 0.3:
        bearing = 0.0
    elif lat > LAT_MAX - 0.3:
        bearing = 180.0
    return bearing


def one_line(rng: random.Random, index: int) -> dict[str, object]:
    lon = rng.uniform(LON_MIN + 0.6, LON_MAX - 0.6)
    lat = rng.uniform(LAT_MIN + 0.4, LAT_MAX - 0.4)
    bearing = rng.uniform(0.0, 360.0)
    coords = [[round(lon, 5), round(lat, 5)]]
    steps = rng.randint(8, 18)
    for _ in range(steps):
        bearing = (bearing + rng.uniform(-25.0, 25.0)) % 360.0
        bearing = clamp_bearing(lon, lat, bearing)
        lon, lat = move(lon, lat, bearing, rng.uniform(8.0, 16.0))
        lon = min(max(lon, LON_MIN), LON_MAX)
        lat = min(max(lat, LAT_MIN), LAT_MAX)
        coords.append([round(lon, 5), round(lat, 5)])
    voltage = VOLTAGES[index % len(VOLTAGES)]
    region, direction = REGIONS[index % len(REGIONS)]
    number = f"F{index:03d}"
    return {
        "type": "Feature",
        "properties": {
            "HQ_OBJET": f"Ligne fictive {voltage} kV",
            "HQ_ETIQUETTE": f"{voltage} kV {number}",
            "HQ_SOURCE": "Réseau fictif de démonstration",
            "LIGNE": number,
            "TYPE": "aérien",
            "TENSION_EXP": voltage,
            "RESPONSA": region,
            "DIRECTION_TE": direction,
            "ETAT": "Fictif",
        },
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def main() -> None:
    rng = random.Random(1942)
    features = [one_line(rng, index) for index in range(1, 81)]
    collection = {
        "type": "FeatureCollection",
        "name": "reseau_fictif",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    OUTPUT.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")
    print(f"{len(features)} lignes fictives écrites dans {OUTPUT.name}")


if __name__ == "__main__":
    main()
