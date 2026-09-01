"""End-to-end climate tests against a real Home Assistant instance.

These exercise the full service path the frontend uses: a user calling
``climate.set_temperature`` (or ``set_preset_mode``) on the Magic Climate
entity, which forwards to a wrapped source thermostat. The source here is a
faithful fake: it reports Fahrenheit (matching a US install) and only supports
a two-point target band (matching a Mitsubishi CN105 heat pump with dual
setpoint enabled), and it actually stores whatever temperatures it is told to.

The reported bug: on a °F install, changing the temperature on the Magic
Climate entity did not stick — the displayed value reverted to the source's
current value because the forwarded ``temperature`` was rejected by a
range-only source. These tests assert the wrapper now reshapes writes to what
the source accepts, and that the source thermostat actually receives them.

Units: the system is °F, so Home Assistant interprets every ``set_temperature``
input in °F and converts the wrapper's reported attributes back to °F. The
wrapper normalizes to °C internally, but the whole test surface — inputs,
source values, and reported attributes — is in °F. Source values are kept on
whole-°F steps (the source's target_temperature_step is 1.0) so preset values
echo back exactly and don't trip drift detection.
"""
from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_NAME,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotSupported
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    setup_test_component_platform,
)

from custom_components.magic_climate.const import (
    CONF_ENABLED_PRESETS,
    CONF_PRESETS,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
)

SOURCE_ENTITY_ID = "climate.test_source"
MAGIC_ENTITY_ID = "climate.bedroom_magic"


class FakeThermostat(ClimateEntity):
    """A CN105-like source: reports °F, only supports a two-point target band,
    and remembers what it is set to. HA publishes only target_temp_low/high
    for this entity (not a single ``temperature``)."""

    _attr_name = "Test Source"
    _attr_unique_id = "test_source"
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = 1.0
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.HEAT_COOL,
        HVACMode.AUTO,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    ]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE_RANGE

    def __init__(self) -> None:
        self._attr_hvac_mode = HVACMode.COOL
        self._attr_current_temperature = 74.0
        self._attr_target_temperature = 82.0
        self._attr_target_temperature_low = 70.0
        self._attr_target_temperature_high = 82.0
        self._attr_min_temp = 61.0
        self._attr_max_temp = 90.0

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if ATTR_TEMPERATURE in kwargs:
            self._attr_target_temperature = kwargs[ATTR_TEMPERATURE]
        if "target_temp_low" in kwargs:
            self._attr_target_temperature_low = kwargs["target_temp_low"]
        if "target_temp_high" in kwargs:
            self._attr_target_temperature_high = kwargs["target_temp_high"]
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()


class SingleOnlyThermostat(FakeThermostat):
    """A plain thermostat that only supports a single setpoint (°F)."""

    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE


async def _setup(
    hass: HomeAssistant,
    *,
    source: FakeThermostat | None = None,
    enabled: list[str] | None = None,
    presets: dict[str, dict[str, float]] | None = None,
) -> FakeThermostat:
    """Stand up a °F source thermostat and a Magic Climate wrapping it.

    ``presets`` uses main's standard-preset storage shape:
    ``{<preset_id>: {"low": .., "high": ..}}`` in °C, gated by ``enabled``.
    """
    hass.config.units = US_CUSTOMARY_SYSTEM

    source = source or FakeThermostat()
    setup_test_component_platform(hass, "climate", [source], built_in=True)
    assert await async_setup_component(
        hass, "climate", {"climate": {"platform": "test"}}
    )
    await hass.async_block_till_done()

    options: dict[str, Any] = {}
    if enabled is not None:
        options[CONF_ENABLED_PRESETS] = enabled
    if presets is not None:
        options[CONF_PRESETS] = presets

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,  # matches config_flow.VERSION; skips migration entirely
        data={CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY_ID, CONF_NAME: "Bedroom Magic"},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return source


async def _set_mode(hass: HomeAssistant, mode: str) -> None:
    await hass.services.async_call(
        "climate", "set_hvac_mode",
        {ATTR_ENTITY_ID: MAGIC_ENTITY_ID, "hvac_mode": mode}, blocking=True,
    )
    await hass.async_block_till_done()


