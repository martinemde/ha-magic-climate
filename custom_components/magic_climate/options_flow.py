"""Options flow for Magic Climate — one page, because it is one decision.

Comfort, Eco, Boost and Sleep are not four independent settings. Eco is
Comfort minus what you will pay for at peak; Boost is what you do to Comfort
just before that; Sleep is Comfort with nobody watching. Reading any one of
them means looking at the others, so they are laid out on a single scrolling
form rather than behind a menu that shows one at a time.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import UnitOfTemperature
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    BOOST_MINUTES,
    BOOST_PRELOAD,
    CONF_BOOST,
    CONF_ENABLED_PRESETS,
    CONF_PEAK,
    CONF_PRESETS,
    CONF_SLEEP,
    CONF_SOURCE_ENTITY_ID,
    DEFAULT_PRELOAD_MINUTES,
    MAX_PRELOAD_MINUTES,
    MIN_PRELOAD_MINUTES,
    PEAK_ENABLED,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_DEFAULTS,
    PRESET_ECO,
    PRESET_FAN,
    PRESET_HIGH,
    PRESET_LOW,
    PRESET_SLEEP,
    TEMP_MAX_C,
    TEMP_MIN_C,
    WINDOW_END,
    WINDOW_START,
    default_boost,
    default_peak,
    default_sleep,
)
from .schedule import Window, WindowValidationError

# Empty string means "the preset does not touch this setting".
_NO_OVERRIDE = ""
_NO_OVERRIDE_LABEL = "Leave unchanged"

# Section keys. Each preset owns its section; the windows and toggles that
# drive a preset live inside the section for the band they move to, because
# that is the band you are looking at when you decide the times.
_ENABLED = "enabled"
_AUTO = "auto"


class MagicClimateOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        # Fixed at entry creation. The entity's state subscription, unique id,
        # and every stored fan value are tied to this source, so changing it
        # means deleting the entry and adding a new one.
        self._source_entity_id: str = entry.data[CONF_SOURCE_ENTITY_ID]

    # ------------------------------------------------------------- the form

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            options, errors = self._read(user_input)
            if not errors:
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=self._schema(),
            errors=errors,
            description_placeholders={"source": self._source_entity_id},
        )

    # ------------------------------------------------------------ read side

    def _read(self, user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """Turn a submitted form into options, or into an error to show.

        Validation is deliberately about *coherence*, not about taste: a band
        whose low is above its high cannot be applied, and a window that asks
        to preload before a peak that does not exist has nothing to preload
        for. Everything else — an Eco band warmer than Comfort, a sleep window
        that covers the whole day — is a choice, not a mistake.
        """
        comfort = user_input.get(PRESET_COMFORT, {})
        away = user_input.get(PRESET_AWAY, {})
        eco = user_input.get(PRESET_ECO, {})
        boost = user_input.get(PRESET_BOOST, {})
        sleep = user_input.get(PRESET_SLEEP, {})

        enabled = [
            pid
            for pid, data in (
                (PRESET_ECO, eco),
                (PRESET_BOOST, boost),
                (PRESET_SLEEP, sleep),
            )
            if data.get(_ENABLED)
        ]

        presets = {
            PRESET_COMFORT: self._band(comfort),
            PRESET_AWAY: self._band(away),
            PRESET_ECO: self._band(eco),
            PRESET_BOOST: self._band(boost),
            PRESET_SLEEP: self._band(sleep),
        }

        peak_start = eco.get(WINDOW_START) or ""
        peak_end = eco.get(WINDOW_END) or ""
        options = {
            CONF_ENABLED_PRESETS: enabled,
            CONF_PRESETS: presets,
            CONF_PEAK: {
                PEAK_ENABLED: bool(eco.get(_AUTO)),
                WINDOW_START: peak_start,
                WINDOW_END: peak_end,
            },
            CONF_BOOST: {
                BOOST_PRELOAD: bool(boost.get(BOOST_PRELOAD)),
                BOOST_MINUTES: int(
                    boost.get(BOOST_MINUTES) or DEFAULT_PRELOAD_MINUTES
                ),
            },
            CONF_SLEEP: {
                WINDOW_START: sleep.get(WINDOW_START) or "",
                WINDOW_END: sleep.get(WINDOW_END) or "",
            },
        }

        error = self._first_error(options, enabled)
        return options, ({"base": error} if error else {})

    def _first_error(self, options: dict[str, Any], enabled: list[str]) -> str | None:
        for pid in (PRESET_COMFORT, PRESET_AWAY, *enabled):
            band = options[CONF_PRESETS][pid]
            if band[PRESET_LOW] >= band[PRESET_HIGH]:
                return f"{pid}_low_not_below_high"

        peak = _valid_window(options[CONF_PEAK])
        if options[CONF_PEAK][PEAK_ENABLED]:
            if PRESET_ECO not in enabled:
                return "auto_needs_eco"
            if peak is None:
                return "auto_needs_peak_times"
        if options[CONF_BOOST][BOOST_PRELOAD]:
            if PRESET_BOOST not in enabled:
                return "preload_needs_boost"
            if peak is None or not options[CONF_PEAK][PEAK_ENABLED]:
                return "preload_needs_peak"
        if PRESET_SLEEP in enabled and _half_a_window(options[CONF_SLEEP]):
            return "sleep_window_incomplete"
        if _half_a_window(options[CONF_PEAK]):
            return "peak_window_incomplete"
        return None

    def _band(self, data: dict[str, Any]) -> dict[str, Any]:
        band: dict[str, Any] = {
            PRESET_LOW: round(self._display_to_c(float(data[PRESET_LOW])), 2),
            PRESET_HIGH: round(self._display_to_c(float(data[PRESET_HIGH])), 2),
        }
        fan = (data.get(PRESET_FAN) or "").strip()
        if fan:
            band[PRESET_FAN] = fan
        return band

    # ----------------------------------------------------------- write side

    def _schema(self) -> vol.Schema:
        """The whole form. Sections are open, so the bands can be compared.

        A section cannot react to its own checkbox — HA renders the form
        once — so a preset's band stays visible under an unchecked box. The
        checkbox decides only whether the preset reaches the picker, and the
        values under it are kept either way, so turning one off and back on
        restores what was there.
        """
        options = self._entry.options or {}
        stored = options.get(CONF_PRESETS, {}) or {}
        enabled = set(options.get(CONF_ENABLED_PRESETS, []) or [])
        peak = {**default_peak(), **(options.get(CONF_PEAK) or {})}
        boost = {**default_boost(), **(options.get(CONF_BOOST) or {})}
        sleep = {**default_sleep(), **(options.get(CONF_SLEEP) or {})}

        def band(pid: str) -> dict[Any, Any]:
            values = stored.get(pid) or PRESET_DEFAULTS[pid]
            return {
                vol.Required(
                    PRESET_LOW, default=self._c_to_display(values[PRESET_LOW])
                ): self._temp_selector(),
                vol.Required(
                    PRESET_HIGH, default=self._c_to_display(values[PRESET_HIGH])
                ): self._temp_selector(),
                vol.Optional(
                    PRESET_FAN, default=values.get(PRESET_FAN, _NO_OVERRIDE)
                ): self._fan_selector(values.get(PRESET_FAN)),
            }

        def check(key: str, on: bool) -> dict[Any, Any]:
            return {vol.Required(key, default=on): selector.BooleanSelector()}

        return vol.Schema(
            {
                vol.Required(PRESET_COMFORT): _open(band(PRESET_COMFORT)),
                vol.Required(PRESET_ECO): _open(
                    {
                        **check(_ENABLED, PRESET_ECO in enabled),
                        **check(_AUTO, bool(peak[PEAK_ENABLED])),
                        **_optional_time(WINDOW_START, peak[WINDOW_START]),
                        **_optional_time(WINDOW_END, peak[WINDOW_END]),
                        **band(PRESET_ECO),
                    }
                ),
                vol.Required(PRESET_BOOST): _open(
                    {
                        **check(_ENABLED, PRESET_BOOST in enabled),
                        **check(BOOST_PRELOAD, bool(boost[BOOST_PRELOAD])),
                        vol.Required(
                            BOOST_MINUTES, default=int(boost[BOOST_MINUTES])
                        ): selector.NumberSelector(
                            selector.NumberSelectorConfig(
                                min=MIN_PRELOAD_MINUTES,
                                max=MAX_PRELOAD_MINUTES,
                                step=5,
                                unit_of_measurement="min",
                                mode=selector.NumberSelectorMode.BOX,
                            )
                        ),
                        **band(PRESET_BOOST),
                    }
                ),
                vol.Required(PRESET_SLEEP): _open(
                    {
                        **check(_ENABLED, PRESET_SLEEP in enabled),
                        **_optional_time(WINDOW_START, sleep[WINDOW_START]),
                        **_optional_time(WINDOW_END, sleep[WINDOW_END]),
                        **band(PRESET_SLEEP),
                    }
                ),
                # Away sits last: it is the only band Home never moves
                # through, so it has nothing to be compared against.
                vol.Required(PRESET_AWAY): _open(band(PRESET_AWAY)),
            }
        )

    # ------------------------------------------------------- source options

    def _fan_selector(self, current: str | None) -> selector.Selector:
        """Dropdown of the source's fan modes, or free text if it reports none.

        There is no universal fan vocabulary to fall back on, so a source
        that is unavailable (or has no fan control) leaves the field as text
        rather than an empty dropdown. A stored value the source no longer
        offers is kept in the list: a firmware or ESPHome change that drops a
        speed should not silently rewrite a preset configured against it.
        """
        state = self.hass.states.get(self._source_entity_id)
        fan_modes = (
            [str(v) for v in state.attributes.get("fan_modes") or []] if state else []
        )
        if not fan_modes:
            return selector.TextSelector()
        if current and current not in fan_modes:
            fan_modes = [*fan_modes, current]
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(
                        value=_NO_OVERRIDE, label=_NO_OVERRIDE_LABEL
                    ),
                    *(
                        selector.SelectOptionDict(
                            value=option, label=option.replace("_", " ").capitalize()
                        )
                        for option in fan_modes
                    ),
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

    # ---------------------------------------------------------- unit helpers

    @property
    def _fahrenheit(self) -> bool:
        return self.hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT

    def _c_to_display(self, c: float) -> float:
        if self._fahrenheit:
            return round(c * 9.0 / 5.0 + 32.0, 1)
        return round(c, 1)

    def _display_to_c(self, val: float) -> float:
        if self._fahrenheit:
            return (val - 32.0) * 5.0 / 9.0
        return val

    def _temp_selector(self) -> selector.NumberSelector:
        if self._fahrenheit:
            return selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=round(TEMP_MIN_C * 9.0 / 5.0 + 32.0),
                    max=round(TEMP_MAX_C * 9.0 / 5.0 + 32.0),
                    step=1.0,
                    unit_of_measurement="°F",
                )
            )
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=TEMP_MIN_C,
                max=TEMP_MAX_C,
                step=0.5,
                unit_of_measurement="°C",
            )
        )


def _open(fields: dict[Any, Any]) -> section:
    """A section rendered expanded — the point is to compare, not to drill in."""
    return section(vol.Schema(fields), {"collapsed": False})


def _optional_time(key: str, current: str) -> dict[Any, Any]:
    """A time field that may be left blank.

    Blank is meaningful, not missing: it says this band has no window and is
    reached only by selecting it. So the stored value is offered as a
    *suggestion* rather than a default — a default is re-applied when the
    field comes back empty, which would make an existing window impossible
    to clear.
    """
    field = vol.Optional(
        key, description={"suggested_value": current} if current else None
    )
    return {field: selector.TimeSelector()}


def _valid_window(raw: dict[str, Any]) -> Window | None:
    try:
        window = Window.from_dict(raw)
        if window is not None:
            window.validate()
    except (WindowValidationError, ValueError, TypeError):
        return None
    return window


def _half_a_window(raw: dict[str, Any]) -> bool:
    """One time filled in and the other blank — an unfinished thought."""
    return bool(raw.get(WINDOW_START)) != bool(raw.get(WINDOW_END))
