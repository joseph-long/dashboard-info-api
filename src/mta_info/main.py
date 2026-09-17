import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import (
    ConfigurationUpdate,
    Database,
    DeviceExistsError,
    DuplicateServiceError,
    ScheduleUpdate,
)
from .departures import compute_departures, merge_departures
from .feeds import FeedCache, feed_groups_for_routes
from .gtfs_static import load_index, refresh_static_gtfs
from .paths import db_path
from .word_of_the_day import WordOfTheDayCache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class EnrollRequest(BaseModel):
    id: str = Field(min_length=1, max_length=200)


class ConfigurationPayload(BaseModel):
    origin_station_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    direction: Literal["uptown", "downtown"]
    destination_station_id: str | None = None
    destination_walk_minutes: int | None = Field(default=None, ge=0)


class ConfigurationsPayload(BaseModel):
    configurations: list[ConfigurationPayload]


class SchedulePayload(BaseModel):
    """A device's commute/dim/off windows -- self-luminous displays only, see
    README. All fields optional; omitted/null disables that window."""

    commute_start: str | None = None
    commute_end: str | None = None
    dim_start: str | None = None
    dim_end: str | None = None
    dim_brightness: int | None = Field(default=None, ge=0, le=255)
    off_start: str | None = None
    off_end: str | None = None


async def _fetch_static_gtfs_if_missing(app: FastAPI) -> None:
    """The station pickers need static GTFS data to exist at all; fetch it
    once on first run so the config page has stations to pick from. On later
    runs the cache from a previous fetch is reused."""
    if app.state.gtfs_index.stations:
        return
    logger.info("No cached static GTFS data found; fetching it now")
    try:
        app.state.gtfs_index = await refresh_static_gtfs()
    except Exception:
        logger.exception(
            "Failed to fetch static GTFS data on startup; use Refresh on the "
            "configure page once network access to MTA is available"
        )
        return
    logger.info("Fetched static GTFS data: %d stations", len(app.state.gtfs_index.stations))


