"""Run the local design API in an isolated store and verify actual CPU results.

Only the Python standard library is imported by this driver. The subprocess uses
the installed HerbFold application and its ordinary dependencies. Synthetic
assay labels are written ONLY inside a TemporaryDirectory for identity controls;
they must never be copied to runtime or reported as scientific observations.
No external engines or biological prediction endpoints are submitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
PREFIX = "/api/design-pipeline"
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
SYNTHETIC_SOURCE = "test://design-pipeline-isolated-parent-not-scientific-data"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def check_finite(value):
    if isinstance(value, float):
        assert math.isfinite(value), "Non-finite numerical result"
    elif isinstance(value, dict):
        for item in value.values():
            check_finite(item)
    elif isinstance(value, list):
        for item in value:
            check_finite(item)


class IsolatedAPI:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.log = None
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        # Do not forward workstation credentials or runtime paths to this server.
        excluded = ("HERBFOLD_", "AF3_", "OPENAI_", "IBM_", "QISKIT_")
        env = {key: value for key, value in os.environ.items() if not key.startswith(excluded)}
        env.update({
            "PYTHON_DOTENV_DISABLED": "1",
            "HERBFOLD_DATA_DIR": str(self.root / "store"),
            "HERBFOLD_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "PYTHONPATH": str(REPO / "src"),
        })
        self.log = (self.root / "server.log").open("a")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "herbfold.api:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=REPO, env=env, stdout=self.log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Isolated API exited before readiness; see verifier server log")
            try:
                self.request("GET", PREFIX + "/options")
                return
            except (URLError, TimeoutError):
                time.sleep(0.1)
        raise TimeoutError("Isolated API readiness exceeded 45 seconds")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()

    def request(self, method, path, payload=None, *, expected=(200, 202)):
        body = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        request = Request(self.url + path, data=body, method=method,
                          headers={"Content-Type": "application/json"} if body else {})
        try:
            with urlopen(request, timeout=15) as response:
                status, raw = response.status, response.read()
        except HTTPError as error:
            status, raw = error.code, error.read()
        value = json.loads(raw)
        assert status in expected, f"{method} {path}: HTTP {status}, {value}"
        check_finite(value)
        return value

    def wait(self, run_id):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            value = self.request("GET", f"{PREFIX}/runs/{run_id}")
            if value["status"] in TERMINAL:
                return value
            time.sleep(0.1)
        raise TimeoutError(f"Design run {run_id} did not finish within 120 seconds")


def candidates(run):
    return (run.get("result") or {}).get("candidates", [])


def candidate_signature(rows):
    keys = ("id", "name", "smiles", "kind", "parents", "descriptors",
            "descriptor_delta", "components")
    return digest([{key: row.get(key) for key in keys} for row in rows])


def write_synthetic_evidence(root, canonical):
    """Only an isolated identity-join fixture; label 0 is deliberately valid."""
    validation = root / "store" / "validation"
    biology = validation / "bio-validation" / "curated-records.json"
    safety = validation / "tox21" / "curated-labels.jsonl"
    biology.parent.mkdir(parents=True, exist_ok=True)
    safety.parent.mkdir(parents=True, exist_ok=True)
    common = {"id": "synthetic-parent-identity-control", "smiles": canonical,
              "synthetic_fixture": True}
    biology.write_text(json.dumps([{**common,
        "target": "PTGS2", "endpoint": "IC50",
        "pactivity": 0.0, "value_nM": 1000000000.0,
        "assay_id": "SYNTHETIC_CONTROL_ONLY", "source_url": SYNTHETIC_SOURCE,
        "activity_ids": ["SYNTHETIC_PARENT_ONLY"],
        "reason": "Synthetic API identity control, not a scientific observation",
    }]) + "\n")
    safety.write_text(json.dumps({**common, "labels": {"SR-ARE": 0},
                                 "source_ids": ["SYNTHETIC_PARENT_ONLY"]}) + "\n")


def verify(api, root, report):
    options = api.request("GET", PREFIX + "/options")
    report["options_sha256"] = digest(options)
    catalog = {row["id"]: row for row in api.request("GET", "/api/catalog")}
    fields = ("id", "name", "smiles", "category", "source_url")
    parents = [{key: catalog[name][key] for key in fields if key in catalog[name]}
               for name in ("quercetin", "aspirin")]
    canonical = {
        row["id"]: api.request("POST", "/api/molecules/describe", {"smiles": row["smiles"]})
        ["canonical_smiles"] for row in parents
    }
    write_synthetic_evidence(root, canonical["quercetin"])
    report["synthetic_evidence_scope"] = (
        "Only temporary parent-structure join controls; no biological accuracy inference"
    )
    base = {"name": "Isolated API verification", "compounds": parents,
            "target_accession": "P35354", "max_candidates": 6,
            "transformations": ["o_methylation", "o_acetylation", "stereoisomers"], "seed": 42}
    report["input_sha256"] = digest(base)
    completed = {}
    for mode in ("combination", "hybrid", "transform"):
        payload = {**base, "mode": mode}
        submitted = api.request("POST", PREFIX + "/runs", payload)
        run = api.wait(submitted["id"])
        assert run["status"] == "completed", run
        rows = candidates(run)
        assert 0 < len(rows) <= base["max_candidates"], (mode, len(rows))
        assert len({row["id"] for row in rows}) == len(rows), "Candidate IDs must be unique"
        observed_parent_control = False
        for row in rows:
            if mode == "combination":
                assert not row.get("smiles"), "A combination is not one covalent molecule"
                assert not row.get("descriptors"), "Do not invent aggregate molecular descriptors"
                assert len(row.get("components", [])) >= 2
                observed_parent_control |= SYNTHETIC_SOURCE in json.dumps(row.get("components"))
                for component in row["components"]:
                    actual = api.request("POST", "/api/molecules/describe", {"smiles": component["smiles"]})
                    assert component["descriptors"]["canonical_smiles"] == actual["canonical_smiles"]
                    for key in ("formula", "molecular_weight", "logp", "tpsa", "hbd", "hba", "qed"):
                        assert component["descriptors"][key] == actual[key], (mode, component["id"], key)
            else:
                smiles = row.get("smiles")
                assert smiles and smiles not in canonical.values(), "Expected a changed chemical structure"
                actual = api.request("POST", "/api/molecules/describe", {"smiles": smiles})
                descriptor = row["descriptors"]
                for key in ("formula", "molecular_weight", "logp", "tpsa", "hbd", "hba", "qed"):
                    assert descriptor[key] == actual[key], (mode, row["id"], key)
                assert SYNTHETIC_SOURCE not in json.dumps(row.get("evidence", [])), (
                    "Parent observation falsely inherited by a new structure"
                )
                assert row.get("evidence") == [], (
                    "Only parent structures have observations in this isolated store; a changed child must not inherit target or pathway labels"
                )
                if mode == "transform":
                    method = row["provenance"]["method"]
                    parent_id = row["parents"][0]["id"]
                    delta = row["descriptor_delta"][parent_id]
                    # An independent stoichiometric check: replace one O–H with
                    # O–CH3 (+CH2) or O–COCH3 (+C2H2O), losing one donor.
                    if method in {"o_methylation", "o_acetylation"}:
                        expected_mass = 14.027 if method == "o_methylation" else 42.037
                        assert abs(delta["molecular_weight"] - expected_mass) < 0.002
                        assert delta["hbd"] == -1
            assert row.get("limitations"), "Candidates require their interpretation limits"
        if mode == "combination":
            assert observed_parent_control, "Positive parent observation control was not exercised"
            parent_evidence = [item for row in rows for component in row["components"]
                               for item in component.get("evidence", [])]
            assert any(item.get("kind") == "pathway_assay" and item.get("label") == 0
                       for item in parent_evidence), "Inactive label 0 was dropped"
            assert any(item.get("kind") == "target_assay" and item.get("pactivity") == 0
                       for item in parent_evidence), "Valid pactivity 0 was dropped"
            report["inactive_and_zero_parent_values_preserved"] = True
        loaded = api.request("GET", f"{PREFIX}/runs/{run['id']}")
        assert loaded == run, "Completed result changed when reloaded"
        repeated = api.wait(api.request("POST", PREFIX + "/runs", payload)["id"])
        assert repeated["status"] == "completed"
        signature = candidate_signature(rows)
        assert candidate_signature(candidates(repeated)) == signature, "Identical inputs changed chemistry"
        completed[run["id"]] = run
        report.setdefault("modes", {})[mode] = {
            "status": run["status"], "candidate_count": len(rows),
            "candidate_signature_sha256": signature, "repeat_matches": True,
            "descriptor_recalculation_matches": True,
            "descriptor_scope": "individual_components" if mode == "combination" else "generated_structures",
        }
    listed = api.request("GET", PREFIX + "/runs")["items"]
    assert set(completed).issubset({row["id"] for row in listed})
    report["history_contains_all_modes"] = True

    wrong_target = api.wait(api.request("POST", PREFIX + "/runs", {
        **base, "mode": "combination", "target_accession": "P00533",
    })["id"])
    assert wrong_target["status"] == "completed"
    wrong_evidence = [item for row in candidates(wrong_target) for component in row["components"]
                      for item in component.get("evidence", [])]
    assert not any(item["kind"] == "target_assay" for item in wrong_evidence)
    assert any(item["kind"] == "pathway_assay" and item["label"] == 0 for item in wrong_evidence)
    report["wrong_target_does_not_link_ptgs2_observation"] = True
    report["pathway_label_remains_separate_from_selected_target"] = True

    stereo_parent = {key: catalog["ibuprofen"][key] for key in fields if key in catalog["ibuprofen"]}
    stereo = api.wait(api.request("POST", PREFIX + "/runs", {
        **base, "mode": "transform", "compounds": [stereo_parent], "transformations": ["stereoisomers"],
    })["id"])
    assert stereo["status"] == "completed"
    stereo_rows = candidates(stereo)
    assert len(stereo_rows) == 2 and len({row["smiles"] for row in stereo_rows}) == 2
    assert all(row["descriptors"]["unassigned_stereocenters"] == 0 for row in stereo_rows)
    assert all(row["descriptors"]["formula"] == "C13H18O2" for row in stereo_rows)
    assert all(all(value == 0 for value in row["descriptor_delta"][stereo_parent["id"]].values())
               for row in stereo_rows)
    report["stereoisomers"] = {"candidate_count": 2, "distinct_graphs": True,
                              "specified_stereo": True, "same_2d_descriptors": True}

    assay_input = {
        "component_a": "SYNTHETIC A", "component_b": "SYNTHETIC B",
        "assay_context": "Synthetic arithmetic control only", "source": SYNTHETIC_SOURCE,
        "concentration_unit": "uM", "matched_conditions": True,
        "points": [
            {"concentration_a": 1.0, "concentration_b": 1.0,
             "inhibition_a": 0.2, "inhibition_b": 0.3, "inhibition_combination": 0.6},
            {"concentration_a": 0.0, "concentration_b": 0.0,
             "inhibition_a": 0.0, "inhibition_b": 0.0, "inhibition_combination": 0.0},
        ],
    }
    assay = api.request("POST", PREFIX + "/combination-assay", assay_input)
    assert assay["source_verified"] is False
    assert assay["points"][0]["bliss_expected"] == 0.44
    assert assay["points"][0]["bliss_excess_percentage_points"] == 16.0
    assert assay["points"][0]["hsa_excess_percentage_points"] == 30.0
    assert assay["points"][1]["bliss_expected"] == 0.0
    assert assay["points"][1]["bliss_excess_percentage_points"] == 0.0
    api.request("POST", PREFIX + "/combination-assay", {**assay_input, "matched_conditions": False},
                expected=(422,))
    for invalid_confirmation in (1, 1.0, "true"):
        api.request("POST", PREFIX + "/combination-assay", {
            **assay_input, "matched_conditions": invalid_confirmation,
        }, expected=(422,))
    report["combination_assay_arithmetic"] = {
        "synthetic_only": True, "bliss_and_hsa_match": True,
        "valid_zeros_retained": True, "unmatched_conditions_rejected": True,
        "coerced_confirmation_rejected": True,
    }

    invalid = {**base, "mode": "transform", "compounds": [{**parents[0], "smiles": "not_smiles"}]}
    before = {row["id"] for row in api.request("GET", PREFIX + "/runs")["items"]}
    api.request("POST", PREFIX + "/runs", invalid, expected=(422,))
    after = {row["id"] for row in api.request("GET", PREFIX + "/runs")["items"]}
    assert before == after, "Invalid request persisted as a runnable design"
    report["invalid_structure_rejected_before_job_creation"] = True

    submitted = api.request("POST", PREFIX + "/runs", {**base, "mode": "hybrid"})
    cancelled = api.request("POST", f"{PREFIX}/runs/{submitted['id']}/cancel", {})
    terminal = api.wait(submitted["id"])
    assert terminal["status"] in {"cancelled", "completed"}, terminal
    report["cancel"] = {"immediate_status": cancelled["status"], "terminal_status": terminal["status"],
                        "completed_before_cancel": terminal["status"] == "completed"}
    # Terminal cancel must not erase an existing result or reactivate execution.
    existing_id = next(iter(completed))
    terminal_cancel = api.request("POST", f"{PREFIX}/runs/{existing_id}/cancel", {})
    assert terminal_cancel["status"] == "completed"
    assert candidates(terminal_cancel) == candidates(completed[existing_id])
    report["completed_cancel_preserves_result"] = True

    competing = IsolatedAPI(root)
    try:
        competing.start()
        raise AssertionError("Second service acquired the same runtime while the first was active")
    except RuntimeError:
        assert "active design-pipeline owner" in (root / "server.log").read_text()
        report["second_service_rejected_without_recovering_first"] = True
    finally:
        competing.stop()
    for run_id, run in completed.items():
        assert api.request("GET", f"{PREFIX}/runs/{run_id}") == run

    api.stop()
    api.start()
    for run_id, run in completed.items():
        assert api.request("GET", f"{PREFIX}/runs/{run_id}") == run
    report["server_restart_preserves_completed_results"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="tmp/design-pipeline-api-verification.json")
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {"started_utc": datetime.now(UTC).isoformat(), "status": "running",
              "mode": "isolated_loopback_api", "python": sys.version.split()[0],
              "production_runtime_modified": False, "external_engine_submissions": 0}
    with tempfile.TemporaryDirectory(prefix="herbfold-design-api-") as directory:
        root = Path(directory)
        api = IsolatedAPI(root)
        try:
            api.start()
            verify(api, root, report)
            report["status"] = "passed"
        except Exception as error:
            report.update(status="failed", failure=f"{type(error).__name__}: {error}")
            raise
        finally:
            api.stop()
            report["finished_utc"] = datetime.now(UTC).isoformat()
            report["source_sha256_at_final_review"] = {
                name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
                for name in ("src/herbfold/design_pipeline.py", "src/herbfold/design_pipeline_api.py",
                             "src/herbfold/combination_assay.py", "scripts/verify_design_pipeline.py")
            }
            destination.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
            server_log = root / "server.log"
            if server_log.exists():
                destination.with_suffix(".server.log").write_text(server_log.read_text())
    print(json.dumps({"status": report["status"], "output": str(destination)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
