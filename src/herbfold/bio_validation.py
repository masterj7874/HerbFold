"""Assay-specific computational evidence; never a clinical efficacy/safety verdict."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
import numpy as np
from rdkit import Chem, DataStructs, rdBase
from rdkit.Chem import rdFingerprintGenerator
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from threadpoolctl import threadpool_limits

from .chemistry import _filter_catalogs, canonical_smiles, scaffold_key, standardize_molecule

TARGETS = [
    {"id": "PTGS2", "label": "Human COX-2 / PTGS2", "chembl_id": "CHEMBL230", "uniprot": "P35354",
     "endpoints": ["Ki", "Kd", "IC50"], "scope": "Target biochemical assay activity; not therapeutic efficacy"},
    {"id": "KCNH2", "label": "Human hERG / KCNH2", "chembl_id": "CHEMBL240", "uniprot": "Q12809",
     "endpoints": ["IC50"], "scope": "Assay-specific hERG binding or functional measurement; not human safety"},
]
_BASE = "https://www.ebi.ac.uk/chembl/api/data"
_FP = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048, includeChirality=True)
POLICY = {
    "min_unique_structures": 120, "min_scaffolds": 12,
    "min_train": 50, "min_tune": 15, "min_calibration": 20, "min_test": 20,
    "similarity_threshold": 0.5, "nominal_coverage": 0.9,
    "max_test_mae": 0.75, "min_relative_baseline_improvement": 0.05,
    "min_empirical_interval_coverage": 0.8, "max_interval_half_width": 1.0,
    "random_state": 2026,
}


def _now():
    return datetime.now(UTC).isoformat()


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def _source_json(client: httpx.Client, directory: Path, name: str, resource: str,
                 params: dict | None = None, refresh: bool = False) -> dict:
    url = f"{_BASE}/{resource}" + ("?" + urlencode(params) if params else "")
    path = directory / f"{name}.json"
    receipt_path = directory / f"{name}.manifest.json"
    if path.exists() and receipt_path.exists() and not refresh:
        receipt = json.loads(receipt_path.read_text())
        raw = path.read_bytes()
        if receipt.get("url") == url and hashlib.sha256(raw).hexdigest() == receipt.get("sha256"):
            return json.loads(raw)
    for attempt in range(3):
        try:
            response = client.get(url)
            response.raise_for_status()
            result = response.json()
            raw = response.content
            path.write_bytes(raw)
            _write(receipt_path, {"url": url, "fetched_at": _now(), "sha256": hashlib.sha256(raw).hexdigest(),
                                  "bytes": len(raw), "license": "ChEMBL CC BY-SA 3.0"})
            time.sleep(0.15)
            return result
        except (httpx.HTTPError, ValueError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    raise RuntimeError("Unreachable source fetch state")


def fetch_chembl_sources(output_root: Path, max_activity_records: int = 5000, refresh: bool = False) -> dict:
    """Bound each target's exact-activity sample; keep Ki, Kd and IC50 distinct."""
    if not isinstance(max_activity_records, int) or not 1 <= max_activity_records <= 50_000:
        raise ValueError("max_activity_records must be between 1 and 50,000 per target")
    directory = Path(output_root) / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    bundle = {"targets": [], "activities": [], "assays": {}, "retrieval": []}
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        bundle["chembl_status"] = _source_json(client, directory, "chembl-status", "status.json", refresh=refresh)
        for target in TARGETS:
            metadata = _source_json(client, directory, f"target-{target['id']}",
                                    f"target/{target['chembl_id']}.json", refresh=refresh)
            accessions = {c.get("accession") for c in metadata.get("target_components", [])}
            if (metadata.get("organism") != "Homo sapiens" or metadata.get("target_type") != "SINGLE PROTEIN"
                    or accessions != {target["uniprot"]}):
                raise ValueError(f"Official target metadata does not match requested human target {target['id']}")
            bundle["targets"].append({**target, "source_url": f"{_BASE}/target/{target['chembl_id']}.json"})
            remaining = max_activity_records
            for endpoint in target["endpoints"]:
                offset, total, retained = 0, None, 0
                while remaining > 0:
                    limit = min(1000, remaining)
                    params = {"target_chembl_id": target["chembl_id"], "standard_type": endpoint,
                              "standard_relation": "=", "standard_units": "nM", "order_by": "activity_id",
                              "limit": limit, "offset": offset}
                    page = _source_json(client, directory, f"activities-{target['id']}-{endpoint}-{offset}-{limit}",
                                        "activity.json", params, refresh)
                    rows = page.get("activities", [])
                    total = page["page_meta"]["total_count"]
                    for row in rows:
                        bundle["activities"].append({**row, "requested_target": target["id"]})
                    retained += len(rows)
                    remaining -= len(rows)
                    offset += len(rows)
                    if not rows or offset >= total:
                        break
                bundle["retrieval"].append({"target": target["id"], "endpoint": endpoint,
                                             "available_exact_nM": total, "downloaded": retained,
                                             "truncated": total is None or retained < total})
                print(f"ChEMBL {target['id']} {endpoint}: {retained}/{total} activities", flush=True)
            assay_ids = sorted({r["assay_chembl_id"] for r in bundle["activities"] if r["requested_target"] == target["id"]})
            for start in range(0, len(assay_ids), 50):
                chunk = assay_ids[start:start + 50]
                params = {"assay_chembl_id__in": ",".join(chunk), "limit": 1000}
                digest = hashlib.sha256(",".join(chunk).encode()).hexdigest()[:12]
                page = _source_json(client, directory, f"assays-{target['id']}-{digest}", "assay.json", params, refresh)
                for assay in page.get("assays", []):
                    bundle["assays"][assay["assay_chembl_id"]] = assay
            print(f"ChEMBL {target['id']}: {len(assay_ids)} assay metadata records requested", flush=True)
    _write(Path(output_root) / "source-bundle.json", bundle)
    return bundle


