"""Constants for the Magic Climate integration."""

DOMAIN = "magic_climate"

CONF_SOURCE_ENTITY_ID = "source_entity_id"
CONF_PRESETS = "presets"

# Preset dict keys (stored in entry.options[CONF_PRESETS])
PRESET_NAME = "name"
PRESET_LOW = "low"
PRESET_HIGH = "high"
PRESET_MODE = "mode"
PRESET_FAN = "fan"

# Drift detection tolerance in °C
DRIFT_TOLERANCE = 0.1

# How long after a preset apply to suppress drift detection
APPLY_GUARD_SECONDS = 5.0
