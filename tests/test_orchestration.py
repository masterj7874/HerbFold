import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from herbfold import alphafold, connectors, quantum
from herbfold.llm import MODEL, AstraProvider, LLMError
from herbfold.orchestration import AnalysisRequest, Budgets, Orchestrator
from herbfold.orchestration_api import make_router
from herbfold.storage import Store


class FakeAstra:
    """Test double only; production never substitutes this for the exact model."""

    def __init__(self, *, available=True, fail_role=None, barrier=None):
        self.calls = []
        self.available = available
        self.fail_role = fail_role
        self.barrier = barrier

    def status(self, **kwargs):
        return {"model": MODEL, "available": self.available, "message": "Test model unavailable"}

    def decide(self, role, instructions, context, **kwargs):
        self.calls.append(role)
        if self.barrier and role in {"evidence", "chemistry"}:
            self.barrier.wait(timeout=5)
        if role == self.fail_role:
            raise LLMError("Test API failure", code="test_failure")
        selected = context.get("allowed_ids", [])
        if role == "coordinator":
            selected = [cid for cid in selected if cid in {"quercetin", "aspirin"}]
        if role == "candidate_design":
            selected = list(reversed(selected))[:2]
        return {
            "model": MODEL,
            "response_id": "test-response-" + role,
            "usage": {"input_tokens": 11, "output_tokens": 7},
            "decision": {
                "summary": "Test structured decision for " + role,
                "decision": "continue",
                "selected_ids": selected,
                "evidence_ids": context.get("evidence_ids", []),
                "requested_tools": context.get("allowed_tools", []),
                "candidate_policy": "low_alerts",
                "risks": [],
                "next_actions": [],
            },
        }


@contextmanager
def engine(tmp_path, provider=None):
    with ThreadPoolExecutor(max_workers=2) as pool:
        yield Orchestrator(Store(tmp_path), pool, provider=provider or FakeAstra())


def request(**kwargs):
    return AnalysisRequest(**{"compound_ids": ["quercetin", "aspirin"], "max_candidates": 3, **kwargs})