def curate_assay_records(bundle: dict) -> tuple[list[dict], dict]:
    """Keep exact valid measurements; reject conflicting duplicates within an assay."""
    targets = {target["id"]: target for target in bundle["targets"]}
    groups = defaultdict(list)
    excluded = Counter()
    for original in bundle["activities"]:
        target = targets.get(original.get("requested_target"))
        assay = bundle["assays"].get(original.get("assay_chembl_id"))
        if target is None or assay is None:
            excluded["missing_verified_target_or_assay_metadata"] += 1
            continue
        if (assay.get("confidence_score") != 9 or assay.get("target_chembl_id") != target["chembl_id"]
                or original.get("target_chembl_id") != target["chembl_id"]
                or original.get("target_organism") != "Homo sapiens"):
            excluded["not_high_confidence_human_single_target"] += 1
            continue
        if original.get("assay_variant_mutation") or original.get("assay_variant_accession"):
            excluded["variant_or_mutant_assay"] += 1
            continue
        endpoint = original.get("standard_type")
        if endpoint not in target["endpoints"] or original.get("standard_relation") != "=":
            excluded["endpoint_or_censored_relation"] += 1
            continue
        if original.get("standard_upper_value") not in (None, ""):
            excluded["interval_or_range_measurement"] += 1
            continue
        if original.get("standard_units") != "nM" or original.get("standard_flag") != 1:
            excluded["nonstandard_unit_or_measurement"] += 1
            continue
        if original.get("data_validity_comment") or original.get("potential_duplicate"):
            excluded["source_validity_or_duplicate_flag"] += 1
            continue
        try:
            value = float(original["standard_value"])
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid concentration")
            with rdBase.BlockLogs():
                smiles = canonical_smiles(original["canonical_smiles"])
                scaffold = scaffold_key(smiles)
        except (ValueError, TypeError, KeyError, RuntimeError):
            excluded["invalid_structure_or_value"] += 1
            continue
        row = {
            "target": target["id"], "target_chembl_id": target["chembl_id"], "endpoint": endpoint,
            "assay_id": assay["assay_chembl_id"], "assay_type": assay.get("assay_type"),
            "assay_description": assay.get("description"), "assay_organism": assay.get("assay_organism"),
            "assay_cell_type": assay.get("assay_cell_type"), "confidence_score": 9,
            "smiles": smiles, "scaffold": scaffold, "pactivity": 9.0 - math.log10(value),
            "value_nM": value, "activity_ids": [original["activity_id"]],
            "molecule_chembl_ids": [original["molecule_chembl_id"]],
            "document_chembl_ids": [original.get("document_chembl_id")],
            "source_url": f"{_BASE}/activity/{original['activity_id']}.json",
            "assay_source_url": f"{_BASE}/assay/{assay['assay_chembl_id']}.json",
        }
        groups[(row["target"], endpoint, row["assay_id"], smiles)].append(row)
    curated = []
    conflicts = []
    for identity, records in groups.items():
        values = {round(row["pactivity"], 10) for row in records}
        if len(values) > 1:
            excluded["conflicting_same_assay_replicates"] += len(records)
            conflicts.append({"target": identity[0], "endpoint": identity[1], "assay_id": identity[2],
                              "smiles": identity[3], "measurements": records})
            continue
        row = records[0]
        for key in ("activity_ids", "molecule_chembl_ids", "document_chembl_ids"):
            row[key] = sorted({value for record in records for value in record[key] if value is not None})
        excluded["identical_same_assay_duplicates"] += len(records) - 1
        curated.append(row)
    counts = Counter(f"{r['target']}:{r['endpoint']}:{r['assay_type']}" for r in curated)
    return curated, {
        "input_rows": len(bundle["activities"]), "retained_rows": len(curated),
        "excluded": dict(excluded), "retained_by_target_endpoint_assay_type": dict(counts),
        "conflicting_groups": conflicts,
        "policy": "Exact standardized nM, confidence 9 human single target, no variants/validity flags; "
                  "endpoints and assay ids never pooled. Nonidentical same-assay replicates excluded, not averaged.",
    }


