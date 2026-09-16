"""Selected identities must match observed complex artifacts, never their names."""

import hashlib
import json
from collections import Counter

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from rdkit import Chem

from herbfold import alphafold
from herbfold import molecular_selection as selection
from herbfold.molecular_api import _job_scene, make_router
from herbfold.storage import Store


def _cif(path, smiles="CCO", protein=("ALA", "CYS", "ASP"), component="LIG_B", extra="", omit_smiles=False):
    text = "data_test\n_entry.id 5IKR\n"
    if not omit_smiles:
        text += f"_chem_comp.id {component}\n_chem_comp.pdbx_smiles '{smiles}'\n"
    text += (
        "loop_\n"
        + "\n".join(
            "_atom_site." + col
            for col in (
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
        )
        + "\n"
    )
    rows = [
        ("C", "CA", residue, "A", index, index, index * 4, 0, 0, 60)
        for index, residue in enumerate(protein, 1)
    ]
    count = Counter()
    for index, atom in enumerate(Chem.MolFromSmiles(smiles).GetAtoms()):
        element = atom.GetSymbol()
        count[element] += 1
        rows.append(
            (element, f"{element.upper()}{count[element]}", component, "B", ".", 1, index * 1.4, 4, 0, 70)
        )
    text += "\n".join(" ".join(map(str, row)) for row in rows) + "\n#\n" + extra
    path.write_text(text)
    return path


def _job(
    store,
    *,
    output_smiles="CCO",
    payload_smiles="CCO",
    protein=("ALA", "CYS", "ASP"),
    imported=False,
    omit_smiles=False,
    extra="",
    name="ptgs2_misleading_name",
):
    payload = {
        "name": name,
        "sequences": [
            {"protein": {"id": "A", "sequence": "ACD"}},
            {"ligand": {"id": "B", "smiles": payload_smiles}},
        ],
    }
    if imported:
        payload = {"files": ["saved_model.cif"]}
    job = store.create("af3_import" if imported else "alphafold", payload)
    output = store.directory(job["id"]) / "output"
    output.mkdir()
    _cif(output / "saved_model.cif", output_smiles, protein, extra=extra, omit_smiles=omit_smiles)
    (output / "saved_summary_confidences.json").write_text(json.dumps({"ptm": 0.4, "iptm": 0.3}))
    result = alphafold.parse_outputs(output)
    result["execution_verified"] = not imported
    store.update(job["id"], "completed", result)
    return job


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(selection, "_target_sequence", lambda acc: "ACD" if acc == "P35354" else None)
    return Store(tmp_path / "runtime")


def test_exact_identity_preserves_stereo_charge_and_rejects_disconnected_salts():
    assert selection.exact_identity("OCC") == selection.exact_identity("CCO")
    assert selection.exact_identity("C[C@H](O)F") != selection.exact_identity("C[C@@H](O)F")
    assert selection.exact_identity("CCO") != selection.exact_identity("CC[O-]")
    with pytest.raises(ValueError):
        selection.exact_identity("CCO.[Na+]")


def test_selected_ligand_matches_real_chain_graph_and_selected_atom_ids(store):
    job = _job(store)
    response = selection.resolve(store, "OCC", "alphafold3_prediction")
    assert response["status"] == "matched"
    scene = response["scene"]
    assert response["matches"][0]["job_id"] == job["id"]
    assert "/artifacts/" in response["matches"][0]["source_url"]
    ids = set(scene["metadata"]["selected_ligand_atom_ids"])
    assert len(ids) == 3
    assert all(atom["chain_id"] == "B" for atom in scene["atoms"] if atom["id"] in ids)
    assert scene["metadata"]["selection"]["target_chains"] == ["A"]
    assert selection.resolve(store, "CCN", "alphafold3_prediction")["scene"] is None
    assert selection.resolve(store, "CCO", "alphafold3_prediction", "Q05769")["scene"] is None


def test_prediction_exclusion_preserves_archive_without_confidence(store):
    job = _job(store)
    result = store.get(job["id"])["result"]
    result.update(
        prediction_eligible=False,
        parameter_audit={"status": "test_parameters"},
        exclusion_reason="Synthetic performance parameters",
    )
    # The eligibility flag itself is sufficient even if status has not changed.
    store.update(job["id"], "completed", result)
    assert selection.resolve(store, "CCO", "alphafold3_prediction")["status"] == "unavailable"
    assert (
        selection.resolve(store, "CCO", "alphafold3_prediction", job_id=job["id"])["status"] == "unavailable"
    )
    archive = _job_scene(store, job["id"])
    assert archive["source"] == "structure_file"
    assert "예측 사용 제외" in archive["label"]
    assert archive["metadata"]["execution_verified"] is True
    assert archive["metadata"]["confidence_kind"] is None
    assert archive["metadata"]["summary_metrics"] == {}
    assert all(atom["confidence"] is None for atom in archive["atoms"])
    assert archive["metadata"]["parameter_audit"]["status"] == "test_parameters"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"output_smiles": "COC"},
        {"omit_smiles": True},
        {"protein": ("ALA", "ASN", "ASP")},
        {"protein": ()},
    ],
)
def test_payload_or_job_name_cannot_override_missing_or_mismatched_output_identity(store, kwargs):
    _job(store, **kwargs)
    response = selection.resolve(store, "CCO", "alphafold3_prediction")
    assert response["status"] == "unavailable"
    assert response["scene"] is None and response["matches"] == []


