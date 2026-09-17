import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from mta_info.main import create_app
from mta_info.tests.test_departures import NOW_EPOCH, feed_message
from mta_info.word_of_the_day import WordOfTheDayEntry

FIXTURE_INDEX = {
    "fetched_at": "2026-01-01T00:00:00+00:00",
    "stations": {
        "101": {
            "id": "101",
            "name": "Test Station",
            "routes": ["A", "C"],
            "child_stops": [
                {"id": "101N", "name": "Test Station", "direction": "N"},
                {"id": "101S", "name": "Test Station", "direction": "S"},
            ],
        },
        "201": {
            "id": "201",
            "name": "Far Away Ave",
            "routes": ["A"],
            "child_stops": [
                {"id": "201N", "name": "Far Away Ave", "direction": "N"},
                {"id": "201S", "name": "Far Away Ave", "direction": "S"},
            ],
        },
    },
    "routes": {
        "A": {"id": "A", "short_name": "A", "color": "#0062CF", "text_color": "#FFFFFF"},
        "C": {"id": "C", "short_name": "C", "color": "#0062CF", "text_color": "#FFFFFF"},
    },
}


class FakeFeedCache:
    """Stands in for FeedCache: serves canned feed messages, never touches
    the network."""

    def __init__(self, messages):
        self._messages = messages

    async def messages_for(self, groups):
        return {group: self._messages.get(group) for group in groups}

    async def close(self):
        pass


class FakeWordOfTheDayCache:
    """Stands in for WordOfTheDayCache: serves canned entries, never touches
    the network."""

    def __init__(self, entries):
        self._entries = entries

    async def entries_for_today(self):
        return dict(self._entries)

    async def close(self):
        pass


