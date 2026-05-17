"""Config flow for Magic Climate."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_SOURCE_ENTITY_ID, DOMAIN


class MagicClimateConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup: pick a source climate entity and a display name."""

    VERSION = 1

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
                options={"presets": []},
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
