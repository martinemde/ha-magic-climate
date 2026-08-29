"""Clock-driven band selection. Pure Python, no Home Assistant imports.

Home is the only preset that moves on its own. Everything it can move to is
a band with a daily clock window attached, and this module answers the one
question that follows from that: given the wall clock, which band should
Home be holding right now.

Windows are stored as wall-clock times rather than precomputed booleans so
callers can also ask *when* the next one opens — the peak boundaries are
published for exactly that reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

from .const import PRESET_BOOST, PRESET_COMFORT, PRESET_ECO, PRESET_SLEEP


class WindowValidationError(ValueError):
    """Raised when a window fails validation."""


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


def minus_minutes(value: time, minutes: int) -> time:
    """`value` moved earlier by `minutes`, wrapping past midnight."""
    total = value.hour * 3600 + value.minute * 60 + value.second - minutes * 60
    total %= 86400
    return time(total // 3600, (total % 3600) // 60, total % 60)


@dataclass(frozen=True)
class Window:
    """A daily [start, end) clock-time range.

    The interval is half-open: at exactly `end` the window is over. A window
    whose end is before its start wraps past midnight (20:00–06:00), which is
    an ordinary overnight period, not an error.
    """

    start: time
    end: time

    def validate(self) -> None:
        if self.start == self.end:
            raise WindowValidationError("start and end must differ")

    @property
    def wraps_midnight(self) -> bool:
        return self.end < self.start

    def contains(self, moment: time | datetime) -> bool:
        """True while the window is open.

        Compares wall-clock time only. That is deliberate: these windows are
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
    def from_dict(cls, data: dict) -> Optional["Window"]:
        """Build from a stored dict, or None when either time is blank.

        Blank times are how "trigger this one manually" is stored: the preset
        still exists and can be selected, it just has no window to fire on.
        """
        start, end = data.get("start"), data.get("end")
        if not start or not end:
            return None
        return cls(start=parse_time(start), end=parse_time(end))


@dataclass(frozen=True)
class Schedule:
    """The windows that move Home, in precedence order.

    Sleep, then Eco, then Boost, then Comfort as the floor.

    Sleep outranks Eco because a night band exists for someone asleep in the
    room, and the saving from holding Eco through it is not worth waking up
    for. Boost sits under Eco only for completeness — preload ends exactly
    where peak begins, so the two never actually overlap.

    A window is only ever built for a preset that exists, so switching a
    preset off silences its window rather than leaving a second flag that
    could disagree. Leaving its times blank does the same thing while keeping
    the preset selectable: that is how a band is put under manual control.
    """

    peak: Optional[Window] = None
    preload: Optional[Window] = None
    sleep: Optional[Window] = None

    @property
    def moves_home(self) -> bool:
        """Whether anything can pull Home off the Comfort band.

        When nothing can, Home *is* Comfort, and they are one preset with one
        name rather than two picker entries pushing identical setpoints.
        """
        return any((self.peak, self.preload, self.sleep))

    def resolve(self, moment: time | datetime) -> str:
        """The preset whose band Home should be holding at `moment`."""
        if self.sleep is not None and self.sleep.contains(moment):
            return PRESET_SLEEP
        if self.peak is not None and self.peak.contains(moment):
            return PRESET_ECO
        if self.preload is not None and self.preload.contains(moment):
            return PRESET_BOOST
        return PRESET_COMFORT
