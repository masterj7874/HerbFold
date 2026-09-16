"""Small synthetic unit fixtures; no production outputs, APIs, downloads or GPU."""
import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts/verify_af3_msa_comparison.py"
_SPEC = importlib.util.spec_from_file_location("msa_comparison_under_test", _SCRIPT)
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


def atom(position, xyz, residue="ALA", *, chain="A", alt=".", occupancy=1, name="CA", element="C"):
    return {"position": position, "xyz": xyz, "residue": residue, "chain": chain, "alt": alt,
            "occupancy": occupancy, "name": name, "element": element, "bfactor": 50}


def test_kabsch_recovers_rotation_translation_without_reflection():
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0], [0, 0, 3]], dtype=float)
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    target = points @ rotation + [7, -8, 2]
    result = audit.kabsch(points, target)
    assert result["rmsd_angstrom"] < 1e-12
    assert result["rotation_determinant"] == pytest.approx(1)
    reflected = points.copy()
    reflected[:, 0] *= -1
    assert audit.kabsch(points, reflected)["rmsd_angstrom"] > 0.1


def test_kabsch_keeps_all_pairs_even_with_a_large_outlier():
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0], [0, 0, 3], [1, 2, 3]], dtype=float)
    target = points.copy()
    target[-1] += [100, 30, 20]
    result = audit.kabsch(points, target)
    assert result["matched_ca_count"] == 5
    assert len(result["per_residue_distance_angstrom"]) == 5
    assert result["rmsd_angstrom"] > 10
    assert result["outlier_rejection"] is False


@pytest.mark.parametrize("points", [[[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 0, 0], [1, 2, float('nan')], [2, 1, 0]]])
def test_kabsch_rejects_undefined_or_nonfinite_geometry(points):
    with pytest.raises(audit.AuditError):
        audit.kabsch(points, points)


def test_ca_alt_locations_use_occupancy_then_deterministic_tie_without_cherry_picking_distance():
    rows = [atom(1, [0, 0, 0], alt="A", occupancy=.4), atom(1, [100, 100, 100], alt="B", occupancy=.6),
            atom(2, [1, 0, 0], alt="B", occupancy=.5), atom(2, [2, 0, 0], alt="A", occupancy=.5)]
    selected, diagnostics = audit.select_ca(rows, "A")
    assert selected[1]["alt"] == "B" and selected[2]["alt"] == "A"
    assert len(diagnostics) == 2
    with pytest.raises(audit.AuditError, match="Duplicate"):
        audit.select_ca([atom(1, [0, 0, 0]), atom(1, [1, 0, 0])], "A")


def test_ca_matching_reports_missing_residues_and_rejects_identity_conflicts():
    sequence = "AAAAAA"
    reference = {"mapping": {1: 2, 2: 3, 3: 4, 4: 5}, "ca": {
        1: atom(1, [0, 0, 0]), 2: atom(2, [1, 0, 0]),
        3: atom(3, [0, 1, 0]), 4: atom(4, [0, 0, 1])}}
    prediction = {2: atom(2, [0, 0, 0]), 3: atom(3, [1, 0, 0]), 4: atom(4, [0, 1, 0])}
    result = audit.align_ca(prediction, reference, sequence)
    assert result["matched_uniprot_positions"] == [2, 3, 4]
    assert result["missing_prediction_ca_uniprot_positions"] == [5]
    assert result["excluded_full_target_positions_outside_reference"] == [1, 6]
    prediction[3]["residue"] = "GLY"
    with pytest.raises(audit.AuditError, match="identity mismatch"):
        audit.align_ca(prediction, reference, sequence)


def test_msa_counts_duplicates_insertions_and_coverage_without_calling_it_neff():
    msa = ">query\nACDE\n>insertion\nAcCD.E\n>partial\nAC--\n>duplicate\nAC--\n"
    result = audit.msa_diagnostics(msa, "ACDE")
    assert result["aligned_width"] == 4
    assert result["sequence_rows_including_query"] == 4
    assert result["aligned_unique_sequences_including_query"] == 2
    assert result["duplicate_aligned_rows"] == 2
    assert result["non_query_rows_identical_to_query"] == 1
    assert result["non_query_coverage_fraction"]["median"] == .5
    assert result["non_query_coverage_fraction"]["p95"] == pytest.approx(.95)
    assert result["column_counts"] == [3, 3, 1, 1]
    assert result["query_positions_with_non_query_support_fraction"] == 1
    assert "not Neff" in result["interpretation"]


