"""Switch platform for Shinobi monitors (enable + recording)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import ACTIVE_MODES, DOMAIN, MODE_RECORD, MODE_START, MODE_STOP
from .coordinator import ShinobiDataCoordinator
from .entity import ShinobiMonitorEntity, async_add_monitor_entities

ATTR_LAST_ACTIVE_MODE = "last_active_mode"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up enable + recording switches per monitor, including any added later."""
    coordinator: ShinobiDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_monitor_entities(
        coordinator,
        async_add_entities,
        lambda mid, _monitor: [
            ShinobiEnableSwitch(coordinator, mid),
            ShinobiRecordSwitch(coordinator, mid),
        ],
    )


class _ShinobiBaseSwitch(ShinobiMonitorEntity, SwitchEntity):
    """Shared refresh helper for mode-changing switches."""

    _attr_device_class = SwitchDeviceClass.SWITCH

    async def _set_mode(self, mode: str) -> None:
        await self.coordinator.client.async_set_mode(self._monitor_id, mode)
        await self.coordinator.async_request_refresh()


class ShinobiEnableSwitch(_ShinobiBaseSwitch, RestoreEntity):
    """Enable vs disable (stop) a monitor.

    Enabling restores whichever active mode the monitor was last in —
    a monitor that was recording before being disabled comes back
    recording, not watch-only. The coordinator tracks that mode while HA
    runs; it's also written to this entity's state attributes so the
    choice survives a restart taken while the monitor was stopped.
    """

    def __init__(
        self, coordinator: ShinobiDataCoordinator, monitor_id: str
    ) -> None:
        super().__init__(coordinator, monitor_id)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_enabled"
        self._attr_name = "Enabled"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Only seed from the restored state if this run hasn't already seen
        # the monitor in an active mode — live data always wins.
        if self._monitor_id in self.coordinator.last_active_mode:
            return
        last_state = await self.async_get_last_state()
        restored = (last_state.attributes.get(ATTR_LAST_ACTIVE_MODE)
                    if last_state else None)
        if restored in ACTIVE_MODES:
            self.coordinator.last_active_mode[self._monitor_id] = restored

    @property
    def is_on(self) -> bool:
        return str(self.monitor.get("mode")) != MODE_STOP

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_LAST_ACTIVE_MODE: self.coordinator.last_active_mode.get(
                self._monitor_id, MODE_START
            )
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_mode(
            self.coordinator.last_active_mode.get(self._monitor_id, MODE_START)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_mode(MODE_STOP)


class ShinobiRecordSwitch(_ShinobiBaseSwitch):
    """Toggle continuous recording (record) vs watch-only (start)."""

    def __init__(
        self, coordinator: ShinobiDataCoordinator, monitor_id: str
    ) -> None:
        super().__init__(coordinator, monitor_id)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_record"
        self._attr_name = "Recording"

    @property
    def available(self) -> bool:
        # Recording is meaningless while the monitor is disabled.
        return super().available and str(self.monitor.get("mode")) != MODE_STOP

    @property
    def is_on(self) -> bool:
        return str(self.monitor.get("mode")) == MODE_RECORD

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_mode(MODE_RECORD)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_mode(MODE_START)
