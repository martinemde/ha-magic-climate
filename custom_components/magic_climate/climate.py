"""Magic Climate wrapper entity."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    PRECISION_TENTHS,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

from .const import (
    APPLY_GUARD_SECONDS,
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
    DRIFT_TOLERANCE,
    PEAK_ENABLED,
    PEAK_SUBSTITUTE_FOR,
    PEAK_SUBSTITUTE_WITH,
    STANDARD_PRESETS,
)
from .peak import PeakValidationError, PeakWindow, resolve
from .presets import Preset, PresetValidationError, compute_service_data

_LOGGER = logging.getLogger(__name__)


def _load_presets(entry: ConfigEntry) -> list[Preset]:
    """Build the Preset list from entry options, filtering to enabled+valid."""
    options = entry.options or {}
    enabled = set(options.get(CONF_ENABLED_PRESETS, []) or [])
    stored = options.get(CONF_PRESETS, {}) or {}
    presets: list[Preset] = []
    # STANDARD_PRESETS preserves UI ordering.
    for pid in STANDARD_PRESETS:
        if pid not in enabled:
            continue
        raw = stored.get(pid)
        if not raw:
            continue
        try:
            preset = Preset.from_dict({"name": pid, **raw})
            preset.validate()
        except (PresetValidationError, KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Skipping invalid preset %r: %s", pid, err)
            continue
        presets.append(preset)
    return presets


def _load_peak(entry: ConfigEntry) -> PeakWindow | None:
    """The entry's peak window, or None when peak substitution is off.

    An unparseable or degenerate window is dropped rather than raised: the
    wrapper must keep working as an ordinary thermostat if the stored peak
    config is bad.
    """
    raw = (entry.options or {}).get(CONF_PEAK) or {}
    if not raw.get(PEAK_ENABLED):
        return None
    try:
        window = PeakWindow.from_dict(raw)
        window.validate()
    except (PeakValidationError, KeyError, TypeError, ValueError) as err:
        _LOGGER.warning("Ignoring invalid peak window %r: %s", raw, err)
        return None
    return window


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Magic Climate platform from a config entry."""
    source_id: str = entry.data[CONF_SOURCE_ENTITY_ID]
    name: str | None = entry.data.get(CONF_NAME)
    async_add_entities(
        [MagicClimate(hass, entry, source_id, name)],
        update_before_add=True,
    )


