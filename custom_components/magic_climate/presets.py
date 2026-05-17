"""Preset model and pure-Python apply logic. No Home Assistant imports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class PresetValidationError(ValueError):
    """Raised when a preset fails validation."""


@dataclass
class Preset:
    """A named comfort profile.

    A preset always declares a low/high temperature band. Mode and fan are
    optional — when set, they are pushed to the source before the temps.

    Construction does not validate. Call `validate()` at trust boundaries
    (loading from config storage, options-flow submit).
    """

    name: str
    low: float
    high: float
    mode: Optional[str] = None
    fan: Optional[str] = None

    def validate(self) -> None:
        if not self.name or not self.name.strip():
            raise PresetValidationError("name must be non-empty")
        if not (self.low < self.high):
            raise PresetValidationError("low must be < high")


# HVAC mode string constants — match Home Assistant's HVACMode enum values.
MODE_HEAT_COOL = "heat_cool"
MODE_AUTO = "auto"
MODE_HEAT = "heat"
MODE_COOL = "cool"
MODE_DRY = "dry"
MODE_FAN_ONLY = "fan_only"
MODE_OFF = "off"


def compute_service_data(preset: Preset, effective_mode: str) -> dict:
    """Translate a preset's low/high into source service-call kwargs.

    Returns the kwargs for the climate.set_temperature service. Does NOT
    include hvac_mode — the caller is responsible for issuing
    set_hvac_mode separately when the preset overrides the mode.

    Returns an empty dict for modes that don't accept a temperature
    (FAN_ONLY, OFF).
    """
    if effective_mode == MODE_HEAT_COOL:
        return {
            "target_temp_low": preset.low,
            "target_temp_high": preset.high,
        }
    if effective_mode == MODE_AUTO:
        # Mitsubishi (and HA's contract) treats AUTO as a single setpoint;
        # the unit applies its own deadband (±4 °C on Mitsubishi).
        return {"temperature": (preset.low + preset.high) / 2.0}
    if effective_mode == MODE_HEAT:
        return {"temperature": preset.low}
    if effective_mode == MODE_COOL:
        return {"temperature": preset.high}
    if effective_mode == MODE_DRY:
        return {"temperature": preset.high}
    # MODE_FAN_ONLY, MODE_OFF, and anything unrecognized: no temperature push.
    return {}
