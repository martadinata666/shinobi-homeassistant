"""Base entity for Shinobi monitors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ShinobiDataCoordinator

# HA deprecated DeviceInfo's `via_device` (a (domain, identifier) tuple) in
# favour of `via_device_id` (the hub device's registry id). On versions that
# have made the switch, passing `via_device` raises from
# device_registry.async_get_or_create — and because a DeviceInfo property is
# evaluated from inside HA's own helper frames, the deprecation reporter
# can't attribute it to a custom integration (MissingIntegrationFrame) and
# escalates the warning to a RuntimeError, which aborts adding the entity.
# Detect which key this HA understands instead of pinning a version.
_SUPPORTS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__
_SUPPORTS_MODEL_ID = "model_id" in DeviceInfo.__annotations__


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
        name = mon.get("name") or self._monitor_id
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_{self._monitor_id}")},
            name=name,
            manufacturer=MANUFACTURER,
            # HA renders `model` as the device-card heading (the area-picker
            # and device-info cards both show model + manufacturer above the
            # editable name), so the monitor's own name goes here. Shinobi's
            # monitor `type` is the stream codec (h264/mjpeg/...), which is
            # what `model_id` is for.
            model=name,
            configuration_url=self.coordinator.client.base_url,
        )
        if _SUPPORTS_MODEL_ID and mon.get("type"):
            info["model_id"] = mon["type"]
        if _SUPPORTS_VIA_DEVICE_ID:
            # coordinator.hass, not self.hass: device_info is read while the
            # entity is being added, before its own hass attribute is set on
            # every HA version.
            hub = dr.async_get(self.coordinator.hass).async_get_device(
                identifiers={(DOMAIN, self._entry_id)}
            )
            if hub is not None:
                info["via_device_id"] = hub.id
        else:
            info["via_device"] = (DOMAIN, self._entry_id)
        return info


def async_add_monitor_entities(
    coordinator: ShinobiDataCoordinator,
    async_add_entities: AddEntitiesCallback,
    entities_for_monitor: Callable[[str, dict[str, Any]], list[Entity]],
) -> None:
    """Create entities per monitor now, and for any monitor that appears later.

    Shinobi has no push notification for "a monitor was added" — the
    coordinator just picks up new monitor IDs on its next poll. Platforms
    only get an `async_add_entities` callback once, at setup, so without this
    a monitor added after HA starts stays invisible until the integration is
    reloaded. This mirrors the coordinator's poll cycle via
    `async_add_listener` and diffs against monitors already turned into
    entities, adding only what's new each time.

    ``entities_for_monitor(monitor_id, monitor)`` returns the list of
    entities for one monitor (possibly filtered/empty, e.g. PTZ buttons only
    for control-enabled monitors) and is re-evaluated for every newly seen
    monitor, not just at startup.
    """
    known_monitor_ids: set[str] = set()
    coordinator.monitor_id_trackers.append(known_monitor_ids)

    def _sync() -> None:
        new_entities: list[Entity] = []
        for mid, monitor in coordinator.data.get("monitors", {}).items():
            if mid in known_monitor_ids:
                continue
            known_monitor_ids.add(mid)
            new_entities.extend(entities_for_monitor(mid, monitor))
        if new_entities:
            async_add_entities(new_entities)

    _sync()
    coordinator.async_add_listener(_sync)