def test_msa_query_only_has_no_homolog_distribution_and_zero_support():
    result = audit.msa_diagnostics(">query\nACDE\n", "ACDE")
    assert result["non_query_coverage_fraction"] is None
    assert result["query_positions_with_non_query_support_fraction"] == 0
    assert result["column_non_gap_non_query_row_count"] == {"min": 0, "median": 0, "max": 0}


@pytest.mark.parametrize("msa", [">q\nACD\n", ">q\nACDE\n>x\nACDEF\n", ">q\nACDE\n>x\nAC1E\n", "ACDE\n"])
def test_msa_rejects_partial_query_wrong_width_and_malformed_rows(msa):
    with pytest.raises(audit.AuditError):
        audit.msa_diagnostics(msa, "ACDE")


def test_raw_ligand_identity_rejects_charges_missing_atoms_and_wrong_target():
    cif = {"_chem_comp.id": ["LIG"], "_chem_comp.pdbx_smiles": ["CCO"]}
    atoms = [atom(1, [0, 0, 0]), atom(2, [1, 0, 0], "CYS")]
    atoms += [atom(None, [0, 0, 1], "LIG", chain="B", name=name, element=element)
              for name, element in (("C1", "C"), ("C2", "C"), ("O1", "O"))]
    _, identity = audit.verify_raw_identity(cif, atoms, "AC", "OCC", "A", "B")
    assert identity["full_observed_sequence_verified"] is True
    assert identity["ligand_heavy_atoms"] == 3
    with pytest.raises(audit.AuditError, match="graph identity"):
        audit.verify_raw_identity(cif, atoms, "AC", "CC[O-]", "A", "B")
    with pytest.raises(audit.AuditError, match="heavy atoms"):
        audit.verify_raw_identity(cif, atoms[:-1], "AC", "CCO", "A", "B")
    with pytest.raises(audit.AuditError, match="residue identity"):
        audit.verify_raw_identity(cif, atoms, "AA", "CCO", "A", "B")


def test_sifts_mapping_rejects_overlap_instead_of_silently_trimming():
    registry = {"references": [{"pdb_id": "5IKR", "target_accession": "P35354", "target_chains": ["A"],
        "construct": {"sample_sequence_length": 3, "reference_alignments": [{"provenance_source": "SIFTS", "reference_database_accession": "P35354",
            "aligned_regions": [{"entity_beg_seq_id": 1, "ref_beg_seq_id": 3, "length": 3}]}]}}]}
    _, mapping = audit.reference_mapping(registry, "AAAAAAA", "P35354")
    assert mapping == {1: 3, 2: 4, 3: 5}
    registry["references"][0]["construct"]["reference_alignments"][0]["aligned_regions"].append({"entity_beg_seq_id": 2, "ref_beg_seq_id": 4, "length": 1})
    with pytest.raises(audit.AuditError, match="Overlapping"):
        audit.reference_mapping(registry, "AAAAAAA", "P35354")


def test_conditions_do_not_treat_missing_values_or_changed_seeds_as_equal():
    a = {"msa_mode": "none", "seeds": [1], "model_content_sha256": "fixture", "max_template_date": "2021-09-30"}
    b = {**a, "msa_mode": "search"}
    assert audit.compare_conditions(a, b)["all_controlled_conditions_equal"] is True
    b["seeds"] = [2]
    assert audit.compare_conditions(a, b)["mismatches"] == ["seeds"]
    del b["model_content_sha256"]
    assert "model_content_sha256" in audit.compare_conditions(a, b)["mismatches"]


def test_artifact_path_cannot_escape_job_or_hide_missing_file(tmp_path):
    (tmp_path / "present.cif").write_text("fixture")
    assert audit.safe_artifact(tmp_path, "present.cif").is_file()
    for relative in ("../outside.cif", "/etc/passwd", "missing.cif"):
        with pytest.raises(audit.AuditError):
            audit.safe_artifact(tmp_path, relative)


