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

    v1 -> v2 reshaped a free-form list of presets into a standard-preset
    dict. v2 -> v3 added the peak-window block. v3 -> v4 is the bigger one:
    Home stops owning a band and becomes the automatic preset, so its band
    moves to Comfort, and the schedule gains the Boost preload and the Sleep
    window. Nothing new is switched on, so an upgraded entry keeps behaving
    exactly as it did.
    """
    from .const import (
        CONF_BOOST,
        CONF_ENABLED_PRESETS,
        CONF_PEAK,
        CONF_PRESETS,
        CONF_SLEEP,
        CONFIGURABLE_PRESETS,
        OPTIONAL_PRESETS,
        PRESET_COMFORT,
        PRESET_DEFAULTS,
        PRESET_HIGH,
        PRESET_HOME,
        PRESET_LOW,
        default_boost,
        default_peak,
        default_sleep,
    )

    if entry.version >= 4:
        return True

    options = dict(entry.options or {})
    if entry.version == 1:
        options = _reshape_v1_presets(options)

    old_presets = dict(options.get(CONF_PRESETS, {}) or {})
    old_enabled = list(options.get(CONF_ENABLED_PRESETS, []) or [])

    # Home's band is the user's comfort setting — that is precisely what the
    # split names it. A pre-existing Comfort band loses: before v4 Comfort
    # was an ordinary standalone preset nobody in the live install enabled,
    # and letting it win would silently change what Home applies.
    if PRESET_HOME in old_presets:
        old_presets[PRESET_COMFORT] = old_presets[PRESET_HOME]

    presets = {}
    for pid in CONFIGURABLE_PRESETS:
        raw = dict(old_presets.get(pid) or PRESET_DEFAULTS[pid])
        # Presets no longer carry an HVAC mode; fan survives.
        raw.pop("mode", None)
        presets[pid] = {
            PRESET_LOW: raw[PRESET_LOW],
            PRESET_HIGH: raw[PRESET_HIGH],
            **({"fan": raw["fan"]} if raw.get("fan") else {}),
        }

    new_options = {
        # Home, Comfort and Away are unconditional now, so only the three
        # checkbox presets are listed here.
        CONF_ENABLED_PRESETS: [pid for pid in OPTIONAL_PRESETS if pid in old_enabled],
        CONF_PRESETS: presets,
        CONF_PEAK: options.get(CONF_PEAK) or default_peak(),
        CONF_BOOST: options.get(CONF_BOOST) or default_boost(),
        # No times: an existing Sleep preset was always selected by hand, and
        # an upgrade must not start putting the house to bed on a schedule.
        CONF_SLEEP: options.get(CONF_SLEEP) or default_sleep(),
    }
    hass.config_entries.async_update_entry(entry, options=new_options, version=4)
    return True


def _reshape_v1_presets(options: dict) -> dict:
    """Fold a v1 free-form preset list into the standard-preset dict shape.

    Returns a new options dict; the caller writes the entry once, at the
    current version, rather than migrating it a version at a time.
    """
    from .const import (
        CONF_ENABLED_PRESETS,
        CONF_PRESETS,
        PRESET_HIGH,
        PRESET_LOW,
        PRESET_ORDER,
    )

    old_presets = options.get(CONF_PRESETS, []) or []
    presets: dict[str, dict] = {}
    enabled: list[str] = []
    for raw in old_presets:
        name = str(raw.get("name", "")).strip().lower()
        if name not in PRESET_ORDER:
            continue
        mapped: dict = {PRESET_LOW: float(raw["low"]), PRESET_HIGH: float(raw["high"])}
        if raw.get("fan"):
            mapped["fan"] = raw["fan"]
        presets[name] = mapped
        if name not in enabled:
            enabled.append(name)

    return {
        **options,
        CONF_PRESETS: presets,
        CONF_ENABLED_PRESETS: enabled,
    }
