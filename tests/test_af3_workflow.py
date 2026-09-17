"""Durability fixtures only: no AF3 runner, GPU or real queue is invoked."""

import json
import threading
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from herbfold import molecular_selection
from herbfold.af3_workflow import AF3WorkflowAttach, AF3WorkflowRequest, AF3Workflows
from herbfold.af3_workflow_api import make_router
from herbfold.storage import Store, utcnow


def request(**changes):
    return AF3WorkflowRequest.model_validate({"request_id": str(uuid.uuid4()),
        "compound": {"id": "fixture", "name": "Synthetic workflow fixture", "smiles": "CCO", "category": "candidate"},
        "target_accession": "P35354", "msa_mode": "search", "seeds": [1], **changes})


class FakePredictions:
    """Store-backed adapter; execute changes JSON state without starting a process."""

    def __init__(self, store):
        self.store = store
        self.prepare_calls = self.execute_calls = 0
        self.next_status = "prepared"
        self.prepare_gate = None
        self.execute_gate = None
        self.prepare_error = None
        self.lost_prepare_ack = self.lost_execute_ack = False
        with store.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS studio_predictions(job_id TEXT PRIMARY KEY, request TEXT)")

    def prepare(self, value):
        self.prepare_calls += 1
        if self.prepare_gate:
            assert self.prepare_gate.wait(5)
        if self.prepare_error:
            raise self.prepare_error
        selected = {"canonical_smiles": molecular_selection.exact_identity(value.smiles), "smiles": value.smiles,
                    "target_accession": value.target_accession, "msa_mode": value.msa_mode, "seeds": value.seeds,
                    "exploratory_ack": value.exploratory_ack}
        matches = self.list(value.smiles, value.target_accession)["items"]
        matching = [row for row in matches if row["requested"] == selected]
        if matching and (not value.retry or matching[0]["job"]["status"] in {"prepared", "queued", "running", "completed"}):
            return matching[0]
        job = self.store.create("alphafold", {"name": "SYNTHETIC ONLY", "sequences": []})
        with self.store.connect() as con:
            con.execute("INSERT INTO studio_predictions VALUES (?,?)", (job["id"], json.dumps(selected)))
        blocked = self.next_status == "blocked"
        result = {"runnable": not blocked, "blockers": ["SYNTHETIC_NO_GPU"] if blocked else []}
        self.store.update(job["id"], self.next_status, result, "SYNTHETIC_NO_GPU" if blocked else None)
        if self.lost_prepare_ack:
            raise TimeoutError("Synthetic lost preparation acknowledgement")
        return self.get(job["id"])

    def get(self, job_id):
        with self.store.connect() as con:
            row = con.execute("SELECT request FROM studio_predictions WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        job = self.store.get(job_id)
        result = job["result"] or {}
        return {"job": job, "requested": json.loads(row[0]),
                "readiness": {"runnable": result.get("runnable", False), "blockers": result.get("blockers", []), "warnings": []},
                "orphan_process_active": result.get("orphan_process_active", False),
                "msa_features": result.get("msa_features"), "output_validation": result.get("output_validation"),
                "stage": result.get("stage"), "scene_url": f"/api/molecular/predictions/{job_id}/scene"}

    def list(self, smiles, target_accession):
        with self.store.connect() as con:
            rows = con.execute("SELECT job_id FROM studio_predictions ORDER BY rowid DESC").fetchall()
        return {"items": [self.get(row[0]) for row in rows
                          if self.get(row[0])["requested"]["canonical_smiles"] == molecular_selection.exact_identity(smiles)
                          and self.get(row[0])["requested"]["target_accession"] == target_accession]}

    def execute(self, job_id):
        self.execute_calls += 1
        with self.store.connect() as con:
            state = json.loads(con.execute("SELECT state FROM af3_workflows WHERE json_extract(state,'$.job_id')=?", (job_id,)).fetchone()[0])
        assert state["checkpoints"]["prediction_prepared"]["job_id"] == job_id
        assert state["checkpoints"]["submission_started"]["job_id"] == job_id
        if self.execute_gate:
            assert self.execute_gate.wait(5)
        job = self.store.get(job_id)
        self.store.update(job_id, "queued", job["result"])
        if self.lost_execute_ack:
            raise TimeoutError("Synthetic lost queue acknowledgement")
        return self.get(job_id)


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path)
    instance = AF3Workflows(store, FakePredictions(store))
    yield instance
    instance.close()
    instance.pool.shutdown(wait=True)


