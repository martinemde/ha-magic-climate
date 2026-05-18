import pytest

from custom_components.magic_climate.presets import (
    Preset,
    PresetValidationError,
    compute_service_data,
)


def test_preset_basic_construction():
    p = Preset(name="Sleep", low=16.0, high=21.0)
    assert p.name == "Sleep"
    assert p.low == 16.0
    assert p.high == 21.0
    assert p.mode is None
    assert p.fan is None


def test_preset_with_optional_mode_and_fan():
    p = Preset(name="Away", low=14.5, high=28.0, mode="heat_cool", fan="auto")
    assert p.mode == "heat_cool"
    assert p.fan == "auto"


def test_preset_validates_low_below_high():
    with pytest.raises(PresetValidationError, match="low must be < high"):
        Preset(name="Bad", low=22.0, high=20.0).validate()


def test_preset_validates_equal_low_high_rejected():
    with pytest.raises(PresetValidationError, match="low must be < high"):
        Preset(name="Bad", low=22.0, high=22.0).validate()


def test_preset_validates_name_nonempty():
    with pytest.raises(PresetValidationError, match="name must be non-empty"):
        Preset(name="", low=16.0, high=21.0).validate()


def test_preset_validates_name_whitespace_only_rejected():
    with pytest.raises(PresetValidationError, match="name must be non-empty"):
        Preset(name="   ", low=16.0, high=21.0).validate()


def test_preset_valid_passes_validate():
    Preset(name="Sleep", low=16.0, high=21.0).validate()  # no exception


def test_compute_service_data_heat_cool():
    p = Preset(name="Away", low=14.5, high=28.0)
    data = compute_service_data(p, effective_mode="heat_cool")
    assert data == {
        "target_temp_low": 14.5,
        "target_temp_high": 28.0,
    }


def test_compute_service_data_heat_cool_with_mode_override():
    p = Preset(name="Away", low=14.5, high=28.0, mode="heat_cool")
    data = compute_service_data(p, effective_mode="heat_cool")
    assert "hvac_mode" not in data
    assert data["target_temp_low"] == 14.5
    assert data["target_temp_high"] == 28.0


def test_compute_service_data_auto_uses_midpoint():
    p = Preset(name="Sleep", low=16.0, high=21.0)
    data = compute_service_data(p, effective_mode="auto")
    assert data == {"temperature": 18.5}


def test_compute_service_data_auto_asymmetric_band():
    p = Preset(name="Eco", low=15.5, high=26.5)
    data = compute_service_data(p, effective_mode="auto")
    assert data == {"temperature": 21.0}


def test_compute_service_data_heat_uses_low():
    p = Preset(name="Away", low=14.5, high=28.0)
    data = compute_service_data(p, effective_mode="heat")
    assert data == {"temperature": 14.5}


def test_compute_service_data_cool_uses_high():
    p = Preset(name="Away", low=14.5, high=28.0)
    data = compute_service_data(p, effective_mode="cool")
    assert data == {"temperature": 28.0}


def test_compute_service_data_dry_uses_high():
    p = Preset(name="Eco", low=15.5, high=26.5)
    data = compute_service_data(p, effective_mode="dry")
    assert data == {"temperature": 26.5}


def test_compute_service_data_fan_only_no_temp():
    p = Preset(name="Home", low=18.0, high=25.0)
    assert compute_service_data(p, effective_mode="fan_only") == {}


def test_compute_service_data_off_no_temp():
    p = Preset(name="Home", low=18.0, high=25.0)
    assert compute_service_data(p, effective_mode="off") == {}


def test_compute_service_data_unknown_mode_returns_empty():
    """An unrecognized mode is treated as 'don't push temperature'."""
    p = Preset(name="Home", low=18.0, high=25.0)
    assert compute_service_data(p, effective_mode="snowflake") == {}


def test_preset_to_dict():
    p = Preset(name="Sleep", low=16.0, high=21.0, mode="heat_cool", fan="auto")
    assert p.to_dict() == {
        "name": "Sleep",
        "low": 16.0,
        "high": 21.0,
        "mode": "heat_cool",
        "fan": "auto",
    }


def test_preset_to_dict_omits_none_optionals():
    p = Preset(name="Sleep", low=16.0, high=21.0)
    assert p.to_dict() == {"name": "Sleep", "low": 16.0, "high": 21.0}


def test_preset_from_dict():
    p = Preset.from_dict({"name": "Sleep", "low": 16.0, "high": 21.0})
    assert p == Preset(name="Sleep", low=16.0, high=21.0)


def test_preset_from_dict_with_optionals():
    p = Preset.from_dict({
        "name": "Away", "low": 14.5, "high": 28.0,
        "mode": "heat_cool", "fan": "auto",
    })
    assert p == Preset(name="Away", low=14.5, high=28.0, mode="heat_cool", fan="auto")
