"""The schedule driving a real Home Assistant instance.

Home holds no band of its own: it resolves to Comfort, Eco, Boost or Sleep by
the wall clock and keeps *reporting* "home" throughout. These tests pin both
halves of that — the setpoints the source actually receives, and the preset
the wrapper advertises — plus the rule that picking anything else pins it.

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
    BOOST_MINUTES,
    BOOST_PRELOAD,
    CONF_BOOST,
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SLEEP,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
    PEAK_ENABLED,
    PRESET_HIGH,
    PRESET_LOW,
    WINDOW_END,
    WINDOW_START,
)

from .test_climate_temperature import (
    MAGIC_ENTITY_ID,
    SOURCE_ENTITY_ID,
    FakeThermostat,
)

MIDDAY = datetime(2026, 8, 28, 12, 0)
IN_PRELOAD = datetime(2026, 8, 28, 15, 30)
IN_PEAK = datetime(2026, 8, 28, 18, 0)
IN_SLEEP = datetime(2026, 8, 28, 23, 30)
PRELOAD_START_AT = datetime(2026, 8, 28, 15, 0)
PEAK_START_AT = datetime(2026, 8, 28, 16, 0)
PEAK_END_AT = datetime(2026, 8, 28, 21, 0)


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def band(low_f: float, high_f: float) -> dict[str, float]:
    return {PRESET_LOW: f_to_c(low_f), PRESET_HIGH: f_to_c(high_f)}


# Distinct enough that a mix-up cannot pass by rounding.
COMFORT = band(68.0, 76.0)
ECO = band(62.0, 82.0)
BOOST = band(72.0, 73.0)
SLEEP = band(64.0, 70.0)
AWAY = band(61.0, 88.0)

BANDS = {
    "comfort": COMFORT,
    "away": AWAY,
    "eco": ECO,
    "boost": BOOST,
    "sleep": SLEEP,
}

PEAK_ON = {PEAK_ENABLED: True, WINDOW_START: "16:00:00", WINDOW_END: "21:00:00"}
PEAK_OFF = {**PEAK_ON, PEAK_ENABLED: False}
PEAK_MANUAL = {PEAK_ENABLED: True, WINDOW_START: "", WINDOW_END: ""}
PRELOAD_ON = {BOOST_PRELOAD: True, BOOST_MINUTES: 60}
PRELOAD_OFF = {BOOST_PRELOAD: False, BOOST_MINUTES: 60}
SLEEP_WINDOW = {WINDOW_START: "23:00:00", WINDOW_END: "06:30:00"}
SLEEP_MANUAL = {WINDOW_START: "", WINDOW_END: ""}


async def _setup(
    hass: HomeAssistant,
    freezer,
    *,
    now: datetime = MIDDAY,
    enabled: list[str] | None = None,
    peak: dict[str, Any] | None = None,
    boost: dict[str, Any] | None = None,
    sleep: dict[str, Any] | None = None,
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
        version=4,
        data={CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY_ID, CONF_NAME: "Bedroom Magic"},
        options={
            CONF_ENABLED_PRESETS: ["eco"] if enabled is None else enabled,
            CONF_PRESETS: BANDS,
            CONF_PEAK: PEAK_ON if peak is None else peak,
            CONF_BOOST: PRELOAD_OFF if boost is None else boost,
            CONF_SLEEP: SLEEP_MANUAL if sleep is None else sleep,
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
    """Advance the clock past a window boundary and let the minute tick fire."""
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


def _modes(hass: HomeAssistant) -> list[str]:
    return _attrs(hass)["preset_modes"]


# --- Home resolves by the clock --------------------------------------------


async def test_home_off_peak_pushes_the_comfort_band(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=MIDDAY)
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


async def test_home_during_preload_pushes_the_boost_band(hass, freezer) -> None:
    source = await _setup(
        hass, freezer, now=IN_PRELOAD,
        enabled=["eco", "boost"], boost=PRELOAD_ON,
    )
    await _select(hass, "home")
    assert _pushed(source) == (72.0, 73.0)
    assert _preset(hass) == "home"


async def test_home_during_sleep_pushes_the_sleep_band(hass, freezer) -> None:
    source = await _setup(
        hass, freezer, now=IN_SLEEP, enabled=["eco", "sleep"], sleep=SLEEP_WINDOW,
    )
    await _select(hass, "home")
    assert _pushed(source) == (64.0, 70.0)
    assert _preset(hass) == "home"


async def test_sleep_outranks_an_overnight_peak(hass, freezer) -> None:
    """Stated priority: the room stays sleepable rather than cheap."""
    source = await _setup(
        hass,
        freezer,
        now=IN_SLEEP,
        enabled=["eco", "sleep"],
        peak={PEAK_ENABLED: True, WINDOW_START: "20:00:00", WINDOW_END: "06:00:00"},
        sleep=SLEEP_WINDOW,
    )
    await _select(hass, "home")
    assert _pushed(source) == (64.0, 70.0)


async def test_an_overnight_peak_applies_before_sleep_starts(hass, freezer) -> None:
    source = await _setup(
        hass,
        freezer,
        now=datetime(2026, 8, 28, 21, 0),
        enabled=["eco", "sleep"],
        peak={PEAK_ENABLED: True, WINDOW_START: "20:00:00", WINDOW_END: "06:00:00"},
        sleep=SLEEP_WINDOW,
    )
    await _select(hass, "home")
    assert _pushed(source) == (62.0, 82.0)


# --- Nothing scheduled -----------------------------------------------------


async def test_home_is_comfort_when_the_switch_is_off(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=IN_PEAK, peak=PEAK_OFF)
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)


async def test_home_is_comfort_when_eco_is_not_created(hass, freezer) -> None:
    """The window needs its band; switching Eco off silences it."""
    source = await _setup(hass, freezer, now=IN_PEAK, enabled=[])
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)


async def test_comfort_is_hidden_while_nothing_moves_home(hass, freezer) -> None:
    """Home already is the Comfort band; two entries would be one thing twice.

    Eco stays in the list. Its checkbox is on, so the band was created and can
    be selected — switching the auto-swap off only stops it firing by itself.
    """
    await _setup(hass, freezer, peak=PEAK_OFF)
    assert _modes(hass) == ["home", "away", "eco"]


async def test_comfort_appears_once_something_moves_home(hass, freezer) -> None:
    await _setup(hass, freezer)
    assert _modes(hass) == ["home", "comfort", "away", "eco"]


async def test_a_preset_with_no_times_is_created_but_never_fires(
    hass, freezer
) -> None:
    """Blank times mean the band exists to be selected, nothing more."""
    source = await _setup(
        hass, freezer, now=IN_SLEEP, enabled=["eco", "sleep"], sleep=SLEEP_MANUAL,
    )
    assert "sleep" in _modes(hass)
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)


async def test_eco_with_no_peak_times_is_manual_only(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=IN_PEAK, peak=PEAK_MANUAL)
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)
    assert "eco" in _modes(hass)


async def test_preload_needs_the_boost_band_to_exist(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=IN_PRELOAD, boost=PRELOAD_ON)
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)


# --- Selecting anything else pins it ---------------------------------------


async def test_choosing_eco_off_peak_holds_eco(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=MIDDAY)
    await _select(hass, "eco")
    assert _pushed(source) == (62.0, 82.0)
    assert _preset(hass) == "eco"


async def test_choosing_comfort_during_peak_holds_comfort(hass, freezer) -> None:
    """The reason Comfort is a preset at all: pinning the non-peak band."""
    source = await _setup(hass, freezer, now=IN_PEAK)
    await _select(hass, "comfort")
    assert _pushed(source) == (68.0, 76.0)
    assert _preset(hass) == "comfort"


async def test_choosing_sleep_outside_its_window_holds_sleep(hass, freezer) -> None:
    source = await _setup(
        hass, freezer, now=MIDDAY, enabled=["eco", "sleep"], sleep=SLEEP_WINDOW,
    )
    await _select(hass, "sleep")
    assert _pushed(source) == (64.0, 70.0)
    assert _preset(hass) == "sleep"


async def test_away_is_always_available(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=IN_PEAK, enabled=[])
    await _select(hass, "away")
    assert _pushed(source) == (61.0, 88.0)
    assert _preset(hass) == "away"


# --- Boundary crossings ----------------------------------------------------


async def test_peak_opening_swaps_a_held_home_to_the_eco_band(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=datetime(2026, 8, 28, 15, 58))
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)

    await _cross_boundary(hass, freezer, PEAK_START_AT)
    assert _pushed(source) == (62.0, 82.0)
    assert _preset(hass) == "home"


async def test_peak_closing_restores_the_comfort_band(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=datetime(2026, 8, 28, 20, 58))
    await _select(hass, "home")
    assert _pushed(source) == (62.0, 82.0)

    await _cross_boundary(hass, freezer, PEAK_END_AT)
    assert _pushed(source) == (68.0, 76.0)
    assert _preset(hass) == "home"


async def test_preload_opens_an_hour_before_peak(hass, freezer) -> None:
    source = await _setup(
        hass, freezer, now=datetime(2026, 8, 28, 14, 58),
        enabled=["eco", "boost"], boost=PRELOAD_ON,
    )
    await _select(hass, "home")
    assert _pushed(source) == (68.0, 76.0)

    await _cross_boundary(hass, freezer, PRELOAD_START_AT)
    assert _pushed(source) == (72.0, 73.0)


async def test_preload_hands_straight_over_to_peak(hass, freezer) -> None:
    """Boost runs up to peak start and Eco takes it from there, no gap."""
    source = await _setup(
        hass, freezer, now=IN_PRELOAD, enabled=["eco", "boost"], boost=PRELOAD_ON,
    )
    await _select(hass, "home")
    assert _pushed(source) == (72.0, 73.0)

    await _cross_boundary(hass, freezer, PEAK_START_AT)
    assert _pushed(source) == (62.0, 82.0)
    assert _preset(hass) == "home"


async def test_a_tick_inside_a_window_does_not_re_push(hass, freezer) -> None:
    """Only the transition acts; the wrapper is not a control loop."""
    source = await _setup(hass, freezer, now=IN_PEAK)
    await _select(hass, "home")

    source._attr_target_temperature_low = 63.0
    later = IN_PEAK + timedelta(minutes=1)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done()

    assert source._attr_target_temperature_low == 63.0


async def test_a_boundary_does_not_disturb_a_pinned_preset(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=datetime(2026, 8, 28, 15, 58))
    await _select(hass, "comfort")
    assert _pushed(source) == (68.0, 76.0)

    await _cross_boundary(hass, freezer, PEAK_START_AT)
    assert _pushed(source) == (68.0, 76.0)
    assert _preset(hass) == "comfort"


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
    until someone picks a preset again — so the schedule cannot fight a wall
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


# --- Published state -------------------------------------------------------


async def test_attributes_publish_the_peak_boundaries(hass, freezer) -> None:
    """A pre-cool consumer needs *when* peak starts, not just whether it is on."""
    await _setup(hass, freezer, now=MIDDAY)
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


async def test_effective_preset_is_comfort_off_peak(hass, freezer) -> None:
    await _setup(hass, freezer, now=MIDDAY)
    await _select(hass, "home")
    assert _attrs(hass)["effective_preset"] == "comfort"


async def test_peak_attributes_collapse_when_nothing_is_scheduled(
    hass, freezer
) -> None:
    await _setup(hass, freezer, now=IN_PEAK, peak=PEAK_OFF)
    assert _attrs(hass)["peak_active"] is False
    assert "next_peak_start" not in _attrs(hass)
