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
    AUTO_PRESET,
    BOOST_MINUTES,
    BOOST_PRELOAD,
    CONF_BOOST,
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SLEEP,
    CONF_SOURCE_ENTITY_ID,
    CONFIGURABLE_PRESETS,
    DEFAULT_PRELOAD_MINUTES,
    DOMAIN,
    DRIFT_TOLERANCE,
    OPTIONAL_PRESETS,
    PEAK_ENABLED,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_DEFAULTS,
    PRESET_ECO,
    PRESET_ORDER,
    PRESET_SLEEP,
    WINDOW_END,
    WINDOW_START,
)
from .presets import Preset, PresetValidationError, compute_service_data
from .schedule import (
    Schedule,
    Window,
    WindowValidationError,
    format_time,
    minus_minutes,
)

_LOGGER = logging.getLogger(__name__)


def _load_presets(entry: ConfigEntry) -> dict[str, Preset]:
    """The bands this entry offers, keyed by preset id.

    Comfort and Away are unconditional — Comfort because it is the band Home
    rests on when nothing else is scheduled, Away because it is the one
    preset with no window at all and exists purely to be selected. The rest
    appear only when their checkbox is on.

    Home is deliberately absent. It holds no band of its own; it resolves to
    one of these by the clock.
    """
    options = entry.options or {}
    enabled = set(options.get(CONF_ENABLED_PRESETS, []) or [])
    stored = options.get(CONF_PRESETS, {}) or {}
    presets: dict[str, Preset] = {}
    for pid in CONFIGURABLE_PRESETS:
        if pid in OPTIONAL_PRESETS and pid not in enabled:
            continue
        raw = stored.get(pid) or PRESET_DEFAULTS.get(pid)
        try:
            preset = Preset.from_dict({"name": pid, **raw})
            preset.validate()
        except (PresetValidationError, KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Skipping invalid preset %r: %s", pid, err)
            continue
        presets[pid] = preset
    return presets


def _load_window(raw: dict, label: str) -> Window | None:
    """One stored window, or None if it is blank or unusable.

    Blank is the ordinary case, not an error: it is how "this preset is
    triggered by hand" is stored. A window that is present but unparseable
    is dropped with a warning rather than raised — a bad stored time must
    not take the thermostat down with it.
    """
    try:
        window = Window.from_dict(raw or {})
        if window is not None:
            window.validate()
    except (WindowValidationError, KeyError, TypeError, ValueError) as err:
        _LOGGER.warning("Ignoring invalid %s window %r: %s", label, raw, err)
        return None
    return window


def _load_schedule(entry: ConfigEntry, presets: dict[str, Preset]) -> Schedule:
    """The windows that move Home.

    Each one needs its band to exist before it can fire, so switching a
    preset off disables its window by construction rather than by a second
    flag that could disagree with the first.
    """
    options = entry.options or {}

    peak_raw = options.get(CONF_PEAK) or {}
    peak = None
    if peak_raw.get(PEAK_ENABLED) and PRESET_ECO in presets:
        peak = _load_window(peak_raw, "peak")

    # Preload is derived, not stored: it is the run-up to peak, so it can
    # only exist where peak does and always ends exactly where peak begins.
    preload = None
    boost_raw = options.get(CONF_BOOST) or {}
    if peak is not None and boost_raw.get(BOOST_PRELOAD) and PRESET_BOOST in presets:
        minutes = int(boost_raw.get(BOOST_MINUTES) or DEFAULT_PRELOAD_MINUTES)
        if minutes > 0:
            preload = _load_window(
                {
                    WINDOW_START: format_time(minus_minutes(peak.start, minutes)),
                    WINDOW_END: format_time(peak.start),
                },
                "preload",
            )

    sleep = None
    if PRESET_SLEEP in presets:
        sleep = _load_window(options.get(CONF_SLEEP) or {}, "sleep")

    return Schedule(peak=peak, preload=preload, sleep=sleep)


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
        self._presets: dict[str, Preset] = _load_presets(entry)
        self._schedule: Schedule = _load_schedule(entry, self._presets)
        # Latched so the minute tick can act on the *transition* between
        # bands rather than re-pushing every minute a window is open.
        self._scheduled: str = self._schedule.resolve(dt_util.now())
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
        self._scheduled = self._schedule.resolve(dt_util.now())
        self.async_on_remove(
            async_track_time_change(self.hass, self._handle_minute_tick, second=0)
        )

    async def _async_restore_preset(self) -> None:
        """Pick the held preset back up after a restart.

        preset_mode used to live only in memory, so a restart dropped it and
        nothing re-asserted. The schedule made that matter: a restart inside
        a window left Home applying the Comfort band, with no boundary left
        to correct it.

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
        if not restored or restored not in (self.preset_modes or []):
            return

        preset = self._effective_preset(restored)
        if preset is None:
            return

        self._attr_preset_mode = restored
        # No guard window: this run pushed nothing, so the very next source
        # event should be judged on its merits.
        self._pending_apply = {
            "temp_kwargs": self._planned_push(preset),
            "fan": preset.fan,
        }
        self._pending_apply_deadline = 0.0

        # A window boundary crossed while HA was down leaves the source
        # holding a different band. Drift would read that as a manual
        # override and silently drop the preset, so reconcile it here.
        if last.attributes.get("effective_preset") not in (None, preset.name):
            _LOGGER.debug(
                "Schedule moved while down; re-applying %r on %s",
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
        appear in the picker. Presets and the schedule are the only things
        options can change; the source entity is fixed at entry creation, so
        the state subscription never needs rebuilding.

        Reloading does not re-push. Editing a band while holding its preset
        takes effect the next time the preset is applied, which keeps the
        one-shot contract: options edits never command the hardware.
        """
        self._presets = _load_presets(entry)
        self._schedule = _load_schedule(entry, self._presets)
        self._scheduled = self._schedule.resolve(dt_util.now())
        if self._attr_preset_mode and self._attr_preset_mode not in (
            self.preset_modes or []
        ):
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

    # --------------------------------------------------------------- schedule

    def _preset_by_name(self, name: str) -> Preset | None:
        return self._presets.get(name)

    def _effective_preset(self, requested: str) -> Preset | None:
        """The preset whose band actually gets pushed for `requested`.

        Only Home is ever redirected. Every other preset is its own band —
        picking Eco holds Eco whether or not peak is open — which is what
        makes selecting anything a manual override of the schedule.

        The *reported* preset stays whatever was requested; see
        _async_apply_preset for why that matters.
        """
        if requested != AUTO_PRESET:
            return self._presets.get(requested)
        target = (
            self._schedule.resolve(dt_util.now())
            if self._schedule.moves_home
            else PRESET_COMFORT
        )
        return self._presets.get(target) or self._presets.get(PRESET_COMFORT)

    async def _handle_minute_tick(self, now: datetime) -> None:
        """Move Home to the band the clock now calls for.

        This is the one place the wrapper writes without being asked, so it
        is deliberately narrow: only on a change of resolved band, only while
        Home is the held preset, and only when something is scheduled to move
        it. A manual touch or a wall thermostat will already have cleared
        preset_mode via drift detection, so this can never overwrite anything
        but the wrapper's own earlier push.

        A minute tick rather than re-armed point-in-time callbacks: it
        re-derives every window from the wall clock, so it heals itself
        across restarts, DST changes, and options edits instead of depending
        on timers armed under the old configuration.
        """
        scheduled = self._schedule.resolve(dt_util.now())
        if scheduled == self._scheduled:
            return
        previous, self._scheduled = self._scheduled, scheduled

        if (
            self._attr_preset_mode == AUTO_PRESET
            and self._schedule.moves_home
            and self.available
        ):
            _LOGGER.debug(
                "Schedule moved %s -> %s; re-applying %r on %s",
                previous,
                scheduled,
                AUTO_PRESET,
                self._source_entity_id,
            )
            await self._async_apply_preset(AUTO_PRESET)
        else:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Which band is really applied, and when peak next moves.

        `effective_preset` is the one attribute that always publishes: while
        Home is held it is the only way to see which band the schedule chose,
        and the restart path reads it back to tell a crossed boundary apart
        from a manual override.

        Peak is additionally published as clock times and absolute next
        occurrences, because a consumer that wants to pre-cool needs to know
        *when* peak starts, not just whether it is running. These change at
        most a few times a day, so they do not churn the recorder.
        """
        effective = None
        if self._attr_preset_mode is not None:
            preset = self._effective_preset(self._attr_preset_mode)
            effective = preset.name if preset else None

        attrs: dict[str, Any] = {
            "peak_active": False,
            "effective_preset": effective,
        }
        peak = self._schedule.peak
        if peak is not None:
            now = dt_util.now()
            window = peak.to_dict()
            attrs.update(
                {
                    "peak_active": peak.contains(now),
                    "peak_start": window["start"],
                    "peak_end": window["end"],
                    "next_peak_start": peak.next_start(now).isoformat(),
                    "next_peak_end": peak.next_end(now).isoformat(),
                }
            )
        return attrs

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
        """Home first, then every band that can be pinned by hand.

        Comfort is hidden while nothing is scheduled: Home already *is* the
        Comfort band then, and two picker entries pushing identical setpoints
        are two names for one thing.
        """
        if not self._presets:
            return None
        available = set(self._presets)
        available.add(AUTO_PRESET)
        if not self._schedule.moves_home:
            available.discard(PRESET_COMFORT)
        return [pid for pid in PRESET_ORDER if pid in available]

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
        if preset_mode not in (self.preset_modes or []):
            _LOGGER.warning("Unknown preset: %r", preset_mode)
            return
        await self._async_apply_preset(preset_mode)

    @property
    def _source_mode(self) -> str:
        """The HVAC mode a preset's setpoints get mapped through.

        Always whatever the source is in. Presets carry no mode of their own:
        heating or cooling is a seasonal decision made once for the unit, not
        something a comfort band flips on the way past.
        """
        current = self.hvac_mode
        return current.value if current is not None else "off"

    def _planned_push(self, preset: Preset) -> dict[str, Any]:
        """The set_temperature kwargs applying `preset` would send.

        Pure given the preset and the source's current mode and flags —
        which is what lets startup restore reconstruct what a previous run
        pushed without having to store it.

        Values come back in the *source's* unit, because that is what the
        source echoes on its next state event and therefore what drift
        detection has to compare against.
        """
        temp_kwargs = self._adapt_temp_kwargs_to_source(
            compute_service_data(preset, self._source_mode), preset
        )
        return {
            key: self._denormalize_temp(val)
            if key in ("temperature", "target_temp_low", "target_temp_high")
            else val
            for key, val in temp_kwargs.items()
        }

    async def _async_apply_preset(self, preset_mode: str) -> None:
        """Push `preset_mode`, resolving Home to whatever the clock calls for.

        `preset_mode` is what gets *reported* afterwards; `preset` is what
        actually gets pushed. Keeping them separate is what stops the
        schedule from feeding back: preset_mode stays "home" across every
        boundary, so no automation sees an event, and drift is measured
        against the values really sent rather than the Comfort band.
        """
        preset = self._effective_preset(preset_mode)
        if preset is None:
            _LOGGER.warning("Unknown preset: %r", preset_mode)
            return

        # 1. Push temperatures, denormalized for the source unit. The source-
        # unit values are what the source will echo back on its next state
        # event, so store those for drift comparison too.
        pushed_temp_kwargs = self._planned_push(preset)
        if pushed_temp_kwargs:
            await self.hass.services.async_call(
                "climate", "set_temperature",
                {"entity_id": self._source_entity_id, **pushed_temp_kwargs},
                blocking=True,
            )

        # 2. Push fan (if declared).
        if preset.fan is not None and self.fan_mode != preset.fan:
            await self.hass.services.async_call(
                "climate", "set_fan_mode",
                {"entity_id": self._source_entity_id, "fan_mode": preset.fan},
                blocking=True,
            )

        # Store the preset's *declared* fan (possibly None). Drift is measured
        # only against properties the preset actually specifies — nudging the
        # fan does not exit a band-only preset.
        self._pending_apply = {
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
