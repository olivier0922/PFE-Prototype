from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]

ERA5_ARCHIVE = ROOT_DIR / "era5-pressure-levels-19421229-30.zarr.zip"
ERA5_DIR = ROOT_DIR / "era5-pressure-levels-19421229-30.zarr"
LINES_PATH = ROOT_DIR / "lignes_demo.geojson"
TERRAIN_PATH = ROOT_DIR / "mnt_ETOPO.tiff"

GRAVITY_M_S2 = 9.80665
STANDARD_LAPSE_RATE_K_PER_M = 0.0065

# Sampling of the selected corridor for the detailed cross-section.
TRACK_SPACING_KM = 2.5
MIN_TRACK_POINTS = 24
MAX_TRACK_POINTS = 400
ALTITUDE_STEP_M = 100.0

# Coarser sampling used to rank every corridor of the network.
OVERVIEW_SPACING_KM = 12.0

# Height above ground used as a proxy for conductor-level conditions.
NEAR_GROUND_OFFSET_M = 100.0

# Maximum gap bridged when chaining consecutive segments of the same line.
CORRIDOR_GAP_TOLERANCE_M = 1500.0
MIN_CORRIDOR_LENGTH_KM = 5.0

# Local time zone used by Hydro-Québec operations (no DST in December).
LOCAL_UTC_OFFSET_HOURS = -5
LOCAL_TZ_LABEL = "HNE"

EXPECTED_ERA5_VARIABLES = {"t", "r", "u", "v", "z"}
