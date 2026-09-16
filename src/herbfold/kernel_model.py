"""Measured-affinity kernel regression with a frozen, training-only feature fit.

The experiment artifact binds molecular/optional structure features, labels and
the group split to a digest. QPU execution remains in :mod:`herbfold.quantum`.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.metrics.pairwise import rbf_kernel
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import StandardScaler

from . import benchmark, quantum

FORMAT = "herbfold-kernel-experiment-v1"
MAX_SAMPLES = 24
ALPHA = 1.0  # A predeclared baseline setting; never tuned using held-out labels.


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _experiment_digest(experiment: dict) -> str:
    return _digest({key: value for key, value in experiment.items() if key != "experiment_sha256"})


def prepare_experiment(
    records: list[dict],
    endpoint: str = "Kd",
    split: str = "scaffold",
    test_fraction: float = 0.25,
    seed: int = 42,
) -> dict:
    """Freeze an 8–24 sample measured-data experiment before calculating kernels.

    Returned ``train_indices``/``test_indices`` address the *prepared feature
    matrix*. ``input_train_indices``/``input_test_indices`` address original
    records, which can differ when other endpoints have been excluded.
    """
    rows, audit = benchmark.prepare_records(records, endpoint)
    if not 8 <= len(rows) <= MAX_SAMPLES:
        raise ValueError(f"Kernel experiments require 8–{MAX_SAMPLES} unique measured {endpoint} records")
    if (
        isinstance(test_fraction, bool)
        or not isinstance(test_fraction, (int, float))
        or not 0.1 <= test_fraction <= 0.5
    ):
        raise ValueError("test_fraction must lie between 0.1 and 0.5")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("seed must be an integer from 0 to 2**32-1")
    groups = benchmark._groups(rows, split)
    if len(set(groups)) < 2:
        raise ValueError("At least two disjoint scaffold/target groups are needed")
    if split in ("target", "scaffold_target") and not all(row["protein_sequence"] for row in rows):
        raise ValueError("Target-disjoint evaluation requires protein_sequence for every record")
    splitter = GroupShuffleSplit(n_splits=1, test_size=test_fraction, random_state=seed)
    train, test = next(splitter.split(rows, groups=groups))
    if len(train) < 4 or len(test) < 2:
        raise ValueError("The disjoint split needs at least four training and two test records")
    training, testing = [rows[i] for i in train], [rows[i] for i in test]
    overlap = {
        "groups": sorted({groups[i] for i in train} & {groups[i] for i in test}),
        "scaffolds": sorted({r["scaffold"] for r in training} & {r["scaffold"] for r in testing}),
        "targets": sorted({r["target_id"] for r in training} & {r["target_id"] for r in testing}),
        "ligands": sorted({r["smiles"] for r in training} & {r["smiles"] for r in testing}),
    }
    if (
        overlap["groups"]
        or (split in ("scaffold", "scaffold_target") and overlap["scaffolds"])
        or (split in ("target", "scaffold_target") and overlap["targets"])
    ):
        raise ValueError("Split integrity check failed")
    # Even the one-hot vocabulary and optional structure schema come from train.
    schema = benchmark._schema(training)
    raw = benchmark._matrix(rows, schema)
    scaler = StandardScaler().fit(raw[train])
    features = scaler.transform(raw)
    labels = np.asarray([row["pactivity"] for row in rows])
    if np.std(labels[train]) < 1e-12:
        raise ValueError("Measured training labels must have nonzero variation")
    if not np.isfinite(features).all():
        raise ValueError("Features overflowed after training-only standardization")
    experiment = {
        "format": FORMAT,
        "endpoint": endpoint,
        "output_unit": f"p{endpoint}",
        "split": split,
        "test_fraction": float(test_fraction),
        "seed": seed,
        "n_samples": len(rows),
        "schema": schema,
        "features": features.tolist(),
        "labels": labels.tolist(),
        "train_indices": train.tolist(),
        "test_indices": test.tolist(),
        "input_train_indices": [rows[i]["input_index"] for i in train],
        "input_test_indices": [rows[i]["input_index"] for i in test],
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "scaler_fit_indices": train.tolist(),
        "groups": groups,
        "overlap_audit": overlap,
        "data_audit": audit,
        "rows": rows,
        "feature_sha256": quantum._fingerprint(features),
        "kernel_circuits_required": len(rows) * (len(rows) + 1) // 2,
        "regression": {
            "alpha": ALPHA,
            "label_centering": "training_mean_only",
            "classical_rbf_gamma": 1 / features.shape[1],
            "hyperparameter_tuning": "none",
            "quantum_psd_policy": "training_block_positive_eigenspace_projection",
        },
        "structure_features": {
            "included": bool(schema["structure_keys"]),
            "names": schema["structure_keys"],
            "provenance": "user_supplied; validate AF3 artifact/source separately",
        },
        "limitations": [
            "A small exploratory held-out comparison, not prospective or clinical validation.",
            "Source and measured-data declarations are required but are not independently verified.",
            "AF3 structure features are included only when supplied consistently for every selected row.",
            "Identical target identifiers/scaffolds are grouped; protein homology and assay batch leakage need separate curation.",
            "Repeatedly choosing splits or hyperparameters after inspecting test metrics invalidates the holdout.",
        ],
    }
    experiment["experiment_sha256"] = _experiment_digest(experiment)
    return experiment


def _validate_experiment(experiment: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if not isinstance(experiment, dict) or experiment.get("format") != FORMAT:
        raise ValueError("Unsupported kernel experiment artifact")
    try:
        if experiment.get("experiment_sha256") != _experiment_digest(experiment):
            raise ValueError("Experiment digest mismatch; use the unchanged prepared artifact")
        n = experiment["n_samples"]
        if isinstance(n, bool) or not isinstance(n, int) or not 8 <= n <= MAX_SAMPLES:
            raise ValueError("Invalid experiment sample count")
        features = np.asarray(experiment["features"], dtype=float)
        labels = np.asarray(experiment["labels"], dtype=float)
        if (
            features.ndim != 2
            or features.shape[0] != n
            or not 1 <= features.shape[1] <= quantum.MAX_FEATURES
            or labels.shape != (n,)
            or not np.isfinite(features).all()
            or not np.isfinite(labels).all()
        ):
            raise ValueError("Invalid experiment matrix or labels")
        train_raw, test_raw = experiment["train_indices"], experiment["test_indices"]
        if (
            not isinstance(train_raw, list)
            or not isinstance(test_raw, list)
            or any(isinstance(i, bool) or not isinstance(i, int) for i in train_raw + test_raw)
        ):
            raise ValueError("Split indices must be integer lists")
        train, test = np.asarray(train_raw, dtype=int), np.asarray(test_raw, dtype=int)
        if len(train) < 4 or len(test) < 2 or sorted(train_raw + test_raw) != list(range(n)):
            raise ValueError("Split must partition all rows into disjoint training/test sets")
        if experiment["scaler_fit_indices"] != train_raw:
            raise ValueError("Scaler was not fit on the training split")
        if experiment["feature_sha256"] != quantum._fingerprint(features):
            raise ValueError("Feature fingerprint mismatch")
        if experiment["endpoint"] not in ("Kd", "Ki") or np.std(labels[train]) < 1e-12:
            raise ValueError("Invalid or constant training endpoint")
        if (
            experiment["regression"]["alpha"] != ALPHA
            or experiment["regression"]["classical_rbf_gamma"] != 1 / features.shape[1]
        ):
            raise ValueError("Unsupported experiment hyperparameter policy")
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Malformed kernel experiment artifact") from exc
    return features, labels, train, test


def _read_kernel(kernel: Sequence[Sequence[float]] | dict, experiment: dict) -> tuple[np.ndarray, dict]:
    metadata: dict = {
        "origin": "supplied_matrix",
        "feature_provenance_verified": False,
        "hardware_executed": None,
    }
    if isinstance(kernel, dict):
        if "kernel" not in kernel:
            raise ValueError("Quantum job has no complete kernel; retrieve all successful jobs first")
        info = kernel.get("metadata", kernel.get("plan", {}))
        if info.get("feature_sha256") != experiment["feature_sha256"]:
            raise ValueError("Kernel feature fingerprint does not match the prepared experiment")
        if "plan" in kernel and kernel.get("status") != "completed":
            raise ValueError("IBM kernel job is not completed")
        metadata = {
            "origin": kernel.get("estimator", info.get("estimator", "quantum_result")),
            "feature_provenance_verified": True,
            "hardware_executed": kernel.get("hardware_executed", info.get("hardware_executed")),
            "n_qubits": info.get("n_qubits"),
            "layers": info.get("layers"),
            "backend_name": info.get("backend_name"),
            "encoding": info.get("encoding"),
            "kernel_method": kernel.get("method", info.get("kernel_method", "fidelity")),
            "feature_map": info.get("feature_map"),
            "block_size": info.get("block_size"),
            "gamma": info.get("gamma"),
            "shots": info.get("shots_used", info.get("shots")),
            "job_ids": [record["job_id"] for record in kernel.get("jobs", [])],
        }
        kernel = kernel["kernel"]
    try:
        matrix = np.asarray(kernel, dtype=float)
    except (ValueError, TypeError):
        raise ValueError("Kernel must be a finite square numerical matrix") from None
    n = experiment["n_samples"]
    if matrix.shape != (n, n) or not np.isfinite(matrix).all():
        raise ValueError("Kernel dimensions must match every prepared row and contain only finite values")
    if not np.allclose(matrix, matrix.T, atol=1e-10, rtol=0):
        raise ValueError("A quantum feature kernel must be symmetric")
    if matrix.min() < -1e-10 or matrix.max() > 1 + 1e-10:
        raise ValueError("Quantum feature kernel values must lie in [0, 1]")
    return (matrix + matrix.T) / 2, metadata


def _metrics(observed: np.ndarray, predicted: np.ndarray) -> dict:
    varying = np.std(observed) > 1e-12 and np.std(predicted) > 1e-12
    return {
        "mae": float(mean_absolute_error(observed, predicted)),
        "rmse": float(math.sqrt(mean_squared_error(observed, predicted))),
        "r2": float(r2_score(observed, predicted)) if np.std(observed) > 1e-12 else None,
        "pearson_r": float(np.corrcoef(observed, predicted)[0, 1]) if varying else None,
        "spearman_rho": float(spearmanr(observed, predicted).statistic) if varying else None,
    }


def evaluate_experiment(experiment: dict, kernel: Sequence[Sequence[float]] | dict) -> dict:
    """Compare fixed-alpha quantum and RBF kernel ridge on exactly the same split.

    Only the training kernel block is used for the spectral noise correction.
    Test-to-training entries produce predictions; test-to-test entries are never
    used to fit, stabilize, select hyperparameters, or predict.
    """
    features, labels, train, test = _validate_experiment(experiment)
    matrix, provenance = _read_kernel(kernel, experiment)
    train_kernel = matrix[np.ix_(train, train)]
    cross_kernel = matrix[np.ix_(test, train)]
    eigenvalues, eigenvectors = np.linalg.eigh(train_kernel)
    minimum = float(eigenvalues.min())
    corrected = minimum < -1e-10
    if corrected:
        positive = eigenvalues > 1e-10
        vectors = eigenvectors[:, positive]
        # Phi_train = V+ sqrt(Lambda+), Phi_test = K_* V+ / sqrt(Lambda+).
        # Their cross Gram is K_* V+ V+^T; no held-out eigensystem is fitted.
        train_kernel = (vectors * eigenvalues[positive]) @ vectors.T
        cross_kernel = cross_kernel @ vectors @ vectors.T
    mean = float(labels[train].mean())
    centered = labels[train] - mean
    dual = np.linalg.solve(train_kernel + ALPHA * np.eye(len(train)), centered)
    quantum_prediction = mean + cross_kernel @ dual
    gamma = experiment["regression"]["classical_rbf_gamma"]
    classical_train = rbf_kernel(features[train], features[train], gamma=gamma)
    classical_cross = rbf_kernel(features[test], features[train], gamma=gamma)
    classical_dual = np.linalg.solve(classical_train + ALPHA * np.eye(len(train)), centered)
    classical_prediction = mean + classical_cross @ classical_dual
    mean_prediction = np.full(len(test), mean)
    observed = labels[test]
    metrics = {
        "quantum": _metrics(observed, quantum_prediction),
        "classical_rbf": _metrics(observed, classical_prediction),
        "training_mean": _metrics(observed, mean_prediction),
    }
    warnings = list(experiment["limitations"])
    if corrected:
        warnings.append(
            "Noisy training kernel was indefinite: a training-only positive-eigenspace projection was applied; report raw and corrected status."
        )
    if not provenance["feature_provenance_verified"]:
        warnings.append(
            "A bare matrix has no verifiable feature fingerprint; caller must establish its origin and row order."
        )
    if provenance.get("hardware_executed") is not True:
        warnings.append("This result does not establish execution on a physical quantum processor.")
    warnings += [
        "Quantum feature similarity is converted to pactivity only through measured training labels in this fitted regression.",
        "A metric difference on one small holdout does not demonstrate quantum advantage; compare additional seeds/datasets prospectively.",
        "No calibrated predictive confidence interval or drug safety/efficacy conclusion is produced.",
    ]
    return {
        "format": "herbfold-kernel-evaluation-v1",
        "experiment_sha256": experiment["experiment_sha256"],
        "endpoint": experiment["endpoint"],
        "metric_unit": experiment["output_unit"],
        "split": experiment["split"],
        "seed": experiment["seed"],
        "train_count": len(train),
        "test_count": len(test),
        "train_indices": train.tolist(),
        "test_indices": test.tolist(),
        "input_train_indices": experiment["input_train_indices"],
        "input_test_indices": experiment["input_test_indices"],
        "overlap_audit": experiment["overlap_audit"],
        "data_audit": experiment["data_audit"],
        "metrics": metrics,
        "regression": experiment["regression"],
        "kernel_provenance": provenance,
        "structure_features": experiment["structure_features"],
        "kernel_diagnostics": {
            "raw_training_min_eigenvalue": minimum,
            "raw_training_mean_diagonal": float(np.diag(matrix[np.ix_(train, train)]).mean()),
            "raw_training_rank": int(np.linalg.matrix_rank(matrix[np.ix_(train, train)])),
            "psd_projection_applied": corrected,
            "discarded_negative_eigenvalues": int(np.count_nonzero(eigenvalues < -1e-10)),
            "test_test_block_used": False,
        },
        "test_predictions": [
            {
                "prepared_index": int(i),
                "input_index": experiment["rows"][i]["input_index"],
                "smiles": experiment["rows"][i]["smiles"],
                "target_id": experiment["rows"][i]["target_id"],
                "source": experiment["rows"][i]["source"],
                "measured_pactivity": float(y),
                "quantum_predicted_pactivity": float(q),
                "classical_rbf_predicted_pactivity": float(c),
                "training_mean_pactivity": mean,
                "prediction_is_measured": False,
                "unit": experiment["output_unit"],
            }
            for i, y, q, c in zip(test, observed, quantum_prediction, classical_prediction)
        ],
        "interpretation": "Exploratory measured-data kernel-ridge holdout comparison; no superiority, prospective or clinical validation claim.",
        "caveats": warnings,
    }


def run_local_experiment(
    records: list[dict],
    endpoint: str = "Kd",
    split: str = "scaffold",
    test_fraction: float = 0.25,
    seed: int = 42,
    qubits: int = 4,
    layers: int = 2,
) -> dict:
    """Prepare, calculate a bounded exact fidelity matrix, and evaluate the fit."""
    experiment = prepare_experiment(records, endpoint, split, test_fraction, seed)
    result = quantum.local_kernel(
        experiment["features"],
        n_qubits=qubits,
        layers=layers,
        max_circuits=experiment["kernel_circuits_required"],
    )
    return {
        "experiment": experiment,
        "quantum": result,
        "evaluation": evaluate_experiment(experiment, result),
    }