class MagicClimate(ClimateEntity, RestoreEntity):
    """Wraps a source climate.* entity and adds UI-configured presets."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        source_entity_id: str,
        name: str | None,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._entry = entry
        self._source_entity_id = source_entity_id
        self._attr_name = name or source_entity_id
        self._attr_unique_id = f"{DOMAIN}::{entry.entry_id}"
        self._source_state = None
        self._presets: list[Preset] = _load_presets(entry)
        self._peak: PeakWindow | None = _load_peak(entry)
        # Latched so the minute tick can act on the *transition* rather than
        # re-pushing every minute the window is open.
        self._peak_active: bool = self._in_peak()
        self._attr_preset_mode: str | None = None
        # Tracks the state the wrapper just pushed; used to suppress drift
        # detection while the source confirms each setting.
        self._pending_apply: dict[str, Any] | None = None
        self._pending_apply_deadline: float = 0.0

    async def async_added_to_hass(self) -> None:
        self._source_state = self.hass.states.get(self._source_entity_id)
        await self._async_restore_preset()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], self._handle_source_change
            )
        )
        self.async_on_remove(
            self._entry.add_update_listener(self._handle_entry_update)
        )
        # A minute tick rather than two re-armed point-in-time callbacks:
        # it re-derives the window from the clock every time, so it heals
        # itself across restarts, DST changes, and options edits instead of
        # depending on a timer that was armed under the old configuration.
        self._peak_active = self._in_peak()
        self.async_on_remove(
            async_track_time_change(self.hass, self._handle_minute_tick, second=0)
        )

    async def _async_restore_preset(self) -> None:
        """Pick the held preset back up after a restart.

        preset_mode used to live only in memory, so a restart dropped it and
        nothing re-asserted. Peak made that matter: a restart during the
        window left Home applying the Home band, with no boundary left to
        correct it.

        Restoring the label on its own would misreport — the source may have
        been moved while HA was down. So the push that preset implies is
        reconstructed and handed to the normal drift detector, which decides
        on the first source event exactly as it would have without the
        restart. The one case drift cannot judge is a boundary crossed while
        HA was down, because the source faithfully holds what the previous
        run pushed; that is re-applied here.
        """
        last = await self.async_get_last_state()
        if last is None:
            return
        restored = last.attributes.get("preset_mode")
        if not restored or self._preset_by_name(restored) is None:
            return

        preset = self._effective_preset(restored)
        if preset is None:
            return

        self._attr_preset_mode = restored
        # No guard window: this run pushed nothing, so the very next source
        # event should be judged on its merits.
        self._pending_apply = {
            "mode": preset.mode,
            "temp_kwargs": self._planned_push(preset, self._effective_mode_for(preset)),
            "fan": preset.fan,
        }
        self._pending_apply_deadline = 0.0

        # A peak boundary crossed while HA was down leaves the source holding
        # the *other* band. Drift would read that as a manual override and
        # silently drop the preset, so reconcile it here instead.
        if last.attributes.get("effective_preset") not in (None, preset.name):
            _LOGGER.debug(
                "Peak boundary crossed while down; re-applying %r on %s",
                restored,
                self._source_entity_id,
            )
            await self._async_apply_preset(restored)

    async def _handle_entry_update(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Refresh presets in place when options change.

        Avoids a full integration reload — recreating the entity races
        against the OptionsFlow being open, and HA's frontend sometimes
        misses the resulting state push so newly enabled presets never
        appear in the picker. Presets and the peak window are the only
        things options can change; the source entity is fixed at entry
        creation, so the state subscription never needs rebuilding.

        Reloading does not re-push. Editing a band while holding its preset
        takes effect the next time the preset is applied, which keeps the
        one-shot contract: options edits never command the hardware.
        """
        self._presets = _load_presets(entry)
        self._peak = _load_peak(entry)
        self._peak_active = self._in_peak()
        if self._attr_preset_mode and self._attr_preset_mode not in {
            p.name for p in self._presets
        }:
            self._attr_preset_mode = None
            self._pending_apply = None
        self.async_write_ha_state()

    @callback
    def _handle_source_change(self, event: Event) -> None:
        self._source_state = event.data.get("new_state")

        if self._attr_preset_mode is not None:
            if self._still_within_guard_window():
                # Wrapper-initiated change; do not clear preset_mode.
                pass
            elif self._has_drifted():
                self._attr_preset_mode = None
                self._pending_apply = None

        self.async_write_ha_state()

    def _still_within_guard_window(self) -> bool:
        return (
            self._pending_apply is not None
            and time.monotonic() < self._pending_apply_deadline
        )

    def _has_drifted(self) -> bool:
        """True if the source's reported state no longer matches what we pushed."""
        if self._pending_apply is None or self._source_state is None:
            return False

        expected = self._pending_apply
        attrs = self._source_state.attributes

        # Mode check
        if expected["mode"] is not None and self._source_state.state != expected["mode"]:
            return True

        # Temperature check
        for key, expected_val in expected["temp_kwargs"].items():
            actual = attrs.get(key)
            if actual is None:
                continue  # source doesn't expose this attribute in this mode
            if abs(actual - expected_val) > DRIFT_TOLERANCE:
                return True

        # Fan check
        if expected["fan"] is not None and attrs.get("fan_mode") != expected["fan"]:
            return True

        return False

    # ------------------------------------------------------------ peak window

    def _in_peak(self) -> bool:
        """Whether the peak window is open right now, by the wall clock."""
        return self._peak is not None and self._peak.contains(dt_util.now())

    def _substitute_name(self) -> str | None:
        """The preset that stands in during peak, if it is available.

        `self._presets` holds only enabled, valid presets, so this returns
        None whenever Eco is switched off — which is the nesting the options
        screen implies: no Eco, nothing to substitute.
        """
        if self._peak is None:
            return None
        if any(p.name == PEAK_SUBSTITUTE_WITH for p in self._presets):
            return PEAK_SUBSTITUTE_WITH
        return None

    def _preset_by_name(self, name: str) -> Preset | None:
        return next((p for p in self._presets if p.name == name), None)

    def _effective_preset(self, requested: str) -> Preset | None:
        """The preset whose band actually gets pushed for `requested`.

        During peak this is Eco in place of Home. The *reported* preset stays
        whatever was requested — see async_set_preset_mode.
        """
        target = resolve(
            requested,
            PEAK_SUBSTITUTE_FOR,
            self._substitute_name(),
            self._in_peak(),
        )
        return self._preset_by_name(target) or self._preset_by_name(requested)

    async def _handle_minute_tick(self, now: datetime) -> None:
        """Re-apply the Home preset when the peak window opens or closes.

        This is the one place the wrapper writes without being asked, so it
        is deliberately narrow: it fires only on a boundary crossing, only
        while Home is still the held preset, and only when a substitute
        exists. A manual touch or a wall thermostat will already have
        cleared preset_mode via drift detection, so this can never overwrite
        anything but the wrapper's own earlier push.
        """
        in_peak = self._in_peak()
        if in_peak == self._peak_active:
            return
        self._peak_active = in_peak

        if (
            self._attr_preset_mode == PEAK_SUBSTITUTE_FOR
            and self._substitute_name() is not None
            and self.available
        ):
            _LOGGER.debug(
                "Peak %s; re-applying %r on %s",
                "started" if in_peak else "ended",
                PEAK_SUBSTITUTE_FOR,
                self._source_entity_id,
            )
            await self._async_apply_preset(PEAK_SUBSTITUTE_FOR)
        else:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Peak state, exposed as clock times rather than only a flag.

        A consumer that wants to pre-cool needs to know *when* peak starts,
        not just whether it is running, so the boundaries are published as
        absolute next-occurrence timestamps.
        """
        if self._peak is None:
            return {"peak_active": False}
        now = dt_util.now()
        effective = None
        if self._attr_preset_mode is not None:
            preset = self._effective_preset(self._attr_preset_mode)
            effective = preset.name if preset else None
        window = self._peak.to_dict()
        return {
            "peak_active": self._in_peak(),
            "peak_start": window["start"],
            "peak_end": window["end"],
            "next_peak_start": self._peak.next_start(now).isoformat(),
            "next_peak_end": self._peak.next_end(now).isoformat(),
            "effective_preset": effective,
        }

    @property
    def available(self) -> bool:
        s = self._source_state
        return s is not None and s.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    @property
    def temperature_unit(self) -> str:
        # Always report °C. Source temperatures are normalized internally
        # (see _normalize_temp / _denormalize_temp) so HA does not apply a
        # second Fahrenheit conversion on top of the source's values.
        return UnitOfTemperature.CELSIUS

    @property
    def precision(self) -> float:
        """Round as finely as HA allows, instead of by the system's unit.

        HA's default precision comes from the *system* unit — whole degrees
        on a Fahrenheit install (see ClimateEntity.precision). That default
        is meant for an entity reporting its own hardware readings. This
        wrapper instead reads values the source entity has already rounded
        to the source's own precision, so a second, coarser rounding here
        can only destroy resolution the source published. Tenths is HA's
        finest, which passes the source's value through unchanged.

        Note this governs the setpoints as well as current_temperature —
        HA has a single precision knob for both. It does not add precision
        the source didn't have; a whole-degree setpoint stays whole.
        """
        return PRECISION_TENTHS

    @property
    def target_temperature_step(self) -> float | None:
        """Mirror the source's step so the UI nudges by what it accepts.

        ATTR_TARGET_TEMP_STEP is published raw, in the reporting entity's
        own unit rather than the system unit, and both the source and this
        wrapper report Celsius — so this passes through unconverted.
        """
        if self._source_state:
            return self._source_state.attributes.get("target_temp_step")
        return None

    @property
    def _source_unit(self) -> str:
        return self.hass.config.units.temperature_unit

    def _normalize_temp(self, val: float | None) -> float | None:
        if val is None:
            return None
        if self._source_unit == UnitOfTemperature.FAHRENHEIT:
            return (val - 32.0) * 5.0 / 9.0
        return val

    def _denormalize_temp(self, val: float | None) -> float | None:
        if val is None:
            return None
        if self._source_unit == UnitOfTemperature.FAHRENHEIT:
            return val * 9.0 / 5.0 + 32.0
        return val

    @property
    def current_temperature(self) -> float | None:
        if not self._source_state:
            return None
        return self._normalize_temp(self._source_state.attributes.get("current_temperature"))

    @property
    def target_temperature(self) -> float | None:
        if not self._source_state:
            return None
        attrs = self._source_state.attributes
        val = attrs.get("temperature")
        if val is not None:
            return self._normalize_temp(val)
        # Source uses range setpoints internally (e.g. Mitsubishi CN105 in
        # two-point mode). Pick the side that actually drives the heat pump
        # in the current mode, so the wrapper UI matches what's applied.
        low = attrs.get("target_temp_low")
        high = attrs.get("target_temp_high")
        mode = self.hvac_mode
        if mode == HVACMode.HEAT and low is not None:
            return self._normalize_temp(low)
        if mode in (HVACMode.COOL, HVACMode.DRY) and high is not None:
            return self._normalize_temp(high)
        if low is not None and high is not None:
            return self._normalize_temp((low + high) / 2.0)
        return self._normalize_temp(low if low is not None else high)

    @property
    def target_temperature_low(self) -> float | None:
        if self._source_state:
            return self._normalize_temp(self._source_state.attributes.get("target_temp_low"))
        return None

    @property
    def target_temperature_high(self) -> float | None:
        if self._source_state:
            return self._normalize_temp(self._source_state.attributes.get("target_temp_high"))
        return None

    @property
    def min_temp(self) -> float:
        if self._source_state:
            val = self._source_state.attributes.get("min_temp")
            if val is not None:
                return self._normalize_temp(val)
        return 7.0

    @property
    def max_temp(self) -> float:
        if self._source_state:
            val = self._source_state.attributes.get("max_temp")
            if val is not None:
                return self._normalize_temp(val)
        return 35.0

    @property
    def hvac_mode(self) -> HVACMode | None:
        if not self._source_state:
            return None
        try:
            return HVACMode(self._source_state.state)
        except ValueError:
            return None

    @property
    def hvac_modes(self) -> list[HVACMode]:
        if not self._source_state:
            return []
        out: list[HVACMode] = []
        for raw in self._source_state.attributes.get("hvac_modes", []) or []:
            try:
                out.append(HVACMode(raw))
            except ValueError:
                _LOGGER.debug("Unknown hvac_mode from source: %r", raw)
        return out

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self._source_state:
            return None
        raw = self._source_state.attributes.get("hvac_action")
        if raw is None:
            return None
        try:
            return HVACAction(raw)
        except ValueError:
            return None

    @property
    def fan_mode(self) -> str | None:
        if self._source_state:
            return self._source_state.attributes.get("fan_mode")
        return None

    @property
    def fan_modes(self) -> list[str] | None:
        if self._source_state:
            return self._source_state.attributes.get("fan_modes")
        return None

    @property
    def swing_mode(self) -> str | None:
        if self._source_state:
            return self._source_state.attributes.get("swing_mode")
        return None

    @property
    def swing_modes(self) -> list[str] | None:
        if self._source_state:
            return self._source_state.attributes.get("swing_modes")
        return None

    @property
    def preset_modes(self) -> list[str] | None:
        if not self._presets:
            return None
        return [p.name for p in self._presets]

    @property
    def preset_mode(self) -> str | None:
        return self._attr_preset_mode

    @property
    def supported_features(self) -> ClimateEntityFeature:
        if not self._source_state:
            return ClimateEntityFeature(0)

        source_features = self._source_state.attributes.get("supported_features", 0)

        # Drop both temperature flags and decide fresh.
        features = source_features & ~ClimateEntityFeature.TARGET_TEMPERATURE
        features = features & ~ClimateEntityFeature.TARGET_TEMPERATURE_RANGE

        source_supports_dual = bool(
            source_features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        ) and HVACMode.HEAT_COOL in self.hvac_modes

        if source_supports_dual and (
            self.hvac_mode == HVACMode.HEAT_COOL
            or (self.hvac_mode == HVACMode.OFF and HVACMode.HEAT_COOL in self.hvac_modes)
        ):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE

        if self._presets:
            features |= ClimateEntityFeature.PRESET_MODE

        return ClimateEntityFeature(features)

    def _adapt_temp_kwargs_to_source(
        self, temp_kwargs: dict[str, Any], preset: Preset
    ) -> dict[str, Any]:
        """Rewrite set_temperature kwargs so they match the source's flags.

        Some climate entities (notably range-only HVACs) advertise only
        TARGET_TEMPERATURE_RANGE even in single-setpoint modes like heat or
        cool, and reject `temperature`. Others do the opposite. Adapt rather
        than fail the service call.
        """
        if not temp_kwargs or not self._source_state:
            return temp_kwargs

        source_features = self._source_state.attributes.get("supported_features", 0)
        supports_single = bool(source_features & ClimateEntityFeature.TARGET_TEMPERATURE)
        supports_range = bool(source_features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE)
        wants_single = "temperature" in temp_kwargs
        wants_range = "target_temp_low" in temp_kwargs or "target_temp_high" in temp_kwargs

        if wants_single and not supports_single and supports_range:
            # Range-only sources (e.g. Mitsubishi CN105 in two-point mode)
            # already pick the appropriate side per HVAC mode: low for HEAT,
            # high for COOL/DRY, median for AUTO. Send the full preset band
            # and let the source select.
            return {"target_temp_low": preset.low, "target_temp_high": preset.high}
        if wants_range and not supports_range and supports_single:
            return {"temperature": (preset.low + preset.high) / 2.0}
        return temp_kwargs

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.hass.services.async_call(
            "climate", "set_hvac_mode",
            {"entity_id": self._source_entity_id, "hvac_mode": hvac_mode},
            blocking=True,
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.hass.services.async_call(
            "climate", "set_fan_mode",
            {"entity_id": self._source_entity_id, "fan_mode": fan_mode},
            blocking=True,
        )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.hass.services.async_call(
            "climate", "set_swing_mode",
            {"entity_id": self._source_entity_id, "swing_mode": swing_mode},
            blocking=True,
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if self._preset_by_name(preset_mode) is None:
            _LOGGER.warning("Unknown preset: %r", preset_mode)
            return
        await self._async_apply_preset(preset_mode)

    def _effective_mode_for(self, preset: Preset) -> str:
        """The HVAC mode a preset's setpoints get mapped through.

        An explicit override if the preset declares one, otherwise whatever
        mode the source is in right now.
        """
        if preset.mode is not None:
            return preset.mode
        current = self.hvac_mode
        return current.value if current is not None else "off"

    def _planned_push(self, preset: Preset, effective_mode: str) -> dict[str, Any]:
        """The set_temperature kwargs applying `preset` would send.

        Pure given the preset, the mode, and the source's current flags —
        which is what lets startup restore reconstruct what a previous run
        pushed without having to store it.

        Values come back in the *source's* unit, because that is what the
        source echoes on its next state event and therefore what drift
        detection has to compare against.
        """
        temp_kwargs = self._adapt_temp_kwargs_to_source(
            compute_service_data(preset, effective_mode), preset
        )
        return {
            key: self._denormalize_temp(val)
            if key in ("temperature", "target_temp_low", "target_temp_high")
            else val
            for key, val in temp_kwargs.items()
        }

    async def _async_apply_preset(self, preset_mode: str) -> None:
        """Push `preset_mode`, substituting Eco's band while peak is open.

        `preset_mode` is what gets *reported* afterwards; `preset` is what
        actually gets pushed. Keeping them separate is what stops the
        substitution from feeding back: preset_mode never changes, so no
        automation sees an event, and drift is measured against the values
        really sent rather than against the requested preset's band.
        """
        preset = self._effective_preset(preset_mode)
        if preset is None:
            _LOGGER.warning("Unknown preset: %r", preset_mode)
            return

        effective_mode = self._effective_mode_for(preset)

        # 1. Push mode change first (if the preset declares one).
        if preset.mode is not None and self.hvac_mode != HVACMode(preset.mode):
            await self.hass.services.async_call(
                "climate", "set_hvac_mode",
                {"entity_id": self._source_entity_id, "hvac_mode": preset.mode},
                blocking=True,
            )

        # 2. Push temperatures, denormalized for the source unit. The source-
        # unit values are what the source will echo back on its next state
        # event, so store those for drift comparison too.
        pushed_temp_kwargs = self._planned_push(preset, effective_mode)
        if pushed_temp_kwargs:
            await self.hass.services.async_call(
                "climate", "set_temperature",
                {"entity_id": self._source_entity_id, **pushed_temp_kwargs},
                blocking=True,
            )

        # 3. Push fan (if declared).
        if preset.fan is not None and self.fan_mode != preset.fan:
            await self.hass.services.async_call(
                "climate", "set_fan_mode",
                {"entity_id": self._source_entity_id, "fan_mode": preset.fan},
                blocking=True,
            )

        # Store the preset's *declared* mode/fan (possibly None). Drift is
        # measured only against properties the preset actually specifies —
        # changing an unspecified property does not exit the preset.
        self._pending_apply = {
            "mode": preset.mode,
            "temp_kwargs": dict(pushed_temp_kwargs),
            "fan": preset.fan,
        }
        self._pending_apply_deadline = time.monotonic() + APPLY_GUARD_SECONDS
        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        data: dict[str, Any] = {"entity_id": self._source_entity_id}
        for key in ("temperature", "target_temp_low", "target_temp_high"):
            if key in kwargs:
                data[key] = self._denormalize_temp(kwargs[key])
        if "hvac_mode" in kwargs:
            data["hvac_mode"] = kwargs["hvac_mode"]
        self._reshape_set_temperature_for_source(data)
        await self.hass.services.async_call(
            "climate", "set_temperature", data, blocking=True,
        )

    def _reshape_set_temperature_for_source(self, data: dict[str, Any]) -> None:
        """Rewrite a manual set_temperature payload to match source flags.

        Mirrors `_adapt_temp_kwargs_to_source` but for ad-hoc UI tweaks: when
        the user nudges the wrapper's setpoint in a single-setpoint mode and
        the source only accepts range, set just the side that drives the
        current mode and preserve the other side from source state.
        """
        if not self._source_state:
            return
        source_features = self._source_state.attributes.get("supported_features", 0)
        supports_single = bool(source_features & ClimateEntityFeature.TARGET_TEMPERATURE)
        supports_range = bool(source_features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE)

        if "temperature" in data and not supports_single and supports_range:
            target = data.pop("temperature")
            attrs = self._source_state.attributes
            cur_low = attrs.get("target_temp_low")
            cur_high = attrs.get("target_temp_high")
            mode = self.hvac_mode
            if mode in (HVACMode.COOL, HVACMode.DRY):
                data["target_temp_low"] = cur_low if cur_low is not None else target
                data["target_temp_high"] = target
            elif mode == HVACMode.AUTO:
                # Source averages low/high in AUTO; center a band on the new
                # target so the displayed midpoint equals what the user set.
                span = 4.0
                if (
                    cur_low is not None
                    and cur_high is not None
                    and cur_high > cur_low
                ):
                    span = cur_high - cur_low
                half = span / 2.0
                data["target_temp_low"] = target - half
                data["target_temp_high"] = target + half
            else:
                data["target_temp_low"] = target
                data["target_temp_high"] = cur_high if cur_high is not None else target
        elif (
            ("target_temp_low" in data or "target_temp_high" in data)
            and not supports_range
            and supports_single
        ):
            low = data.pop("target_temp_low", None)
            high = data.pop("target_temp_high", None)
            if low is not None and high is not None:
                data["temperature"] = (low + high) / 2.0
            else:
                data["temperature"] = low if low is not None else high
