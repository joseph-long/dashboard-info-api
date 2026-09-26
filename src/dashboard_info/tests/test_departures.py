from datetime import datetime, timedelta, timezone

from google.transit import gtfs_realtime_pb2

from dashboard_info.db import Configuration
from dashboard_info.departures import compute_departures, merge_departures, Departure
from dashboard_info.gtfs_static import ChildStop, GtfsIndex, RouteInfo, Station

NOW = datetime(2026, 9, 13, 17, 0, 0, tzinfo=timezone.utc)
NOW_EPOCH = int(NOW.timestamp())


def feed_message(trips: list[tuple[str, list[tuple[str, int]]]]) -> gtfs_realtime_pb2.FeedMessage:
    """Build a FeedMessage from (route_id, [(stop_id, arrival_epoch)]) trips."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    for i, (route_id, stop_updates) in enumerate(trips):
        entity = msg.entity.add()
        entity.id = f"trip-{i}"
        trip_update = entity.trip_update
        trip_update.trip.route_id = route_id
        trip_update.trip.trip_id = f"trip-{i}"
        for stop_id, epoch in stop_updates:
            stu = trip_update.stop_time_update.add()
            stu.stop_id = stop_id
            stu.arrival.time = epoch
    return msg


def build_index() -> GtfsIndex:
    origin = Station(
        id="101",
        name="Test Station",
        routes=["A", "C"],
        child_stops=[
            ChildStop(id="101N", name="Test Station", direction="N"),
            ChildStop(id="101S", name="Test Station", direction="S"),
        ],
    )
    destination = Station(
        id="201",
        name="Far Away Ave",
        routes=["A"],
        child_stops=[
            ChildStop(id="201N", name="Far Away Ave", direction="N"),
            ChildStop(id="201S", name="Far Away Ave", direction="S"),
        ],
    )
    return GtfsIndex(
        fetched_at=None,
        stations={"101": origin, "201": destination},
        routes={"A": RouteInfo(id="A", short_name="A", color="#0062CF", text_color="#FFFFFF")},
    )


def make_config(**overrides) -> Configuration:
    values = dict(
        id=1,
        origin_station_id="101",
        service="A",
        direction="uptown",
        destination_station_id=None,
        destination_walk_minutes=None,
    )
    values.update(overrides)
    return Configuration(**values)


def test_matches_service_and_resolves_terminus():
    index = build_index()
    feed = feed_message(
        [
            ("A", [("101N", NOW_EPOCH + 5 * 60), ("201N", NOW_EPOCH + 25 * 60)]),
            ("C", [("101N", NOW_EPOCH + 2 * 60), ("201N", NOW_EPOCH + 20 * 60)]),
        ]
    )
    rows = compute_departures({"gtfs-ace": feed}, make_config(), index, now=NOW)
    assert len(rows) == 1
    assert rows[0].service == "A"
    assert rows[0].terminus == "Far Away Ave"
    assert rows[0].arrives_at == datetime.fromtimestamp(NOW_EPOCH + 5 * 60, tz=timezone.utc)


def test_terminus_name_override_applied():
    index = build_index()
    # "201"/"201N" resolve to "Far Away Ave" in the shared fixture; override
    # its resolved name in place to exercise the terminus override lookup
    # without needing a whole separate GtfsIndex fixture.
    index.stop_names["201"] = "Coney Island-Stillwell Av"
    index.stop_names["201N"] = "Coney Island-Stillwell Av"
    feed = feed_message([("A", [("101N", NOW_EPOCH + 5 * 60), ("201N", NOW_EPOCH + 25 * 60)])])
    rows = compute_departures({"gtfs-ace": feed}, make_config(), index, now=NOW)
    assert rows[0].terminus == "Coney Island"


def test_direction_filters_by_platform_suffix():
    index = build_index()
    feed = feed_message(
        [
            ("A", [("101N", NOW_EPOCH + 5 * 60), ("201N", NOW_EPOCH + 25 * 60)]),
            ("A", [("101S", NOW_EPOCH + 3 * 60), ("201S", NOW_EPOCH + 20 * 60)]),
        ]
    )
    uptown = compute_departures({"gtfs-ace": feed}, make_config(), index, now=NOW)
    assert [r.arrives_at for r in uptown] == [
        datetime.fromtimestamp(NOW_EPOCH + 5 * 60, tz=timezone.utc)
    ]
    downtown = compute_departures(
        {"gtfs-ace": feed}, make_config(direction="downtown"), index, now=NOW
    )
    assert [r.arrives_at for r in downtown] == [
        datetime.fromtimestamp(NOW_EPOCH + 3 * 60, tz=timezone.utc)
    ]


def test_bare_platform_stop_matches_either_direction():
    """Terminals/shuttles report updates against the unsuffixed parent stop
    id, which can't be attributed to a direction -- include it either way."""
    index = build_index()
    feed = feed_message([("A", [("101", NOW_EPOCH + 7 * 60), ("201N", NOW_EPOCH + 30 * 60)])])
    for direction in ("uptown", "downtown"):
        rows = compute_departures(
            {"gtfs-ace": feed}, make_config(direction=direction), index, now=NOW
        )
        assert len(rows) == 1


