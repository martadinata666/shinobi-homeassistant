"""PTZ button platform for Shinobi monitors with control enabled."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, PTZ_BUTTONS, monitor_ptz_enabled
from .coordinator import ShinobiDataCoordinator
from .entity import ShinobiMonitorEntity, async_add_monitor_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PTZ buttons, one set per PTZ-enabled monitor.

    PTZ-enabled state (the `control` detail flag) is re-checked for every
    newly seen monitor, including ones that appear after startup — not just
    at initial setup.
    """
    coordinator: ShinobiDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _buttons_for(mid: str, monitor: dict) -> list[ButtonEntity]:
        if not monitor_ptz_enabled(monitor):
            return []
        return [
            ShinobiPtzButton(coordinator, mid, direction, name, icon)
            for direction, name, icon in PTZ_BUTTONS
        ]

    async_add_monitor_entities(coordinator, async_add_entities, _buttons_for)


class ShinobiPtzButton(ShinobiMonitorEntity, ButtonEntity):
    """A single PTZ nudge (or Set Home) action.

    A single Shinobi API call is a complete action — for ONVIF continuous
    moves Shinobi starts the move, waits the monitor's configured
    `control_url_stop_timeout`, then stops it server-side, so the button
    doesn't need to manage press/hold or a separate stop call.
    """

    def __init__(
        self,
        coordinator: ShinobiDataCoordinator,
        monitor_id: str,
        direction: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator, monitor_id)
        self._direction = direction
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_ptz_{direction}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return super().available and monitor_ptz_enabled(self.monitor)

    async def async_press(self) -> None:
        await self.coordinator.client.async_ptz(self._monitor_id, self._direction)
