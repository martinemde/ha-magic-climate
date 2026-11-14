"""Downloadable state snapshots and a short, in-memory decision history."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    BOOST_MINUTES,
    BOOST_PRELOAD,
    CONF_BOOST,
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SLEEP,
    CONF_SOURCE_ENTITY_ID,
    CONFIGURABLE_PRESETS,
    PEAK_ENABLED,
    PRESET_FAN,
    PRESET_HIGH,
    PRESET_LOW,
    WINDOW_END,
    WINDOW_START,
)

_LOGGER = logging.getLogger(__name__)
HISTORY_LIMIT = 20

# Do not export whole State objects: names, entity ids and integration-specific
# attributes can identify the home or contain unrelated private information.
_CLIMATE_ATTRIBUTES = (
    "temperature_unit",
    "current_temperature",
    "temperature",
    "target_temp_low",
    "target_temp_high",
    "min_temp",
    "max_temp",
    "target_temp_step",
    "hvac_action",
    "hvac_modes",
    "fan_mode",
    "fan_modes",
    "preset_mode",
    "preset_modes",
    "supported_features",
    "effective_preset",
    "peak_active",
    "peak_start",
    "peak_end",
    "next_peak_start",
    "next_peak_end",
)


class DiagnosticsHistory:
    """Recent decisions for one loaded entry; never written to storage."""

    def __init__(self) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=HISTORY_LIMIT)

    def record(self, event: str, **details: Any) -> None:
        """Record structured details, not raw exceptions or source attributes."""
        self._events.append(
            {
                "timestamp": dt_util.utcnow().isoformat(),
                "event": event,
                "details": deepcopy(details),
            }
        )
        _LOGGER.debug("%s: %s", event, details)

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a detached snapshot so downloads cannot mutate the history."""
        return deepcopy(list(self._events))


def _state_snapshot(state: State | None, temperature_unit: str) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "state": state.state,
        "temperature_unit": temperature_unit,
        "attributes": {
            key: deepcopy(state.attributes[key])
            for key in _CLIMATE_ATTRIBUTES
            if key in state.attributes
        },
    }


def _options_snapshot(options: dict[str, Any]) -> dict[str, Any]:
    """Export only known settings; bands are stored in Celsius."""
    stored = options.get(CONF_PRESETS) or {}
    return {
        "temperature_unit": UnitOfTemperature.CELSIUS,
        CONF_ENABLED_PRESETS: list(options.get(CONF_ENABLED_PRESETS) or []),
        CONF_PRESETS: {
            preset: {
                key: deepcopy(raw[key])
                for key in (PRESET_LOW, PRESET_HIGH, PRESET_FAN)
                if key in raw
            }
            for preset in CONFIGURABLE_PRESETS
            if (raw := stored.get(preset)) is not None
        },
        **{
            section: {
                key: deepcopy(raw[key]) for key in keys if key in raw
            }
            for section, keys in (
                (CONF_PEAK, (PEAK_ENABLED, WINDOW_START, WINDOW_END)),
                (CONF_BOOST, (BOOST_PRELOAD, BOOST_MINUTES)),
                (CONF_SLEEP, (WINDOW_START, WINDOW_END)),
            )
            if (raw := options.get(section)) is not None
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Describe configuration and decisions without names or entity ids."""
    registry = er.async_get(hass)
    wrapper = next(
        (
            hass.states.get(entity.entity_id)
            for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
            if entity.domain == "climate"
        ),
        None,
    )
    return {
        "options": _options_snapshot(dict(entry.options)),
        "source": _state_snapshot(
            hass.states.get(entry.data[CONF_SOURCE_ENTITY_ID]),
            hass.config.units.temperature_unit,
        ),
        "wrapper": _state_snapshot(wrapper, hass.config.units.temperature_unit),
        "history": entry.runtime_data.snapshot(),
    }
