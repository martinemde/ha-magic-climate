"""Magic Climate wrapper entity."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_SOURCE_ENTITY_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Magic Climate platform from a config entry."""
    source_id: str = entry.data[CONF_SOURCE_ENTITY_ID]
    name: str | None = entry.data.get(CONF_NAME)
    async_add_entities(
        [MagicClimate(hass, entry, source_id, name)],
        update_before_add=True,
    )


class MagicClimate(ClimateEntity):
    """Wraps a source climate.* entity and adds UI-configured presets."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        source_entity_id: str,
        name: str | None,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._entry = entry
        self._source_entity_id = source_entity_id
        self._attr_name = name or source_entity_id
        self._attr_unique_id = f"{DOMAIN}::{source_entity_id}"
        self._source_state = None

    async def async_added_to_hass(self) -> None:
        self._source_state = self.hass.states.get(self._source_entity_id)
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], self._handle_source_change
            )
        )

    @callback
    def _handle_source_change(self, event: Event) -> None:
        self._source_state = event.data.get("new_state")
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        s = self._source_state
        return s is not None and s.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    @property
    def temperature_unit(self) -> str:
        # Always report °C — _normalize_temp normalizes internally in Task 16.
        return UnitOfTemperature.CELSIUS

    @property
    def _source_unit(self) -> str:
        return self.hass.config.units.temperature_unit

    def _normalize_temp(self, val: float | None) -> float | None:
        if val is None:
            return None
        if self._source_unit == UnitOfTemperature.FAHRENHEIT:
            return (val - 32.0) * 5.0 / 9.0
        return val

    def _denormalize_temp(self, val: float | None) -> float | None:
        if val is None:
            return None
        if self._source_unit == UnitOfTemperature.FAHRENHEIT:
            return val * 9.0 / 5.0 + 32.0
        return val

    @property
    def current_temperature(self) -> float | None:
        if not self._source_state:
            return None
        return self._normalize_temp(self._source_state.attributes.get("current_temperature"))

    @property
    def target_temperature(self) -> float | None:
        if not self._source_state:
            return None
        val = self._source_state.attributes.get("temperature")
        if val is not None:
            return self._normalize_temp(val)
        low = self._source_state.attributes.get("target_temp_low")
        high = self._source_state.attributes.get("target_temp_high")
        if low is not None and high is not None:
            return self._normalize_temp((low + high) / 2.0)
        return self._normalize_temp(low if low is not None else high)

    @property
    def target_temperature_low(self) -> float | None:
        if self._source_state:
            return self._normalize_temp(self._source_state.attributes.get("target_temp_low"))
        return None

    @property
    def target_temperature_high(self) -> float | None:
        if self._source_state:
            return self._normalize_temp(self._source_state.attributes.get("target_temp_high"))
        return None

    @property
    def min_temp(self) -> float:
        if self._source_state and "min_temp" in self._source_state.attributes:
            return self._normalize_temp(self._source_state.attributes["min_temp"])
        return 7.0

    @property
    def max_temp(self) -> float:
        if self._source_state and "max_temp" in self._source_state.attributes:
            return self._normalize_temp(self._source_state.attributes["max_temp"])
        return 35.0

    @property
    def hvac_mode(self) -> HVACMode | None:
        if not self._source_state:
            return None
        try:
            return HVACMode(self._source_state.state)
        except ValueError:
            return None

    @property
    def hvac_modes(self) -> list[HVACMode]:
        if not self._source_state:
            return []
        out: list[HVACMode] = []
        for raw in self._source_state.attributes.get("hvac_modes", []) or []:
            try:
                out.append(HVACMode(raw))
            except ValueError:
                _LOGGER.debug("Unknown hvac_mode from source: %r", raw)
        return out

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self._source_state:
            return None
        raw = self._source_state.attributes.get("hvac_action") \
            or self._source_state.attributes.get("action")
        if raw is None:
            return None
        try:
            return HVACAction(raw)
        except ValueError:
            return None

    @property
    def fan_mode(self) -> str | None:
        if self._source_state:
            return self._source_state.attributes.get("fan_mode")
        return None

    @property
    def fan_modes(self) -> list[str] | None:
        if self._source_state:
            return self._source_state.attributes.get("fan_modes")
        return None

    @property
    def swing_mode(self) -> str | None:
        if self._source_state:
            return self._source_state.attributes.get("swing_mode")
        return None

    @property
    def swing_modes(self) -> list[str] | None:
        if self._source_state:
            return self._source_state.attributes.get("swing_modes")
        return None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        if not self._source_state:
            return ClimateEntityFeature(0)

        source_features = self._source_state.attributes.get("supported_features", 0)

        # Drop both temperature flags and decide fresh.
        features = source_features & ~ClimateEntityFeature.TARGET_TEMPERATURE
        features = features & ~ClimateEntityFeature.TARGET_TEMPERATURE_RANGE

        source_supports_dual = bool(
            source_features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        ) and HVACMode.HEAT_COOL in self.hvac_modes

        if source_supports_dual and (
            self.hvac_mode == HVACMode.HEAT_COOL
            or (self.hvac_mode == HVACMode.OFF and HVACMode.HEAT_COOL in self.hvac_modes)
        ):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE

        return ClimateEntityFeature(features)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.hass.services.async_call(
            "climate", "set_hvac_mode",
            {"entity_id": self._source_entity_id, "hvac_mode": hvac_mode},
            blocking=True,
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.hass.services.async_call(
            "climate", "set_fan_mode",
            {"entity_id": self._source_entity_id, "fan_mode": fan_mode},
            blocking=True,
        )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.hass.services.async_call(
            "climate", "set_swing_mode",
            {"entity_id": self._source_entity_id, "swing_mode": swing_mode},
            blocking=True,
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        data: dict[str, Any] = {"entity_id": self._source_entity_id}
        for key in ("temperature", "target_temp_low", "target_temp_high"):
            if key in kwargs:
                data[key] = self._denormalize_temp(kwargs[key])
        if "hvac_mode" in kwargs:
            data["hvac_mode"] = kwargs["hvac_mode"]
        await self.hass.services.async_call(
            "climate", "set_temperature", data, blocking=True,
        )