def _fingerprints(smiles: list[str]):
    fps = [_FP.GetFingerprint(standardize_molecule(s)) for s in smiles]
    matrix = np.zeros((len(fps), 2048), dtype=np.uint8)
    for i, fp in enumerate(fps):
        DataStructs.ConvertToNumpyArray(fp, matrix[i])
    return fps, matrix


def scaffold_partitions(rows: list[dict], random_state: int = 2026) -> dict[str, list[int]]:
    """Four scaffold-disjoint partitions, with tuning and calibration separated."""
    indices = np.arange(len(rows))
    groups = np.asarray([row["scaffold"] for row in rows])
    rest_local, test_local = next(GroupShuffleSplit(n_splits=1, test_size=0.2,
                                                   random_state=random_state).split(indices, groups=groups))
    rest = indices[rest_local]
    train_tune_local, calibration_local = next(GroupShuffleSplit(n_splits=1, test_size=0.1875,
        random_state=random_state + 1).split(rest, groups=groups[rest]))
    train_tune = rest[train_tune_local]
    train_local, tune_local = next(GroupShuffleSplit(n_splits=1, test_size=0.230769,
        random_state=random_state + 2).split(train_tune, groups=groups[train_tune]))
    return {"train": train_tune[train_local].tolist(), "tune": train_tune[tune_local].tolist(),
            "calibration": rest[calibration_local].tolist(), "test": indices[test_local].tolist()}


