"""Options flow for Magic Climate — basic options + per-preset menus."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers import selector

from .const import (
    CONF_ENABLED_PRESETS,
    CONF_PRESETS,
    CONF_SOURCE_ENTITY_ID,
    PRESET_DEFAULTS,
    PRESET_FAN,
    PRESET_HIGH,
    PRESET_LOW,
    PRESET_MODE,
    STANDARD_PRESETS,
    TEMP_MAX_C,
    TEMP_MIN_C,
)

_HVAC_MODES = ["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"]


class MagicClimateOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._source_entity_id: str = entry.data[CONF_SOURCE_ENTITY_ID]
        self._enabled: list[str] = list(
            entry.options.get(CONF_ENABLED_PRESETS, []) or []
        )
        existing_presets = entry.options.get(CONF_PRESETS, {}) or {}
        # Carry every standard preset's stored config (even if not enabled) so
        # toggling a preset on later restores its previous values.
        self._presets: dict[str, dict[str, Any]] = {
            pid: dict(existing_presets.get(pid, PRESET_DEFAULTS[pid]))
            for pid in STANDARD_PRESETS
        }

    # ------------------------------------------------------------------ menu

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        menu_options: dict[str, str] = {"basic": "Basic Options"}
        for pid in STANDARD_PRESETS:
            if pid in self._enabled:
                menu_options[f"preset_{pid}"] = f"{pid.title()} Preset"
        menu_options["save"] = "Save and exit"
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        # Persist only currently-enabled presets in the stored options. Keep
        # ordering aligned with STANDARD_PRESETS so the UI is stable.
        stored_presets = {
            pid: self._presets[pid] for pid in STANDARD_PRESETS if pid in self._enabled
        }
        # Update entry.data if the source entity changed (config flow sets it
        # there originally; basic options lets the user move the wrapper to a
        # different climate device without recreating the entry).
        if self._source_entity_id != self._entry.data.get(CONF_SOURCE_ENTITY_ID):
            new_data = dict(self._entry.data)
            new_data[CONF_SOURCE_ENTITY_ID] = self._source_entity_id
            self.hass.config_entries.async_update_entry(
                self._entry,
                data=new_data,
                unique_id=f"magic_climate::{self._source_entity_id}",
            )
        return self.async_create_entry(
            title="",
            data={
                CONF_ENABLED_PRESETS: [pid for pid in STANDARD_PRESETS if pid in self._enabled],
                CONF_PRESETS: stored_presets,
            },
        )

    # ----------------------------------------------------------- basic step

    async def async_step_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._source_entity_id = user_input[CONF_SOURCE_ENTITY_ID]
            self._enabled = [pid for pid in STANDARD_PRESETS if user_input.get(pid)]
            return await self.async_step_init()

        schema = vol.Schema({
            vol.Required(
                CONF_SOURCE_ENTITY_ID, default=self._source_entity_id
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate")
            ),
            **{
                vol.Required(pid, default=pid in self._enabled): selector.BooleanSelector()
                for pid in STANDARD_PRESETS
            },
        })
        return self.async_show_form(step_id="basic", data_schema=schema)

    # ---------------------------------------------------------- preset step

    async def _async_preset_step(
        self, preset_id: str, user_input: dict[str, Any] | None
    ) -> config_entries.ConfigFlowResult:
        # Guard against the (unreachable from UI) case of a disabled preset.
        if preset_id not in self._enabled:
            return await self.async_step_init()

        existing = self._presets[preset_id]
        errors: dict[str, str] = {}

        if user_input is not None:
            low_c = self._display_to_c(float(user_input[PRESET_LOW]))
            high_c = self._display_to_c(float(user_input[PRESET_HIGH]))
            if low_c >= high_c:
                errors["base"] = "low_not_below_high"

            if not errors:
                updated: dict[str, Any] = {
                    PRESET_LOW: round(low_c, 2),
                    PRESET_HIGH: round(high_c, 2),
                }
                if user_input.get(PRESET_MODE):
                    updated[PRESET_MODE] = user_input[PRESET_MODE]
                fan = (user_input.get(PRESET_FAN) or "").strip()
                if fan:
                    updated[PRESET_FAN] = fan
                self._presets[preset_id] = updated
                return await self.async_step_init()

        schema = vol.Schema({
            vol.Required(
                PRESET_LOW, default=self._c_to_display(existing[PRESET_LOW]),
            ): self._temp_selector(),
            vol.Required(
                PRESET_HIGH, default=self._c_to_display(existing[PRESET_HIGH]),
            ): self._temp_selector(),
            vol.Optional(
                PRESET_MODE, default=existing.get(PRESET_MODE, ""),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["", *_HVAC_MODES],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                PRESET_FAN, default=existing.get(PRESET_FAN, ""),
            ): selector.TextSelector(),
        })
        return self.async_show_form(
            step_id=f"preset_{preset_id}",
            data_schema=schema,
            errors=errors,
            description_placeholders={"preset": preset_id.title()},
        )

    # ---------------------------------------------------------- unit helpers

    @property
    def _fahrenheit(self) -> bool:
        return self.hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT

    def _c_to_display(self, c: float) -> float:
        if self._fahrenheit:
            return round(c * 9.0 / 5.0 + 32.0, 1)
        return round(c, 1)

    def _display_to_c(self, val: float) -> float:
        if self._fahrenheit:
            return (val - 32.0) * 5.0 / 9.0
        return val

    def _temp_selector(self) -> selector.NumberSelector:
        if self._fahrenheit:
            return selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=round(TEMP_MIN_C * 9.0 / 5.0 + 32.0),
                    max=round(TEMP_MAX_C * 9.0 / 5.0 + 32.0),
                    step=1.0,
                    unit_of_measurement="°F",
                )
            )
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=TEMP_MIN_C,
                max=TEMP_MAX_C,
                step=0.5,
                unit_of_measurement="°C",
            )
        )


# Bind seven async_step_preset_<id> methods. They all funnel into
# _async_preset_step; menu_options keys in async_step_init route HA's flow
# dispatcher to these names.
def _make_preset_step(preset_id: str):
    async def _step(
        self: MagicClimateOptionsFlow,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        return await self._async_preset_step(preset_id, user_input)
    _step.__name__ = f"async_step_preset_{preset_id}"
    return _step


for _pid in STANDARD_PRESETS:
    setattr(MagicClimateOptionsFlow, f"async_step_preset_{_pid}", _make_preset_step(_pid))