def test_local_run_is_offline_and_reproducible(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Explicit local mode attempted a network request")

    monkeypatch.setattr(connectors, "pubchem_lookup", no_network)
    provider = FakeAstra(available=False)
    with engine(tmp_path, provider) as worker:
        run = worker.create(request(mode="local"), start=False)
        first = worker.run(run["id"])
        second = worker.run(run["id"])
    assert first["status"] == "completed"
    assert first == second
    assert first["llm_used"] is False and first["model"] is None
    assert first["usage"]["llm_calls"] == 0
    assert provider.calls == []
    assert len(first["result"]["candidates"]) == 3
    assert first["result"]["quantum"]["hardware_executed"] is False
    assert first["result"]["quantum"]["metadata"]["n_qubits"] == 4
    assert first["result"]["affinity"] is None
    assert all(stage["artifact"]["sha256"] for stage in first["stages"])


def test_astra_roles_parallel_and_decisions_control_parent_and_shortlist(tmp_path):
    provider = FakeAstra(barrier=threading.Barrier(2))
    with engine(tmp_path, provider) as worker:
        req = AnalysisRequest(
            compound_ids=["quercetin", "baicalein", "aspirin"],
            max_candidates=4,
            budgets=Budgets(max_source_requests=0),
        )
        run = worker.create(req, start=False)
        result = worker.run(run["id"])
    assert result["status"] == "completed"
    assert result["llm_used"] is True
    assert result["usage"] == {"llm_calls": 6, "input_tokens": 66, "output_tokens": 42, "source_requests": 0}
    assert set(provider.calls) == {
        "coordinator",
        "evidence",
        "chemistry",
        "candidate_design",
        "review",
        "report",
    }
    assert len(result["result"]["candidates"]) == 2
    assert all(row["herbal_parent_id"] == "quercetin" for row in result["result"]["candidates"])
    stage = worker._stage(result, "candidate_design")
    assert [row["id"] for row in result["result"]["candidates"]] == stage["agent"]["decision"]["selected_ids"]
    assert result["result"]["quantum"]["sample_ids"][:2] == stage["agent"]["decision"]["selected_ids"]


def test_missing_exact_model_blocks_without_local_fallback(tmp_path):
    with engine(tmp_path, FakeAstra(available=False)) as worker:
        run = worker.create(request(), start=False)
        result = worker.run(run["id"])
    assert result["status"] == "blocked"
    assert result["model"] == MODEL and not result["llm_used"]
    assert result["result"]["candidates"] == []
    assert result["usage"]["llm_calls"] == 0


def test_call_budget_enforced_with_parallel_roles(tmp_path):
    with engine(tmp_path) as worker:
        run = worker.create(request(budgets=Budgets(max_llm_calls=2, max_source_requests=0)), start=False)
        result = worker.run(run["id"])
        with pytest.raises(ValueError, match="budget exhausted"):
            worker.resume(run["id"])
    assert result["status"] == "blocked"
    assert result["usage"]["llm_calls"] == 2
    assert not result["result"]["af3_jobs"]


def test_resume_reuses_completed_model_checkpoints(tmp_path):
    provider = FakeAstra(fail_role="candidate_design")
    with engine(tmp_path, provider) as worker:
        run = worker.create(request(budgets=Budgets(max_source_requests=0)), start=False)
        failed = worker.run(run["id"])
        assert failed["status"] == "blocked"
        provider.fail_role = None
        worker.resume(run["id"])
        worker._futures[run["id"]].result(timeout=10)
        result = worker.get(run["id"])
    assert result["status"] == "completed"
    assert provider.calls.count("coordinator") == 1
    assert provider.calls.count("evidence") == 1
    assert provider.calls.count("chemistry") == 1
    assert provider.calls.count("candidate_design") == 2
    assert result["usage"]["llm_calls"] == 7


def test_cancel_before_start_and_resume(tmp_path):
    with engine(tmp_path) as worker:
        run = worker.create(request(mode="local", quantum_mode="off"), start=False)
        worker.cancel(run["id"])
        cancelled = worker.run(run["id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["usage"]["llm_calls"] == 0
        worker.resume(run["id"])
        worker._futures[run["id"]].result(timeout=10)
        assert worker.get(run["id"])["status"] == "completed"


def test_startup_recovery_retains_checkpoint(tmp_path):
    with engine(tmp_path) as worker:
        run = worker.create(request(mode="local"), start=False)
        worker._mutate(run["id"], lambda state: state.update(status="running"))
        worker._execute_stage(run["id"], "coordinator", worker._coordinator)
    with engine(tmp_path) as restarted:
        state = restarted.get(run["id"])
        assert state["status"] == "interrupted"
        assert restarted._stage(state, "coordinator")["status"] == "completed"
        restarted.resume(run["id"])
        restarted._futures[run["id"]].result(timeout=10)
        assert restarted.get(run["id"])["status"] == "completed"


def test_ibm_uncertain_submission_never_resubmits(tmp_path, monkeypatch):
    submissions = []
    monkeypatch.setattr(quantum, "plan_quantum", lambda *a, **kw: {"status": "ready", "n_qubits": 156})

    def submit(*a, **kw):
        submissions.append(kw)
        raise RuntimeError("Accepted remotely but connection lost before manifest")

    monkeypatch.setattr(quantum, "submit_kernel", submit)
    with engine(tmp_path) as worker:
        run = worker.create(request(mode="local", quantum_mode="ibm"), start=False)
        first = worker.run(run["id"])
        assert first["status"] == "failed"
        second = worker.run(run["id"])
    assert second["status"] == "blocked"
    assert "no duplicate submission" in second["error"]
    assert len(submissions) == 1
    assert submissions[0]["qubits"] == "max"
    assert submissions[0]["max_execution_time"] <= 30
    assert submissions[0]["max_total_shots"] <= 4096


def test_recorded_native_execution_is_not_repeated(tmp_path, monkeypatch):
    def forbid(*a, **kw):
        pytest.fail("Interrupted AF3 native execution was repeated")

    monkeypatch.setattr("herbfold.orchestration.subprocess.Popen", forbid)
    with engine(tmp_path) as worker:
        run = worker.create(request(mode="local"), start=False)
        job = worker.store.create("alphafold", {})
        worker.store.update(job["id"], "running", {"runnable": True})
        recovered = worker._run_af3(run["id"], worker.store.get(job["id"]))
    assert recovered["status"] == "interrupted"


def test_stale_af3_readiness_cannot_bypass_current_parameter_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        alphafold,
        "prepare_job",
        lambda *a, **kw: {
            "runnable": False,
            "blockers": ["Current parameters are random performance-test weights"],
        },
    )
    monkeypatch.setattr(
        "herbfold.orchestration.subprocess.Popen",
        lambda *a, **kw: pytest.fail("Stale readiness must not launch AF3"),
    )
    with engine(tmp_path) as worker:
        run = worker.create(request(mode="local"), start=False)
        job = worker.store.create("alphafold", {})
        worker.store.update(job["id"], "prepared", {"runnable": True})
        result = worker._run_af3(run["id"], worker.store.get(job["id"]))
    assert result["status"] == "blocked"
    assert "random performance-test" in result["error"]


def test_prepare_af3_does_not_execute_and_respects_limit(tmp_path, monkeypatch):
    prepared = []

    def prepare(directory, data):
        prepared.append(data)
        return {"runnable": False, "blockers": ["Test environment"], "msa_mode": "search"}

    monkeypatch.setattr(alphafold, "prepare_job", prepare)
    with engine(tmp_path) as worker:
        run = worker.create(
            request(mode="local", protein_sequence="ACDEFGHIKLMNPQRSTVWY", quantum_mode="off"), start=False
        )
        result = worker.run(run["id"])
    assert result["status"] == "completed"
    assert len(prepared) == 1
    assert len(result["result"]["af3_jobs"]) == 1
    assert result["result"]["af3_jobs"][0]["status"] == "prepared"
    assert not result["result"]["af3_jobs"][0]["execution_requested"]


def test_live_identity_conflict_blocks_before_candidate_design(tmp_path, monkeypatch):
    monkeypatch.setattr(
        connectors,
        "pubchem_lookup",
        lambda cid: {"smiles": "CCO", "source": "https://pubchem.ncbi.nlm.nih.gov/compound/702"},
    )
    with engine(tmp_path) as worker:
        run = worker.create(request(), start=False)
        result = worker.run(run["id"])
    assert result["status"] == "blocked"
    assert "identity differs" in result["error"]
    assert result["result"]["candidates"] == []


def test_provider_http_contract_and_no_secret_in_output(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-server-secret")
    bodies = []

    def respond(req):
        if req.method == "GET":
            return httpx.Response(200, json={"id": MODEL})
        bodies.append(json.loads(req.content))
        decision = {
            "summary": "Verified",
            "decision": "continue",
            "selected_ids": [],
            "evidence_ids": [],
            "requested_tools": [],
            "candidate_policy": "balanced",
            "risks": [],
            "next_actions": [],
        }
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "model": MODEL,
                "status": "completed",
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": json.dumps(decision)}]}
                ],
            },
        )

    provider = AstraProvider(transport=httpx.MockTransport(respond))
    status = provider.status()
    result = provider.decide(
        "coordinator",
        "test",
        {"allowed_ids": [], "allowed_tools": [], "evidence_ids": []},
        max_output_tokens=256,
        request_id="test",
    )
    assert status["available"] and result["model"] == MODEL
    assert bodies[0]["model"] == MODEL
    assert bodies[0]["max_output_tokens"] == 256
    assert bodies[0]["text"]["format"]["strict"] is True
    assert bodies[0]["store"] is False
    assert "unit-test-server-secret" not in json.dumps([status, result, bodies])


