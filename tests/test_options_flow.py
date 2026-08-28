"""Options-flow tests: preset mode/fan dropdowns come from the source entity.

A preset's HVAC mode and fan mode used to be a static list and a free-text
box. Both are now offered as the wrapped climate entity reports them, so a
preset can only ask the hardware for something it actually has.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import CONF_NAME, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    setup_test_component_platform,
)

from custom_components.magic_climate.const import (
    CONF_ENABLED_PRESETS,
    CONF_PRESETS,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
    PRESET_FAN,
    PRESET_HIGH,
    PRESET_LOW,
    PRESET_MODE,
    STANDARD_PRESETS,
)

SOURCE_ENTITY_ID = "climate.test_source"


class FanThermostat(ClimateEntity):
    """A source that reports a narrow set of modes and a real fan list."""

    _attr_name = "Test Source"
    _attr_unique_id = "test_source"
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
    _attr_fan_modes = ["auto", "low", "high"]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
    )

    def __init__(self) -> None:
        self._attr_hvac_mode = HVACMode.COOL
        self._attr_fan_mode = "auto"
        self._attr_current_temperature = 21.0
        self._attr_target_temperature = 22.0


class FanlessThermostat(FanThermostat):
    """A source with no fan control at all."""

    _attr_fan_modes = None
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE


async def _setup(
    hass: HomeAssistant,
    *,
    source: ClimateEntity | None = None,
    presets: dict[str, dict[str, Any]] | None = None,
) -> MockConfigEntry:
    """Stand up a source thermostat and a Magic Climate entry wrapping it."""
    setup_test_component_platform(hass, "climate", [source or FanThermostat()], built_in=True)
    assert await async_setup_component(hass, "climate", {"climate": {"platform": "test"}})
    await hass.async_block_till_done()

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY_ID, CONF_NAME: "Bedroom Magic"},
        options={
            CONF_ENABLED_PRESETS: ["home", "sleep"],
            CONF_PRESETS: presets
            or {
                "home": {PRESET_LOW: 20.0, PRESET_HIGH: 22.0},
                "sleep": {PRESET_LOW: 18.0, PRESET_HIGH: 21.0},
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _menu_step(hass: HomeAssistant, entry: MockConfigEntry, step_id: str):
    """Open the options flow and step into one menu entry's form."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def _preset_form(hass: HomeAssistant, entry: MockConfigEntry, preset_id: str):
    """Open the options flow and step into one preset's form."""
    return await _menu_step(hass, entry, f"preset_{preset_id}")


def _field_options(result, field: str) -> list[str] | None:
    """The selectable values for `field`, or None if it is not a dropdown."""
    for key, validator in result["data_schema"].schema.items():
        if str(key) != field:
            continue
        config = getattr(validator, "config", None)
        if config is None or "options" not in config:
            return None
        return [option["value"] for option in config["options"]]
    raise AssertionError(f"{field!r} not in schema")


# --- Options come from the source ------------------------------------------


async def test_mode_options_come_from_source_hvac_modes(hass: HomeAssistant) -> None:
    """The source offers off/heat/cool, so the preset cannot ask for dry."""
    entry = await _setup(hass)
    result = await _preset_form(hass, entry, "sleep")
    assert _field_options(result, PRESET_MODE) == ["", "off", "heat", "cool"]


async def test_fan_options_come_from_source_fan_modes(hass: HomeAssistant) -> None:
    """Fan is a dropdown of the source's fan list, not free text."""
    entry = await _setup(hass)
    result = await _preset_form(hass, entry, "sleep")
    assert _field_options(result, PRESET_FAN) == ["", "auto", "low", "high"]


async def test_fan_falls_back_to_text_when_source_has_no_fan_modes(
    hass: HomeAssistant,
) -> None:
    """No universal fan vocabulary exists, so a fanless source leaves text."""
    entry = await _setup(hass, source=FanlessThermostat())
    result = await _preset_form(hass, entry, "sleep")
    assert _field_options(result, PRESET_FAN) is None


async def test_stored_value_the_source_no_longer_offers_is_kept(
    hass: HomeAssistant,
) -> None:
    """Re-pointing at a different unit must not silently drop a stored fan."""
    entry = await _setup(
        hass,
        presets={
            "home": {PRESET_LOW: 20.0, PRESET_HIGH: 22.0},
            "sleep": {PRESET_LOW: 18.0, PRESET_HIGH: 21.0, PRESET_FAN: "turbo"},
        },
    )
    result = await _preset_form(hass, entry, "sleep")
    assert _field_options(result, PRESET_FAN) == ["", "auto", "low", "high", "turbo"]


# --- Round-trip: picking from the dropdown stores the value ----------------


async def test_selecting_mode_and_fan_persists_to_the_preset(
    hass: HomeAssistant,
) -> None:
    entry = await _setup(hass)
    result = await _preset_form(hass, entry, "sleep")
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PRESET_LOW: 18.0, PRESET_HIGH: 21.0, PRESET_MODE: "cool", PRESET_FAN: "high"},
    )
    await hass.async_block_till_done()

    stored = entry.options[CONF_PRESETS]["sleep"]
    assert stored[PRESET_MODE] == "cool"
    assert stored[PRESET_FAN] == "high"


async def test_leave_unchanged_clears_a_previously_set_fan(
    hass: HomeAssistant,
) -> None:
    """The empty option means "this preset does not touch the fan"."""
    entry = await _setup(
        hass,
        presets={
            "home": {PRESET_LOW: 20.0, PRESET_HIGH: 22.0},
            "sleep": {PRESET_LOW: 18.0, PRESET_HIGH: 21.0, PRESET_FAN: "high"},
        },
    )
    result = await _preset_form(hass, entry, "sleep")
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PRESET_LOW: 18.0, PRESET_HIGH: 21.0, PRESET_MODE: "", PRESET_FAN: ""},
    )
    await hass.async_block_till_done()

    assert PRESET_FAN not in entry.options[CONF_PRESETS]["sleep"]


async def test_preset_form_returns_to_the_menu_after_submit(
    hass: HomeAssistant,
) -> None:
    entry = await _setup(hass)
    result = await _preset_form(hass, entry, "home")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PRESET_LOW: 20.0, PRESET_HIGH: 22.0, PRESET_MODE: "heat", PRESET_FAN: "auto"},
    )
    assert result["type"] is FlowResultType.MENU


# --- The wrapped entity is fixed at creation -------------------------------


async def test_basic_options_does_not_offer_the_source_entity(
    hass: HomeAssistant,
) -> None:
    """Re-pointing an entry would orphan its stored mode/fan values and its
    state subscription, so the source is only settable in the config flow."""
    entry = await _setup(hass)
    result = await _menu_step(hass, entry, "basic")
    assert set(result["data_schema"].schema) == set(STANDARD_PRESETS)


async def test_basic_options_submit_leaves_the_source_alone(
    hass: HomeAssistant,
) -> None:
    entry = await _setup(hass)
    result = await _menu_step(hass, entry, "basic")
    await hass.config_entries.options.async_configure(
        result["flow_id"], {pid: pid in ("home", "sleep", "eco") for pid in STANDARD_PRESETS}
    )
    await hass.async_block_till_done()

    assert entry.data[CONF_SOURCE_ENTITY_ID] == SOURCE_ENTITY_ID
    assert entry.options[CONF_ENABLED_PRESETS] == ["home", "sleep", "eco"]
