"""Camera platform for Shinobi monitors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ShinobiDataCoordinator
from .entity import ShinobiMonitorEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a camera per Shinobi monitor."""
    coordinator: ShinobiDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ShinobiCamera(coordinator, mid)
        for mid in coordinator.data.get("monitors", {})
    )


class ShinobiCamera(ShinobiMonitorEntity, Camera):
    """A Shinobi monitor exposed as an HA camera."""

    _attr_name = None  # use the device name
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self, coordinator: ShinobiDataCoordinator, monitor_id: str
    ) -> None:
        ShinobiMonitorEntity.__init__(self, coordinator, monitor_id)
        Camera.__init__(self)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_camera"

    async def stream_source(self) -> str | None:
        """Return the HLS stream URL from the monitor's streams list."""
        streams = self.monitor.get("streams") or []
        if not streams:
            return None
        return self.coordinator.client.stream_url(streams[0])

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Fetch a still JPEG snapshot."""
        url = self.coordinator.client.snapshot_url(self._monitor_id)
        session = async_get_clientsession(self.hass)
        try:
            resp = await session.get(url)
            if resp.status != 200:
                return None
            return await resp.read()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Snapshot fetch failed for %s: %s", self._monitor_id, err)
            return None

    @property
    def is_recording(self) -> bool:
        return str(self.monitor.get("mode")) == "record"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        mon = self.monitor
        return {
            "monitor_id": self._monitor_id,
            "status": mon.get("status"),
            "mode": mon.get("mode"),
        }