async def _set_temp(hass: HomeAssistant, **temps: float) -> None:
    await hass.services.async_call(
        "climate", "set_temperature",
        {ATTR_ENTITY_ID: MAGIC_ENTITY_ID, **temps}, blocking=True,
    )
    await hass.async_block_till_done()


def _magic(hass: HomeAssistant):
    return hass.states.get(MAGIC_ENTITY_ID)


# --- Read projection: which source edge the wrapper shows per mode ----------


async def test_cool_mode_projects_high_edge(hass: HomeAssistant) -> None:
    """In cool mode the wrapper's single setpoint reflects the source's high
    edge (82 °F)."""
    await _setup(hass)  # FakeThermostat: low=70, high=82 °F, cool
    assert _magic(hass).attributes[ATTR_TEMPERATURE] == pytest.approx(82.0, abs=0.5)


async def test_heat_mode_projects_low_edge(hass: HomeAssistant) -> None:
    """In heat mode the wrapper shows the source's low edge (70 °F)."""
    await _setup(hass)
    await _set_mode(hass, "heat")
    assert _magic(hass).attributes[ATTR_TEMPERATURE] == pytest.approx(70.0, abs=0.5)


# --- Feature projection: single vs range per mode ---------------------------


async def test_features_single_in_cool_mode(hass: HomeAssistant) -> None:
    """Cool mode advertises a single setpoint even though the source is
    range-only."""
    await _setup(hass)
    feats = _magic(hass).attributes["supported_features"]
    assert feats & ClimateEntityFeature.TARGET_TEMPERATURE
    assert not (feats & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE)


async def test_features_range_in_heat_cool_mode(hass: HomeAssistant) -> None:
    await _setup(hass)
    await _set_mode(hass, "heat_cool")
    feats = _magic(hass).attributes["supported_features"]
    assert feats & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    assert not (feats & ClimateEntityFeature.TARGET_TEMPERATURE)


async def test_features_range_in_off_mode(hass: HomeAssistant) -> None:
    """Off mode advertises the range, because heat_cool is the resting mode a
    dual-setpoint source returns to."""
    await _setup(hass)
    await _set_mode(hass, "off")
    feats = _magic(hass).attributes["supported_features"]
    assert feats & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    assert not (feats & ClimateEntityFeature.TARGET_TEMPERATURE)


# --- Manual writes: reshape single setpoint to the source's range -----------


async def test_cool_mode_set_moves_high(hass: HomeAssistant) -> None:
    """The original bug: a single ``temperature`` write in cool mode must land
    on the source's high edge."""
    source = await _setup(hass)  # cool, low=70, high=82 °F
    await _set_temp(hass, temperature=75.0)
    assert source.target_temperature_high == pytest.approx(75.0, abs=0.5)
    assert _magic(hass).attributes[ATTR_TEMPERATURE] == pytest.approx(75.0, abs=0.5)


async def test_cool_mode_slides_low_out_of_the_way(hass: HomeAssistant) -> None:
    """A heating bound parked under the cooling one makes the unit clamp.

    The source starts at 70/82. Cooling to 75 leaves only 5 °F of band, under
    the 4 °C (7.2 °F) minimum, so the low edge has to come down with it.
    """
    source = await _setup(hass)
    await _set_temp(hass, temperature=75.0)
    assert source.target_temperature_high == pytest.approx(75.0, abs=0.5)
    assert source.target_temperature_low == pytest.approx(75.0 - 7.2, abs=0.5)


async def test_cool_mode_leaves_a_wide_band_alone(hass: HomeAssistant) -> None:
    """Only ever slide *toward* the minimum — a wider band is the user's."""
    source = await _setup(hass)
    await _set_temp(hass, temperature=80.0)  # 80 - 70 = 10 °F, already wide
    assert source.target_temperature_high == pytest.approx(80.0, abs=0.5)
    assert source.target_temperature_low == pytest.approx(70.0, abs=0.5)


