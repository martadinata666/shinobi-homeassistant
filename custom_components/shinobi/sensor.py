"""Sensor platform for Shinobi monitors (status)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ShinobiDataCoordinator
from .entity import ShinobiMonitorEntity, async_add_monitor_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a status sensor per monitor, including any added later."""
    coordinator: ShinobiDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_monitor_entities(
        coordinator,
        async_add_entities,
        lambda mid, _monitor: [ShinobiStatusSensor(coordinator, mid)],
    )


class ShinobiStatusSensor(ShinobiMonitorEntity, SensorEntity):
    """Reports the monitor's Shinobi status string."""

    _attr_icon = "mdi:cctv"
    _attr_translation_key = "status"

    def __init__(
        self, coordinator: ShinobiDataCoordinator, monitor_id: str
    ) -> None:
        super().__init__(coordinator, monitor_id)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_status"
        self._attr_name = "Status"

    @property
    def native_value(self) -> str | None:
        return self.monitor.get("status") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        mon = self.monitor
        return {
            "mode": mon.get("mode"),
            "type": mon.get("type"),
            "width": mon.get("width"),
            "height": mon.get("height"),
            "fps": mon.get("fps"),
        }
