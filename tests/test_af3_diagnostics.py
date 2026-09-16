"""Synthetic CPU artifacts exercise exact sample selection and confidence scope."""

import hashlib
import json
from collections import Counter
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from rdkit import Chem

from herbfold import af3_diagnostics, alphafold, molecular_selection
from herbfold.af3_studio import StudioPredictions, make_router
from herbfold.storage import Store


def write_sample(output, name, smiles="CCO", protein=("ALA", "CYS", "ASP"), confidence=70):
    output.mkdir(parents=True, exist_ok=True)
    columns = (
        "type_symbol",
        "label_atom_id",
        "label_comp_id",
        "label_asym_id",
        "label_seq_id",
        "auth_seq_id",
        "Cartn_x",
        "Cartn_y",
        "Cartn_z",
        "B_iso_or_equiv",
    )
    text = f"data_synthetic_only\n_chem_comp.id LIG_B\n_chem_comp.pdbx_smiles '{smiles}'\nloop_\n"
    text += "\n".join("_atom_site." + key for key in columns) + "\n"
    rows = [
        ("C", "CA", residue, "A", i, i, i * 4, 0, 0, confidence - 10) for i, residue in enumerate(protein, 1)
    ]
    seen = Counter()
    ligand = Chem.MolFromSmiles(smiles)
    for i, atom in enumerate(ligand.GetAtoms()):
        element = atom.GetSymbol()
        seen[element] += 1
        rows.append((element, element + str(seen[element]), "LIG_B", "B", ".", 1, i * 1.4, 4, 0, confidence))
    text += "\n".join(" ".join(map(str, row)) for row in rows) + "\n#\n"
    (output / (name + "_model.cif")).write_text(text)
    n_protein, n_ligand = len(protein), ligand.GetNumAtoms()
    count = n_protein + n_ligand
    matrix = np.ones((count, count))
    matrix[:n_protein, n_protein:] = 2
    matrix[n_protein:, :n_protein] = 5
    summary = {
        "ptm": confidence / 100,
        "iptm": 0.3,
        "ranking_score": confidence / 100,
        "has_clash": False,
        "chain_ids": ["A", "B"],
        "chain_pair_pae_min": [[1, 2], [5, 1]],
    }
    (output / (name + "_summary_confidences.json")).write_text(json.dumps(summary))
    full = {
        "atom_chain_ids": ["A"] * n_protein + ["B"] * n_ligand,
        "atom_plddts": [confidence - 10] * n_protein + [confidence] * n_ligand,
        "token_chain_ids": ["A"] * n_protein + ["B"] * n_ligand,
        "token_res_ids": list(range(1, n_protein + 1)) + [1] * n_ligand,
        "pae": matrix.tolist(),
    }
    (output / (name + "_confidences.json")).write_text(json.dumps(full))


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(
        molecular_selection, "_target_sequence", lambda acc: "ACD" if acc == "P35354" else None
    )
    store = Store(tmp_path / "runtime")
    service = StudioPredictions(store, SimpleNamespace())
    payload = {
        "sequences": [{"protein": {"id": "A", "sequence": "ACD"}}, {"ligand": {"id": "B", "smiles": "CCO"}}]
    }
    job = store.create("alphafold", payload)
    output = store.directory(job["id"]) / "output"
    write_sample(output, "synthetic")
    write_sample(output / "seed-1_sample-0", "synthetic_seed-1_sample-0", confidence=80)
    result = alphafold.parse_outputs(output)
    result["execution_verified"] = True  # synthetic registry flag, no AF3/GPU run
    store.update(job["id"], "completed", result)
    requested = {
        "canonical_smiles": "CCO",
        "target_accession": "P35354",
        "msa_mode": "none",
        "target_sequence_sha256": hashlib.sha256(b"ACD").hexdigest(),
    }
    with store.connect() as con:
        con.execute(
            "INSERT INTO studio_predictions VALUES (?,?,?,?,?,?)",
            (job["id"], "test", "CCO", "P35354", json.dumps(requested), None),
        )
    return SimpleNamespace(store=store, service=service, job_id=job["id"], output=output, requested=requested)


def rewrite_confidence(fixture, mutate):
    path = fixture.output / "synthetic_confidences.json"
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def test_exact_selected_sample_stats_and_directional_pae(fixture):
    value = fixture.service.diagnostics(fixture.job_id, smiles="OCC", target_accession="P35354")
    assert value["status"] == "available" and value["identity_verified"]
    assert value["sample"]["is_top_ranked_copy"] is True
    assert value["summary_metrics"]["ptm"] == 0.7
    assert value["plddt"]["selected_ligand"] == {"count": 3, "min": 70, "mean": 70, "max": 70}
    assert value["plddt"]["protein"]["mean"] == 60
    assert value["pae"]["token_count"] == 6
    pairs = {(row["frame_chain"], row["target_chain"]): row for row in value["pae"]["chain_pairs"]}
    assert pairs["A", "B"]["mean"] == 2 and pairs["B", "A"]["mean"] == 5
    assert pairs["A", "B"]["count"] == 9 and pairs["A", "B"]["frame_is_target"]
    assert value["pae"]["identity_checks"]["registered_confidence_sha256"] is False
    assert "do not independently authenticate" in value["pae"]["integrity_scope"]
    selected = fixture.service.diagnostics(
        fixture.job_id, file="output/seed-1_sample-0/synthetic_seed-1_sample-0_model.cif"
    )
    assert selected["sample"] == {"seed": 1, "sample": 0, "is_top_ranked_copy": False}
    assert selected["summary_metrics"]["ptm"] == 0.8
    assert selected["plddt"]["selected_ligand"]["mean"] == 80
    assert selected["structure_sha256"] != value["structure_sha256"]
    assert selected["pae"]["artifact"] != value["pae"]["artifact"]


