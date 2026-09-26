import asyncio
import os
import time

import httpx
from google.transit import gtfs_realtime_pb2

# Overridable so tests can point the app at a stub feed server.
FEED_BASE = os.environ.get(
    "DASHBOARD_MTA_FEED_BASE", "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/"
)

# MTA splits subway real-time data across these feed groups; the dict key is
# also the URL suffix (nyct%2F<key>).
FEED_GROUPS: dict[str, list[str]] = {
    "gtfs": ["1", "2", "3", "4", "5", "6", "7", "S", "GS"],
    "gtfs-ace": ["A", "C", "E", "H", "FS"],
    "gtfs-bdfm": ["B", "D", "F", "M"],
    "gtfs-g": ["G"],
    "gtfs-jz": ["J", "Z"],
    "gtfs-nqrw": ["N", "Q", "R", "W"],
    "gtfs-l": ["L"],
    "gtfs-si": ["SI"],
}

ROUTE_TO_FEED_GROUP: dict[str, str] = {
    route: group for group, routes in FEED_GROUPS.items() for route in routes
}


def feed_url(group: str) -> str:
    return f"{FEED_BASE}nyct%2F{group}"


def feed_group_for_route(route_id: str) -> str | None:
    """Express variants (6X, 7X, FX) aren't listed explicitly in FEED_GROUPS;
    they ride the same feed as their base route."""
    group = ROUTE_TO_FEED_GROUP.get(route_id)
    if group is None and route_id.endswith("X"):
        group = ROUTE_TO_FEED_GROUP.get(route_id.removesuffix("X"))
    return group


def feed_groups_for_routes(routes) -> set[str]:
    return {g for r in routes if (g := feed_group_for_route(r)) is not None}


class FeedFetcher:
    """Fetches and decodes a single GTFS-RT feed group per call; any failure
    yields None so one sick feed doesn't take the others down with it."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=15)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self, group: str) -> gtfs_realtime_pb2.FeedMessage | None:
        try:
            resp = await self._client.get(feed_url(group))
            resp.raise_for_status()
            message = gtfs_realtime_pb2.FeedMessage()
            message.ParseFromString(resp.content)
            return message
        except Exception:
            return None


class FeedCache:
    """On-demand, TTL-cached view of the GTFS-RT feeds. Only the feed groups
    actually requested (i.e. those needed by configured services) are ever
    fetched; a group is refetched once its cached copy is older than the TTL."""

    def __init__(self, fetcher: FeedFetcher | None = None, ttl_seconds: float = 25):
        self._fetcher = fetcher or FeedFetcher()
        self._ttl = ttl_seconds
        self._messages: dict[str, gtfs_realtime_pb2.FeedMessage | None] = {}
        self._fetched_at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._fetcher.close()

    def _stale(self, groups: set[str]) -> list[str]:
        now = time.monotonic()
        return [g for g in groups if now - self._fetched_at.get(g, 0) > self._ttl]

    async def messages_for(
        self, groups: set[str]
    ) -> dict[str, gtfs_realtime_pb2.FeedMessage | None]:
        stale = self._stale(groups)
        if stale:
            async with self._lock:
                # Another request may have refreshed while we waited.
                stale = self._stale(groups)
                if stale:
                    results = await asyncio.gather(
                        *(self._fetcher.fetch(g) for g in stale)
                    )
                    for group, message in zip(stale, results):
                        self._messages[group] = message
                        self._fetched_at[group] = time.monotonic()
        return {g: self._messages.get(g) for g in groups}
