import json

import numpy as np
import pytest

from herbfold.bio_validation import (
    TARGETS,
    _prediction_evidence,
    conformal_half_width,
    curate_assay_records,
    evaluate_assay,
    run_bio_validation,
    scaffold_partitions,
)
from herbfold.chemistry import canonical_smiles, scaffold_key


def activity(identifier=1, smiles="CCO", value=10, endpoint="Ki", assay="A", **extra):
    return {"activity_id": identifier, "canonical_smiles": smiles, "standard_value": str(value),
            "standard_units": "nM", "standard_type": endpoint, "standard_relation": "=", "standard_flag": 1,
            "potential_duplicate": 0, "data_validity_comment": None, "requested_target": "PTGS2",
            "target_chembl_id": "CHEMBL230",
            "target_organism": "Homo sapiens", "assay_chembl_id": assay,
            "molecule_chembl_id": f"CHEMBL{identifier}", "document_chembl_id": "DOC", **extra}


def bundle(activities):
    ids = {row["assay_chembl_id"] for row in activities}
    return {"targets": TARGETS, "activities": activities, "retrieval": [],
            "assays": {identifier: {"assay_chembl_id": identifier, "target_chembl_id": "CHEMBL230",
                       "confidence_score": 9, "assay_type": "B", "description": "Synthetic unit-test fixture"}
                       for identifier in ids}}


def test_endpoint_assay_and_conflicting_replicates_are_not_pooled():
    source = bundle([
        activity(1), activity(2, smiles="OCC"), activity(3, endpoint="IC50"),
        activity(4, value=50, assay="B"), activity(5, smiles="CCN", value=1, assay="C"),
        activity(6, smiles="CCN", value=100, assay="C"),
    ])
    rows, report = curate_assay_records(source)
    assert len(rows) == 3
    assert {(row["assay_id"], row["endpoint"]) for row in rows} == {("A", "Ki"), ("A", "IC50"), ("B", "Ki")}
    assert next(row for row in rows if row["assay_id"] == "A" and row["endpoint"] == "Ki")["activity_ids"] == [1, 2]
    assert report["excluded"]["conflicting_same_assay_replicates"] == 2
    assert len(report["conflicting_groups"][0]["measurements"]) == 2
    assert rows[0]["pactivity"] == 8.0


def test_censored_flags_mutants_and_low_confidence_are_excluded():
    source = bundle([
        activity(1, standard_relation=">"), activity(2, potential_duplicate=1),
        activity(3, assay_variant_mutation="R123A"), activity(4, value=-1),
        activity(5, assay="LOW"), activity(6, target_organism="Mus musculus"),
        activity(7, target_chembl_id="WRONG_TARGET"), activity(8, standard_upper_value="20"),
    ])
    source["assays"]["LOW"]["confidence_score"] = 8
    rows, report = curate_assay_records(source)
    assert rows == []
    assert report["retained_rows"] == 0
    assert report["excluded"]["not_high_confidence_human_single_target"] == 3
    assert report["excluded"]["interval_or_range_measurement"] == 1


def test_four_partitions_are_scaffold_disjoint_and_cover_every_row_once():
    rows = [{"scaffold": f"group{i // 5}"} for i in range(200)]
    parts = scaffold_partitions(rows)
    all_indices = [i for indices in parts.values() for i in indices]
    assert sorted(all_indices) == list(range(200))
    sets = {name: {rows[i]["scaffold"] for i in indices} for name, indices in parts.items()}
    names = list(sets)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            assert sets[left].isdisjoint(sets[right])
    assert parts == scaffold_partitions(rows)


def test_conformal_interval_uses_finite_sample_rank():
    assert conformal_half_width(range(1, 21), 0.9) == 19
    for errors in ([], [float("nan")], [-1]):
        with pytest.raises(ValueError):
            conformal_half_width(errors)


