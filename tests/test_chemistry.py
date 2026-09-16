import pytest
from rdkit import Chem

from herbfold.chemistry import (
    canonical_smiles,
    compare_compounds,
    describe_molecule,
    generate_candidates,
    load_catalog,
    scaffold_key,
)


def test_real_catalog_identity_and_aspirin_descriptors():
    catalog = load_catalog()
    assert len(catalog) == 7
    assert all(row["source_url"].startswith("https://pubchem.ncbi.nlm.nih.gov/compound/") for row in catalog)
    aspirin = describe_molecule(next(row["smiles"] for row in catalog if row["id"] == "aspirin"))
    assert aspirin["formula"] == "C9H8O4"
    assert aspirin["molecular_weight"] == pytest.approx(180.159, abs=0.01)
    assert aspirin["hbd"] == 1
    assert aspirin["hba"] == 3
    assert 0 < aspirin["qed"] < 1


def test_standardization_charge_and_stereo_are_not_silently_lost():
    assert canonical_smiles("[Na+].CC(=O)[O-]") == "CC(=O)[O-]"
    assert canonical_smiles("C[C@H](O)C(=O)O") != canonical_smiles("C[C@@H](O)C(=O)O")
    assert describe_molecule("CC(O)C(=O)O")["unassigned_stereocenters"] == 1
    assert any("Fragment parent" in note for note in describe_molecule("CCO.[Na+]")["notes"])


@pytest.mark.parametrize("smiles", ["", "invalid", "*CC", "[Na+]", "C" * 257, None])
def test_invalid_molecules_are_rejected(smiles):
    with pytest.raises(ValueError):
        describe_molecule(smiles)


def test_scaffolds_group_related_compounds_and_all_acyclic_structures():
    assert scaffold_key("Oc1ccccc1") == scaffold_key("Cc1ccccc1")
    assert scaffold_key("CCO") == scaffold_key("CCCC") == "ACYCLIC"


def test_structural_similarity_does_not_invent_affinities():
    results = compare_compounds()
    assert len(results) == 12
    assert all(0 <= row["tanimoto"] <= 1 for row in results)
    assert all("affinity" not in row for row in results)
    identical = compare_compounds(
        [
            {"id": "a", "smiles": "CCO", "category": "herbal"},
            {"id": "b", "smiles": "OCC", "category": "drug"},
        ]
    )
    assert identical[0]["tanimoto"] == 1


def test_brics_candidates_are_sanitized_reproducible_and_have_two_parent_ancestry():
    catalog = {row["id"]: row for row in load_catalog()}
    parents = [catalog[name]["smiles"] for name in ("quercetin", "aspirin")]
    products = generate_candidates(parents, max_candidates=5)
    assert products
    assert products == generate_candidates(list(reversed(parents)), max_candidates=5)
    assert len(products) <= 5
    source_smiles = {canonical_smiles(smiles) for smiles in parents}
    assert len({row["smiles"] for row in products}) == len(products)
    for row in products:
        assert row["smiles"] not in source_smiles
        assert set(row["parents"]) == source_smiles
        assert all(parent["contributing_fragments"] for parent in row["parent_fragments"])
        mol = Chem.MolFromSmiles(row["smiles"])
        Chem.SanitizeMol(mol)
        assert all(atom.GetAtomicNum() > 0 for atom in mol.GetAtoms())
        assert row["status"] == "unvalidated_computational_candidate"


def test_no_recombination_returns_empty_list_not_parent_copies():
    assert generate_candidates(["CCO", "CCCC"]) == []
    with pytest.raises(ValueError, match="distinct"):
        generate_candidates(["CCO", "OCC"])


def test_pains_alerts_are_explicit_and_optional_filter_enforced():
    quercetin = next(row for row in load_catalog() if row["id"] == "quercetin")
    assert describe_molecule(quercetin["smiles"])["alerts"]
    aspirin = next(row for row in load_catalog() if row["id"] == "aspirin")
    candidates = generate_candidates([quercetin["smiles"], aspirin["smiles"]], reject_alerts=True)
    assert all(not row["descriptors"]["alerts"] for row in candidates)
