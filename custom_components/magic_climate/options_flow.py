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

# Presets pick their mode and fan from whatever the source entity reports, so
# the dropdowns only ever offer settings the hardware actually has. This list
# is the fallback for when the source is unavailable at options-flow time —
# HVACMode is a fixed enum, so a static list is still correct there. Fan modes
# are device-defined and have no equivalent fallback.
_FALLBACK_HVAC_MODES = ["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"]

_HVAC_LABELS = {
    "off": "Off",
    "heat": "Heat",
    "cool": "Cool",
    "heat_cool": "Heat/Cool",
    "auto": "Auto",
    "dry": "Dry",
    "fan_only": "Fan only",
}

# Empty string means "the preset does not touch this setting".
_NO_OVERRIDE = ""
_NO_OVERRIDE_LABEL = "Leave unchanged"


class MagicClimateOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        # Fixed at entry creation. The entity's state subscription, unique id,
        # and every stored mode/fan value are tied to this source, so changing
        # it means deleting the entry and adding a new one.
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

    def _current_options(self) -> dict[str, Any]:
        """Snapshot of options as they would be persisted right now."""
        enabled = [pid for pid in STANDARD_PRESETS if pid in self._enabled]
        return {
            CONF_ENABLED_PRESETS: enabled,
            CONF_PRESETS: {pid: self._presets[pid] for pid in enabled},
        }

    def _persist(self) -> None:
        """Write current in-memory state to the config entry immediately.

        Each sub-step calls this on submit so changes survive the user
        closing the dialog without clicking "Save and exit". The update
        listener refreshes the climate entity so newly enabled presets show
        up in the picker right away.
        """
        self.hass.config_entries.async_update_entry(
            self._entry, options=self._current_options()
        )

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        # Options have already been persisted incrementally by each sub-step;
        # this just closes the flow with the current state.
        return self.async_create_entry(title="", data=self._current_options())

    # ----------------------------------------------------------- basic step

    async def async_step_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._enabled = [pid for pid in STANDARD_PRESETS if user_input.get(pid)]
            self._persist()
            return await self.async_step_init()

        schema = vol.Schema({
            vol.Required(pid, default=pid in self._enabled): selector.BooleanSelector()
            for pid in STANDARD_PRESETS
        })
        return self.async_show_form(
            step_id="basic",
            data_schema=schema,
            description_placeholders={"source": self._source_entity_id},
        )

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
                self._persist()
                return await self.async_step_init()

        schema = vol.Schema({
            vol.Required(
                PRESET_LOW, default=self._c_to_display(existing[PRESET_LOW]),
            ): self._temp_selector(),
            vol.Required(
                PRESET_HIGH, default=self._c_to_display(existing[PRESET_HIGH]),
            ): self._temp_selector(),
            vol.Optional(
                PRESET_MODE, default=existing.get(PRESET_MODE, _NO_OVERRIDE),
            ): self._override_selector(
                self._source_options("hvac_modes") or _FALLBACK_HVAC_MODES,
                existing.get(PRESET_MODE),
                _HVAC_LABELS,
            ),
            vol.Optional(
                PRESET_FAN, default=existing.get(PRESET_FAN, _NO_OVERRIDE),
            ): self._fan_selector(existing.get(PRESET_FAN)),
        })
        return self.async_show_form(
            step_id=f"preset_{preset_id}",
            data_schema=schema,
            errors=errors,
            description_placeholders={"preset": preset_id.title()},
        )

    # ------------------------------------------------------- source options

    def _source_options(self, attribute: str) -> list[str]:
        """Values the source climate entity currently reports for `attribute`.

        Empty when the source is missing or unavailable — callers decide
        whether a fallback exists.
        """
        state = self.hass.states.get(self._source_entity_id)
        if state is None:
            return []
        return [str(value) for value in state.attributes.get(attribute) or []]

    def _override_selector(
        self,
        options: list[str],
        current: str | None,
        labels: dict[str, str] | None = None,
    ) -> selector.SelectSelector:
        """A dropdown of `options` plus a leading "leave unchanged" entry.

        A stored value the source no longer offers is kept in the list. The
        source cannot change, but what it reports can — a firmware or ESPHome
        config change that drops a fan speed should not silently rewrite a
        preset the user configured against it.
        """
        if current and current not in options:
            options = [*options, current]
        labels = labels or {}
        choices = [
            selector.SelectOptionDict(value=_NO_OVERRIDE, label=_NO_OVERRIDE_LABEL),
            *(
                selector.SelectOptionDict(
                    value=option,
                    label=labels.get(option, option.replace("_", " ").capitalize()),
                )
                for option in options
            ),
        ]
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=choices,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

    def _fan_selector(self, current: str | None) -> selector.Selector:
        """Dropdown of the source's fan modes, or free text if it reports none.

        Unlike HVAC modes there is no universal fan vocabulary to fall back
        on, so a source that is unavailable (or has no fan control) leaves
        the field as text rather than an empty dropdown.
        """
        fan_modes = self._source_options("fan_modes")
        if not fan_modes:
            return selector.TextSelector()
        return self._override_selector(fan_modes, current)

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
