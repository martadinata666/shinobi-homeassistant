"""Image platform for Shinobi monitors: latest timelapse frame."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ShinobiDataCoordinator
from .entity import ShinobiMonitorEntity, async_add_monitor_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a latest-timelapse-frame image per monitor."""
    coordinator: ShinobiDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_monitor_entities(
        coordinator,
        async_add_entities,
        lambda mid, _monitor: [ShinobiLatestFrameImage(coordinator, mid, hass)],
    )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class ShinobiLatestFrameImage(ShinobiMonitorEntity, ImageEntity):
    """Shows Shinobi's most recent timelapse frame for a monitor.

    Unlike the camera entity's snapshot (always the current live frame),
    this reflects Shinobi's periodic timelapse capture — a distinct "last
    known frame" image, closer to what a doorbell integration's "last
    person" image entity represents.
    """

    _attr_name = "Latest Frame"

    def __init__(
        self, coordinator: ShinobiDataCoordinator, monitor_id: str, hass: HomeAssistant
    ) -> None:
        ShinobiMonitorEntity.__init__(self, coordinator, monitor_id)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_latest_frame"
        self._last_filename: str | None = None

    @property
    def _frame(self) -> dict[str, Any] | None:
        return self.monitor.get("_latest_frame")

    @property
    def available(self) -> bool:
        return super().available and bool(self._frame)

    async def async_image(self) -> bytes | None:
        frame = self._frame
        if not frame or not frame.get("filename"):
            return None
        url = self.coordinator.client.timelapse_frame_url(
            self._monitor_id, frame["filename"]
        )
        session = async_get_clientsession(self.hass)
        try:
            resp = await session.get(url)
            if resp.status != 200:
                return None
            return await resp.read()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Frame fetch failed for %s: %s", self._monitor_id, err)
            return None

    @callback
    def _handle_coordinator_update(self) -> None:
        # image_last_updated drives the entity's state and cache-busting
        # token; only bump it when the underlying frame has actually
        # changed, not on every poll (frames refresh far less often than
        # the default scan interval).
        frame = self._frame
        filename = frame.get("filename") if frame else None
        if filename and filename != self._last_filename:
            self._last_filename = filename
            self._attr_image_last_updated = (
                _parse_time(frame.get("time")) or datetime.now(timezone.utc)
            )
        super()._handle_coordinator_update()
