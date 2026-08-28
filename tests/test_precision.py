"""Precision and step passthrough on a Fahrenheit install.

HA derives a climate entity's default precision from the *system* unit, so
on a °F install every entity that doesn't say otherwise rounds to whole
degrees. That default suits an entity reporting its own hardware readings.
This wrapper reports values the source already rounded to the source's own
precision, so taking the default meant rounding a second time, more coarsely
— quietly discarding whatever resolution the source published.

The source modelled here is a faithful ESPHome climate entity: Celsius, with
an explicit tenths precision (the esphome integration derives that from
`visual: current_temperature_step`) and a 0.5 °C target step.
"""
from __future__ import annotations

import pytest
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    PRECISION_TENTHS,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    setup_test_component_platform,
)

from custom_components.magic_climate.const import CONF_SOURCE_ENTITY_ID, DOMAIN

SOURCE_ENTITY_ID = "climate.test_source"
MAGIC_ENTITY_ID = "climate.bedroom_magic"

# 21.7778 °C is exactly 71.2 °F — a reading a whole-degree rounding destroys.
FRACTIONAL_C = 21.7778
# 22.2222 °C is exactly 72.0 °F — a setpoint that is already whole.
WHOLE_SETPOINT_C = 22.2222


class EsphomeLikeSource(ClimateEntity):
    """Celsius source that declares tenths precision, as ESPHome's does."""

    _attr_name = "Test Source"
    _attr_unique_id = "test_source"
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(self) -> None:
        self._attr_hvac_mode = HVACMode.COOL
        self._attr_current_temperature = FRACTIONAL_C
        self._attr_target_temperature = WHOLE_SETPOINT_C
        self._attr_min_temp = 14.0
        self._attr_max_temp = 28.0


async def _setup(hass: HomeAssistant) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    setup_test_component_platform(hass, "climate", [EsphomeLikeSource()], built_in=True)
    assert await async_setup_component(hass, "climate", {"climate": {"platform": "test"}})
    await hass.async_block_till_done()

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY_ID, CONF_NAME: "Bedroom Magic"},
        options={},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _attrs(hass: HomeAssistant, entity_id: str) -> dict:
    return hass.states.get(entity_id).attributes


async def test_wrapper_does_not_flatten_the_source_reading(
    hass: HomeAssistant,
) -> None:
    """The source publishes 71.2 °F; the wrapper must not round it to 71."""
    await _setup(hass)
    source = _attrs(hass, SOURCE_ENTITY_ID)["current_temperature"]
    wrapper = _attrs(hass, MAGIC_ENTITY_ID)["current_temperature"]
    assert source == pytest.approx(71.2, abs=0.05)
    assert wrapper == pytest.approx(source, abs=0.05)


async def test_setpoint_gains_no_false_precision(hass: HomeAssistant) -> None:
    """Tenths precision must not invent resolution: 72 °F stays 72 °F."""
    await _setup(hass)
    assert _attrs(hass, MAGIC_ENTITY_ID)[ATTR_TEMPERATURE] == pytest.approx(
        72.0, abs=0.05
    )


async def test_target_temperature_step_mirrors_the_source(
    hass: HomeAssistant,
) -> None:
    """The wrapper used to drop the source's step and fall back to HA's."""
    await _setup(hass)
    assert _attrs(hass, MAGIC_ENTITY_ID)["target_temp_step"] == pytest.approx(
        _attrs(hass, SOURCE_ENTITY_ID)["target_temp_step"]
    )
    assert _attrs(hass, MAGIC_ENTITY_ID)["target_temp_step"] == 0.5


async def test_min_max_still_track_the_source(hass: HomeAssistant) -> None:
    """Finer precision must not shift the bounds (14/28 °C = 57.2/82.4 °F)."""
    await _setup(hass)
    attrs = _attrs(hass, MAGIC_ENTITY_ID)
    assert attrs["min_temp"] == pytest.approx(57.2, abs=0.05)
    assert attrs["max_temp"] == pytest.approx(82.4, abs=0.05)


async def test_unavailable_source_reports_no_step(hass: HomeAssistant) -> None:
    """target_temperature_step must tolerate a missing source state."""
    await _setup(hass)
    hass.states.async_remove(SOURCE_ENTITY_ID)
    await hass.async_block_till_done()
    assert "target_temp_step" not in _attrs(hass, MAGIC_ENTITY_ID)
