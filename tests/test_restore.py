"""preset_mode survives a Home Assistant restart.

It used to live only in memory: a restart silently dropped it, so the wrapper
forgot it was holding Home and nothing re-asserted. Peak made that visible —
restart mid-window and Home kept applying the Home band with no boundary left
to correct it.

A restart is simulated the way HA's own tests do it: seed the restore cache
with the state the entity last published, then set the entry up fresh.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, CONF_NAME
from homeassistant.core import HomeAssistant, State
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
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
from .test_peak_substitution import (
    DEFAULT_PRESETS,
    IN_PEAK,
    OFF_PEAK,
    PEAK_OFF,
    PEAK_ON,
)


async def _restart(
    hass: HomeAssistant,
    freezer,
    *,
    now: datetime,
    restored: dict[str, Any] | None,
    source_band: tuple[float, float],
    peak: dict[str, Any] = PEAK_ON,
) -> FakeThermostat:
    """Bring the wrapper up with `restored` in the restore cache.

    `source_band` is what the source is holding at boot — i.e. what a previous
    run left on the hardware, in °F.
    """
    hass.config.units = US_CUSTOMARY_SYSTEM
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(now)

    if restored is not None:
        mock_restore_cache_with_extra_data(
            hass, ((State(MAGIC_ENTITY_ID, "cool", restored), {}),)
        )

    source = FakeThermostat()
    source._attr_target_temperature_low = source_band[0]
    source._attr_target_temperature_high = source_band[1]
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
            CONF_ENABLED_PRESETS: ["home", "eco", "sleep"],
            CONF_PRESETS: DEFAULT_PRESETS,
            CONF_PEAK: peak,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return source


def _preset(hass: HomeAssistant) -> str | None:
    return hass.states.get(MAGIC_ENTITY_ID).attributes.get("preset_mode")


def _pushed(source: FakeThermostat) -> tuple[float, float]:
    return (
        round(source._attr_target_temperature_low, 1),
        round(source._attr_target_temperature_high, 1),
    )


HOME_HELD = {"preset_mode": "home", "effective_preset": "home"}
HOME_HELD_AS_ECO = {"preset_mode": "home", "effective_preset": "eco"}


# --- The basic restore -----------------------------------------------------


async def test_preset_survives_a_restart(hass, freezer) -> None:
    await _restart(
        hass, freezer, now=OFF_PEAK, restored=HOME_HELD, source_band=(68.0, 76.0)
    )
    assert _preset(hass) == "home"


async def test_nothing_is_restored_without_a_previous_state(hass, freezer) -> None:
    await _restart(
        hass, freezer, now=OFF_PEAK, restored=None, source_band=(68.0, 76.0)
    )
    assert _preset(hass) is None


async def test_restore_does_not_push(hass, freezer) -> None:
    """A restart is not a reason to command the hardware."""
    source = await _restart(
        hass, freezer, now=OFF_PEAK, restored=HOME_HELD, source_band=(68.0, 76.0)
    )
    assert _pushed(source) == (68.0, 76.0)


async def test_a_preset_no_longer_enabled_is_not_restored(hass, freezer) -> None:
    await _restart(
        hass,
        freezer,
        now=OFF_PEAK,
        restored={"preset_mode": "boost", "effective_preset": "boost"},
        source_band=(68.0, 76.0),
    )
    assert _preset(hass) is None


# --- Restoring keeps drift detection honest --------------------------------


async def test_a_setpoint_moved_while_down_clears_the_restored_preset(
    hass, freezer
) -> None:
    """The label must not outlive the state it describes: if something moved
    the setpoint while HA was down, the preset is gone."""
    source = await _restart(
        hass, freezer, now=OFF_PEAK, restored=HOME_HELD, source_band=(68.0, 76.0)
    )
    await hass.services.async_call(
        "climate", "set_temperature",
        {ATTR_ENTITY_ID: SOURCE_ENTITY_ID,
         "target_temp_low": 63.0, "target_temp_high": 84.0},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert _preset(hass) is None
    assert _pushed(source) == (63.0, 84.0)


async def test_the_restored_preset_survives_an_echo_of_its_own_band(
    hass, freezer
) -> None:
    """A source re-publishing the values it already holds is not drift."""
    source = await _restart(
        hass, freezer, now=OFF_PEAK, restored=HOME_HELD, source_band=(68.0, 76.0)
    )
    source.async_write_ha_state()
    await hass.async_block_till_done()

    assert _preset(hass) == "home"


# --- Boundaries slept through ----------------------------------------------


async def test_a_peak_boundary_crossed_while_down_is_re_applied(
    hass, freezer
) -> None:
    """HA was holding Home off-peak and came back up inside the window. No
    boundary is left to fire, so startup has to reconcile it."""
    source = await _restart(
        hass, freezer, now=IN_PEAK, restored=HOME_HELD, source_band=(68.0, 76.0)
    )
    assert _pushed(source) == (62.0, 82.0)
    assert _preset(hass) == "home"


async def test_peak_ending_while_down_restores_the_home_band(hass, freezer) -> None:
    source = await _restart(
        hass,
        freezer,
        now=datetime(2026, 8, 28, 22, 0),
        restored=HOME_HELD_AS_ECO,
        source_band=(62.0, 82.0),
    )
    assert _pushed(source) == (68.0, 76.0)
    assert _preset(hass) == "home"


async def test_no_re_apply_when_the_window_did_not_move(hass, freezer) -> None:
    """Restarting inside the window it was already inside changes nothing."""
    source = await _restart(
        hass, freezer, now=IN_PEAK, restored=HOME_HELD_AS_ECO, source_band=(62.0, 82.0)
    )
    assert _pushed(source) == (62.0, 82.0)


async def test_no_re_apply_for_a_preset_peak_does_not_touch(hass, freezer) -> None:
    source = await _restart(
        hass,
        freezer,
        now=IN_PEAK,
        restored={"preset_mode": "sleep", "effective_preset": "sleep"},
        source_band=(64.0, 72.0),
    )
    assert _pushed(source) == (64.0, 72.0)
    assert _preset(hass) == "sleep"


async def test_restore_with_peak_disabled_never_re_applies(hass, freezer) -> None:
    source = await _restart(
        hass,
        freezer,
        now=IN_PEAK,
        restored=HOME_HELD,
        source_band=(68.0, 76.0),
        peak=PEAK_OFF,
    )
    assert _pushed(source) == (68.0, 76.0)
    assert _preset(hass) == "home"


# --- The boundary still works after a restore ------------------------------


async def test_a_restored_preset_still_tracks_the_next_boundary(
    hass, freezer
) -> None:
    """Restoring must hand the preset back to the live boundary logic, not
    leave it inert until someone reselects it."""
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    source = await _restart(
        hass,
        freezer,
        now=datetime(2026, 8, 28, 15, 58),
        restored=HOME_HELD,
        source_band=(68.0, 76.0),
    )
    assert _pushed(source) == (68.0, 76.0)

    boundary = datetime(2026, 8, 28, 16, 0)
    freezer.move_to(boundary)
    async_fire_time_changed(hass, boundary)
    await hass.async_block_till_done()

    assert _pushed(source) == (62.0, 82.0)
    assert _preset(hass) == "home"