def wait_finished(service, workflow_id):
    for _ in range(200):
        with service.lock:
            if workflow_id not in service.futures:
                return service.get(workflow_id)
        time.sleep(.01)
    pytest.fail("Synthetic worker did not finish")


def test_immediate_receipt_dedupe_and_checkpoint_before_queue(service):
    service.predictions.prepare_gate = threading.Event()
    body = request()
    before = time.monotonic()
    first = service.create(body)
    assert time.monotonic() - before < 1
    assert first["job_id"] is None
    assert service.by_request(body.request_id)["id"] == first["id"]
    assert service.create(body)["id"] == first["id"]
    service.predictions.prepare_gate.set()
    final = wait_finished(service, first["id"])
    assert final["status"] == "queued_for_execution"
    assert service.predictions.prepare_calls == service.predictions.execute_calls == 1
    assert final["checkpoints"]["submission_acknowledged"]["job_id"] == final["job_id"]
    assert service.resume(final["id"])["id"] == final["id"]
    assert service.predictions.execute_calls == 1
    changed = body.model_copy(update={"execute": False})
    with pytest.raises(ValueError, match="different payload"):
        service.create(changed)


def test_blocked_preflight_and_explicit_retry_keep_old_job(service):
    service.predictions.next_status = "blocked"
    first = service.create(request(), background=False)
    assert first["status"] == "blocked" and "SYNTHETIC_NO_GPU" in first["blockers"]
    assert service.predictions.execute_calls == 0
    assert first["actions"]["resume_kind"] == "retry"
    service.predictions.next_status = "prepared"
    final = service.resume(first["id"], background=False)
    assert final["job_id"] != first["job_id"] and first["job_id"] in final["job_history"]
    assert final["status"] == "queued_for_execution"


def test_prepare_error_is_durable_blocked_not_unhandled(service):
    service.predictions.prepare_error = RuntimeError("SYNTHETIC unavailable runtime")
    state = service.create(request(), background=False)
    assert state["status"] == "blocked" and "unavailable runtime" in state["error"]
    assert state["job_id"] is None and state["actions"]["resume_kind"] == "prepare"
    assert service.predictions.execute_calls == 0


def test_lost_preparation_ack_recovers_unique_checkpoint_job_without_execute(service):
    service.predictions.lost_prepare_ack = True
    state = service.create(request(), background=False)
    assert state["job_id"] and state["status"] == "prepared"
    assert service.predictions.execute_calls == 0
    assert state["actions"]["resume_kind"] == "execute"
    assert service.resume(state["id"], background=False)["status"] == "queued_for_execution"
    assert service.predictions.prepare_calls == service.predictions.execute_calls == 1


def test_lost_queue_ack_reconnects_without_second_submission(service):
    service.predictions.lost_execute_ack = True
    state = service.create(request(), background=False)
    assert state["status"] == "queued_for_execution"
    assert "submission_acknowledged" not in state["checkpoints"]
    service.resume(state["id"], background=False)
    assert service.predictions.execute_calls == 1


def test_completed_attach_and_stable_get_never_run_work(service):
    original = service.create(request(execute=False), background=False)
    job_id = original["job_id"]
    service.store.update(job_id, "completed", {"execution_verified": True,
        "output_validation": {"identity_verified": True, "status": "identity_verified_quality_unassessed"},
        "stage": {"name": "completed"}})
    final = service.get(original["id"])
    count = service.predictions.prepare_calls
    assert final["status"] == "completed"
    for _ in range(5):
        assert service.get(final["id"]) == final
    assert service.attach(AF3WorkflowAttach(job_id=job_id))["id"] == final["id"]
    assert service.resume(final["id"], background=False) == final
    assert service.predictions.prepare_calls == count and service.predictions.execute_calls == 0
    for kwargs in ({"compound": {"smiles": "CCN"}}, {"target_accession": "P00533"}):
        with pytest.raises(ValueError):
            service.attach(AF3WorkflowAttach(job_id=job_id, **kwargs))