def _metrics(actual, predicted) -> dict:
    actual, predicted = np.asarray(actual), np.asarray(predicted)
    if len(actual) < 2:
        return {"n": len(actual), "mae": None, "rmse": None, "r2": None, "spearman": None}
    rho = float(spearmanr(actual, predicted).statistic) if np.std(predicted) > 1e-12 and np.std(actual) > 1e-12 else None
    return {"n": len(actual), "mae": float(mean_absolute_error(actual, predicted)),
            "rmse": float(math.sqrt(mean_squared_error(actual, predicted))),
            "r2": float(r2_score(actual, predicted)) if np.std(actual) > 1e-12 else None,
            "spearman": rho if rho is not None and math.isfinite(rho) else None}


def conformal_half_width(residuals: Iterable[float], coverage: float = 0.9) -> float:
    errors = np.asarray(list(residuals), dtype=float)
    if not len(errors) or not np.all(np.isfinite(errors)) or np.any(errors < 0) or not 0 < coverage < 1:
        raise ValueError("Finite nonnegative calibration errors and coverage between 0 and 1 required")
    rank = min(len(errors), math.ceil((len(errors) + 1) * coverage))
    return float(np.sort(errors)[rank - 1])


def _nearest(fingerprints, reference_fingerprints) -> np.ndarray:
    return np.asarray([max(DataStructs.BulkTanimotoSimilarity(fp, reference_fingerprints), default=0.0)
                       for fp in fingerprints])


