"""API integration tests; ALL activity/coordinate fixtures here are SYNTHETIC.

Fixtures test software contracts only and must never be exposed as research data,
training examples in the application, measured evidence, or an accuracy claim.
"""

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from herbfold.api import create_app
from herbfold.chemistry import canonical_smiles


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("HERBFOLD_API_TOKEN", raising=False)
    monkeypatch.setenv("HERBFOLD_ALLOWED_HOSTS", "testserver,localhost")
    with TestClient(create_app(tmp_path / "science-api-test")) as test_client:
        yield test_client


def synthetic_records():
    """Made-up test labels, explicitly isolated from the production catalog."""
    molecules = [
        "c1ccccc1",
        "Cc1ccccc1",
        "c1ccncc1",
        "Cc1ccncc1",
        "c1cncnc1",
        "Cc1cncnc1",
        "c1ccsc1",
        "Cc1ccsc1",
        "c1ccoc1",
        "Cc1ccoc1",
        "C1CCCCC1",
        "CC1CCCCC1",
        "N1CCCCC1",
        "CN1CCCCC1",
        "c1ncc[nH]1",
        "Cc1ncc[nH]1",
    ]
    return [
        {
            "smiles": smiles,
            "target_id": "SYNTHETIC_TEST_TARGET",
            "endpoint": "Kd",
            "value": 10 * (i + 1),
            "unit": "nM",
            "relation": "=",
            "is_measured": True,
            "source": "test://synthetic-fixture-not-scientific-data",
            "assay_id": "SYNTHETIC_TEST_ASSAY",
        }
        for i, smiles in enumerate(molecules)
    ]