def test_completion_before_identity_callback_keeps_validation_active(service, monkeypatch):
    from herbfold import af3_workflow

    state = service.create(request(execute=False), background=False)
    monkeypatch.setattr(af3_workflow, "process_is_alive", lambda _: True)
    pending = {"execution_verified": True, "execution": {"owner": {"synthetic": True}},
               "stage": {"name": "output_validation", "started_at": utcnow()}}
    service.store.update(state["job_id"], "completed", pending)
    current = service.get(state["id"])
    assert current["status"] == "validating"
    assert next(s for s in current["stages"] if s["id"] == "validation")["status"] == "running"
    pending.update(output_validation={"identity_verified": True, "status": "identity_verified_quality_unassessed"}, stage={"name": "completed"})
    service.store.update(state["job_id"], "completed", pending)
    assert service.get(state["id"])["status"] == "completed"
    service.store.update(state["job_id"], "completed", {"stage": {"name": "output_validation", "started_at": "2000-01-01T00:00:00+00:00"}})
    legacy = service.get(state["id"])
    assert legacy["status"] == "completed"
    assert next(s for s in legacy["stages"] if s["id"] == "results")["status"] == "unknown"


def test_submission_and_retry_remain_active_during_slow_adapter_calls(service):
    service.predictions.execute_gate = threading.Event()
    state = service.create(request())
    for _ in range(100):
        if service.get(state["id"])["checkpoints"].get("submission_started"):
            break
        time.sleep(.01)
    assert service.get(state["id"])["status"] == "preparing"
    service.predictions.execute_gate.set()
    state = wait_finished(service, state["id"])
    service.store.update(state["job_id"], "failed", {}, "SYNTHETIC failed")
    service.predictions.prepare_gate = threading.Event()
    service.resume(state["id"])
    for _ in range(100):
        if service.get(state["id"])["checkpoints"].get("prepare_in_progress"):
            break
        time.sleep(.01)
    assert service.get(state["id"])["status"] == "preparing"
    service.predictions.prepare_gate.set()
    wait_finished(service, state["id"])


def test_resume_receipt_is_active_before_worker_dispatch(service, monkeypatch):
    state = service.create(request(execute=False), background=False)
    scheduled = []
    monkeypatch.setattr(service, "_schedule", lambda workflow_id, kind, **kwargs: scheduled.append((workflow_id, kind, kwargs["execute"])))
    resumed = service.resume(state["id"])
    assert resumed["status"] == "preparing"
    assert service.get(state["id"])["status"] == "preparing"
    assert resumed["actions"]["can_resume"] is False
    assert resumed["checkpoints"]["execution_confirmation"]["job_id"] == state["job_id"]
    assert scheduled == [(state["id"], "execute", True)]
    assert service.predictions.execute_calls == 0
    service._work(*scheduled[0])
    assert service.get(state["id"])["status"] == "queued_for_execution"


@pytest.mark.parametrize("has_old_job", [False, True])
def test_prepare_only_resume_preserves_intent_and_does_not_project_old_job(service, monkeypatch, has_old_job):
    if has_old_job:
        service.predictions.next_status = "blocked"
    else:
        service.predictions.prepare_error = TimeoutError("Synthetic prepare interruption")
    state = service.create(request(execute=False), background=False)
    service.predictions.next_status = "prepared"
    service.predictions.prepare_error = None
    scheduled = []
    monkeypatch.setattr(service, "_schedule", lambda workflow_id, kind, **kwargs: scheduled.append((workflow_id, kind, kwargs["execute"])))
    resumed = service.resume(state["id"])
    assert resumed["status"] == "preparing"
    assert service.get(state["id"])["status"] == "preparing"
    assert scheduled[0][1:] == ("retry" if has_old_job else "prepare", False)
    service._work(*scheduled[0])
    final = service.get(state["id"])
    assert final["status"] == "prepared"
    assert final["request"]["execute"] is False and service.predictions.execute_calls == 0


