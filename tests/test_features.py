"""Geometry integration fixtures are synthetic and never scientific evidence."""

import hashlib
import json
from pathlib import Path

import pytest

from herbfold.alphafold import build_input
from herbfold.features import enrich_records
from herbfold.storage import Store

SEQUENCE = "ACDEFGHIKLMNPQRSTVWYA"
CIF = """data_test
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


def setup_job(tmp_path, metrics=None):
    store = Store(tmp_path)
    data = build_input(
        "fixture",
        [{"id": "A", "sequence": SEQUENCE}],
        [{"id": "B", "smiles": "C[C@H](O)C(=O)O"}],
        msa_mode="none",
    )
    job = store.create("alphafold", data)
    directory = store.directory(job["id"])
    store.write(job["id"], "fold_input.json", data)
    input_hash = hashlib.sha256((directory / "fold_input.json").read_bytes()).hexdigest()
    store.write(
        job["id"],
        "af3_manifest.json",
        {
            "version": "3.0.4",
            "input_sha256": input_hash,
            "provenance": {"runner": "native", "source_commit": "fixture"},
        },
    )
    (directory / "output").mkdir()
    (directory / "output/model.cif").write_text(CIF)
    result = {
        "execution_verified": True,
        "models": [
            {
                "is_top_ranked_copy": True,
                "structure_path": "model.cif",
                "structure_sha256": hashlib.sha256(CIF.encode()).hexdigest(),
                "metrics": metrics or {"has_clash": False, "iptm": 0.8},
            }
        ],
    }
    store.update(job["id"], "completed", result)
    row = {
        "af3_job_id": job["id"],
        "protein_sequence": SEQUENCE,
        "smiles": "C[C@H](O)C(=O)O",
        "is_measured": True,
        "endpoint": "Kd",
        "value": 123,
        "unit": "nM",
    }
    return store, job["id"], row


def test_enriches_actual_coordinates_and_preserves_measurements(tmp_path):
    store, _, row = setup_job(tmp_path)
    row["smiles"] = "O=C(O)[C@@H](O)C"  # Same stereoisomer, alternate atom order.
    result = enrich_records([row], store)
    output = result["records"][0]
    assert output["structure_features"]["contact_pairs"] == 4
    assert output["structure_features"]["pocket_residues"] == 1
    assert output["value"] == 123 and output["endpoint"] == "Kd"
    assert "structure_features" not in row
    assert output["structure_provenance"]["input_sha256"]
    assert "affinity" in result["confidence_note"]


def test_rejects_missing_or_different_protein_sequence(tmp_path):
    store, _, row = setup_job(tmp_path)
    for sequence in (None, "", SEQUENCE + "A"):
        with pytest.raises(ValueError, match="Row 1:.*protein_sequence"):
            enrich_records([{**row, "protein_sequence": sequence}], store)


def test_rejects_wrong_ligand_and_stereoisomer(tmp_path):
    store, _, row = setup_job(tmp_path)
    for smiles in ("CCO", "C[C@@H](O)C(=O)O", "CC(O)C(=O)O", "C[C@H](O)C(=O)[O-]"):
        with pytest.raises(ValueError, match="smiles differs"):
            enrich_records([{**row, "smiles": smiles}], store)


def test_rejects_imported_unfinished_and_unverified_jobs(tmp_path):
    store, job_id, row = setup_job(tmp_path)
    original = store.get(job_id)
    store.update(job_id, "running", original["result"])
    with pytest.raises(ValueError, match="completed"):
        enrich_records([row], store)
    store.update(job_id, "completed", {**original["result"], "execution_verified": False})
    with pytest.raises(ValueError, match="execution_verified"):
        enrich_records([row], store)
    with store.connect() as connection:
        connection.execute("UPDATE jobs SET kind='af3_import' WHERE id=?", (job_id,))
    store.update(job_id, "completed", original["result"])
    with pytest.raises(ValueError, match="imported"):
        enrich_records([row], store)


def test_quality_gate_rejects_clash_low_or_missing_confidence(tmp_path):
    store, job_id, row = setup_job(tmp_path)
    result = store.get(job_id)["result"]
    for threshold in (True, float("nan"), -0.1, 1.1):
        with pytest.raises(ValueError, match="min_iptm"):
            enrich_records([row], store, min_iptm=threshold)
    for metrics in (
        {"has_clash": True, "iptm": 0.9},
        {"has_clash": False, "iptm": 0.39},
        {"has_clash": False},
        {"has_clash": False, "iptm": "0.9"},
    ):
        result["models"][0]["metrics"] = metrics
        store.update(job_id, "completed", result)
        with pytest.raises(ValueError, match="quality gate"):
            enrich_records([row], store)


def test_rejects_extra_entities_and_invalid_model_selection(tmp_path):
    store, job_id, row = setup_job(tmp_path)
    with pytest.raises(ValueError, match="af3_model_index"):
        enrich_records([{**row, "af3_model_index": True}], store)
    original = store.get(job_id)
    payload = original["payload"]
    payload["sequences"].append({"ligand": {"id": "C", "smiles": "CCO"}})
    with store.connect() as connection:
        connection.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), job_id))
    with pytest.raises(ValueError, match="exactly one"):
        enrich_records([row], store)


def test_rejects_traversal_and_modified_artifacts(tmp_path):
    store, job_id, row = setup_job(tmp_path)
    result = store.get(job_id)["result"]
    result["models"][0]["structure_path"] = "../fold_input.json"
    store.update(job_id, "completed", result)
    with pytest.raises(ValueError, match="traverse"):
        enrich_records([row], store)
    result["models"][0]["structure_path"] = "model.cif"
    store.update(job_id, "completed", result)
    (store.directory(job_id) / "output/model.cif").write_text(CIF + "# modified\n")
    with pytest.raises(ValueError, match="hash differs"):
        enrich_records([row], store)


def test_real_low_quality_smoke_is_rejected_when_available():
    root = Path(__file__).resolve().parents[1] / "runtime"
    job_id = "2e03b13d30d54f69b5582e24c6eceecb"
    if not (root / job_id / "result.json").exists():
        pytest.skip("Optional local real-inference artifact is not distributed with tests")
    store = Store(root)
    job = store.get(job_id)
    row = {
        "af3_job_id": job_id,
        "protein_sequence": job["payload"]["sequences"][0]["protein"]["sequence"],
        "smiles": job["payload"]["sequences"][1]["ligand"]["smiles"],
    }
    eligibility_failed = (
        job["status"] != "completed"
        or (job.get("result") or {}).get("execution_verified") is not True
    )
    if job["status"] == "quarantined":
        assert job["result"]["prediction_eligible"] is False
        assert job["result"].get("parameter_audit")
    expected_gate = "completed with execution_verified=true" if eligibility_failed else "quality gate"
    with pytest.raises(ValueError, match=expected_gate):
        enrich_records([row], store)
