"""Peak-window model. Pure Python, no Home Assistant imports.

A peak window is a daily clock-time range — the hours a utility charges a
higher rate. It is stored as two wall-clock times rather than a precomputed
"in peak" boolean so callers can also ask *when* the next window starts,
which is what a future pre-cool needs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional


class PeakValidationError(ValueError):
    """Raised when a peak window fails validation."""


def parse_time(value: str | time) -> time:
    """Accept either a time or HA's "HH:MM:SS" (or "HH:MM") string form."""
    if isinstance(value, time):
        return value
    parts = [int(part) for part in str(value).split(":")]
    while len(parts) < 3:
        parts.append(0)
    hour, minute, second = parts[:3]
    return time(hour, minute, second)


def format_time(value: time) -> str:
    """Render as HA's TimeSelector wants it."""
    return value.strftime("%H:%M:%S")


@dataclass(frozen=True)
class PeakWindow:
    """A daily [start, end) clock-time range.

    The interval is half-open: at exactly `end` the window is over. A window
    whose end is before its start wraps past midnight (20:00–06:00), which is
    an ordinary overnight rate period, not an error.
    """

    start: time
    end: time

    def validate(self) -> None:
        if self.start == self.end:
            raise PeakValidationError("start and end must differ")

    @property
    def wraps_midnight(self) -> bool:
        return self.end < self.start

    def contains(self, moment: time | datetime) -> bool:
        """True while the window is active.

        Compares wall-clock time only. That is deliberate: a peak period is
        defined by what the clock reads, so this stays correct across a DST
        change in a way that arithmetic on absolute instants would not.
        """
        now = moment.time() if isinstance(moment, datetime) else moment
        if self.start == self.end:
            return False
        if self.wraps_midnight:
            return now >= self.start or now < self.end
        return self.start <= now < self.end

    def next_start(self, now: datetime) -> datetime:
        """The next moment the window opens, at or after `now`."""
        return self._next_occurrence(now, self.start)

    def next_end(self, now: datetime) -> datetime:
        """The next moment the window closes, at or after `now`."""
        return self._next_occurrence(now, self.end)

    @staticmethod
    def _next_occurrence(now: datetime, target: time) -> datetime:
        candidate = now.replace(
            hour=target.hour,
            minute=target.minute,
            second=target.second,
            microsecond=0,
        )
        if candidate < now:
            candidate += timedelta(days=1)
        return candidate

    def to_dict(self) -> dict:
        return {"start": format_time(self.start), "end": format_time(self.end)}

    @classmethod
    def from_dict(cls, data: dict) -> "PeakWindow":
        return cls(start=parse_time(data["start"]), end=parse_time(data["end"]))


def resolve(
    requested: str,
    substitute_for: str,
    substitute_with: Optional[str],
    in_peak: bool,
) -> str:
    """Which preset should actually be pushed for a requested preset.

    Only `substitute_for` is ever redirected, and only while the window is
    active and a substitute exists. Everything else is the identity.
    """
    if not in_peak or substitute_with is None or requested != substitute_for:
        return requested
    return substitute_with
