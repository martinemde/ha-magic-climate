"""Config-entry migrations.

Three v2 entries exist in the live install, one per heat pump. They must come
forward with their presets intact and peak substitution switched off, so the
upgrade changes nothing about how the house behaves until peak is configured.
"""
from __future__ import annotations

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.magic_climate.const import (
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
    PEAK_ENABLED,
    PEAK_END,
    PEAK_START,
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


V2_OPTIONS = {
    CONF_ENABLED_PRESETS: ["home", "away", "sleep", "eco"],
    CONF_PRESETS: {
        "home": {"low": 20.0, "high": 22.0},
        "away": {"low": 16.0, "high": 26.0},
        "sleep": {"low": 17.78, "high": 24.44},
        "eco": {"low": 17.0, "high": 25.0},
    },
}


async def test_v2_entry_becomes_v3(hass: HomeAssistant) -> None:
    entry = await _migrate(hass, version=2, options=V2_OPTIONS)
    assert entry.version == 3


async def test_v2_migration_adds_peak_switched_off(hass: HomeAssistant) -> None:
    """An upgrade must not start substituting Eco on its own."""
    entry = await _migrate(hass, version=2, options=V2_OPTIONS)
    assert entry.options[CONF_PEAK] == {
        PEAK_ENABLED: False,
        PEAK_START: "16:00:00",
        PEAK_END: "21:00:00",
    }


async def test_v2_migration_preserves_presets_and_enabled(hass: HomeAssistant) -> None:
    entry = await _migrate(hass, version=2, options=V2_OPTIONS)
    assert entry.options[CONF_ENABLED_PRESETS] == ["home", "away", "sleep", "eco"]
    assert entry.options[CONF_PRESETS] == V2_OPTIONS[CONF_PRESETS]


async def test_v1_entry_migrates_all_the_way_to_v3(hass: HomeAssistant) -> None:
    """The v1 -> v2 reshape now lands on v3 directly rather than stopping short."""
    entry = await _migrate(
        hass,
        version=1,
        options={CONF_PRESETS: [{"name": "Home", "low": 20.0, "high": 22.0}]},
    )
    assert entry.version == 3
    assert entry.options[CONF_ENABLED_PRESETS] == ["home"]
    assert entry.options[CONF_PEAK][PEAK_ENABLED] is False


async def test_migrating_twice_is_a_no_op(hass: HomeAssistant) -> None:
    """A v3 entry with peak already configured must not be reset to defaults."""
    configured = {PEAK_ENABLED: True, PEAK_START: "14:00:00", PEAK_END: "19:00:00"}
    entry = await _migrate(
        hass, version=3, options={**V2_OPTIONS, CONF_PEAK: configured}
    )
    assert entry.options[CONF_PEAK] == configured
