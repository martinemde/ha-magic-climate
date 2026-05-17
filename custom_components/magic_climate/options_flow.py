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
        self._editing_index: int | None = None

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

    async def async_step_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if not self._presets:
            return await self.async_step_init()

        if user_input is not None:
            name = user_input[PRESET_NAME]
            self._presets = [p for p in self._presets if p[PRESET_NAME] != name]
            return await self.async_step_init()

        schema = vol.Schema({
            vol.Required(PRESET_NAME): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[p[PRESET_NAME] for p in self._presets],
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        })
        return self.async_show_form(step_id="delete", data_schema=schema)

    async def async_step_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if not self._presets:
            return await self.async_step_init()

        if user_input is not None:
            self._editing_index = next(
                (i for i, p in enumerate(self._presets) if p[PRESET_NAME] == user_input[PRESET_NAME]),
                None,
            )
            if self._editing_index is None:
                return await self.async_step_init()
            return await self.async_step_edit_fields()

        schema = vol.Schema({
            vol.Required(PRESET_NAME): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[p[PRESET_NAME] for p in self._presets],
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        })
        return self.async_show_form(step_id="edit", data_schema=schema)

    async def async_step_edit_fields(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        assert self._editing_index is not None
        original = self._presets[self._editing_index]
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[PRESET_NAME].strip()
            if not name:
                errors[PRESET_NAME] = "name_required"
            elif any(
                p[PRESET_NAME] == name and i != self._editing_index
                for i, p in enumerate(self._presets)
            ):
                errors[PRESET_NAME] = "name_taken"
            elif user_input[PRESET_LOW] >= user_input[PRESET_HIGH]:
                errors["base"] = "low_not_below_high"

            if not errors:
                updated: dict[str, Any] = {
                    PRESET_NAME: name,
                    PRESET_LOW: float(user_input[PRESET_LOW]),
                    PRESET_HIGH: float(user_input[PRESET_HIGH]),
                }
                if user_input.get(PRESET_MODE):
                    updated[PRESET_MODE] = user_input[PRESET_MODE]
                if user_input.get(PRESET_FAN):
                    updated[PRESET_FAN] = user_input[PRESET_FAN]
                self._presets[self._editing_index] = updated
                self._editing_index = None
                return await self.async_step_init()

        schema = vol.Schema({
            vol.Required(PRESET_NAME, default=original[PRESET_NAME]): selector.TextSelector(),
            vol.Required(PRESET_LOW, default=original[PRESET_LOW]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
            ),
            vol.Required(PRESET_HIGH, default=original[PRESET_HIGH]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
            ),
            vol.Optional(PRESET_MODE, default=original.get(PRESET_MODE, "")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["", "off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(PRESET_FAN, default=original.get(PRESET_FAN, "")): selector.TextSelector(),
        })
        return self.async_show_form(step_id="edit_fields", data_schema=schema, errors=errors)