def test_missing_msa_report_fails_explicitly_without_baseline_only(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"jobs": {"fixture": {}}}))
    output = tmp_path / "report.json"
    code = audit.main(["--baseline-report", str(baseline), "--msa-report", str(tmp_path / "missing.json"), "--output", str(output)])
    assert code == 1
    report = json.loads(output.read_text())
    assert report["passed"] is False and report["comparison"] == {}
    assert "FileNotFoundError" in report["error"]


def test_empty_msa_records_are_not_silently_removed():
    for value in (">q\nACDE\n>empty\n", ">empty\n>q\nACDE\n"):
        with pytest.raises(audit.AuditError, match="Empty"):
            audit.msa_diagnostics(value, "ACDE")


def test_top_copy_allows_creation_time_change_but_not_coords_or_model_identity():
    original = {"_atom_site.Cartn_x": ["1.000"], "_ma_model_list.model_group_name": ["AF3 @ 2026-01-01 00:00:01"]}
    copy = {**original, "_ma_model_list.model_group_name": ["AF3 @ 2026-01-01 00:00:02"]}
    assert audit.molecular_table_sha256(original) == audit.molecular_table_sha256(copy)
    assert set(audit.verify_copy_metadata(original, copy)) == {"_ma_model_list.model_group_name"}
    with pytest.raises(audit.AuditError, match="more than creation"):
        audit.verify_copy_metadata(original, {**copy, "_ma_model_list.model_group_name": ["Different model @ 2026-01-01 00:00:02"]})
    modified = {**copy, "_atom_site.Cartn_x": ["1.001"]}
    assert audit.molecular_table_sha256(original) != audit.molecular_table_sha256(modified)
    with pytest.raises(audit.AuditError, match="outside creation"):
        audit.verify_copy_metadata(original, modified)


