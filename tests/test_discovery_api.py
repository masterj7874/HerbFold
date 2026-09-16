"""Software-contract fixtures only; none are evidence of a useful medicine."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from herbfold import chemistry
from herbfold.api import create_app
from herbfold.discovery_api import CampaignRequest, DiscoveryService, IngestRequest
from herbfold.orchestration import AnalysisRequest, Orchestrator
from herbfold.storage import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("HERBFOLD_API_TOKEN", raising=False)
    monkeypatch.setenv("HERBFOLD_ALLOWED_HOSTS", "testserver,localhost")
    app = create_app(tmp_path)
    with TestClient(app) as result:
        yield result


def populate(service):
    # Existing public structures, solely used to exercise software contracts.
    service.catalog.ingest_records(
        [row for row in chemistry.load_catalog() if row["category"] == "herbal"],
        source_id="lotus-2026-04",
        source_url="https://example.org/test-fixture",
        license_label="test fixture",
    )


def test_empty_summary_has_no_fabricated_millions(client):
    result = client.get("/api/discovery/summary").json()
    assert result["catalog"]["compound_count"] == 0
    assert result["catalog"]["provenance_count"] == 0
    assert result["campaigns"] == []
    assert result["scale"]["max_target_count"] == 100_000_000
    assert sum(row["automated"] for row in result["sources"]) == 3


def test_catalog_search_pages_detail_and_validation(client):
    service = client.app.state.discovery
    populate(service)
    first = client.get("/api/discovery/compounds?limit=2").json()
    second = client.get("/api/discovery/compounds?limit=2&offset=2").json()
    assert first["total"] == 4
    assert not {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
    search = client.get("/api/discovery/compounds?search=baicalein").json()
    assert search["total"] == 1
    detail = client.get(f"/api/discovery/compounds/{search['items'][0]['id']}").json()
    assert detail["provenance"][0]["license_label"] == "test fixture"
    assert client.get("/api/discovery/compounds?limit=1000000").status_code == 422
    assert client.get("/api/discovery/compounds/-1").status_code == 404


def test_source_whitelist_and_manual_sources_never_submit(client):
    for source in ("https://127.0.0.1/private", "unknown", "kiom-oasis", "imppat"):
        assert client.post("/api/discovery/ingestions", json={"source_id": source}).status_code == 422
    assert client.get("/api/discovery/ingestions").json() == []


def test_real_campaign_contract_pause_resume_and_hundred_million_goal(client, monkeypatch):
    service = client.app.state.discovery
    populate(service)
    monkeypatch.setattr(service, "schedule_campaign", lambda _: None)
    response = client.post(
        "/api/discovery/campaigns",
        json={
            "target_count": 100_000_000,
            "seed_limit": 4,
            "max_attempts": 200,
        },
    )
    assert response.status_code == 202, response.text
    created = response.json()
    assert created["retained"] == created["generated"] == 0
    assert created["requested"] == 100_000_000
    assert created["seed_count"] <= 7
    route = f"/api/discovery/campaigns/{created['id']}"
    service.campaigns.run_batch(created["id"], max_attempts=5)
    paused = client.post(route + "/pause").json()
    before = paused["attempted"]
    assert paused["status"] == "paused"
    assert service.campaigns.run_batch(created["id"])["attempted"] == before
    assert client.post(route + "/resume").json()["status"] == "queued"
    state = service.campaigns.run_batch(created["id"], max_attempts=200)
    assert state["status"] in {"exhausted", "budget_exhausted"}
    assert state["retained"] < state["requested"]
    rows = client.get(route + "/candidates?limit=200").json()
    assert rows["total"] > 0
    assert all(row["affinity"] is None and row["novelty"] == "not_assessed" for row in rows["items"])
    assert all("natural_product_parent_id" in row for row in rows["items"])
    assert client.get(route + "/candidates?limit=500").status_code == 422


def test_oversized_campaign_and_empty_seed_search_are_rejected(client, monkeypatch):
    populate(client.app.state.discovery)
    monkeypatch.setattr(client.app.state.discovery, "schedule_campaign", lambda _: None)
    assert client.post("/api/discovery/campaigns", json={"target_count": 100_000_001}).status_code == 422
    assert (
        client.post("/api/discovery/campaigns", json={"seed_search": "NO_SUCH_TEST_TAXON"}).status_code == 422
    )
    assert client.post("/api/discovery/campaigns", json={"seed_source": "chembl-approved"}).status_code == 422


def test_source_progress_job_identity_and_retry_dedup(tmp_path, monkeypatch):
    from herbfold import discovery_api

    service = DiscoveryService(Store(tmp_path))
    file = tmp_path / "test.csv"
    file.write_text("id,smiles,name\nA,CCO,TEST_ONLY\nB,broken,INVALID_TEST_ONLY\n")
    monkeypatch.setattr(discovery_api, "download_source", lambda *args: (file, {"sha256": "test"}))
    try:
        job = service.start_ingestion(IngestRequest(source_id="coconut-2026-09"), background=False)
        assert job["id"] == service._ingest_jobs()[0]["id"]
        assert job["status"] == "completed" and job["processed"] == 2
        assert job["inserted"] == 1 and job["invalid"] == 1
        assert service.catalog.summary()["compound_count"] == 1
        retry = service.start_ingestion(IngestRequest(source_id="coconut-2026-09"), background=False)
        assert retry["inserted"] == 0
        assert service.catalog.summary()["provenance_count"] == 1
    finally:
        service.close()


def test_recovery_keeps_committed_imports_and_pauses_campaign(tmp_path):
    store = Store(tmp_path)
    service = DiscoveryService(store)
    try:
        populate(service)
        job = store.create("bulk_import", {"source_id": "lotus-2026-04"})
        store.update(job["id"], "importing", {"processed_records": 12})
        campaign = service.campaigns.create(
            {"target_candidates": 100}, service._seeds(CampaignRequest(seed_limit=4))
        )
        service.recover()
        assert store.get(job["id"])["status"] == "interrupted"
        assert service.catalog.summary()["compound_count"] == 4
        assert service.campaigns.get(campaign["id"])["status"] == "paused"
    finally:
        service.close()


def test_natural_product_parents_preserve_identity_in_existing_workflow(client):
    rows = chemistry.load_catalog()
    plant = {**rows[1], "id": "test-natural-product", "category": "natural_product"}
    drug = next(row for row in rows if row["category"] == "drug")
    response = client.post("/api/workflows", json={"compounds": [plant, drug], "max_candidates": 2})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["compounds"][0]["category"] == "natural_product"
    assert result["comparison"][0]["source_parent_category"] == "natural_product"
    assert all(
        "natural_product_parent_id" in row and "herbal_parent_id" not in row for row in result["candidates"]
    )


def test_natural_product_orchestration_can_prepare_without_paid_services(tmp_path):
    rows = chemistry.load_catalog()
    plant = {**rows[1], "id": "test-natural-product", "category": "natural_product"}
    drug = next(row for row in rows if row["category"] == "drug")
    with ThreadPoolExecutor(max_workers=1) as pool:
        orchestrator = Orchestrator(Store(tmp_path), pool)
        request = AnalysisRequest(compounds=[plant, drug], mode="local", quantum_mode="off")
        state = orchestrator.create(request, start=False)
        assert state["compounds"][0]["category"] == "natural_product"
        assert state["usage"]["llm_calls"] == 0
        assert state["status"] == "queued"


def test_catalog_payload_is_bounded_and_json_serializable(client):
    populate(client.app.state.discovery)
    response = client.get("/api/discovery/summary")
    assert len(json.dumps(response.json())) < 40_000


def test_local_registered_source_can_seed_with_unverified_origin(tmp_path):
    service = DiscoveryService(Store(tmp_path))
    try:
        service.catalog.ingest_records(
            [{"id": "test-natural-parent", "smiles": chemistry.load_catalog()[1]["smiles"]}],
            source_id="local-fixture",
            source_url="https://example.org/source",
            license_label="test fixture",
        )
        request = CampaignRequest(seed_source="local-fixture", seed_limit=1)
        parents = list(service._seeds(request))
        source_parent = next(row for row in parents if row["category"] == "natural_product")
        assert source_parent["selected_source_id"] == "local-fixture"
        assert "not_verified" in source_parent["source_scope"]
    finally:
        service.close()


def test_korean_herb_lookup_is_explicit_and_preserves_query(client):
    service = client.app.state.discovery
    service.catalog.ingest_records(
        [{"id": "T", "smiles": "CCO", "organisms": "Scutellaria baicalensis"}],
        source_id="lotus-2026-04",
        source_url="https://example.org/taxon-fixture",
        license_label="test fixture",
    )
    aliases = client.get("/api/discovery/herbs").json()
    assert aliases["total"] > 10
    entry = next(row for row in aliases["items"] if row["name_ko"] == "강황")
    assert entry["query"] == "Curcuma longa"
    # An arbitrary mixed/unknown term is never silently reinterpreted.
    result = client.get("/api/discovery/compounds?search=unmapped_TEST").json()
    assert result["query_resolution"]["original_query"] == "unmapped_TEST"
    assert not result["query_resolution"]["mapped"]


def test_catalog_kind_filters_preserve_korean_mapping_and_provenance(client):
    service = client.app.state.discovery
    source = {"source_url": "https://example.org/kind-fixture", "license_label": "test fixture"}
    service.catalog.ingest_records([
        {"id": "plant", "smiles": "CCO", "organisms": "Scutellaria baicalensis"},
        {"id": "plant-duplicate", "smiles": "CCO", "organisms": "Scutellaria baicalensis"},
        {"id": "plant-other", "smiles": "O", "organisms": "Scutellaria baicalensis"},
    ], source_id="lotus-2026-04", **source)
    service.catalog.ingest_records([
        {"id": "drug", "smiles": "CCO", "name": "Example drug"},
    ], source_id="chembl-approved", **source)

    params = {"kind": "natural_product", "source": "lotus-2026-04", "search": "황금", "limit": 1}
    first = client.get("/api/discovery/compounds", params=params)
    assert first.status_code == 200, first.text
    first = first.json()
    second = client.get("/api/discovery/compounds", params={**params, "offset": 1}).json()
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["query_resolution"]["original_query"] == "황금"
    assert first["query_resolution"]["mapped"]
    assert first["query_resolution"]["query"] == "Scutellaria baicalensis"
    assert first["items"][0]["source_kinds"] == ["natural_product", "drug"]
    assert client.get("/api/discovery/compounds", params={"kind": "drug", "search": "황금"}).json()["total"] == 0
    drugs = client.get("/api/discovery/compounds", params={"kind": "drug"}).json()
    assert drugs["total"] == 1
    assert drugs["items"][0]["sources"][0]["source_id"] == "chembl-approved"
    assert client.get("/api/discovery/compounds", params={
        "kind": "drug", "source": "lotus-2026-04",
    }).json()["total"] == 0


@pytest.mark.parametrize("kind", ["herbal", "reference", "", "DRUG", "all"])
def test_catalog_kind_query_rejects_invalid_values(client, kind):
    response = client.get("/api/discovery/compounds", params={"kind": kind})
    assert response.status_code == 422