def evaluate_assay(rows: list[dict], policy: dict | None = None) -> tuple[dict, dict | None]:
    """Tune on a separate partition; report untouched scaffold-test performance."""
    policy = {**POLICY, **(policy or {})}
    if not rows:
        raise ValueError("An assay needs at least one curated row")
    identities = {(r["target"], r["endpoint"], r["assay_id"], r["assay_type"]) for r in rows}
    if len(identities) != 1 or len({r["smiles"] for r in rows}) != len(rows):
        raise ValueError("Evaluate one assay/endpoint with unique canonical structures at a time")
    row = rows[0]
    report = {key: row[key] for key in ("target", "endpoint", "assay_id", "assay_type", "assay_description", "assay_source_url")}
    report.update({"id": f"{row['target']}:{row['endpoint']}:{row['assay_id']}",
                   "quality_status": "insufficient_data", "data_counts": {
                       "unique_structures": len(rows), "scaffolds": len({r['scaffold'] for r in rows})},
                   "policy": policy, "split_counts": {}, "metrics": {}, "applicability": {
                       "threshold": policy["similarity_threshold"]}, "uncertainty": None,
                   "selected_model": None, "reason": []})
    if len(rows) < policy["min_unique_structures"] or report["data_counts"]["scaffolds"] < policy["min_scaffolds"]:
        report["reason"] = ["Insufficient unique measured structures or distinct scaffolds within this assay"]
        return report, None
    try:
        split = scaffold_partitions(rows, policy["random_state"])
    except ValueError:
        report["reason"] = ["Unable to construct four scaffold-disjoint partitions"]
        return report, None
    report["split_counts"] = {key: len(indices) for key, indices in split.items()}
    report["splits"] = {key: [{"smiles": rows[i]["smiles"], "scaffold": rows[i]["scaffold"],
                                "activity_ids": rows[i]["activity_ids"]} for i in indices]
                        for key, indices in split.items()}
    if any(len(split[key]) < policy[f"min_{key}"] for key in split):
        report["reason"] = ["Scaffold-disjoint partitions are too small; no random-split substitute used"]
        return report, None
    fps, matrix = _fingerprints([r["smiles"] for r in rows])
    values = np.asarray([r["pactivity"] for r in rows])
    train, tune, calibration, test = [np.asarray(split[k]) for k in ("train", "tune", "calibration", "test")]
    baseline = float(np.median(values[train]))
    estimators = {
        "ridge": Ridge(alpha=10.0, solver="lsqr"),
        "random_forest": RandomForestRegressor(n_estimators=128, min_samples_leaf=3,
                                               max_features=0.33, random_state=policy["random_state"], n_jobs=2),
    }
    predictions = {}
    with threadpool_limits(limits=2):
        for name, estimator in estimators.items():
            estimator.fit(matrix[train], values[train])
            predictions[name] = estimator.predict(matrix)
    selected = min(estimators, key=lambda name: mean_absolute_error(values[tune], predictions[name][tune]))
    report["selected_model"] = selected
    for name, predicted in {"median_baseline": np.full(len(rows), baseline), **predictions}.items():
        report["metrics"][name] = {"tune": _metrics(values[tune], predicted[tune]),
                                   "test": _metrics(values[test], predicted[test])}
    train_fps = [fps[i] for i in train]
    similarities = _nearest(fps, train_fps)
    cal_domain = calibration[similarities[calibration] >= policy["similarity_threshold"]]
    test_domain = test[similarities[test] >= policy["similarity_threshold"]]
    report["applicability"].update({
        "calibration_in_domain": len(cal_domain), "test_in_domain": len(test_domain),
        "calibration_coverage": len(cal_domain) / len(calibration), "test_coverage": len(test_domain) / len(test),
        "reference": "Training-only chirality-aware Morgan radius-2 2048-bit Tanimoto",
    })
    for name, predicted in {"median_baseline": np.full(len(rows), baseline), **predictions}.items():
        report["metrics"][name]["test_in_domain"] = _metrics(values[test_domain], predicted[test_domain])
    report["quality_status"] = "failed_validation"
    reasons = []
    if len(cal_domain) < policy["min_calibration"] or len(test_domain) < policy["min_test"]:
        reasons.append("Insufficient in-domain calibration or independent test coverage")
    if len(cal_domain):
        half_width = conformal_half_width(abs(values[cal_domain] - predictions[selected][cal_domain]),
                                          policy["nominal_coverage"])
        coverage = float(np.mean(abs(values[test_domain] - predictions[selected][test_domain]) <= half_width)) if len(test_domain) else None
        report["uncertainty"] = {
            "method": "Split calibration absolute-residual interval; scaffold shift makes coverage empirical, not guaranteed",
            "nominal_coverage": policy["nominal_coverage"], "empirical_test_coverage": coverage,
            "interval_half_width": half_width, "unit": f"p{row['endpoint']}",
        }
        if half_width > policy["max_interval_half_width"]:
            reasons.append("Calibration interval exceeds allowed width")
        if coverage is None or coverage < policy["min_empirical_interval_coverage"]:
            reasons.append("Independent test interval coverage below threshold")
    else:
        half_width = None
    measured = report["metrics"][selected]["test_in_domain"]
    comparison = report["metrics"]["median_baseline"]["test_in_domain"]
    if measured["mae"] is None or measured["mae"] > policy["max_test_mae"]:
        reasons.append("Independent test MAE does not pass the predeclared threshold")
    if (measured["mae"] is None or comparison["mae"] is None
            or measured["mae"] >= comparison["mae"] * (1 - policy["min_relative_baseline_improvement"])):
        reasons.append("Does not improve the training-median baseline on independent in-domain test data")
    if measured["r2"] is None or measured["r2"] <= 0:
        reasons.append("Independent in-domain R2 is not positive")
    report["reason"] = reasons
    report["test_predictions"] = [{"smiles": rows[i]["smiles"], "actual_pactivity": float(values[i]),
        "predicted_pactivity": float(predictions[selected][i]), "nearest_similarity": float(similarities[i]),
        "in_domain": bool(similarities[i] >= policy["similarity_threshold"])} for i in test]
    if reasons:
        return report, None
    report["quality_status"] = "qualified"
    return report, {"report": report, "estimator": estimators[selected], "train_fps": train_fps,
                    "half_width": half_width, "threshold": policy["similarity_threshold"]}


