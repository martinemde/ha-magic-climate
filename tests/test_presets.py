import pytest

from custom_components.magic_climate.presets import Preset, PresetValidationError


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


def test_preset_valid_passes_validate():
    Preset(name="Sleep", low=16.0, high=21.0).validate()  # no exception
