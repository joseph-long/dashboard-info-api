from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from google.transit import gtfs_realtime_pb2

from .db import Configuration
from .gtfs_static import GtfsIndex, Station


@dataclass
class Departure:
    """One upcoming departure matching a configuration, in the shape the
    device API returns (see README). `stops_at_destination` /
    `reach_destination_at` are None when the configuration has no destination
    station; those keys are then omitted from the API payload entirely."""

    service: str
    terminus: str
    arrives_at: datetime
    stops_at_destination: bool | None = None
    reach_destination_at: datetime | None = None

    def to_dict(self) -> dict:
        result = {
            "service": self.service,
            "terminus": self.terminus,
            "arrives_at": _iso_z(self.arrives_at),
        }
        if self.stops_at_destination is not None:
            result["stops_at_destination"] = self.stops_at_destination
        if self.reach_destination_at is not None:
            result["reach_destination_at"] = _iso_z(self.reach_destination_at)
        return result


def _iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# Curated overrides for terminus names the raw GTFS stop name renders as too
# long/confusing on a small display. Keyed by the resolved GTFS name (not the
# stop id), so an override applies regardless of which platform/complex it
# came from. Add more here as they come up -- this only affects the terminus
# field of the departures API, not station names shown elsewhere (e.g. the
# configure page's station picker).
TERMINUS_NAME_OVERRIDES: dict[str, str] = {
    "Coney Island-Stillwell Av": "Coney Island",
}


def _terminus_name(gtfs_index: GtfsIndex, stop_id: str) -> str:
    name = gtfs_index.resolve_stop_name(stop_id)
    return TERMINUS_NAME_OVERRIDES.get(name, name)


def _stop_ids_for(station: Station) -> set[str]:
    # Bare parent id included too: terminals/shuttles with no N/S-suffixed
    # platform report real-time updates against the parent stop id itself.
    return {c.id for c in station.child_stops} | {station.id}


def _wanted_stop_ids(station: Station, direction: str) -> set[str]:
    """Platform stop ids matching the configured direction ("N"-suffixed for
    uptown, "S"-suffixed for downtown). A bare stop id with no N/S suffix
    (terminals/shuttles with a single platform) can't be attributed to a
    direction, so it's included regardless -- this is also what makes
    `stops_at_destination` well-defined: only trains actually heading toward
    the destination are ever matched, so "false" really means "this train
    skips your destination", not "this train is going the other way"."""
    suffix = "N" if direction == "uptown" else "S"
    return {
        c.id
        for c in station.child_stops
        if c.direction in (suffix, None)
    } | {station.id}


def _stop_time_epoch(stu) -> int | None:
    if stu.HasField("arrival") and stu.arrival.time:
        return stu.arrival.time
    if stu.HasField("departure") and stu.departure.time:
        return stu.departure.time
    return None


def compute_departures(
    feed_messages: dict[str, "gtfs_realtime_pb2.FeedMessage | None"],
    config: Configuration,
    gtfs_index: GtfsIndex,
    now: datetime | None = None,
) -> list[Departure]:
    """All upcoming arrivals of the configuration's service at its origin
    station in the configured direction, soonest first."""
    now = now or datetime.now(timezone.utc)
    origin = gtfs_index.stations.get(config.origin_station_id or "")
    if origin is None or not config.service:
        return []
    wanted = _wanted_stop_ids(origin, config.direction or "")

    destination = gtfs_index.stations.get(config.destination_station_id or "")
    destination_stop_ids = _stop_ids_for(destination) if destination else set()
    destination_walk = timedelta(minutes=config.destination_walk_minutes or 0)

    departures: list[Departure] = []
    for message in feed_messages.values():
        if message is None:
            continue
        for entity in message.entity:
            if not entity.HasField("trip_update"):
                continue
            trip_update = entity.trip_update
            if trip_update.trip.route_id != config.service:
                continue
            stop_time_updates = list(trip_update.stop_time_update)
            if not stop_time_updates:
                continue
            for idx, stu in enumerate(stop_time_updates):
                if stu.stop_id not in wanted:
                    continue
                arrival_epoch = _stop_time_epoch(stu)
                if arrival_epoch is None:
                    continue
                arrival_dt = datetime.fromtimestamp(arrival_epoch, tz=timezone.utc)
                if arrival_dt < now:
                    continue

                terminus = _terminus_name(gtfs_index, stop_time_updates[-1].stop_id)

                stops_at_destination = None
                reach_destination_at = None
                if destination is not None:
                    # Only stops from here on count -- a stop the trip already
                    # made before reaching our platform can't be "reached".
                    match = next(
                        (
                            s
                            for s in stop_time_updates[idx:]
                            if s.stop_id in destination_stop_ids
                        ),
                        None,
                    )
                    stops_at_destination = match is not None
                    if match is not None:
                        match_epoch = _stop_time_epoch(match)
                        if match_epoch is not None:
                            reach_destination_at = (
                                datetime.fromtimestamp(match_epoch, tz=timezone.utc)
                                + destination_walk
                            )

                departures.append(
                    Departure(
                        service=config.service,
                        terminus=terminus,
                        arrives_at=arrival_dt,
                        stops_at_destination=stops_at_destination,
                        reach_destination_at=reach_destination_at,
                    )
                )

    departures.sort(key=lambda d: d.arrives_at)
    return departures


def merge_departures(
    by_config: list[list[Departure]], limit: int
) -> list[Departure]:
    """The union of matching departures across all of a device's
    configurations, soonest first, capped at `limit`. (Two configurations of
    one device can never return the same trip: each matches a distinct
    service, so no deduplication is needed.)"""
    merged = [d for departures in by_config for d in departures]
    merged.sort(key=lambda d: d.arrives_at)
    return merged[:limit]