def post_ok(client, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_catalog_descriptor_comparison_and_candidate_api_flow(client):
    catalog = client.get("/api/catalog").json()
    assert len(catalog) == 7
    assert all("value" not in row and "affinity" not in row and "is_measured" not in row for row in catalog)
    assert all("test://" not in json.dumps(row) for row in catalog)
    lookup = {row["id"]: row for row in catalog}
    selected = [lookup[name] for name in ("quercetin", "aspirin")]
    descriptor = post_ok(client, "/api/molecules/describe", {"smiles": selected[1]["smiles"]})
    assert descriptor["formula"] == "C9H8O4"
    assert descriptor["molecular_weight"] == pytest.approx(180.159, abs=0.01)
    comparison = post_ok(client, "/api/compare", {"compounds": selected})
    assert len(comparison) == 1
    assert 0 <= comparison[0]["tanimoto"] <= 1
    assert "Structural similarity only" in comparison[0]["interpretation"]
    candidates = post_ok(
        client,
        "/api/candidates",
        {
            "parent_smiles": [row["smiles"] for row in selected],
            "max_candidates": 3,
        },
    )
    assert 0 < len(candidates) <= 3
    parent_set = {canonical_smiles(row["smiles"]) for row in selected}
    for candidate in candidates:
        assert candidate["smiles"] not in parent_set
        assert set(candidate["parents"]) == parent_set
        assert candidate["novel_relative_to_inputs"] is True
        assert candidate["status"] == "unvalidated_computational_candidate"
        assert "affinity" not in candidate and "predicted_pactivity" not in candidate


def test_persisted_workflow_exports_af3_inputs_without_inventing_results(client):
    job = post_ok(
        client,
        "/api/workflows",
        {
            "compound_ids": ["quercetin", "aspirin"],
            "max_candidates": 3,
            "protein_sequence": "ACDEFGHIKLMNPQRSTVWY",
            "target_id": "SYNTHETIC_SEQUENCE_INPUT_ONLY",
        },
    )
    assert job["kind"] == "discovery" and job["status"] == "completed"
    result = job["result"]
    assert result["affinity"] is None
    assert len(result["af3_inputs"]) == len(result["compounds"]) + len(result["candidates"])
    assert all(data["dialect"] == "alphafold3" and data["version"] == 4 for data in result["af3_inputs"])
    assert all(data["modelSeeds"] == [1, 2, 3] for data in result["af3_inputs"])
    assert "models" not in result
    saved = client.get(f"/api/jobs/{job['id']}/artifacts/discovery.json")
    assert saved.status_code == 200 and saved.json() == result
    assert client.get(f"/api/jobs/{job['id']}").json()["result"] == result


def test_every_multi_parent_herbal_workflow_candidate_has_herbal_and_drug_ancestry(client):
    catalog = {row["id"]: row for row in client.get("/api/catalog").json()}
    job = post_ok(
        client,
        "/api/workflows",
        {
            "compound_ids": ["quercetin", "aspirin", "ibuprofen"],
            "max_candidates": 8,
        },
    )
    herbal = {canonical_smiles(catalog["quercetin"]["smiles"])}
    drugs = {canonical_smiles(catalog[name]["smiles"]) for name in ("aspirin", "ibuprofen")}
    assert job["result"]["candidates"]
    for candidate in job["result"]["candidates"]:
        parents = set(candidate["parents"])
        assert parents & herbal, (
            "Herbal workflow must not label drug–drug BRICS products as herbal-derived candidates"
        )
        assert parents & drugs


@pytest.mark.parametrize("ids", [["quercetin", "luteolin"], ["aspirin", "ibuprofen"]])
def test_workflow_requires_both_evidence_classes(client, ids):
    response = client.post("/api/workflows", json={"compound_ids": ids})
    assert response.status_code == 422
    assert client.get("/api/jobs").json() == []


def test_csv_to_evaluation_training_artifact_and_prediction_flow(client):
    records = synthetic_records()
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(records)
    imported = client.post("/api/data/csv", content=buffer.getvalue(), headers={"Content-Type": "text/csv"})
    assert imported.status_code == 200
    imported_records = imported.json()["records"]
    assert len(imported_records) == len(records)
    assert all(row["is_measured"] is True for row in imported_records)
    request = {"records": imported_records, "endpoint": "Kd", "split": "scaffold", "seed": 42}
    evaluation = post_ok(client, "/api/benchmark/evaluate", request)
    result = evaluation["result"]
    assert result["metric_unit"] == "pKd"
    assert result["overlap_audit"]["scaffolds"] == []
    assert result["overlap_audit"]["ligand_target_pairs"] == []
    assert result["overlap_audit"]["targets"] == ["SYNTHETIC_TEST_TARGET"]
    assert set(result["train_indices"]).isdisjoint(result["test_indices"])
    # Do not assert an accuracy threshold on made-up labels: only the endpoint contract.
    assert result["train_count"] + result["test_count"] == len(records)
    trained = post_ok(client, "/api/models/train", request)
    assert trained["status"] == "completed" and trained["kind"] == "model"
    model_id = trained["result"]["model_id"]
    artifact = client.get(f"/api/jobs/{model_id}/artifacts/model.json")
    assert artifact.status_code == 200
    model = artifact.json()
    assert model["format"] == "herbfold-ridge-v1"
    assert model["endpoint"] == "Kd" and isinstance(model["coefficients"], list)
    json.dumps(model, allow_nan=False)
    prediction = post_ok(
        client,
        "/api/models/predict",
        {
            "model_id": model_id,
            "queries": [{"smiles": "Oc1ccccc1", "target_id": "SYNTHETIC_TEST_TARGET"}],
        },
    )
    row = prediction["predictions"][0]
    assert row["is_measured"] is False and row["unit"] == "pKd"
    assert row["endpoint"] == "Kd" and "predicted_pactivity" in row
    assert "measured_pactivity" not in row
    assert prediction["model_training_sha256"] == model["training_sha256"]
    unseen = client.post(
        "/api/models/predict",
        json={
            "model_id": model_id,
            "queries": [{"smiles": "CCO", "target_id": "UNSEEN_TEST_TARGET"}],
        },
    )
    assert unseen.status_code == 422 and "Unseen target" in unseen.json()["detail"]


@pytest.mark.parametrize(
    "change",
    [
        {"is_measured": False},
        {"relation": "<"},
        {"source": ""},
        {"unit": "ng/mL"},
    ],
)
def test_training_rejects_invalid_or_predicted_labels_without_creating_model(client, change):
    records = synthetic_records()
    records[0].update(change)
    response = client.post("/api/models/train", json={"records": records, "split": "scaffold"})
    assert response.status_code == 422
    assert client.get("/api/jobs").json() == []


def test_duplicate_experiments_and_mixed_endpoints_have_explicit_outcomes(client):
    records = synthetic_records()
    duplicate = client.post(
        "/api/benchmark/evaluate",
        json={
            "records": records + [{**records[0], "smiles": "C1=CC=CC=C1", "assay_id": "ANOTHER_ASSAY"}],
            "split": "scaffold",
        },
    )
    assert duplicate.status_code == 422 and "Duplicate" in duplicate.json()["detail"]
    mixed = post_ok(
        client,
        "/api/benchmark/evaluate",
        {
            "records": records + [{**records[0], "endpoint": "IC50", "value": 9999}],
            "endpoint": "Kd",
            "split": "scaffold",
        },
    )
    assert mixed["result"]["data_audit"]["excluded_other_endpoints"] == {"IC50": 1}
    assert mixed["result"]["data_audit"]["retained_count"] == len(records)
    assert all(row["endpoint"] == "Kd" for row in mixed["result"]["test_predictions"])


def test_invalid_utf8_csv_returns_validation_error(client):
    response = client.post(
        "/api/data/csv", content=b"smiles,source\nCCO,\xff\xfe\n", headers={"Content-Type": "text/csv"}
    )
    assert response.status_code == 422


def test_imported_synthetic_structure_preserves_confidence_and_geometric_feature_boundary(client):
    # Four made-up atoms are sufficient to test import/contact arithmetic; no AF3 inference occurred.
    cif = """data_synthetic_api_fixture
loop_
_atom_site.id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.type_symbol
_atom_site.pdbx_PDB_model_num
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
1 A 1 C 1 0 0 0
2 A 1 N 1 0 1 0
3 B . C 1 3 0 0
4 B . O 1 3 1 0
"""
    confidence = {
        "ptm": 0.7,
        "iptm": 0.8,
        "ranking_score": 0.78,
        "has_clash": False,
        "fraction_disordered": 0,
    }
    imported = post_ok(
        client,
        "/api/af3/import",
        {
            "files": {
                "synthetic_summary_confidences.json": json.dumps(confidence),
                "synthetic_model.cif": cif,
            }
        },
    )
    assert imported["status"] == "completed" and imported["kind"] == "af3_import"
    result = imported["result"]
    assert result["execution_verified"] is False
    assert result["affinity_prediction"] is None
    assert result["models"][0]["metrics"]["iptm"] == 0.8
    pocket = client.get(f"/api/af3/{imported['id']}/pocket", params={"file": "output/synthetic_model.cif"})
    assert pocket.status_code == 200, pocket.text
    features = pocket.json()["structure_features"]
    assert features["contact_pairs"] == 4
    assert features["pocket_residues"] == 1
    assert features["min_distance_angstrom"] == 3.0
    assert features["ligand_contact_fraction"] == 1.0
    assert features["close_atom_pairs"] == 0
    assert "affinity" not in features and "energy" not in features


def test_import_rejects_path_traversal_before_creating_a_job(client):
    response = client.post("/api/af3/import", json={"files": {"../escape_model.cif": "data_bad"}})
    assert response.status_code == 422
    assert client.get("/api/jobs").json() == []
