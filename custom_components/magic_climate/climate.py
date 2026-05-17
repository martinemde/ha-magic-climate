"""Magic Climate wrapper entity."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SOURCE_ENTITY_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Magic Climate platform from a config entry."""
    source_id: str = entry.data[CONF_SOURCE_ENTITY_ID]
    name: str | None = entry.data.get(CONF_NAME)
    async_add_entities(
        [MagicClimate(hass, entry, source_id, name)],
        update_before_add=True,
    )


class MagicClimate(ClimateEntity):
    """Wraps a source climate.* entity and adds UI-configured presets."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        source_entity_id: str,
        name: str | None,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._entry = entry
        self._source_entity_id = source_entity_id
        self._attr_name = name or source_entity_id
        self._attr_unique_id = f"{DOMAIN}::{source_entity_id}"
        self._source_state = None