def create_app(
    feed_cache: FeedCache | None = None,
    wotd_cache: WordOfTheDayCache | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = Database(db_path())
        app.state.gtfs_index = load_index()
        if app.state.feed_cache is None:
            app.state.feed_cache = FeedCache()
        if app.state.wotd_cache is None:
            app.state.wotd_cache = WordOfTheDayCache()
        bootstrap_task = asyncio.create_task(_fetch_static_gtfs_if_missing(app))
        try:
            yield
        finally:
            bootstrap_task.cancel()
            await app.state.feed_cache.close()
            await app.state.wotd_cache.close()

    app = FastAPI(lifespan=lifespan)
    app.state.feed_cache = feed_cache
    app.state.wotd_cache = wotd_cache

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # -- pages -----------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def devices_page():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/devices/{device_id}/configure", include_in_schema=False)
    async def configure_page(device_id: str):
        return FileResponse(STATIC_DIR / "configure.html")

    @app.get("/devices/{device_id}/dashboard", include_in_schema=False)
    async def dashboard_page(device_id: str):
        return FileResponse(STATIC_DIR / "dashboard.html")

    # -- devices & configurations (web UI backend) ------------------------

    @app.get("/api/devices")
    async def list_devices(request: Request):
        return [asdict(d) for d in request.app.state.db.list_devices()]

    @app.post("/api/devices", status_code=201)
    async def enroll_device(request: Request, payload: EnrollRequest):
        try:
            device = request.app.state.db.enroll_device(payload.id)
        except DeviceExistsError:
            raise HTTPException(409, f"device already enrolled: {payload.id}")
        return asdict(device)

    @app.get("/api/devices/{device_id}")
    async def get_device(request: Request, device_id: str):
        db: Database = request.app.state.db
        device = db.get_device(device_id)
        if device is None:
            raise HTTPException(404, f"unknown device: {device_id}")
        return {
            **asdict(device),
            "configurations": [
                asdict(c) for c in db.get_configurations(device_id)
            ],
        }

    @app.delete("/api/devices/{device_id}", status_code=204)
    async def delete_device(request: Request, device_id: str):
        db: Database = request.app.state.db
        if not db.delete_device(device_id):
            raise HTTPException(404, f"unknown device: {device_id}")

    @app.put("/api/devices/{device_id}/configurations")
    async def put_configurations(
        request: Request, device_id: str, payload: ConfigurationsPayload
    ):
        db: Database = request.app.state.db
        if db.get_device(device_id) is None:
            raise HTTPException(404, f"unknown device: {device_id}")
        try:
            configs = db.replace_configurations(
                device_id,
                [ConfigurationUpdate(**c.model_dump()) for c in payload.configurations],
            )
        except DuplicateServiceError as exc:
            raise HTTPException(
                409, f"only one configuration per service is allowed: {exc.service}"
            )
        return [asdict(c) for c in configs]

    @app.put("/api/devices/{device_id}/schedule")
    async def put_schedule(request: Request, device_id: str, payload: SchedulePayload):
        db: Database = request.app.state.db
        if db.get_device(device_id) is None:
            raise HTTPException(404, f"unknown device: {device_id}")
        try:
            device = db.update_schedule(device_id, ScheduleUpdate(**payload.model_dump()))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return asdict(device)

    # -- stations / GTFS ---------------------------------------------------

    @app.get("/api/stations")
    async def list_stations(request: Request):
        index = request.app.state.gtfs_index
        stations = [
            {"id": s.id, "name": s.name, "routes": s.routes}
            for s in sorted(index.stations.values(), key=lambda s: s.name)
        ]
        return {"fetched_at": index.fetched_at, "stations": stations}

    @app.post("/api/gtfs/refresh")
    async def refresh_gtfs(request: Request):
        try:
            new_index = await refresh_static_gtfs()
        except Exception as exc:
            logger.exception("failed to refresh static GTFS data")
            raise HTTPException(502, f"failed to fetch GTFS data: {exc}") from exc
        request.app.state.gtfs_index = new_index
        return {
            "fetched_at": new_index.fetched_at,
            "station_count": len(new_index.stations),
        }

    # -- the device-facing API ----------------------------------------------
    # Every route a device calls unauthenticated over the open internet lives
    # under /public/devices/{device_id}/... -- the device id is a path
    # segment, not a header, so the reverse proxy in front of this app can
    # allow-list the whole /public/ prefix once and never need to change again
    # when a new device-facing route is added (see infra's mta-info-api.nix).

    @app.get("/public/devices/{device_id}/departures")
    async def public_departures(
        request: Request, device_id: str, max_departures: int = Query(default=3, ge=1)
    ):
        db: Database = request.app.state.db
        if db.get_device(device_id) is None:
            raise HTTPException(404, f"unknown device: {device_id}")
        db.touch_device(device_id)

        configs = [c for c in db.get_configurations(device_id) if c.is_complete]
        index = request.app.state.gtfs_index
        groups = feed_groups_for_routes([c.service for c in configs])
        messages = await request.app.state.feed_cache.messages_for(groups)
        by_config = [compute_departures(messages, c, index) for c in configs]
        return [d.to_dict() for d in merge_departures(by_config, max_departures)]

    @app.get("/public/devices/{device_id}/schedule")
    async def public_schedule(request: Request, device_id: str):
        db: Database = request.app.state.db
        device = db.get_device(device_id)
        if device is None:
            raise HTTPException(404, f"unknown device: {device_id}")
        db.touch_device(device_id)
        return {
            "commute_start": device.commute_start,
            "commute_end": device.commute_end,
            "dim_start": device.dim_start,
            "dim_end": device.dim_end,
            "dim_brightness": device.dim_brightness,
            "off_start": device.off_start,
            "off_end": device.off_end,
        }

    # Not device-specific -- same content for every device -- so it isn't
    # nested under /devices/{device_id}/, but it still lives under /public/
    # since devices call it unauthenticated over the open internet.
    @app.get("/public/word-of-the-day")
    async def public_word_of_the_day(request: Request):
        entries = await request.app.state.wotd_cache.entries_for_today()
        return {lang: (asdict(entry) if entry else None) for lang, entry in entries.items()}

    return app


app = create_app()


def main():
    parser = argparse.ArgumentParser(description="MTA info API server")
    parser.add_argument("--host", default=os.environ.get("MTA_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MTA_PORT", "8000"))
    )
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("mta_info.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
