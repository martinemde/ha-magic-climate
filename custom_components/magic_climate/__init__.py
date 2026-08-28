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
    # Options changes are handled in place by the climate entity itself —
    # see MagicClimate._handle_entry_update. Nothing an options flow can
    # change requires a reload; the source entity is fixed at creation.
    return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, _platforms())


async def async_migrate_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Migrate v1 (free-form list of presets) to v2 (standard-preset dict)."""
    from .const import (
        CONF_ENABLED_PRESETS,
        CONF_PRESETS,
        PRESET_DEFAULTS,
        PRESET_HIGH,
        PRESET_LOW,
        STANDARD_PRESETS,
    )

    if entry.version >= 2:
        return True

    old_presets = (entry.options or {}).get(CONF_PRESETS, []) or []
    new_presets: dict[str, dict] = {
        pid: dict(defaults) for pid, defaults in PRESET_DEFAULTS.items()
    }
    enabled: list[str] = []
    # Carry over any v1 preset whose lowercased name matches a standard one.
    for raw in old_presets:
        name = str(raw.get("name", "")).strip().lower()
        if name in STANDARD_PRESETS:
            mapped: dict = {
                PRESET_LOW: float(raw["low"]),
                PRESET_HIGH: float(raw["high"]),
            }
            if raw.get("mode"):
                mapped["mode"] = raw["mode"]
            if raw.get("fan"):
                mapped["fan"] = raw["fan"]
            new_presets[name] = mapped
            if name not in enabled:
                enabled.append(name)
    if not enabled:
        from .const import DEFAULT_ENABLED_PRESETS
        enabled = list(DEFAULT_ENABLED_PRESETS)

    new_options = {
        CONF_ENABLED_PRESETS: enabled,
        CONF_PRESETS: new_presets,
    }
    hass.config_entries.async_update_entry(entry, options=new_options, version=2)
    return True