def test_provider_rejects_unknown_tool_and_unexpected_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    for model, tool, error_code in [
        ("other-model", [], "model_mismatch"),
        (MODEL, ["execute_shell"], "invalid_decision"),
    ]:
        decision = {
            "summary": "test",
            "decision": "continue",
            "selected_ids": [],
            "evidence_ids": [],
            "requested_tools": tool,
            "candidate_policy": "balanced",
            "risks": [],
            "next_actions": [],
        }

        def respond(req):
            return httpx.Response(
                200,
                json={
                    "id": "resp_test",
                    "model": model,
                    "status": "completed",
                    "usage": {},
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": json.dumps(decision)}],
                        }
                    ],
                },
            )

        provider = AstraProvider(transport=httpx.MockTransport(respond))
        with pytest.raises(LLMError) as caught:
            provider.decide(
                "coordinator",
                "test",
                {"allowed_ids": [], "allowed_tools": [], "evidence_ids": []},
                max_output_tokens=256,
                request_id="test",
            )
        assert caught.value.code == error_code


def test_router_poll_cancel_and_invalid_request(tmp_path):
    app = FastAPI()
    with ThreadPoolExecutor(max_workers=2) as pool:
        router = make_router(Store(tmp_path), pool, provider=FakeAstra(available=False))
        app.include_router(router)
        with TestClient(app) as client:
            missing = client.get("/api/analyses/does-not-exist")
            assert missing.status_code == 404
            invalid = client.post(
                "/api/analyses", json={"compound_ids": ["quercetin", "aspirin"], "mode": "made_up"}
            )
            assert invalid.status_code == 422
            created = client.post("/api/analyses", json={"compound_ids": ["quercetin", "aspirin"]})
            assert created.status_code == 202
            run_id = created.json()["id"]
            router.orchestrator._futures[run_id].result(timeout=10)
            assert client.get(f"/api/analyses/{run_id}").json()["status"] == "blocked"
            events = client.get(f"/api/analyses/{run_id}/events?after=1").json()
            assert all(event["seq"] > 1 for event in events["events"])
            assert client.get("/api/analyses").json()["analyses"][0]["id"] == run_id
            assert client.get("/api/llm/status").json()["model"] == MODEL
            assert client.post(f"/api/analyses/{run_id}/cancel").json()["status"] == "cancelled"


