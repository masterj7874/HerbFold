"""Exercise the AF3 agent bridge over HTTP with an isolated synthetic Studio.

The fixture adapter never starts AF3, LLM or quantum runners. Its
saved jobs only represent lifecycle states; they contain no predicted structures
or biological measurements. No production store or credentials are read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
PREFIX = "/api/af3-workflows"


def synthetic_app(root: Path):
    """Import the actual bridge only inside the isolated loopback fixture server."""
    from fastapi import FastAPI

    from herbfold.af3_workflow_api import make_router
    from herbfold.molecular_selection import exact_identity
    from herbfold.storage import Store

    store = Store(root / "store")

    class SyntheticPredictions:
        def __init__(self):
            with store.connect() as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS synthetic_predictions (job_id TEXT PRIMARY KEY, request TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS studio_predictions (job_id TEXT PRIMARY KEY)")
                connection.execute("CREATE TABLE IF NOT EXISTS synthetic_calls (method TEXT, job_id TEXT)")
                connection.execute("CREATE TABLE IF NOT EXISTS synthetic_checkpoint_checks (job_id TEXT)")

        def _call(self, method, job_id=None):
            with store.connect() as connection:
                connection.execute("INSERT INTO synthetic_calls VALUES (?,?)", (method, job_id))

        def get(self, job_id, **kwargs):
            with store.connect() as connection:
                row = connection.execute("SELECT request FROM synthetic_predictions WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Not a synthetic Studio fixture")
            job = store.get(job_id)
            return {"job": job, "requested": json.loads(row[0]), "reused": False,
                    "retry_of": None, "readiness": {"runnable": True, "blockers": [],
                    "warnings": ["SYNTHETIC WORKFLOW FIXTURE; NOT AN AF3 PREDICTION"]},
                    "stage": {"name": job["status"], "label": "Synthetic lifecycle state"},
                    "queue_position": None, "orphan_process_active": False,
                    "scene_url": f"/SYNTHETIC_NO_STRUCTURE/{job_id}",
                    "output_validation": (job.get("result") or {}).get("output_validation")}

        def list(self, smiles, target_accession="P35354"):
            canonical = exact_identity(smiles)
            with store.connect() as connection:
                rows = connection.execute("SELECT job_id,request FROM synthetic_predictions ORDER BY rowid DESC").fetchall()
            return {"items": [self.get(row[0]) for row in rows
                              if json.loads(row[1])["canonical_smiles"] == canonical
                              and json.loads(row[1])["target_accession"] == target_accession]}

        def prepare(self, request):
            self._call("prepare")
            time.sleep(0.7)  # Acceptance must not wait for this synthetic preflight.
            requested = {**request.model_dump(), "canonical_smiles": exact_identity(request.smiles),
                         "target_sequence_sha256": hashlib.sha256(b"SYNTHETIC_ONLY").hexdigest()}
            job = store.create("alphafold", {"synthetic_fixture": True})
            with store.connect() as connection:
                connection.execute("INSERT INTO synthetic_predictions VALUES (?,?)", (job["id"], json.dumps(requested)))
                connection.execute("INSERT INTO studio_predictions VALUES (?)", (job["id"],))
            store.update(job["id"], "prepared", {"synthetic_fixture": True, "runnable": True})
            if requested["canonical_smiles"] == "CCl":
                raise TimeoutError("Synthetic lost prepare acknowledgement after Studio job persistence")
            return self.get(job["id"])

        def execute(self, job_id):
            with store.connect() as connection:
                linked = connection.execute("SELECT state FROM af3_workflows WHERE json_extract(state,'$.job_id')=?", (job_id,)).fetchone()
                assert linked is not None, "Execution happened before the workflow job checkpoint"
                assert json.loads(linked[0])["checkpoints"]["prediction_prepared"]["job_id"] == job_id
                connection.execute("INSERT INTO synthetic_checkpoint_checks VALUES (?)", (job_id,))
            self._call("execute", job_id)
            time.sleep(0.35)  # A dispatch acknowledgment may lag behind the accepted request.
            job = store.get(job_id)
            if job["status"] == "prepared":
                store.update(job_id, "running", {"synthetic_fixture": True,
                    "stage": {"name": "inference", "label": "Synthetic running state; no computation"}})
            return self.get(job_id)

    predictions = SyntheticPredictions()
    router = make_router(store, predictions)

    @asynccontextmanager
    async def lifespan(app):
        yield
        router.service.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)

    @app.get("/__fixture__/stats")
    def stats():
        with store.connect() as connection:
            return {"calls": {row[0]: row[1] for row in connection.execute(
                "SELECT method,count(*) FROM synthetic_calls GROUP BY method")},
                "checkpoint_verified_count": connection.execute("SELECT count(*) FROM synthetic_checkpoint_checks").fetchone()[0],
                "active_preparations": len(router.service.futures),
                "jobs": [dict(row) for row in connection.execute("SELECT id,status FROM jobs")]}

    @app.post("/__fixture__/complete/{job_id}")
    def complete(job_id: str):
        store.update(job_id, "completed", {"synthetic_fixture": True, "models": [],
                    "output_validation": {"status": "synthetic_lifecycle_only", "identity_verified": None,
                                          "quality_pass": None},
                    "warnings": ["No structure or prediction was generated"]})
        return predictions.get(job_id)

    @app.post("/__fixture__/validation-pending/{job_id}")
    def validation_pending(job_id: str):
        from herbfold.af3_execution import process_identity
        store.update(job_id, "completed", {"synthetic_fixture": True, "models": [],
            "stage": {"name": "output_validation", "started_at": datetime.now(UTC).isoformat()},
            "execution": {"owner": process_identity(os.getpid())}})
        return predictions.get(job_id)

    @app.post("/__fixture__/existing")
    def existing():
        from herbfold.af3_studio import StudioPredictionRequest
        value = predictions.prepare(StudioPredictionRequest(smiles="CCN", target_accession="P35354"))
        complete(value["job"]["id"])
        return predictions.get(value["job"]["id"])

    return app


class Server:
    def __init__(self, root):
        self.root, self.process, self.log = root, None, None
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("HERBFOLD_", "AF3_", "OPENAI_", "IBM_", "QISKIT_"))}
        env.update(PYTHON_DOTENV_DISABLED="1", PYTHONPATH=str(REPO / "src"))
        self.log = (self.root / "server.log").open("a")
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve",
            "--fixture-root", str(self.root), "--port", str(self.port)], cwd=REPO, env=env,
            stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Synthetic HTTP server exited; inspect verifier log")
            try:
                self.request("GET", "/__fixture__/stats")
                return
            except (URLError, TimeoutError):
                time.sleep(0.05)
        raise TimeoutError("Synthetic HTTP server not ready")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()

    def request(self, method, path, payload=None, expected=(200, 201, 202)):
        data = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        req = Request(self.url + path, data=data, method=method,
                      headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with urlopen(req, timeout=10) as response:
                status, body = response.status, response.read()
        except HTTPError as error:
            status, body = error.code, error.read()
        result = json.loads(body)
        assert status in expected, (method, path, status, result)
        return result

    def poll(self, path, predicate):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            value = self.request("GET", path, expected=(200, 404))
            if predicate(value):
                return value
            time.sleep(0.05)
        raise TimeoutError(f"Workflow state timeout: {value}")


def verify(server, report):
    def payload(smiles="OCC", *, execute=True):
        return {"request_id": str(uuid.uuid4()),
                "compound": {"id": "synthetic-" + uuid.uuid4().hex[:8],
                             "name": "Synthetic workflow fixture", "smiles": smiles, "category": "candidate"},
                "target_accession": "P35354", "msa_mode": "search", "seeds": [1],
                "exploratory_ack": False, "execute": execute}

    def calls():
        return server.request("GET", "/__fixture__/stats")["calls"]

    def read(request):
        return server.request("GET", f"{PREFIX}/by-request/{request['request_id']}")

    def wait_job(request):
        server.poll(f"{PREFIX}/by-request/{request['request_id']}", lambda value: bool(value.get("job_id")))
        server.poll("/__fixture__/stats", lambda value: value["active_preparations"] == 0)
        return read(request)

    first_request = payload()
    before = time.monotonic()
    first = server.request("POST", PREFIX, first_request)
    elapsed = time.monotonic() - before
    assert elapsed < 0.6, "Request acceptance waited for the synthetic 0.7s preflight"
    assert first["id"] == read(first_request)["id"], "Accepted request was not durable"
    repeated = server.request("POST", PREFIX, first_request)
    assert repeated["id"] == first["id"]
    first = wait_job(first_request)
    server.poll("/__fixture__/stats", lambda value: value["calls"].get("execute") == 1)
    assert calls() == {"prepare": 1, "execute": 1}
    report["durable_acceptance_seconds"] = round(elapsed, 4)
    report["duplicate_request_reuses_workflow_and_single_execution"] = True
    conflicting = {**first_request, "compound": {**first_request["compound"], "smiles": "CCC"}}
    server.request("POST", PREFIX, conflicting, expected=(409, 422))
    assert read(first_request)["job_id"] == first["job_id"]
    report["request_id_payload_conflict_rejected"] = True

    server.request("POST", f"/__fixture__/validation-pending/{first['job_id']}", {})
    validating = server.request("GET", f"{PREFIX}/{first['id']}")
    assert validating["status"] == "validating", "Process completion stopped polling before output validation"
    assert next(s for s in validating["stages"] if s["id"] == "validation")["status"] == "running"
    assert next(s for s in validating["stages"] if s["id"] == "results")["status"] != "completed"
    report["process_completion_keeps_identity_validation_active"] = True
    server.request("POST", f"/__fixture__/complete/{first['job_id']}", {})
    completed = server.poll(f"{PREFIX}/{first['id']}", lambda value: value["status"] == "completed")
    baseline = calls()
    for _ in range(3):
        assert server.request("GET", f"{PREFIX}/{first['id']}") == completed, "Unchanged GET fabricated fresh stages, events or timestamps"
    assert calls() == baseline
    report["completed_result_reloads_without_execution"] = True
    assert next(s for s in completed["stages"] if s["id"] == "results")["status"] != "completed"
    report["synthetic_unverified_completion_not_claimed_as_available_structure"] = True

    # Send all request bytes then close without reading an acknowledgement.
    # The request_id provides a recovery path even if the browser loses its POST response.
    disconnected_request = payload("CCCC", execute=False)
    body = json.dumps(disconnected_request).encode()
    packet = (f"POST {PREFIX} HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n"
              f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
              "Connection: close\r\n\r\n").encode() + body
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as connection:
        connection.sendall(packet)
    disconnected = wait_job(disconnected_request)
    assert disconnected["job_id"] != first["job_id"]
    assert calls() == {"prepare": 2, "execute": 1}
    assert server.request("POST", PREFIX, disconnected_request)["id"] == disconnected["id"]
    report["lost_post_ack_restores_by_request_id_without_duplicate"] = True

    # Kill the fixture while a new preflight is pending, before any job is saved.
    interrupted_request = payload("CCF")
    interrupted = server.request("POST", PREFIX, interrupted_request)
    prepare_only_request = payload("CCCl", execute=False)
    prepare_only = server.request("POST", PREFIX, prepare_only_request)
    server.poll("/__fixture__/stats", lambda value: value["calls"].get("prepare") == 4)
    server.process.kill()
    server.process.wait(timeout=5)
    server.stop()
    server.start()
    recovered = read(interrupted_request)
    assert recovered["id"] == interrupted["id"]
    assert not recovered.get("job_id")
    assert calls() == {"prepare": 4, "execute": 1}
    assert server.request("GET", f"{PREFIX}/{completed['id']}")["job_id"] == first["job_id"]
    report["restart_preserves_completed_and_unresolved_requests_without_automatic_execution"] = True
    for invalid in (False, 1, 1.0, "true"):
        server.request("POST", f"{PREFIX}/{interrupted['id']}/resume", {"confirm": invalid}, expected=(422,))
    assert calls() == {"prepare": 4, "execute": 1}
    server.request("POST", f"{PREFIX}/{interrupted['id']}/resume", {"confirm": True})
    resumed = wait_job(interrupted_request)
    server.poll("/__fixture__/stats", lambda value: value["calls"].get("execute") == 2)
    assert resumed["job_id"] != first["job_id"]
    assert calls() == {"prepare": 5, "execute": 2}
    report["explicit_resume_only_and_strict_boolean_confirmation"] = True
    server.request("POST", f"{PREFIX}/{prepare_only['id']}/resume", {"confirm": True})
    prepared_only = wait_job(prepare_only_request)
    assert prepared_only["status"] == "prepared", f"Prepare-only resume became {prepared_only['status']}"
    assert prepared_only["request"]["execute"] is False
    assert calls() == {"prepare": 6, "execute": 2}, "Resuming interrupted preparation silently authorized inference"
    report["interrupted_prepare_only_resume_does_not_submit_inference"] = True

    existing = server.request("POST", "/__fixture__/existing", {})
    existing_id = existing["job"]["id"]
    assert existing_id in {row["job"]["id"] for row in server.request("GET", f"{PREFIX}/jobs")["items"]}
    baseline = calls()
    attached = server.request("POST", f"{PREFIX}/attach", {"job_id": existing_id,
        "compound": {"id": "synthetic-existing", "name": "Synthetic existing", "smiles": "NCC"},
        "target_accession": "P35354"})
    assert attached["job_id"] == existing_id
    assert server.request("POST", f"{PREFIX}/attach", {"job_id": existing_id})["id"] == attached["id"]
    server.request("POST", f"{PREFIX}/attach", {"job_id": existing_id,
        "compound": {"id": "wrong", "name": "Wrong graph", "smiles": "CCO"}}, expected=(409, 422))
    server.request("POST", f"{PREFIX}/attach", {"job_id": existing_id,
        "target_accession": "P00533"}, expected=(409, 422))
    assert calls() == baseline
    report["existing_job_attach_deduplicates_and_rejects_wrong_identity_without_execution"] = True
    listing = server.request("GET", PREFIX)
    assert {first["id"], disconnected["id"], interrupted["id"], prepare_only["id"], attached["id"]}.issubset(
        {row["id"] for row in listing["items"]})
    report["history_contains_all_workflows"] = True
    lost_prepare_request = payload("CCl")
    baseline = calls()
    lost_prepare = server.request("POST", PREFIX, lost_prepare_request)
    recovered_prepare = wait_job(lost_prepare_request)
    assert recovered_prepare["id"] == lost_prepare["id"]
    assert recovered_prepare["status"] == "prepared"
    assert calls() == {"prepare": baseline["prepare"] + 1, "execute": baseline["execute"]}
    assert server.request("POST", PREFIX, lost_prepare_request)["job_id"] == recovered_prepare["job_id"]
    report["lost_prepare_ack_discovers_exact_saved_job_without_automatic_execution"] = True
    baseline = calls()
    dispatching = server.request("POST", f"{PREFIX}/{disconnected['id']}/resume", {"confirm": True})
    assert dispatching["status"] in {"preparing", "queued", "queued_for_execution", "running"}, (
        "Explicit execute acknowledgment looked terminal while submission was pending", dispatching["status"])
    immediate = read(disconnected_request)
    assert immediate["status"] in {"preparing", "queued", "queued_for_execution", "running"}
    dispatched = wait_job(disconnected_request)
    assert dispatched["status"] == "running"
    assert calls() == {"prepare": baseline["prepare"], "execute": baseline["execute"] + 1}
    report["explicit_execute_active_before_delayed_queue_acknowledgment"] = True
    stats = server.request("GET", "/__fixture__/stats")
    assert stats["checkpoint_verified_count"] == stats["calls"]["execute"]
    report["job_checkpoint_persisted_before_every_submission"] = True
    report["synthetic_adapter_calls"] = calls()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fixture-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--output", default="tmp/af3-workflow-verification.json")
    args = parser.parse_args()
    if args.serve:
        import uvicorn
        uvicorn.run(synthetic_app(args.fixture_root), host="127.0.0.1", port=args.port, log_level="warning")
        return
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {"started_utc": datetime.now(UTC).isoformat(), "status": "running",
              "scope": "isolated real HTTP; synthetic Studio lifecycle only",
              "new_af3_llm_qpu_executions": 0, "production_runtime_modified": False}
    with tempfile.TemporaryDirectory(prefix="herbfold-af3-workflow-") as directory:
        root = Path(directory)
        server = Server(root)
        try:
            server.start()
            verify(server, report)
            report["status"] = "passed"
        except Exception as error:
            report.update(status="failed", failure=f"{type(error).__name__}: {error}")
            raise
        finally:
            server.stop()
            report["finished_utc"] = datetime.now(UTC).isoformat()
            report["source_sha256"] = {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
                for name in ["src/herbfold/af3_workflow.py", "src/herbfold/af3_workflow_api.py", "src/herbfold/af3_execution.py",
                             "scripts/verify_af3_workflow.py"] if (REPO / name).exists()}
            destination.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
            if (root / "server.log").exists():
                destination.with_suffix(".server.log").write_text((root / "server.log").read_text())
    print(json.dumps({"status": report["status"], "output": str(destination)}))


if __name__ == "__main__":
    main()
