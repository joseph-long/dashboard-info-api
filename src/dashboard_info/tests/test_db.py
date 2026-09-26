import pytest

from dashboard_info.db import (
    ConfigurationUpdate,
    Database,
    DeviceExistsError,
    DuplicateServiceError,
    ScheduleUpdate,
    validate_hhmm,
)


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


def test_enroll_creates_device_with_blank_default_configuration(db):
    device = db.enroll_device("board-1")
    assert device.id == "board-1"
    assert device.last_request_at is None
    assert device.created_at is not None

    configs = db.get_configurations("board-1")
    assert len(configs) == 1
    assert configs[0].is_complete is False


def test_enroll_rejects_duplicates_and_empty_ids(db):
    db.enroll_device("board-1")
    with pytest.raises(DeviceExistsError):
        db.enroll_device("board-1")
    with pytest.raises(ValueError):
        db.enroll_device("   ")


def test_list_devices_orders_by_creation(db):
    db.enroll_device("b-device")
    db.enroll_device("a-device")
    assert [d.id for d in db.list_devices()] == ["b-device", "a-device"]


def test_touch_device_sets_last_request(db):
    db.enroll_device("board-1")
    db.touch_device("board-1")
    assert db.get_device("board-1").last_request_at is not None


def test_delete_device_removes_it_and_its_configurations(db):
    db.enroll_device("board-1")
    db.replace_configurations(
        "board-1",
        [ConfigurationUpdate(origin_station_id="101", service="A", direction="uptown")],
    )
    assert db.delete_device("board-1") is True
    assert db.get_device("board-1") is None
    with db._connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM configurations").fetchone()["n"]
    assert count == 0


def test_delete_device_returns_false_for_unknown_device(db):
    assert db.delete_device("nope") is False


def test_delete_device_leaves_other_devices_alone(db):
    db.enroll_device("board-1")
    db.enroll_device("board-2")
    db.delete_device("board-1")
    assert [d.id for d in db.list_devices()] == ["board-2"]


def test_replace_configurations_round_trips_and_normalizes(db):
    db.enroll_device("board-1")
    saved = db.replace_configurations(
        "board-1",
        [
            ConfigurationUpdate(
                origin_station_id="101", service="n", direction="Uptown"
            ),
            ConfigurationUpdate(
                origin_station_id="201",
                service="6x",
                direction="DOWNTOWN",
                destination_station_id="101",
                destination_walk_minutes=3,
            ),
        ],
    )
    assert [c.service for c in saved] == ["N", "6X"]

    configs = db.get_configurations("board-1")
    assert len(configs) == 2
    first, second = configs
    assert first.origin_station_id == "101"
    assert first.direction == "uptown"
    assert first.destination_station_id is None
    assert second.service == "6X"
    assert second.direction == "downtown"
    assert second.destination_station_id == "101"
    assert second.destination_walk_minutes == 3
    assert all(c.is_complete for c in configs)


def test_replace_configurations_rejects_invalid_direction(db):
    db.enroll_device("board-1")
    with pytest.raises(ValueError, match="invalid direction"):
        db.replace_configurations(
            "board-1",
            [ConfigurationUpdate(origin_station_id="101", service="N", direction="up")],
        )


def test_replace_configurations_rejects_duplicate_services(db):
    db.enroll_device("board-1")
    with pytest.raises(DuplicateServiceError):
        db.replace_configurations(
            "board-1",
            [
                ConfigurationUpdate(origin_station_id="101", service="N", direction="uptown"),
                # Same service after normalization (case-insensitive).
                ConfigurationUpdate(origin_station_id="201", service="n", direction="downtown"),
            ],
        )


def test_replace_configurations_deletes_orphans(db):
    db.enroll_device("board-1")
    db.replace_configurations(
        "board-1",
        [ConfigurationUpdate(origin_station_id="101", service="A", direction="uptown")],
    )
    db.replace_configurations(
        "board-1",
        [ConfigurationUpdate(origin_station_id="201", service="C", direction="downtown")],
    )
    with db._connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM configurations").fetchone()["n"]
    assert count == 1


def test_replace_configurations_can_empty_the_set(db):
    db.enroll_device("board-1")
    db.replace_configurations("board-1", [])
    assert db.get_configurations("board-1") == []


@pytest.mark.parametrize("value", ["07:00", "00:00", "23:59", "09:30"])
def test_validate_hhmm_accepts_valid_times(value):
    assert validate_hhmm(value) == value


@pytest.mark.parametrize("value", ["", "7:00", "24:00", "12:60", "noon", "07-00", " 07:00 x"])
def test_validate_hhmm_rejects_invalid_times(value):
    with pytest.raises(ValueError, match="invalid time"):
        validate_hhmm(value)


def test_update_schedule_round_trips(db):
    db.enroll_device("board-1")
    device = db.update_schedule(
        "board-1",
        ScheduleUpdate(
            commute_start="07:00",
            commute_end="09:30",
            dim_start="20:00",
            dim_end="23:00",
            dim_brightness=40,
            off_start="23:00",
            off_end="06:00",
        ),
    )
    assert device.commute_start == "07:00"
    assert device.commute_end == "09:30"
    assert device.dim_start == "20:00"
    assert device.dim_end == "23:00"
    assert device.dim_brightness == 40
    assert device.off_start == "23:00"
    assert device.off_end == "06:00"

    reloaded = db.get_device("board-1")
    assert reloaded.commute_start == "07:00"
    assert reloaded.dim_brightness == 40


def test_update_schedule_defaults_are_unset(db):
    db.enroll_device("board-1")
    device = db.update_schedule("board-1", ScheduleUpdate())
    assert device.commute_start is None
    assert device.dim_brightness is None
    assert device.off_end is None


def test_update_schedule_resaving_replaces_the_whole_set(db):
    db.enroll_device("board-1")
    db.update_schedule("board-1", ScheduleUpdate(commute_start="07:00", commute_end="09:00"))
    device = db.update_schedule("board-1", ScheduleUpdate(dim_start="20:00", dim_end="23:00"))
    # Re-saving with a different subset clears fields not included this time --
    # same "save the whole thing" shape as replace_configurations.
    assert device.commute_start is None
    assert device.dim_start == "20:00"


def test_update_schedule_rejects_invalid_time(db):
    db.enroll_device("board-1")
    with pytest.raises(ValueError, match="invalid time"):
        db.update_schedule("board-1", ScheduleUpdate(commute_start="7am", commute_end="09:00"))


@pytest.mark.parametrize("brightness", [-1, 256])
def test_update_schedule_rejects_out_of_range_brightness(db, brightness):
    db.enroll_device("board-1")
    with pytest.raises(ValueError, match="dim_brightness"):
        db.update_schedule("board-1", ScheduleUpdate(dim_brightness=brightness))
