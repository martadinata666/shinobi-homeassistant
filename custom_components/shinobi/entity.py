"""Base entity for Shinobi monitors."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ShinobiDataCoordinator


class ShinobiMonitorEntity(CoordinatorEntity[ShinobiDataCoordinator]):
    """Common base tying an entity to a single Shinobi monitor."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ShinobiDataCoordinator, monitor_id: str
    ) -> None:
        super().__init__(coordinator)
        self._monitor_id = monitor_id
        self._entry_id = coordinator.entry.entry_id

    @property
    def monitor(self) -> dict[str, Any]:
        """Return the latest monitor payload (or empty dict if gone)."""
        return self.coordinator.data.get("monitors", {}).get(self._monitor_id, {})

    @property
    def available(self) -> bool:
        return super().available and bool(self.monitor)

    @property
    def device_info(self) -> DeviceInfo:
        mon = self.monitor
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_{self._monitor_id}")},
            name=mon.get("name") or self._monitor_id,
            manufacturer=MANUFACTURER,
            model=mon.get("type") or "Monitor",
            configuration_url=self.coordinator.client.base_url,
            via_device=(DOMAIN, self._entry_id),
        )
