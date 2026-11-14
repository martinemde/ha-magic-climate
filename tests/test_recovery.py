"""Missed schedule transitions and failed one-shot preset applications."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.const import ATTR_TEMPERATURE, CONF_NAME, STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
    setup_test_component_platform,
)

from custom_components.magic_climate.const import (
    CONF_BOOST,
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SLEEP,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
    PRESET_FAN,
)

from custom_components.magic_climate.diagnostics import async_get_config_entry_diagnostics

from .test_climate_temperature import MAGIC_ENTITY_ID, SOURCE_ENTITY_ID, FakeThermostat
from .test_restore import HOME_HELD, _restart
from .test_schedule_apply import (
    BANDS,
    MIDDAY,
    PEAK_END_AT,
    PEAK_ON,
    PEAK_START_AT,
    PRELOAD_OFF,
    PRELOAD_ON,
    PRELOAD_START_AT,
    SLEEP_MANUAL,
    _attrs,
    _cross_boundary,
    _preset,
    _pushed,
    _select,
)


BEFORE_PEAK = datetime(2026, 8, 28, 15, 50)


class RecoveryThermostat(FakeThermostat):
    """External hardware boundary with observable commands and a failing fan."""

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE | ClimateEntityFeature.FAN_MODE
    )
    _attr_fan_modes = ["auto", "high"]

    def __init__(self) -> None:
        super().__init__()
        self._attr_fan_mode = "auto"
        self.temperature_commands: list[dict[str, Any]] = []
        self.fan_commands: list[str] = []
        self.fail_fan = False
        self.disconnect_after_temperature = False
        self.error_class: type[Exception] = HomeAssistantError

    async def async_set_temperature(self, **kwargs: Any) -> None:
        self.temperature_commands.append(dict(kwargs))
        await super().async_set_temperature(**kwargs)
        if self.disconnect_after_temperature:
            self._attr_available = False
            self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self.fan_commands.append(fan_mode)
        if self.fail_fan:
            raise self.error_class("Source fan command failed")
        self._attr_fan_mode = fan_mode
        self.async_write_ha_state()


async def _setup(
    hass, freezer, *, now=MIDDAY, enabled=None, boost=None,
    available=True, restored=None, source_band=None,
) -> RecoveryThermostat:
    hass.config.units = US_CUSTOMARY_SYSTEM
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(now)
    if restored is not None:
        mock_restore_cache_with_extra_data(
            hass, ((State(MAGIC_ENTITY_ID, "cool", restored), {}),)
        )
    source = RecoveryThermostat()
    source._attr_available = available
    if source_band is not None:
        source._attr_target_temperature_low, source._attr_target_temperature_high = source_band
    setup_test_component_platform(hass, "climate", [source], built_in=True)
    assert await async_setup_component(hass, "climate", {"climate": {"platform": "test"}})
    await hass.async_block_till_done()
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        data={CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY_ID, CONF_NAME: "Bedroom Magic"},
        options={
            CONF_ENABLED_PRESETS: ["eco"] if enabled is None else enabled,
            CONF_PRESETS: BANDS,
            CONF_PEAK: PEAK_ON,
            CONF_BOOST: PRELOAD_OFF if boost is None else boost,
            CONF_SLEEP: SLEEP_MANUAL,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return source


async def _set_available(hass, source, available: bool) -> None:
    source._attr_available = available
    source.async_write_ha_state()
    await hass.async_block_till_done()


async def test_missed_boundary_applies_once_on_reconnect(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=BEFORE_PEAK)
    await _select(hass, "home")
    source.temperature_commands.clear()
    await _set_available(hass, source, False)

    await _cross_boundary(hass, freezer, PEAK_START_AT)
    assert _pushed(source) == (68.0, 76.0)
    assert hass.states.get(MAGIC_ENTITY_ID).state == STATE_UNAVAILABLE
    assert source.temperature_commands == []

    await _set_available(hass, source, True)
    assert _pushed(source) == (62.0, 82.0)
    assert _preset(hass) == "home"
    assert _attrs(hass)["effective_preset"] == "eco"
    assert len(source.temperature_commands) == 1

    source.async_write_ha_state()
    await hass.async_block_till_done()
    await _cross_boundary(hass, freezer, PEAK_START_AT + timedelta(minutes=1))
    assert len(source.temperature_commands) == 1


async def test_reconnect_without_boundary_does_not_write(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=MIDDAY)
    await _select(hass, "home")
    source.temperature_commands.clear()
    await _set_available(hass, source, False)
    await _cross_boundary(hass, freezer, MIDDAY + timedelta(minutes=1))
    await _set_available(hass, source, True)
    assert _preset(hass) == "home"
    assert source.temperature_commands == []


async def test_reconnect_preserves_manual_change_during_outage(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=BEFORE_PEAK)
    await _select(hass, "home")
    source.temperature_commands.clear()
    await _set_available(hass, source, False)
    await _cross_boundary(hass, freezer, PEAK_START_AT)

    source._attr_target_temperature_high = 80.0
    await _set_available(hass, source, True)
    assert _pushed(source) == (68.0, 80.0)
    assert _preset(hass) is None
    assert _attrs(hass)["effective_preset"] is None
    await _cross_boundary(hass, freezer, PEAK_END_AT)
    assert source.temperature_commands == []


async def test_multiple_missed_boundaries_apply_only_current_band(hass, freezer) -> None:
    source = await _setup(
        hass, freezer, now=datetime(2026, 8, 28, 14, 50),
        enabled=["eco", "boost"], boost=PRELOAD_ON,
    )
    await _select(hass, "home")
    source.temperature_commands.clear()
    await _set_available(hass, source, False)
    await _cross_boundary(hass, freezer, PRELOAD_START_AT)
    await _cross_boundary(hass, freezer, PEAK_START_AT)
    await _set_available(hass, source, True)

    assert len(source.temperature_commands) == 1
    assert _pushed(source) == (62.0, 82.0)
    assert _attrs(hass)["effective_preset"] == "eco"


async def test_return_to_applied_band_needs_no_recovery_write(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=BEFORE_PEAK)
    await _select(hass, "home")
    source.temperature_commands.clear()
    await _set_available(hass, source, False)
    await _cross_boundary(hass, freezer, PEAK_START_AT)
    await _cross_boundary(hass, freezer, PEAK_END_AT)
    await _set_available(hass, source, True)

    assert source.temperature_commands == []
    assert _preset(hass) == "home"
    assert _pushed(source) == (68.0, 76.0)
    assert _attrs(hass)["effective_preset"] == "comfort"


async def test_options_edit_cancels_deferred_apply_without_writing(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=BEFORE_PEAK)
    await _select(hass, "home")
    source.temperature_commands.clear()
    await _set_available(hass, source, False)
    await _cross_boundary(hass, freezer, PEAK_START_AT)

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_PRESETS: {**BANDS, "eco": BANDS["away"]}},
    )
    await hass.async_block_till_done()
    await _set_available(hass, source, True)

    assert source.temperature_commands == []
    assert _pushed(source) == (68.0, 76.0)


async def _configure_failing_fan(hass, source, preset: str) -> None:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    presets = {**BANDS, preset: {**BANDS[preset], PRESET_FAN: "high"}}
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_PRESETS: presets})
    await hass.async_block_till_done()
    source.fail_fan = True


@pytest.mark.parametrize("error_class", [HomeAssistantError, ValueError, RuntimeError])
async def test_partial_user_apply_clears_preset_without_rollback(
    hass, freezer, error_class
) -> None:
    source = await _setup(hass, freezer)
    await _select(hass, "home")
    await _configure_failing_fan(hass, source, "away")
    source.error_class = error_class
    with pytest.raises(error_class, match="Source fan command failed"):
        await _select(hass, "away")
    await hass.async_block_till_done()

    assert _pushed(source) == (61.0, 88.0)
    assert source.fan_mode == "auto"
    assert _preset(hass) is None
    assert _attrs(hass)["effective_preset"] is None
    assert source.fan_commands == ["high"]


async def test_partial_schedule_apply_does_not_retry(hass, freezer) -> None:
    source = await _setup(hass, freezer, now=BEFORE_PEAK)
    await _select(hass, "home")
    await _configure_failing_fan(hass, source, "eco")
    await _cross_boundary(hass, freezer, PEAK_START_AT)

    assert _pushed(source) == (62.0, 82.0)
    assert _preset(hass) is None
    assert _attrs(hass)["effective_preset"] is None
    await _cross_boundary(hass, freezer, PEAK_START_AT + timedelta(minutes=1))
    await _set_available(hass, source, False)
    await _set_available(hass, source, True)
    assert source.fan_commands == ["high"]


@pytest.mark.parametrize("manual_change", [False, True])
async def test_restart_across_boundary_waits_for_source_and_respects_manual_change(
    hass, freezer, manual_change
) -> None:
    source = await _setup(
        hass, freezer, now=PEAK_START_AT,
        restored={"preset_mode": "home", "effective_preset": "comfort"},
        available=False, source_band=(68.0, 80.0 if manual_change else 76.0),
    )
    assert source.temperature_commands == []
    assert hass.states.get(MAGIC_ENTITY_ID).state == STATE_UNAVAILABLE

    await _set_available(hass, source, True)
    if manual_change:
        assert source.temperature_commands == []
        assert _preset(hass) is None
        assert _pushed(source) == (68.0, 80.0)
    else:
        assert len(source.temperature_commands) == 1
        assert _preset(hass) == "home"
        assert _attrs(hass)["effective_preset"] == "eco"
        assert _pushed(source) == (62.0, 82.0)


async def test_disconnect_between_commands_releases_preset_without_claiming_success(
    hass, freezer
) -> None:
    source = await _setup(hass, freezer)
    await _select(hass, "home")
    await _configure_failing_fan(hass, source, "away")
    source.fail_fan = False
    source.disconnect_after_temperature = True

    with pytest.raises(HomeAssistantError):
        await _select(hass, "away")
    await hass.async_block_till_done()
    assert _pushed(source) == (61.0, 88.0)
    assert source.fan_commands == []

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    data = await async_get_config_entry_diagnostics(hass, entry)
    history = data["history"]
    assert not any(
        event["event"] == "preset_applied" and event["details"]["preset"] == "away"
        for event in history
    )
    failed = next(event for event in history if event["event"] == "command_failed")
    assert failed["details"]["service"] == "set_fan_mode"
    assert failed["details"]["error"] == "HomeAssistantError"
    released = next(event for event in history if event["event"] == "preset_cleared")
    assert released["details"]["reason"] == "apply_failed"
    assert released["details"]["requested_preset"] == "away"

    await _set_available(hass, source, True)
    assert _preset(hass) is None
    assert _attrs(hass)["effective_preset"] is None
    assert source.fan_commands == []


async def test_restart_boundary_refreshes_wrapper_target_from_source_echo(hass, freezer) -> None:
    source = await _restart(
        hass, freezer, now=PEAK_START_AT,
        restored=HOME_HELD, source_band=(68.0, 76.0),
    )
    assert _pushed(source) == (62.0, 82.0)
    assert _preset(hass) == "home"
    assert _attrs(hass)["effective_preset"] == "eco"
    assert _attrs(hass)[ATTR_TEMPERATURE] == pytest.approx(source.target_temperature_high)