def test_validation_rejects_hidden_budget_growth():
    with pytest.raises(ValueError, match="candidate budget"):
        request(max_candidates=20, budgets=Budgets(max_candidates=4))
    with pytest.raises(ValueError):
        AnalysisRequest(compound_ids=["quercetin", "aspirin"], protein_sequence="ACD;$(ls)")
    with pytest.raises(ValueError):
        AnalysisRequest(compound_ids=["quercetin", "aspirin"], budgets={"max_llm_calls": 1000})


def test_imported_compounds_are_validated_and_provenance_remains_explicit(tmp_path):
    from herbfold.chemistry import load_catalog

    catalog = {row["id"]: row for row in load_catalog()}
    supplied = [
        {
            **catalog["quercetin"],
            "id": "custom_herb",
            "cid": 5280343,
            "pubchem_cid": None,
            "descriptors": {"qed": 999},
        },
        {**catalog["aspirin"], "id": "custom_drug"},
    ]
    supplied[0].pop("pubchem_cid")
    with engine(tmp_path) as worker:
        req = AnalysisRequest(compounds=supplied, mode="local", max_candidates=2, quantum_mode="off")
        run = worker.create(req, start=False)
        result = worker.run(run["id"])
    assert result["status"] == "completed"
    assert result["compounds"][0]["pubchem_cid"] == 5280343
    assert all(row["source_status"] == "user_provided_unverified" for row in result["compounds"])
    assert all(record["kind"] == "user_import" for record in result["result"]["evidence"]["records"])
    assert len(result["result"]["candidates"]) == 2
    assert all(candidate["descriptors"]["qed"] <= 1 for candidate in result["result"]["candidates"])


def test_catalog_compounds_sent_as_objects_keep_verified_snapshot(tmp_path):
    from herbfold.chemistry import load_catalog

    supplied = [row for row in load_catalog() if row["id"] in {"quercetin", "aspirin"}]
    with engine(tmp_path) as worker:
        run = worker.create(AnalysisRequest(compounds=supplied, mode="local"), start=False)
    assert all(row["source_status"] == "catalog_snapshot" for row in run["compounds"])
    assert all(row.get("source_url") for row in run["compounds"])


