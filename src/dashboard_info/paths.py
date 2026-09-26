import os
from pathlib import Path


def state_dir() -> Path:
    path = Path(os.environ.get("DASHBOARD_STATE_DIR", "./var")).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return state_dir() / "dashboard_info.db"


def gtfs_static_dir() -> Path:
    path = state_dir() / "gtfs_static"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stations_cache_path() -> Path:
    return gtfs_static_dir() / "stations.json"


def gtfs_zip_path() -> Path:
    return gtfs_static_dir() / "google_transit.zip"


def stations_csv_path() -> Path:
    return gtfs_static_dir() / "Stations.csv"
