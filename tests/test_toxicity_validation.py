import numpy as np

from herbfold.toxicity_validation import curate_rows, quality_gate, scaffold_partition


def test_missing_labels_are_not_inactive_and_conflicts_are_excluded():
    rows, report = curate_rows([
        {"smiles": "CCO", "mol_id": "A", "NR-AR": "0", "NR-AhR": ""},
        {"smiles": "OCC", "mol_id": "B", "NR-AR": "1", "NR-AhR": "1"},
        {"smiles": "CCC", "mol_id": "C", "NR-AR": "7"},
        {"smiles": "[Na+]", "mol_id": "D", "NR-AR": "0"},
    ])
    merged = next(row for row in rows if row["smiles"] == "CCO")
    assert merged["labels"]["NR-AR"] is None
    assert merged["labels"]["NR-AhR"] == 1
    assert merged["labels"]["SR-p53"] is None
    assert report["conflicting_labels_excluded"]["NR-AR"] == 1
    assert report["invalid_labels_excluded"] == 1
    assert report["invalid_structures"] == 1


def test_holdout_never_splits_same_scaffold():
    records = [{"scaffold": str(i // 3), "smiles": str(i)} for i in range(60)]
    train, test = scaffold_partition(records)
    assert not set(train) & set(test)
    assert not {records[i]["scaffold"] for i in train} & {records[i]["scaffold"] for i in test}
    assert np.array_equal(scaffold_partition(records)[1], test)


def test_quality_gate_abstains_for_unsupported_or_baseline_inferior_model():
    metrics = {"roc_auc_ci95": [0.6, 0.9], "average_precision": 0.3,
               "prevalence": 0.1, "brier": 0.07, "train_prevalence_brier_baseline": 0.09}
    assert quality_gate(metrics, 30, 300, 200)
    assert not quality_gate(metrics, 2, 300, 200)
    assert not quality_gate({**metrics, "roc_auc_ci95": [0.4, 0.9]}, 30, 300, 200)
    assert not quality_gate({**metrics, "brier": 0.11}, 30, 300, 200)
