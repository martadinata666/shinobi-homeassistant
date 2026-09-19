"""Data update coordinator for Shinobi NVR."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ShinobiApiError, ShinobiAuthError, ShinobiClient
from .const import (
    ACTIVE_MODES,
    CONF_MOTION_TIMEOUT,
    CONF_OBJECT_COUNT_HOURS,
    CONF_SCAN_INTERVAL,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_OBJECT_COUNT_HOURS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OBJECT_COUNT_RECOMPUTE_EVERY_N_POLLS,
    OBJECT_COUNT_ROW_CAP,
)

_LOGGER = logging.getLogger(__name__)


def _parse_time(value: str | None) -> datetime | None:
    """Parse a Shinobi ISO timestamp into an aware datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: datetime) -> str:
    """Format a datetime as an ISO-8601 string Shinobi's date filters accept."""
    return dt.replace(microsecond=0).isoformat()


class ShinobiDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll monitors and recent events, deriving per-monitor motion state."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ShinobiClient
    ) -> None:
        self.client = client
        self.entry = entry
        self._motion_timeout = entry.options.get(
            CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT
        )
        # monitor_id -> datetime of most-recent detection event we've seen
        self._last_event_time: dict[str, datetime] = {}
        # Each platform's async_add_monitor_entities() keeps its own private
        # "already added" set (it alone knows which monitor IDs it has turned
        # into entities) but registers that set here so the stale-monitor
        # cleanup in __init__.py can forget an ID from every platform at
        # once when a monitor disappears — otherwise, if that monitor ID
        # later reappears, each platform would think it "already handled"
        # it and never re-add entities for it.
        self.monitor_id_trackers: list[set[str]] = []
        self.object_count_hours = entry.options.get(
            CONF_OBJECT_COUNT_HOURS, DEFAULT_OBJECT_COUNT_HOURS
        )
        # monitor_id -> {tag: count, "_total": N}, recomputed every Nth poll
        # and served from cache in between (see _async_update_object_counts).
        self._object_counts: dict[str, dict[str, int]] = {}
        # monitor_id -> last mode seen that wasn't "stop"/"idle". Shinobi
        # forgets nothing about *how* a monitor was running once it's
        # stopped, so re-enabling it would otherwise always land on "start"
        # (watch-only) even if it had been recording. Tracked here on every
        # poll so ShinobiEnableSwitch can put it back the way it was.
        self.last_active_mode: dict[str, str] = {}
        self._poll_count = 0
        scan = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            monitors = await self.client.async_get_monitors()
            events = await self.client.async_get_events(limit=30)
        except ShinobiAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except ShinobiApiError as err:
            raise UpdateFailed(f"Error communicating with Shinobi: {err}") from err

        # Track newest event time per monitor.
        for event in events:
            mid = event.get("mid")
            ts = _parse_time(event.get("time"))
            if not mid or ts is None:
                continue
            prev = self._last_event_time.get(mid)
            if prev is None or ts > prev:
                self._last_event_time[mid] = ts

        monitor_ids = [mon["mid"] for mon in monitors if mon.get("mid")]
        latest_frames = await self._async_fetch_latest_frames(monitor_ids)
        await self._async_maybe_update_object_counts(monitor_ids)

        now = datetime.now(timezone.utc)
        monitors_by_id: dict[str, dict[str, Any]] = {}
        for mon in monitors:
            mid = mon.get("mid")
            if not mid:
                continue
            last = self._last_event_time.get(mid)
            motion = bool(
                last and (now - last).total_seconds() <= self._motion_timeout
            )
            mon["_motion"] = motion
            mon["_last_motion"] = last.isoformat() if last else None
            mon["_latest_frame"] = latest_frames.get(mid)
            mon["_object_counts"] = self._object_counts.get(mid, {})
            mode = str(mon.get("mode"))
            if mode in ACTIVE_MODES:
                self.last_active_mode[mid] = mode
            monitors_by_id[mid] = mon

        return {"monitors": monitors_by_id}

    async def _async_fetch_latest_frames(
        self, monitor_ids: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        """Fetch the newest timelapse frame for each monitor, in parallel.

        A bare ``?limit=1`` timelapse query (no start/end) is a single cheap
        row lookup — verified live it returns newest-first — so this runs on
        every poll, unlike the heavier windowed object-count query below.
        """

        async def _one(mid: str) -> tuple[str, dict[str, Any] | None]:
            try:
                return mid, await self.client.async_get_timelapse_frame(mid)
            except ShinobiApiError as err:
                _LOGGER.debug("Latest frame fetch failed for %s: %s", mid, err)
                return mid, None

        results = await asyncio.gather(*(_one(mid) for mid in monitor_ids))
        return dict(results)

    async def _async_maybe_update_object_counts(self, monitor_ids: list[str]) -> None:
        """Recompute per-monitor detected-object tag counts, rate-limited.

        An event only counts as an object detection if
        ``details.reason == "object"`` — this is Shinobi's own convention,
        not an assumption: ``libs/videos.js``'s ``listOTags`` (which builds
        the "Objects Found" column in the media table) filters
        ``row.details.reason === 'object'`` before reading
        ``details.matrices``, and Shinobi's reference OpenCV detector
        (``test/opencvMotionTest.js``) always sets `reason: 'object'`
        alongside `matrices` when it reports a detection. Events that carry
        a `matrices` array for some other purpose (e.g. relayed
        Frigate-style zone events, which set `reason` to the zone name) are
        intentionally excluded — mirrors what Shinobi itself counts as an
        "object" event, not just anything with matrices.

        A fresh windowed /events query per monitor can return a lot of
        rows, so this only actually runs every
        OBJECT_COUNT_RECOMPUTE_EVERY_N_POLLS polls; other polls just keep
        serving the last computed counts.
        """
        self._poll_count += 1
        if self._poll_count != 1 and (
            self._poll_count % OBJECT_COUNT_RECOMPUTE_EVERY_N_POLLS != 0
        ):
            return

        since = _iso(
            datetime.now(timezone.utc) - timedelta(hours=self.object_count_hours)
        )

        async def _one(mid: str) -> tuple[str, dict[str, int]]:
            try:
                events = await self.client.async_get_events(
                    mid, limit=OBJECT_COUNT_ROW_CAP, start=since
                )
            except ShinobiApiError as err:
                _LOGGER.debug("Object-count fetch failed for %s: %s", mid, err)
                return mid, self._object_counts.get(mid, {})
            counts: dict[str, int] = {}
            for event in events:
                details = event.get("details") or {}
                if details.get("reason") != "object":
                    continue
                for matrix in details.get("matrices") or []:
                    tag = matrix.get("tag")
                    if tag:
                        counts[tag] = counts.get(tag, 0) + 1
            counts["_total"] = sum(counts.values())
            return mid, counts

        results = await asyncio.gather(*(_one(mid) for mid in monitor_ids))
        self._object_counts = dict(results)
