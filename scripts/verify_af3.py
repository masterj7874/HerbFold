#!/usr/bin/env python3
"""Run and record actual AF3 inference; never manufacture prediction artifacts.

Primary smoke: human PTGS2 (UniProt P35354) + ibuprofen, MSA/template-free,
seed 1, one diffusion sample, three recycles. Only an OOM failure permits the
explicitly labeled fallback to the 21-residue human insulin A chain.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from herbfold import alphafold
from herbfold.connectors import uniprot_lookup
from herbfold.storage import Store
from herbfold.structure import pocket_features

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs/af3_verification.json"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def save_report(report):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(REPORT_PATH)


def load_target():
    path = ROOT / "data/ptgs2.json"
    if path.is_file():
        value = json.loads(path.read_text())
    else:
        value = uniprot_lookup("P35354")
        path.write_text(json.dumps(value, indent=2) + "\n")
    if value["accession"] != "P35354" or len(value["sequence"]) != 604:
        raise ValueError("PTGS2 smoke requires the verified human P35354 sequence (604 residues)")
    return value


def execute_attempt(store, config, target, ligand, label, timeout, report):
    data = alphafold.build_input(
        name=label,
        proteins=[{"id": "A", "sequence": target["sequence"], "description": target["name"]}],
        ligands=[
            {
                "id": "B",
                "smiles": ligand["smiles"],
                "description": "Ibuprofen; unspecified stereochemistry from catalog",
            }
        ],
        seeds=[1],
        msa_mode="none",
    )
    job = store.create("alphafold_smoke", data)
    job_id = job["id"]
    directory = store.directory(job_id)
    plan = alphafold.prepare_job(directory, data, config)
    attempt = {
        "job_id": job_id,
        "target_accession": target["accession"],
        "target_name": target["name"],
        "protein_residues": len(target["sequence"]),
        "target_source": target["source"],
        "target_sequence_sha256": hashlib.sha256(target["sequence"].encode()).hexdigest(),
        "ligand": ligand["id"],
        "ligand_source": ligand["source_url"],
        "input_sha256": plan["input_sha256"],
        "started_at": timestamp(),
        "status": "running" if plan["runnable"] else "blocked",
        "execution_verified": False,
        "runtime_log": f"runtime/{job_id}/run.log",
        "manifest": f"runtime/{job_id}/af3_manifest.json",
    }
    report["attempts"].append(attempt)
    report["status"] = attempt["status"]
    save_report(report)
    print(json.dumps({"event": "started", **attempt}), flush=True)
    if not plan["runnable"]:
        store.update(job_id, "blocked", plan, "; ".join(plan["blockers"]))
        attempt.update(status="blocked", blockers=plan["blockers"])
        save_report(report)
        return False, False
    store.update(job_id, "running", plan)
    started = time.monotonic()
    try:
        with (directory / "run.log").open("w") as log:
            process = subprocess.Popen(
                plan["command"],
                cwd=plan["cwd"],
                env=alphafold.execution_environment(),
                stdout=log,
                stderr=subprocess.STDOUT,
                shell=False,
                start_new_session=True,
            )
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise TimeoutError(f"AF3 smoke exceeded {timeout} seconds")
        attempt.update(
            returncode=returncode,
            elapsed_seconds=round(time.monotonic() - started, 2),
            finished_at=timestamp(),
        )
        if returncode != 0:
            text = (directory / "run.log").read_text(errors="replace")
            oom = any(
                marker in text.lower()
                for marker in (
                    "resource_exhausted",
                    "out of memory",
                    "failed to allocate",
                    "cuda_error_out_of_memory",
                )
            )
            attempt.update(status="failed", failure_category="out_of_memory" if oom else "process_error")
            store.update(
                job_id,
                "failed",
                {"plan": plan, "verification": attempt},
                f"Real AF3 process exited {returncode}; see run.log",
            )
            report["status"] = "failed"
            save_report(report)
            print(json.dumps({"event": "failed", **attempt}), flush=True)
            return False, oom
        results = alphafold.parse_outputs(plan["output_dir"])
        if not results["models"]:
            raise ValueError("AF3 exited successfully without paired confidence/structure artifacts")
        top = next((m for m in results["models"] if m["is_top_ranked_copy"]), results["models"][0])
        structure = alphafold.safe_output_path(plan["output_dir"], top["structure_path"])
        geometry = pocket_features(structure, ligand_chain="B", protein_chains=["A"])
        results.update(
            execution_verified=True,
            validation_scope="actual_structure_inference_smoke_only",
            execution={
                "returncode": returncode,
                "af3_version": alphafold.AF3_VERSION,
                "source_commit": alphafold.AF3_COMMIT,
                "input_sha256": plan["input_sha256"],
                "elapsed_seconds": attempt["elapsed_seconds"],
            },
            pocket_geometry=geometry,
        )
        store.write(job_id, "result.json", results)
        store.update(job_id, "completed", results)
        attempt.update(
            status="completed",
            execution_verified=True,
            metrics=top["metrics"],
            chain_ids=top["chain_ids"],
            structure_sha256=top["structure_sha256"],
            structure=f"runtime/{job_id}/output/{top['structure_path']}",
            result=f"runtime/{job_id}/result.json",
            pocket_geometry=geometry,
        )
        report.update(
            status="completed",
            completed_at=timestamp(),
            execution_verified=True,
            verified_job_id=job_id,
            verified_target=target["accession"],
        )
        save_report(report)
        print(json.dumps({"event": "completed", **attempt}), flush=True)
        return True, False
    except Exception as exc:
        attempt.update(
            status="failed",
            finished_at=timestamp(),
            elapsed_seconds=round(time.monotonic() - started, 2),
            failure_category=type(exc).__name__,
        )
        store.update(job_id, "failed", {"plan": plan, "verification": attempt}, str(exc))
        report["status"] = "failed"
        save_report(report)
        print(json.dumps({"event": "failed", **attempt}), flush=True)
        return False, False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--no-fallback", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 7200:
        parser.error("timeout must be 1–7200 seconds")
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("AF3_CUDA_VISIBLE_DEVICES", "1")
    os.environ["AF3_PREALLOCATE"] = "false"
    config = replace(alphafold.AF3Config.from_env(), num_diffusion_samples=1, num_recycles=3)
    store = Store(ROOT / "runtime")
    ligand = next(
        item for item in json.loads((ROOT / "data/compounds.json").read_text()) if item["id"] == "ibuprofen"
    )
    report = {
        "af3_version": alphafold.AF3_VERSION,
        "source_commit": alphafold.AF3_COMMIT,
        "created_at": timestamp(),
        "scope": "real_inference_smoke_test",
        "model_seeds": [1],
        "samples_per_seed": 1,
        "num_recycles": 3,
        "msa_mode": "none",
        "templates": "none",
        "execution_verified": False,
        "device": config.device,
        "cuda_visible_devices": os.environ.get("AF3_CUDA_VISIBLE_DEVICES"),
        "scientific_limits": [
            "Single-seed, single-sample, reduced-recycle, MSA-free smoke test checks execution, not accuracy or efficacy.",
            alphafold.CONFIDENCE_NOTE,
            "Ibuprofen catalog structure has unspecified stereochemistry; no stereoisomer-specific effect is established.",
            "If PTGS2 runs out of memory, a separately identified insulin A-chain fallback only tests the software execution path.",
        ],
        "attempts": [],
    }
    ok, oom = execute_attempt(
        store, config, load_target(), ligand, "ptgs2_ibuprofen_smoke", args.timeout, report
    )
    if not ok and oom and not args.no_fallback:
        insulin = uniprot_lookup("P01308")
        chain = insulin["sequence"][-21:]
        if chain != "GIVEQCCTSICSLYQLENYCN":
            raise ValueError("Unexpected human insulin A-chain sequence")
        target = {
            **insulin,
            "name": "Human insulin mature A chain (21 residues; execution smoke fallback)",
            "sequence": chain,
        }
        ok, _ = execute_attempt(
            store, config, target, ligand, "insulin_a_ibuprofen_smoke_fallback", args.timeout, report
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
