"""Options flow for Magic Climate — menu-driven preset management."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import CONF_PRESETS, PRESET_FAN, PRESET_HIGH, PRESET_LOW, PRESET_MODE, PRESET_NAME


class MagicClimateOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._presets: list[dict[str, Any]] = list(
            entry.options.get(CONF_PRESETS, [])
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "add": "Add preset",
                "edit": "Edit preset",
                "delete": "Delete preset",
                "save": "Save and exit",
            },
        )

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_create_entry(title="", data={CONF_PRESETS: self._presets})