def test_actual_enriched_input_hash_counts_and_selected_ligand_are_linked(tmp_path):
    import hashlib
    sequence = "ACDE"
    protein = {"id": "A", "sequence": sequence, "unpairedMsa": ">q\nACDE\n>h1\nAC--\n>h2\nA---\n",
               "pairedMsa": ">q\nACDE\n", "templates": []}
    ligand = {"ligand": {"id": "B", "smiles": "CCO"}}
    enriched = {"name": "synthetic-feature-test", "modelSeeds": [1], "sequences": [{"protein": protein}, ligand]}
    actual_path = tmp_path / "inference_input.json"
    actual_path.write_text(json.dumps(enriched))
    (tmp_path / "output").mkdir()
    (tmp_path / "output/fixture_data.json").write_text(json.dumps(enriched))
    features = {key: protein[key] for key in ("sequence", "unpairedMsa", "pairedMsa", "templates")}
    feature_sha = hashlib.sha256((json.dumps(features, separators=(",", ":")) + "\n").encode()).hexdigest()
    metadata = {"status": "ready", "unpaired_msa_sequences": 3, "paired_msa_sequences": 1, "non_query_sequences": 2,
                "template_count": 0, "templates": [], "feature_sha256": feature_sha,
                "protein_sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(), "max_template_date": "2021-09-30"}
    entry = {"requested": {"msa_mode": "search", "execution_profile": {"max_template_date": "2021-09-30"}}, "msa_features": metadata,
             "job": {"payload": {"name": enriched["name"], "modelSeeds": [1], "sequences": [{"protein": {"id": "A", "sequence": sequence}}, ligand]},
                     "result": {"inference_input_sha256": audit.sha256(actual_path)}}}
    metadata.update(database_fingerprint="fixture-database", af3_version="fixture-version", af3_commit="fixture-code")
    entry["requested"].update(submission_context={"database_fingerprint": "fixture-database"}, af3_version="fixture-version", af3_commit="fixture-code")
    entry["job"]["result"]["execution_provenance"] = {"databases": {"fingerprint_sha256": "fixture-database"}}
    result = audit.audit_msa_features(entry, tmp_path, sequence)
    assert result["unpaired"]["query_positions_with_non_query_support_fraction"] == .5
    assert result["template_5ikr_explicitly_present"] is False
    metadata["unpaired_msa_sequences"] = 999
    with pytest.raises(audit.AuditError, match="Stored MSA count"):
        audit.audit_msa_features(entry, tmp_path, sequence)
    metadata["unpaired_msa_sequences"] = 3
    entry["job"]["payload"]["sequences"][-1] = {"ligand": {"id": "B", "smiles": "CCN"}}
    with pytest.raises(audit.AuditError, match="selected ligand"):
        audit.audit_msa_features(entry, tmp_path, sequence)


def test_inline_template_5ikr_presence_and_residue_mapping_are_read_from_actual_input(tmp_path):
    import hashlib
    sequence = "ACDE"
    cif = "data_5ikr\n_entry.id 5IKR\nloop_\n_entity_poly_seq.num\n1\n2\n#\n"
    template = {"mmcif": cif, "queryIndices": [0, 1], "templateIndices": [0, 1]}
    protein = {"id": "A", "sequence": sequence, "unpairedMsa": ">q\nACDE\n>h\nAC--\n", "pairedMsa": ">q\nACDE\n", "templates": [template]}
    ligand = {"ligand": {"id": "B", "smiles": "CCO"}}
    payload = {"name": "synthetic-template-test", "modelSeeds": [1], "sequences": [{"protein": protein}, ligand]}
    input_path = tmp_path / "inference_input.json"
    input_path.write_text(json.dumps(payload))
    (tmp_path / "output").mkdir()
    (tmp_path / "output/fixture_data.json").write_text(json.dumps(payload))
    features = {key: protein[key] for key in ("sequence", "unpairedMsa", "pairedMsa", "templates")}
    def digest(data):
        return hashlib.sha256((json.dumps(data, separators=(",", ":")) + "\n").encode()).hexdigest()
    template_record = {"entry_id": "5IKR", "sha256": hashlib.sha256(cif.encode()).hexdigest(),
                       "mapping_sha256": digest({"queryIndices": [0, 1], "templateIndices": [0, 1]}),
                       "query_indices": [0, 1], "template_indices": [0, 1]}
    metadata = {"status": "ready", "protein_sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                "max_template_date": "2021-09-30", "feature_sha256": digest(features), "unpaired_msa_sequences": 2,
                "paired_msa_sequences": 1, "non_query_sequences": 1, "template_count": 1, "templates": [template_record]}
    entry = {"requested": {"msa_mode": "search", "execution_profile": {"max_template_date": "2021-09-30"}}, "msa_features": metadata,
             "job": {"payload": payload, "result": {"inference_input_sha256": audit.sha256(input_path)}}}
    metadata.update(database_fingerprint="fixture-database", af3_version="fixture-version", af3_commit="fixture-code")
    entry["requested"].update(submission_context={"database_fingerprint": "fixture-database"}, af3_version="fixture-version", af3_commit="fixture-code")
    entry["job"]["result"]["execution_provenance"] = {"databases": {"fingerprint_sha256": "fixture-database"}}
    result = audit.audit_msa_features(entry, tmp_path, sequence)
    assert result["template_5ikr_explicitly_present"] is True
    assert result["templates"][0]["query_indices"] == [0, 1]
    template_record["query_indices"] = [2, 3]
    with pytest.raises(audit.AuditError, match="mapping differs"):
        audit.audit_msa_features(entry, tmp_path, sequence)
    template_record["query_indices"] = [0, 1]
    template_record["entry_id"] = "OTHER"
    with pytest.raises(audit.AuditError, match="entry ID"):
        audit.audit_msa_features(entry, tmp_path, sequence)


def projected_payload():
    return {"name": "synthetic-output-data-test", "modelSeeds": [1], "sequences": [
        {"protein": {"id": "A", "sequence": "ACDE", "unpairedMsa": ">q\nACDE\n>h\nAC--\n",
                     "pairedMsa": ">q\nACDE\n", "templates": [{"mmcif": "synthetic", "queryIndices": [0], "templateIndices": [0]}]}},
        {"ligand": {"id": "B", "smiles": "CCO"}}]}


def test_actual_output_data_accepts_upstream_empty_defaults_without_whole_dict_equality(tmp_path):
    expected = projected_payload()
    actual = copy.deepcopy(expected)
    actual.update(bondedAtomPairs=None, userCCD=None, dialect="alphafold3", version=4)
    actual["sequences"][0]["protein"]["modifications"] = []
    actual["sequences"][1]["ligand"]["smiles"] = "OCC"
    output = tmp_path / "output"
    output.mkdir()
    (output / "fixture_data.json").write_text(json.dumps(actual))
    result = audit.verify_output_data(tmp_path, expected)
    assert result["matches_input"] is True
    assert result["protein_chain"] == "A" and result["ligand_chain"] == "B"
    assert len(result["sha256"]) == len(result["semantic_projection_sha256"]) == 64


@pytest.mark.parametrize("field", ["name", "seed", "protein_chain", "ligand_chain", "sequence", "ligand", "unpaired", "paired", "templates", "modifications", "bondedAtomPairs", "userCCD"])
def test_actual_output_data_rejects_changed_identity_or_features_even_if_inference_input_was_valid(tmp_path, field):
    expected = projected_payload()
    actual = copy.deepcopy(expected)
    protein, ligand = actual["sequences"][0]["protein"], actual["sequences"][1]["ligand"]
    destination, key, value = {
        "name": (actual, "name", "another-job"), "seed": (actual, "modelSeeds", [2]),
        "protein_chain": (protein, "id", "C"), "ligand_chain": (ligand, "id", "D"),
        "sequence": (protein, "sequence", "AAAA"), "ligand": (ligand, "smiles", "CCN"),
        "unpaired": (protein, "unpairedMsa", ""), "paired": (protein, "pairedMsa", ""),
        "templates": (protein, "templates", []),
        "modifications": (protein, "modifications", [{"ptmType": "SEP", "ptmPosition": 1}]),
        "bondedAtomPairs": (actual, "bondedAtomPairs", [[["A", 1, "CA"], ["B", 1, "C1"]]]),
        "userCCD": (actual, "userCCD", "different chemical-component dictionary"),
    }[field]
    destination[key] = value
    output = tmp_path / "output"
    output.mkdir()
    (output / "fixture_data.json").write_text(json.dumps(actual))
    with pytest.raises(audit.AuditError, match="output data semantics differ"):
        audit.verify_output_data(tmp_path, expected)


def test_missing_or_ambiguous_actual_output_data_is_not_accepted(tmp_path):
    with pytest.raises(audit.AuditError, match="Exactly one"):
        audit.verify_output_data(tmp_path, projected_payload())
    output = tmp_path / "output"
    output.mkdir()
    for name in ("a_data.json", "b_data.json"):
        (output / name).write_text(json.dumps(projected_payload()))
    with pytest.raises(audit.AuditError, match="Exactly one"):
        audit.verify_output_data(tmp_path, projected_payload())


def compared_job(positions, rmsd):
    return {"canonical_smiles": "CCO", "conditions": {"seeds": [1]}, "sample_ranges": {},
            "msa_features": {"template_5ikr_explicitly_present": False}, "top_ranked_copy": {
                "summary_metrics": {"ptm": .2, "iptm": .3, "ranking_score": .28},
                "full_protein_ca_plddt": {"mean": 30}, "ligand_heavy_atom_plddt": {"mean": 20},
                "reference_alignment": {"matched_uniprot_positions": positions, "rmsd_angstrom": rmsd}}}


@pytest.mark.parametrize("msa_positions", [[19, 20, 21], [19, 20, 21, 23]])
def test_rmsd_delta_is_withheld_for_missing_or_same_count_different_residue_sets(msa_positions):
    before = compared_job([19, 20, 21, 22], 30)
    after = compared_job(msa_positions, 1)
    result = audit.comparison(before, after)
    assert result["top_copy_delta_msa_minus_baseline"]["reference_ca_rmsd_angstrom"] is None
    rmsd = result["reference_ca_rmsd_comparison"]
    assert rmsd["comparable"] is False
    assert rmsd["status"] == "not_comparable_residue_sets_differ"
    assert rmsd["baseline_rmsd_angstrom"] == 30 and rmsd["msa_rmsd_angstrom"] == 1
    assert "cannot represent a structural improvement" in rmsd["reason"]


def test_rmsd_delta_uses_equal_residue_sets_regardless_of_report_order():
    result = audit.comparison(compared_job([19, 20, 21], 30), compared_job([21, 19, 20], 2))
    assert result["reference_ca_rmsd_comparison"]["comparable"] is True
    assert result["top_copy_delta_msa_minus_baseline"]["reference_ca_rmsd_angstrom"] == -28
