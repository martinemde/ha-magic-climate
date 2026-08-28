"""Peak substitution against a real Home Assistant instance.

While the peak window is open, asking for Home pushes the Eco band — but the
wrapper keeps *reporting* "home". These tests pin both halves of that: the
setpoints the source actually receives, and the preset the wrapper advertises.

Setup mirrors tests/test_climate_temperature.py: a °F system and a range-only
CN105-like source. Preset bands are declared in °F and converted, so every
value the source echoes lands on a whole °F step and never trips drift
detection.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    setup_test_component_platform,
)

from custom_components.magic_climate.const import (
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
    PEAK_ENABLED,
    PEAK_END,
    PEAK_START,
    PRESET_HIGH,
    PRESET_LOW,
)

from .test_climate_temperature import (
    MAGIC_ENTITY_ID,
    SOURCE_ENTITY_ID,
    FakeThermostat,
)

OFF_PEAK = datetime(2026, 8, 28, 12, 0)
IN_PEAK = datetime(2026, 8, 28, 18, 0)
PEAK_START_AT = datetime(2026, 8, 28, 16, 0)
PEAK_END_AT = datetime(2026, 8, 28, 21, 0)


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def band(low_f: float, high_f: float) -> dict[str, float]:
    return {PRESET_LOW: f_to_c(low_f), PRESET_HIGH: f_to_c(high_f)}


# Distinct enough that a mix-up cannot pass by rounding.
HOME_BAND = band(68.0, 76.0)
ECO_BAND = band(62.0, 82.0)
SLEEP_BAND = band(64.0, 72.0)

DEFAULT_PRESETS = {"home": HOME_BAND, "eco": ECO_BAND, "sleep": SLEEP_BAND}
PEAK_ON = {PEAK_ENABLED: True, PEAK_START: "16:00:00", PEAK_END: "21:00:00"}
PEAK_OFF = {PEAK_ENABLED: False, PEAK_START: "16:00:00", PEAK_END: "21:00:00"}


async def _setup(
    hass: HomeAssistant,
    freezer,
    *,
    now: datetime = OFF_PEAK,
    enabled: list[str] | None = None,
    presets: dict[str, dict[str, float]] | None = None,
    peak: dict[str, Any] | None = None,
) -> FakeThermostat:
    hass.config.units = US_CUSTOMARY_SYSTEM
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(now)

    source = FakeThermostat()
    setup_test_component_platform(hass, "climate", [source], built_in=True)
    assert await async_setup_component(
        hass, "climate", {"climate": {"platform": "test"}}
    )
    await hass.async_block_till_done()

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        data={CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY_ID, CONF_NAME: "Bedroom Magic"},
        options={
            CONF_ENABLED_PRESETS: enabled
            if enabled is not None
            else ["home", "eco", "sleep"],
            CONF_PRESETS: presets if presets is not None else DEFAULT_PRESETS,
            CONF_PEAK: peak if peak is not None else PEAK_ON,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return source


async def _select(hass: HomeAssistant, preset: str) -> None:
    await hass.services.async_call(
        "climate", "set_preset_mode",
        {ATTR_ENTITY_ID: MAGIC_ENTITY_ID, "preset_mode": preset}, blocking=True,
    )
    await hass.async_block_till_done()


async def _cross_boundary(hass: HomeAssistant, freezer, moment: datetime) -> None:
    """Advance the clock past a peak boundary and let the minute tick fire."""
    freezer.move_to(moment)
    async_fire_time_changed(hass, moment)
    await hass.async_block_till_done()


def _pushed(source: FakeThermostat) -> tuple[float, float]:
    """The band the source is currently holding, in °F."""
    return (
        round(source._attr_target_temperature_low, 1),
        round(source._attr_target_temperature_high, 1),
    )


def _preset(hass: HomeAssistant) -> str | None:
    return hass.states.get(MAGIC_ENTITY_ID).attributes.get("preset_mode")


def _attrs(hass: HomeAssistant) -> dict[str, Any]:
    return hass.states.get(MAGIC_ENTITY_ID).attributes


# --- Substitution on demand ------------------------------------------------


async def test_home_off_peak_pushes_the_home_band(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=OFF_PEAK)
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)


async def test_home_during_peak_pushes_the_eco_band(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=IN_PEAK)
    await _select(hass, "home")
    assert _pushed(source) == (62.0, 82.0)


async def test_home_during_peak_still_reports_home(hass, freezer) -> None:
    """The whole point: no preset change for anything downstream to react to."""
    await _setup(hass, freezer, now=IN_PEAK)
    await _select(hass, "home")
    assert _preset(hass) == "home"


async def test_choosing_eco_during_peak_holds_eco(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=IN_PEAK)
    await _select(hass, "eco")
    assert _pushed(source) == (62.0, 82.0)
    assert _preset(hass) == "eco"


async def test_other_presets_are_untouched_during_peak(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=IN_PEAK)
    await _select(hass, "sleep")
    assert _pushed(source) == (64.0, 72.0)
    assert _preset(hass) == "sleep"


async def test_no_substitution_when_peak_is_disabled(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=IN_PEAK, peak=PEAK_OFF)
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)


async def test_no_substitution_when_eco_is_not_enabled(hass, freezer) -> None:
    """Peak has nothing to substitute if Eco is switched off."""
    source = await _setup(hass, freezer, now=IN_PEAK, enabled=["home", "sleep"])
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)


async def test_an_overnight_window_substitutes_after_midnight(hass, freezer) -> None:
    source = await _setup(
        hass,
        freezer,
        now=datetime(2026, 8, 28, 2, 0),
        peak={PEAK_ENABLED: True, PEAK_START: "20:00:00", PEAK_END: "06:00:00"},
    )
    await _select(hass, "home")
    assert _pushed(source) == (62.0, 82.0)


# --- Boundary crossings ----------------------------------------------------


async def test_peak_opening_swaps_a_held_home_to_the_eco_band(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=datetime(2026, 8, 28, 15, 58))
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)

    await _cross_boundary(hass, freezer, PEAK_START_AT)
    assert _pushed(source) == (62.0, 82.0)
    assert _preset(hass) == "home"


async def test_peak_closing_restores_the_home_band(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=datetime(2026, 8, 28, 20, 58))
    await _select(hass, "home")
    assert _pushed(source) == (62.0, 82.0)

    await _cross_boundary(hass, freezer, PEAK_END_AT)
    assert _pushed(source) == (68.0, 76.0)
    assert _preset(hass) == "home"


async def test_a_tick_inside_the_window_does_not_re_push(hass, freezer) -> None:
    """Only the transition acts; the wrapper is not a control loop."""
    source = await _setup(hass, freezer, now=IN_PEAK)
    await _select(hass, "home")

    source._attr_target_temperature_low = 63.0
    later = IN_PEAK + timedelta(minutes=1)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done()

    assert source._attr_target_temperature_low == 63.0


async def test_a_boundary_does_not_disturb_another_preset(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=datetime(2026, 8, 28, 15, 58))
    await _select(hass, "sleep")
    assert _pushed(source) == (64.0, 72.0)

    await _cross_boundary(hass, freezer, PEAK_START_AT)
    assert _pushed(source) == (64.0, 72.0)
    assert _preset(hass) == "sleep"


async def test_a_boundary_does_not_push_when_no_preset_is_held(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=datetime(2026, 8, 28, 15, 58))
    source._attr_target_temperature_low = 63.0
    source._attr_target_temperature_high = 84.0

    await _cross_boundary(hass, freezer, PEAK_START_AT)
    assert _pushed(source) == (63.0, 84.0)
    assert _preset(hass) is None


async def test_a_manual_change_stops_the_boundary_from_re_pushing(
    hass, freezer
) -> None:
    """Drift clears the preset, which takes the wrapper out of the picture
    until someone picks a preset again — so peak cannot fight a wall
    thermostat that has taken the setpoint."""
    source = await _setup(hass, freezer, now=datetime(2026, 8, 28, 15, 50))
    await _select(hass, "home")

    # Past the apply guard, then something else moves the setpoint.
    freezer.move_to(datetime(2026, 8, 28, 15, 55))
    await hass.services.async_call(
        "climate", "set_temperature",
        {ATTR_ENTITY_ID: SOURCE_ENTITY_ID, "target_temp_low": 63.0,
         "target_temp_high": 84.0},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _preset(hass) is None

    await _cross_boundary(hass, freezer, PEAK_START_AT)
    assert _pushed(source) == (63.0, 84.0)


# --- Published peak state --------------------------------------------------


async def test_attributes_publish_the_window_boundaries(hass, freezer) -> None:
    """A future pre-cool needs *when* peak starts, not just whether it is on."""
    await _setup(hass, freezer, now=OFF_PEAK)
    attrs = _attrs(hass)
    assert attrs["peak_active"] is False
    assert attrs["peak_start"] == "16:00:00"
    assert attrs["peak_end"] == "21:00:00"
    assert attrs["next_peak_start"].startswith("2026-08-28T16:00:00")


async def test_next_peak_start_rolls_over_once_the_window_has_opened(
    hass, freezer
) -> None:
    await _setup(hass, freezer, now=IN_PEAK)
    attrs = _attrs(hass)
    assert attrs["peak_active"] is True
    assert attrs["next_peak_start"].startswith("2026-08-29T16:00:00")
    assert attrs["next_peak_end"].startswith("2026-08-28T21:00:00")


async def test_effective_preset_names_the_band_actually_applied(
    hass, freezer
) -> None:
    await _setup(hass, freezer, now=IN_PEAK)
    await _select(hass, "home")
    assert _preset(hass) == "home"
    assert _attrs(hass)["effective_preset"] == "eco"


async def test_effective_preset_matches_the_preset_off_peak(hass, freezer) -> None:
    await _setup(hass, freezer, now=OFF_PEAK)
    await _select(hass, "home")
    assert _attrs(hass)["effective_preset"] == "home"


async def test_peak_attributes_collapse_when_peak_is_off(hass, freezer) -> None:
    await _setup(hass, freezer, now=IN_PEAK, peak=PEAK_OFF)
    assert _attrs(hass)["peak_active"] is False
    assert "next_peak_start" not in _attrs(hass)
