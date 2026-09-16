"""One explicitly requested, free-Open-plan-only maximal-width hardware check.

Default is read-only. ``--execute`` submits at most one 30-second Runtime job;
``--refresh`` retrieves the already-recorded job without submitting anything.
No token, account ID or instance CRN is written to the verification artifact.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path

from qiskit_ibm_runtime import QiskitRuntimeService

from herbfold.chemistry import describe_molecule
from herbfold.quantum import _service, _write_manifest, inspect_backends, retrieve_kernel, submit_kernel
from herbfold.storage import Store

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "quantum_verification.json"


class VerificationError(ValueError):
    """A safe, application-authored verification failure."""


def stamp():
    return datetime.now(UTC).isoformat()


def free_service():
    service = _service()
    active = service.active_instance()
    records = service.instances()
    current = next((row for row in records if row.get("crn") == active), None)
    if current is None or current.get("plan") != "open":
        raise VerificationError("The active instance is not demonstrably Open/free; no job will be submitted")
    # Pin the exact verified free instance; never allow a paid fallback.
    account = service.active_account()
    service = QiskitRuntimeService(channel="ibm_quantum_platform", token=account["token"], instance=active)
    if service.active_instance() != active:
        raise VerificationError("Pinned instance verification failed")
    usage = service.usage()
    consumed = float(usage["usage_consumed_seconds"])
    limit = float(usage["usage_limit_seconds"])
    remaining = min(limit - consumed, float(usage.get("usage_remaining_seconds", limit - consumed)))
    if not all(math.isfinite(x) for x in (consumed, limit, remaining)):
        raise VerificationError("Could not establish a finite free usage allowance")
    evidence = {
        "checked_at": stamp(),
        "instance_plan": "open",
        "instance_pinned": True,
        "usage_consumed_seconds": consumed,
        "usage_limit_seconds": limit,
        "usage_remaining_seconds": remaining,
        "usage_limit_reached": usage.get("usage_limit_reached"),
        "usage_period": usage.get("usage_period"),
        "required_maximum_qpu_seconds": 30,
        "eligible_for_free_validation": usage.get("usage_limit_reached") is False and remaining >= 30,
        "sources": [
            "https://quantum.cloud.ibm.com/docs/en/guides/instances",
            "https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/qiskit-runtime-service",
            "https://quantum.cloud.ibm.com/docs/en/api/qiskit-runtime-rest/tags/instances",
        ],
    }
    return service, evidence


def save(evidence):
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument("--execute", action="store_true")
    flags.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    # Provider warning messages sometimes list account identifiers. Keep stdout
    # limited to the explicitly filtered artifact below.
    logging.getLogger("qiskit_ibm_runtime").setLevel(logging.ERROR)
    if not args.refresh and EVIDENCE.exists():
        old = json.loads(EVIDENCE.read_text())
        if old.get("store_job_id") or old.get("submission_attempted"):
            if args.execute:
                raise VerificationError(
                    "A verification submission was already attempted. Use --refresh, not --execute"
                )
            print(json.dumps(old, ensure_ascii=False))
            return
    service, eligibility = free_service()
    store = Store(ROOT / "runtime")
    if args.refresh:
        evidence = json.loads(EVIDENCE.read_text())
        job_id = evidence["store_job_id"]
        result = retrieve_kernel(store.directory(job_id) / "quantum.json", service=service)
        if result["status"] == "completed":
            for record in result["jobs"]:
                runtime_job = service.job(record["job_id"])
                try:
                    used = float(runtime_job.usage())
                    record["quantum_seconds"] = used if math.isfinite(used) else None
                except Exception:  # noqa: BLE001 - optional provider telemetry must not obscure successful measurements
                    pass
                pubs = runtime_job.result(timeout=1)
                record["measurement_diagnostics"] = []
                for pub in pubs:
                    counts = pub.data.meas.get_counts()
                    total = sum(counts.values())
                    record["measurement_diagnostics"].append(
                        {
                            "bitstring_width": pub.data.meas.num_bits,
                            "observed_distinct_bitstrings": len(counts),
                            "minimum_observed_hamming_weight": min(bits.count("1") for bits in counts),
                            "mean_hamming_weight": sum(
                                bits.count("1") * count for bits, count in counts.items()
                            )
                            / total,
                        }
                    )
            _write_manifest(store.directory(job_id) / "quantum.json", result)
        store.update(job_id, result["status"], result)
        evidence.update(
            status=result["status"],
            refreshed_at=stamp(),
            jobs=[
                {
                    "job_id": job["job_id"],
                    "status": job["status"],
                    "quantum_seconds": job.get("quantum_seconds"),
                    "observations": job.get("observations"),
                    "measurement_diagnostics": job.get("measurement_diagnostics"),
                }
                for job in result["jobs"]
            ],
            usage_after=eligibility,
            hardware_executed=result.get("hardware_executed"),
        )
        if "kernel" in result:
            evidence.update(kernel=result["kernel"], kernel_diagnostics=result["kernel_diagnostics"])
        save(evidence)
        print(json.dumps(evidence, ensure_ascii=False))
        return
    inventory = inspect_backends(service)
    selected = next(
        (b for b in inventory["backends"] if b["name"] == inventory["selected_max_backend"]), None
    )
    molecules = json.loads((ROOT / "data" / "compounds.json").read_text())
    selected_molecules = [
        next(row for row in molecules if row["id"] == name) for name in ("quercetin", "luteolin")
    ]
    feature_names = ["molecular_weight/500", "logp/5", "tpsa/150", "hbd/5", "hba/10", "qed"]
    features = []
    for molecule in selected_molecules:
        d = describe_molecule(molecule["smiles"])
        features.append(
            [
                d["molecular_weight"] / 500,
                d["logp"] / 5,
                d["tpsa"] / 150,
                d["hbd"] / 5,
                d["hba"] / 10,
                d["qed"],
            ]
        )
    payload = {
        "features": features,
        "mode": "ibm",
        "qubits": "max",
        "shots": 1024,
        "max_circuits": 3,
        "circuits_per_job": 3,
        "max_total_shots": 3072,
        "max_jobs": 1,
        "max_execution_time": 30,
        "layers": 2,
        "backend_name": None,
    }
    evidence = {
        "checked_at": stamp(),
        "status": "not_submitted",
        "submission_attempted": False,
        "free_plan_evidence": eligibility,
        "backend": {key: selected[key] for key in ("name", "num_qubits", "usable_qubits", "operational")}
        if selected
        else None,
        "molecules": [
            {key: row[key] for key in ("id", "smiles", "pubchem_cid", "source_url")}
            for row in selected_molecules
        ],
        "feature_names": feature_names,
        "payload": payload,
        "purpose": "Minimal real maximum-accessible-qubit execution check; molecular feature similarity only, not binding affinity or efficacy.",
        "authorization": "User requested IBM Quantum maximum qubits; this check is constrained to verified Open/free allowance and one job.",
        "hardware_executed": False,
    }
    save(evidence)
    if not args.execute:
        print(json.dumps(evidence, ensure_ascii=False))
        return
    if not eligibility["eligible_for_free_validation"] or selected is None:
        raise VerificationError("Free allowance or operational backend is insufficient; no submission")
    job = store.create("quantum", payload)
    evidence.update(store_job_id=job["id"], submission_attempted=True, status="submitting")
    save(evidence)
    try:
        result = submit_kernel(
            manifest_path=store.directory(job["id"]) / "quantum.json",
            execute=True,
            service=service,
            **payload,
        )
    except Exception as exc:  # noqa: BLE001 - preserve journal on any provider failure without exposing secrets
        store.update(
            job["id"],
            "failed",
            error=f"Verification submission failed ({type(exc).__name__}); inspect quantum.json before any new experiment",
        )
        evidence.update(status="submission_failed", error_type=type(exc).__name__)
        save(evidence)
        raise VerificationError(f"Submission failed ({type(exc).__name__}); no automatic retry") from None
    store.update(job["id"], result["status"], result)
    evidence.update(
        status=result["status"],
        submitted_at=stamp(),
        backend={
            "name": result["plan"]["backend_name"],
            "num_qubits": result["plan"]["backend_num_qubits"],
            "used_qubits": result["plan"]["n_qubits"],
        },
        compilation={
            key: result["compiled"][key]
            for key in ("logical_qubits", "depth", "operation_counts", "connected_components")
        },
        jobs=[{"job_id": row["job_id"], "status": row["status"]} for row in result["jobs"]],
        hardware_executed=None,
    )
    save(evidence)
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - never print a provider traceback containing account data
        # No provider exception strings/tracebacks with potential credentials.
        print(
            json.dumps(
                {
                    "status": "stopped",
                    "error_type": type(exc).__name__,
                    "detail": str(exc)
                    if isinstance(exc, VerificationError)
                    else "Inspect the safe verification artifact",
                }
            )
        )
        raise SystemExit(1) from None
