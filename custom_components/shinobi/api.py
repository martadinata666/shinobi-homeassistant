"""Thin async client for the Shinobi NVR HTTP API."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import async_timeout

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20


class ShinobiApiError(Exception):
    """Raised when a Shinobi API request fails."""


class ShinobiAuthError(ShinobiApiError):
    """Raised when authentication (API key / group key) is rejected."""


class ShinobiClient:
    """Minimal wrapper around the Shinobi REST API.

    All authenticated endpoints are shaped ``/{api_key}/{action}/{group_key}/...``.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        api_key: str,
        group_key: str,
        use_ssl: bool = False,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self._host = host
        self._port = port
        self._api_key = api_key
        self._group_key = group_key
        self._scheme = "https" if use_ssl else "http"
        self._verify_ssl = verify_ssl

    @property
    def base_url(self) -> str:
        """Return the server root, e.g. ``http://10.1.104.34:8080``."""
        return f"{self._scheme}://{self._host}:{self._port}"

    @property
    def group_key(self) -> str:
        return self._group_key

    def api_path(self, path: str) -> str:
        """Return an absolute URL for an api-key-prefixed ``path``."""
        return f"{self.base_url}/{self._api_key}/{path.lstrip('/')}"

    def stream_url(self, stream_path: str) -> str:
        """Resolve a monitor ``streams[]`` entry to an absolute URL.

        Shinobi already includes the api key in the stream path, so we only
        prepend the server root.
        """
        return f"{self.base_url}/{stream_path.lstrip('/')}"

    def snapshot_url(self, monitor_id: str) -> str:
        """Full JPEG snapshot URL for a monitor."""
        return self.api_path(f"jpeg/{self._group_key}/{monitor_id}/s.jpg")

    def embed_url(self, monitor_id: str) -> str:
        """Shinobi's embeddable player page for a monitor.

        Renders Shinobi's own client-side player (hls.js/flv.js) in the
        browser, so playback goes straight from the browser to Shinobi with
        no server-side re-transcode. Meant to be dropped into an iframe /
        Webpage Lovelace card for smoother live view than HA's built-in
        camera stream dialog, which re-muxes the stream through its own
        `stream` integration.

        The trailing ``:addon`` path segment is a pipe-delimited flag list
        (see Shinobi's ``web/pages/embed.ejs``). ``jquery`` is not optional —
        the player script (``bs5.embed.js``) references jQuery at module
        load time and throws immediately without it, so the page silently
        never initializes a player. ``gui`` pulls in the stream-chrome CSS
        and ``fullscreen`` adds a fullscreen toggle button.
        """
        addons = "fullscreen|jquery|gui"
        return self.api_path(f"embed/{self._group_key}/{monitor_id}/{addons}")

    async def _get_json(self, path: str) -> Any:
        url = self.api_path(path)
        try:
            async with async_timeout.timeout(REQUEST_TIMEOUT):
                resp = await self._session.get(url, ssl=self._verify_ssl)
                if resp.status == 401 or resp.status == 403:
                    raise ShinobiAuthError(f"Unauthorized ({resp.status}) for {path}")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except ShinobiApiError:
            raise
        except aiohttp.ClientResponseError as err:
            raise ShinobiApiError(f"HTTP {err.status} for {path}") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ShinobiApiError(f"Request to {path} failed: {err}") from err
        # Shinobi returns {"ok": false, "msg": ...} on auth/validation errors
        if isinstance(data, dict) and data.get("ok") is False:
            msg = str(data.get("msg", "")).lower()
            if "auth" in msg or "not authorized" in msg:
                raise ShinobiAuthError(data.get("msg", "Not authorized"))
        return data

    async def async_get_monitors(self) -> list[dict[str, Any]]:
        """Return the list of monitors for the configured group."""
        data = await self._get_json(f"monitor/{self._group_key}")
        if isinstance(data, dict):
            # A single-monitor query returns an object; normalise to a list.
            data = [data]
        if not isinstance(data, list):
            raise ShinobiApiError("Unexpected monitor payload")
        return data

    async def async_get_events(
        self, monitor_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return recent detection/motion events (newest first)."""
        path = f"events/{self._group_key}"
        if monitor_id:
            path += f"/{monitor_id}"
        path += f"?limit={limit}"
        data = await self._get_json(path)
        if isinstance(data, dict):
            data = data.get("events", [])
        return data if isinstance(data, list) else []

    async def async_get_videos(
        self, monitor_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return recorded video clips."""
        path = f"videos/{self._group_key}"
        if monitor_id:
            path += f"/{monitor_id}"
        path += f"?limit={limit}"
        data = await self._get_json(path)
        if isinstance(data, dict):
            data = data.get("videos", [])
        return data if isinstance(data, list) else []

    async def async_set_mode(self, monitor_id: str, mode: str) -> None:
        """Change a monitor's mode (start/record/stop/idle)."""
        await self._get_json(f"monitor/{self._group_key}/{monitor_id}/{mode}")

    async def async_trigger_motion(self, monitor_id: str) -> None:
        """Fire an external motion trigger for a monitor."""
        await self._get_json(f"motion/{self._group_key}/{monitor_id}")

    async def async_validate(self) -> list[dict[str, Any]]:
        """Validate credentials by fetching monitors; used by the config flow."""
        return await self.async_get_monitors()
