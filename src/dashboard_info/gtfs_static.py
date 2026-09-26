import csv
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .paths import gtfs_zip_path, stations_cache_path, stations_csv_path

STATIC_GTFS_URL = "http://web.mta.info/developers/data/nyct/subway/google_transit.zip"
STATIONS_CSV_URL = "http://web.mta.info/developers/data/nyct/subway/Stations.csv"


@dataclass
class ChildStop:
    id: str
    name: str
    direction: str | None  # "N", "S", or None (no direction suffix)


@dataclass
class Station:
    id: str
    name: str
    routes: list[str] = field(default_factory=list)
    child_stops: list[ChildStop] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "routes": self.routes,
            "child_stops": [
                {"id": c.id, "name": c.name, "direction": c.direction} for c in self.child_stops
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Station":
        return cls(
            id=data["id"],
            name=data["name"],
            routes=data["routes"],
            child_stops=[ChildStop(**c) for c in data["child_stops"]],
        )


@dataclass
class RouteInfo:
    id: str
    short_name: str
    color: str
    text_color: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "short_name": self.short_name,
            "color": self.color,
            "text_color": self.text_color,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RouteInfo":
        return cls(**data)


@dataclass
class GtfsIndex:
    fetched_at: str | None
    stations: dict[str, Station]
    routes: dict[str, RouteInfo]
    stop_names: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.stop_names:
            for station in self.stations.values():
                self.stop_names[station.id] = station.name
                for child in station.child_stops:
                    self.stop_names[child.id] = station.name

    def station_route_label(self, station: Station) -> str:
        return ", ".join(station.routes)

    def resolve_stop_name(self, stop_id: str) -> str:
        return self.stop_names.get(stop_id, stop_id)


async def download_static_gtfs(dest: Path | None = None) -> Path:
    dest = dest or gtfs_zip_path()
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        resp = await client.get(STATIC_GTFS_URL)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


async def download_stations_csv(dest: Path | None = None) -> Path:
    dest = dest or stations_csv_path()
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        resp = await client.get(STATIONS_CSV_URL)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


def parse_complex_ids(csv_path: Path) -> dict[str, str]:
    """GTFS Stop ID -> Complex ID, from MTA's own curated station/complex
    dataset (separate from GTFS). This is the authoritative grouping transit
    apps use for "which platforms count as one station" -- geometric distance
    can't substitute for it: e.g. Chambers St's platforms are ~450m apart yet
    are one complex, while the distinct "23 St" stations on different lines
    are only ~275m apart yet are NOT connected."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return {row["GTFS Stop ID"]: row["Complex ID"] for row in rows if row.get("GTFS Stop ID")}


def _direction_of(stop_id: str) -> str | None:
    if stop_id.endswith("N"):
        return "N"
    if stop_id.endswith("S"):
        return "S"
    return None


def _read_dicts(zf: zipfile.ZipFile, name: str) -> list[dict]:
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        return list(csv.DictReader(text))


def _read_stop_times_pairs(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    with zf.open("stop_times.txt") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        reader = csv.reader(text)
        header = next(reader)
        trip_idx = header.index("trip_id")
        stop_idx = header.index("stop_id")
        return [(row[trip_idx], row[stop_idx]) for row in reader if row]


def parse_routes(zip_path: Path) -> dict[str, RouteInfo]:
    with zipfile.ZipFile(zip_path) as zf:
        rows = _read_dicts(zf, "routes.txt")
    routes: dict[str, RouteInfo] = {}
    for row in rows:
        route_id = row["route_id"]
        color = (row.get("route_color") or "").strip() or "6E6E6E"
        text_color = (row.get("route_text_color") or "").strip() or "FFFFFF"
        short_name = (row.get("route_short_name") or route_id).strip()
        routes[route_id] = RouteInfo(
            id=route_id,
            short_name=short_name,
            color=f"#{color}" if not color.startswith("#") else color,
            text_color=f"#{text_color}" if not text_color.startswith("#") else text_color,
        )
    return routes


def parse_stations(zip_path: Path, complex_id_by_stop: dict[str, str] | None = None) -> dict[str, Station]:
    complex_id_by_stop = complex_id_by_stop or {}
    with zipfile.ZipFile(zip_path) as zf:
        stops_rows = _read_dicts(zf, "stops.txt")
        trips_rows = _read_dicts(zf, "trips.txt")
        stop_time_pairs = _read_stop_times_pairs(zf)

    stop_names: dict[str, str] = {}
    parent_of: dict[str, str | None] = {}
    parent_station_ids: set[str] = set()
    for row in stops_rows:
        stop_id = row["stop_id"]
        stop_names[stop_id] = row["stop_name"]
        parent_of[stop_id] = row.get("parent_station") or None
        if (row.get("location_type") or "0") == "1":
            parent_station_ids.add(stop_id)

    trip_route: dict[str, str] = {row["trip_id"]: row["route_id"] for row in trips_rows}

    routes_by_stop: dict[str, set[str]] = {}
    for trip_id, stop_id in stop_time_pairs:
        route_id = trip_route.get(trip_id)
        if route_id is None:
            continue
        routes_by_stop.setdefault(stop_id, set()).add(route_id)

    stations: dict[str, Station] = {
        parent_id: Station(id=parent_id, name=stop_names.get(parent_id, parent_id))
        for parent_id in parent_station_ids
    }

    for stop_id, parent_id in parent_of.items():
        if parent_id in stations:
            stations[parent_id].child_stops.append(
                ChildStop(
                    id=stop_id,
                    name=stop_names.get(stop_id, stop_id),
                    direction=_direction_of(stop_id),
                )
            )

    # Some terminals/shuttles have no N/S-suffixed platform at all and report real-time
    # updates against the bare parent stop id instead -- keep that id reachable too.
    for parent_id, station in stations.items():
        if not station.child_stops:
            station.child_stops.append(ChildStop(id=parent_id, name=station.name, direction=None))

    for station in stations.values():
        route_set: set[str] = set()
        for child in station.child_stops:
            route_set |= routes_by_stop.get(child.id, set())
        station.routes = sorted(route_set)

    return _merge_stations_by_complex(stations, complex_id_by_stop)


def _merge_stations_by_complex(
    stations: dict[str, Station], complex_id_by_stop: dict[str, str]
) -> dict[str, Station]:
    """Standard GTFS represents a transfer complex (e.g. Times Sq-42 St) as
    several distinct parent stations, one per line group, each only listing
    its own routes. Group them by MTA's own curated Complex ID (see
    parse_complex_ids) into a single selectable entry with the union of
    routes and platforms, matching how riders actually think of "a station".
    A station missing from the complex dataset falls back to being its own
    ungrouped complex, rather than guessing.
    """
    by_complex: dict[str, list[Station]] = {}
    for station in stations.values():
        complex_id = complex_id_by_stop.get(station.id, station.id)
        by_complex.setdefault(complex_id, []).append(station)

    merged: dict[str, Station] = {}
    for group in by_complex.values():
        group.sort(key=lambda s: s.id)
        merged_id = "+".join(s.id for s in group)
        merged[merged_id] = Station(
            id=merged_id,
            name=group[0].name,
            routes=sorted({route for s in group for route in s.routes}),
            child_stops=[child for s in group for child in s.child_stops],
        )
    return merged


def save_index(stations: dict[str, Station], routes: dict[str, RouteInfo], path: Path | None = None) -> GtfsIndex:
    path = path or stations_cache_path()
    fetched_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "fetched_at": fetched_at,
        "stations": {sid: s.to_dict() for sid, s in stations.items()},
        "routes": {rid: r.to_dict() for rid, r in routes.items()},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return GtfsIndex(fetched_at=fetched_at, stations=stations, routes=routes)


def load_index(path: Path | None = None) -> GtfsIndex:
    path = path or stations_cache_path()
    if not path.exists():
        return GtfsIndex(fetched_at=None, stations={}, routes={})
    data = json.loads(path.read_text(encoding="utf-8"))
    return GtfsIndex(
        fetched_at=data.get("fetched_at"),
        stations={sid: Station.from_dict(d) for sid, d in data.get("stations", {}).items()},
        routes={rid: RouteInfo.from_dict(d) for rid, d in data.get("routes", {}).items()},
    )


async def refresh_static_gtfs() -> GtfsIndex:
    zip_path = await download_static_gtfs()
    csv_path = await download_stations_csv()
    complex_id_by_stop = parse_complex_ids(csv_path)
    stations = parse_stations(zip_path, complex_id_by_stop)
    routes = parse_routes(zip_path)
    return save_index(stations, routes)
