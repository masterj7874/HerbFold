"""Reproducible Tox21 pathway-activity validation, never a human safety verdict.

The fixed scaffold holdout, model settings, applicability threshold and quality
gate are declared before reading outcome metrics. Missing labels stay missing.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sklearn
from rdkit import Chem, DataStructs, rdBase
from rdkit.Chem import rdFingerprintGenerator
from rdkit.Chem.Scaffolds import MurckoScaffold
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit

from .chemistry import standardize_molecule

TASKS = (
    "NR-AR", "NR-AR-LBD", "NR-AhR", "NR-Aromatase", "NR-ER", "NR-ER-LBD",
    "NR-PPAR-gamma", "SR-ARE", "SR-ATAD5", "SR-HSE", "SR-MMP", "SR-p53",
)
SOURCE_URL = "https://deepchemdata.s3-us-west-1.amazonaws.com/datasets/tox21.csv.gz"
METHOD = "tox21-scaffold-rf-v1"
SEED = 20260908
DOMAIN_THRESHOLD = 0.40
_FP = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048, includeChirality=True)
LIMITATIONS = [
    "Tox21 labels describe activity in 12 nuclear receptor/stress-response assays, not human safety.",
    "Inactive, missing or abstained results do not establish absence of toxicity.",
    "No animal, clinical, Ames, organ toxicity, metabolism or exposure experiments were performed.",
    "Model scores are uncalibrated estimates of assay activity; they are not probabilities of human toxicity.",
    "A scaffold holdout is retrospective and cannot establish prospective or clinical performance.",
    "Applicability uses training-set Morgan similarity, not a confidence interval or validated safety cutoff.",
    "Standardized-structure matches remove counterions; assay identity may differ in salt/formulation/exposure.",
]


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def curate_rows(rows: list[dict]) -> tuple[list[dict], dict]:
    """Merge standardized identities, excluding each conflicting endpoint label."""
    buckets: dict[str, dict] = {}
    invalid, invalid_labels = 0, 0
    for row in rows:
        try:
            mol = standardize_molecule(row["smiles"])
            smiles = Chem.MolToSmiles(mol, isomericSmiles=True)
        except (ValueError, KeyError):
            invalid += 1
            continue
        if smiles not in buckets:
            scaffold = MurckoScaffold.GetScaffoldForMol(mol)
            buckets[smiles] = {
                "smiles": smiles,
                "scaffold": Chem.MolToSmiles(scaffold, isomericSmiles=False) or "ACYCLIC",
                "source_ids": [], "original_smiles": [], "values": defaultdict(set),
            }
        entry = buckets[smiles]
        entry["source_ids"].append(row.get("mol_id", ""))
        entry["original_smiles"].append(row["smiles"])
        for task in TASKS:
            raw = row.get(task)
            if raw in (None, "", "nan"):
                continue
            try:
                value = float(raw)
                if value not in (0, 1):
                    raise ValueError("Nonbinary assay label")
            except (ValueError, TypeError):
                invalid_labels += 1
                continue
            entry["values"][task].add(int(value))
    conflicts = dict.fromkeys(TASKS, 0)
    curated = []
    for entry in sorted(buckets.values(), key=lambda row: row["smiles"]):
        values = entry.pop("values")
        entry["labels"] = {}
        for task in TASKS:
            labels = values.get(task, set())
            if len(labels) > 1:
                conflicts[task] += 1
            entry["labels"][task] = next(iter(labels)) if len(labels) == 1 else None
        curated.append(entry)
    return curated, {
        "raw_rows": len(rows), "curated_structures": len(curated), "invalid_structures": invalid,
        "collapsed_duplicate_rows": len(rows) - invalid - len(curated),
        "invalid_labels_excluded": invalid_labels, "conflicting_labels_excluded": conflicts,
        "missing_labels": {task: sum(row["labels"][task] is None for row in curated) for task in TASKS},
    }


def scaffold_partition(records: list[dict], seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    groups = np.array([row["scaffold"] for row in records])
    if len(set(groups)) < 5:
        raise ValueError("At least five independent scaffold groups are required")
    train, test = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=seed).split(groups, groups=groups))
    if set(groups[train]) & set(groups[test]):
        raise AssertionError("Scaffold leakage")
    return train, test


def _metrics(labels: np.ndarray, scores: np.ndarray, groups: np.ndarray, bootstrap: int = 200) -> dict:
    prevalence = float(np.mean(labels))
    result = {"prevalence": prevalence, "brier": float(brier_score_loss(labels, scores)),
              "roc_auc": None, "average_precision": None, "roc_auc_ci95": None}
    if len(np.unique(labels)) < 2:
        return result
    result.update(roc_auc=float(roc_auc_score(labels, scores)),
                  average_precision=float(average_precision_score(labels, scores)))
    group_ids = np.unique(groups)
    if len(group_ids) < 5:
        return result
    rng = np.random.default_rng(SEED)
    blocks = [np.flatnonzero(groups == group) for group in group_ids]
    aucs = []
    for _ in range(bootstrap):
        indices = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        if len(np.unique(labels[indices])) == 2:
            aucs.append(roc_auc_score(labels[indices], scores[indices]))
    if len(aucs) >= max(20, bootstrap // 2):
        result["roc_auc_ci95"] = [float(value) for value in np.quantile(aucs, [0.025, 0.975])]
    return result


def quality_gate(metrics: dict, positive: int, negative: int, train_positive: int) -> bool:
    """Predeclared minimal retrospective usefulness; not a safety qualification."""
    interval = metrics.get("roc_auc_ci95")
    return bool(
        positive >= 20 and negative >= 20 and train_positive >= 30 and interval
        and interval[0] > 0.5 and metrics["average_precision"] > metrics["prevalence"]
        and metrics["brier"] < metrics["train_prevalence_brier_baseline"]
    )


def _fingerprints(smiles: list[str]) -> tuple[list, np.ndarray]:
    fps = [_FP.GetFingerprint(Chem.MolFromSmiles(value)) for value in smiles]
    array = np.zeros((len(fps), 2048), dtype=np.uint8)
    for index, fp in enumerate(fps):
        DataStructs.ConvertToNumpyArray(fp, array[index])
    return fps, array


def run_tox21_validation(dataset_path: Path, candidates_path: Path, output_root: Path) -> dict:
    started = time.perf_counter()
    output_root.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "running", "method": METHOD, "started_at": datetime.now(timezone.utc).isoformat(),
        "endpoints": [], "limitations": LIMITATIONS,
        "protocol": {
            "split": "80/20 scaffold groups; shared across endpoints; fixed before metrics",
            "seed": SEED, "fingerprint": "Morgan radius 2 / 2048 bits / chirality",
            "model": "RandomForestClassifier(n_estimators=128,min_samples_leaf=3,max_features=sqrt)",
            "quality_gate": "test positive/negative >=20; train positive >=30; scaffold bootstrap ROC lower95>0.5; AP>test prevalence; Brier<train-prevalence baseline",
            "applicability": f"Morgan Tanimoto to nearest labeled training molecule >= {DOMAIN_THRESHOLD}",
            "hyperparameter_search": False, "calibration": "none; uncalibrated assay scores",
            "rdkit_version": rdBase.rdkitVersion, "sklearn_version": sklearn.__version__,
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "candidates": {"total": 0, "report_path": str(output_root / "candidates.jsonl")},
    }
    summary_path = output_root / "summary.json"
    _write_json(summary_path, report)
    with gzip.open(dataset_path, "rt") as stream:
        records, curation = curate_rows(list(csv.DictReader(stream)))
    report["dataset"] = {
        **curation, "source_url": SOURCE_URL, "sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "bytes": dataset_path.stat().st_size, "path": str(dataset_path),
        "standardization": "RDKit Cleanup + FragmentParent; charge/stereo retained",
    }
    train, test = scaffold_partition(records)
    test_set = set(test)
    split_membership = [{"smiles": row["smiles"], "scaffold": row["scaffold"],
                         "split": "test" if index in test_set else "train"}
                        for index, row in enumerate(records)]
    split_path = output_root / "split-membership.jsonl"
    split_path.write_text("".join(json.dumps(row) + "\n" for row in split_membership))
    report["protocol"]["split_sha256"] = hashlib.sha256(split_path.read_bytes()).hexdigest()
    report["protocol"]["split_counts"] = {"train": len(train), "test": len(test),
                                           "overlapping_scaffolds": 0, "overlapping_structures": 0}
    (output_root / "curated-labels.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))
    fps, features = _fingerprints([row["smiles"] for row in records])
    groups = np.array([row["scaffold"] for row in records])
    candidate_map = {}
    for line in candidates_path.read_text().splitlines():
        row = json.loads(line)
        smiles = Chem.MolToSmiles(standardize_molecule(row["smiles"]), isomericSmiles=True)
        candidate_map.setdefault(smiles, {**row, "smiles": smiles})
    candidate_rows = list(candidate_map.values())
    if not candidate_rows:
        raise ValueError("No candidates to assess")
    candidate_fps, candidate_features = _fingerprints([row["smiles"] for row in candidate_rows])
    lookup = {row["smiles"]: row for row in records}
    results = [{
        "id": row["id"], "smiles": row["smiles"], "cohorts": row.get("cohorts", ["prior_campaign_complete"]),
        "observed_assays": [
            {"endpoint": task, "label": matched["labels"][task],
             "source_ids": matched["source_ids"], "original_smiles": matched["original_smiles"],
             "status": "standardized_structure_match", "source_url": SOURCE_URL}
            for matched in [lookup[row["smiles"]]] if row["smiles"] in lookup
            for task in TASKS if matched["labels"][task] is not None
        ] if row["smiles"] in lookup else [],
        "predictions": [], "nearest_training_similarity": 0.0, "in_domain": False,
        "interpretation": "Pathway assay activity only. No efficacy or human safety conclusion.",
    } for row in candidate_rows]
    report["candidates"].update(total=len(results),
        input_sha256=hashlib.sha256(candidates_path.read_bytes()).hexdigest(),
        exact_dataset_matches=sum(bool(row["observed_assays"]) for row in results))
    _write_json(summary_path, report)
    for task in TASKS:
        train_indices = np.array([i for i in train if records[i]["labels"][task] is not None], dtype=int)
        test_indices = np.array([i for i in test if records[i]["labels"][task] is not None], dtype=int)
        y_train = np.array([records[i]["labels"][task] for i in train_indices], dtype=int)
        y_test = np.array([records[i]["labels"][task] for i in test_indices], dtype=int)
        endpoint = {"endpoint": task, "labeled_count": len(train_indices) + len(test_indices),
                    "train_count": len(train_indices), "test_count": len(test_indices),
                    "test_positive": int(y_test.sum()), "train_positive": int(y_train.sum()),
                    "metrics": {}, "model_gate_passed": False}
        if len(np.unique(y_train)) < 2 or len(np.unique(y_test)) < 2:
            endpoint["reason"] = "insufficient_class_support"
            for result in results:
                result["predictions"].append({"endpoint": task, "status": "abstained", "score": None,
                                              "reason": "insufficient_class_support"})
            report["endpoints"].append(endpoint)
            _write_json(summary_path, report)
            continue
        model = RandomForestClassifier(n_estimators=128, min_samples_leaf=3, max_features="sqrt",
                                       random_state=SEED, n_jobs=2)
        model.fit(features[train_indices], y_train)
        scores = model.predict_proba(features[test_indices])[:, 1]
        metrics = _metrics(y_test, scores, groups[test_indices])
        metrics["train_prevalence_brier_baseline"] = float(
            brier_score_loss(y_test, np.full(len(y_test), np.mean(y_train))))
        gate = quality_gate(metrics, int(y_test.sum()), int((1 - y_test).sum()), int(y_train.sum()))
        train_fps = [fps[i] for i in train_indices]
        similarities = np.array([max(DataStructs.BulkTanimotoSimilarity(fps[i], train_fps)) for i in test_indices])
        domain = similarities >= DOMAIN_THRESHOLD
        domain_metrics = _metrics(y_test[domain], scores[domain], groups[test_indices][domain], bootstrap=100) if domain.any() else None
        endpoint.update(metrics=metrics, model_gate_passed=gate,
                        applicability={"threshold": DOMAIN_THRESHOLD, "test_coverage": float(domain.mean()),
                                       "test_in_domain": int(domain.sum()), "metrics_in_domain": domain_metrics})
        candidate_scores = model.predict_proba(candidate_features)[:, 1]
        candidate_similarities = [max(DataStructs.BulkTanimotoSimilarity(fp, train_fps)) for fp in candidate_fps]
        accepted = 0
        for result, score, similarity in zip(results, candidate_scores, candidate_similarities, strict=True):
            inside = similarity >= DOMAIN_THRESHOLD
            result["nearest_training_similarity"] = max(result["nearest_training_similarity"], float(similarity))
            result["in_domain"] = result["in_domain"] or inside
            observed = any(row["endpoint"] == task for row in result["observed_assays"])
            reason = "observed_assay_available" if observed else (
                "model_quality_gate_failed" if not gate else "outside_training_domain" if not inside else None)
            accepted += reason is None
            result["predictions"].append({"endpoint": task, "status": "predicted" if reason is None else "abstained",
                "score": float(score) if reason is None else None, "reason": reason,
                "nearest_training_similarity": float(similarity), "in_domain": bool(inside)})
        endpoint["candidate_predictions"] = accepted
        endpoint["candidate_abstentions"] = len(results) - accepted
        # Local artifact for replay only; never accept serialized models from API input.
        import joblib
        joblib.dump(model, output_root / f"{task}.joblib", compress=3)
        (output_root / f"{task}-holdout.jsonl").write_text("".join(json.dumps({
            "smiles": records[i]["smiles"], "scaffold": records[i]["scaffold"], "label": int(label),
            "score": float(score), "nearest_training_similarity": float(similarity),
        }) + "\n" for i, label, score, similarity in zip(test_indices, y_test, scores, similarities, strict=True)))
        report["endpoints"].append(endpoint)
        report["elapsed_seconds"] = time.perf_counter() - started
        _write_json(summary_path, report)
        print(json.dumps({"endpoint": task, "roc_auc": metrics["roc_auc"], "gate": gate,
                          "candidate_predictions": accepted, "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    output = output_root / "candidates.jsonl"
    temporary = output.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in results))
    temporary.replace(output)
    report["candidates"].update(
        in_domain=sum(row["in_domain"] for row in results),
        out_of_domain=sum(not row["in_domain"] for row in results),
        with_any_prediction=sum(any(p["status"] == "predicted" for p in row["predictions"]) for row in results),
        with_all_12_predictions=sum(all(p["status"] == "predicted" for p in row["predictions"]) for row in results),
        sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
    )
    report["candidates"]["cohorts"] = {
        cohort: {
            "total": sum(cohort in row["cohorts"] for row in results),
            "in_domain": sum(cohort in row["cohorts"] and row["in_domain"] for row in results),
            "out_of_domain": sum(cohort in row["cohorts"] and not row["in_domain"] for row in results),
            "observed_assay_matches": sum(cohort in row["cohorts"] and bool(row["observed_assays"]) for row in results),
            "with_any_prediction": sum(cohort in row["cohorts"] and any(p["status"] == "predicted" for p in row["predictions"]) for row in results),
        } for cohort in sorted({cohort for row in results for cohort in row["cohorts"]})
    }
    report.update(status="completed", completed_at=datetime.now(timezone.utc).isoformat(),
                  elapsed_seconds=time.perf_counter() - started)
    _write_json(summary_path, report)
    return report
