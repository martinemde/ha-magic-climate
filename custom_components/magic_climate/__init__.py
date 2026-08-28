"""The Magic Climate integration."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _platforms() -> list:
    # Imported lazily so pure-logic tests can import submodules without
    # requiring the homeassistant package to be installed.
    from homeassistant.const import Platform

    return [Platform.CLIMATE]


def _async_track_source_rename(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    """Follow the wrapped entity if its entity id changes.

    The source is pinned by entity id at creation, so a rename would
    otherwise leave the wrapper subscribed to an id nothing publishes: it
    goes unavailable with nothing logged and no hint of the cause.

    This is deliberately a plain registry subscription rather than
    `async_handle_source_entity_changes`. Everything that helper adds over
    this is device relinking, and these wrappers are device-less on purpose
    — the source's device sits in a different area than the wrapper belongs
    in, so inheriting it would be wrong. HA core uses this same plain form
    for sources it does not device-link (see generic_thermostat's sensor).
    """
    from homeassistant.helpers.event import async_track_entity_registry_updated_event

    from .const import CONF_SOURCE_ENTITY_ID, DOMAIN

    source_entity_id: str = entry.data[CONF_SOURCE_ENTITY_ID]

    async def _source_registry_updated(event: "Event") -> None:
        data: dict[str, Any] = event.data
        if data["action"] == "remove":
            # Nothing to repoint at. The wrapper reports unavailable on its
            # own; say why, because otherwise this is silent.
            _LOGGER.warning(
                "Magic Climate source %s was removed; %s will stay unavailable "
                "until the entry is deleted and recreated against a new source",
                source_entity_id,
                entry.title,
            )
            return
        if data["action"] != "update" or "entity_id" not in data["changes"]:
            return

        new_entity_id: str = data["entity_id"]
        _LOGGER.debug(
            "Magic Climate source renamed %s -> %s", source_entity_id, new_entity_id
        )
        # The unique id embeds the source, so it has to move too — a stale
        # one would stop guarding against the renamed entity being wrapped
        # a second time.
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_SOURCE_ENTITY_ID: new_entity_id},
            unique_id=f"{DOMAIN}::{new_entity_id}",
        )
        # The entity captured the old id in its state subscription and every
        # service call, so rebuild it. RestoreEntity carries the held preset
        # across the reload.
        hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(
        async_track_entity_registry_updated_event(
            hass, source_entity_id, _source_registry_updated
        )
    )


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Set up Magic Climate from a config entry."""
    _async_track_source_rename(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, _platforms())
    # Options changes are handled in place by the climate entity itself —
    # see MagicClimate._handle_entry_update. Nothing an options flow can
    # change requires a reload; only a source rename does, and that is
    # handled above.
    return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, _platforms())


async def async_migrate_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Bring an entry forward to the current options schema.

    v1 -> v2 reshapes a free-form list of presets into a standard-preset
    dict. v2 -> v3 adds the peak-window block, switched off, so an existing
    entry keeps behaving exactly as it did until peak is configured.
    """
    from .const import (
        CONF_ENABLED_PRESETS,
        CONF_PEAK,
        CONF_PRESETS,
        PRESET_DEFAULTS,
        PRESET_HIGH,
        PRESET_LOW,
        STANDARD_PRESETS,
        default_peak,
    )

    if entry.version >= 3:
        return True

    if entry.version == 2:
        options = dict(entry.options or {})
        options.setdefault(CONF_PEAK, default_peak())
        hass.config_entries.async_update_entry(entry, options=options, version=3)
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
        CONF_PEAK: default_peak(),
    }
    hass.config_entries.async_update_entry(entry, options=new_options, version=3)
    return True