def test_same_formula_different_declared_output_graph_is_not_a_match(store):
    extra = """loop_
_chem_comp_bond.comp_id
_chem_comp_bond.atom_id_1
_chem_comp_bond.atom_id_2
_chem_comp_bond.value_order
LIG_B C1 O1 SING
LIG_B O1 C2 SING
#
"""
    _job(store, extra=extra)
    assert selection.resolve(store, "CCO", "alphafold3_prediction")["status"] == "unavailable"


def test_missing_observed_ligand_atom_cannot_inherit_complete_input_smiles(store):
    job = _job(store)
    path = store.directory(job["id"]) / "output" / "saved_model.cif"
    path.write_text("\n".join(line for line in path.read_text().splitlines() if not line.startswith("O O1")))
    result = alphafold.parse_outputs(path.parent)
    result["execution_verified"] = True
    store.update(job["id"], "completed", result)
    assert selection.resolve(store, "CCO", "alphafold3_prediction")["status"] == "unavailable"


def test_imports_may_match_output_identity_but_never_gain_verified_execution(store):
    _job(store, imported=True)
    result = selection.resolve(store, "CCO", "alphafold3_prediction")
    assert result["status"] == "matched"
    assert result["scene"]["metadata"]["execution_verified"] is False
    assert "미검증" in result["scene"]["label"]
    assert any("not been independently verified" in note for note in result["scene"]["warnings"])


def test_imported_non_cox_target_requires_registered_reference_and_observed_sequence(store, monkeypatch):
    job = _job(store, imported=True)
    monkeypatch.setattr(selection, "registered_target", lambda store, accession: {
        "accession": "P23219", "sequence": "ACD",
        "sequence_sha256": hashlib.sha256(b"ACD").hexdigest(),
    } if accession == "P23219" else None)
    result = selection.resolve(store, "CCO", "alphafold3_prediction", "P23219", job_id=job["id"])
    assert result["status"] == "matched"
    assert result["scene"]["metadata"]["execution_verified"] is False
    assert result["scene"]["metadata"]["selection"]["target_accession"] == "P23219"
    assert selection.resolve(store, "CCO", "alphafold3_prediction", "P12345")["status"] == "unavailable"
    monkeypatch.setattr(selection, "registered_target", lambda *args: {
        "accession": "P23219", "sequence": "AAA",
    })
    assert selection.resolve(store, "CCO", "alphafold3_prediction", "P23219")["status"] == "unavailable"


