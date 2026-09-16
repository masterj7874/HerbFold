from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from herbfold.api import create_app
from herbfold.storage import Store


def test_job_claim_is_atomic(tmp_path):
    store = Store(tmp_path)
    job = store.create("alphafold", {"name": "same_submission"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda _: store.claim(job["id"]), range(20)))
    assert sum(claimed) == 1
    assert store.get(job["id"])["status"] == "queued"


def test_artifact_paths_cannot_escape_job(tmp_path):
    store = Store(tmp_path / "store")
    job = store.create("example", {})
    outside = tmp_path / "outside.json"
    outside.write_text("private")
    (store.directory(job["id"]) / "escape.json").symlink_to(outside)
    for path in ("../../outside.json", str(outside), "escape.json"):
        with pytest.raises(ValueError):
            store.artifact(job["id"], path)


def test_api_token_and_cross_origin_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERBFOLD_API_TOKEN", "test-token-not-real")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/catalog").status_code == 401
        headers = {"Authorization": "Bearer test-token-not-real"}
        assert client.get("/api/catalog", headers=headers).status_code == 200
        response = client.post(
            "/api/molecules/describe",
            json={"smiles": "CCO"},
            headers={**headers, "Origin": "https://unrelated.example"},
        )
        assert response.status_code == 403
        assert client.get("/", headers={"Host": "unrelated.example"}).status_code == 400


def test_restarted_af3_jobs_are_marked_interrupted(tmp_path):
    store = Store(tmp_path)
    job = store.create("alphafold", {})
    store.update(job["id"], "running", {"job_id": "local"})
    with TestClient(create_app(tmp_path)):
        assert store.get(job["id"])["status"] == "interrupted"


def test_invalid_request_numbers_never_become_artifact_values(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/quantum/plan",
            content='{"features":[[NaN],[1]]}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
