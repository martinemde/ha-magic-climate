"""Constants for the Magic Climate integration."""

DOMAIN = "magic_climate"

CONF_SOURCE_ENTITY_ID = "source_entity_id"
CONF_PRESETS = "presets"
CONF_ENABLED_PRESETS = "enabled_presets"
CONF_PEAK = "peak"
CONF_BOOST = "boost"
CONF_SLEEP = "sleep"

# Preset dict keys (stored in entry.options[CONF_PRESETS][<preset_id>])
PRESET_LOW = "low"
PRESET_HIGH = "high"
PRESET_FAN = "fan"

# Window dict keys. A blank start or end means the window never fires and the
# preset is reached only by selecting it — see schedule.Window.from_dict.
WINDOW_START = "start"
WINDOW_END = "end"

# entry.options[CONF_PEAK]
PEAK_ENABLED = "enabled"

# entry.options[CONF_BOOST]
BOOST_PRELOAD = "preload"
BOOST_MINUTES = "minutes"

# HA's standard climate preset names. These are the only preset IDs we expose;
# names are matched against homeassistant.components.climate.const.PRESET_*.
PRESET_HOME = "home"
PRESET_COMFORT = "comfort"
PRESET_AWAY = "away"
PRESET_ECO = "eco"
PRESET_BOOST = "boost"
PRESET_SLEEP = "sleep"

# Home holds no band of its own. It is the automatic preset: it resolves to
# one of the others by the wall clock, and reports "home" the whole time.
AUTO_PRESET = PRESET_HOME

# Always available. Comfort is the band Home falls back to, so it is not
# optional; Away has no window and is reached by a presence automation.
BASE_PRESETS: tuple[str, ...] = (PRESET_COMFORT, PRESET_AWAY)

# Each has a checkbox in the options flow. Order is picker order.
OPTIONAL_PRESETS: tuple[str, ...] = (PRESET_ECO, PRESET_BOOST, PRESET_SLEEP)

# Every band the options flow stores, whether or not it is currently offered.
# Toggling a preset off and back on restores the values it had.
CONFIGURABLE_PRESETS: tuple[str, ...] = (*BASE_PRESETS, *OPTIONAL_PRESETS)

# Picker order: the automatic one first, then the band it rests on, then
# everything that pins a specific band.
PRESET_ORDER: tuple[str, ...] = (
    PRESET_HOME,
    PRESET_COMFORT,
    PRESET_AWAY,
    PRESET_ECO,
    PRESET_BOOST,
    PRESET_SLEEP,
)

# A typical afternoon time-of-use peak. Applies every day; utilities that
# exempt weekends are not modeled.
DEFAULT_PEAK_START = "16:00:00"
DEFAULT_PEAK_END = "21:00:00"

# How long before peak the Boost band runs when preloading. An hour is a
# starting point, not a measurement — the right value is a property of the
# building's thermal mass and is meant to be tuned per room.
DEFAULT_PRELOAD_MINUTES = 60
MIN_PRELOAD_MINUTES = 5
MAX_PRELOAD_MINUTES = 240


def default_peak() -> dict:
    """Peak config for an entry that has never had one: off, but populated."""
    return {
        PEAK_ENABLED: False,
        WINDOW_START: DEFAULT_PEAK_START,
        WINDOW_END: DEFAULT_PEAK_END,
    }


def default_boost() -> dict:
    """Boost preload for an entry that has never had one."""
    return {BOOST_PRELOAD: False, BOOST_MINUTES: DEFAULT_PRELOAD_MINUTES}


def default_sleep() -> dict:
    """Sleep window for an entry that has never had one: manual, no times."""
    return {WINDOW_START: "", WINDOW_END: ""}


# Sensible per-preset defaults in °C. User adjusts via the options flow.
#
# Boost is a narrow band straddling Comfort's midpoint rather than a wider or
# warmer one: preloading means driving the room *past* where Comfort would
# settle, so both edges move inward — heat to a higher floor, cool to a lower
# ceiling — before the expensive hours start.
PRESET_DEFAULTS: dict[str, dict[str, float]] = {
    PRESET_COMFORT: {PRESET_LOW: 20.0, PRESET_HIGH: 22.0},
    PRESET_AWAY:    {PRESET_LOW: 16.0, PRESET_HIGH: 26.0},
    PRESET_ECO:     {PRESET_LOW: 17.0, PRESET_HIGH: 25.0},
    PRESET_BOOST:   {PRESET_LOW: 20.5, PRESET_HIGH: 21.5},
    PRESET_SLEEP:   {PRESET_LOW: 18.0, PRESET_HIGH: 21.0},
}

# Temperature band selectors operate in °C internally.
TEMP_MIN_C = 5.0
TEMP_MAX_C = 35.0
TEMP_STEP_C = 0.5

# Drift detection tolerance in °C
DRIFT_TOLERANCE = 0.1

# How long after a preset apply to suppress drift detection
APPLY_GUARD_SECONDS = 5.0