def test_saved_lookup_is_not_limited_by_unrelated_100_recent_jobs(store):
    matching = _job(store)
    for _ in range(105):
        store.create("discovery", {"name": "newer unrelated work"})
    result = selection.resolve(store, "CCO", "alphafold3_prediction")
    assert result["matches"][0]["job_id"] == matching["id"]
    newer = _job(store, name="no_target_hint")
    result = selection.resolve(store, "CCO", "alphafold3_prediction")
    assert [row["job_id"] for row in result["matches"]] == [newer["id"], matching["id"]]


def test_changed_artifact_is_not_returned(store):
    job = _job(store)
    path = store.directory(job["id"]) / "output" / "saved_model.cif"
    path.write_text(path.read_text() + "# changed\n")
    assert selection.resolve(store, "CCO", "alphafold3_prediction")["status"] == "unavailable"


@pytest.fixture
def experimental(store, monkeypatch):
    reference = {
        "pdb_id": "5IKR",
        "target_accession": "P35354",
        "target_entity_id": "1",
        "target": "PTGS2",
        "ligand": "Test ethanol",
        "ligand_component": "LIG",
        "ligand_chains": ["B"],
        "ligand_smiles": "CCO",
        "label": "Experimental test reference",
        "source_url": "https://www.rcsb.org/structure/5IKR",
        "url": "https://files.rcsb.org/download/5IKR.cif",
        "construct": {
            "canonical_construct_sequence_sha256": hashlib.sha256(b"ACD").hexdigest(),
            "full_length_canonical_sequence": False,
            "sample_sequence_length": 3,
        },
    }
    monkeypatch.setattr(
        selection,
        "reference_catalog",
        lambda accession="P35354": [reference] if accession == "P35354" else [],
    )
    cache = store.root / "reference_structures"
    cache.mkdir()
    _cif(
        cache / "5IKR.cif",
        component="LIG",
        extra="""_exptl.method 'X-RAY DIFFRACTION'
_struct_ref.db_name UNP
_struct_ref.pdbx_db_accession P35354
_struct_ref.entity_id 1
_entity_poly.entity_id 1
_entity_poly.pdbx_seq_one_letter_code_can ACD
loop_
_struct_asym.id
_struct_asym.entity_id
A 1
B 2
#
""",
    )
    (cache / "CCD_LIG.cif").write_text("""data_LIG
_chem_comp.id LIG
loop_
_pdbx_chem_comp_descriptor.type
_pdbx_chem_comp_descriptor.descriptor
SMILES_CANONICAL CCO
#
loop_
_chem_comp_atom.atom_id
_chem_comp_atom.type_symbol
_chem_comp_atom.charge
C1 C 0
C2 C 0
O1 O 0
#
loop_
_chem_comp_bond.comp_id
_chem_comp_bond.atom_id_1
_chem_comp_bond.atom_id_2
_chem_comp_bond.value_order
LIG C1 C2 SING
LIG C2 O1 SING
#
""")
    return reference, cache


def test_experimental_requires_exact_ccd_observed_graph_and_actual_target_mapping(store, experimental):
    _, cache = experimental
    result = selection.resolve(store, "CCO", "experimental_pdb")
    assert result["status"] == "matched"
    assert result["scene"]["metadata"]["selection"]["target_chains"] == ["A"]
    assert len(result["scene"]["metadata"]["selected_ligand_atom_ids"]) == 3
    assert result["scene"]["metadata"]["construct"]["sample_sequence_length"] == 3
    assert selection.resolve(store, "CCN", "experimental_pdb")["reason_code"] == "no_registered_exact_match"
    path = cache / "5IKR.cif"
    path.write_text(path.read_text().replace("P35354", "Q05769"))
    invalid = selection.resolve(store, "CCO", "experimental_pdb")
    assert invalid["scene"] is None
    assert invalid["reason_code"] == "reference_validation_failed"


