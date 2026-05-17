"""The Magic Climate integration."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def _platforms() -> list:
    # Imported lazily so pure-logic tests can import submodules without
    # requiring the homeassistant package to be installed.
    from homeassistant.const import Platform

    return [Platform.CLIMATE]


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Set up Magic Climate from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, _platforms())
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, _platforms())


async def _async_update_listener(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    """Reload the entry so the climate entity picks up new options."""
    await hass.config_entries.async_reload(entry)