FIXTURE_WOTD = {
    "en": WordOfTheDayEntry(
        language="en",
        date="2026-01-01",
        word="serendipity",
        part_of_speech="n",
        definition="The occurrence of happy accidents.",
        example=None,
        source_url="https://en.wiktionary.org/wiki/serendipity",
    ),
    "es": WordOfTheDayEntry(
        language="es",
        date="2026-01-01",
        word="casa",
        part_of_speech="noun, feminine",
        definition="Edificio para habitar.",
        example="Una casa de ocho plantas.",
        source_url="https://dle.rae.es/casa",
    ),
    "de": WordOfTheDayEntry(
        language="de",
        date="2026-01-01",
        word="Irrealis",
        part_of_speech="Substantiv, maskulin",
        definition="Modus des irrealen Wunsches.",
        example=None,
        source_url="https://www.duden.de/rechtschreibung/Irrealis",
    ),
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    feed = feed_message(
        [
            ("A", [("101N", now_epoch + 5 * 60), ("201N", now_epoch + 25 * 60)]),
            ("A", [("101N", now_epoch + 12 * 60), ("999", now_epoch + 30 * 60)]),
            ("C", [("101S", now_epoch + 2 * 60), ("201S", now_epoch + 22 * 60)]),
        ]
    )
    gtfs_dir = tmp_path / "gtfs_static"
    gtfs_dir.mkdir()
    (gtfs_dir / "stations.json").write_text(json.dumps(FIXTURE_INDEX), encoding="utf-8")
    monkeypatch.setenv("MTA_STATE_DIR", str(tmp_path))

    app = create_app(
        feed_cache=FakeFeedCache({"gtfs-ace": feed}),
        wotd_cache=FakeWordOfTheDayCache(FIXTURE_WOTD),
    )
    with TestClient(app) as test_client:
        yield test_client


def enroll(client, device_id="board-1"):
    resp = client.post("/api/devices", json={"id": device_id})
    assert resp.status_code == 201


def configure(client, configurations, device_id="board-1"):
    return client.put(
        f"/api/devices/{device_id}/configurations",
        json={"configurations": configurations},
    )


def test_departures_rejects_unknown_device(client):
    resp = client.get("/public/devices/nope/departures")
    assert resp.status_code == 404


def test_enrolled_device_with_blank_config_gets_empty_list(client):
    enroll(client)
    resp = client.get("/public/devices/board-1/departures")
    assert resp.status_code == 200
    assert resp.json() == []


def test_polling_updates_last_request_timestamp(client):
    enroll(client)
    assert client.get("/api/devices/board-1").json()["last_request_at"] is None
    client.get("/public/devices/board-1/departures")
    assert client.get("/api/devices/board-1").json()["last_request_at"] is not None


def test_departures_payload_shape(client):
    enroll(client)
    resp = configure(
        client,
        [
            {
                "origin_station_id": "101",
                "service": "A",
                "direction": "uptown",
                "destination_station_id": "201",
                "destination_walk_minutes": 4,
            }
        ],
    )
    assert resp.status_code == 200

    resp = client.get("/public/devices/board-1/departures")
    departures = resp.json()
    assert len(departures) == 2

    first, second = departures
    assert first["service"] == "A"
    assert first["terminus"] == "Far Away Ave"
    assert first["arrives_at"].endswith("Z")
    assert first["stops_at_destination"] is True
    reach = datetime.fromisoformat(first["reach_destination_at"].replace("Z", "+00:00"))
    arrive = datetime.fromisoformat(first["arrives_at"].replace("Z", "+00:00"))
    # 20 minutes Test Station -> Far Away Ave, plus 4 minutes of walking.
    assert (reach - arrive) == timedelta(minutes=24)

    # Second trip terminates early at unknown stop 999: flagged, no ETA.
    assert second["stops_at_destination"] is False
    assert "reach_destination_at" not in second


def test_direction_is_applied(client):
    enroll(client)
    # Both A trips in the fixture feed stop at the uptown platform (101N).
    configure(client, [{"origin_station_id": "101", "service": "A", "direction": "downtown"}])
    resp = client.get("/public/devices/board-1/departures")
    assert resp.json() == []


def test_invalid_direction_rejected(client):
    enroll(client)
    resp = configure(client, [{"origin_station_id": "101", "service": "A", "direction": "up"}])
    assert resp.status_code == 422


def test_destination_keys_omitted_when_no_destination_configured(client):
    enroll(client)
    configure(client, [{"origin_station_id": "101", "service": "A", "direction": "uptown"}])
    [departure] = client.get(
        "/public/devices/board-1/departures?max_departures=1"
    ).json()
    assert "stops_at_destination" not in departure
    assert "reach_destination_at" not in departure


def test_departures_unions_configurations_and_caps_at_max(client):
    enroll(client)
    configure(
        client,
        [
            {"origin_station_id": "101", "service": "A", "direction": "uptown"},
            {"origin_station_id": "101", "service": "C", "direction": "downtown"},
        ],
    )
    resp = client.get("/public/devices/board-1/departures")
    departures = resp.json()
    # 3 departures total, default cap is 3: C(+2), A(+5), A(+12).
    assert [d["service"] for d in departures] == ["C", "A", "A"]

    resp = client.get("/public/devices/board-1/departures?max_departures=2")
    assert [d["service"] for d in resp.json()] == ["C", "A"]


def test_duplicate_service_rejected_with_409(client):
    enroll(client)
    resp = configure(
        client,
        [
            {"origin_station_id": "101", "service": "A", "direction": "uptown"},
            {"origin_station_id": "201", "service": "a", "direction": "downtown"},
        ],
    )
    assert resp.status_code == 409
    assert "one configuration per service" in resp.json()["detail"]


def test_stations_endpoint_serves_loaded_index(client):
    data = client.get("/api/stations").json()
    assert data["fetched_at"] == "2026-01-01T00:00:00+00:00"
    assert [s["name"] for s in data["stations"]] == ["Far Away Ave", "Test Station"]


def test_get_device_includes_configurations(client):
    enroll(client)
    configure(client, [{"origin_station_id": "101", "service": "A", "direction": "uptown"}])
    data = client.get("/api/devices/board-1").json()
    assert data["id"] == "board-1"
    assert len(data["configurations"]) == 1
    assert data["configurations"][0]["service"] == "A"
    assert data["configurations"][0]["direction"] == "uptown"


def test_delete_device_removes_it(client):
    enroll(client)
    resp = client.delete("/api/devices/board-1")
    assert resp.status_code == 204
    assert client.get("/api/devices/board-1").status_code == 404
    assert client.get("/api/devices").json() == []


def test_delete_device_rejects_unknown_device(client):
    resp = client.delete("/api/devices/nope")
    assert resp.status_code == 404


def test_delete_device_is_idempotent_failure(client):
    enroll(client)
    client.delete("/api/devices/board-1")
    resp = client.delete("/api/devices/board-1")
    assert resp.status_code == 404


SCHEDULE = {
    "commute_start": "07:00",
    "commute_end": "09:30",
    "dim_start": "20:00",
    "dim_end": "23:00",
    "dim_brightness": 40,
    "off_start": "23:00",
    "off_end": "06:00",
}


def test_put_schedule_round_trips_via_get_device(client):
    enroll(client)
    resp = client.put("/api/devices/board-1/schedule", json=SCHEDULE)
    assert resp.status_code == 200
    for key, value in SCHEDULE.items():
        assert resp.json()[key] == value

    device = client.get("/api/devices/board-1").json()
    for key, value in SCHEDULE.items():
        assert device[key] == value


def test_put_schedule_rejects_unknown_device(client):
    resp = client.put("/api/devices/nope/schedule", json=SCHEDULE)
    assert resp.status_code == 404


def test_put_schedule_rejects_bad_time(client):
    enroll(client)
    resp = client.put(
        "/api/devices/board-1/schedule",
        json={"commute_start": "not-a-time", "commute_end": "09:00"},
    )
    assert resp.status_code == 422


def test_put_schedule_rejects_bad_brightness(client):
    enroll(client)
    resp = client.put("/api/devices/board-1/schedule", json={"dim_brightness": 300})
    assert resp.status_code == 422


def test_get_schedule_rejects_unknown_device(client):
    resp = client.get("/public/devices/nope/schedule")
    assert resp.status_code == 404


def test_get_schedule_returns_raw_config_not_computed_state(client):
    enroll(client)
    client.put("/api/devices/board-1/schedule", json=SCHEDULE)
    resp = client.get("/public/devices/board-1/schedule")
    assert resp.status_code == 200
    assert resp.json() == SCHEDULE
    assert "commute_active" not in resp.json()
    assert "brightness" not in resp.json()


def test_get_schedule_defaults_to_all_null(client):
    enroll(client)
    resp = client.get("/public/devices/board-1/schedule")
    assert resp.json() == {
        "commute_start": None,
        "commute_end": None,
        "dim_start": None,
        "dim_end": None,
        "dim_brightness": None,
        "off_start": None,
        "off_end": None,
    }


def test_departures_response_shape_unchanged_by_schedule_feature(client):
    # /public/devices/{id}/departures must stay a bare array -- the schedule
    # feature lives entirely on its own endpoints, not folded into this response.
    enroll(client)
    resp = client.get("/public/devices/board-1/departures")
    assert isinstance(resp.json(), list)


def test_word_of_the_day_payload_shape(client):
    resp = client.get("/public/word-of-the-day")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"en", "es", "de"}
    assert data["en"]["word"] == "serendipity"
    assert data["es"]["example"] == "Una casa de ocho plantas."
    assert data["de"]["part_of_speech"] == "Substantiv, maskulin"


def test_word_of_the_day_serves_other_languages_when_one_is_missing(client):
    # A source can be temporarily unreachable; the endpoint still serves the
    # other languages rather than failing the whole request.
    app = create_app(
        feed_cache=FakeFeedCache({}),
        wotd_cache=FakeWordOfTheDayCache({**FIXTURE_WOTD, "de": None}),
    )
    with TestClient(app) as broken_client:
        resp = broken_client.get("/public/word-of-the-day")
    assert resp.status_code == 200
    data = resp.json()
    assert data["de"] is None
    assert data["en"]["word"] == "serendipity"
