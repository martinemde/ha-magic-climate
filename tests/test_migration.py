"""Config-entry migrations.

Three v3 entries exist in the live install, one per heat pump. v4 is the
version where Home stops owning a band, so the thing to pin is that an
upgrade changes what the *options* look like without changing what the house
does: the same band Home was applying before is the band it applies after,
and nothing new starts running on a schedule.
"""
from __future__ import annotations

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.magic_climate.const import (
    BOOST_PRELOAD,
    CONF_BOOST,
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SLEEP,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
    PEAK_ENABLED,
    WINDOW_END,
    WINDOW_START,
)

SOURCE_ENTITY_ID = "climate.living_area_heat_pump"


async def _migrate(hass: HomeAssistant, *, version: int, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=version,
        data={CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY_ID, CONF_NAME: "Living Area Magic"},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


HOME_BAND = {"low": 20.0, "high": 22.0}
PEAK_CONFIGURED = {PEAK_ENABLED: True, WINDOW_START: "16:00:00", WINDOW_END: "21:00:00"}

# What the live install looks like today.
V3_OPTIONS = {
    CONF_ENABLED_PRESETS: ["home", "away", "sleep", "eco"],
    CONF_PRESETS: {
        "home": HOME_BAND,
        "away": {"low": 16.0, "high": 26.0},
        "sleep": {"low": 17.78, "high": 24.44},
        "eco": {"low": 17.0, "high": 25.0},
    },
    CONF_PEAK: PEAK_CONFIGURED,
}

V2_OPTIONS = {k: v for k, v in V3_OPTIONS.items() if k != CONF_PEAK}


async def test_v3_entry_becomes_v4(hass: HomeAssistant) -> None:
    entry = await _migrate(hass, version=3, options=V3_OPTIONS)
    assert entry.version == 4


async def test_the_home_band_becomes_the_comfort_band(hass: HomeAssistant) -> None:
    """The whole point of the split: Home was already your comfort setting."""
    entry = await _migrate(hass, version=3, options=V3_OPTIONS)
    assert entry.options[CONF_PRESETS]["comfort"] == HOME_BAND


async def test_home_is_no_longer_listed_as_enabled(hass: HomeAssistant) -> None:
    """Home, Comfort and Away are unconditional; only the checkboxes list."""
    entry = await _migrate(hass, version=3, options=V3_OPTIONS)
    assert entry.options[CONF_ENABLED_PRESETS] == ["eco", "sleep"]


async def test_other_bands_survive_untouched(hass: HomeAssistant) -> None:
    entry = await _migrate(hass, version=3, options=V3_OPTIONS)
    presets = entry.options[CONF_PRESETS]
    assert presets["eco"] == {"low": 17.0, "high": 25.0}
    assert presets["sleep"] == {"low": 17.78, "high": 24.44}
    assert presets["away"] == {"low": 16.0, "high": 26.0}


async def test_a_band_never_configured_gets_its_default(hass: HomeAssistant) -> None:
    entry = await _migrate(hass, version=3, options=V3_OPTIONS)
    assert entry.options[CONF_PRESETS]["boost"] == {"low": 20.5, "high": 21.5}


async def test_the_peak_window_is_carried_across(hass: HomeAssistant) -> None:
    entry = await _migrate(hass, version=3, options=V3_OPTIONS)
    assert entry.options[CONF_PEAK] == PEAK_CONFIGURED


async def test_nothing_new_starts_running(hass: HomeAssistant) -> None:
    """An upgrade must not begin preloading or putting the house to bed."""
    entry = await _migrate(hass, version=3, options=V3_OPTIONS)
    assert entry.options[CONF_BOOST][BOOST_PRELOAD] is False
    assert entry.options[CONF_SLEEP] == {WINDOW_START: "", WINDOW_END: ""}


async def test_a_stored_hvac_mode_is_dropped(hass: HomeAssistant) -> None:
    """Presets no longer carry a mode; a stale key must not survive."""
    entry = await _migrate(
        hass,
        version=3,
        options={
            **V3_OPTIONS,
            CONF_PRESETS: {
                **V3_OPTIONS[CONF_PRESETS],
                "home": {**HOME_BAND, "mode": "heat_cool", "fan": "auto"},
            },
        },
    )
    comfort = entry.options[CONF_PRESETS]["comfort"]
    assert "mode" not in comfort
    assert comfort["fan"] == "auto"


async def test_a_pre_existing_comfort_band_loses_to_home(hass: HomeAssistant) -> None:
    """Comfort meant something else before v4. Home is what Home applied."""
    entry = await _migrate(
        hass,
        version=3,
        options={
            **V3_OPTIONS,
            CONF_PRESETS: {
                **V3_OPTIONS[CONF_PRESETS],
                "comfort": {"low": 1.0, "high": 2.0},
            },
        },
    )
    assert entry.options[CONF_PRESETS]["comfort"] == HOME_BAND


async def test_a_v2_entry_migrates_all_the_way(hass: HomeAssistant) -> None:
    entry = await _migrate(hass, version=2, options=V2_OPTIONS)
    assert entry.version == 4
    assert entry.options[CONF_PRESETS]["comfort"] == HOME_BAND
    assert entry.options[CONF_PEAK][PEAK_ENABLED] is False


async def test_a_v1_entry_migrates_all_the_way(hass: HomeAssistant) -> None:
    entry = await _migrate(
        hass,
        version=1,
        options={CONF_PRESETS: [{"name": "Home", "low": 20.0, "high": 22.0}]},
    )
    assert entry.version == 4
    assert entry.options[CONF_PRESETS]["comfort"] == HOME_BAND
    assert entry.options[CONF_ENABLED_PRESETS] == []
    assert entry.options[CONF_PEAK][PEAK_ENABLED] is False


async def test_migrating_twice_is_a_no_op(hass: HomeAssistant) -> None:
    """A v4 entry must not be rewritten back to defaults."""
    v4 = {
        CONF_ENABLED_PRESETS: ["eco"],
        CONF_PRESETS: {"comfort": {"low": 19.0, "high": 23.0}},
        CONF_PEAK: {PEAK_ENABLED: True, WINDOW_START: "14:00:00", WINDOW_END: "19:00:00"},
        CONF_BOOST: {BOOST_PRELOAD: True, "minutes": 90},
        CONF_SLEEP: {WINDOW_START: "22:00:00", WINDOW_END: "06:00:00"},
    }
    entry = await _migrate(hass, version=4, options=v4)
    assert entry.options == v4
