#!/usr/bin/env python3
"""Archived assay audit and explicitly separated post-review CPU reconstruction.

Outputs are confined to research/manuscript/round1/assays unless --output names
another new tmp report directory. Default audit never fits or infers. Optional
--reconstruct-rf-only fits only the already selected fixed RF in a new report
subdirectory. No network, serialized-model loading or hardware job submission.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import hashlib
import io
import json
import math
import platform
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy
import sklearn
from rdkit import Chem, DataStructs, rdBase
from rdkit.Chem import rdFingerprintGenerator
from scipy.stats import spearmanr
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
BIO = Path("runtime/validation/bio-validation")
TOX = Path("runtime/validation/tox21")
BOOTSTRAP_SEED = 20260909
BOOTSTRAP_REPLICATES = 2000
SENSITIVITY_CUTOFFS = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path, value):
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_csv(path, rows):
    if not rows:
        return
    columns = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    k: json.dumps(clean(v), ensure_ascii=False) if isinstance(v, (list, dict)) else clean(v)
                    for k, v in row.items()
                }
            )


class Audit:
    def __init__(self):
        self.sources = {}
        self.checks = []

    def read(self, path):
        path = ROOT / path
        raw = path.read_bytes()
        rel = str(path.relative_to(ROOT))
        self.sources[rel] = {"path": rel, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        return raw

    def json(self, path):
        return json.loads(self.read(path))

    def jsonl(self, path):
        return [json.loads(line) for line in self.read(path).splitlines() if line]

    def check(self, name, condition, details=None):
        passed = bool(condition)
        self.checks.append({"check": name, "passed": passed, "details": details})
        if not passed:
            raise AssertionError(name)

    def close(self, name, actual, expected, atol=1e-12):
        self.check(
            name,
            np.allclose(actual, expected, rtol=1e-11, atol=atol),
            {"actual": clean(actual), "expected": clean(expected)},
        )

    def unchanged(self):
        for row in list(self.sources.values()):
            self.check(f"unchanged:{row['path']}", digest(ROOT / row["path"]) == row["sha256"])


def literals(source):
    result = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    result[target.id] = value
    return result


def gates(bio_source, tox_source, chemistry_source, policy):
    rows = []
    sources = {
        "bio": ("src/herbfold/bio_validation.py", bio_source),
        "tox21": ("src/herbfold/toxicity_validation.py", tox_source),
        "identity": ("src/herbfold/chemistry.py", chemistry_source),
    }

    def add(system, stage, rule, comparison, threshold, unit, scope, anchor, rationale):
        path, code = sources[system]
        line = next(i for i, text in enumerate(code.splitlines(), 1) if anchor in text)
        rows.append(
            {
                "system": system,
                "execution_order": len(rows) + 1,
                "stage": stage,
                "criterion": rule,
                "comparison": comparison,
                "threshold": threshold,
                "unit": unit,
                "scope": scope,
                "source_path": path,
                "source_line": line,
                "source_anchor": anchor,
                "rationale": rationale,
                "rationale_status": "Code purpose/operational interpretation; numeric cutoff not independently validated",
                "chronology": "Executable/archived policy is observable; preregistration or outcome-blind historical choice not established",
            }
        )

    for rule, comparison, value, anchor, rationale in [
        (
            "SMILES input length",
            "1..4096 nonblank",
            4096,
            "len(smiles) > 4096",
            "Bound small-molecule input size",
        ),
        (
            "Molecule parsing",
            "valid and atom count >0",
            0,
            "mol.GetNumAtoms() == 0",
            "Reject unparseable/empty graphs",
        ),
        (
            "Pre-fragment heavy atom limit",
            "<=",
            256,
            "GetNumHeavyAtoms() > 256",
            "Small-molecule scope, applied before removing counterions",
        ),
        ("Wildcard atom count", "=", 0, "GetAtomicNum() == 0", "Exclude incomplete attachment graphs"),
        ("Parent carbon atoms", ">=", 1, "GetAtomicNum() == 6", "Restrict to organic parent structures"),
    ]:
        add(
            "identity",
            "structure eligibility",
            rule,
            comparison,
            value,
            "graph/input",
            "bio and Tox21 curation/candidates",
            anchor,
            rationale,
        )
    for rule, comp, val, anchor, rationale in [
        (
            "Requested target identity",
            "exact",
            "human SINGLE PROTEIN plus singleton requested UniProt accession",
            "accessions !=",
            "Bind each target to official identity",
        ),
        (
            "Activity request cap",
            "1..50000; default 5000 per target",
            5000,
            "max_activity_records: int = 5000",
            "Bound retrieval; observed run downloaded all advertised exact-nM rows",
        ),
        (
            "Endpoint pagination",
            "ascending activity_id; page <=1000",
            1000,
            "limit = min(1000",
            "Deterministic bounded retrieval; endpoints consume a shared target budget in listed order",
        ),
        (
            "Verified target and assay metadata",
            "present",
            True,
            "target is None or assay is None",
            "Require source records for assignment",
        ),
        (
            "Target assignment confidence",
            "=",
            9,
            "confidence_score",
            "Restrict to ChEMBL high-confidence target assignments",
        ),
        (
            "Assay/activity target and organism",
            "exact",
            "requested target IDs; Homo sapiens",
            'original.get("target_organism")',
            "Do not pool species or targets",
        ),
        (
            "Variant fields",
            "absent",
            "mutation and variant accession",
            "assay_variant_mutation",
            "Exclude variant/mutant assay contexts",
        ),
        (
            "Endpoint and censoring",
            "endpoint allowed; relation =",
            "PTGS2 Ki/Kd/IC50; KCNH2 IC50",
            "endpoint not in target",
            "Keep endpoints separate and exclude censored measurements",
        ),
        (
            "Upper bound",
            "absent",
            "None or empty",
            "standard_upper_value",
            "Exclude interval/range measurements",
        ),
        (
            "Units and standard flag",
            "exact",
            "nM; standard_flag=1",
            "standard_units",
            "Comparable standardized concentration units",
        ),
        (
            "Source validity and duplicate flags",
            "false/absent",
            "data_validity_comment; potential_duplicate",
            "data_validity_comment",
            "Respect source quality annotations",
        ),
        ("Concentration", "finite and >", 0, "value <= 0", "Define pActivity=9-log10(nM)"),
        (
            "Replicate agreement",
            "one distinct rounded pActivity",
            "round(...,10)",
            'round(row["pactivity"], 10)',
            "Exclude conflicting same-assay identity groups; merge exact duplicates without averaging",
        ),
        (
            "Assay selection",
            "largest unique count; assay ID tie break",
            1,
            "sorted(assays, key=",
            "One assay per target/endpoint/type chosen by sample size, not favourable measured outcomes",
        ),
    ]:
        add(
            "bio",
            "source curation/selection",
            rule,
            comp,
            val,
            "record/context",
            "before model fitting",
            anchor,
            rationale,
        )
    for key, comp, scope in [
        ("min_unique_structures", ">=", "assay"),
        ("min_scaffolds", ">=", "assay"),
        ("min_train", ">=", "training partition"),
        ("min_tune", ">=", "tuning partition"),
        ("min_calibration", ">=", "calibration partition"),
        ("min_test", ">=", "test partition"),
    ]:
        add(
            "bio",
            "model sample support",
            key,
            comp,
            policy[key],
            "structures or scaffold groups",
            scope,
            '"' + key + '"',
            "Minimum sample support; no random-split replacement for failed scaffold partitions",
        )
    add(
        "bio",
        "partition",
        "GroupShuffleSplit sequence",
        "test fraction of scaffold groups",
        [0.2, 0.1875, 0.230769],
        "fractions",
        "test; calibration within rest; tuning within train+tune",
        "test_size=0.2",
        "Four scaffold-disjoint partitions; molecule fractions need not equal group fractions",
    )
    add(
        "bio",
        "partition",
        "Partition seeds",
        "base,+1,+2",
        [2026, 2027, 2028],
        "seed",
        "three split calls",
        "random_state=random_state",
        "Recorded deterministic partitions",
    )
    add(
        "bio",
        "tuning",
        "RF versus Ridge model selection",
        "minimum tuning MAE",
        "RF128 leaf3 max_features0.33; Ridge alpha10 lsqr",
        "pActivity",
        "fit train only; select on tuning; tie picks insertion-first Ridge",
        "selected = min(estimators",
        "Test is excluded from model selection, but later used in eligibility reporting",
    )
    add(
        "bio",
        "applicability",
        "Nearest training Morgan Tanimoto",
        ">=",
        policy["similarity_threshold"],
        "similarity",
        "calibration/test/candidate",
        "cal_domain =",
        "Restrict to training-neighbourhood support; not a confidence interval",
    )
    add(
        "bio",
        "quality",
        "In-domain calibration/test counts",
        ">=",
        [policy["min_calibration"], policy["min_test"]],
        "structures",
        "selected model",
        "Insufficient in-domain",
        "Require evaluation support after applicability restriction",
    )
    for rule, comp, value, unit, anchor, rationale in [
        (
            "Nominal residual interval coverage",
            "ceil((n+1)c), capped at n",
            policy["nominal_coverage"],
            "fraction",
            "rank = min",
            "Order statistic of in-domain calibration absolute residuals; scaffold shift invalidates unconditional coverage guarantees",
        ),
        (
            "Interval half width",
            "<=",
            policy["max_interval_half_width"],
            "pActivity",
            "half_width >",
            "Limit imprecision",
        ),
        (
            "Empirical interval test coverage",
            ">=",
            policy["min_empirical_interval_coverage"],
            "fraction",
            "coverage <",
            "Selected model must cover sufficient in-domain held-out observations",
        ),
        (
            "Selected-model in-domain test MAE",
            "<=",
            policy["max_test_mae"],
            "pActivity",
            'measured["mae"] >',
            "Operational maximum held-out error",
        ),
        (
            "Relative median-baseline improvement",
            "> strict",
            policy["min_relative_baseline_improvement"],
            "fraction",
            'comparison["mae"] *',
            "Selected model MAE must be strictly less than baseline MAE times 0.95",
        ),
        (
            "In-domain held-out R2",
            "> strict",
            0,
            "R2",
            'measured["r2"] <= 0',
            "Require positive explained variation relative to test-mean denominator",
        ),
    ]:
        add(
            "bio",
            "quality",
            rule,
            comp,
            value,
            unit,
            "calibration/test outcome-conditioned gate",
            anchor,
            rationale,
        )
    add(
        "bio",
        "candidate",
        "Measured same-structure observations",
        "precede predictions",
        "up to20 rows per endpoint; conflicts flagged",
        "records",
        "candidate endpoint",
        "matches[:20]",
        "Retain source observations; neither a new experiment nor a model prediction",
    )
    add(
        "bio",
        "candidate",
        "Qualified model selection",
        "greatest training similarity",
        "qualified models only",
        "similarity",
        "per candidate target/endpoint",
        "np.argmax(similarities)",
        "Select applicable assay without selecting a favourable predicted activity",
    )
    add(
        "bio",
        "candidate",
        "Prediction eligibility",
        "qualified model and similarity >=0.5",
        policy["similarity_threshold"],
        "similarity",
        "new computational score",
        "similarity < model",
        "Otherwise abstain with reason",
    )
    add(
        "bio",
        "advisory",
        "PAINS/BRENK alerts",
        ">=1 match",
        1,
        "rule matches",
        "review alert; not a prediction gate",
        "if any(alerts.values())",
        "Alerts do not establish toxicity and do not suppress otherwise available source/assay evidence",
    )
    for rule, comp, value, stage, anchor, rationale in [
        (
            "Endpoint label",
            "in",
            [0, 1],
            "curation",
            "value not in (0, 1)",
            "Missing/invalid values are not negative labels",
        ),
        (
            "Missing tokens",
            "None, empty or nan",
            "missing",
            "curation",
            "raw in (None",
            "Do not impute absent assay labels",
        ),
        (
            "Conflicting duplicate labels",
            "exactly one value per identity/endpoint",
            "else None",
            "curation",
            "len(labels) > 1",
            "Merge standardized identity; remove only the conflicting endpoint",
        ),
        (
            "Minimum scaffold groups",
            ">=",
            5,
            "partition",
            "len(set(groups)) < 5",
            "Permit scaffold partition",
        ),
        (
            "Shared GroupShuffleSplit",
            "20% of scaffold groups test",
            0.2,
            "partition",
            "test_size=0.2",
            "Same structure/scaffold partition for all endpoints before missing-label removal",
        ),
        (
            "Model/partition/bootstrap seed",
            "fixed",
            20260908,
            "configuration",
            "SEED =",
            "Deterministic configuration, not independently timestamped preregistration",
        ),
        (
            "Class support before fitting",
            "two classes in train and test",
            2,
            "fit support",
            "len(np.unique(y_train))",
            "Both ROC and binary prediction require class support",
        ),
        (
            "RF configuration",
            "fixed; no hyperparameter search",
            "128 trees, leaf3, sqrt features, n_jobs2",
            "fit",
            "model = RandomForestClassifier",
            "Declared configuration; no calibration of assay probabilities",
        ),
        (
            "ROC bootstrap groups",
            ">=",
            5,
            "uncertainty",
            "len(group_ids) < 5",
            "With fewer groups the interval is withheld",
        ),
        (
            "Full/in-domain ROC bootstrap draws",
            "200/100",
            [200, 100],
            "uncertainty",
            "bootstrap: int = 200",
            "Resample scaffold groups with replacement; keep all molecules in each drawn group",
        ),
        (
            "Valid ROC bootstrap replicates",
            ">=max(20,B//2)",
            [100, 50],
            "uncertainty",
            "max(20, bootstrap // 2)",
            "Skip single-class draws; insufficient valid draws give null interval",
        ),
        (
            "ROC bootstrap interval",
            "quantile linear",
            [0.025, 0.975],
            "uncertainty",
            "np.quantile(aucs",
            "Percentiles over nondegenerate group-bootstrap replicates; seed resets for each call",
        ),
        (
            "Held-out positive/negative support",
            ">= each",
            20,
            "quality",
            "positive >= 20",
            "Minimum class support for endpoint reporting",
        ),
        (
            "Training positive support",
            ">=",
            30,
            "quality",
            "train_positive >= 30",
            "Minimum positive training support",
        ),
        (
            "Held-out ROC lower95",
            "> strict",
            0.5,
            "quality",
            "interval[0] > 0.5",
            "Discrimination exceeds chance according to this retrospective interval",
        ),
        (
            "Held-out average precision",
            "> strict",
            "held-out prevalence",
            "quality",
            'metrics["average_precision"] >',
            "Compare with no-skill ranking reference",
        ),
        (
            "Held-out Brier loss",
            "< strict",
            "training-prevalence constant baseline",
            "quality",
            'metrics["brier"] <',
            "Compare squared-error prediction with training-only constant; not calibration certification",
        ),
        (
            "Nearest labeled training Tanimoto",
            ">=",
            0.4,
            "applicability",
            "domain = similarities >=",
            "Endpoint-specific training reference; biological validity of cutoff not established",
        ),
        (
            "Candidate evidence precedence",
            "observed > failed quality > outside domain > predicted",
            "ordered reasons",
            "candidate",
            'reason = "observed_assay_available"',
            "Observations suppress inferred scores for the same endpoint even if model/domain checks would fail",
        ),
    ]:
        add(
            "tox21",
            stage,
            rule,
            comp,
            value,
            "as specified",
            "endpoint-specific unless shared split/configuration",
            anchor,
            rationale,
        )
    return rows


def regression_metrics(y, pred, baseline, width):
    error = pred - y
    total = np.sum((y - y.mean()) ** 2)
    rho = spearmanr(y, pred).statistic if np.std(y) > 1e-12 and np.std(pred) > 1e-12 else None
    return {
        "mae": float(np.mean(abs(error))),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "r2": float(1 - np.sum(error**2) / total) if total > 1e-12 else None,
        "spearman": float(rho) if rho is not None and math.isfinite(rho) else None,
        "interval_coverage_fixed_archived_half_width": float(np.mean(abs(error) <= width)),
        "median_baseline_mae": float(np.mean(abs(baseline - y))),
        "paired_mae_improvement_over_median": float(np.mean(abs(baseline - y) - abs(error))),
    }


def regression_bootstrap(rows, seed=BOOTSTRAP_SEED, repetitions=BOOTSTRAP_REPLICATES):
    y = np.array([r["actual_pactivity"] for r in rows])
    pred = np.array([r["predicted_pactivity"] for r in rows])
    baseline = np.array([r["training_median_baseline"] for r in rows])
    groups = np.array([r["scaffold"] for r in rows])
    unique = np.unique(groups)
    blocks = [np.flatnonzero(groups == group) for group in unique]
    width = rows[0]["archived_interval_half_width"]
    point = regression_metrics(y, pred, baseline, width)
    draws = defaultdict(list)
    rng = np.random.default_rng(seed)
    sample_sizes = []
    for _ in range(repetitions):
        ix = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        sample_sizes.append(len(ix))
        for key, value in regression_metrics(y[ix], pred[ix], baseline[ix], width).items():
            if value is not None and math.isfinite(value):
                draws[key].append(value)
    return [
        {
            "metric": key,
            "point": value,
            "lower95": float(np.quantile(draws[key], 0.025)),
            "upper95": float(np.quantile(draws[key], 0.975)),
            "valid_replicates": len(draws[key]),
            "requested_replicates": repetitions,
            "seed": seed,
            "structures": len(rows),
            "scaffolds": len(unique),
            "resampled_structure_count_min": min(sample_sizes),
            "resampled_structure_count_max": max(sample_sizes),
            "conditioning": "fixed selected RF, train-median baseline, domain subset and archived calibration half-width; no refitting/reselection/recalibration",
        }
        for key, value in point.items()
    ]


def roc_bootstrap(rows, repetitions, seed):
    labels = np.array([r["label"] for r in rows], dtype=int)
    scores = np.array([r["score"] for r in rows])
    groups = np.array([r["scaffold"] for r in rows])
    unique = np.unique(groups)
    out = {
        "repetitions": repetitions,
        "seed": seed,
        "scaffolds": len(unique),
        "structures": len(rows),
        "valid_replicates": 0,
        "single_class_replicates_skipped": 0,
        "lower95": None,
        "upper95": None,
        "quantile_method": "numpy.quantile method=linear (default)",
        "minimum_valid_replicates": max(20, repetitions // 2),
    }
    if len(np.unique(labels)) < 2 or len(unique) < 5:
        out["status"] = "unavailable_single_class_or_fewer_than_five_groups"
        return out
    rng = np.random.default_rng(seed)
    blocks = [np.flatnonzero(groups == group) for group in unique]
    values = []
    for _ in range(repetitions):
        ix = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        if len(np.unique(labels[ix])) == 2:
            values.append(roc_auc_score(labels[ix], scores[ix]))
    out.update(valid_replicates=len(values), single_class_replicates_skipped=repetitions - len(values))
    if len(values) >= out["minimum_valid_replicates"]:
        out.update(
            status="available",
            lower95=float(np.quantile(values, 0.025)),
            upper95=float(np.quantile(values, 0.975)),
        )
    else:
        out["status"] = "unavailable_insufficient_valid_replicates"
    return out


def analyze_herg(audit, models, curated, output):
    selected = [m for m in models if m["quality_status"] == "qualified"]
    audit.check("one_qualified_assay", len(selected) == 1)
    model = selected[0]
    audit.check(
        "qualified_assay_identity_and_model",
        model["assay_id"] == "CHEMBL1827362" and model["selected_model"] == "random_forest",
    )
    record_map = {
        r["smiles"]: r
        for r in curated
        if r["assay_id"] == model["assay_id"] and r["endpoint"] == model["endpoint"]
    }
    parts = model["splits"]
    for left in parts:
        for right in parts:
            if left < right:
                audit.check(
                    f"herg_split_disjoint:{left}:{right}",
                    not ({r["smiles"] for r in parts[left]} & {r["smiles"] for r in parts[right]})
                    and not ({r["scaffold"] for r in parts[left]} & {r["scaffold"] for r in parts[right]}),
                )
    median = float(np.median([record_map[r["smiles"]]["pactivity"] for r in parts["train"]]))
    fpgen = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048, includeChirality=True)
    fps = {s: fpgen.GetFingerprint(Chem.MolFromSmiles(s)) for s in record_map}
    references = [fps[r["smiles"]] for r in parts["train"]]
    predictions = {r["smiles"]: r for r in model["test_predictions"]}
    width = model["uncertainty"]["interval_half_width"]
    structures = []
    for part, rows in parts.items():
        for row in rows:
            record = record_map[row["smiles"]]
            similarity = max(DataStructs.BulkTanimotoSimilarity(fps[row["smiles"]], references))
            pred = predictions.get(row["smiles"])
            result = {
                "assay_id": model["assay_id"],
                "split": part,
                "smiles": row["smiles"],
                "scaffold": row["scaffold"],
                "activity_ids": record["activity_ids"],
                "actual_pactivity": record["pactivity"],
                "nearest_training_similarity": similarity,
                "in_domain": similarity >= 0.5,
                "predicted_pactivity": pred["predicted_pactivity"] if pred else None,
                "residual_predicted_minus_actual": pred["predicted_pactivity"] - record["pactivity"]
                if pred
                else None,
                "absolute_residual": abs(pred["predicted_pactivity"] - record["pactivity"]) if pred else None,
                "training_median_baseline": median,
                "archived_interval_half_width": width,
                "prediction_availability": "archived selected-model test prediction"
                if pred
                else "not archived; no refit performed",
            }
            if pred:
                audit.close(f"herg_actual:{row['smiles']}", record["pactivity"], pred["actual_pactivity"])
                audit.close(f"herg_similarity:{row['smiles']}", similarity, pred["nearest_similarity"])
            structures.append(result)
    write_csv(output / "herg_structure_predictions.csv", structures)
    calibration = [r for r in structures if r["split"] == "calibration"]
    cal_inside = sum(r["in_domain"] for r in calibration)
    audit.check(
        "herg_calibration_recomputed_domain", cal_inside == model["applicability"]["calibration_in_domain"]
    )
    intervals = []
    for subset in ("test", "test_in_domain"):
        rows = [r for r in structures if r["split"] == "test" and (subset == "test" or r["in_domain"])]
        points = regression_metrics(
            np.array([r["actual_pactivity"] for r in rows]),
            np.array([r["predicted_pactivity"] for r in rows]),
            np.full(len(rows), median),
            width,
        )
        for metric in ("mae", "rmse", "r2", "spearman"):
            audit.close(
                f"herg_{subset}_{metric}", points[metric], model["metrics"]["random_forest"][subset][metric]
            )
        audit.close(
            f"herg_{subset}_baseline",
            points["median_baseline_mae"],
            model["metrics"]["median_baseline"][subset]["mae"],
        )
        if subset == "test_in_domain":
            audit.close(
                "herg_interval_coverage",
                points["interval_coverage_fixed_archived_half_width"],
                model["uncertainty"]["empirical_test_coverage"],
            )
        intervals += [{"subset": subset, **row} for row in regression_bootstrap(rows)]
    write_csv(output / "herg_bootstrap.csv", intervals)
    return {
        "assay_id": model["assay_id"],
        "scope": model["assay_description"],
        "selected_model": model["selected_model"],
        "splits": {
            p: {"structures": len(rows), "scaffolds": len({r["scaffold"] for r in rows})}
            for p, rows in parts.items()
        },
        "training_median_baseline": median,
        "calibration_total": len(calibration),
        "calibration_in_domain": cal_inside,
        "calibration_residuals_available": False,
        "calibration_bootstrap": None,
        "calibration_limitation": "Only split identities/labels and aggregate half-width survive. Individual calibration RF predictions/residuals and fitted estimator are not archived. No refit or fabricated residuals were used.",
        "interval_rank_in_domain": min(
            cal_inside, math.ceil((cal_inside + 1) * model["policy"]["nominal_coverage"])
        ),
        "uncertainty_archived": model["uncertainty"],
        "bootstrap": intervals,
        "ridge_comparison": {
            "tune_mae_rf": model["metrics"]["random_forest"]["tune"]["mae"],
            "tune_mae_ridge": model["metrics"]["ridge"]["tune"]["mae"],
            "test_mae_rf": model["metrics"]["random_forest"]["test"]["mae"],
            "test_mae_ridge": model["metrics"]["ridge"]["test"]["mae"],
            "paired_rf_ridge_uncertainty": None,
            "limitation": "RF selected on tuning despite slightly worse observed test MAE. Per-structure Ridge predictions are not archived, so no paired RF-versus-Ridge interval is reconstructed.",
        },
    }


def analyze_tox(audit, report, output):
    split = audit.jsonl(TOX / "split-membership.jsonl")
    labels = audit.jsonl(TOX / "curated-labels.jsonl")
    membership = {r["smiles"]: r for r in split}
    label_map = {r["smiles"]: r for r in labels}
    audit.check(
        "tox_curated_unique",
        len(label_map) == len(labels) == len(membership) == report["dataset"]["curated_structures"],
    )
    train_scaffolds = {r["scaffold"] for r in split if r["split"] == "train"}
    test_scaffolds = {r["scaffold"] for r in split if r["split"] == "test"}
    audit.check("tox_scaffold_no_leakage", not train_scaffolds & test_scaffolds)
    audit.check(
        "tox_split_hash_matches",
        digest(ROOT / TOX / "split-membership.jsonl") == report["protocol"]["split_sha256"],
    )
    endpoints, intervals, holdout_export = [], [], []
    for endpoint in report["endpoints"]:
        name = endpoint["endpoint"]
        rows = audit.jsonl(TOX / f"{name}-holdout.jsonl")
        training = [
            r for r in labels if membership[r["smiles"]]["split"] == "train" and r["labels"][name] is not None
        ]
        y = np.array([r["label"] for r in rows])
        scores = np.array([r["score"] for r in rows])
        train_pos = sum(r["labels"][name] for r in training)
        audit.check(
            f"tox_holdout_membership:{name}",
            all(
                membership[r["smiles"]]["split"] == "test"
                and label_map[r["smiles"]]["labels"][name] == r["label"]
                for r in rows
            ),
        )
        audit.check(
            f"tox_endpoint_support:{name}",
            len(rows) == endpoint["test_count"]
            and len(training) == endpoint["train_count"]
            and int(y.sum()) == endpoint["test_positive"]
            and train_pos == endpoint["train_positive"],
        )
        metrics = {
            "roc_auc": roc_auc_score(y, scores),
            "average_precision": average_precision_score(y, scores),
            "brier": brier_score_loss(y, scores),
            "prevalence": y.mean(),
            "train_prevalence_brier_baseline": brier_score_loss(
                y, np.full(len(y), train_pos / len(training))
            ),
        }
        for key, value in metrics.items():
            audit.close(f"tox_metric:{name}:{key}", value, endpoint["metrics"][key])
        failures = []
        for criterion, passed in {
            "test_positive>=20": int(y.sum()) >= 20,
            "test_negative>=20": int((1 - y).sum()) >= 20,
            "train_positive>=30": train_pos >= 30,
            "ROC_lower95>0.5": endpoint["metrics"]["roc_auc_ci95"][0] > 0.5,
            "AP>test_prevalence": metrics["average_precision"] > metrics["prevalence"],
            "Brier<train_prevalence_baseline": metrics["brier"] < metrics["train_prevalence_brier_baseline"],
        }.items():
            if not passed:
                failures.append(criterion)
        audit.check(f"tox_quality_replay:{name}", (not failures) == endpoint["model_gate_passed"])
        for subset, subset_rows, repetitions, original_metrics in [
            ("all_test", rows, 200, endpoint["metrics"]),
            (
                "in_domain_test",
                [r for r in rows if r["nearest_training_similarity"] >= 0.4],
                100,
                endpoint["applicability"]["metrics_in_domain"],
            ),
        ]:
            bootstrap = roc_bootstrap(subset_rows, repetitions, report["protocol"]["seed"])
            audit.close(
                f"tox_ROC_interval_replay:{name}:{subset}",
                [bootstrap["lower95"], bootstrap["upper95"]],
                original_metrics["roc_auc_ci95"],
            )
            intervals.append({"endpoint": name, "subset": subset, **bootstrap})
        endpoints.append(
            {
                "endpoint": name,
                "curated_total": len(labels),
                "labeled_count": endpoint["labeled_count"],
                "missing_or_conflicting_labels": report["dataset"]["missing_labels"][name],
                "conflicting_identity_labels": report["dataset"]["conflicting_labels_excluded"][name],
                "train_count": len(training),
                "train_positive": train_pos,
                "train_negative": len(training) - train_pos,
                "test_count": len(rows),
                "test_positive": int(y.sum()),
                "test_negative": int((1 - y).sum()),
                "test_scaffolds": len({r["scaffold"] for r in rows}),
                **metrics,
                "roc_lower95": endpoint["metrics"]["roc_auc_ci95"][0],
                "roc_upper95": endpoint["metrics"]["roc_auc_ci95"][1],
                "quality_gate_passed": not failures,
                "failed_criteria": failures,
                "domain_threshold": 0.4,
                "test_in_domain": endpoint["applicability"]["test_in_domain"],
                "test_domain_coverage": endpoint["applicability"]["test_coverage"],
                "candidate_predictions": endpoint["candidate_predictions"],
                "candidate_abstentions": endpoint["candidate_abstentions"],
            }
        )
        holdout_export += [{"endpoint": name, **r} for r in rows]
    write_csv(output / "tox21_endpoints.csv", endpoints)
    write_csv(output / "tox21_bootstrap_replay.csv", intervals)
    write_csv(output / "tox21_holdout_predictions.csv", holdout_export)
    raw = audit.read("runtime/validation/sources/tox21.csv.gz")
    receipt = audit.json("runtime/validation/sources/tox21.csv.gz.receipt.json")
    decompressed = gzip.decompress(raw)
    original_rows = list(csv.DictReader(io.StringIO(decompressed.decode())))
    audit.check(
        "tox_download_snapshot_hash",
        hashlib.sha256(raw).hexdigest() == report["dataset"]["sha256"] == receipt["sha256"],
    )
    audit.check("tox_download_snapshot_rows", len(original_rows) == report["dataset"]["raw_rows"])
    c = report["dataset"]
    audit.check(
        "tox_curation_denominator",
        c["raw_rows"] == c["invalid_structures"] + c["collapsed_duplicate_rows"] + c["curated_structures"],
    )
    snapshot = {
        "source_url": receipt["url"],
        "compressed_sha256": hashlib.sha256(raw).hexdigest(),
        "compressed_bytes": len(raw),
        "decompressed_sha256": hashlib.sha256(decompressed).hexdigest(),
        "decompressed_bytes": len(decompressed),
        "download_timestamp": receipt.get("fetched_at"),
        "timestamp_limitation": "Receipt has URL/hash/bytes only; no recorded retrieval timestamp or immutable upstream release ID.",
        "raw_csv_columns": list(original_rows[0]),
        "raw_rows": len(original_rows),
        "original_raw_id_count": len({r["mol_id"] for r in original_rows}),
        "upstream_version": None,
        "version_scope": "Exact downloaded DeepChem S3 gzip snapshot, not an independently authenticated Tox21 challenge release.",
        "curation": report["dataset"],
        "source_gzip_path": "runtime/validation/sources/tox21.csv.gz",
        "split": {
            "train_structures": sum(r["split"] == "train" for r in split),
            "test_structures": sum(r["split"] == "test" for r in split),
            "train_scaffolds": len(train_scaffolds),
            "test_scaffolds": len(test_scaffolds),
            "overlap": 0,
        },
    }
    write_json(output / "tox21_download_snapshot.json", snapshot)
    return {
        "endpoints": endpoints,
        "bootstrap_replay": intervals,
        "snapshot": snapshot,
        "bootstrap_definition": "For each call, reset default_rng(20260908); sort unique scaffold strings; sample G group indices with replacement, concatenate complete groups, skip single-class draws; 200 draws full test and 100 in-domain; require max(20,B//2) valid draws; NumPy linear 2.5/97.5 percentiles. No label imputation, model refitting or calibration.",
    }


def candidate_analysis(audit, bio_report, tox_report, output):
    bio = audit.jsonl(BIO / "candidates.jsonl")
    tox = audit.jsonl(TOX / "candidates.jsonl")
    audit.check(
        "bio_candidate_hash", digest(ROOT / BIO / "candidates.jsonl") == bio_report["candidate_sha256"]
    )
    audit.check(
        "tox_candidate_hash", digest(ROOT / TOX / "candidates.jsonl") == tox_report["candidates"]["sha256"]
    )
    audit.check(
        "candidate_same_identity_set",
        {r["smiles"] for r in bio} == {r["smiles"] for r in tox} and len(bio) == len(tox) == 15740,
    )
    tox_map = {r["smiles"]: r for r in tox}
    quality = {e["endpoint"]: e["model_gate_passed"] for e in tox_report["endpoints"]}
    profiles, coverage = [], Counter()
    stage = Counter()
    sensitivity = []
    observations_only = []
    for row in bio:
        t = tox_map[row["smiles"]]
        evidence_by = defaultdict(list)
        for ev in row["evidence"]:
            evidence_by[f"{ev['target']}:{ev['endpoint']}"].append(ev)
        herg = evidence_by["KCNH2:IC50"][0]
        profile = {
            "candidate_id": row["id"],
            "smiles": row["smiles"],
            "cohorts": t["cohorts"],
            "herg_nearest_training_similarity": herg.get("nearest_similarity"),
            "herg_status": herg["status"],
            "herg_reason": herg.get("reason"),
            "tox21_max_nearest_similarity": t["nearest_training_similarity"],
            "tox21_any_domain": t["in_domain"],
            "tox21_observed_endpoints": [r["endpoint"] for r in t["observed_assays"]],
        }
        for ep, entries in evidence_by.items():
            statuses = {e["status"] for e in entries}
            status = "exact_measured" if "exact_measured" in statuses else entries[0]["status"]
            reason = "observed_source_measurement" if status == "exact_measured" else entries[0].get("reason")
            for cohort in ["all", *t["cohorts"]]:
                coverage[("biochemical", ep, cohort, status, reason)] += 1
        any_pred = any(p["status"] == "predicted" for p in t["predictions"])
        stage["total"] += 1
        stage["tox_any_domain"] += t["in_domain"]
        stage["tox_any_prediction"] += any_pred
        stage["tox_outside_every_domain"] += not t["in_domain"]
        stage["tox_observed_no_prediction_in_domain"] += t["in_domain"] and not any_pred
        stage["tox_any_observed"] += bool(t["observed_assays"])
        if t["in_domain"] and not any_pred:
            audit.check(
                f"observed_precedence_partition:{row['id']}",
                all(p["reason"] == "observed_assay_available" for p in t["predictions"] if p["in_domain"]),
            )
            observations_only.append(
                {
                    "candidate_id": row["id"],
                    "smiles": row["smiles"],
                    "cohorts": t["cohorts"],
                    "observed_endpoints": [r["endpoint"] for r in t["observed_assays"]],
                    "in_domain_endpoints": [p["endpoint"] for p in t["predictions"] if p["in_domain"]],
                }
            )
        for pred in t["predictions"]:
            ep = pred["endpoint"]
            profile[f"{ep}_similarity"] = pred.get("nearest_training_similarity")
            profile[f"{ep}_status"] = pred["status"]
            for cohort in ["all", *t["cohorts"]]:
                coverage[("tox21", ep, cohort, pred["status"], pred.get("reason") or "eligible_score")] += 1
        profiles.append(profile)
    write_csv(output / "candidate_similarity_profiles.csv", profiles)
    coverage_rows = [
        {
            "system": system,
            "endpoint": ep,
            "cohort": cohort,
            "status": status,
            "reason": reason,
            "candidates": n,
            "denominator": len(bio) if cohort == "all" else sum(cohort in r["cohorts"] for r in tox),
        }
        for (system, ep, cohort, status, reason), n in sorted(coverage.items())
    ]
    write_csv(output / "candidate_coverage.csv", coverage_rows)
    coverage_totals = Counter()
    for row in coverage_rows:
        coverage_totals[(row["system"], row["endpoint"], row["cohort"])] += row["candidates"]
    for (system, endpoint, cohort), total in coverage_totals.items():
        expected = len(bio) if cohort == "all" else sum(cohort in r["cohorts"] for r in tox)
        audit.check(f"candidate_exhaustive_endpoint_coverage:{system}:{endpoint}:{cohort}", total == expected)
    write_csv(output / "observed_only_in_domain.csv", observations_only)
    audit.check(
        "tox_exhaustive_candidate_partition",
        stage["tox_outside_every_domain"]
        + stage["tox_any_prediction"]
        + stage["tox_observed_no_prediction_in_domain"]
        == len(bio),
    )
    audit.check(
        "tox_partition_matches_archive",
        stage["tox_outside_every_domain"] == tox_report["candidates"]["out_of_domain"]
        and stage["tox_any_prediction"] == tox_report["candidates"]["with_any_prediction"],
    )
    hsim = np.array([r["herg_nearest_training_similarity"] for r in profiles])
    proximity = []
    for endpoint, values, cutoff in [
        ("KCNH2:IC50", hsim, 0.5),
        *[(ep, np.array([r[f"{ep}_similarity"] for r in profiles]), 0.4) for ep in quality],
        ("any_endpoint_max_similarity", np.array([r["tox21_max_nearest_similarity"] for r in profiles]), 0.4),
    ]:
        inside = values >= cutoff
        proximity.append(
            {
                "endpoint": endpoint,
                "original_cutoff": cutoff,
                "denominator": len(values),
                "minimum_similarity": float(values.min()),
                "maximum_similarity": float(values.max()),
                "below_cutoff": int(np.sum(~inside)),
                "at_or_above_cutoff": int(inside.sum()),
                "within_0_01_below_exclusive": int(np.sum((values >= cutoff - 0.01) & ~inside)),
                "exactly_at_cutoff": int(np.sum(values == cutoff)),
                "within_0_01_above_inclusive": int(np.sum(inside & (values <= cutoff + 0.01))),
                "largest_similarity_below_cutoff": float(values[~inside].max()) if np.any(~inside) else None,
                "smallest_similarity_at_or_above_cutoff": float(values[inside].min())
                if inside.any()
                else None,
                "scope": "Cached similarities only; diagnostic bins were added post-review, without changing gates or scores",
            }
        )
    write_csv(output / "similarity_proximity.csv", proximity)
    for cutoff in SENSITIVITY_CUTOFFS:
        sensitivity.append(
            {
                "system": "biochemical",
                "endpoint": "KCNH2:IC50",
                "threshold": cutoff,
                "original_threshold": 0.5,
                "candidates": len(profiles),
                "inside_domain_if_threshold_changed": int(np.sum(hsim >= cutoff)),
                "eligible_after_fixed_quality_and_observed_precedence": int(np.sum(hsim >= cutoff)),
                "newly_eligible_vs_recorded_threshold": int(np.sum((hsim >= cutoff) & (hsim < 0.5))),
                "interpretation": "Post hoc coverage only; no newly inferred scores, no refit, no calibration/gate revalidation",
            }
        )
        any_eligible = np.zeros(len(profiles), bool)
        any_inside = np.zeros(len(profiles), bool)
        for ep, passed in quality.items():
            sims = np.array([r[f"{ep}_similarity"] for r in profiles])
            observed = np.array([ep in r["tox21_observed_endpoints"] for r in profiles])
            inside = sims >= cutoff
            eligible = inside & ~observed & passed
            any_inside |= inside
            any_eligible |= eligible
            sensitivity.append(
                {
                    "system": "tox21",
                    "endpoint": ep,
                    "threshold": cutoff,
                    "original_threshold": 0.4,
                    "candidates": len(profiles),
                    "inside_domain_if_threshold_changed": int(inside.sum()),
                    "eligible_after_fixed_quality_and_observed_precedence": int(eligible.sum()),
                    "newly_eligible_vs_recorded_threshold": int(np.sum(eligible & (sims < 0.4))),
                    "interpretation": "Post hoc eligibility counts using cached similarity; abstained model probabilities were not archived and are not reconstructed",
                }
            )
        sensitivity.append(
            {
                "system": "tox21",
                "endpoint": "any_endpoint_union",
                "threshold": cutoff,
                "original_threshold": 0.4,
                "candidates": len(profiles),
                "inside_domain_if_threshold_changed": int(any_inside.sum()),
                "eligible_after_fixed_quality_and_observed_precedence": int(any_eligible.sum()),
                "interpretation": "Union of endpoint eligibility; overlapping endpoint counts must not be added",
            }
        )
    for row in sensitivity:
        if row["system"] == "tox21" and row["threshold"] == 0.4:
            expected = (
                tox_report["candidates"]["with_any_prediction"]
                if row["endpoint"] == "any_endpoint_union"
                else next(
                    e["candidate_predictions"]
                    for e in tox_report["endpoints"]
                    if e["endpoint"] == row["endpoint"]
                )
            )
            audit.check(
                f"candidate_threshold_replay:{row['endpoint']}",
                row["eligible_after_fixed_quality_and_observed_precedence"] == expected,
            )
    write_csv(output / "similarity_sensitivity.csv", sensitivity)
    return {
        "stages": dict(stage),
        "herg_similarity": {
            "minimum": float(hsim.min()),
            "maximum": float(hsim.max()),
            "quantiles": dict(
                zip(
                    ["q0", "q25", "q50", "q75", "q100"],
                    np.quantile(hsim, [0, 0.25, 0.5, 0.75, 1]).tolist(),
                    strict=True,
                )
            ),
        },
        "sensitivity": sensitivity,
        "proximity": proximity,
        "coverage_rows": len(coverage_rows),
        "scope": "15,740 unique standardized candidates; cohort memberships overlap once. Flags/eligibility are endpoint-specific, not efficacy or safety decisions.",
    }


def assay_selection(audit, models, curated, output):
    pool = defaultdict(list)
    for row in curated:
        pool[(row["target"], row["endpoint"], row["assay_type"], row["assay_id"])].append(row)
    strata = defaultdict(list)
    for key, rows in pool.items():
        strata[key[:3]].append((key, rows))
    selected_ids = {m["id"] for m in models}
    pool_rows = []
    for key, rows in pool.items():
        ident = f"{key[0]}:{key[1]}:{key[3]}"
        pool_rows.append(
            {
                "target": key[0],
                "endpoint": key[1],
                "assay_type": key[2],
                "assay_id": key[3],
                "unique_structures": len(rows),
                "scaffolds": len({r["scaffold"] for r in rows}),
                "selected": ident in selected_ids,
                "assay_description": rows[0]["assay_description"],
            }
        )
    for key, rows in strata.items():
        largest = sorted(rows, key=lambda x: (-len(x[1]), x[0][3]))[0][0]
        audit.check(
            f"largest_assay_selection:{key}", f"{largest[0]}:{largest[1]}:{largest[3]}" in selected_ids
        )
    ledger = []
    for model in models:
        policy = model["policy"]
        counts = model["data_counts"]
        gate_values = {
            "structures>=120": counts["unique_structures"] >= policy["min_unique_structures"],
            "scaffolds>=12": counts["scaffolds"] >= policy["min_scaffolds"],
        }
        model_group = pool[(model["target"], model["endpoint"], model["assay_type"], model["assay_id"])]
        ledger.append(
            {
                "target": model["target"],
                "endpoint": model["endpoint"],
                "assay_type": model["assay_type"],
                "assay_id": model["assay_id"],
                "assay_description": model["assay_description"],
                "source_url": model["assay_source_url"],
                "unique_structures": counts["unique_structures"],
                "scaffolds": counts["scaffolds"],
                "available_assays_in_stratum": len(
                    strata[(model["target"], model["endpoint"], model["assay_type"])]
                ),
                "raw_activity_identifiers": sorted({v for r in model_group for v in r["activity_ids"]}),
                "passes_structure_gate": gate_values["structures>=120"],
                "passes_scaffold_gate": gate_values["scaffolds>=12"],
                "split_counts": model["split_counts"],
                "selected_model": model["selected_model"],
                "quality_status": model["quality_status"],
                "reasons": model["reason"],
                "semantic_caution": "Source target ID is PTGS2 but description names PTGES; already fails scaffold gate, not accepted as validated COX-2 training evidence"
                if model["assay_id"] == "CHEMBL5732036"
                else None,
            }
        )
    write_csv(output / "assay_groups.csv", ledger)
    write_csv(output / "assay_selection_pool.csv", pool_rows)
    return {
        "selected_assays": len(ledger),
        "all_curated_assay_groups": len(pool_rows),
        "strata": len(strata),
        "ledger": ledger,
    }


def self_checks(audit):
    y = np.array([1.0, 2.0, 3.0])
    pred = np.array([1.0, 2.0, 3.0])
    baseline = np.array([2.0, 2.0, 2.0])
    m = regression_metrics(y, pred, baseline, 0.1)
    audit.check(
        "perfect_prediction_metrics",
        m["mae"] == 0
        and m["rmse"] == 0
        and m["r2"] == 1
        and m["interval_coverage_fixed_archived_half_width"] == 1,
    )
    audit.check("paired_improvement_sign", m["paired_mae_improvement_over_median"] > 0)
    degenerate = [{"label": 1, "score": 0.9, "scaffold": str(i)} for i in range(6)]
    audit.check("single_class_roc_interval_withheld", roc_bootstrap(degenerate, 200, 1)["lower95"] is None)
    few_groups = [{"label": i % 2, "score": 0.2 + 0.5 * (i % 2), "scaffold": str(i % 4)} for i in range(20)]
    audit.check("few_scaffolds_roc_interval_withheld", roc_bootstrap(few_groups, 200, 1)["lower95"] is None)
    blocks = [np.array([0, 1, 2]), np.array([3])]
    drawn = np.concatenate([blocks[i] for i in [0, 0]])
    audit.check(
        "cluster_resampling_retains_whole_groups_and_multiplicity", drawn.tolist() == [0, 1, 2, 0, 1, 2]
    )


def reconstruct_rf(output):
    """New fixed-model reconstruction, never a replacement of archived evidence."""
    from sklearn.ensemble import RandomForestRegressor
    from threadpoolctl import threadpool_limits

    sys.path.insert(0, str(ROOT / "src"))
    from herbfold.bio_validation import _fingerprints, scaffold_partitions

    destination = output / "postreview_reconstruction"
    destination.mkdir(parents=True, exist_ok=True)
    audit = Audit()
    audit.read(Path(__file__).relative_to(ROOT))
    for filename in ("bio_validation.py", "chemistry.py"):
        audit.read(Path("src/herbfold") / filename)
    models = audit.json(BIO / "model-metrics.json")
    curated = audit.json(BIO / "curated-records.json")
    model = next(r for r in models if r["quality_status"] == "qualified")
    audit.check("fixed_selected_rf", model["selected_model"] == "random_forest")
    rows = [r for r in curated if r["assay_id"] == model["assay_id"] and r["endpoint"] == model["endpoint"]]
    indices = scaffold_partitions(rows, model["policy"]["random_state"])
    for split, ix in indices.items():
        audit.check(
            f"identical_ordered_split:{split}",
            [rows[i]["smiles"] for i in ix] == [r["smiles"] for r in model["splits"][split]],
        )
    fps, matrix = _fingerprints([r["smiles"] for r in rows])
    y = np.array([r["pactivity"] for r in rows])
    parameters = dict(
        n_estimators=128,
        min_samples_leaf=3,
        max_features=0.33,
        random_state=model["policy"]["random_state"],
        n_jobs=2,
    )
    estimator = RandomForestRegressor(**parameters)
    with threadpool_limits(limits=2):
        estimator.fit(matrix[indices["train"]], y[indices["train"]])
        predicted = estimator.predict(matrix)
    tolerance = {"absolute": 1e-12, "relative": 1e-11}
    reference = [fps[i] for i in indices["train"]]
    similarities = np.array([max(DataStructs.BulkTanimotoSimilarity(fp, reference)) for fp in fps])
    historical = {r["smiles"]: r for r in model["test_predictions"]}
    test_comparison, calibration, all_predictions = [], [], []
    split_by_index = {i: name for name, ix in indices.items() for i in ix}
    for i, row in enumerate(rows):
        record = {
            "row_index_in_original_curated_assay": i,
            "smiles": row["smiles"],
            "scaffold": row["scaffold"],
            "split": split_by_index[i],
            "activity_ids": row["activity_ids"],
            "actual_pactivity": float(y[i]),
            "new_predicted_pactivity": float(predicted[i]),
            "new_residual_predicted_minus_actual": float(predicted[i] - y[i]),
            "new_absolute_residual": float(abs(predicted[i] - y[i])),
            "nearest_training_similarity": float(similarities[i]),
            "in_domain": bool(similarities[i] >= model["policy"]["similarity_threshold"]),
            "origin": "NEW post-review fixed-RF reconstruction; not a recovered historical prediction",
        }
        all_predictions.append(record)
        if split_by_index[i] == "calibration":
            calibration.append(record)
        if split_by_index[i] == "test":
            old = historical[row["smiles"]]["predicted_pactivity"]
            test_comparison.append(
                {
                    **record,
                    "archived_predicted_pactivity": old,
                    "absolute_difference": float(abs(predicted[i] - old)),
                    "matches_tolerance": bool(
                        np.isclose(predicted[i], old, atol=tolerance["absolute"], rtol=tolerance["relative"])
                    ),
                }
            )
    in_domain = [r for r in calibration if r["in_domain"]]
    errors = np.array([r["new_absolute_residual"] for r in in_domain])
    rank = min(len(errors), math.ceil((len(errors) + 1) * model["policy"]["nominal_coverage"]))
    half_width = float(np.sort(errors)[rank - 1])
    widths_match = bool(
        np.isclose(
            half_width,
            model["uncertainty"]["interval_half_width"],
            atol=tolerance["absolute"],
            rtol=tolerance["relative"],
        )
    )
    metrics = []
    for name in ("tune", "test", "test_in_domain"):
        ix = np.array(indices["test" if name == "test_in_domain" else name])
        if name == "test_in_domain":
            ix = ix[similarities[ix] >= model["policy"]["similarity_threshold"]]
        values = regression_metrics(
            y[ix], predicted[ix], np.full(len(ix), np.median(y[indices["train"]])), half_width
        )
        for key in ("mae", "rmse", "r2", "spearman"):
            old = model["metrics"]["random_forest"][name][key]
            metrics.append(
                {
                    "subset": name,
                    "metric": key,
                    "new_value": values[key],
                    "archived_value": old,
                    "absolute_difference": abs(values[key] - old),
                    "matches_tolerance": bool(
                        np.isclose(values[key], old, atol=tolerance["absolute"], rtol=tolerance["relative"])
                    ),
                }
            )
    matches = all(r["matches_tolerance"] for r in test_comparison + metrics) and widths_match
    groups = np.array([r["scaffold"] for r in in_domain])
    blocks = [np.flatnonzero(groups == group) for group in np.unique(groups)]
    rng = np.random.default_rng(BOOTSTRAP_SEED + 1)
    width_draws, sample_sizes = [], []
    for _ in range(BOOTSTRAP_REPLICATES):
        ix = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        draw_rank = min(len(ix), math.ceil((len(ix) + 1) * model["policy"]["nominal_coverage"]))
        width_draws.append(float(np.sort(errors[ix])[draw_rank - 1]))
        sample_sizes.append(len(ix))
    bootstrap = {
        "statistic": "Finite-sample rank absolute-residual half-width in the NEW reconstruction",
        "point": half_width,
        "lower95": float(np.quantile(width_draws, 0.025)),
        "upper95": float(np.quantile(width_draws, 0.975)),
        "replicates": BOOTSTRAP_REPLICATES,
        "seed": BOOTSTRAP_SEED + 1,
        "calibration_structures": len(in_domain),
        "scaffolds": len(blocks),
        "minimum_resampled_structures": min(sample_sizes),
        "maximum_resampled_structures": max(sample_sizes),
        "quantile_method": "numpy.quantile linear",
        "rank_recomputed_for_each_draw_size": True,
        "limitation": "Post hoc group-bootstrap sensitivity conditional on the reconstructed fixed estimator and observed calibration groups; not a guaranteed confidence interval or prospective coverage statement.",
    }
    write_csv(destination / "all_structure_predictions.csv", all_predictions)
    write_csv(destination / "calibration_predictions.csv", calibration)
    write_csv(destination / "archived_test_comparison.csv", test_comparison)
    write_csv(destination / "archived_metric_comparison.csv", metrics)
    write_csv(
        destination / "calibration_width_bootstrap_draws.csv",
        [
            {"draw": i, "structures": n, "half_width": v}
            for i, (n, v) in enumerate(zip(sample_sizes, width_draws))
        ],
    )
    write_json(destination / "calibration_width_bootstrap.json", bootstrap)
    audit.unchanged()
    report = {
        "schema_version": 1,
        "status": "matched_within_tolerance" if matches else "mismatch_disclosed",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "analysis_label": "NEW 2026-09-09 post-review CPU RF reconstruction, not recovered historical residuals",
        "model_fits": 1,
        "candidate_predictions": 0,
        "reselection": False,
        "gates_changed": False,
        "historical_artifacts_replaced": False,
        "assay_id": model["assay_id"],
        "parameters_explicit": parameters,
        "parameters_resolved_with_library_defaults": estimator.get_params(),
        "training_structures": len(indices["train"]),
        "training_split_order_verified": True,
        "fingerprint": {
            "radius": 2,
            "bits": 2048,
            "chirality": True,
            "dtype": str(matrix.dtype),
            "training_matrix_sha256": hashlib.sha256(matrix[indices["train"]].tobytes()).hexdigest(),
            "ordered_training_labels_sha256": hashlib.sha256(y[indices["train"]].tobytes()).hexdigest(),
        },
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "sklearn": sklearn.__version__,
            "rdkit": rdBase.rdkitVersion,
            "executable": sys.executable,
        },
        "tolerance": tolerance,
        "archived_test_predictions_compared": len(test_comparison),
        "maximum_absolute_test_prediction_difference": max(r["absolute_difference"] for r in test_comparison),
        "all_test_predictions_match": all(r["matches_tolerance"] for r in test_comparison),
        "all_aggregate_metrics_match": all(r["matches_tolerance"] for r in metrics),
        "calibration_total": len(calibration),
        "calibration_in_domain": len(in_domain),
        "calibration_rank_formula": "min(n, ceil((n+1)*0.9))",
        "calibration_rank": rank,
        "new_half_width": half_width,
        "archived_half_width": model["uncertainty"]["interval_half_width"],
        "half_width_matches": widths_match,
        "calibration_width_bootstrap": bootstrap,
        "interpretation": "Agreement supports operational reproducibility of the fixed algorithm and split. It does not authenticate unarchived historical calibration predictions or establish outcome-blind gate chronology.",
        "sources": list(audit.sources.values()),
        "checks": audit.checks,
        "source_bytes_unchanged": True,
    }
    write_json(destination / "summary.json", report)
    (destination / "methods-results-draft.md").write_text(
        f"# New post-review RF reconstruction\n\nOn 2026-09-09 we fitted the already selected RF once, using its fixed settings and the same ordered 84 training structures; no model reselection, gate tuning or new candidate scoring occurred. The 24 archived test predictions agreed within absolute 1e-12/relative 1e-11 tolerance (maximum absolute difference {report['maximum_absolute_test_prediction_difference']:.3g}); all twelve tune/test/in-domain aggregate comparisons and the stored interval half-width also agreed: {matches}.\n\nThe newly computed calibration predictions cover all 27 structures, with 25 meeting the original similarity threshold. Rank min(25,ceil(26×0.9))=24 gives half-width {half_width:.9f} pIC50. A new {BOOTSTRAP_REPLICATES}-replicate scaffold-group bootstrap of those 25 residuals across {len(blocks)} scaffolds, seed {BOOTSTRAP_SEED + 1}, gave half-width percentiles {bootstrap['lower95']:.6f}–{bootstrap['upper95']:.6f}. The rank is recalculated for each resampled number of structures. This is conditional post hoc sensitivity to the observed calibration groups, not guaranteed interval coverage. These residuals are new reconstructed computations and do not replace absent historical calibration artifacts.\n"
    )
    print(
        json.dumps(
            {
                "stage": "postreview_reconstruction",
                "status": report["status"],
                "max_test_difference": report["maximum_absolute_test_prediction_difference"],
                "half_width": half_width,
                "bootstrap": bootstrap,
            }
        ),
        flush=True,
    )


def make_figure(output):
    """Plot only saved numerical outputs; the plotting interpreter performs no fit."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    sources = [
        output / "summary.json",
        output / "herg_structure_predictions.csv",
        output / "similarity_sensitivity.csv",
        output / "postreview_reconstruction/summary.json",
        output / "postreview_reconstruction/calibration_predictions.csv",
    ]
    hashes = [
        {"path": str(p.relative_to(ROOT)), "sha256": digest(p), "bytes": p.stat().st_size} for p in sources
    ]
    summary = json.loads(sources[0].read_text())
    reconstruction = json.loads(sources[3].read_text())
    if reconstruction["status"] != "matched_within_tolerance":
        raise ValueError("Do not visualize reconstruction as matching archived results after a mismatch")

    def read_csv(path):
        with path.open(newline="") as handle:
            return list(csv.DictReader(handle))

    test = [r for r in read_csv(sources[1]) if r["split"] == "test"]
    sensitivity = read_csv(sources[2])
    cal = read_csv(sources[4])
    inside = sorted(
        [r for r in cal if r["in_domain"] == "True"],
        key=lambda r: (float(r["new_absolute_residual"]), r["smiles"]),
    )
    outside = sorted([r for r in cal if r["in_domain"] != "True"], key=lambda r: r["smiles"])
    width = reconstruction["archived_half_width"]
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.titlesize": 9.2,
            "axes.labelsize": 8.2,
            "xtick.labelsize": 7.2,
            "ytick.labelsize": 7.2,
            "legend.fontsize": 7,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.65,
            "savefig.facecolor": "white",
        }
    )
    fig = plt.figure(figsize=(7.3, 6.6))
    grid = fig.add_gridspec(
        2, 2, height_ratios=[1, 0.98], hspace=0.55, wspace=0.31, left=0.09, right=0.98, top=0.91, bottom=0.13
    )
    axa, axb, axc = fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1]), fig.add_subplot(grid[1, :])
    blue, red, gold = "#216b88", "#b84e42", "#ba871e"
    plotted = []
    groups = sorted({r["scaffold"] for r in test})
    axa.axhspan(-width, width, color=blue, alpha=0.09, label="Fixed interval half-width")
    axa.axhline(0, color="#59636c", lw=0.7)
    for i, group in enumerate(groups, 1):
        group_rows = sorted([r for r in test if r["scaffold"] == group], key=lambda r: r["smiles"])
        if i % 2 == 0:
            axa.axvspan(i - 0.5, i + 0.5, color="#59636c", alpha=0.035)
        for offset, row in zip(
            np.linspace(-0.19, 0.19, len(group_rows)) if len(group_rows) > 1 else [0], group_rows
        ):
            in_domain = row["in_domain"] == "True"
            error = float(row["residual_predicted_minus_actual"])
            axa.scatter(
                i + offset,
                error,
                s=23 if in_domain else 35,
                c=blue if in_domain else red,
                marker="o" if in_domain else "^",
                linewidths=0.4,
                edgecolors="white",
                zorder=3,
            )
            plotted.append(
                {
                    "panel": "a",
                    "structure_smiles": row["smiles"],
                    "scaffold": group,
                    "scaffold_display_index": i,
                    "x": i + offset,
                    "y": error,
                    "in_domain": in_domain,
                    "source": str(sources[1].relative_to(ROOT)),
                    "origin": "archived test prediction",
                }
            )
    axa.scatter([], [], c=blue, s=20, label="In domain (23)")
    axa.scatter([], [], c=red, s=28, marker="^", label="Outside domain (1)")
    axa.set(
        xlim=(0.3, 19.7),
        ylim=(-2.12, 1.35),
        xlabel="Held-out scaffold group (19 groups)",
        ylabel="Prediction - measured pIC50",
    )
    axa.set_xticks([1, 4, 7, 10, 13, 16, 19])
    axa.set_title("Archived hERG residuals", loc="left", pad=9)
    axa.legend(loc="upper left", frameon=False, handlelength=1.1, labelspacing=0.35)

    boot = reconstruction["calibration_width_bootstrap"]
    axb.axhspan(boot["lower95"], boot["upper95"], color=gold, alpha=0.13)
    axb.axhline(width, color=gold, lw=1.1, linestyle="--", label=f"Rank 24 width = {width:.3f}")
    for rank, row in enumerate(inside, 1):
        error = float(row["new_absolute_residual"])
        axb.scatter(rank, error, s=19, c=blue, linewidths=0.3, edgecolors="white", zorder=3)
        plotted.append(
            {
                "panel": "b",
                "structure_smiles": row["smiles"],
                "scaffold": row["scaffold"],
                "residual_order": rank,
                "x": rank,
                "y": error,
                "in_domain": True,
                "source": str(sources[4].relative_to(ROOT)),
                "origin": "new post-review reconstruction",
            }
        )
    axb.axvline(26, color="#aab1b5", lw=0.7)
    for x, row in zip([27, 28], outside):
        error = float(row["new_absolute_residual"])
        axb.scatter(x, error, s=26, c=red, marker="x", linewidths=1)
        plotted.append(
            {
                "panel": "b",
                "structure_smiles": row["smiles"],
                "scaffold": row["scaffold"],
                "residual_order": None,
                "x": x,
                "y": error,
                "in_domain": False,
                "source": str(sources[4].relative_to(ROOT)),
                "origin": "new post-review reconstruction",
            }
        )
    axb.set(
        xlim=(0, 29),
        ylim=(0, 1.24),
        xlabel="Ordered in-domain residual (25 structures)",
        ylabel="Absolute calibration residual (pIC50)",
    )
    axb.set_xticks([1, 5, 10, 15, 20, 25])
    axb.set_title("New calibration reconstruction", loc="left", pad=9)
    axb.legend(loc="upper left", frameon=False, handlelength=1.8)
    axb.text(
        0.03,
        0.83,
        f"Width bootstrap: {boot['lower95']:.3f}-{boot['upper95']:.3f}\n25 structures / 13 scaffolds",
        transform=axb.transAxes,
        fontsize=6.9,
    )
    axb.annotate(
        "2 excluded",
        xy=(27.4, 0.21),
        xytext=(20.8, 0.07),
        fontsize=6.5,
        arrowprops={"arrowstyle": "-", "color": red, "lw": 0.6},
        color=red,
    )

    tx = [r for r in sensitivity if r["endpoint"] == "any_endpoint_union"]
    thresholds = np.array([float(r["threshold"]) for r in tx])
    counts = np.array([int(r["eligible_after_fixed_quality_and_observed_precedence"]) for r in tx])
    denominator = summary["candidates"]["stages"]["total"]
    axc.plot(thresholds, 100 * counts / denominator, "o-", color=blue, lw=1.4, markersize=4)
    axc.axvline(0.4, color="#858f97", linestyle="--", lw=0.8)
    axc.annotate(
        "Recorded Tox21 cutoff 0.40\n3,011 / 15,740 (19.13%)",
        xy=(0.4, 100 * 3011 / denominator),
        xytext=(0.365, 39),
        fontsize=7.4,
        color=blue,
        bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5},
        arrowprops={"arrowstyle": "-", "color": blue, "lw": 0.7},
    )
    axc.set(
        xlim=(0.29, 0.61),
        ylim=(0, 54),
        xlabel="Nearest labeled-training Morgan similarity cutoff",
        ylabel="Tox21-eligible candidates (%)",
    )
    axc.set_title(
        "Candidate eligibility sensitivity: fixed gates and observed-label precedence", loc="left", pad=9
    )
    axc.grid(axis="y", lw=0.45, color="#e1e5e7")
    hx = [r for r in sensitivity if r["endpoint"] == "KCNH2:IC50"]
    inset = axc.inset_axes([0.56, 0.62, 0.405, 0.30])
    hn = [int(r["eligible_after_fixed_quality_and_observed_precedence"]) for r in hx]
    inset.plot(thresholds, hn, "o-", color=red, lw=0.9, markersize=3)
    inset.axvline(0.5, color=red, linestyle=":", lw=0.8)
    inset.set(xlim=(0.29, 0.61), ylim=(-4, 49), xticks=[0.30, 0.40, 0.50, 0.60], yticks=[0, 20, 40])
    inset.tick_params(labelsize=6, pad=2, length=2)
    inset.set_title("hERG count: 39, 4, then 0 (cutoff >=0.40)", loc="left", fontsize=6.9, pad=5)
    inset.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=3))
    for threshold, count in zip(thresholds, hn):
        plotted.append(
            {
                "panel": "c_inset",
                "x": threshold,
                "y": count,
                "denominator": denominator,
                "source": str(sources[2].relative_to(ROOT)),
                "origin": "cached similarities; no new score",
            }
        )
    for threshold, count in zip(thresholds, counts):
        plotted.append(
            {
                "panel": "c",
                "x": threshold,
                "y": 100 * count / denominator,
                "numerator": int(count),
                "denominator": denominator,
                "source": str(sources[2].relative_to(ROOT)),
                "origin": "cached similarities; no new score",
            }
        )
    for label, ax in zip("abc", [axa, axb, axc]):
        ax.text(
            -0.15 if label != "c" else -0.073,
            1.055,
            label,
            transform=ax.transAxes,
            fontweight="bold",
            fontsize=12,
            va="bottom",
        )
    fig.text(
        0.09,
        0.974,
        "Assay support, residual uncertainty and applicability",
        fontsize=11.5,
        weight="bold",
        va="top",
    )
    fig.text(
        0.09,
        0.034,
        "Retrospective, selection-conditioned analysis. New calibration fit is labeled; cutoff sensitivity does not create new scores.",
        fontsize=7.1,
        color="#59636c",
        va="bottom",
    )
    prefix = output / "figure-S5-assay-audit"
    for extension in ("svg", "pdf"):
        fig.savefig(
            prefix.with_suffix("." + extension),
            metadata={"Creator": "HerbFold round-one archived assay audit"},
        )
    fig.savefig(prefix.with_suffix(".png"), dpi=600)
    fig.savefig(output / "figure-S5-assay-audit.docx.png", dpi=240)
    plt.close(fig)
    write_csv(output / "figure-S5-data.csv", plotted)
    caption = (
        "Supplementary Figure S5. Assay residuals and applicability sensitivity. "
        "(a) Archived predictions for 24 held-out hERG binding-assay structures, arranged by 19 scaffold groups; horizontal offsets only separate structures sharing a scaffold. Blue circles denote the 23 in-domain structures and the red triangle the excluded structure. The shaded band is the fixed archived calibration half-width, not a confidence interval for each point. "
        "(b) A new post-review fixed-RF reconstruction reproduced all 24 archived test predictions within 1e-12 absolute/1e-11 relative tolerance and produced calibration predictions for all 27 structures. The 25 in-domain residuals span 13 scaffolds; two excluded residuals are shown separately. The dashed line is rank 24 of 25 absolute residuals (0.647691 pIC50). The shaded region, 0.605892-1.028695, is the 2.5th/97.5th percentile of 2,000 scaffold-group bootstrap half-widths (seed 20260910), with the finite-sample rank recomputed for every draw size. It describes conditional sensitivity, not guaranteed coverage; these are newly reconstructed residuals, not recovered historical artifacts. "
        "(c) Candidate eligibility from cached nearest-training similarities at descriptive cutoffs 0.30-0.60, holding all model gates and observed-label precedence fixed. The denominator is 15,740 unique standardized candidates and the line is the union across 12 overlapping Tox21 endpoints. The recorded 0.40 cutoff gives 3,011 inferred-score candidates; 36 additional in-domain structures have observed labels instead. The inset shows hERG candidate counts, including zero at its recorded 0.50 cutoff. No threshold was optimized, no new candidate scores were generated, and no biological safety or efficacy conclusion follows. Test and calibration bootstraps condition on the already selected RF and report-quality gates."
    )
    (output / "figure-S5-assay-audit.caption.txt").write_text(caption + "\n")
    if not all(digest(ROOT / r["path"]) == r["sha256"] for r in hashes):
        raise AssertionError("Figure source changed during render")
    artifacts = [
        p
        for p in sorted(output.glob("figure-S5*"))
        if p.is_file()
        and p.name != "figure-S5-manifest.json"
        and not p.name.startswith(("figure-S5-pdf-render", "figure-S5-visual-review"))
    ]
    write_json(
        output / "figure-S5-manifest.json",
        {
            "sources": hashes,
            "all_sources_unchanged": True,
            "matplotlib_version": matplotlib.__version__,
            "plotting_python": platform.python_version(),
            "script_sha256": digest(Path(__file__)),
            "new_model_fits_in_plotting_process": 0,
            "artifacts": [
                {"path": str(p.relative_to(ROOT)), "sha256": digest(p), "bytes": p.stat().st_size}
                for p in artifacts
            ],
        },
    )
    print(
        json.dumps(
            {"stage": "figure_complete", "source_count": len(hashes), "prefix": str(prefix.relative_to(ROOT))}
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research/manuscript/round1/assays")
    parser.add_argument(
        "--reconstruct-rf-only",
        action="store_true",
        help="New fixed-RF CPU fit in a separate post-review subdirectory; no historical outputs overwritten",
    )
    parser.add_argument(
        "--figures-only",
        action="store_true",
        help="Read saved audit/reconstruction values and export S5; requires Matplotlib but performs no fit",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    allowed = ROOT / "research/manuscript/round1/assays"
    if output != allowed and not output.is_relative_to(ROOT / "tmp"):
        raise ValueError("Only the round1 assay output or a new tmp report directory is permitted")
    output.mkdir(parents=True, exist_ok=True)
    if args.reconstruct_rf_only and args.figures_only:
        raise ValueError("Reconstruction and plotting are separate executions")
    if args.figures_only:
        make_figure(output)
        return
    if args.reconstruct_rf_only:
        reconstruct_rf(output)
        return
    audit = Audit()
    audit.read(Path(__file__).relative_to(ROOT))
    bio_source = audit.read("src/herbfold/bio_validation.py").decode()
    tox_source = audit.read("src/herbfold/toxicity_validation.py").decode()
    chemistry_source = audit.read("src/herbfold/chemistry.py").decode()
    models = audit.json(BIO / "model-metrics.json")
    curated = audit.json(BIO / "curated-records.json")
    bio_report = audit.json(BIO / "summary.json")
    tox_report = audit.json(TOX / "summary.json")
    curation = audit.json(BIO / "assay-curation.json")
    source = audit.json(BIO / "source-bundle.json")
    policy = literals(bio_source)["POLICY"]
    audit.check("all_archived_bio_policies_match_current_code", all(m["policy"] == policy for m in models))
    audit.check(
        "tox_archived_execution_code_matches_current",
        hashlib.sha256(tox_source.encode()).hexdigest() == tox_report["protocol"]["code_sha256"],
    )
    audit.check(
        "source_curation_denominator",
        curation["input_rows"] == curation["retained_rows"] + sum(curation["excluded"].values()),
    )
    audit.check("curated_row_count", len(curated) == curation["retained_rows"])
    self_checks(audit)
    gate_ledger = gates(bio_source, tox_source, chemistry_source, policy)
    write_csv(output / "gate_ledger.csv", gate_ledger)
    selection = assay_selection(audit, models, curated, output)
    herg = analyze_herg(audit, models, curated, output)
    print(
        json.dumps(
            {"stage": "herg_complete", "structures": 24, "bootstrap_replicates": BOOTSTRAP_REPLICATES}
        ),
        flush=True,
    )
    tox = analyze_tox(audit, tox_report, output)
    print(json.dumps({"stage": "tox21_complete", "endpoints": len(tox["endpoints"])}), flush=True)
    candidates = candidate_analysis(audit, bio_report, tox_report, output)
    input_path = Path(bio_report["input_snapshot"]["path"])
    audit.check(
        "candidate_input_shared_snapshot",
        hashlib.sha256(audit.read(input_path)).hexdigest()
        == bio_report["input_snapshot"]["sha256"]
        == tox_report["candidates"]["input_sha256"],
    )
    receipts = []
    for path in sorted((ROOT / BIO / "sources").glob("*.manifest.json")):
        value = audit.json(path.relative_to(ROOT))
        raw_path = path.with_name(path.name.replace(".manifest.json", ".json"))
        raw = audit.read(raw_path.relative_to(ROOT))
        audit.check(
            f"chembl_receipt_raw_integrity:{raw_path.name}",
            hashlib.sha256(raw).hexdigest() == value["sha256"]
            and (value.get("bytes") is None or len(raw) == value["bytes"]),
        )
        receipts.append(
            {
                "path": str(path.relative_to(ROOT)),
                "raw_path": str(raw_path.relative_to(ROOT)),
                "recorded_bytes": value.get("bytes"),
                "verified_actual_bytes": len(raw),
                "url": value.get("url"),
                "fetched_at": value.get("fetched_at"),
                "sha256": value.get("sha256"),
            }
        )
    write_csv(output / "chembl_source_receipts.csv", receipts)
    chronology = {
        "status": "historical_preregistration_not_established",
        "bio_report_written_at": bio_report["created_at"],
        "tox_started_at": tox_report["started_at"],
        "tox_completed_at": tox_report["completed_at"],
        "chembl_fetch_earliest": min(r["fetched_at"] for r in receipts if r["fetched_at"]),
        "chembl_fetch_latest": max(r["fetched_at"] for r in receipts if r["fetched_at"]),
        "tox_execution_code_hash_matches": True,
        "bio_execution_code_hash_recorded": False,
        "operational_order": "Source curation → scaffold partition → train fit → RF/Ridge tune selection (bio only) → calibration/test metrics → report-quality gate → candidate applicability/observed precedence.",
        "observed_claims_not_historical_proof": [
            "Tox21 source docstring says declared before reading metrics, and the recorded protocol repeats this wording.",
            "A matching code hash links the executed Tox21 algorithm to its stored report; it does not independently prove cutoff choices preceded all inspection of outcomes.",
            "No preregistration, timestamped outcome-blind policy snapshot or Git history is available in the inspected workspace. Filesystem times are not used to infer policy chronology.",
        ],
        "selection_limitation": "hERG and Tox21 report eligibility uses held-out performance. These are retrospective selection-conditioned results, not untouched prospective performance estimates.",
    }
    write_json(output / "chronology.json", chronology)
    summary = {
        "schema_version": 1,
        "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "R1-3/R1-4/R1-9 archived assay gates, candidate coverage, exact Tox21 snapshot and post hoc uncertainty; no model refitting or new predictions.",
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "sklearn": sklearn.__version__,
            "rdkit": rdBase.rdkitVersion,
            "executable": sys.executable,
        },
        "new_model_fits": 0,
        "new_af3_qpu_llm_jobs": 0,
        "gate_criteria": len(gate_ledger),
        "policy": policy,
        "assay_selection": selection,
        "herg": herg,
        "tox21": tox,
        "candidates": candidates,
        "biochemical_curation": {k: v for k, v in curation.items() if k != "conflicting_groups"},
        "chembl_release": source["chembl_status"],
        "chembl_retrieval": source["retrieval"],
        "chronology": chronology,
        "limitations": [
            "Bootstrap intervals condition on the already selected model, fixed data split and domain; they do not account for training, model selection, gating, calibration, dataset ascertainment or future scaffold shift.",
            "hERG individual calibration residuals and Ridge predictions were not preserved; their absent uncertainty analyses remain null.",
            "Similarity cutoffs were varied only for descriptive cached-score coverage, never optimized against assay outcomes. Lower cutoffs do not authorize new activity or safety predictions.",
            "Tox21 endpoint scores are uncalibrated pathway-assay scores; hERG here measures dofetilide displacement, not patch-clamp function or human safety.",
        ],
    }
    audit.unchanged()
    summary["verification"] = {
        "checks": len(audit.checks),
        "all_passed": all(r["passed"] for r in audit.checks),
        "source_files": len(audit.sources),
        "source_bytes_unchanged": True,
    }
    write_json(output / "summary.json", summary)
    write_json(output / "verification.json", {"checks": audit.checks, "passed": True})
    write_json(
        output / "source_manifest.json",
        {"sources": list(audit.sources.values()), "all_unchanged_after_analysis": True},
    )
    write_csv(output / "source_hashes.csv", list(audit.sources.values()))
    draft = draft_text(summary)
    (output / "methods-results-draft.md").write_text(draft)
    write_json(
        output / "artifact_manifest.json",
        {
            "artifacts": [
                {"path": str(p.relative_to(ROOT)), "sha256": digest(p), "bytes": p.stat().st_size}
                for p in sorted(output.iterdir())
                if p.is_file() and p.name != "artifact_manifest.json"
            ]
        },
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "output": str(output.relative_to(ROOT)),
                "verification": summary["verification"],
            }
        ),
        flush=True,
    )


