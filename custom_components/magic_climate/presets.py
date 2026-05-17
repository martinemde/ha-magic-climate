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