def test_import_rejects_invalid_structure_and_ambiguous_categories(tmp_path):
    parents = [
        {"id": "a", "category": "herbal", "smiles": "not a molecule"},
        {"id": "b", "category": "drug", "smiles": "CCO"},
    ]
    with engine(tmp_path) as worker:
        with pytest.raises(ValueError, match="Invalid SMILES"):
            worker.create(AnalysisRequest(compounds=parents), start=False)
    parents[0]["smiles"] = "CC"
    with pytest.raises(ValueError, match="not both"):
        AnalysisRequest(compounds=parents, compound_ids=["quercetin", "aspirin"])
    parents[0]["category"] = "unknown"
    with pytest.raises(ValueError):
        AnalysisRequest(compounds=parents)
    parents[0]["category"] = "herbal"
    parents[0]["source_url"] = "javascript:alert(1)"
    with pytest.raises(ValueError, match="HTTP"):
        AnalysisRequest(compounds=parents)


def test_cancel_during_ibm_preflight_prevents_submission(tmp_path, monkeypatch):
    submissions = []
    with engine(tmp_path) as worker:
        run = worker.create(request(mode="local", quantum_mode="ibm"), start=False)

        def preflight(*args, **kwargs):
            worker.cancel(run["id"])
            return {"status": "ready", "n_qubits": 156}

        monkeypatch.setattr(quantum, "plan_quantum", preflight)
        monkeypatch.setattr(quantum, "submit_kernel", lambda *a, **kw: submissions.append(kw))
        result = worker.run(run["id"])
        assert not (worker.store.directory(run["id"]) / "quantum-intent.json").exists()
    assert result["status"] == "cancelled"
    assert submissions == []


def test_refresh_analysis_quantum_retrieves_only_and_marks_old_report_stale(tmp_path, monkeypatch):
    submissions = []
    monkeypatch.setattr(quantum, "submit_kernel", lambda *a, **kw: submissions.append(kw))
    with engine(tmp_path) as worker:
        run = worker.create(request(mode="local", quantum_mode="ibm"), start=False)
        manifest = {
            "schema_version": 1,
            "status": "submitted",
            "plan": {"n_qubits": 156},
            "jobs": [{"job_id": "existing-qpu"}],
            "hardware_executed": None,
        }
        worker.store.write(run["id"], "quantum.json", manifest)

        def setup(state):
            state["status"] = "completed"
            worker._stage(state, "quantum").update(
                status="completed", result={**manifest, "sample_ids": ["a", "b"]}
            )
            worker._stage(state, "report").update(
                status="completed", result={"summary": "Original pending-job report"}
            )
            worker._stage(state, "review").update(
                status="completed", result={"summary": "Original pending-job review"}
            )

        worker._mutate(run["id"], setup)
        monkeypatch.setattr(
            quantum,
            "retrieve_kernel",
            lambda path: {
                **manifest,
                "status": "completed",
                "hardware_executed": True,
                "kernel": [[1, 0.2], [0.2, 1]],
            },
        )
        refreshed = worker.refresh_quantum(run["id"])
    assert submissions == []
    assert refreshed["result"]["quantum"]["hardware_executed"] is True
    assert refreshed["result"]["quantum"]["sample_ids"] == ["a", "b"]
    assert refreshed["result"]["report"]["stale"] is True
    assert refreshed["usage"]["llm_calls"] == 0


def test_old_stopped_prompt_is_reassessed_without_overriding_the_decision(tmp_path):
    from herbfold.llm import PROMPT_VERSION

    provider = FakeAstra(fail_role="candidate_design")
    with engine(tmp_path, provider) as worker:
        run = worker.create(request(budgets=Budgets(max_source_requests=0)), start=False)
        worker.run(run["id"])

        def old_prompt(state):
            stage = worker._stage(state, "chemistry")
            stage["status"] = "blocked"
            stage["agent"]["prompt_version"] = "old-scope"
            stage["agent"]["decision"]["decision"] = "stop"
            stage["agent"]["decision"]["summary"] = "Old prompt incorrectly attempted later stages"

        worker._mutate(run["id"], old_prompt)
        provider.fail_role = None
        worker.resume(run["id"])
        worker._futures[run["id"]].result(timeout=10)
        result = worker.get(run["id"])
    assert result["status"] == "completed"
    current = worker._stage(result, "chemistry")
    assert current["agent"]["prompt_version"] == PROMPT_VERSION
    assert current["agent_history"][0]["decision"]["decision"] == "stop"
    assert provider.calls.count("chemistry") == 2