async def test_cool_mode_accepts_a_narrow_band_at_the_source_floor(
    hass: HomeAssistant,
) -> None:
    """The slide stops at the source's min_temp, even if that costs the span.

    Cooling to 64 wants the low edge at 56.8, below the source's 61 °F floor.
    Pushing it there makes HA reject the whole call, so the cooling setpoint
    the user asked for would be lost too. Clamp and accept the narrow band.
    """
    source = await _setup(hass)
    await _set_temp(hass, temperature=64.0)
    assert source.target_temperature_high == pytest.approx(64.0, abs=0.5)
    assert source.target_temperature_low == pytest.approx(61.0, abs=0.5)


async def test_heat_mode_accepts_a_narrow_band_at_the_source_ceiling(
    hass: HomeAssistant,
) -> None:
    """Mirror: the high edge stops at the source's max_temp of 90 °F."""
    source = await _setup(hass)
    await _set_mode(hass, "heat")
    await _set_temp(hass, temperature=86.0)
    assert source.target_temperature_low == pytest.approx(86.0, abs=0.5)
    assert source.target_temperature_high == pytest.approx(90.0, abs=0.5)


async def test_heat_to_80_against_the_real_ceiling(hass: HomeAssistant) -> None:
    """The live case: the units cap at 28 °C (82.4 °F) per their ESPHome
    ``visual: max_temperature``. Heating to 80 wants the cooling edge at
    87.2, so there is nowhere for it to go — leave the two close together
    rather than dropping the write."""
    source = await _setup(hass)
    source._attr_max_temp = 82.4
    source.async_write_ha_state()
    await hass.async_block_till_done()
    await _set_mode(hass, "heat")
    await _set_temp(hass, temperature=80.0)
    assert source.target_temperature_low == pytest.approx(80.0, abs=0.5)
    assert source.target_temperature_high == pytest.approx(82.4, abs=0.5)


async def test_heat_mode_set_moves_low(hass: HomeAssistant) -> None:
    source = await _setup(hass)
    await _set_mode(hass, "heat")
    await _set_temp(hass, temperature=68.0)
    assert source.target_temperature_low == pytest.approx(68.0, abs=0.5)
    # 82 is already more than 7.2 °F above 68, so it stays put.
    assert source.target_temperature_high == pytest.approx(82.0, abs=0.5)


async def test_heat_mode_slides_high_out_of_the_way(hass: HomeAssistant) -> None:
    """Mirror of the cooling case: heating up to meet the cooling bound."""
    source = await _setup(hass)
    await _set_mode(hass, "heat")
    await _set_temp(hass, temperature=80.0)  # 82 - 80 = 2 °F, far too narrow
    assert source.target_temperature_low == pytest.approx(80.0, abs=0.5)
    assert source.target_temperature_high == pytest.approx(80.0 + 7.2, abs=0.5)


# --- fan_only has no setpoint ----------------------------------------------


async def test_fan_only_reports_no_setpoint(hass: HomeAssistant) -> None:
    """The band midpoint is fiction in fan_only, and it was being published."""
    await _setup(hass)
    await _set_mode(hass, "fan_only")
    assert _magic(hass).attributes.get(ATTR_TEMPERATURE) is None


async def test_fan_only_advertises_no_temperature_control(
    hass: HomeAssistant,
) -> None:
    await _setup(hass)
    await _set_mode(hass, "fan_only")
    feats = _magic(hass).attributes["supported_features"]
    assert not (feats & ClimateEntityFeature.TARGET_TEMPERATURE)
    assert not (feats & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE)


async def test_fan_only_write_does_not_touch_the_band(hass: HomeAssistant) -> None:
    """Nudging a fan-mode setpoint is what parked the bound in the first place.

    Dropping the feature flag makes Home Assistant refuse the call before it
    reaches the entity, so the band cannot be written by accident at all.
    """
    source = await _setup(hass)
    await _set_mode(hass, "fan_only")
    with pytest.raises(ServiceNotSupported):
        await _set_temp(hass, temperature=72.0)
    assert source.target_temperature_low == pytest.approx(70.0, abs=0.5)
    assert source.target_temperature_high == pytest.approx(82.0, abs=0.5)


