"""Quantum API provenance and control-flow tests, isolated synthetic inputs only."""

import copy

import pytest
from fastapi.testclient import TestClient

from herbfold import quantum
from herbfold.api import create_app
from herbfold.storage import Store


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv("HERBFOLD_API_TOKEN", raising=False)
    monkeypatch.setenv("HERBFOLD_ALLOWED_HOSTS", "testserver,localhost")
    root = tmp_path / "quantum-api"
    with TestClient(create_app(root)) as client:
        yield client, Store(root)


def original_analysis(store):
    source = store.create("analysis", {"synthetic_test_only": True})
    previous = {
        "sample_ids": ["synthetic-a", "synthetic-b"],
        "feature_definition": {"features": [[0.1, 0.2], [0.8, -0.2]], "columns": ["test1", "test2"]},
        "kernel": [[0, 0], [0, 0]],
    }
    store.update(source["id"], "completed", {"quantum": previous})
    return source["id"], previous


def test_default_local_projected_api_preserves_original_and_linked_result(api):
    client, store = api
    source_id, previous = original_analysis(store)
    response = client.post("/api/quantum/run", json={
        "features": previous["feature_definition"]["features"],
        "source_analysis_id": source_id, "sample_labels": ["Test A", "Test B"],
        "qubits": 2,
    })
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "completed"
    assert job["payload"]["kernel_method"] == "projected"
    assert job["result"]["source_analysis_id"] == source_id
    assert job["result"]["sample_ids"] == previous["sample_ids"]
    assert job["result"]["feature_definition"] == previous["feature_definition"]
    assert store.get(source_id)["result"]["quantum"] == previous
    listing = client.get("/api/quantum/results", params={"source_analysis_id": source_id}).json()
    assert [row["id"] for row in listing["items"]] == [job["id"]]
    assert client.get(f"/api/jobs/{job['id']}/artifacts/kernel.json").json() == job["result"]


@pytest.mark.parametrize("change", ["values", "row_order", "sample_order"])
def test_reanalysis_rejects_false_original_association_before_submission(api, monkeypatch, change):
    client, store = api
    source_id, previous = original_analysis(store)
    payload = {"features": copy.deepcopy(previous["feature_definition"]["features"]),
               "source_analysis_id": source_id, "sample_ids": previous["sample_ids"]}
    if change == "values":
        payload["features"][0][0] += 0.01
    elif change == "row_order":
        payload["features"].reverse()
    else:
        payload["sample_ids"] = list(reversed(payload["sample_ids"]))
    submitted = []
    monkeypatch.setattr(quantum, "submit_kernel", lambda *a, **kw: submitted.append(kw))
    for endpoint in ("plan", "run"):
        response = client.post(f"/api/quantum/{endpoint}", json=payload)
        assert response.status_code == 422
        assert "exactly match" in response.text
    assert submitted == []
    assert len(store.list()) == 1


def test_quantum_metadata_does_not_leak_into_plan_kwargs(api, monkeypatch):
    client, _ = api
    calls = []

    def planner(**kwargs):
        calls.append(kwargs)
        return {"status": "ready", "n_qubits": 2}

    monkeypatch.setattr(quantum, "plan_quantum", planner)
    result = client.post("/api/quantum/plan", json={
        "features": [[1], [2]], "sample_ids": ["a", "b"], "sample_labels": ["A", "B"],
    })
    assert result.status_code == 200
    assert result.json()["sample_ids"] == ["a", "b"]
    assert all(key not in calls[0] for key in ("sample_ids", "sample_labels", "source_analysis_id"))
    assert calls[0]["kernel_method"] == "projected"


@pytest.mark.parametrize("extra", [
    {"mode": "unknown"}, {"kernel_method": "synthetic"}, {"block_size": 7}, {"gamma": 0},
    {"sample_ids": ["one"]}, {"sample_ids": ["same", "same"]},
    {"source_analysis_id": "../../private"},
])
def test_invalid_quantum_request_fails_before_creating_job(api, extra):
    client, store = api
    response = client.post("/api/quantum/run", json={"features": [[1], [2]], **extra})
    assert response.status_code == 422
    assert not store.list()


def test_failed_preflight_creates_no_quantum_job(api, monkeypatch):
    client, store = api
    monkeypatch.setattr(quantum, "plan_quantum", lambda **kwargs: {"status": "credentials_required"})
    response = client.post("/api/quantum/run", json={"features": [[1], [2]], "mode": "ibm"})
    assert response.status_code == 422
    assert "credentials_required" in response.text
    assert not store.list()


def test_refresh_preserves_source_mapping_and_never_submits(api, monkeypatch):
    client, store = api
    source_id, previous = original_analysis(store)
    payload = {"mode": "ibm", "source_analysis_id": source_id,
               "sample_ids": previous["sample_ids"], "sample_labels": ["A", "B"],
               "feature_definition": previous["feature_definition"]}
    job = store.create("quantum", payload)
    submitted = []
    monkeypatch.setattr(quantum, "submit_kernel", lambda *a, **kw: submitted.append(kw))
    monkeypatch.setattr(quantum, "retrieve_kernel", lambda *a: {
        "status": "completed", "kernel": [[1, 0.8], [0.8, 1]], "method": "projected",
    })
    response = client.post(f"/api/quantum/{job['id']}/refresh")
    assert response.status_code == 200
    assert response.json()["result"]["source_analysis_id"] == source_id
    assert response.json()["result"]["sample_ids"] == previous["sample_ids"]
    assert submitted == []
    assert store.get(source_id)["result"]["quantum"] == previous
