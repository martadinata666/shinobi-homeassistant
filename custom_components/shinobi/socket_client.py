"""Real-time detector-event relay from Shinobi via its own socket.io server.

Shinobi already has a purpose-built, zero-config channel for exactly this.
``libs/socketio.js``'s ``detector_on`` handler joins a per-monitor
``DETECTOR_{ke}{mid}`` room, with the comment: "Subscribe to detection
events only (no viewer registration, no stream rooms). Used by mobile
clients for push/local notifications." Every triggered event broadcasts to
it (``libs/events/utils.js``: ``s.tx({f:'detector_trigger', id, ke, time,
details, doObjectDetection}, DETECTOR_${ke}${mid})``) — no MQTT broker/topic
configuration, no Shinobi-side setup at all. Connecting and subscribing IS
the entire configuration. Verified end-to-end against the live dev server:
connect -> f:init -> init_success -> detector_on -> a synthetic event fired
via the REST motion-trigger endpoint arrived over the socket as a
detector_trigger with the same shape read from source.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import socketio

from homeassistant.core import HomeAssistant

from .api import ShinobiApiError, ShinobiClient
from .const import DETECTOR_SNAPSHOT_WINDOW_MINUTES, EVENT_DETECTOR_TRIGGER

_LOGGER = logging.getLogger(__name__)

# Once authenticated, this connection is treated like any other Shinobi
# dashboard session and receives everything broadcast to its GRP_{ke} room —
# not just detector events. That includes `users_online`, which carries
# *other users'* plaintext credentials (webdav/B2/etc. passwords), plus disk
# usage and raw FFMPEG stderr lines. Only ever act on — and only ever log —
# the message types below; every other payload is dropped unread and unlogged.
_HANDLED_MESSAGE_TYPES = {"init_success", "detector_trigger"}

SOCKET_RECONNECT_DELAY = 5  # seconds, python-socketio's built-in retry


class ShinobiSocketClient:
    """Maintains one authenticated socket.io connection per config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: ShinobiClient,
        entry_id: str,
        get_monitor_ids: Callable[[], list[str]],
    ) -> None:
        self.hass = hass
        self._client = client
        self._entry_id = entry_id
        self._get_monitor_ids = get_monitor_ids
        self._uid: str | None = None
        self._subscribed: set[str] = set()
        self._sio = socketio.AsyncClient(
            reconnection=True,
            reconnection_delay=SOCKET_RECONNECT_DELAY,
            logger=False,
            engineio_logger=False,
        )
        self._sio.on("connect", self._on_connect)
        self._sio.on("f", self._on_f)
        self._started = False

    async def async_start(self) -> None:
        """Resolve our uid and open the connection. Safe to call once."""
        if self._started:
            return
        self._started = True
        self._uid = await self._client.async_get_own_uid()
        if not self._uid:
            _LOGGER.warning(
                "Shinobi real-time notifications disabled: could not resolve "
                "a uid for the configured API key. In Shinobi, make sure this "
                "key has 'Auth via Socket' enabled."
            )
            return
        try:
            await self._sio.connect(
                self._client.base_url,
                socketio_path="/socket.io",
                transports=["websocket", "polling"],
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Could not connect to Shinobi's socket.io server for "
                "real-time notifications: %s",
                err,
            )

    async def async_stop(self) -> None:
        """Disconnect cleanly on unload/reload."""
        self._started = False
        if self._sio.connected:
            await self._sio.disconnect()

    async def async_ensure_subscribed(self, monitor_ids: list[str]) -> None:
        """Subscribe any monitor id not already joined (e.g. newly discovered)."""
        if not self._sio.connected:
            return
        for monitor_id in monitor_ids:
            if monitor_id not in self._subscribed:
                await self._async_subscribe(monitor_id)

    async def _on_connect(self) -> None:
        self._subscribed.clear()  # room joins don't survive a reconnect
        await self._sio.emit(
            "f",
            {
                "f": "init",
                "ke": self._client.group_key,
                "uid": self._uid,
                "auth": self._client.api_key,
            },
        )

    async def _on_f(self, data: dict[str, Any]) -> None:
        message_type = data.get("f")
        if message_type not in _HANDLED_MESSAGE_TYPES:
            return
        if message_type == "init_success":
            await self.async_ensure_subscribed(self._get_monitor_ids())
            return
        await self._async_handle_detector_trigger(data)

    async def _async_subscribe(self, monitor_id: str) -> None:
        await self._sio.emit(
            "f",
            {
                "f": "monitor",
                "ff": "detector_on",
                "id": monitor_id,
                "ke": self._client.group_key,
            },
        )
        self._subscribed.add(monitor_id)

    async def _async_handle_detector_trigger(self, data: dict[str, Any]) -> None:
        monitor_id = data.get("id")
        event_time = data.get("time")
        details = data.get("details") or {}
        snapshot_url = None
        # Only bother looking up a snapshot for genuine object detections —
        # matches the same `reason == "object"` convention already used for
        # the object-count sensor (see coordinator.py), rather than doing a
        # lookup for every bare ONVIF motion ping, which carries no matrices
        # and can fire far more often than a snapshot would be useful for.
        if monitor_id and event_time and details.get("reason") == "object":
            snapshot_url = await self._async_lookup_snapshot(monitor_id, event_time)
        self.hass.bus.async_fire(
            EVENT_DETECTOR_TRIGGER,
            {
                "entry_id": self._entry_id,
                "monitor_id": monitor_id,
                "time": event_time,
                "reason": details.get("reason"),
                "confidence": details.get("confidence"),
                "matrices": details.get("matrices"),
                "do_object_detection": data.get("doObjectDetection"),
                "snapshot_url": snapshot_url,
            },
        )

    async def _async_lookup_snapshot(
        self, monitor_id: str, event_time: str
    ) -> str | None:
        """Best-effort: find the timelapse frame nearest the event's own time.

        Shinobi doesn't attach a snapshot to the detector_trigger broadcast
        itself, so this reuses the same timelapse-frame index the media
        browser sources thumbnails from — searching a window around the
        event's own timestamp rather than grabbing a fresh live snapshot,
        which would reflect "now" rather than the moment of detection.
        """
        center = _parse_time(event_time)
        if center is None:
            return None
        window = timedelta(minutes=DETECTOR_SNAPSHOT_WINDOW_MINUTES)
        start = _iso(center - window)
        end = _iso(center + window)
        try:
            frame = await self._client.async_get_timelapse_frame(
                monitor_id, start, end
            )
        except ShinobiApiError as err:
            _LOGGER.debug("Snapshot lookup failed for %s: %s", monitor_id, err)
            return None
        if not frame or not frame.get("filename"):
            return None
        return self._client.timelapse_frame_url(monitor_id, frame["filename"])


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


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()
