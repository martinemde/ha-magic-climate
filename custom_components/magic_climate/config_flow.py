"""Config flow for Magic Climate."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BOOST,
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SLEEP,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
    PRESET_DEFAULTS,
    default_boost,
    default_peak,
    default_sleep,
)


def initial_options() -> dict[str, Any]:
    """Default options for a freshly created entry.

    Nothing optional is switched on and nothing is scheduled, so a new entry
    is an ordinary thermostat with a Home band and an Away band until someone
    opens the options and asks for more.
    """
    return {
        CONF_ENABLED_PRESETS: [],
        CONF_PRESETS: {pid: dict(defaults) for pid, defaults in PRESET_DEFAULTS.items()},
        CONF_PEAK: default_peak(),
        CONF_BOOST: default_boost(),
        CONF_SLEEP: default_sleep(),
    }


class MagicClimateConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup: pick a source climate entity and a display name."""

    VERSION = 4

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            source_id = user_input[CONF_SOURCE_ENTITY_ID]
            await self.async_set_unique_id(f"magic_climate::{source_id}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input.get(CONF_NAME) or source_id,
                data={
                    CONF_SOURCE_ENTITY_ID: source_id,
                    CONF_NAME: user_input.get(CONF_NAME),
                },
                options=initial_options(),
            )

        schema = vol.Schema({
            vol.Required(CONF_SOURCE_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate")
            ),
            vol.Optional(CONF_NAME): selector.TextSelector(),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        from .options_flow import MagicClimateOptionsFlow
        return MagicClimateOptionsFlow(config_entry)
