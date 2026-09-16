"""All numeric activities below are synthetic UNIT-TEST fixtures, not scientific data."""

import copy
import json

import pytest

from herbfold.benchmark import evaluate_records, predict_model, prepare_records, to_pactivity, train_model


def records(with_sequences=True):
    rings = [
        ("c1ccccc1", "Cc1ccccc1"),
        ("c1ccncc1", "Cc1ccncc1"),
        ("c1cncnc1", "Cc1cncnc1"),
        ("c1ccsc1", "Cc1ccsc1"),
        ("c1ccoc1", "Cc1ccoc1"),
        ("C1CCCCC1", "CC1CCCCC1"),
        ("N1CCCCC1", "CN1CCCCC1"),
        ("c1ncc[nH]1", "Cc1ncc[nH]1"),
    ]
    result = []
    for i, pair in enumerate(rings):
        for j, smiles in enumerate(pair):
            row = {
                "smiles": smiles,
                "target_id": f"TEST_TARGET_{i}",
                "endpoint": "Kd",
                "value": 10 * (i + 1) ** 2 + j,
                "unit": "nM",
                "relation": "=",
                "is_measured": True,
                "source": "test://synthetic-fixture-only",
            }
            if with_sequences:
                row["protein_sequence"] = "ACDEFGHIKLMNPQRSTVWY" + "A" * i
            result.append(row)
    return result


def test_endpoint_units_convert_dimensionally():
    assert to_pactivity(1, "nM", "Kd") == 9
    assert to_pactivity(1, "uM", "Ki") == 6
    assert to_pactivity(1000, "pM", "Kd") == 9
    with pytest.raises(ValueError, match="IC50"):
        to_pactivity(1, "nM", "IC50")
    for invalid in (0, -1, float("nan"), float("inf"), True, "<1"):
        with pytest.raises(ValueError):
            to_pactivity(invalid, "nM")
    with pytest.raises(ValueError):
        to_pactivity(1, "ng/mL")


def test_mixed_endpoints_are_never_silently_merged():
    data = records()
    other = {**data[0], "endpoint": "IC50", "value": 1e8}
    normalized, audit = prepare_records(data + [other], "Kd")
    assert len(normalized) == 16
    assert audit["excluded_other_endpoints"] == {"IC50": 1}
    assert normalized[0]["pactivity"] == 8


@pytest.mark.parametrize(
    "change",
    [
        {"relation": "<"},
        {"is_measured": False},
        {"source": ""},
        {"value": "inf"},
        {"unit": "bad"},
    ],
)
def test_unmeasured_censored_or_untraceable_labels_are_rejected(change):
    data = records()
    data[0].update(change)
    with pytest.raises(ValueError):
        train_model(data)


def test_canonical_duplicate_pairs_are_rejected_before_split():
    data = records()
    data.append({**data[0], "smiles": "C1=CC=CC=C1"})
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate_records(data)


def test_combined_split_has_no_scaffold_target_or_ligand_leakage():
    result = evaluate_records(records(), seed=42)
    assert result["train_count"] == 12
    assert result["test_count"] == 4
    assert all(not overlaps for overlaps in result["overlap_audit"].values())
    assert set(result["train_indices"]).isdisjoint(result["test_indices"])
    assert len(result["test_predictions"]) == result["test_count"]
    assert result["metrics"]["rmse"] >= 0
    assert result == evaluate_records(records(), seed=42)
    json.dumps(result, allow_nan=False)


def test_split_rejects_one_connected_group_and_unseen_target_without_sequence():
    data = records(with_sequences=False)
    with pytest.raises(ValueError, match="protein_sequence"):
        evaluate_records(data, split="target")
    for row in data:
        row["target_id"] = "ONE_TARGET"
    with pytest.raises(ValueError, match="disjoint groups"):
        evaluate_records(data)
    result = evaluate_records(data, split="scaffold")
    assert result["overlap_audit"]["scaffolds"] == []
    assert result["overlap_audit"]["targets"] == ["ONE_TARGET"]


def test_json_model_roundtrip_and_known_target_query_contract():
    data = records(with_sequences=False)
    model = train_model(data)
    model = json.loads(json.dumps(model, allow_nan=False))
    query = [{"smiles": "Oc1ccccc1", "target_id": data[0]["target_id"]}]
    output = predict_model(model, query)
    assert output["predictions"][0]["is_measured"] is False
    assert output["predictions"][0]["unit"] == "pKd"
    assert output == predict_model(model, query)
    with pytest.raises(ValueError, match="Unseen target"):
        predict_model(model, [{**query[0], "target_id": "UNSEEN"}])
    model["coefficients"][0] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        predict_model(model, query)


def test_structure_features_schema_and_target_construct_must_match():
    data = records()
    for i, row in enumerate(data):
        row["structure_features"] = {"contact_pairs": i + 2, "min_distance_angstrom": 2.4}
    model = train_model(data)
    query = copy.deepcopy(data[0])
    assert predict_model(model, [query])["predictions"]
    query["structure_features"].pop("contact_pairs")
    with pytest.raises(ValueError, match="schema"):
        predict_model(model, [query])
    query = copy.deepcopy(data[0])
    query["protein_sequence"] += "A"
    with pytest.raises(ValueError, match="construct"):
        predict_model(model, [query])
