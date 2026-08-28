from datetime import datetime, time

import pytest

from custom_components.magic_climate.peak import (
    PeakValidationError,
    PeakWindow,
    format_time,
    parse_time,
    resolve,
)

AFTERNOON = PeakWindow(start=time(16, 0), end=time(21, 0))
OVERNIGHT = PeakWindow(start=time(20, 0), end=time(6, 0))


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
    with pytest.raises(PeakValidationError, match="must differ"):
        PeakWindow(start=time(16, 0), end=time(16, 0)).validate()


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
    same = PeakWindow(start=time(16, 0), end=time(16, 0))
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


# --- substitution ----------------------------------------------------------


def test_home_becomes_eco_during_peak():
    assert resolve("home", "home", "eco", in_peak=True) == "eco"


def test_home_stays_home_off_peak():
    assert resolve("home", "home", "eco", in_peak=False) == "home"


def test_other_presets_are_never_substituted():
    """Only Home is redirected; picking Sleep during peak means Sleep."""
    assert resolve("sleep", "home", "eco", in_peak=True) == "sleep"


def test_eco_itself_is_not_substituted():
    """Choosing Eco deliberately holds Eco, peak or not."""
    assert resolve("eco", "home", "eco", in_peak=True) == "eco"


def test_no_substitute_available_is_the_identity():
    assert resolve("home", "home", None, in_peak=True) == "home"


# --- round trip ------------------------------------------------------------


def test_to_dict_from_dict_round_trip():
    assert PeakWindow.from_dict(AFTERNOON.to_dict()) == AFTERNOON


def test_from_dict_accepts_short_time_strings():
    assert PeakWindow.from_dict({"start": "16:00", "end": "21:00"}) == AFTERNOON