async def test_auto_mode_centers_band_on_target(hass: HomeAssistant) -> None:
    """AUTO keeps a band (the source averages it); the wrapper centers the
    existing span on the new target rather than collapsing to a point. Span is
    82 - 70 = 12 °F, so a 72 °F target yields 66 .. 78."""
    source = await _setup(hass)
    await _set_mode(hass, "auto")
    await _set_temp(hass, temperature=72.0)
    assert source.target_temperature_low == pytest.approx(66.0, abs=0.5)
    assert source.target_temperature_high == pytest.approx(78.0, abs=0.5)


async def test_heat_cool_mode_sets_both_edges(hass: HomeAssistant) -> None:
    source = await _setup(hass)
    await _set_mode(hass, "heat_cool")
    await _set_temp(hass, target_temp_low=66.0, target_temp_high=78.0)
    assert source.target_temperature_low == pytest.approx(66.0, abs=0.5)
    assert source.target_temperature_high == pytest.approx(78.0, abs=0.5)


async def test_single_only_source_round_trips(hass: HomeAssistant) -> None:
    """A plain single-setpoint source receives ``temperature`` unchanged."""
    source = await _setup(hass, source=SingleOnlyThermostat())
    await _set_temp(hass, temperature=73.0)
    assert source.target_temperature == pytest.approx(73.0, abs=0.5)
    assert _magic(hass).attributes[ATTR_TEMPERATURE] == pytest.approx(73.0, abs=0.5)


# --- Presets ----------------------------------------------------------------

# The Comfort band stored in °C, chosen so both edges land on whole °F:
# low(heat)=20 -> 68 °F, high(cool)=25 -> 77 °F. Home applies it, since
# nothing here is scheduled to move Home off it.
COMFORT = {"comfort": {"low": 20.0, "high": 25.0}}


async def _apply_home(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "climate", "set_preset_mode",
        {ATTR_ENTITY_ID: MAGIC_ENTITY_ID, "preset_mode": "home"}, blocking=True,
    )
    await hass.async_block_till_done()


async def test_presets_exposed(hass: HomeAssistant) -> None:
    """With no schedule, Comfort is not offered separately — Home is it."""
    await _setup(hass, enabled=[], presets=COMFORT)
    state = _magic(hass)
    assert state.attributes["supported_features"] & ClimateEntityFeature.PRESET_MODE
    assert state.attributes["preset_modes"] == ["home", "away"]


async def test_preset_in_cool_mode_sends_full_band(hass: HomeAssistant) -> None:
    """Range-only source: a single-mode preset is sent as the whole band and
    the source picks the side it needs (both edges land on the preset band)."""
    source = await _setup(hass, enabled=[], presets=COMFORT)
    await _apply_home(hass)
    assert source.target_temperature_low == pytest.approx(68.0, abs=0.5)
    assert source.target_temperature_high == pytest.approx(77.0, abs=0.5)


async def test_preset_in_heat_cool_mode_sets_both(hass: HomeAssistant) -> None:
    source = await _setup(hass, enabled=[], presets=COMFORT)
    await _set_mode(hass, "heat_cool")
    await _apply_home(hass)
    assert source.target_temperature_low == pytest.approx(68.0, abs=0.5)
    assert source.target_temperature_high == pytest.approx(77.0, abs=0.5)


async def test_manual_change_clears_active_preset(
    hass: HomeAssistant, monkeypatch
) -> None:
    """A manual setpoint nudge after a preset apply drifts from the pushed
    preset values and clears the active preset.

    The guard window is collapsed to zero so drift is evaluated immediately;
    the preset's own apply echoes back exactly (whole-°F values), so only the
    later manual change registers as drift.
    """
    import custom_components.magic_climate.climate as climate_mod

    monkeypatch.setattr(climate_mod, "APPLY_GUARD_SECONDS", 0.0)

    await _setup(hass, enabled=[], presets=COMFORT)
    await _apply_home(hass)
    assert _magic(hass).attributes.get("preset_mode") == "home"

    await _set_temp(hass, temperature=73.0)  # cool: moves high to 73, low stays 68
    assert _magic(hass).attributes.get("preset_mode") is None