def test_recovery_does_not_prepare_or_execute_checkpointed_job(tmp_path):
    store = Store(tmp_path)
    predictions = FakePredictions(store)
    first = AF3Workflows(store, predictions)
    state = first.create(request(execute=False), background=False)
    first.close()
    second = AF3Workflows(store, predictions)
    try:
        assert second.get(state["id"])["status"] == "prepared"
        assert predictions.prepare_calls == 1 and predictions.execute_calls == 0
        second.resume(state["id"], background=False)
        assert predictions.execute_calls == 1
        first.close()
        assert second.get(state["id"])["status"] == "queued_for_execution"
        with pytest.raises(RuntimeError, match="already active"):
            AF3Workflows(store, predictions)
    finally:
        second.close()


def test_completed_record_is_unchanged_by_restart(tmp_path):
    store = Store(tmp_path)
    predictions = FakePredictions(store)
    first = AF3Workflows(store, predictions)
    state = first.create(request(execute=False), background=False)
    store.update(state["job_id"], "completed", {"execution_verified": True,
        "output_validation": {"identity_verified": True, "status": "identity_verified_quality_unassessed"},
        "stage": {"name": "completed"}})
    final = first.get(state["id"])
    first.close()
    second = AF3Workflows(store, predictions)
    try:
        assert second.get(state["id"]) == final
        assert predictions.prepare_calls == 1 and predictions.execute_calls == 0
    finally:
        second.close()


def test_close_retains_ownership_until_slow_prepare_checkpoint_and_never_submits(tmp_path):
    store = Store(tmp_path)
    predictions = FakePredictions(store)
    predictions.prepare_gate = threading.Event()
    first = AF3Workflows(store, predictions)
    state = first.create(request())
    for _ in range(100):
        if predictions.prepare_calls:
            break
        time.sleep(.01)
    assert predictions.prepare_calls == 1
    first.close()
    try:
        with pytest.raises(RuntimeError, match="already active"):
            AF3Workflows(store, predictions)
    finally:
        predictions.prepare_gate.set()
        first.pool.shutdown(wait=True)
    second = AF3Workflows(store, predictions)
    try:
        assert second.get(state["id"])["status"] == "prepared"
        assert predictions.prepare_calls == 1 and predictions.execute_calls == 0
        first.close()
        with pytest.raises(RuntimeError, match="already active"):
            AF3Workflows(store, predictions)
    finally:
        second.close()


def test_event_tail_sequence_remains_strictly_increasing():
    state = {"events": []}
    for _ in range(450):
        AF3Workflows._event(state, "input", "running", "synthetic")
    assert len(state["events"]) == 200
    assert [e["seq"] for e in state["events"]] == list(range(251, 451))


@pytest.mark.parametrize("changes", [{"seeds": [True]}, {"seeds": [-1]}, {"seeds": [2**32]},
    {"seeds": [1, 1]}, {"exploratory_ack": 1}, {"execute": "true"}, {"msa_mode": "none"},
    {"target_accession": "invalid"}, {"request_id": "not-a-uuid"},
    {"compound": {"id": "x", "name": "fixture", "smiles": "invalid"}}])
def test_invalid_input_rejected(changes):
    with pytest.raises((ValidationError, ValueError)):
        request(**changes)


def test_api_strict_resume_and_request_lookup(tmp_path):
    store = Store(tmp_path)
    router = make_router(store, FakePredictions(store))
    app = FastAPI()
    app.include_router(router)
    try:
        with TestClient(app) as client:
            body = request(execute=False).model_dump(mode="json")
            response = client.post("/api/af3-workflows", json=body)
            assert response.status_code == 202
            workflow_id = response.json()["id"]
            state = wait_finished(router.service, workflow_id)
            assert client.get("/api/af3-workflows/by-request/" + body["request_id"]).json()["id"] == workflow_id
            for bad in [False, 1, 1.0, "true"]:
                assert client.post(f"/api/af3-workflows/{workflow_id}/resume", json={"confirm": bad}).status_code == 422
            assert client.get("/api/af3-workflows/jobs").json()["items"][0]["job"]["id"] == state["job_id"]
    finally:
        router.service.close()
