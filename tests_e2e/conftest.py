import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2

REPO_ROOT = Path(__file__).resolve().parents[1]

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


def _fixture_feed_bytes() -> bytes:
    """A feed with two trips stopping at Test Station: the A in ~5 minutes
    (heading to Far Away Ave) and the C in ~2."""
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    trips = [
        ("A", [("101N", now_epoch + 5 * 60), ("201N", now_epoch + 25 * 60)]),
        ("C", [("101S", now_epoch + 2 * 60), ("201S", now_epoch + 22 * 60)]),
    ]
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
    return msg.SerializeToString()


@pytest.fixture(scope="session")
def stub_feed_server():
    """Serves the canned GTFS-RT feed for any path; the app under test is
    pointed at it via DASHBOARD_MTA_FEED_BASE."""
    payload = _fixture_feed_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_up(base_url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{base_url}/api/stations", timeout=1)
            return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.2)
    raise RuntimeError(f"server at {base_url} did not come up in time")


@pytest.fixture()
def live_server(tmp_path, stub_feed_server):
    gtfs_dir = tmp_path / "gtfs_static"
    gtfs_dir.mkdir()
    (gtfs_dir / "stations.json").write_text(json.dumps(FIXTURE_INDEX), encoding="utf-8")

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "dashboard_info.main:app",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "DASHBOARD_STATE_DIR": str(tmp_path),
            "DASHBOARD_MTA_FEED_BASE": stub_feed_server,
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_up(base_url)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def enroll_device(base_url: str, device_id: str) -> None:
    req = urllib.request.Request(
        f"{base_url}/api/devices",
        data=json.dumps({"id": device_id}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)


def save_configurations(base_url: str, device_id: str, configurations: list[dict]) -> None:
    req = urllib.request.Request(
        f"{base_url}/api/devices/{device_id}/configurations",
        data=json.dumps({"configurations": configurations}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req, timeout=5)
