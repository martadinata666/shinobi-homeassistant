"""Sensor platform for Shinobi monitors (status)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
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
    """Set up status + object-count sensors per monitor, including any added later."""
    coordinator: ShinobiDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_monitor_entities(
        coordinator,
        async_add_entities,
        lambda mid, _monitor: [
            ShinobiStatusSensor(coordinator, mid),
            ShinobiObjectCountSensor(coordinator, mid),
        ],
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


class ShinobiObjectCountSensor(ShinobiMonitorEntity, SensorEntity):
    """Total detected-object count over a rolling lookback window.

    Shinobi events carry detections in `details.matrices[]`, each with a
    `tag` (e.g. "person", "car") — populated by Shinobi's own object
    detector or by relayed Frigate-style MQTT events (see
    dropInEvents/mqtt.js's frigate handler). Plain ONVIF motion events carry
    no matrices, so this stays at 0 on monitors without an object detector.
    The state is the total detection count across all tags; the breakdown
    per tag is in `extra_state_attributes`.
    """

    _attr_icon = "mdi:shape-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "objects"
    _attr_translation_key = "object_count"

    def __init__(
        self, coordinator: ShinobiDataCoordinator, monitor_id: str
    ) -> None:
        super().__init__(coordinator, monitor_id)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_object_count"
        self._attr_name = "Object Count"

    @property
    def _counts(self) -> dict[str, int]:
        return self.monitor.get("_object_counts") or {}

    @property
    def native_value(self) -> int:
        return self._counts.get("_total", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "lookback_hours": self.coordinator.object_count_hours,
            "counts_by_tag": {
                tag: count for tag, count in self._counts.items() if tag != "_total"
            },
        }
