"""Export a static GitHub Pages bundle from the Dash analysis.

GitHub Pages cannot run the Dash server. This script writes the same
corridor analysis as compact JSON under ``docs/data/`` for ``docs/app.js``.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    CORRIDORS,
    DEFAULT_CORRIDOR,
    DEFAULT_TIME,
    LABELS,
    PEAK_SCORE,
    PEAK_TIME,
    TIMES,
    overview,
    repository,
)
from src.figures import format_time  # noqa: E402
from src.interpolation import column_diagnostics, spatial_columns  # noqa: E402
from src.network import sample_line_geometry  # noqa: E402
from src.risk import RiskAssessment, assess_risk, exposed_segments  # noqa: E402


DOCS = ROOT / "docs" / "data"


def _b64(array: np.ndarray, dtype: np.dtype) -> str:
    packed = np.ascontiguousarray(array, dtype=dtype)
    return base64.b64encode(packed.tobytes()).decode("ascii")


def _file_id(corridor_id: str) -> str:
    return corridor_id.replace("#", "_")


def _segments(distance_km: np.ndarray, risk: RiskAssessment) -> list[dict[str, object]]:
    frame = exposed_segments(distance_km, risk, TIMES)
    if frame.empty:
        return []
    rows = []
    for row in frame.itertuples():
        rows.append(
            {
                "km0": round(float(row.km_debut), 1),
                "km1": round(float(row.km_fin), 1),
                "len": round(float(max(row.longueur_km, 1)), 1),
                "score": round(float(row.risque_max), 1),
                "cat": row.categorie,
                "hazard": row.alea,
                "precip": row.precipitation,
                "temp": round(float(row.temperature_c), 1),
                "wind": round(float(row.vent_kmh)),
                "when": format_time(row.pic_time, short=True),
            }
        )
    return rows


def _corridor_payload(corridor_id: str) -> dict[str, object]:
    corridor = repository.get_corridor(corridor_id)
    distance, longitude, latitude = sample_line_geometry(corridor.geometry)
    terrain = repository.sample_terrain(longitude, latitude)
    extra = ("vo",) if "vo" in repository.weather.data_vars else ()
    levels = spatial_columns(repository, longitude, latitude, extra)
    diagnostics = column_diagnostics(
        levels["height"], levels["temperature"], levels["humidity"], levels["u"], levels["v"], terrain
    )
    risk = assess_risk(diagnostics)
    return {
        "id": corridor.corridor_id,
        "line": corridor.line_number,
        "label": corridor.label,
        "voltage": int(corridor.voltage_kv),
        "length": round(float(corridor.length_km), 1),
        "region": corridor.region,
        "part": int(corridor.part_index),
        "parts": int(corridor.part_count),
        "n": int(len(distance)),
        "levels": [float(v) for v in repository.weather.level.values],
        "distance": _b64(distance, np.float32),
        "lon": _b64(longitude, np.float32),
        "lat": _b64(latitude, np.float32),
        "terrain": _b64(terrain, np.float32),
        "height": _b64(levels["height"], np.float16),
        "temperature": _b64(levels["temperature"], np.float16),
        "humidity": _b64(levels["humidity"], np.float16),
        "u": _b64(levels["u"], np.float16),
        "v": _b64(levels["v"], np.float16),
        **({"vo": _b64(levels["vo"], np.float16)} if "vo" in levels else {}),
        "score": _b64(risk.score, np.float16),
        "nearT": _b64(diagnostics.near_ground_temperature_c, np.float16),
        "nearRh": _b64(diagnostics.near_ground_humidity, np.float16),
        "nearWind": _b64(diagnostics.near_ground_wind_ms, np.float16),
        "warm": _b64(diagnostics.warm_layer_max_c, np.float16),
        "dominant": _b64(risk.dominant, np.int8),
        "precip": _b64(risk.precipitation, np.uint8),
        "segments": _segments(distance, risk),
    }


def main() -> int:
    out = DOCS / "corridors"
    out.mkdir(parents=True, exist_ok=True)
    ids = [str(cid) for cid in CORRIDORS["corridor_id"]]
    paths = {}
    for cid, (lon, lat) in overview.map_paths.items():
        paths[cid] = [_b64(lon, np.float32), _b64(lat, np.float32), int(len(lon))]

    meta = {
        "times": [format_time(value) for value in TIMES],
        "timesShort": [format_time(value, short=True) for value in TIMES],
        "defaultCorridor": DEFAULT_CORRIDOR,
        "defaultTime": int(DEFAULT_TIME),
        "voltages": [int(v) for v in repository.voltages],
        "regions": list(repository.regions),
        "corridors": [
            {
                "id": cid,
                "label": LABELS[cid],
                "voltage": int(row.voltage_kv),
                "region": str(row.region),
                "length": round(float(row.length_km), 1),
                "peak": None if not np.isfinite(PEAK_SCORE[i]) else round(float(PEAK_SCORE[i]), 1),
                "peakTime": int(PEAK_TIME[i]),
            }
            for i, (cid, row) in enumerate(zip(ids, CORRIDORS.itertuples()))
        ],
        "score": _b64(overview.score, np.float16),
        "nTime": int(overview.score.shape[0]),
        "paths": paths,
    }
    (DOCS / "network.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"{len(ids)} corridors — export des coupes…", flush=True)
    for index, cid in enumerate(ids, start=1):
        payload = _corridor_payload(cid)
        (out / f"{_file_id(cid)}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        if index % 25 == 0 or index == len(ids):
            print(f"  {index}/{len(ids)}", flush=True)
    print(f"Site statique écrit dans {DOCS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