def _select_assays(records: list[dict]) -> list[list[dict]]:
    """Choose the largest assay per target/endpoint/type without using labels."""
    groups = defaultdict(list)
    for record in records:
        groups[(record["target"], record["endpoint"], record["assay_type"], record["assay_id"])].append(record)
    strata = defaultdict(list)
    for key, rows in groups.items():
        strata[key[:3]].append(rows)
    return [sorted(assays, key=lambda rows: (-len(rows), rows[0]["assay_id"]))[0]
            for _, assays in sorted(strata.items(), key=lambda item: str(item[0]))]


def _prediction_evidence(target: str, endpoint: str, fingerprint, matrix: np.ndarray,
                         runtime_models: list[dict]) -> dict:
    eligible = [model for model in runtime_models if model["report"]["target"] == target
                and model["report"]["endpoint"] == endpoint]
    base = {"target": target, "endpoint": endpoint, "assay_id": None, "status": "abstained"}
    if not eligible:
        return {**base, "reason": "No assay-specific model passed independent evaluation and calibration gates"}
    similarities = [float(_nearest([fingerprint], model["train_fps"])[0]) for model in eligible]
    index = int(np.argmax(similarities))
    model, similarity = eligible[index], similarities[index]
    base.update({"assay_id": model["report"]["assay_id"], "model_id": model["report"]["id"],
                 "assay_type": model["report"].get("assay_type"),
                 "assay_description": model["report"].get("assay_description"),
                 "nearest_similarity": similarity, "source_url": model["report"]["assay_source_url"]})
    if similarity < model["threshold"]:
        return {**base, "reason": "Outside the training-structure applicability threshold"}
    value = float(model["estimator"].predict(matrix.reshape(1, -1))[0])
    return {**base, "status": "predicted", "value_pactivity": value,
            "interval": [value - model["half_width"], value + model["half_width"]],
            "interval_nominal_coverage": model["report"]["uncertainty"]["nominal_coverage"],
            "reason": "Computational assay-specific estimate, not a measured candidate or clinical conclusion"}