@pytest.mark.parametrize(
    "selection",
    [
        {"smiles": "CCN"},
        {"smiles": "CC[O-]"},
        {"target_accession": "Q05769"},
        {"file": "../other/model.cif"},
        {"file": "output/unregistered_model.cif"},
    ],
)
def test_wrong_selection_never_borrows_another_job_or_sample(fixture, selection):
    with pytest.raises(HTTPException) as error:
        fixture.service.diagnostics(fixture.job_id, **selection)
    assert error.value.status_code == 409


def test_changed_structure_hash_and_wrong_target_sequence_abstain(fixture):
    path = fixture.output / "synthetic_model.cif"
    path.write_text(path.read_text() + "# modified\n")
    assert fixture.service.diagnostics(fixture.job_id)["status"] == "unavailable"
    wrong = {**fixture.requested, "target_sequence_sha256": "0" * 64}
    assert (
        af3_diagnostics.selected_diagnostics(fixture.store, fixture.job_id, wrong)["status"] == "unavailable"
    )


def test_non_cox_saved_structure_and_diagnostics_use_frozen_input_not_current_registry(fixture, monkeypatch):
    requested = {
        **fixture.requested,
        "target_accession": "P23219",
        "target_provenance": {
            "accession": "P23219", "sequence_sha256": hashlib.sha256(b"ACD").hexdigest(),
            "source": "synthetic_test_fixture", "length": 3,
        },
    }
    with fixture.store.connect() as con:
        con.execute("UPDATE studio_predictions SET target_accession=?,request=? WHERE job_id=?", (
            "P23219", json.dumps(requested), fixture.job_id,
        ))
    monkeypatch.setattr(molecular_selection, "registered_target", lambda *args: (_ for _ in ()).throw(
        AssertionError("An exact historical job must not reread the current registry")
    ))
    scene = fixture.service.scene(fixture.job_id)
    assert scene["status"] == "matched"
    assert scene["scene"]["metadata"]["selection"]["target_sequence_sha256"] == requested["target_sequence_sha256"]
    diagnostics = fixture.service.diagnostics(fixture.job_id, target_accession="P23219")
    assert diagnostics["status"] == "available" and diagnostics["identity_verified"]
    assert diagnostics["pae"]["status"] == "available"
    assert fixture.service.validate_output(fixture.job_id)["identity_verified"] is True
    # A future target revision, and even a missing registry entry, do not rewrite historical identity.
    for current in [{"accession": "P23219", "sequence": "AAA"}, None]:
        monkeypatch.setattr(molecular_selection, "registered_target", lambda *args: current)
        found = molecular_selection.resolve(fixture.store, "CCO", "alphafold3_prediction", "P23219")
        assert found["status"] == "matched"
        assert found["matches"][0]["job_id"] == fixture.job_id
    wrong_target = molecular_selection.resolve(fixture.store, "CCO", "alphafold3_prediction", "P35354")
    assert wrong_target["status"] == "unavailable"
    wrong_ligand = molecular_selection.resolve(fixture.store, "CCN", "alphafold3_prediction", "P23219")
    assert wrong_ligand["status"] == "unavailable"
    # Original input tampering cannot borrow today's reference or the otherwise matching output.
    with fixture.store.connect() as con:
        job = fixture.store.get(fixture.job_id)
        job["payload"]["sequences"][0]["protein"]["sequence"] = "AAA"
        con.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(job["payload"]), fixture.job_id))
    assert fixture.service.scene(fixture.job_id)["status"] == "unavailable"
    assert fixture.service.diagnostics(fixture.job_id)["status"] == "unavailable"