def test_assay_with_too_few_structures_abstains():
    rows, _ = curate_assay_records(bundle([activity()]))
    report, model = evaluate_assay(rows)
    assert report["quality_status"] == "insufficient_data"
    assert model is None
    result = _prediction_evidence("PTGS2", "Ki", None, np.zeros(0), [])
    assert result["status"] == "abstained"
    assert "value_pactivity" not in result


def test_noise_model_is_evaluated_but_does_not_pass_calibration_gates():
    rng = np.random.default_rng(91)
    rows = []
    for ring in range(3, 23):
        for tail in range(1, 11):
            smiles = canonical_smiles("C1" + "C" * (ring - 2) + "C1" + "C" * tail)
            rows.append({"target": "PTGS2", "endpoint": "Ki", "assay_id": "synthetic-noise",
                         "assay_type": "B", "assay_description": "Synthetic test data, never report as measured",
                         "assay_source_url": "https://example.invalid/test", "smiles": smiles,
                         "scaffold": scaffold_key(smiles), "activity_ids": [len(rows)],
                         "pactivity": float(rng.uniform(1, 12))})
    report, model = evaluate_assay(rows)
    assert report["quality_status"] == "failed_validation"
    assert model is None
    assert {"median_baseline", "ridge", "random_forest"} == set(report["metrics"])
    assert report["uncertainty"] is not None
    assert report["reason"]


def test_out_of_domain_prediction_abstains_even_for_qualified_model():
    from rdkit import Chem
    from rdkit.Chem import rdFingerprintGenerator

    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048, includeChirality=True)
    query = generator.GetFingerprint(Chem.MolFromSmiles("[Na+]"))
    train = generator.GetFingerprint(Chem.MolFromSmiles("c1ccccc1"))
    model = {"report": {"target": "PTGS2", "endpoint": "Ki", "assay_id": "A", "id": "M",
                        "assay_source_url": "https://example.invalid/assay"},
             "threshold": 0.5, "train_fps": [train]}
    result = _prediction_evidence("PTGS2", "Ki", query, np.zeros(2048), [model])
    assert result["status"] == "abstained"
    assert result["nearest_similarity"] == 0
    assert "value_pactivity" not in result


def test_candidate_artifact_is_not_replaced_until_success_and_failure_status_is_atomic(tmp_path):
    previous = tmp_path / "candidates.jsonl"
    previous.write_text('previous complete result\n')

    def candidates():
        yield {"id": "1", "smiles": "CCO"}
        assert previous.read_text() == 'previous complete result\n'
        assert json.loads((tmp_path / "summary.json").read_text())["status"] == "running"
        raise OSError("interrupted candidate input")

    with pytest.raises(OSError, match="interrupted"):
        run_bio_validation(candidates(), tmp_path, source_bundle=bundle([activity()]))
    assert previous.read_text() == 'previous complete result\n'
    assert json.loads((tmp_path / "summary.json").read_text())["status"] == "failed"
    assert (tmp_path / "candidates.jsonl.part").exists()


def test_exact_measurement_and_alerts_are_separate_from_clinical_claims(tmp_path):
    result = run_bio_validation([{"id": "1", "smiles": "CCO", "cohort": "fixed_test"}], tmp_path,
                                source_bundle=bundle([activity()]))
    assert result["status"] == "completed"
    assert result["counts"]["exact_measured_candidates"] == 1
    assert result["cohort_counts"] == {"fixed_test": 1}
    candidate = json.loads((tmp_path / "candidates.jsonl").read_text())
    measured = [r for r in candidate["evidence"] if r["status"] == "exact_measured"]
    assert measured[0]["endpoint"] == "Ki"
    assert measured[0]["value_nM"] == 10
    assert measured[0]["activity_ids"] == [1]
    assert "QED" not in candidate and "safe" not in candidate
    assert candidate["catalog_identity"]["checked"] is False
    assert not (tmp_path / "candidates.jsonl.part").exists()
