from datetime import datetime, time

import pytest

from custom_components.magic_climate.schedule import (
    Schedule,
    Window,
    WindowValidationError,
    format_time,
    minus_minutes,
    parse_time,
)

AFTERNOON = Window(start=time(16, 0), end=time(21, 0))
OVERNIGHT = Window(start=time(20, 0), end=time(6, 0))
NIGHT = Window(start=time(22, 0), end=time(6, 30))
PRELOAD = Window(start=time(15, 0), end=time(16, 0))


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 28, hour, minute)


# --- parsing ---------------------------------------------------------------


def test_parse_time_full_string():
    assert parse_time("16:30:15") == time(16, 30, 15)


def test_parse_time_without_seconds():
    """HA's TimeSelector has emitted both forms across versions."""
    assert parse_time("16:30") == time(16, 30, 0)


def test_parse_time_passes_through_a_time():
    assert parse_time(time(9, 5)) == time(9, 5)


def test_format_time_round_trips():
    assert format_time(parse_time("16:00:00")) == "16:00:00"


# --- validation ------------------------------------------------------------


def test_equal_start_and_end_is_rejected():
    with pytest.raises(WindowValidationError, match="must differ"):
        Window(start=time(16, 0), end=time(16, 0)).validate()


def test_a_normal_window_validates():
    AFTERNOON.validate()  # no exception


def test_an_overnight_window_validates():
    OVERNIGHT.validate()  # no exception


# --- containment, normal window --------------------------------------------


def test_before_start_is_off_peak():
    assert AFTERNOON.contains(at(15, 59)) is False


def test_exactly_at_start_is_on_peak():
    assert AFTERNOON.contains(at(16, 0)) is True


def test_midway_is_on_peak():
    assert AFTERNOON.contains(at(18, 30)) is True


def test_exactly_at_end_is_off_peak():
    """Half-open interval: the window is over the instant it ends."""
    assert AFTERNOON.contains(at(21, 0)) is False


def test_after_end_is_off_peak():
    assert AFTERNOON.contains(at(22, 0)) is False


def test_contains_accepts_a_bare_time():
    assert AFTERNOON.contains(time(17, 0)) is True


# --- containment, overnight window -----------------------------------------


def test_overnight_window_wraps():
    assert OVERNIGHT.wraps_midnight is True
    assert AFTERNOON.wraps_midnight is False


def test_overnight_evening_is_on_peak():
    assert OVERNIGHT.contains(at(22, 0)) is True


def test_overnight_small_hours_are_on_peak():
    assert OVERNIGHT.contains(at(2, 0)) is True


def test_overnight_midday_is_off_peak():
    assert OVERNIGHT.contains(at(12, 0)) is False


def test_overnight_exactly_at_end_is_off_peak():
    assert OVERNIGHT.contains(at(6, 0)) is False


def test_degenerate_window_is_never_active():
    """Never rather than always: a mis-saved window should not hold Eco all day."""
    same = Window(start=time(16, 0), end=time(16, 0))
    assert same.contains(at(16, 0)) is False
    assert same.contains(at(3, 0)) is False


# --- next occurrence -------------------------------------------------------


def test_next_start_later_today():
    assert AFTERNOON.next_start(at(9, 0)) == at(16, 0)


def test_next_start_rolls_to_tomorrow_once_passed():
    assert AFTERNOON.next_start(at(18, 0)) == datetime(2026, 8, 29, 16, 0)


def test_next_start_at_the_boundary_is_now():
    assert AFTERNOON.next_start(at(16, 0)) == at(16, 0)


def test_next_end_later_today():
    assert AFTERNOON.next_end(at(18, 0)) == at(21, 0)


def test_next_end_rolls_to_tomorrow_once_passed():
    assert AFTERNOON.next_end(at(22, 0)) == datetime(2026, 8, 29, 21, 0)


def test_next_start_drops_sub_second_precision():
    now = datetime(2026, 8, 28, 9, 0, 30, 500000)
    assert AFTERNOON.next_start(now) == at(16, 0)


# --- blank windows ---------------------------------------------------------


def test_a_blank_window_is_none():
    """Blank times are how "I trigger this one myself" is stored."""
    assert Window.from_dict({"start": "", "end": ""}) is None


def test_half_a_window_is_none():
    assert Window.from_dict({"start": "22:00:00", "end": ""}) is None


def test_a_missing_window_is_none():
    assert Window.from_dict({}) is None


# --- preload arithmetic ----------------------------------------------------


def test_minus_minutes_subtracts_within_the_day():
    assert minus_minutes(time(16, 0), 60) == time(15, 0)


def test_minus_minutes_wraps_past_midnight():
    assert minus_minutes(time(0, 30), 60) == time(23, 30)


def test_minus_minutes_keeps_seconds():
    assert minus_minutes(time(16, 0, 30), 90) == time(14, 30, 30)


# --- resolution ------------------------------------------------------------


def test_nothing_scheduled_resolves_to_comfort():
    assert Schedule().resolve(at(18, 0)) == "comfort"


def test_nothing_scheduled_does_not_move_home():
    assert Schedule().moves_home is False


def test_any_window_moves_home():
    assert Schedule(sleep=NIGHT).moves_home is True


def test_peak_resolves_to_eco():
    assert Schedule(peak=AFTERNOON).resolve(at(18, 0)) == "eco"


def test_outside_peak_resolves_to_comfort():
    assert Schedule(peak=AFTERNOON).resolve(at(12, 0)) == "comfort"


def test_preload_resolves_to_boost():
    assert Schedule(peak=AFTERNOON, preload=PRELOAD).resolve(at(15, 30)) == "boost"


def test_preload_hands_over_at_peak_start():
    """The preload window is half-open, so peak owns its own first minute."""
    schedule = Schedule(peak=AFTERNOON, preload=PRELOAD)
    assert schedule.resolve(at(15, 59)) == "boost"
    assert schedule.resolve(at(16, 0)) == "eco"


def test_sleep_resolves_to_sleep():
    assert Schedule(sleep=NIGHT).resolve(at(2, 0)) == "sleep"


def test_sleep_outranks_peak():
    """A night peak loses to the sleep band, on purpose."""
    schedule = Schedule(peak=OVERNIGHT, sleep=NIGHT)
    assert schedule.resolve(at(2, 0)) == "sleep"


def test_peak_owns_the_evening_before_sleep_starts():
    """Sleep only outranks peak where they actually overlap."""
    schedule = Schedule(peak=OVERNIGHT, sleep=NIGHT)
    assert schedule.resolve(at(21, 0)) == "eco"


def test_comfort_returns_once_both_windows_have_closed():
    schedule = Schedule(peak=OVERNIGHT, sleep=NIGHT)
    assert schedule.resolve(at(6, 45)) == "comfort"


def test_peak_outranks_preload_if_they_ever_overlap():
    overlapping = Window(start=time(15, 0), end=time(17, 0))
    schedule = Schedule(peak=AFTERNOON, preload=overlapping)
    assert schedule.resolve(at(16, 30)) == "eco"


# --- round trip ------------------------------------------------------------


def test_to_dict_from_dict_round_trip():
    assert Window.from_dict(AFTERNOON.to_dict()) == AFTERNOON


def test_from_dict_accepts_short_time_strings():
    assert Window.from_dict({"start": "16:00", "end": "21:00"}) == AFTERNOON