def test_wrong_selected_sample_identity_does_not_fall_back_to_good_top(fixture):
    write_sample(fixture.output / "seed-1_sample-0", "synthetic_seed-1_sample-0", smiles="CCN")
    result = alphafold.parse_outputs(fixture.output)
    result["execution_verified"] = True
    fixture.store.update(fixture.job_id, "completed", result)
    value = fixture.service.diagnostics(
        fixture.job_id, file="output/seed-1_sample-0/synthetic_seed-1_sample-0_model.cif"
    )
    assert value["status"] == "unavailable" and value["plddt"] is None
    assert fixture.service.diagnostics(fixture.job_id)["status"] == "available"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["pae"].pop(),
        lambda data: data["pae"][0].__setitem__(0, float("nan")),
        lambda data: data["pae"][0].__setitem__(0, -1),
        lambda data: data["pae"][0].__setitem__(3, 0.5),
        lambda data: data["atom_chain_ids"].__setitem__(0, "B"),
        lambda data: data["atom_plddts"].__setitem__(0, 99),
        lambda data: data["atom_plddts"].__setitem__(0, True),
        lambda data: data["token_res_ids"].__setitem__(0, 99),
    ],
)
def test_malformed_or_cross_sample_confidence_is_unavailable_without_invented_zero(fixture, mutate):
    rewrite_confidence(fixture, mutate)
    value = fixture.service.diagnostics(fixture.job_id)
    assert value["status"] == "available"
    assert value["pae"]["status"] == "unavailable" and value["pae"]["reason"]
    assert value["pae"]["token_count"] is None and value["pae"]["chain_pairs"] == []
    assert value["plddt"]["protein"]["mean"] == 60


def test_missing_confidence_does_not_hide_valid_cif_plddt(fixture):
    (fixture.output / "synthetic_confidences.json").unlink()
    value = fixture.service.diagnostics(fixture.job_id)
    assert value["pae"]["status"] == "unavailable"
    assert value["plddt"]["selected_ligand"]["mean"] == 70


def test_upstream_full_pae_one_decimal_and_summary_two_decimals_are_consistent(fixture):
    result = fixture.store.get(fixture.job_id)["result"]
    top = next(row for row in result["models"] if row["is_top_ranked_copy"])
    top["metrics"]["chain_pair_pae_min"] = [[0.96, 1.96], [4.96, 0.96]]
    fixture.store.update(fixture.job_id, "completed", result)
    value = fixture.service.diagnostics(fixture.job_id)
    assert value["pae"]["status"] == "available"
    assert value["pae"]["min"] == 1
    assert value["summary_metrics"]["chain_pair_pae_min"][0][0] == 0.96


def test_confidence_symlink_into_different_sample_is_rejected(fixture):
    path = fixture.output / "synthetic_confidences.json"
    path.unlink()
    path.symlink_to(fixture.output / "seed-1_sample-0" / "synthetic_seed-1_sample-0_confidences.json")
    value = fixture.service.diagnostics(fixture.job_id)
    assert value["pae"]["status"] == "unavailable"
    assert "same-sample sibling" in value["pae"]["reason"]


def test_file_from_other_job_cannot_be_selected_even_with_matching_chemistry(fixture):
    other = fixture.store.create("alphafold", fixture.store.get(fixture.job_id)["payload"])
    output = fixture.store.directory(other["id"]) / "output"
    write_sample(output, "synthetic")
    with pytest.raises(HTTPException) as error:
        fixture.service.diagnostics(fixture.job_id, file=f"../{other['id']}/output/synthetic_model.cif")
    assert error.value.status_code == 409


def test_registered_confidence_hash_and_sibling_confinement(fixture):
    result = fixture.store.get(fixture.job_id)["result"]
    top = next(row for row in result["models"] if row["is_top_ranked_copy"])
    top["confidence_sha256"] = "f" * 64
    fixture.store.update(fixture.job_id, "completed", result)
    assert fixture.service.diagnostics(fixture.job_id)["pae"]["status"] == "unavailable"
    top.pop("confidence_sha256")
    top["confidence_path"] = "seed-1_sample-0/synthetic_seed-1_sample-0_confidences.json"
    fixture.store.update(fixture.job_id, "completed", result)
    assert fixture.service.diagnostics(fixture.job_id)["pae"]["status"] == "unavailable"


def test_bounded_confidence_and_quarantined_prediction(fixture, monkeypatch):
    monkeypatch.setattr(af3_diagnostics, "MAX_CONFIDENCE_BYTES", 10)
    assert fixture.service.diagnostics(fixture.job_id)["pae"]["status"] == "unavailable"
    result = fixture.store.get(fixture.job_id)["result"]
    result["prediction_eligible"] = False
    fixture.store.update(fixture.job_id, "completed", result)
    value = fixture.service.diagnostics(fixture.job_id)
    assert value["status"] == "unavailable" and value["summary_metrics"] == {}


def test_readonly_http_route_selection_guard(fixture):
    app = FastAPI()
    app.include_router(make_router(fixture.store, SimpleNamespace()))
    previous = fixture.store.get(fixture.job_id)
    with TestClient(app) as client:
        path = f"/api/molecular/predictions/{fixture.job_id}/diagnostics"
        assert client.get(path, params={"smiles": "OCC", "target_accession": "P35354"}).json()[
            "identity_verified"
        ]
        assert client.get(path, params={"smiles": "CCN"}).status_code == 409
        assert client.get(path, params={"target_accession": "Q05769"}).status_code == 409
    assert fixture.store.get(fixture.job_id) == previous
