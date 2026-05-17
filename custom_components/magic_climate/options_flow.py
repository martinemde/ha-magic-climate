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

    async def async_step_add(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[PRESET_NAME].strip()
            if not name:
                errors[PRESET_NAME] = "name_required"
            elif any(p[PRESET_NAME] == name for p in self._presets):
                errors[PRESET_NAME] = "name_taken"
            elif user_input[PRESET_LOW] >= user_input[PRESET_HIGH]:
                errors["base"] = "low_not_below_high"

            if not errors:
                new_preset: dict[str, Any] = {
                    PRESET_NAME: name,
                    PRESET_LOW: float(user_input[PRESET_LOW]),
                    PRESET_HIGH: float(user_input[PRESET_HIGH]),
                }
                if user_input.get(PRESET_MODE):
                    new_preset[PRESET_MODE] = user_input[PRESET_MODE]
                if user_input.get(PRESET_FAN):
                    new_preset[PRESET_FAN] = user_input[PRESET_FAN]
                self._presets.append(new_preset)
                return await self.async_step_init()

        schema = vol.Schema({
            vol.Required(PRESET_NAME): selector.TextSelector(),
            vol.Required(PRESET_LOW): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
            ),
            vol.Required(PRESET_HIGH): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
            ),
            vol.Optional(PRESET_MODE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(PRESET_FAN): selector.TextSelector(),
        })
        return self.async_show_form(step_id="add", data_schema=schema, errors=errors)
