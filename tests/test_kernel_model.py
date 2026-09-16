"""All measured-looking values in this module are synthetic unit-test fixtures."""

import copy
import json

import numpy as np
import pytest

from herbfold import kernel_model, quantum


def records(with_structure=True):
    rings = [
        ("c1ccccc1", "Cc1ccccc1"),
        ("c1ccncc1", "Cc1ccncc1"),
        ("c1cncnc1", "Cc1cncnc1"),
        ("c1ccsc1", "Cc1ccsc1"),
        ("c1ccoc1", "Cc1ccoc1"),
        ("C1CCCCC1", "CC1CCCCC1"),
        ("N1CCCCC1", "CN1CCCCC1"),
        ("c1ncc[nH]1", "Cc1ncc[nH]1"),
    ]
    rows = []
    for i, pair in enumerate(rings):
        for j, smiles in enumerate(pair):
            row = {
                "smiles": smiles,
                "target_id": f"TEST_TARGET_{i}",
                "endpoint": "Kd",
                "value": 10 * (i + 1) ** 2 + j,
                "unit": "nM",
                "relation": "=",
                "is_measured": True,
                "source": "test://synthetic-only-not-experimental",
                "protein_sequence": "ACDEFGHIKLMNPQRSTVWY" + "A" * i,
            }
            if with_structure:
                row["structure_features"] = {
                    "contact_pairs": 20 + i * 2 + j,
                    "min_distance_angstrom": 2.1 + 0.1 * j,
                }
            rows.append(row)
    return rows


def test_experiment_standardizes_only_training_and_keeps_structure_features():
    experiment = kernel_model.prepare_experiment(records(), split="scaffold_target")
    features = np.asarray(experiment["features"])
    train = experiment["train_indices"]
    np.testing.assert_allclose(features[train].mean(axis=0), 0, atol=1e-12)
    assert experiment["scaler_fit_indices"] == train
    assert experiment["structure_features"]["included"] is True
    assert "structure:contact_pairs" in experiment["schema"]["names"]
    assert all(not values for values in experiment["overlap_audit"].values())
    assert experiment["kernel_circuits_required"] == 136
    assert experiment == kernel_model.prepare_experiment(records(), split="scaffold_target")
    json.dumps(experiment, allow_nan=False)


def test_heldout_structure_outliers_cannot_change_training_scaler_or_features():
    rows = records()
    before = kernel_model.prepare_experiment(rows)
    changed_index = before["input_test_indices"][0]
    rows[changed_index]["structure_features"]["contact_pairs"] = 1e9
    after = kernel_model.prepare_experiment(rows)
    assert before["scaler_mean"] == after["scaler_mean"]
    assert before["scaler_scale"] == after["scaler_scale"]
    train = before["train_indices"]
    np.testing.assert_array_equal(np.asarray(before["features"])[train], np.asarray(after["features"])[train])
    assert before["features"][changed_index] != after["features"][changed_index]


def test_heldout_labels_cannot_change_fitted_predictions():
    rows = records()
    experiment = kernel_model.prepare_experiment(rows)
    kernel = quantum.local_kernel(experiment["features"], n_qubits=3, max_circuits=136)
    before = kernel_model.evaluate_experiment(experiment, kernel)
    for i in experiment["input_test_indices"]:
        rows[i]["value"] *= 100
    after_experiment = kernel_model.prepare_experiment(rows)
    assert after_experiment["feature_sha256"] == experiment["feature_sha256"]
    after = kernel_model.evaluate_experiment(after_experiment, kernel)
    for old, new in zip(before["test_predictions"], after["test_predictions"]):
        assert old["quantum_predicted_pactivity"] == new["quantum_predicted_pactivity"]
        assert old["classical_rbf_predicted_pactivity"] == new["classical_rbf_predicted_pactivity"]
    assert before["metrics"] != after["metrics"]


def test_end_to_end_local_fits_only_declared_labels_and_reports_both_baselines():
    result = kernel_model.run_local_experiment(records(), qubits=3, split="scaffold_target")
    evaluation = result["evaluation"]
    assert set(evaluation["metrics"]) == {"quantum", "classical_rbf", "training_mean"}
    assert evaluation["train_count"] == 12 and evaluation["test_count"] == 4
    assert evaluation["kernel_provenance"]["hardware_executed"] is False
    assert evaluation["kernel_provenance"]["feature_provenance_verified"] is True
    assert evaluation["kernel_diagnostics"]["psd_projection_applied"] is False
    assert evaluation["metric_unit"] == "pKd"
    assert evaluation["metrics"]["quantum"]["mae"] >= 0
    json.dumps(result, allow_nan=False)


