"""Diagnostics export useful state without names or unrelated private data."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging

import pytest

from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.magic_climate.const import (
    APPLY_GUARD_SECONDS,
    CONF_BOOST,
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SLEEP,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
)
from custom_components.magic_climate.diagnostics import (
    HISTORY_LIMIT,
    DiagnosticsHistory,
    async_get_config_entry_diagnostics,
)

from .test_climate_temperature import MAGIC_ENTITY_ID, SOURCE_ENTITY_ID
from .test_source_rename import _setup


def test_history_is_bounded_timestamped_detached_and_debug_logged(caplog) -> None:
    history = DiagnosticsHistory()
    payload = {"temperature": 72.0}
    with caplog.at_level(logging.DEBUG, logger=f"custom_components.{DOMAIN}.diagnostics"):
        history.record("command_sent", service_data=payload, temperature_unit="°F")
    payload["temperature"] = 99.0

    snapshot = history.snapshot()
    assert snapshot[0]["details"]["service_data"]["temperature"] == 72.0
    assert datetime.fromisoformat(snapshot[0]["timestamp"]).utcoffset() is not None
    assert "command_sent" in caplog.text
    snapshot[0]["details"]["service_data"]["temperature"] = 11.0
    assert history.snapshot()[0]["details"]["service_data"]["temperature"] == 72.0

    for index in range(HISTORY_LIMIT + 2):
        history.record("preset_requested", index=index)
    assert len(history.snapshot()) == HISTORY_LIMIT
    assert history.snapshot()[0]["details"]["index"] == 2
    assert history.snapshot()[-1]["details"]["index"] == HISTORY_LIMIT + 1
    assert DiagnosticsHistory().snapshot() == []


async def test_diagnostics_explicit_schema_preserves_units_and_omits_private_data(hass) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    private = "private-value-not-for-export"
    source_id = "climate.private_source"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=private,
        data={CONF_SOURCE_ENTITY_ID: source_id, "secret": private},
        options={
            CONF_ENABLED_PRESETS: ["eco"],
            CONF_PRESETS: {"eco": {"low": 17.0, "high": 25.0, "fan": "auto", "secret": private}},
            CONF_PEAK: {"enabled": True, "start": "16:00:00", "end": "21:00:00", "secret": private},
            CONF_BOOST: {"preload": True, "minutes": 60, "secret": private},
            CONF_SLEEP: {"start": "23:00:00", "end": "07:00:00", "secret": private},
            "secret": private,
        },
    )
    entry.add_to_hass(hass)
    entry.runtime_data = DiagnosticsHistory()
    entry.runtime_data.record("preset_applied", effective_preset="eco")
    wrapper = er.async_get(hass).async_get_or_create(
        "climate", DOMAIN, "private-unique-id", config_entry=entry
    )
    hass.states.async_set(source_id, "heat_cool", {
        "target_temp_low": 62.0,
        "target_temp_high": 77.0,
        "friendly_name": private,
        "secret": private,
    })
    hass.states.async_set(wrapper.entity_id, "heat_cool", {
        "target_temp_low": 62.0,
        "effective_preset": "eco",
        "preset_mode": "home",
        "friendly_name": private,
        "secret": private,
    })

    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["options"] == {
        "temperature_unit": "°C",
        "enabled_presets": ["eco"],
        "presets": {"eco": {"low": 17.0, "high": 25.0, "fan": "auto"}},
        "peak": {"enabled": True, "start": "16:00:00", "end": "21:00:00"},
        "boost": {"preload": True, "minutes": 60},
        "sleep": {"start": "23:00:00", "end": "07:00:00"},
    }
    assert data["source"] == {
        "state": "heat_cool",
        "temperature_unit": "°F",
        "attributes": {
            "target_temp_low": 62.0,
            "target_temp_high": 77.0,
        },
    }
    assert data["wrapper"]["attributes"]["effective_preset"] == "eco"
    assert data["wrapper"]["temperature_unit"] == "°F"
    assert data["history"][0]["event"] == "preset_applied"
    serialized = json.dumps(data)
    assert private not in serialized
    assert source_id not in serialized
    assert wrapper.entity_id not in serialized
    assert "private-unique-id" not in serialized


async def test_diagnostics_handles_missing_and_unavailable_states(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_SOURCE_ENTITY_ID: "climate.missing"})
    entry.add_to_hass(hass)
    entry.runtime_data = DiagnosticsHistory()
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["source"] is None
    assert data["wrapper"] is None
    assert data["history"] == []

    hass.states.async_set("climate.missing", "unavailable")
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["source"] == {
        "state": "unavailable",
        "temperature_unit": hass.config.units.temperature_unit,
        "attributes": {},
    }


async def test_real_command_and_external_drift_are_explained_in_diagnostics(hass, freezer) -> None:
    _, entry = await _setup(hass, freezer)
    assert isinstance(entry.runtime_data, DiagnosticsHistory)
    await hass.services.async_call(
        "climate", "set_preset_mode",
        {"entity_id": MAGIC_ENTITY_ID, "preset_mode": "home"},
        blocking=True,
    )
    await hass.async_block_till_done()
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["source"]["temperature_unit"] == "°F"
    assert data["wrapper"]["temperature_unit"] == "°F"
    assert data["wrapper"]["attributes"]["preset_mode"] == "home"
    assert data["history"][-1]["event"] == "preset_applied"
    command = next(event for event in data["history"] if event["event"] == "command_sent")
    assert command["details"]["temperature_unit"] == "°F"
    assert "entity_id" not in command["details"]["values"]
    assert command["details"]["values"] == pytest.approx({
        "target_temp_low": 68.0, "target_temp_high": 76.0,
    })

    freezer.tick(delta=timedelta(seconds=APPLY_GUARD_SECONDS + 1))
    await hass.services.async_call(
        "climate", "set_temperature",
        {"entity_id": SOURCE_ENTITY_ID, "target_temp_low": 68.0, "target_temp_high": 80.0},
        blocking=True,
    )
    await hass.async_block_till_done()

    data = await async_get_config_entry_diagnostics(hass, entry)
    cleared = data["history"][-1]
    assert cleared["event"] == "preset_cleared"
    assert cleared["details"]["reason"] == "source_changed"
    assert cleared["details"]["temperature_unit"] == "°F"
    assert cleared["details"]["differences"] == {
        "target_temp_high": {"expected": pytest.approx(76.0), "actual": 80.0},
    }
    assert data["wrapper"]["attributes"]["preset_mode"] is None
    assert data["wrapper"]["attributes"]["effective_preset"] is None
    command = next(event for event in data["history"] if event["event"] == "command_sent")
    assert command["details"]["values"]["target_temp_high"] == pytest.approx(76.0)
