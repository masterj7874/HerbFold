import io
import json
import math

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from rdkit import Chem

from herbfold import alphafold, molecular
from herbfold.molecular_api import _job_scene, _reference_scene, make_router
from herbfold.storage import Store


def write_cif(path, atoms, *, component_smiles=None, extra=""):
    """atom tuples: element,name,component,chain,seq,auth,x,y,z,bfactor."""
    text = "data_test\n_entry.id TEST\n"
    if component_smiles:
        text += f"_chem_comp.id LIG_B\n_chem_comp.pdbx_smiles '{component_smiles}'\n"
    text += (
        "loop_\n"
        + "\n".join(
            "_atom_site." + key
            for key in (
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
    text += "\n".join(" ".join(map(str, atom)) for atom in atoms) + "\n#\n" + extra
    path.write_text(text)
    return path


@pytest.fixture
def alanine_cif(tmp_path):
    return write_cif(
        tmp_path / "protein.cif",
        [
            ("N", "N", "ALA", "A", 1, 1, 0, 0, 0, 87),
            ("C", "CA", "ALA", "A", 1, 1, 1.45, 0, 0, 88),
            ("C", "C", "ALA", "A", 1, 1, 2.9, 0, 0, 89),
            ("O", "O", "ALA", "A", 1, 1, 4.1, 0, 0, 90),
            ("C", "CB", "ALA", "A", 1, 1, 1.45, 1.5, 0, 91),
            ("N", "N", "GLY", "A", 2, 2, 30, 0, 0, 92),
            ("C", "CA", "GLY", "A", 2, 2, 31.45, 0, 0, 93),
        ],
    )


def test_conformer_real_graph_orders_and_finite_distances():
    scene = molecular.conformer("CC(=O)Oc1ccccc1C(=O)O", num_conformers=1)
    mol = Chem.AddHs(Chem.MolFromSmiles(scene["metadata"]["smiles"]))
    assert scene["source"] == "rdkit_conformer"
    assert scene["metadata"]["formula"] == "C9H8O4"
    assert len(scene["atoms"]) == mol.GetNumAtoms()
    assert len(scene["bonds"]) == mol.GetNumBonds()
    assert {bond["order"] for bond in scene["bonds"]} == {1, 1.5, 2}
    assert scene["energy"]["method"] == "MMFF94s"
    assert scene["energy"]["converged"]
    assert math.isfinite(scene["energy"]["value_kcal_mol"])
    for bond in scene["bonds"]:
        a, b = (scene["atoms"][bond[key]] for key in ("source", "target"))
        assert bond["length_angstrom"] == pytest.approx(
            math.dist([a[k] for k in "xyz"], [b[k] for k in "xyz"]), abs=1e-6
        )
        assert 0.65 < bond["length_angstrom"] < 2.4
        assert bond["provenance"] == "input_smiles_graph"
    assert all(a["confidence"] is None for a in scene["atoms"])


def test_seed_reproducibility_and_hydrogen_toggle():
    left = molecular.conformer("CCCO", seed=73, num_conformers=1)
    right = molecular.conformer("CCCO", seed=73, num_conformers=1)
    heavy = molecular.conformer("CCCO", seed=73, num_conformers=1, include_hydrogens=False)
    assert left["atoms"] == right["atoms"]
    assert len(heavy["atoms"]) == 4
    assert not any(a["element"] == "H" for a in heavy["atoms"])
    assert heavy["metadata"]["formula"] == left["metadata"]["formula"]


def test_sdf_preserves_assigned_chirality_from_actual_3d_geometry():
    smiles = "N[C@@H](C)C(=O)O"
    sdf = molecular.conformer_sdf(smiles, num_conformers=1)
    mol = next(Chem.ForwardSDMolSupplier(io.BytesIO(sdf.encode()), removeHs=False))
    Chem.AssignStereochemistryFrom3D(mol, replaceExistingTags=True)
    assert Chem.MolToSmiles(Chem.RemoveHs(mol), isomericSmiles=True) == Chem.MolToSmiles(
        Chem.MolFromSmiles(smiles), isomericSmiles=True
    )
    assert mol.GetProp("COORDINATE_SOURCE") == "rdkit_conformer"
    assert mol.GetConformer().Is3D()
    assert mol.GetProp("MINIMIZATION_CONVERGED") == "True"


@pytest.mark.parametrize("smiles", ["NC(C)C(=O)O", "CC=CC"])
def test_unspecified_stereo_is_not_presented_as_resolved(smiles):
    scene = molecular.conformer(smiles, num_conformers=1)
    assert scene["metadata"]["unspecified_stereo"]
    assert any("does not resolve" in note for note in scene["warnings"])
    assert scene["metadata"]["smiles"] == Chem.MolToSmiles(Chem.MolFromSmiles(smiles), isomericSmiles=True)


def test_missing_force_field_has_no_invented_energy(monkeypatch):
    monkeypatch.setattr(molecular.AllChem, "MMFFHasAllMoleculeParams", lambda mol: False)
    monkeypatch.setattr(molecular.AllChem, "UFFHasAllMoleculeParams", lambda mol: False)
    result = molecular.conformer("CCO", num_conformers=1)
    assert result["energy"]["value_kcal_mol"] is None
    assert not result["energy"]["converged"]
    assert result["energy"]["method"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"smiles": ""},
        {"smiles": "not smiles"},
        {"smiles": "C.C"},
        {"smiles": "*C"},
        {"smiles": "C" * 250},
        {"smiles": "C" * 5001},
        {"smiles": "CC", "seed": True},
        {"smiles": "CC", "seed": -1},
        {"smiles": "CC", "num_conformers": 5},
        {"smiles": "CC", "include_hydrogens": "false"},
    ],
)
def test_conformer_budgets_and_validation(kwargs):
    with pytest.raises(ValueError):
        molecular.conformer(**kwargs)


def test_protein_template_bonds_preserve_distorted_coordinates(alanine_cif):
    scene = molecular.structure_scene(alanine_cif, source="alphafold3_prediction")
    bond = next(b for b in scene["bonds"] if b["provenance"] == "consecutive_polymer_sequence")
    assert (bond["source"], bond["target"]) == (2, 5)
    assert bond["length_angstrom"] == 27.1
    assert scene["atoms"][5]["x"] == 30
    assert scene["geometry"]["long_bond_count"] == 1
    assert any("Coordinate geometry warning" in note for note in scene["warnings"])
    assert scene["atoms"][0]["confidence"] == 87
    assert all(b["provenance"] != "distance_inferred" for b in scene["bonds"])


def test_isoleucine_wwpdb_named_branch_does_not_create_false_geometry_warning(tmp_path):
    # Synthetic coordinates, not a predicted or experimental protein. The
    # wwPDB ILE methylene is CG1; CD1 is not bonded to the methyl branch CG2.
    rows = [
        ("C", "CD1", "ILE", "A", 1, 1, 3.0, 0, 0, 80),
        ("C", "CG2", "ILE", "A", 1, 1, 0, 1.5, 0, 80),
        ("C", "CB", "ILE", "A", 1, 1, 0, 0, 0, 80),
        ("C", "CG1", "ILE", "A", 1, 1, 1.5, 0, 0, 80),
    ]
    path = write_cif(tmp_path / "synthetic_ile.cif", rows)
    original = path.read_bytes()
    scene = molecular.structure_scene(path, source="alphafold3_prediction")
    pairs = {
        frozenset((scene["atoms"][bond["source"]]["name"], scene["atoms"][bond["target"]]["name"]))
        for bond in scene["bonds"]
    }
    assert pairs == {frozenset(pair) for pair in (("CB", "CG1"), ("CB", "CG2"), ("CG1", "CD1"))}
    assert all(bond["length_angstrom"] == 1.5 for bond in scene["bonds"])
    assert all(bond["provenance"] == "wwpdb_ILE_residue_template" for bond in scene["bonds"])
    assert scene["geometry"]["long_bond_count"] == 0
    assert not any("Coordinate geometry warning" in note for note in scene["warnings"])
    assert [(atom["x"], atom["y"], atom["z"]) for atom in scene["atoms"]] == [tuple(row[6:9]) for row in rows]
    assert path.read_bytes() == original


def test_experimental_b_factors_are_not_confidence(alanine_cif):
    scene = molecular.structure_scene(alanine_cif, source="experimental_pdb")
    assert scene["atoms"][0]["b_factor"] == 87
    assert all(atom["confidence"] is None for atom in scene["atoms"])
    assert scene["metadata"]["confidence_kind"] is None


def test_af3_ligand_bonds_map_names_not_coordinate_row_order(tmp_path):
    path = write_cif(
        tmp_path / "ligand.cif",
        [
            ("O", "O1", "LIG_B", "B", ".", 1, 2.8, 0, 0, 50),
            ("C", "C2", "LIG_B", "B", ".", 1, 1.4, 0, 0, 60),
            ("C", "C1", "LIG_B", "B", ".", 1, 0, 0, 0, 70),
        ],
        component_smiles="CCO",
    )
    scene = molecular.structure_scene(path, source="alphafold3_prediction")
    assert {tuple(sorted((b["source"], b["target"]))) for b in scene["bonds"]} == {(0, 1), (1, 2)}
    assert all(b["provenance"] == "af3_v3_smiles_atom_names_validated" for b in scene["bonds"])


def test_af3_ligand_mapping_failure_does_not_guess(tmp_path):
    path = write_cif(
        tmp_path / "ligand.cif",
        [
            ("C", "C1", "LIG_B", "B", ".", 1, 0, 0, 0, 50),
            ("C", "C2", "LIG_B", "B", ".", 1, 1.4, 0, 0, 60),
            ("O", "NOT_O1", "LIG_B", "B", ".", 1, 2.8, 0, 0, 70),
        ],
        component_smiles="CCO",
    )
    scene = molecular.structure_scene(path, source="alphafold3_prediction")
    assert not scene["bonds"]
    assert any("mapping failed" in note for note in scene["warnings"])


def test_ccd_named_bond_orders_override_distance(tmp_path):
    path = write_cif(
        tmp_path / "ccd.cif",
        [
            ("C", "CX", "ABC", "B", ".", 1, 0, 0, 0, 5),
            ("O", "OY", "ABC", "B", ".", 1, 14, 0, 0, 6),
        ],
        extra="loop_\n_chem_comp_bond.comp_id\n_chem_comp_bond.atom_id_1\n_chem_comp_bond.atom_id_2\n_chem_comp_bond.value_order\nABC CX OY DOUB\n",
    )
    scene = molecular.structure_scene(path)
    assert scene["bonds"][0]["order"] == 2
    assert scene["bonds"][0]["length_angstrom"] == 14
    assert scene["bonds"][0]["provenance"] == "wwpdb_chemical_component_bond"


@pytest.mark.parametrize(
    "text", ["not a cif", "data_test\n_entry.id TEST\n", "data_test\n_atom_site.Cartn_x 1\n"]
)
def test_malformed_mmcif_rejected(tmp_path, text):
    path = tmp_path / "bad.cif"
    path.write_text(text)
    with pytest.raises(ValueError):
        molecular.structure_scene(path)


def test_nonfinite_coordinates_rejected(alanine_cif):
    alanine_cif.write_text(alanine_cif.read_text().replace("1.45", "nan"))
    with pytest.raises(ValueError, match="non-finite"):
        molecular.structure_scene(alanine_cif)


def test_mismatched_cif_columns_rejected(alanine_cif, monkeypatch):
    cif = molecular.read_cif(alanine_cif)
    cif["_atom_site.Cartn_y"].pop()
    monkeypatch.setattr(molecular, "read_cif", lambda path: cif)
    with pytest.raises(ValueError, match="length mismatch"):
        molecular.structure_scene(alanine_cif)


def test_artifact_path_and_symlink_containment(tmp_path, alanine_cif):
    store = Store(tmp_path / "runtime")
    job = store.create("alphafold", {"sequences": []})
    directory = store.directory(job["id"])
    (directory / "linked.cif").symlink_to(alanine_cif)
    for filename in ("../protein.cif", str(alanine_cif), "linked.cif"):
        with pytest.raises(ValueError, match="within this job"):
            _job_scene(store, job["id"], filename)
    output = directory / "output"
    output.mkdir()
    (output / "protein_model.cif").write_bytes(alanine_cif.read_bytes())
    (output / "protein_summary_confidences.json").write_text('{"ptm":0.5}')
    store.update(job["id"], "completed", alphafold.parse_outputs(output))
    scene = _job_scene(store, job["id"], "output/protein_model.cif")
    assert scene["metadata"]["artifact"] == "output/protein_model.cif"
    assert len(scene["metadata"]["sha256"]) == 64
    assert scene["source"] == "alphafold3_prediction"


def test_router_conformer_sdf_validation_and_missing_sample(tmp_path):
    app = FastAPI()
    app.include_router(make_router(Store(tmp_path / "runtime")))

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    client = TestClient(app)
    result = client.post("/api/molecular/conformer", json={"smiles": "CCO", "num_conformers": 1})
    assert result.status_code == 200
    assert len(result.json()["bonds"]) == 8
    assert np.isfinite([[a[k] for k in "xyz"] for a in result.json()["atoms"]]).all()
    result = client.post("/api/molecular/sdf", json={"smiles": "CCO", "num_conformers": 1})
    assert result.status_code == 200
    assert "chemical/x-mdl-sdfile" in result.headers["content-type"]
    assert "$$$$" in result.text
    assert client.post("/api/molecular/conformer", json={"smiles": "*"}).status_code == 422
    assert client.post("/api/molecular/conformer", json={"smiles": "CC", "seed": True}).status_code == 422
    assert client.get("/api/molecular/samples/ptgs2").status_code == 404
    assert client.get("/api/molecular/reference/XXXX").status_code == 404


def test_real_import_api_scene_uses_registered_flat_output_and_exact_ligand_graph(tmp_path):
    from herbfold.api import create_app

    path = write_cif(
        tmp_path / "ligand.cif",
        [
            ("O", "O1", "LIG_B", "B", ".", 1, 2.8, 0, 0, 50),
            ("C", "C2", "LIG_B", "B", ".", 1, 1.4, 0, 0, 60),
            ("C", "C1", "LIG_B", "B", ".", 1, 0, 0, 0, 70),
        ],
        component_smiles="CCO",
    )
    with TestClient(create_app(tmp_path / "runtime")) as client:
        imported = client.post(
            "/api/af3/import",
            json={
                "files": {
                    "ptgs2_import_model.cif": path.read_text(),
                    "ptgs2_import_summary_confidences.json": json.dumps(
                        {"ptm": 0.5, "iptm": 0.4, "ranking_score": 0.4}
                    ),
                }
            },
        )
        assert imported.status_code == 200
        job = imported.json()
        assert job["kind"] == "af3_import"
        response = client.get(f"/api/molecular/jobs/{job['id']}/scene")
        assert response.status_code == 200
        scene = response.json()
        assert scene["metadata"]["artifact"] == "output/ptgs2_import_model.cif"
        assert scene["metadata"]["execution_verified"] is False
        assert scene["source"] == "alphafold3_prediction"
        assert scene["metadata"]["provenance"] == "user_supplied_af3_format_artifacts"
        assert "unverified" in scene["label"]
        assert any("not an experimental structure" in note for note in scene["warnings"])
        assert {tuple(sorted((b["source"], b["target"]))) for b in scene["bonds"]} == {(0, 1), (1, 2)}
        assert client.get("/api/molecular/samples/ptgs2").status_code == 200


def test_smoke_job_selects_registered_nested_model_and_verified_execution(tmp_path, alanine_cif):
    store = Store(tmp_path / "runtime")
    job = store.create("alphafold_smoke", {"name": "ptgs2_test", "sequences": []})
    output = store.directory(job["id"]) / "output"
    model_dir = output / "ptgs2_test"
    model_dir.mkdir(parents=True)
    (model_dir / "ptgs2_test_model.cif").write_bytes(alanine_cif.read_bytes())
    (model_dir / "ptgs2_test_summary_confidences.json").write_text('{"ptm":0.5}')
    result = alphafold.parse_outputs(output)
    result["execution_verified"] = True
    store.update(job["id"], "completed", result)
    scene = _job_scene(store, job["id"])
    assert scene["metadata"]["artifact"] == "output/ptgs2_test/ptgs2_test_model.cif"
    assert scene["metadata"]["execution_verified"] is True
    assert scene["metadata"]["provenance"] == "recorded_local_execution"
    app = FastAPI()
    app.include_router(make_router(store))
    assert TestClient(app).get("/api/molecular/samples/ptgs2").status_code == 200


def test_changed_or_unregistered_structure_cannot_inherit_validated_model_provenance(tmp_path, alanine_cif):
    store = Store(tmp_path / "runtime")
    job = store.create("af3_import", {"files": []})
    output = store.directory(job["id"]) / "output"
    output.mkdir()
    model_path = output / "test_model.cif"
    model_path.write_bytes(alanine_cif.read_bytes())
    (output / "test_summary_confidences.json").write_text('{"ptm":0.5}')
    store.update(job["id"], "completed", alphafold.parse_outputs(output))
    (output / "unregistered.cif").write_bytes(alanine_cif.read_bytes())
    with pytest.raises(ValueError, match="not a validated model"):
        _job_scene(store, job["id"], "output/unregistered.cif")
    model_path.write_text(model_path.read_text().replace("1.45", "1.50"))
    with pytest.raises(ValueError, match="checksum"):
        _job_scene(store, job["id"])


def test_cached_reference_cannot_label_an_af3_file_as_experimental(tmp_path, alanine_cif):
    store = Store(tmp_path / "runtime")
    cache = store.root / "reference_structures"
    cache.mkdir()
    path = cache / "5IKR.cif"
    path.write_bytes(alanine_cif.read_bytes())
    with pytest.raises(ValueError, match="unexpected structure identifier"):
        _reference_scene(store, "5IKR")
    path.write_text(path.read_text().replace("_entry.id TEST", "_entry.id 5IKR"))
    with pytest.raises(ValueError, match="experimental X-ray method"):
        _reference_scene(store, "5IKR")
