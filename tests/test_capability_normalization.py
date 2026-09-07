import pytest

from src.utils.capability_normalization import (
    UnknownCapabilityName,
    is_equipment_name,
    normalize_capability_name,
    require_canonical_name,
)


def test_forno_normalizes_to_oven() -> None:
    assert normalize_capability_name("equipment", "forno") == "oven"
    assert normalize_capability_name("equipment", "OVEN") == "oven"


def test_operational_aliases() -> None:
    assert normalize_capability_name("operational", "gás") == "gas_or_electric"
    assert normalize_capability_name("operational", "geladeira") == "fridge_space"
    assert normalize_capability_name("operational", "bocas") == "stove_burners"


def test_skills_remain_free_form() -> None:
    assert normalize_capability_name("skill", "empanar e fritar") == "empanar e fritar"


def test_is_equipment_name() -> None:
    assert is_equipment_name("forno") is True
    assert is_equipment_name("gas_or_electric") is False


def test_unknown_names_are_rejected() -> None:
    with pytest.raises(UnknownCapabilityName):
        normalize_capability_name("equipment", "counter_space medium")
    with pytest.raises(UnknownCapabilityName):
        normalize_capability_name("operational", "counter_space medium")
    with pytest.raises(UnknownCapabilityName):
        require_canonical_name("operational", "counter_space medium")
