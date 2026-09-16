"""One bounded real projected-kernel comparison, preserving the original analysis.

Default only prepares a plan. --execute submits once on a verified pinned free
Open instance; --refresh retrieves existing IDs without submitting a new job.
No LLM, AF3, account mutation, or paid-instance fallback is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from verify_quantum import VerificationError, free_service

from herbfold.quantum import QuantumExecutionError, plan_quantum, retrieve_kernel, submit_kernel
from herbfold.storage import Store

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = "b3d261566fa14891925d3b1fd730f2d1"


def stamp():
    return datetime.now(UTC).isoformat()


def save(path, evidence):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".writing.json")
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def source_context(store, source_id):
    source = store.get(source_id)
    if source["kind"] != "analysis":
        raise VerificationError("Source must be an actual stored analysis")
    previous = source["result"]["quantum"]
    definition = previous["feature_definition"]
    ids = previous["sample_ids"]
    features = definition["features"]
    if len(ids) != len(features) or len(features) < 2:
        raise VerificationError("Missing original feature row mapping")
    catalog = json.loads((ROOT / "data/compounds.json").read_text())
    names = {row["id"]: row.get("name_ko") or row.get("name") or row["id"] for row in catalog}
    context = {
        "source_analysis_id": source_id,
        "sample_ids": ids,
        "sample_labels": [names.get(key, key) for key in ids],
        "feature_definition": definition,
    }
    original = store.directory(source_id) / "quantum.json"
    baseline = {
        "manifest_path": str(original),
        "manifest_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "kernel": previous.get("kernel"),
        "estimator": previous.get("estimator"),
        "jobs": [row["job_id"] for row in previous.get("jobs", [])],
        "plan": previous.get("plan"),
    }
    return features, context, baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument("--execute", action="store_true")
    flags.add_argument("--refresh", action="store_true")
    parser.add_argument("--source-analysis-id", default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/quantum-projected-verification.json")
    args = parser.parse_args()
    logging.getLogger("qiskit_ibm_runtime").setLevel(logging.ERROR)
    store = Store(ROOT / "runtime")
    existing = json.loads(args.output.read_text()) if args.output.exists() else None
    if existing and existing.get("submission_attempted") and not args.refresh:
        if args.execute:
            raise VerificationError("Already attempted; inspect existing IDs and use --refresh")
        print(json.dumps({k: existing.get(k) for k in ("status", "store_job_id", "jobs")}))
        return
    service, allowance = free_service()
    if args.refresh:
        if not existing or not existing.get("store_job_id"):
            raise VerificationError("No recorded submission to retrieve")
        job = store.get(existing["store_job_id"])
        result = retrieve_kernel(store.directory(job["id"]) / "quantum.json", service=service)
        for key in ("source_analysis_id", "sample_ids", "sample_labels", "feature_definition"):
            result[key] = job["payload"][key]
        store.update(job["id"], result["status"], result)
        original = Path(existing["baseline"]["manifest_path"])
        if hashlib.sha256(original.read_bytes()).hexdigest() != existing["baseline"]["manifest_sha256"]:
            raise VerificationError("Original analysis manifest changed during verification")
        existing.update(
            status=result["status"], refreshed_at=stamp(), free_allowance_after=allowance,
            hardware_executed=result.get("hardware_executed"), result=result,
            original_manifest_unchanged=True,
            jobs=[{"job_id": row["job_id"], "status": row["status"]} for row in result["jobs"]],
        )
        save(args.output, existing)
        print(json.dumps({"status": result["status"], "store_job_id": job["id"],
                          "kernel": result.get("kernel"), "jobs": existing["jobs"]}))
        return
    features, context, baseline = source_context(store, args.source_analysis_id)
    parameters = dict(
        mode="ibm", kernel_method="projected", qubits="max", block_size=4, gamma=1.0,
        layers=1, shots=1024, max_circuits=32, circuits_per_job=32,
        max_total_shots=32768, max_jobs=1, max_execution_time=30,
    )
    plan = plan_quantum(features, service=service, **parameters)
    if plan.get("backend_name"):
        parameters["backend_name"] = plan["backend_name"]
    evidence = {
        "created_at": stamp(), "status": "not_submitted", "submission_attempted": False,
        "free_allowance_before": allowance, "baseline": baseline, "context": context,
        "plan": plan, "parameters": parameters,
        "hardware_executed": False,
        "authorization": "User requested repair and real IBM quantum feature analysis; bounded to one verified free Open job.",
        "scope": "Same stored molecular features and row order; new method, controls and 1024 shots versus original 128. No affinity, efficacy or quantum-advantage validation.",
        "sources": [
            "https://quantum.cloud.ibm.com/docs/en/tutorials/projected-quantum-kernels",
            "https://www.nature.com/articles/s41467-024-49287-w",
        ],
    }
    save(args.output, evidence)
    if not args.execute:
        print(json.dumps({"status": evidence["status"], "plan": plan}))
        return
    if not allowance["eligible_for_free_validation"] or plan["status"] != "ready":
        raise VerificationError("Free allowance or hardware readiness is insufficient; not submitted")
    job = store.create("quantum", {"features": features, **parameters, **context})
    evidence.update(store_job_id=job["id"], submission_attempted=True, status="submitting")
    save(args.output, evidence)
    try:
        result = submit_kernel(features, store.directory(job["id"]) / "quantum.json",
                               execute=True, service=service, **parameters)
    except Exception as exc:
        partial = exc.manifest if isinstance(exc, QuantumExecutionError) else None
        status = partial.get("status", "submission_failed") if partial else "submission_failed"
        store.update(job["id"], status, partial, error="Inspect recorded IDs before any retry")
        evidence.update(status=status, error_type=type(exc).__name__, result=partial)
        save(args.output, evidence)
        raise VerificationError("Submission failed; known IDs preserved, no automatic retry") from None
    result.update(context)
    store.update(job["id"], result["status"], result)
    evidence.update(status=result["status"], submitted_at=stamp(), result=result,
                    jobs=[{"job_id": r["job_id"], "status": r["status"]} for r in result["jobs"]])
    save(args.output, evidence)
    print(json.dumps({"status": evidence["status"], "store_job_id": job["id"], "jobs": evidence["jobs"]}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "stopped", "error_type": type(error).__name__,
                          "detail": str(error) if isinstance(error, VerificationError) else "Inspect safe verification receipt"}))
        raise SystemExit(1) from None