def test_experimental_ccd_identity_mismatch_is_not_relabelled(store, experimental):
    _, cache = experimental
    ccd = cache / "CCD_LIG.cif"
    ccd.write_text(ccd.read_text().replace("SMILES_CANONICAL CCO", "SMILES_CANONICAL COC"))
    assert selection.resolve(store, "CCO", "experimental_pdb")["status"] == "unavailable"


def test_reference_fetch_failure_is_distinct_from_no_exact_match(store, experimental, monkeypatch):
    from herbfold import molecular_api

    def failed(*args, **kwargs):
        raise HTTPException(status_code=502, detail="remote unavailable")

    monkeypatch.setattr(molecular_api, "_reference_scene", failed)
    result = selection.resolve(store, "CCO", "experimental_pdb")
    assert result["reason_code"] == "reference_fetch_failed"
    assert "불러오지 못했습니다" in result["reason"]


def test_router_reference_choices_are_explicit_and_unavailable_never_returns_fallback(store, experimental):
    app = FastAPI()
    app.include_router(make_router(store))
    client = TestClient(app)
    choices = client.get("/api/molecular/references").json()["items"]
    assert choices[0]["smiles"] == "CCO"
    assert choices[0]["source_status"] == "experimental_ligand_reference_not_current_approval_status"
    result = client.post("/api/molecular/resolve", json={"smiles": "CCN", "source": "experimental_pdb"})
    assert result.status_code == 200
    assert result.json()["scene"] is None
    assert result.json()["requested"]["canonical_smiles"] == "CCN"
    assert result.json()["available_references"][0]["smiles"] == "CCO"
    assert (
        client.post("/api/molecular/resolve", json={"smiles": "CCO", "source": "random"}).status_code == 422
    )


def test_covalent_adduct_is_not_returned_as_intact_selected_free_ligand(store):
    extra = """loop_
_struct_conn.conn_type_id
_struct_conn.ptnr1_label_asym_id
_struct_conn.ptnr1_label_seq_id
_struct_conn.ptnr1_label_atom_id
_struct_conn.ptnr2_label_asym_id
_struct_conn.ptnr2_label_seq_id
_struct_conn.ptnr2_label_atom_id
_struct_conn.ptnr1_symmetry
_struct_conn.ptnr2_symmetry
_struct_conn.pdbx_value_order
covale A 1 CA B . C1 1_555 1_555 SING
#
"""
    _job(store, extra=extra)
    assert selection.resolve(store, "CCO", "alphafold3_prediction")["status"] == "unavailable"


def test_explicit_output_atom_charge_cannot_contradict_selected_smiles(store):
    job = _job(store)
    path = store.directory(job["id"]) / "output" / "saved_model.cif"
    lines = []
    for line in path.read_text().splitlines():
        if line == "_atom_site.B_iso_or_equiv":
            lines.extend([line, "_atom_site.pdbx_formal_charge"])
        elif line.startswith(("C CA ", "C C", "O O")):
            lines.append(line + (" 1" if line.startswith("C C1 LIG_B") else " 0"))
        else:
            lines.append(line)
    path.write_text("\n".join(lines) + "\n")
    result = alphafold.parse_outputs(path.parent)
    result["execution_verified"] = True
    store.update(job["id"], "completed", result)
    assert selection.resolve(store, "CCO", "alphafold3_prediction")["status"] == "unavailable"


def test_global_artifact_read_budget_returns_explicit_partial_search_not_wrong_fallback(store, monkeypatch):
    from herbfold import molecular_api

    _job(store)  # A correct older result exists beyond the bounded search window.
    for _ in range(3):
        _job(store, output_smiles="CCN")
    monkeypatch.setattr(selection, "_MAX_MODEL_CHECKS", 2)
    original = molecular_api._job_scene
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(molecular_api, "_job_scene", counted)
    response = selection.resolve(store, "CCO", "alphafold3_prediction")
    assert len(calls) == response["checked_model_artifacts"] == 2
    assert response["search_truncated"] is True
    assert response["reason_code"] == "saved_search_limit_reached"
    assert response["status"] == "unavailable" and response["scene"] is None
    assert "더 오래된 자료" in response["reason"]