def draft_text(summary):
    h = summary["herg"]
    c = summary["candidates"]
    n = summary["tox21"]["snapshot"]
    rows = h["bootstrap"]

    def interval(subset, metric):
        return next(r for r in rows if r["subset"] == subset and r["metric"] == metric)

    m = interval("test", "mae")
    mi = interval("test_in_domain", "mae")
    delta = interval("test_in_domain", "paired_mae_improvement_over_median")
    return f"""# Round-one assay audit: Methods and Results draft

## Methods

We reconstructed the executed gates from source code and archived policy/report fields, retaining their exact comparison operators and order in `gate_ledger.csv`. The biochemical pipeline first selects the largest curated assay within each target, endpoint and assay-type stratum, with assay-ID tie breaking. Gates require at least 120 structures and 12 scaffolds, followed by scaffold-disjoint training/tuning/calibration/test partitions of at least 50/15/20/20 structures. Ridge and random forest are fitted on training only and selected by tuning MAE. Morgan similarity of at least 0.5 defines the biochemical domain. At least 20 in-domain calibration and 20 in-domain test observations are required. The recorded residual interval uses nominal 0.9 coverage, half-width at most 1 pActivity unit and empirical test coverage at least 0.8. Further gates require in-domain MAE at most 0.75, strictly more than 5% improvement over the training-median baseline and positive in-domain test R2. These gates use held-out outcomes for report eligibility; qualified performance is consequently selection-conditioned.

For the selected hERG random forest, we used {BOOTSTRAP_REPLICATES:,} post hoc scaffold-group bootstrap replicates with seed {BOOTSTRAP_SEED}. In each replicate, G sorted unique scaffold groups were sampled with replacement and all observations in every selected group retained, including repeated groups. Percentiles were the 2.5th/97.5th quantiles with NumPy's linear interpolation. The model, train-median baseline, domain mask and archived interval width remained fixed. Paired differences resampled the same observations for model and baseline. These intervals condition on the observed split and selected model and omit training, tuning, gate-selection, calibration and future-shift uncertainty. Test-only selected-model predictions survive; no calibration residuals or Ridge per-structure predictions were reconstructed or imputed.

The exact Tox21 gzip snapshot was {n["compressed_bytes"]:,} bytes with SHA-256 `{n["compressed_sha256"]}` and {n["raw_rows"]:,} rows. Its receipt records a DeepChem S3 URL, bytes and hash but no retrieval timestamp or immutable upstream release designation. Structure curation retained 7,617 unique parents after 89 invalid rows and 125 collapsed duplicate rows. Conflicting labels were removed endpoint-wise and missing labels remained missing. A shared 80/20 split of scaffold groups yielded 4,879 training and 2,738 test structures before endpoint-specific label exclusion. The fixed random forest used 128 trees, minimum leaf size 3, square-root feature subsampling and seed 20260908; there was no hyperparameter search or score calibration.

We independently reproduced each archived Tox21 ROC interval from held-out scores: 200 group-bootstrap attempts for all test observations and 100 for the in-domain subset, resetting `default_rng(20260908)` for each call. Entire sorted scaffold blocks were resampled. Single-class draws were skipped; fewer than five groups or fewer than max(20,B/2) valid draws gave no interval. Reporting required at least 20 test positives, 20 test negatives and 30 training positives, ROC lower95 strictly above 0.5, average precision strictly above test prevalence, and Brier loss strictly below the training-prevalence constant baseline. Candidate evidence precedence was an existing observed label, failed model-quality gate, outside-domain status, then a new inferred score. Tox21 applicability used nearest labeled-training Morgan similarity at least 0.4.

Neither the word “predeclared” in a source comment nor a matching execution-code hash is an independently timestamped preregistration. No such policy-registration record or usable Git history was found. We therefore describe these settings as the recorded executable policy and report the outcome-dependent eligibility step explicitly.

## Results

Nine count-selected assay groups were evaluated. Eight failed initial structure/scaffold support. The one qualified group was CHEMBL1827362, measuring displacement of [3H]dofetilide from recombinant human ERG rather than patch-clamp function. It contained 150 structures across 94 scaffolds. Its split was 84 training, 15 tuning, 27 calibration and 24 test structures; only {h["calibration_in_domain"]} calibration structures and 23 test structures met the domain threshold. The archived half-width {h["uncertainty_archived"]["interval_half_width"]:.6f} was the {h["interval_rank_in_domain"]}th ordered absolute residual among those {h["calibration_in_domain"]} calibration observations. Individual calibration residuals were unavailable, preventing an independent calibration-width reconstruction without refitting. Recorded test interval coverage was 21/23; this was a held-out reporting gate, not guaranteed prospective coverage.

Random-forest test MAE was {m["point"]:.6f} pIC50 (scaffold-bootstrap 95% percentile range {m["lower95"]:.6f}–{m["upper95"]:.6f}; n=24), and in-domain MAE was {mi["point"]:.6f} ({mi["lower95"]:.6f}–{mi["upper95"]:.6f}; n=23). The paired in-domain MAE improvement over the training-median baseline was {delta["point"]:.6f} ({delta["lower95"]:.6f}–{delta["upper95"]:.6f}). These ranges condition on model and reporting selection. The RF was chosen by slightly lower tuning MAE although Ridge had slightly lower observed test MAE; no post hoc model substitution was made.

All 15,740 candidates lay outside the qualified biochemical model's original domain, with maximum nearest-training similarity {c["herg_similarity"]["maximum"]:.6f}. Accordingly no new COX-2 or hERG numerical score was emitted. All twelve Tox21 endpoints passed their recorded quality gates; this does not make the twelve tasks independent experimental replications. The exhaustive candidate partition was {c["stages"]["tox_outside_every_domain"]:,} outside every endpoint domain, {c["stages"]["tox_any_prediction"]:,} with at least one inferred score and {c["stages"]["tox_observed_no_prediction_in_domain"]} in-domain candidates whose available observed assay labels took precedence over every otherwise in-domain inference. The 53 candidates with any observed match overlap these categories. Endpoint and cohort memberships must not be summed as independent candidates.

`similarity_sensitivity.csv` varies only cached nearest-training similarity cutoffs from 0.30 to 0.60 while keeping model gates and observed-label precedence fixed. These are post hoc eligibility counts, not newly inferred probabilities or validated alternative thresholds. Absent calibration residuals, missing outside-domain model probabilities, retrospective gate selection and the biochemical assay's narrow binding context remain explicit limitations.
"""


if __name__ == "__main__":
    main()
