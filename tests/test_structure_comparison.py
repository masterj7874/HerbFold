"""Local structure comparison contracts; no AF3, affinity or efficacy claims."""

from itertools import permutations

import pytest
from fastapi.testclient import TestClient

from herbfold.api import create_app
from herbfold.chemistry import compare_structures


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("HERBFOLD_API_TOKEN", raising=False)
    monkeypatch.setenv("HERBFOLD_ALLOWED_HOSTS", "testserver,localhost")
    with TestClient(create_app(tmp_path / "comparison-api-test")) as test_client:
        yield test_client


def selected_compounds():
    return [
        {"id": "b", "name": "Ethanol", "smiles": "CCO", "category": "natural_product"},
        {"id": "a", "name": "Propane", "smiles": "CCC", "category": "natural_product"},
        {"id": "c", "name": "Ethanol alias", "smiles": "OCC", "category": "natural_product"},
        {"id": "d", "name": "Acetic acid", "smiles": "CC(=O)O", "category": "natural_product"},
    ]


def test_all_pairs_are_category_independent_reproducible_and_keep_aliases():
    compounds = selected_compounds()
    result = compare_structures(compounds)
    assert len(result) == 6
    assert len({(row["left_id"], row["right_id"]) for row in result}) == 6
    assert all(row["left_id"] < row["right_id"] for row in result)
    assert all(0 <= row["tanimoto"] <= 1 for row in result)
    assert all("affinity" not in row and "efficacy" not in row for row in result)
    alias = result[0]
    assert (alias["left_id"], alias["right_id"]) == ("b", "c")
    assert (alias["left_name"], alias["right_name"]) == ("Ethanol", "Ethanol alias")
    assert alias["tanimoto"] == 1 and alias["identical_structure"] is True
    assert sum(row["identical_structure"] for row in result) == 1
    for reordered in permutations(compounds):
        assert compare_structures(list(reordered)) == result
    assert compare_structures([{**row, "category": "drug"} for row in compounds]) == result
    assert (
        compare_structures(
            [{key: value for key, value in row.items() if key != "category"} for row in compounds]
        )
        == result
    )


@pytest.mark.parametrize(
    ("left", "right", "identical"),
    [
        ("[Na+].CC(=O)[O-]", "CC(=O)[O-]", True),
        ("CC(=O)[O-]", "CC(=O)O", False),
        ("C[C@H](O)C(=O)O", "C[C@@H](O)C(=O)O", False),
    ],
)
def test_identity_follows_existing_fragment_charge_and_stereo_rules(left, right, identical):
    row = compare_structures([{"id": "a", "smiles": left}, {"id": "b", "smiles": right}])[0]
    assert row["identical_structure"] is identical
    assert (row["left_canonical_smiles"] == row["right_canonical_smiles"]) is identical
    assert "charge/stereo retained" in row["standardization"]
    assert "standardized structures" in row["interpretation"]
    if identical:
        assert row["tanimoto"] == 1
    else:
        assert row["tanimoto"] < 1


def test_fingerprint_identity_does_not_imply_identical_structure():
    row = compare_structures([{"id": "decane", "smiles": "C" * 10}, {"id": "undecane", "smiles": "C" * 11}])[
        0
    ]
    assert row["tanimoto"] == 1
    assert row["identical_structure"] is False
    assert row["left_name"] == "decane" and row["right_name"] == "undecane"


def test_structure_api_compares_same_category_without_creating_workflow(client):
    compounds = selected_compounds()
    response = client.post("/api/compare/structures", json={"compounds": compounds})
    assert response.status_code == 200, response.text
    assert response.json() == compare_structures(compounds)
    assert client.get("/api/jobs").json() == []
    legacy = client.post("/api/compare", json={"compounds": compounds})
    assert legacy.status_code == 422
    compounds[0]["category"] = "drug"
    legacy = client.post("/api/compare", json={"compounds": compounds})
    assert legacy.status_code == 200
    assert len(legacy.json()) == 3
    assert all(row["drug_id"] == "b" and "herbal_id" in row for row in legacy.json())


@pytest.mark.parametrize(
    "compounds",
    [
        [],
        [{"id": "a", "smiles": "CCO"}],
        [{"id": str(index), "smiles": "CCO"} for index in range(9)],
        [{"id": "a", "smiles": "CCO"}, {"id": "a", "smiles": "CCC"}],
        [{"id": "a", "smiles": "CCO"}, {"smiles": "CCC"}],
        [{"id": "a", "smiles": "CCO"}, {"id": " ", "smiles": "CCC"}],
        [{"id": "a", "smiles": "CCO"}, {"id": " b", "smiles": "CCC"}],
        [{"id": "a", "smiles": "CCO"}, {"id": 2, "smiles": "CCC"}],
        [{"id": "a", "smiles": "CCO"}, {"id": "b", "smiles": "invalid"}],
        [{"id": "a", "smiles": "CCO"}, {"id": "b"}],
        [{"id": "a", "smiles": "CCO"}, {"id": "b", "name": None, "smiles": "CCC"}],
        [{"id": "a", "smiles": "CCO"}, {"id": "b", "name": " ", "smiles": "CCC"}],
        [{"id": "a", "smiles": "CCO"}, "CCC"],
    ],
)
def test_structure_comparison_rejects_invalid_identity_structure_or_size(client, compounds):
    with pytest.raises(ValueError):
        compare_structures(compounds)
    response = client.post("/api/compare/structures", json={"compounds": compounds})
    assert response.status_code == 422, response.text
    assert client.get("/api/jobs").json() == []


def test_structure_api_supports_eight_selected_ids_without_deduplicating_chemicals(client):
    response = client.post(
        "/api/compare/structures",
        json={"compounds": [{"id": str(index), "smiles": "CCO"} for index in range(8)]},
    )
    assert response.status_code == 200
    assert len(response.json()) == 28
    assert all(row["identical_structure"] for row in response.json())
