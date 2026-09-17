"""Synthetic arithmetic fixtures; never exported as biological measurements."""

import pytest
from pydantic import ValidationError

from herbfold.combination_assay import CombinationAssayRequest, evaluate_combination


def request(**updates):
    value = dict(component_a="SYNTHETIC A", component_b="SYNTHETIC B",
                 assay_context="synthetic unit-test assay", source="test fixture, not measurements",
                 matched_conditions=True,
                 points=[dict(concentration_a=1, concentration_b=2,
                              inhibition_a=.2, inhibition_b=.3, inhibition_combination=.5)])
    value.update(updates)
    return CombinationAssayRequest(**value)


def test_reference_models_and_units_are_distinct():
    result = evaluate_combination(request())
    point = result["points"][0]
    assert point["bliss_expected"] == pytest.approx(.44)
    assert point["hsa_expected"] == .3
    assert point["bliss_excess_percentage_points"] == pytest.approx(6)
    assert point["hsa_excess_percentage_points"] == pytest.approx(20)
    assert result["source_verified"] is False
    assert result["input"]["source"] == "test fixture, not measurements"


@pytest.mark.parametrize("a,b,observed,bliss,excess", [
    (0, 0, 0, 0, 0), (1, 0, 1, 1, 0), (.5, .5, .5, .75, -25), (1, 1, 1, 1, 0),
])
def test_valid_zero_saturation_and_below_reference(a, b, observed, bliss, excess):
    result = evaluate_combination(request(points=[dict(concentration_a=0, concentration_b=0,
        inhibition_a=a, inhibition_b=b, inhibition_combination=observed)]))["points"][0]
    assert result["bliss_expected"] == bliss
    assert result["bliss_excess_percentage_points"] == excess


@pytest.mark.parametrize("invalid", [-.1, 1.1, float("nan"), float("inf"), True, "0.5"])
def test_invalid_observations_are_rejected_not_clipped(invalid):
    with pytest.raises(ValidationError):
        request(points=[dict(concentration_a=1, concentration_b=1,
            inhibition_a=invalid, inhibition_b=0, inhibition_combination=0)])


@pytest.mark.parametrize("changes", [{"source": " "}, {"matched_conditions": False},
    {"matched_conditions": 1}, {"matched_conditions": 1.0}, {"matched_conditions": "true"}, {"points": []}])
def test_requires_source_and_matched_assay_context(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_same_ingredient_is_not_an_independent_drug_pair():
    with pytest.raises(ValueError):
        evaluate_combination(request(component_b="synthetic a"))
