"""MediaPlayer Device Entity."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, HOSTNAME

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:  # pylint disable=unused-argument
    """Set up PiAssistantMediaPlayer via config entry."""
    entity = PiAssistantMediaPlayer(config_entry.data.get(HOSTNAME))
    async_add_entities([entity])


class PiAssistantMediaPlayer(MediaPlayerEntity):
    """A media player implementation."""

    def __init__(self, hostname) -> None:
        """Initialize."""
        self.hostname = hostname
        self._attr_supported_features = (
            MediaPlayerEntityFeature.PLAY_MEDIA
            | MediaPlayerEntityFeature.BROWSE_MEDIA
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.SEEK
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
        )
        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER
        self._attr_name = f"{self.hostname} Media Player"
        self._attr_unique_id = self.generate_entity_id(self._attr_name)

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        """Implement the websocket media browsing helper."""
        # If your media player has no own media sources to browse, route all browse commands
        # to the media source integration.
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            # This allows filtering content. In this case it will only show audio sources.
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    def generate_entity_id(self, line: str):
        """Generate Entity Id from string."""
        return re.sub(r"[^a-zA-Z0-9]+", "_", line).lower()

    def device_id(self):
        """Get device id."""
        return self.generate_entity_id(self.hostname)

    @property
    def device_info(self):
        """Device info."""
        return {
            "identifiers": {(DOMAIN, self.generate_entity_id(self.hostname))},
            "name": self.hostname,
            "manufacturer": "PiAssistant",
        }

    def update_states(self):
        """Update via request."""
        try:
            state = requests.get("http://" + self.hostname + "/api/state", timeout=1)
            state = state.json()
            self.state = state["state"]
            self._attr_is_volume_muted = state["is_volume_muted"]
            self._attr_media_duration = state["media_duration"]
            self._attr_media_content_type = state["media_content_type"]
            self._attr_media_position = state["media_position"]
            self._attr_media_position_updated_at = state["media_position_updated_at"]
            self._attr_repeat = state["repeat"]
            self._attr_shuffle = state["shuffle"]
            self._attr_source = state["source"]
            self._attr_volume_level = state["volume_level"]
            self._attr_volume_step = state["volume_step"]
        except TimeoutError:
            self.state = "unavailable"

    async def async_update(self):
        """Async Update states."""
        try:
            await self.hass.async_add_executor_job(self.update_states)
        except Exception as e:  # pylint: disable=broad-except  # noqa: BLE001
            _LOGGER.error("ERROR async_update(): %s", e)

    async def async_send_command(self, command, args):
        """Async send command."""
        try:
            return await self.hass.async_add_executor_job(
                self.send_command, command, args
            )
        except Exception as e:  # pylint: disable=broad-except  # noqa: BLE001
            _LOGGER.error("ERROR async_send_command(): %s", e)

    def send_command(self, command, args) -> bool:
        """Send command to device."""
        try:
            result = requests.post(
                "http://" + self.hostname + "/api/command/" + command,
                json=args,
                timeout=1,
            ).json()
            return result["success"]
        except TimeoutError:
            self.state = "unavailable"
        return False

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Send the play command with media url to the media player."""
        if media_source.is_media_source_id(media_id):
            sourced_media = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = sourced_media.url

        media_id = async_process_play_media_url(self.hass, media_id)
        await self.async_send_command("play_media", {"url": media_id})

    def set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        self.send_command("set_volume", {"volume": volume})

    def media_pause(self) -> None:
        """Send pause command."""
        self.send_command("media_pause", {})

    def media_play(self) -> None:
        """Send play command."""
        self.send_command("media_play", {})

    def media_stop(self) -> None:
        """Send stop command."""
        self.send_command("media_stop", {})

    def mute_volume(self, mute: bool) -> None:
        """Mute the volume."""
        self.send_command("volume_mute", {})
