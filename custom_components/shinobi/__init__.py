"""The Shinobi NVR integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ShinobiApiError, ShinobiAuthError, ShinobiClient
from .const import (
    ATTR_DIRECTION,
    ATTR_MODE,
    CONF_API_KEY,
    CONF_GROUP_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_SSL,
    CONF_VERIFY_SSL,
    DOMAIN,
    MANUFACTURER,
    MODES,
    PLATFORMS,
    PTZ_DIRECTIONS,
    SERVICE_PTZ,
    SERVICE_SET_MODE,
    SERVICE_TRIGGER_MOTION,
    monitor_ptz_enabled,
)
from .coordinator import ShinobiDataCoordinator

_LOGGER = logging.getLogger(__name__)

_PLATFORMS = [Platform(p) for p in PLATFORMS]

SERVICE_SCHEMA_SET_MODE = vol.Schema(
    {
        vol.Required("monitor_id"): cv.string,
        vol.Required(ATTR_MODE): vol.In(MODES),
    }
)
SERVICE_SCHEMA_TRIGGER = vol.Schema({vol.Required("monitor_id"): cv.string})
SERVICE_SCHEMA_PTZ = vol.Schema(
    {
        vol.Required("monitor_id"): cv.string,
        vol.Required(ATTR_DIRECTION): vol.In(PTZ_DIRECTIONS),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shinobi from a config entry."""
    session = async_get_clientsession(hass)
    client = ShinobiClient(
        session=session,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        api_key=entry.data[CONF_API_KEY],
        group_key=entry.data[CONF_GROUP_KEY],
        use_ssl=entry.data.get(CONF_SSL, False),
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, True),
    )

    coordinator = ShinobiDataCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        raise
    except ShinobiAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ShinobiApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model="Shinobi NVR",
        configuration_url=client.base_url,
    )

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            for service in (SERVICE_SET_MODE, SERVICE_TRIGGER_MOTION, SERVICE_PTZ):
                hass.services.async_remove(DOMAIN, service)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _find_coordinator(
    hass: HomeAssistant, monitor_id: str
) -> ShinobiDataCoordinator | None:
    """Return the coordinator for whichever entry knows this monitor id."""
    for coordinator in hass.data.get(DOMAIN, {}).values():
        if monitor_id in coordinator.data.get("monitors", {}):
            return coordinator
    # Fall back to the first configured entry (single-server common case).
    for coordinator in hass.data.get(DOMAIN, {}).values():
        return coordinator
    return None


def _find_client(hass: HomeAssistant, monitor_id: str) -> ShinobiClient | None:
    """Return the client for whichever entry knows this monitor id."""
    coordinator = _find_coordinator(hass, monitor_id)
    return coordinator.client if coordinator else None


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration-level services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_MODE):
        return

    async def _handle_set_mode(call: ServiceCall) -> None:
        monitor_id = call.data["monitor_id"]
        client = _find_client(hass, monitor_id)
        if client is None:
            raise ValueError(f"No Shinobi server knows monitor {monitor_id}")
        await client.async_set_mode(monitor_id, call.data[ATTR_MODE])

    async def _handle_trigger(call: ServiceCall) -> None:
        monitor_id = call.data["monitor_id"]
        client = _find_client(hass, monitor_id)
        if client is None:
            raise ValueError(f"No Shinobi server knows monitor {monitor_id}")
        await client.async_trigger_motion(monitor_id)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_MODE, _handle_set_mode, schema=SERVICE_SCHEMA_SET_MODE
    )
    async def _handle_ptz(call: ServiceCall) -> None:
        monitor_id = call.data["monitor_id"]
        coordinator = _find_coordinator(hass, monitor_id)
        if coordinator is None:
            raise HomeAssistantError(f"No Shinobi server knows monitor {monitor_id}")
        monitor = coordinator.data.get("monitors", {}).get(monitor_id)
        # Guard client-side: Shinobi's /control endpoint hangs until timeout
        # (never calls back to res.end()) when a monitor's `control` detail
        # flag isn't "1", instead of responding with a clean error.
        if not monitor or not monitor_ptz_enabled(monitor):
            raise HomeAssistantError(
                f"PTZ control is not enabled on monitor {monitor_id}"
            )
        await coordinator.client.async_ptz(monitor_id, call.data[ATTR_DIRECTION])

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_MOTION,
        _handle_trigger,
        schema=SERVICE_SCHEMA_TRIGGER,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PTZ, _handle_ptz, schema=SERVICE_SCHEMA_PTZ
    )
