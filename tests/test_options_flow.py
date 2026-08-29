"""Options-flow tests: one form, sections, and the coherence rules.

The whole configuration is a single step now — Comfort, Away, Eco, Boost and
Sleep are read together because they only mean anything relative to each
other. What is worth pinning here is the round trip (what the form offers,
what a submit stores) and the small set of combinations the flow refuses.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import CONF_NAME, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, section
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
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
    PRESET_FAN,
    PRESET_HIGH,
    PRESET_LOW,
    WINDOW_END,
    WINDOW_START,
)

SOURCE_ENTITY_ID = "climate.test_source"


class FanThermostat(ClimateEntity):
    """A source that reports a real fan list."""

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


BANDS = {
    "comfort": {PRESET_LOW: 20.0, PRESET_HIGH: 22.0},
    "away": {PRESET_LOW: 16.0, PRESET_HIGH: 26.0},
    "eco": {PRESET_LOW: 17.0, PRESET_HIGH: 25.0},
    "boost": {PRESET_LOW: 20.5, PRESET_HIGH: 21.5},
    "sleep": {PRESET_LOW: 18.0, PRESET_HIGH: 21.0},
}
PEAK_OFF = {PEAK_ENABLED: False, WINDOW_START: "16:00:00", WINDOW_END: "21:00:00"}
# Eco set up to actually swap: checked, auto on, and a window to swap in.
ECO_AUTO = {
    "enabled": True,
    "auto": True,
    WINDOW_START: "16:00:00",
    WINDOW_END: "21:00:00",
}
PRELOAD_OFF = {BOOST_PRELOAD: False, BOOST_MINUTES: 60}
SLEEP_MANUAL = {WINDOW_START: "", WINDOW_END: ""}


async def _setup(
    hass: HomeAssistant,
    *,
    source: ClimateEntity | None = None,
    presets: dict[str, dict[str, Any]] | None = None,
    enabled: list[str] | None = None,
    peak: dict[str, Any] | None = None,
    boost: dict[str, Any] | None = None,
    sleep: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Stand up a source thermostat and a Magic Climate entry wrapping it."""
    setup_test_component_platform(
        hass, "climate", [source or FanThermostat()], built_in=True
    )
    assert await async_setup_component(hass, "climate", {"climate": {"platform": "test"}})
    await hass.async_block_till_done()

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        data={CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY_ID, CONF_NAME: "Bedroom Magic"},
        options={
            CONF_ENABLED_PRESETS: [] if enabled is None else enabled,
            CONF_PRESETS: presets or BANDS,
            CONF_PEAK: peak or PEAK_OFF,
            CONF_BOOST: boost or PRELOAD_OFF,
            CONF_SLEEP: sleep or SLEEP_MANUAL,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _open(hass: HomeAssistant, entry: MockConfigEntry):
    return await hass.config_entries.options.async_init(entry.entry_id)


async def _submit(hass: HomeAssistant, entry: MockConfigEntry, form: dict[str, Any]):
    result = await _open(hass, entry)
    return await hass.config_entries.options.async_configure(result["flow_id"], form)


def _section(result, name: str) -> dict[str, Any]:
    """The fields of one section, keyed by their plain string names."""
    marker = next(k for k in result["data_schema"].schema if str(k) == name)
    sub = result["data_schema"].schema[marker]
    assert isinstance(sub, section)
    return {str(k): (k, v) for k, v in sub.schema.schema.items()}


def _default(result, name: str, field: str):
    marker, _ = _section(result, name)[field]
    return marker.default() if callable(marker.default) else marker.default


def _band_form(low: float, high: float, **extra: Any) -> dict[str, Any]:
    return {PRESET_LOW: low, PRESET_HIGH: high, **extra}


def _form(**overrides: Any) -> dict[str, Any]:
    """A complete, valid submit — override one section to test one thing."""
    base = {
        "comfort": _band_form(20.0, 22.0),
        "away": _band_form(16.0, 26.0),
        "eco": _band_form(17.0, 25.0, enabled=False, auto=False),
        "boost": _band_form(20.5, 21.5, enabled=False, preload=False, minutes=60),
        "sleep": _band_form(18.0, 21.0, enabled=False),
    }
    for key, value in overrides.items():
        base[key] = {**base[key], **value}
    return base


# --- One form --------------------------------------------------------------


async def test_the_flow_is_a_single_step(hass: HomeAssistant) -> None:
    """No menu: every band is visible at once so they can be compared."""
    entry = await _setup(hass)
    result = await _open(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_every_preset_gets_a_section(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await _open(hass, entry)
    assert [str(k) for k in result["data_schema"].schema] == [
        "comfort",
        "away",
        "eco",
        "boost",
        "sleep",
    ]


async def test_sections_render_expanded(hass: HomeAssistant) -> None:
    """Collapsed sections would defeat the point of putting them on one page."""
    entry = await _setup(hass)
    result = await _open(hass, entry)
    for name in ("comfort", "away", "eco", "boost", "sleep"):
        marker = next(k for k in result["data_schema"].schema if str(k) == name)
        assert result["data_schema"].schema[marker].options["collapsed"] is False


async def test_a_disabled_presets_band_is_still_shown(hass: HomeAssistant) -> None:
    """HA renders the form once, so the fields cannot hide behind their own
    checkbox — and keeping them means toggling one off and on is lossless."""
    entry = await _setup(hass, enabled=[])
    result = await _open(hass, entry)
    assert _default(result, "sleep", PRESET_LOW) == 18.0
    assert _default(result, "sleep", "enabled") is False


# --- What the form is seeded with ------------------------------------------


async def test_stored_bands_seed_the_form(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await _open(hass, entry)
    assert _default(result, "comfort", PRESET_LOW) == 20.0
    assert _default(result, "comfort", PRESET_HIGH) == 22.0


async def test_bands_are_shown_in_the_system_unit(hass: HomeAssistant) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = await _setup(hass)
    result = await _open(hass, entry)
    assert _default(result, "comfort", PRESET_LOW) == 68.0


async def test_a_blank_window_suggests_nothing(hass: HomeAssistant) -> None:
    """Blank has to stay blank, or opening the form would schedule Sleep."""
    entry = await _setup(hass, sleep=SLEEP_MANUAL)
    marker, _ = _section(await _open(hass, entry), "sleep")[WINDOW_START]
    assert marker.description is None


async def test_a_set_window_suggests_its_times(hass: HomeAssistant) -> None:
    entry = await _setup(
        hass, sleep={WINDOW_START: "22:30:00", WINDOW_END: "06:00:00"}
    )
    fields = _section(await _open(hass, entry), "sleep")
    assert fields[WINDOW_START][0].description == {"suggested_value": "22:30:00"}
    assert fields[WINDOW_END][0].description == {"suggested_value": "06:00:00"}


async def test_times_are_suggested_not_defaulted(hass: HomeAssistant) -> None:
    """A default would be re-applied on an empty submit, so a configured
    window could never be cleared again."""
    entry = await _setup(
        hass, sleep={WINDOW_START: "22:30:00", WINDOW_END: "06:00:00"}
    )
    marker, _ = _section(await _open(hass, entry), "sleep")[WINDOW_START]
    assert marker.default is vol.UNDEFINED

    await _submit(hass, entry, _form(sleep={"enabled": True}))
    assert entry.options[CONF_SLEEP] == {WINDOW_START: "", WINDOW_END: ""}


# --- Fan dropdowns come from the source ------------------------------------


async def test_fan_options_come_from_the_source(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    _, selector_ = _section(await _open(hass, entry), "comfort")[PRESET_FAN]
    values = [o["value"] for o in selector_.config["options"]]
    assert values == ["", "auto", "low", "high"]


async def test_a_stored_fan_the_source_dropped_is_kept(hass: HomeAssistant) -> None:
    """Firmware losing a speed must not silently rewrite a configured preset."""
    entry = await _setup(
        hass,
        presets={**BANDS, "comfort": {**BANDS["comfort"], PRESET_FAN: "turbo"}},
    )
    _, selector_ = _section(await _open(hass, entry), "comfort")[PRESET_FAN]
    assert "turbo" in [o["value"] for o in selector_.config["options"]]


async def test_a_fanless_source_falls_back_to_text(hass: HomeAssistant) -> None:
    """There is no universal fan vocabulary to offer as a static list."""
    entry = await _setup(hass, source=FanlessThermostat())
    _, selector_ = _section(await _open(hass, entry), "comfort")[PRESET_FAN]
    assert not hasattr(selector_, "config") or "options" not in selector_.config


# --- What a submit stores --------------------------------------------------


async def test_submitting_stores_every_band(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await _submit(hass, entry, _form(comfort={PRESET_LOW: 19.0, PRESET_HIGH: 23.0}))
    assert entry.options[CONF_PRESETS]["comfort"] == {
        PRESET_LOW: 19.0,
        PRESET_HIGH: 23.0,
    }


async def test_a_checkbox_creates_the_preset(hass: HomeAssistant) -> None:
    entry = await _setup(hass, enabled=[])
    await _submit(hass, entry, _form(sleep={"enabled": True}))
    assert entry.options[CONF_ENABLED_PRESETS] == ["sleep"]


async def test_unchecking_keeps_the_band(hass: HomeAssistant) -> None:
    """Turning a preset off and back on restores what was configured."""
    entry = await _setup(hass, enabled=["sleep"])
    await _submit(
        hass, entry, _form(sleep={"enabled": False, PRESET_LOW: 17.0, PRESET_HIGH: 19.0})
    )
    assert entry.options[CONF_ENABLED_PRESETS] == []
    assert entry.options[CONF_PRESETS]["sleep"] == {
        PRESET_LOW: 17.0,
        PRESET_HIGH: 19.0,
    }


async def test_the_peak_window_is_stored_from_the_eco_section(
    hass: HomeAssistant,
) -> None:
    entry = await _setup(hass)
    await _submit(
        hass,
        entry,
        _form(
            eco={
                "enabled": True,
                "auto": True,
                WINDOW_START: "15:00:00",
                WINDOW_END: "20:00:00",
            }
        ),
    )
    assert entry.options[CONF_PEAK] == {
        PEAK_ENABLED: True,
        WINDOW_START: "15:00:00",
        WINDOW_END: "20:00:00",
    }


async def test_blank_times_store_as_blank(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await _submit(hass, entry, _form(sleep={"enabled": True}))
    assert entry.options[CONF_SLEEP] == {WINDOW_START: "", WINDOW_END: ""}


async def test_the_preload_window_is_stored(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await _submit(
        hass,
        entry,
        _form(
            eco=ECO_AUTO,
            boost={"enabled": True, "preload": True, "minutes": 90},
        ),
    )
    assert entry.options[CONF_BOOST] == {BOOST_PRELOAD: True, BOOST_MINUTES: 90}


async def test_a_fan_choice_is_stored(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await _submit(hass, entry, _form(comfort={PRESET_FAN: "high"}))
    assert entry.options[CONF_PRESETS]["comfort"][PRESET_FAN] == "high"


async def test_leave_unchanged_stores_no_fan(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await _submit(hass, entry, _form(comfort={PRESET_FAN: ""}))
    assert PRESET_FAN not in entry.options[CONF_PRESETS]["comfort"]


# --- What the form refuses -------------------------------------------------


async def _error(hass: HomeAssistant, entry: MockConfigEntry, form: dict) -> str:
    result = await _submit(hass, entry, form)
    assert result["type"] is FlowResultType.FORM
    return result["errors"]["base"]


async def test_an_inverted_band_is_refused(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert await _error(
        hass, entry, _form(comfort={PRESET_LOW: 25.0, PRESET_HIGH: 20.0})
    ) == "comfort_low_not_below_high"


async def test_a_disabled_presets_band_is_not_validated(hass: HomeAssistant) -> None:
    """An unchecked preset is not applied, so its numbers cannot hurt anything."""
    entry = await _setup(hass)
    result = await _submit(
        hass, entry, _form(sleep={"enabled": False, PRESET_LOW: 25.0, PRESET_HIGH: 20.0})
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_auto_switching_without_eco_is_refused(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert await _error(
        hass, entry, _form(eco={"enabled": False, "auto": True})
    ) == "auto_needs_eco"


async def test_auto_switching_without_times_is_refused(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert await _error(
        hass,
        entry,
        _form(eco={"enabled": True, "auto": True}),
    ) == "auto_needs_peak_times"


async def test_preloading_without_boost_is_refused(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert await _error(
        hass,
        entry,
        _form(
            eco=ECO_AUTO,
            boost={"enabled": False, "preload": True},
        ),
    ) == "preload_needs_boost"


async def test_preloading_without_a_peak_is_refused(hass: HomeAssistant) -> None:
    """Preload runs up to peak start, so it has nothing to run up to."""
    entry = await _setup(hass)
    assert await _error(
        hass, entry, _form(boost={"enabled": True, "preload": True})
    ) == "preload_needs_peak"


async def test_half_a_sleep_window_is_refused(hass: HomeAssistant) -> None:
    """One time filled in and the other blank is an unfinished thought."""
    entry = await _setup(hass)
    assert await _error(
        hass, entry, _form(sleep={"enabled": True, WINDOW_START: "22:00:00"})
    ) == "sleep_window_incomplete"


async def test_eco_alone_is_accepted(hass: HomeAssistant) -> None:
    """Created but never auto-enabled: the band exists, nothing applies it."""
    entry = await _setup(hass)
    result = await _submit(
        hass, entry, _form(eco={"enabled": True, "auto": False})
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ENABLED_PRESETS] == ["eco"]
    assert entry.options[CONF_PEAK][PEAK_ENABLED] is False
