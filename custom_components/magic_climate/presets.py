"""Preset model and pure-Python apply logic. No Home Assistant imports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class PresetValidationError(ValueError):
    """Raised when a preset fails validation."""


@dataclass
class Preset:
    """A named comfort profile: a temperature band, and optionally a fan speed.

    A preset does not carry an HVAC mode. Heating or cooling is a seasonal
    decision made once for the whole unit, not something a comfort band should
    flip on the way past — a Sleep preset that forced `heat` would fight the
    house in August. The band is applied through whatever mode the unit is
    already in.

    Construction does not validate. Call `validate()` at trust boundaries
    (loading from config storage, options-flow submit).
    """

    name: str
    low: float
    high: float
    fan: Optional[str] = None

    def validate(self) -> None:
        if not self.name or not self.name.strip():
            raise PresetValidationError("name must be non-empty")
        if not (self.low < self.high):
            raise PresetValidationError("low must be < high")

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "low": self.low, "high": self.high}
        if self.fan is not None:
            d["fan"] = self.fan
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Preset":
        return cls(
            name=data["name"],
            low=float(data["low"]),
            high=float(data["high"]),
            fan=data.get("fan"),
        )


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

    `effective_mode` is the mode the source is already in — presets never
    change it. Returns the kwargs for climate.set_temperature, or an empty
    dict for modes that don't accept a temperature (FAN_ONLY, OFF).
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
