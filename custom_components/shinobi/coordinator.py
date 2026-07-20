"""Data update coordinator for Shinobi NVR."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ShinobiApiError, ShinobiAuthError, ShinobiClient
from .const import (
    CONF_MOTION_TIMEOUT,
    CONF_SCAN_INTERVAL,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
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
            monitors_by_id[mid] = mon

        return {"monitors": monitors_by_id}
