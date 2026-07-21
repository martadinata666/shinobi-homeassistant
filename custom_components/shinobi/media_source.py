"""Media source for Shinobi NVR: Server -> Camera -> Day -> Clips."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceError,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ShinobiDataCoordinator

_LOGGER = logging.getLogger(__name__)

MIME_TYPES = {
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "ts": "video/mp2t",
}

# Bucketing days client-side (Shinobi has no "list distinct days" endpoint —
# bs5.videosTable.js/bs5.calendar.js do the same thing: pull a date-bounded
# window of /videos and group by day). Bounded to keep this one bulk request
# reasonable on modest hardware; deep history browsing isn't supported in v1.
DAYS_WINDOW = 30
CLIPS_PER_DAY_CAP = 100


async def async_get_media_source(hass: HomeAssistant) -> ShinobiMediaSource:
    """Set up the Shinobi media source."""
    return ShinobiMediaSource(hass)


def _parse_identifier(
    identifier: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Split a media identifier into (entry_id, monitor_id, day, filename)."""
    if not identifier:
        return None, None, None, None
    parts = identifier.split("/", 3)
    parts += [None] * (4 - len(parts))
    return parts[0], parts[1], parts[2], parts[3]


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


class ShinobiMediaSource(MediaSource):
    """Browse Shinobi recordings from Home Assistant's media browser."""

    name = "Shinobi NVR"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _coordinators(self) -> dict[str, ShinobiDataCoordinator]:
        return self.hass.data.get(DOMAIN, {})

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        entry_id, monitor_id, _day, filename = _parse_identifier(item.identifier)
        coordinator = self._coordinators().get(entry_id) if entry_id else None
        if not coordinator or not monitor_id or not filename:
            raise MediaSourceError("Media not found")
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp4"
        mime = MIME_TYPES.get(ext, "video/mp4")
        url = coordinator.client.video_url(monitor_id, filename)
        return PlayMedia(url, mime)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        entry_id, monitor_id, day, _filename = _parse_identifier(item.identifier)
        if entry_id is None:
            return self._browse_servers()
        coordinator = self._coordinators().get(entry_id)
        if coordinator is None:
            raise MediaSourceError(f"Unknown Shinobi server: {entry_id}")
        if monitor_id is None:
            return self._browse_monitors(entry_id, coordinator)
        if day is None:
            return await self._browse_days(entry_id, monitor_id, coordinator)
        return await self._browse_clips(entry_id, monitor_id, day, coordinator)

    def _browse_servers(self) -> BrowseMediaSource:
        base = BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=self.name,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.DIRECTORY,
        )
        base.children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=entry_id,
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=coordinator.entry.title,
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.DIRECTORY,
            )
            for entry_id, coordinator in self._coordinators().items()
        ]
        return base

    def _browse_monitors(
        self, entry_id: str, coordinator: ShinobiDataCoordinator
    ) -> BrowseMediaSource:
        base = BrowseMediaSource(
            domain=DOMAIN,
            identifier=entry_id,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=coordinator.entry.title,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.DIRECTORY,
        )
        base.children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/{mid}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=monitor.get("name") or mid,
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.DIRECTORY,
            )
            for mid, monitor in coordinator.data.get("monitors", {}).items()
        ]
        return base

    async def _browse_days(
        self, entry_id: str, monitor_id: str, coordinator: ShinobiDataCoordinator
    ) -> BrowseMediaSource:
        monitor = coordinator.data.get("monitors", {}).get(monitor_id, {})
        window_end = datetime.now(timezone.utc)
        window_start = window_end - timedelta(days=DAYS_WINDOW)
        videos = await coordinator.client.async_get_videos(
            monitor_id, limit=None, start=_iso(window_start), end=_iso(window_end)
        )
        counts: dict[str, int] = {}
        for video in videos:
            time_str = video.get("time") or ""
            if len(time_str) < 10:
                continue
            counts[time_str[:10]] = counts.get(time_str[:10], 0) + 1

        base = BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{monitor_id}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=monitor.get("name") or monitor_id,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.DIRECTORY,
        )
        base.children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/{monitor_id}/{day}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=f"{_format_day(day)} ({count} clip{'s' if count != 1 else ''})",
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.VIDEO,
            )
            for day, count in sorted(counts.items(), reverse=True)
        ]
        return base

    async def _browse_clips(
        self,
        entry_id: str,
        monitor_id: str,
        day: str,
        coordinator: ShinobiDataCoordinator,
    ) -> BrowseMediaSource:
        monitor = coordinator.data.get("monitors", {}).get(monitor_id, {})
        client = coordinator.client
        videos = await client.async_get_videos(
            monitor_id,
            limit=None,
            start=f"{day}T00:00:00",
            end=f"{day}T23:59:59",
        )
        videos.sort(key=lambda v: v.get("time") or "", reverse=True)
        videos = videos[:CLIPS_PER_DAY_CAP]

        thumbnails = await asyncio.gather(
            *(self._thumbnail(client, monitor_id, v) for v in videos)
        )

        base = BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{monitor_id}/{day}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"{monitor.get('name') or monitor_id} - {_format_day(day)}",
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.VIDEO,
        )
        base.children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/{monitor_id}/{day}/{video['filename']}",
                media_class=MediaClass.VIDEO,
                media_content_type=MediaType.VIDEO,
                title=_clip_title(video),
                can_play=True,
                can_expand=False,
                thumbnail=thumbnail,
            )
            for video, thumbnail in zip(videos, thumbnails)
            if video.get("filename")
        ]
        return base

    @staticmethod
    async def _thumbnail(
        client: Any, monitor_id: str, video: dict[str, Any]
    ) -> str | None:
        start = video.get("time")
        end = video.get("end") or start
        if not start:
            return None
        try:
            frame = await client.async_get_timelapse_frame(monitor_id, start, end)
        except Exception:  # noqa: BLE001 - a missing thumbnail isn't fatal
            return None
        if not frame or not frame.get("filename"):
            return None
        return client.timelapse_frame_url(monitor_id, frame["filename"])


def _format_day(day: str) -> str:
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    except ValueError:
        return day


def _clip_title(video: dict[str, Any]) -> str:
    time_str = (video.get("time") or "")[11:19]  # HH:MM:SS
    end_str = (video.get("end") or "")[11:19]
    size = video.get("size")
    size_mb = f"{size / 1_048_576:.1f} MB" if isinstance(size, (int, float)) else ""
    parts = [p for p in (f"{time_str}-{end_str}" if end_str else time_str, size_mb) if p]
    return " · ".join(parts) or video.get("filename", "Clip")
