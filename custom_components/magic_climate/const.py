"""Constants for the Magic Climate integration."""

DOMAIN = "magic_climate"

CONF_SOURCE_ENTITY_ID = "source_entity_id"
CONF_PRESETS = "presets"
CONF_ENABLED_PRESETS = "enabled_presets"

# Preset dict keys (stored in entry.options[CONF_PRESETS][<preset_id>])
PRESET_LOW = "low"
PRESET_HIGH = "high"
PRESET_MODE = "mode"
PRESET_FAN = "fan"

# HA's standard climate preset names. These are the only preset IDs we expose;
# names are matched against homeassistant.components.climate.const.PRESET_*.
PRESET_HOME = "home"
PRESET_AWAY = "away"
PRESET_SLEEP = "sleep"
PRESET_ECO = "eco"
PRESET_COMFORT = "comfort"
PRESET_BOOST = "boost"
PRESET_ACTIVITY = "activity"

STANDARD_PRESETS: tuple[str, ...] = (
    PRESET_HOME,
    PRESET_AWAY,
    PRESET_SLEEP,
    PRESET_ECO,
    PRESET_COMFORT,
    PRESET_BOOST,
    PRESET_ACTIVITY,
)

DEFAULT_ENABLED_PRESETS: tuple[str, ...] = (PRESET_HOME, PRESET_AWAY, PRESET_SLEEP)

# Sensible per-preset defaults in °C. User adjusts via options flow.
PRESET_DEFAULTS: dict[str, dict[str, float]] = {
    PRESET_HOME:     {PRESET_LOW: 20.0, PRESET_HIGH: 22.0},
    PRESET_AWAY:     {PRESET_LOW: 16.0, PRESET_HIGH: 26.0},
    PRESET_SLEEP:    {PRESET_LOW: 18.0, PRESET_HIGH: 21.0},
    PRESET_ECO:      {PRESET_LOW: 17.0, PRESET_HIGH: 25.0},
    PRESET_COMFORT:  {PRESET_LOW: 20.0, PRESET_HIGH: 23.0},
    PRESET_BOOST:    {PRESET_LOW: 21.0, PRESET_HIGH: 24.0},
    PRESET_ACTIVITY: {PRESET_LOW: 19.0, PRESET_HIGH: 24.0},
}

# Temperature band selectors operate in °C internally.
TEMP_MIN_C = 5.0
TEMP_MAX_C = 35.0
TEMP_STEP_C = 0.5

# Drift detection tolerance in °C
DRIFT_TOLERANCE = 0.1

# How long after a preset apply to suppress drift detection
APPLY_GUARD_SECONDS = 5.0