def test_noise_projection_is_training_only_and_test_test_block_is_unused():
    experiment = kernel_model.prepare_experiment(records())
    n = experiment["n_samples"]
    train, test = experiment["train_indices"], experiment["test_indices"]
    noisy = np.full((n, n), 0.7)
    np.fill_diagonal(noisy, 0.1)
    before = kernel_model.evaluate_experiment(experiment, noisy.tolist())
    noisy[np.ix_(test, test)] = np.eye(len(test))
    after = kernel_model.evaluate_experiment(experiment, noisy.tolist())
    assert before["kernel_diagnostics"]["raw_training_min_eigenvalue"] < -0.5
    assert before["kernel_diagnostics"]["psd_projection_applied"] is True
    assert before["kernel_diagnostics"]["test_test_block_used"] is False
    assert before["test_predictions"] == after["test_predictions"]
    assert before["metrics"] == after["metrics"]
    assert len(train) == 12


@pytest.mark.parametrize("kind", ["shape", "nan", "asymmetric", "out_of_range"])
def test_invalid_kernel_cannot_be_reported_as_performance(kind):
    experiment = kernel_model.prepare_experiment(records())
    kernel = np.eye(16)
    if kind == "shape":
        kernel = kernel[:5]
    elif kind == "nan":
        kernel[0, 0] = np.nan
    elif kind == "asymmetric":
        kernel[0, 1] = 0.4
    else:
        kernel[0, 0] = 2
    with pytest.raises(ValueError):
        kernel_model.evaluate_experiment(experiment, kernel.tolist())


def test_kernel_digest_and_completion_bind_runtime_results_to_the_experiment():
    experiment = kernel_model.prepare_experiment(records())
    result = {"kernel": np.eye(16).tolist(), "status": "completed", "plan": {"feature_sha256": "wrong"}}
    with pytest.raises(ValueError, match="fingerprint"):
        kernel_model.evaluate_experiment(experiment, result)
    result["plan"]["feature_sha256"] = experiment["feature_sha256"]
    result["status"] = "running"
    with pytest.raises(ValueError, match="completed"):
        kernel_model.evaluate_experiment(experiment, result)
    with pytest.raises(ValueError, match="complete kernel"):
        kernel_model.evaluate_experiment(experiment, {"status": "running"})


def test_changed_experiment_is_rejected_and_no_labels_are_inferred():
    experiment = kernel_model.prepare_experiment(records())
    experiment["labels"][0] += 1
    with pytest.raises(ValueError, match="digest"):
        kernel_model.evaluate_experiment(experiment, np.eye(16).tolist())
    rows = records()
    rows[0]["is_measured"] = False
    with pytest.raises(ValueError, match="is_measured"):
        kernel_model.prepare_experiment(rows)


def test_mixed_endpoints_keep_original_row_mapping_and_separate_units():
    rows = records()
    other = {**rows[0], "endpoint": "IC50"}
    experiment = kernel_model.prepare_experiment([other] + rows)
    assert experiment["data_audit"]["excluded_other_endpoints"] == {"IC50": 1}
    assert experiment["input_train_indices"] == [i + 1 for i in experiment["train_indices"]]
    for row in rows:
        row["endpoint"] = "Ki"
    assert kernel_model.prepare_experiment(rows, endpoint="Ki")["output_unit"] == "pKi"


def test_structure_schema_is_all_or_none_and_no_unseen_target_one_hot_leakage():
    rows = records()
    rows[0]["structure_features"] = {}
    with pytest.raises(ValueError, match="identical"):
        kernel_model.prepare_experiment(rows)
    rows = records(with_structure=False)
    for row in rows:
        del row["protein_sequence"]
    with pytest.raises(ValueError, match="Unseen target"):
        kernel_model.prepare_experiment(rows)
    for row in rows:
        row["target_id"] = "KNOWN_TARGET"
    experiment = kernel_model.prepare_experiment(rows)
    assert experiment["structure_features"]["included"] is False
    assert experiment["schema"]["protein_mode"] == "known_target_one_hot"


def test_sample_and_simulation_budgets_cannot_be_bypassed():
    with pytest.raises(ValueError, match="8–24"):
        kernel_model.prepare_experiment(records()[:6])
    rows = records()
    extra = copy.deepcopy(rows[:9])
    for row in extra:
        row["target_id"] += "_SECOND"
    with pytest.raises(ValueError, match="8–24"):
        kernel_model.prepare_experiment(rows + extra)
    with pytest.raises(ValueError, match="16"):
        kernel_model.run_local_experiment(records(), qubits=156)
