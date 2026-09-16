"""Measured-endpoint ridge baseline with auditable splits and JSON model artifacts.

These functions never turn AF3 confidence, a quantum expectation, or an IC50 into
a measured dissociation constant. Data provenance is supplied by the researcher.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter

import numpy as np
from rdkit import DataStructs, rdBase
from rdkit.Chem import rdFingerprintGenerator
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import StandardScaler

from .chemistry import canonical_smiles, describe_molecule, scaffold_key, standardize_molecule

_DESC = ("molecular_weight", "logp", "tpsa", "hbd", "hba", "rotatable_bonds", "formal_charge", "qed")
_AA = "ACDEFGHIKLMNPQRSTVWY"
_FP_SIZE = 256
_FP = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=_FP_SIZE, includeChirality=True)
_UNITS = {"M": 1.0, "mM": 1e-3, "uM": 1e-6, "µM": 1e-6, "μM": 1e-6, "nM": 1e-9, "pM": 1e-12}


def to_pactivity(value: float, unit: str, endpoint: str = "Kd") -> float:
    if endpoint not in ("Kd", "Ki"):
        raise ValueError("Only Kd and Ki are supported, as separate endpoints; IC50 is not converted")
    if unit not in _UNITS:
        raise ValueError("Concentration unit must be M, mM, uM, µM, μM, nM, or pM")
    if isinstance(value, bool):
        raise ValueError("Measurement must be a finite positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Measurement must be a finite positive number") from exc
    concentration = number * _UNITS[unit]
    if not math.isfinite(concentration) or concentration <= 0:
        raise ValueError("Measurement must be a finite positive number")
    return -math.log10(concentration)


def _sequence(row: dict) -> str | None:
    value = row.get("protein_sequence")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("protein_sequence must be a sequence string")
    sequence = "".join(value.split()).upper()
    if not 20 <= len(sequence) <= 10000 or set(sequence) - set(_AA):
        raise ValueError("protein_sequence must contain 20–10000 standard amino-acid letters")
    return sequence


def _structure(row: dict) -> dict[str, float]:
    features = row.get("structure_features") or {}
    if not isinstance(features, dict) or len(features) > 64:
        raise ValueError("structure_features must be a dictionary of at most 64 numerical features")
    output = {}
    for key, value in features.items():
        if not isinstance(key, str) or not key or len(key) > 100 or isinstance(value, bool):
            raise ValueError("Invalid structure feature name/value")
        try:
            output[key] = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Structure features must be finite numerical values") from exc
        if not math.isfinite(output[key]):
            raise ValueError("Structure features must be finite numerical values")
    return output


def prepare_records(records: list[dict], endpoint: str = "Kd") -> tuple[list[dict], dict]:
    """Validate declared measured rows; explicitly count excluded other endpoints.

    Duplicate standardized ligand/target rows are rejected, rather than allowing
    the same pair to enter both partitions or silently pooling incomparable assays.
    """
    if endpoint not in ("Kd", "Ki"):
        raise ValueError("Choose a single endpoint: Kd or Ki")
    if not isinstance(records, list) or not 1 <= len(records) <= 5000:
        raise ValueError("Provide 1–5000 records")
    prepared, seen, excluded = [], set(), Counter()
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            raise ValueError(f"Row {index + 1}: expected a record object")
        if row.get("endpoint") != endpoint:
            excluded[str(row.get("endpoint", "missing_endpoint"))] += 1
            continue
        try:
            if row.get("relation") != "=":
                raise ValueError(
                    "Exact relation '=' is required; censored bounds cannot be fitted as exact values"
                )
            if row.get("is_measured") not in (True, "true", "True", "1"):
                raise ValueError("is_measured=true is required; predictions/simulations are not labels")
            source = row.get("source")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("A traceable source citation or accession is required")
            target = row.get("target_id")
            if not isinstance(target, str) or not target.strip() or len(target) > 200:
                raise ValueError("A nonempty target_id of at most 200 characters is required")
            target = target.strip()
            smiles = canonical_smiles(row.get("smiles"))
            identity = (smiles, target)
            if identity in seen:
                raise ValueError(
                    "Duplicate standardized ligand/target pair; curate comparable assay replicates first"
                )
            seen.add(identity)
            prepared.append(
                {
                    "smiles": smiles,
                    "target_id": target,
                    "endpoint": endpoint,
                    "pactivity": to_pactivity(row.get("value"), row.get("unit"), endpoint),
                    "scaffold": scaffold_key(smiles),
                    "source": source.strip(),
                    "assay_id": str(row.get("assay_id", "")),
                    "input_index": index,
                    "protein_sequence": _sequence(row),
                    "structure_features": _structure(row),
                }
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Row {index + 1}: {exc}") from exc
    if not prepared:
        raise ValueError(f"No exact, declared measured {endpoint} records remain")
    sequence_presence = {bool(row["protein_sequence"]) for row in prepared}
    if len(sequence_presence) > 1:
        raise ValueError("Supply protein_sequence for every selected record or none")
    target_sequences = {}
    for row in prepared:
        target = row["target_id"]
        if target in target_sequences and target_sequences[target] != row["protein_sequence"]:
            raise ValueError(
                f"Inconsistent sequences for target_id {target}; identify constructs/mutants separately"
            )
        target_sequences[target] = row["protein_sequence"]
    feature_schemas = {tuple(sorted(row["structure_features"])) for row in prepared}
    if len(feature_schemas) > 1:
        raise ValueError("All selected records must have identical structure_features keys")
    return prepared, {
        "input_count": len(records),
        "retained_count": len(prepared),
        "excluded_other_endpoints": dict(excluded),
        "endpoint": endpoint,
        "provenance_policy": "Researcher-declared measured data with required source; citations not automatically verified",
    }


def _fingerprint(smiles: str) -> np.ndarray:
    fingerprint = _FP.GetFingerprint(standardize_molecule(smiles))
    bits = np.zeros(_FP_SIZE, dtype=float)
    DataStructs.ConvertToNumpyArray(fingerprint, bits)
    return bits


def _schema(rows: list[dict]) -> dict:
    sequence_mode = all(row["protein_sequence"] for row in rows)
    targets = sorted({row["target_id"] for row in rows})
    structure_keys = sorted(rows[0]["structure_features"])
    names = list(_DESC) + [f"morgan_{i}" for i in range(_FP_SIZE)]
    names += [f"structure:{key}" for key in structure_keys]
    names += (
        [f"aa_fraction:{aa}" for aa in _AA] + ["log_protein_length"]
        if sequence_mode
        else [f"target:{target}" for target in targets]
    )
    return {
        "names": names,
        "protein_mode": "sequence_composition" if sequence_mode else "known_target_one_hot",
        "targets": targets,
        "structure_keys": structure_keys,
        "target_sequences": {row["target_id"]: row["protein_sequence"] for row in rows},
    }


def _matrix(rows: list[dict], schema: dict) -> np.ndarray:
    features = []
    for row in rows:
        target = row["target_id"]
        descriptor = describe_molecule(row["smiles"])
        values = [descriptor[key] for key in _DESC] + _fingerprint(row["smiles"]).tolist()
        if sorted(row["structure_features"]) != schema["structure_keys"]:
            raise ValueError("Query structure_features must match the training feature schema exactly")
        values += [row["structure_features"][key] for key in schema["structure_keys"]]
        if schema["protein_mode"] == "sequence_composition":
            sequence = row.get("protein_sequence")
            if not sequence:
                raise ValueError(
                    "Every query needs protein_sequence because the model was trained with sequences"
                )
            known_sequence = schema["target_sequences"].get(target)
            if known_sequence and sequence != known_sequence:
                raise ValueError("Query sequence does not match the training target construct")
            values += [sequence.count(aa) / len(sequence) for aa in _AA] + [math.log1p(len(sequence))]
        else:
            if target not in schema["targets"]:
                raise ValueError("Unseen target requires sequence features in training AND query records")
            values += [float(target == known) for known in schema["targets"]]
        features.append(values)
    return np.asarray(features, dtype=float)


def _fit(rows: list[dict], endpoint: str, alpha: float = 10.0) -> dict:
    if len(rows) < 4:
        raise ValueError("At least four unique measured ligand/target pairs are needed for fitting")
    if (
        not isinstance(alpha, (float, int))
        or isinstance(alpha, bool)
        or not math.isfinite(alpha)
        or alpha <= 0
    ):
        raise ValueError("Ridge alpha must be finite and positive")
    schema = _schema(rows)
    matrix = _matrix(rows, schema)
    y = np.asarray([row["pactivity"] for row in rows])
    if float(np.std(y)) < 1e-12:
        raise ValueError("Measured training endpoints have no variation")
    scaler = StandardScaler().fit(matrix)
    estimator = Ridge(alpha=alpha, solver="svd").fit(scaler.transform(matrix), y)
    digest_rows = [
        {
            key: row[key]
            for key in (
                "smiles",
                "target_id",
                "pactivity",
                "source",
                "assay_id",
                "protein_sequence",
                "structure_features",
            )
        }
        for row in rows
    ]
    digest = hashlib.sha256(
        json.dumps(digest_rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "format": "herbfold-ridge-v1",
        "endpoint": endpoint,
        "output_unit": f"p{endpoint}",
        "method": "StandardScaler + Ridge; ligand Morgan/descriptors + protein features + optional structure features",
        "alpha": float(alpha),
        "schema": schema,
        "fingerprint": {"method": "Morgan", "radius": 2, "bits": _FP_SIZE, "chirality": True},
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coefficients": estimator.coef_.tolist(),
        "intercept": float(estimator.intercept_),
        "training_count": len(rows),
        "training_sha256": digest,
        "training_smiles": sorted({row["smiles"] for row in rows}),
        "training_pactivity_range": [float(y.min()), float(y.max())],
        "training_sources": sorted({row["source"] for row in rows}),
        "rdkit_version": rdBase.rdkitVersion,
        "validation_status": "unvalidated_fit; evaluate on held-out measured records before interpretation",
        "limitations": [
            "A descriptor baseline; no AlphaFold model or quantum advantage is claimed.",
            "Protein sequence composition is coarse and does not model target-specific contacts.",
            "Predictions do not establish efficacy, safety, selectivity, or clinical utility.",
        ],
    }


def train_model(records: list[dict], endpoint: str = "Kd", alpha: float = 10.0) -> dict:
    rows, audit = prepare_records(records, endpoint)
    model = _fit(rows, endpoint, alpha)
    model["data_audit"] = audit
    return model


def predict_model(model: dict, query_records: list[dict]) -> dict:
    """Predict with JSON numerical artifacts only; never loads executable pickle data."""
    if model.get("format") != "herbfold-ridge-v1" or model.get("endpoint") not in ("Kd", "Ki"):
        raise ValueError("Unsupported model artifact")
    if not isinstance(query_records, list) or not 1 <= len(query_records) <= 1000:
        raise ValueError("Provide 1–1000 query records")
    queries = []
    for row in query_records:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("target_id"), str)
            or not row["target_id"].strip()
        ):
            raise ValueError("Every query requires smiles and a nonempty target_id")
        queries.append(
            {
                "smiles": canonical_smiles(row.get("smiles")),
                "target_id": row["target_id"].strip(),
                "protein_sequence": _sequence(row),
                "structure_features": _structure(row),
            }
        )
    try:
        schema = model["schema"]
        if len(schema["names"]) > 6000:
            raise ValueError("Invalid feature schema")
        matrix = _matrix(queries, schema)
        mean = np.asarray(model["scaler_mean"], dtype=float)
        scale = np.asarray(model["scaler_scale"], dtype=float)
        coef = np.asarray(model["coefficients"], dtype=float)
        intercept = float(model["intercept"])
        expected = (matrix.shape[1],)
        if any(array.shape != expected or not np.all(np.isfinite(array)) for array in (mean, scale, coef)):
            raise ValueError("Invalid model array dimensions or non-finite coefficients")
        if len(schema["names"]) != matrix.shape[1] or np.any(scale <= 0) or not math.isfinite(intercept):
            raise ValueError("Invalid model scaler or schema")
        predicted = ((matrix - mean) / scale) @ coef + intercept
        train_fps = [_fingerprint(smiles) for smiles in model["training_smiles"]]
        if not train_fps or len(train_fps) > 5000:
            raise ValueError("Invalid training structure metadata")
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Malformed model artifact") from exc
    output = []
    for row, value in zip(queries, predicted):
        if not math.isfinite(float(value)):
            raise ValueError("Non-finite model prediction")
        fp = _fingerprint(row["smiles"])
        similarities = [
            float(np.minimum(fp, known).sum() / max(np.maximum(fp, known).sum(), 1)) for known in train_fps
        ]
        max_similarity = max(similarities)
        output.append(
            {
                "smiles": row["smiles"],
                "target_id": row["target_id"],
                "predicted_pactivity": float(value),
                "endpoint": model["endpoint"],
                "unit": f"p{model['endpoint']}",
                "is_measured": False,
                "max_training_tanimoto": max_similarity,
                "applicability": "low_structural_similarity" if max_similarity < 0.3 else "not_calibrated",
                "unseen_target": row["target_id"] not in schema["targets"],
            }
        )
    return {
        "predictions": output,
        "model_training_sha256": model["training_sha256"],
        "interpretation": "Exploratory fitted baseline predictions; similarity is not a calibrated uncertainty interval.",
    }


def predict_records(training_records: list[dict], query_records: list[dict], endpoint: str = "Kd") -> dict:
    model = train_model(training_records, endpoint)
    return {**predict_model(model, query_records), "model": model}


def _groups(rows: list[dict], split: str) -> list[str]:
    if split in ("scaffold", "target"):
        key = "scaffold" if split == "scaffold" else "target_id"
        return [row[key] for row in rows]
    if split != "scaffold_target":
        raise ValueError("split must be scaffold, target, or scaffold_target")
    # Connected components ensure neither shared targets nor shared scaffolds cross.
    parents = list(range(len(rows)))

    def find(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    owners = {}
    for i, row in enumerate(rows):
        for key in (("scaffold", row["scaffold"]), ("target", row["target_id"])):
            if key in owners:
                parents[find(i)] = find(owners[key])
            else:
                owners[key] = i
    return [str(find(i)) for i in range(len(rows))]


def evaluate_records(
    records: list[dict],
    endpoint: str = "Kd",
    split: str = "scaffold_target",
    test_fraction: float = 0.25,
    seed: int = 42,
) -> dict:
    rows, audit = prepare_records(records, endpoint)
    if not isinstance(test_fraction, (float, int)) or not 0.1 <= test_fraction <= 0.5:
        raise ValueError("test_fraction must lie between 0.1 and 0.5")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**32:
        raise ValueError("seed must be an integer from 0 to 2**32-1")
    if len(rows) < 8:
        raise ValueError(
            "At least eight unique measured pairs are required for an exploratory held-out evaluation"
        )
    groups = _groups(rows, split)
    if len(set(groups)) < 2:
        raise ValueError(
            "Fewer than two disjoint groups; collect independent scaffolds/targets or explicitly select an appropriate split"
        )
    if split in ("target", "scaffold_target") and not all(row["protein_sequence"] for row in rows):
        raise ValueError("Target-disjoint evaluation requires protein_sequence for every record")
    splitter = GroupShuffleSplit(n_splits=1, test_size=test_fraction, random_state=seed)
    train_indices, test_indices = next(splitter.split(rows, groups=groups))
    if len(train_indices) < 4 or len(test_indices) < 2:
        raise ValueError("The requested disjoint split needs at least four training and two test records")
    training, testing = [rows[i] for i in train_indices], [rows[i] for i in test_indices]
    overlaps = {
        "scaffolds": sorted({row["scaffold"] for row in training} & {row["scaffold"] for row in testing}),
        "targets": sorted({row["target_id"] for row in training} & {row["target_id"] for row in testing}),
        "ligands": sorted({row["smiles"] for row in training} & {row["smiles"] for row in testing}),
        "ligand_target_pairs": sorted(
            {(row["smiles"], row["target_id"]) for row in training}
            & {(row["smiles"], row["target_id"]) for row in testing}
        ),
    }
    if (
        overlaps["ligand_target_pairs"]
        or (split in ("scaffold", "scaffold_target") and overlaps["scaffolds"])
        or (split in ("target", "scaffold_target") and overlaps["targets"])
    ):
        raise ValueError("Internal split integrity check failed")
    model = _fit(training, endpoint)
    prediction = predict_model(model, testing)
    y = np.array([row["pactivity"] for row in testing])
    yhat = np.array([row["predicted_pactivity"] for row in prediction["predictions"]])
    has_variation = np.std(y) > 1e-12 and np.std(yhat) > 1e-12
    metrics = {
        "mae": float(mean_absolute_error(y, yhat)),
        "rmse": float(math.sqrt(mean_squared_error(y, yhat))),
        "r2": float(r2_score(y, yhat)) if np.std(y) > 1e-12 else None,
        "pearson_r": float(np.corrcoef(y, yhat)[0, 1]) if has_variation else None,
        "spearman_rho": float(spearmanr(y, yhat).statistic) if has_variation else None,
    }
    mean_prediction = np.full_like(y, np.mean([row["pactivity"] for row in training]))
    baseline = {
        "mae": float(mean_absolute_error(y, mean_prediction)),
        "rmse": float(math.sqrt(mean_squared_error(y, mean_prediction))),
    }
    test_groups = np.asarray([groups[i] for i in test_indices])
    distinct_test_groups = sorted(set(test_groups))
    intervals = None
    if len(distinct_test_groups) >= 3:
        rng = np.random.default_rng(seed)
        boot = []
        for _ in range(500):
            sampled = rng.choice(distinct_test_groups, size=len(distinct_test_groups), replace=True)
            indices = np.concatenate([np.flatnonzero(test_groups == group) for group in sampled])
            error = y[indices] - yhat[indices]
            boot.append([np.abs(error).mean(), np.sqrt(np.square(error).mean())])
        bounds = np.quantile(np.asarray(boot), [0.025, 0.975], axis=0)
        intervals = {
            "mae": bounds[:, 0].tolist(),
            "rmse": bounds[:, 1].tolist(),
            "method": "500 test-group bootstrap resamples; percentile 95%; exploratory",
        }
    return {
        "endpoint": endpoint,
        "metric_unit": f"p{endpoint}",
        "split": split,
        "train_count": len(training),
        "test_count": len(testing),
        "train_indices": [rows[i]["input_index"] for i in train_indices],
        "test_indices": [rows[i]["input_index"] for i in test_indices],
        "train_groups": len(set(groups[i] for i in train_indices)),
        "test_groups": len(distinct_test_groups),
        "overlap_audit": overlaps,
        "data_audit": audit,
        "metrics": metrics,
        "training_mean_baseline": baseline,
        "bootstrap_95_percent": intervals,
        "test_predictions": [
            {**pred, "measured_pactivity": float(observed), "source": row["source"]}
            for pred, observed, row in zip(prediction["predictions"], y, testing)
        ],
        "training_sha256": model["training_sha256"],
        "seed": seed,
        "interpretation": "Single held-out measured-data descriptor-baseline evaluation; not prospective, AF3, quantum, or clinical validation.",
        "limitations": [
            "Scaffold and target identifier separation does not ensure low protein sequence homology.",
            "No confidence interval is returned with fewer than three independent test groups.",
            "A scaffold-only split can share targets; a target-only split can share ligand scaffolds.",
            "Assay comparability, natural-product provenance, and source truth require researcher curation.",
        ],
    }
