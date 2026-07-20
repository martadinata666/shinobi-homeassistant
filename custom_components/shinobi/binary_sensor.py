"""Binary sensor platform (motion) for Shinobi monitors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ShinobiDataCoordinator
from .entity import ShinobiMonitorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a motion binary sensor per monitor."""
    coordinator: ShinobiDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ShinobiMotionSensor(coordinator, mid)
        for mid in coordinator.data.get("monitors", {})
    )


class ShinobiMotionSensor(ShinobiMonitorEntity, BinarySensorEntity):
    """Motion state derived from the monitor's recent detection events."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_translation_key = "motion"

    def __init__(
        self, coordinator: ShinobiDataCoordinator, monitor_id: str
    ) -> None:
        super().__init__(coordinator, monitor_id)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_motion"
        self._attr_name = "Motion"

    @property
    def is_on(self) -> bool:
        return bool(self.monitor.get("_motion"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_motion": self.monitor.get("_last_motion")}
