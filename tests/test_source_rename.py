"""The wrapper follows its source when the source entity is renamed.

The source is pinned by entity id at creation. Before this, renaming
climate.bedroom_heat_pump left the wrapper subscribed to an id nothing
publishes: permanently unavailable, nothing logged, no hint of the cause.

These drive a real rename through the entity registry and assert the wrapper
keeps working — reading from and writing to the entity under its new name.
"""
from __future__ import annotations

from datetime import datetime

from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    setup_test_component_platform,
)

from custom_components.magic_climate.const import (
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
)

from .test_climate_temperature import (
    MAGIC_ENTITY_ID,
    SOURCE_ENTITY_ID,
    FakeThermostat,
)
from .test_peak_substitution import DEFAULT_PRESETS, OFF_PEAK, PEAK_OFF, PEAK_ON

RENAMED = "climate.bedroom_heat_pump_v2"


async def _setup(
    hass: HomeAssistant, freezer, *, now: datetime = OFF_PEAK, peak=PEAK_OFF
) -> tuple[FakeThermostat, MockConfigEntry]:
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
        unique_id=f"{DOMAIN}::{SOURCE_ENTITY_ID}",
        data={CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY_ID, CONF_NAME: "Bedroom Magic"},
        options={
            CONF_ENABLED_PRESETS: ["home", "eco", "sleep"],
            CONF_PRESETS: DEFAULT_PRESETS,
            CONF_PEAK: peak,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return source, entry


async def _rename(hass: HomeAssistant, old: str, new: str) -> None:
    er.async_get(hass).async_update_entity(old, new_entity_id=new)
    await hass.async_block_till_done()


def _pushed(source: FakeThermostat) -> tuple[float, float]:
    return (
        round(source._attr_target_temperature_low, 1),
        round(source._attr_target_temperature_high, 1),
    )


# --- The entry follows the rename ------------------------------------------


async def test_entry_data_follows_the_rename(hass, freezer) -> None:
    _, entry = await _setup(hass, freezer)
    await _rename(hass, SOURCE_ENTITY_ID, RENAMED)
    assert entry.data[CONF_SOURCE_ENTITY_ID] == RENAMED


async def test_unique_id_follows_the_rename(hass, freezer) -> None:
    """It embeds the source id; a stale one would stop guarding against the
    renamed entity being wrapped a second time."""
    _, entry = await _setup(hass, freezer)
    await _rename(hass, SOURCE_ENTITY_ID, RENAMED)
    assert entry.unique_id == f"{DOMAIN}::{RENAMED}"


# --- The wrapper keeps working ---------------------------------------------


async def test_the_wrapper_stays_available_after_a_rename(hass, freezer) -> None:
    await _setup(hass, freezer)
    await _rename(hass, SOURCE_ENTITY_ID, RENAMED)
    assert hass.states.get(MAGIC_ENTITY_ID).state != "unavailable"


async def test_reads_still_track_the_renamed_source(hass, freezer) -> None:
    source, _ = await _setup(hass, freezer)
    await _rename(hass, SOURCE_ENTITY_ID, RENAMED)

    source._attr_current_temperature = 69.0
    source.async_write_ha_state()
    await hass.async_block_till_done()

    assert hass.states.get(MAGIC_ENTITY_ID).attributes[
        "current_temperature"
    ] == 69.0


async def test_writes_still_reach_the_renamed_source(hass, freezer) -> None:
    """The subscription and every service call captured the old id, so this
    is the assertion that actually proves the reload rebuilt them."""
    source, _ = await _setup(hass, freezer)
    await _rename(hass, SOURCE_ENTITY_ID, RENAMED)

    await hass.services.async_call(
        "climate", "set_preset_mode",
        {ATTR_ENTITY_ID: MAGIC_ENTITY_ID, "preset_mode": "home"}, blocking=True,
    )
    await hass.async_block_till_done()

    assert _pushed(source) == (68.0, 76.0)


async def test_setting_temperature_still_reaches_the_renamed_source(
    hass, freezer
) -> None:
    source, _ = await _setup(hass, freezer)
    await _rename(hass, SOURCE_ENTITY_ID, RENAMED)

    await hass.services.async_call(
        "climate", "set_temperature",
        {ATTR_ENTITY_ID: MAGIC_ENTITY_ID, ATTR_TEMPERATURE: 75.0}, blocking=True,
    )
    await hass.async_block_till_done()

    assert source._attr_target_temperature_high == 75.0


# --- The held preset survives the reload -----------------------------------


async def test_the_held_preset_survives_a_rename(hass, freezer) -> None:
    """A rename reloads the entry, which recreates the entity — RestoreEntity
    is what keeps the preset from being dropped by the repair."""
    await _setup(hass, freezer)
    await hass.services.async_call(
        "climate", "set_preset_mode",
        {ATTR_ENTITY_ID: MAGIC_ENTITY_ID, "preset_mode": "home"}, blocking=True,
    )
    await hass.async_block_till_done()

    await _rename(hass, SOURCE_ENTITY_ID, RENAMED)
    assert hass.states.get(MAGIC_ENTITY_ID).attributes.get("preset_mode") == "home"


async def test_a_rename_does_not_command_the_hardware(hass, freezer) -> None:
    """Repairing a link is not a reason to push setpoints."""
    source, _ = await _setup(hass, freezer)
    source._attr_target_temperature_low = 63.0
    source._attr_target_temperature_high = 84.0
    source.async_write_ha_state()
    await hass.async_block_till_done()

    await _rename(hass, SOURCE_ENTITY_ID, RENAMED)
    assert _pushed(source) == (63.0, 84.0)


async def test_peak_substitution_still_works_after_a_rename(hass, freezer) -> None:
    source, _ = await _setup(
        hass, freezer, now=datetime(2026, 8, 28, 18, 0), peak=PEAK_ON
    )
    await _rename(hass, SOURCE_ENTITY_ID, RENAMED)

    await hass.services.async_call(
        "climate", "set_preset_mode",
        {ATTR_ENTITY_ID: MAGIC_ENTITY_ID, "preset_mode": "home"}, blocking=True,
    )
    await hass.async_block_till_done()

    assert _pushed(source) == (62.0, 82.0)
    assert hass.states.get(MAGIC_ENTITY_ID).attributes["preset_mode"] == "home"


# --- Unrelated registry churn is ignored -----------------------------------


async def test_renaming_something_else_is_ignored(hass, freezer) -> None:
    _, entry = await _setup(hass, freezer)
    er.async_get(hass).async_update_entity(
        MAGIC_ENTITY_ID, new_entity_id="climate.renamed_wrapper"
    )
    await hass.async_block_till_done()
    assert entry.data[CONF_SOURCE_ENTITY_ID] == SOURCE_ENTITY_ID


async def test_a_friendly_name_change_does_not_reload(hass, freezer) -> None:
    """Only entity_id changes matter; a display-name edit must not churn."""
    _, entry = await _setup(hass, freezer)
    er.async_get(hass).async_update_entity(SOURCE_ENTITY_ID, name="Upstairs Unit")
    await hass.async_block_till_done()
    assert entry.data[CONF_SOURCE_ENTITY_ID] == SOURCE_ENTITY_ID