def test_past_arrivals_are_excluded():
    index = build_index()
    feed = feed_message([("A", [("101N", NOW_EPOCH - 60), ("201N", NOW_EPOCH + 600)])])
    assert compute_departures({"gtfs-ace": feed}, make_config(), index, now=NOW) == []


def test_unknown_origin_station_yields_nothing():
    index = build_index()
    feed = feed_message([("A", [("101N", NOW_EPOCH + 5 * 60)])])
    config = make_config(origin_station_id="999")
    assert compute_departures({"gtfs-ace": feed}, config, index, now=NOW) == []


def test_destination_keys_omitted_without_destination():
    index = build_index()
    feed = feed_message([("A", [("101N", NOW_EPOCH + 5 * 60), ("201N", NOW_EPOCH + 25 * 60)])])
    rows = compute_departures({"gtfs-ace": feed}, make_config(), index, now=NOW)
    assert rows[0].stops_at_destination is None
    assert rows[0].reach_destination_at is None
    payload = rows[0].to_dict()
    assert "stops_at_destination" not in payload
    assert "reach_destination_at" not in payload


def test_destination_reached_includes_walk_time():
    index = build_index()
    feed = feed_message([("A", [("101N", NOW_EPOCH + 5 * 60), ("201N", NOW_EPOCH + 25 * 60)])])
    config = make_config(destination_station_id="201", destination_walk_minutes=7)
    rows = compute_departures({"gtfs-ace": feed}, config, index, now=NOW)
    assert rows[0].stops_at_destination is True
    assert rows[0].reach_destination_at == datetime.fromtimestamp(
        NOW_EPOCH + 25 * 60, tz=timezone.utc
    ) + timedelta(minutes=7)


def test_train_cut_short_flags_not_stopping_at_destination():
    index = build_index()
    feed = feed_message([("A", [("101N", NOW_EPOCH + 5 * 60), ("999", NOW_EPOCH + 15 * 60)])])
    config = make_config(destination_station_id="201")
    rows = compute_departures({"gtfs-ace": feed}, config, index, now=NOW)
    assert rows[0].stops_at_destination is False
    assert rows[0].reach_destination_at is None
    assert rows[0].to_dict()["stops_at_destination"] is False


def test_to_dict_uses_zulu_iso_timestamps():
    departure = Departure(
        service="A",
        terminus="Far Away Ave",
        arrives_at=datetime.fromtimestamp(NOW_EPOCH + 5 * 60, tz=timezone.utc),
    )
    assert departure.to_dict()["arrives_at"] == "2026-09-13T17:05:00Z"


def test_merge_departures_sorts_and_caps():
    def at(offset_minutes, service):
        return Departure(
            service=service,
            terminus="Somewhere",
            arrives_at=NOW + timedelta(minutes=offset_minutes),
        )

    merged = merge_departures(
        [[at(8, "A"), at(2, "A")], [at(5, "C"), at(1, "C")]], limit=3
    )
    assert [(d.arrives_at.minute, d.service) for d in merged] == [
        (1, "C"),
        (2, "A"),
        (5, "C"),
    ]