def _run_bio_validation(candidates: Iterable[dict], output_root: Path, max_activity_records: int = 5000,
                        refresh: bool = False, discovery_db: Path | None = None, source_bundle: dict | None = None) -> dict:
    """Assess a fixed candidate snapshot against measured assays and alert rules."""
    import sqlite3

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    bundle = source_bundle or fetch_chembl_sources(output_root, max_activity_records, refresh)
    records, curation = curate_assay_records(bundle)
    _write(output_root / "assay-curation.json", curation)
    _write(output_root / "curated-records.json", records)
    reports, runtime_models = [], []
    for assay_rows in _select_assays(records):
        report, runtime_model = evaluate_assay(assay_rows)
        reports.append(report)
        if runtime_model is not None:
            runtime_models.append(runtime_model)
        print(f"Assay {report['id']}: {report['quality_status']} n={len(assay_rows)} {report['reason']}", flush=True)
    represented = {(r["target"], r["endpoint"]) for r in reports}
    for target in TARGETS:
        for endpoint in target["endpoints"]:
            if (target["id"], endpoint) not in represented:
                reports.append({"id": f"{target['id']}:{endpoint}:none", "target": target["id"],
                    "endpoint": endpoint, "assay_id": None, "assay_type": None,
                    "quality_status": "insufficient_data", "data_counts": {"unique_structures": 0, "scaffolds": 0},
                    "split_counts": {}, "metrics": {}, "selected_model": None, "applicability": {
                        "threshold": POLICY["similarity_threshold"]}, "uncertainty": None,
                    "reason": ["No comparable high-confidence exact measurements retained for this endpoint"]})
    _write(output_root / "model-metrics.json", reports)
    exact = defaultdict(list)
    for record in records:
        exact[record["smiles"]].append(record)
    for group in curation["conflicting_groups"]:
        for record in group["measurements"]:
            exact[record["smiles"]].append({**record, "conflicting_replicates": True})
    catalog_connection = None
    if discovery_db is not None and Path(discovery_db).is_file():
        catalog_connection = sqlite3.connect(f"file:{Path(discovery_db).resolve()}?mode=ro", uri=True)
        catalog_connection.row_factory = sqlite3.Row
    counts = Counter(candidates=0, invalid_candidates=0, alerted_candidates=0, catalog_exact_matches=0,
                     exact_measured_candidates=0, predicted_candidates=0, abstained_candidates=0)
    endpoints = [(target["id"], endpoint) for target in TARGETS for endpoint in target["endpoints"]]
    endpoint_counts = {f"{target}:{endpoint}": Counter() for target, endpoint in endpoints}
    cohort_counts = Counter()
    candidate_applicability = {}
    seen = set()
    candidate_path = output_root / "candidates.jsonl"
    candidate_work = output_root / "candidates.jsonl.part"
    try:
        with candidate_work.open("w", encoding="utf-8") as output:
            for candidate in candidates:
                input_smiles = candidate.get("smiles") or candidate.get("canonical_smiles")
                result = {"id": str(candidate.get("id", "")), "smiles": input_smiles,
                          "campaign_ids": candidate.get("campaign_ids", []),
                          "cohort": candidate.get("cohort") or "|".join(candidate.get("cohorts", []))
                                    or "existing_campaign_snapshot",
                          "cohorts": candidate.get("cohorts", []), "sampling": candidate.get("sampling")}
                try:
                    with rdBase.BlockLogs():
                        molecule = standardize_molecule(input_smiles)
                        smiles = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
                    if smiles in seen:
                        continue
                    seen.add(smiles)
                    result["smiles"] = smiles
                    alerts = {name: [match.GetDescription() for match in catalog.GetMatches(molecule)]
                              for name, catalog in _filter_catalogs().items()}
                    result["structural_alerts"] = alerts
                    if any(alerts.values()):
                        counts["alerted_candidates"] += 1
                    known = []
                    if catalog_connection is not None:
                        known = [row[0] for row in catalog_connection.execute(
                            "SELECT id FROM discovery_compounds WHERE canonical_smiles=? LIMIT 10", (smiles,))]
                    result["catalog_identity"] = {"known": bool(known), "matched_ids": known,
                        "checked": catalog_connection is not None,
                        "scope": "Exact catalog canonical SMILES identity; absence does not establish novelty"}
                    counts["catalog_exact_matches"] += bool(known)
                    if runtime_models:
                        fp = _FP.GetFingerprint(molecule)
                        vector = np.zeros(2048, dtype=np.uint8)
                        DataStructs.ConvertToNumpyArray(fp, vector)
                    else:
                        fp, vector = None, np.zeros(0)
                    evidence = []
                    for target, endpoint in endpoints:
                        matches = [r for r in exact.get(smiles, []) if r["target"] == target and r["endpoint"] == endpoint]
                        if matches:
                            for row in matches[:20]:
                                evidence.append({"target": target, "endpoint": endpoint, "assay_id": row["assay_id"],
                                    "assay_type": row["assay_type"], "status": "exact_measured",
                                    "value_pactivity": row["pactivity"], "value_nM": row["value_nM"],
                                    "activity_ids": row["activity_ids"], "source_url": row["source_url"],
                                    "conflicting_replicates": row.get("conflicting_replicates", False),
                                    "matching_assay_records": len(matches), "records_truncated": len(matches) > 20,
                                    "reason": "Database-reported measurement for the same standardized parent structure; not a new experiment"})
                            endpoint_counts[f"{target}:{endpoint}"]["exact_measured"] += 1
                        else:
                            prediction = _prediction_evidence(target, endpoint, fp, vector, runtime_models)
                            evidence.append(prediction)
                            endpoint_counts[f"{target}:{endpoint}"][prediction["status"]] += 1
                            if "nearest_similarity" in prediction:
                                observed = candidate_applicability.setdefault(f"{target}:{endpoint}", {
                                    "assessed": 0, "minimum_similarity": 1.0, "maximum_similarity": 0.0,
                                    "threshold": POLICY["similarity_threshold"], "in_domain": 0})
                                similarity = prediction["nearest_similarity"]
                                observed["assessed"] += 1
                                observed["minimum_similarity"] = min(observed["minimum_similarity"], similarity)
                                observed["maximum_similarity"] = max(observed["maximum_similarity"], similarity)
                                observed["in_domain"] += similarity >= observed["threshold"]
                    result["evidence"] = evidence
                    statuses = {entry["status"] for entry in evidence}
                    counts["exact_measured_candidates"] += "exact_measured" in statuses
                    counts["predicted_candidates"] += "predicted" in statuses
                    counts["abstained_candidates"] += "abstained" in statuses
                    result["status"] = "computational_assessment_complete"
                except (ValueError, TypeError, RuntimeError) as error:
                    result.update({"status": "invalid_structure", "error": str(error), "evidence": []})
                    counts["invalid_candidates"] += 1
                counts["candidates"] += 1
                cohort_counts[result["cohort"]] += 1
                output.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
                if counts["candidates"] % 500 == 0:
                    print(f"Bio candidate assessment: {counts['candidates']}", flush=True)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if catalog_connection is not None:
            catalog_connection.close()
    candidate_work.replace(candidate_path)
    summary = {
        "status": "completed", "created_at": _now(), "elapsed_seconds": round(time.monotonic() - started, 3),
        "scope": "Assay-specific target activity and hERG liability with structural alert triage; "
                 "not drug efficacy, human safety, wet-lab validation or a clinical recommendation",
        "identity_policy": "RDKit Cleanup + FragmentParent with charge/stereochemistry retained; original candidate input remains in input snapshot",
        "counts": dict(counts), "counts_note": "Evidence categories count candidates with at least one such endpoint and can overlap",
        "cohort_counts": dict(cohort_counts),
        "candidate_applicability": candidate_applicability,
        "endpoint_counts": {key: dict(value) for key, value in endpoint_counts.items()},
        "targets": bundle["targets"], "retrieval": bundle["retrieval"],
        "curation": {key: value for key, value in curation.items() if key != "conflicting_groups"},
        "models": [{key: value for key, value in report.items() if key not in {"splits", "test_predictions"}} for report in reports],
        "selection_policy": "Largest curated assay per target/endpoint/assay type selected by count, never by favorable labels. "
                            "Ridge versus random forest selected on scaffold-disjoint tuning data; calibration and test untouched by selection.",
        "candidate_prediction_policy": "Qualified assay model with greatest training similarity selected per endpoint; "
                                       "no qualified model or similarity below 0.5 causes abstention",
        "alert_scope": "PAINS/BRENK matches flag structural review needs; their absence does not establish safety",
        "artifacts": {"candidates": "candidates.jsonl", "models": "model-metrics.json", "curation": "assay-curation.json",
                      "curated_records": "curated-records.json", "sources": "sources/"},
        "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "rdkit_version": rdBase.rdkitVersion,
    }
    _write(output_root / "summary.json", summary)
    return summary


def run_bio_validation(candidates: Iterable[dict], output_root: Path, max_activity_records: int = 5000,
                       refresh: bool = False, discovery_db: Path | None = None, source_bundle: dict | None = None) -> dict:
    """Publish a running status, then atomically publish complete artifacts."""
    output_root = Path(output_root)
    _write(output_root / "summary.json", {"status": "running", "created_at": _now(), "counts": {}, "models": []})
    try:
        return _run_bio_validation(candidates, output_root, max_activity_records, refresh, discovery_db, source_bundle)
    except BaseException as error:
        _write(output_root / "summary.json", {"status": "failed", "created_at": _now(),
                                              "error": f"{type(error).__name__}: {error}", "counts": {}, "models": []})
        raise
