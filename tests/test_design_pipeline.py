"""Software fixtures; synthetic assay values are never efficacy evidence."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from rdkit import Chem

from herbfold import chemistry
from herbfold.design_pipeline import CachedObservations, DesignPipeline, DesignRequest
from herbfold.design_pipeline_api import make_router
from herbfold.storage import Store


def compound(identifier="phenol", smiles="Oc1ccccc1", category="herbal"):
    return {"id": identifier, "name": identifier, "smiles": smiles, "category": category}


def request(mode="transform", compounds=None, **kwargs):
    return DesignRequest(mode=mode, compounds=compounds or [compound()], **kwargs)


@pytest.fixture
def engine(tmp_path):
    instance = DesignPipeline(Store(tmp_path))
    yield instance
    instance.close()


def seed_observations(root, smiles="Oc1ccccc1"):
    directory = root / "validation"
    (directory / "bio-validation").mkdir(parents=True, exist_ok=True)
    (directory / "tox21").mkdir(parents=True, exist_ok=True)
    (directory / "bio-validation/curated-records.json").write_text(json.dumps([
        {"smiles": chemistry.canonical_smiles(smiles), "target": "PTGS2", "endpoint": "IC50",
         "value_nM": 1e9, "pactivity": 0, "assay_id": "SOFTWARE-FIXTURE",
         "source_url": "https://example.org/software-fixture", "activity_ids": [0]},
        {"smiles": chemistry.canonical_smiles(smiles), "target": "KCNH2", "endpoint": "IC50",
         "value_nM": 10, "assay_id": "WRONG-TARGET", "source_url": "https://example.org/wrong-target"}]))
    (directory / "tox21/curated-labels.jsonl").write_text(json.dumps({
        "smiles": chemistry.canonical_smiles(smiles), "labels": {"NR-AR": 0}, "source_ids": ["TEST-ONLY"]}) + "\n")


def test_actual_phenolic_edits_descriptors_and_deterministic_ids(engine):
    first = engine.create(request(transformations=["o_methylation", "o_acetylation"]), background=False)
    second = engine.create(request(transformations=["o_methylation", "o_acetylation"]), background=False)
    assert first["status"] == second["status"] == "completed"
    rows = first["result"]["candidates"]
    assert {r["smiles"] for r in rows} == {chemistry.canonical_smiles("COc1ccccc1"), chemistry.canonical_smiles("CC(=O)Oc1ccccc1")}
    assert [r["id"] for r in rows] == [r["id"] for r in second["result"]["candidates"]]
    assert all(r["descriptor_delta"]["phenol"]["hbd"] == -1 for r in rows)
    assert all(r["provenance"]["single_step"] for r in rows)
    assert all(r["evidence"] == [] for r in rows)
    assert first["result"]["execution"]["external_calls"] == 0
    assert [e["seq"] for e in first["events"]] == list(range(1, len(first["events"]) + 1))
    assert all(s["status"] == "completed" and s["started"] <= s["finished"] for s in first["stages"])


def test_inactive_and_zero_are_retained_and_target_join_is_exact(engine):
    seed_observations(engine.store.root)
    values = engine.observations.lookup("c1ccc(O)cc1", "P35354")
    assert len(values) == 2
    assert values[0]["pactivity"] == 0
    assert values[0]["value"] == 1e9
    assert values[1]["label"] == 0
    assert values[1]["target_accession"] is None
    other = engine.observations.lookup("Oc1ccccc1", "P00533")
    assert len(other) == 1 and other[0]["kind"] == "pathway_assay"
    assert engine.observations.lookup("COc1ccccc1", "P35354") == []


def test_transformed_child_cannot_inherit_parent_observations(engine):
    seed_observations(engine.store.root)
    run = engine.create(request(), background=False)
    assert run["result"]["candidates"]
    assert all(r["evidence"] == [] for r in run["result"]["candidates"])
    assert all(r["evidence_status"] == "no_exact_cached_observation" for r in run["result"]["candidates"])


def test_predicted_censored_or_conflicting_target_rows_are_not_observations(engine):
    seed_observations(engine.store.root)
    path = engine.store.root / "validation/bio-validation/curated-records.json"
    template = json.loads(path.read_text())[0]
    path.write_text(json.dumps([{**template, "status": "predicted"}, {**template, "relation": ">"},
                               {**template, "target": "KCNH2", "target_accession": "P35354"}]))
    rows = engine.observations.lookup("Oc1ccccc1", "P35354")
    assert len(rows) == 1 and rows[0]["kind"] == "pathway_assay"


def test_mixture_keeps_components_and_observations_separate(engine):
    seed_observations(engine.store.root)
    run = engine.create(request("combination", [compound(), compound("drug", "CC(=O)O", "drug")]), background=False)
    candidate = run["result"]["candidates"][0]
    assert "smiles" not in candidate and "descriptors" not in candidate
    assert len(candidate["components"]) == 2 and candidate["evidence"] == []
    assert candidate["components"][0]["evidence"][1]["label"] == 0
    assert candidate["components"][1]["evidence"] == []
    assert not candidate["provenance"]["joint_structure_generated"]


def test_stereo_and_ineligible_transforms_return_real_results(engine):
    run = engine.create(request(compounds=[compound("stereo", "CC(O)C(=O)O")], transformations=["stereoisomers"]), background=False)
    rows = run["result"]["candidates"]
    assert len(rows) == 2
    assert len({r["smiles"] for r in rows}) == 2
    assert all(r["descriptors"]["unassigned_stereocenters"] == 0 for r in rows)
    empty = engine.create(request(compounds=[compound("ethanol", "CCO")], transformations=["o_methylation"]), background=False)
    assert empty["status"] == "completed" and empty["result"]["candidates"] == []


def test_oversized_edit_is_reported_without_losing_other_parent_proposals(engine):
    large = "C" * 249 + "c1ccccc1O"
    assert chemistry.standardize_molecule(large).GetNumHeavyAtoms() == 256
    run = engine.create(request(compounds=[compound("large", large), compound()],
                                transformations=["o_methylation"]), background=False)
    assert run["status"] == "completed", run["error"]
    assert len(run["result"]["candidates"]) == 1
    assert run["result"]["candidates"][0]["parents"][0]["id"] == "phenol"
    assert run["result"]["design_audit"]["skipped_variant_count"] == 1
    assert "256" in run["result"]["design_audit"]["skipped_variants"][0]["reason"]


def test_hybrid_retains_verified_both_parent_lineage(engine):
    parents = [compound("natural", "CCOc1ccccc1", "natural_product"), compound("drug", "CCNC(=O)c1ccncc1", "drug")]
    run = engine.create(request("hybrid", parents, max_candidates=6), background=False)
    assert run["status"] == "completed", run["error"]
    rows = run["result"]["candidates"]
    assert rows
    for row in rows:
        assert Chem.MolFromSmiles(row["smiles"]) is not None
        assert {p["id"] for p in row["parents"]} == {"natural", "drug"}
        assert row["provenance"]["both_parent_fragments_verified"]
        assert all(p["contributing_fragments"] for p in row["provenance"]["parent_fragments"])
        assert row["smiles"] not in {chemistry.canonical_smiles(p["smiles"]) for p in parents}


@pytest.mark.parametrize("kwargs", [
    {"compounds": [compound("x", "invalid")]},
    {"compounds": [compound(), compound()]},
    {"compounds": [compound(), compound("same", "c1ccc(O)cc1")]},
    {"mode": "hybrid", "compounds": [compound()]},
    {"mode": "hybrid", "compounds": [compound(), compound("x", "CCO")]},
    {"max_candidates": 33}, {"max_candidates": True}, {"transformations": []},
    {"compounds": [compound(str(i), "C" * (i + 1)) for i in range(9)]},
    {"target_accession": "not-a-target"},
    {"compounds": [{**compound(), "category": "unknown"}]},
])
def test_invalid_design_inputs_fail_before_creation(kwargs):
    with pytest.raises((ValueError, ValidationError)):
        DesignRequest.model_validate({"mode": "transform", "compounds": [compound()], **kwargs})


def test_cancel_running_work_never_overwrites_terminal_state(engine, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = engine._properties

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(engine, "_properties", blocked)
    run = engine.create(request())
    assert entered.wait(5)
    cancelled = engine.cancel(run["id"])
    assert cancelled["status"] == "cancelled"
    release.set()
    engine.pool.shutdown(wait=True)
    final = engine.get(run["id"])
    assert final["status"] == "cancelled" and final["result"] is None
    assert final["events"][-1]["status"] == "cancelled"


def test_restart_marks_unfinished_records_interrupted(tmp_path):
    store = Store(tmp_path)
    pool = ThreadPoolExecutor(max_workers=1)
    blocker = threading.Event()
    pool.submit(blocker.wait, 5)
    original = DesignPipeline(store, pool)
    run = original.create(request())
    original.close()
    recovered = DesignPipeline(store)
    try:
        assert recovered.get(run["id"])["status"] == "interrupted"
        blocker.set()
        pool.shutdown(wait=True)
        assert recovered.get(run["id"])["status"] == "interrupted"
    finally:
        blocker.set()
        original.close()
        recovered.close()


def test_owner_collision_release_and_old_double_close_preserve_successor(tmp_path):
    store = Store(tmp_path)
    pool = ThreadPoolExecutor(max_workers=1)
    blocker = threading.Event()
    pool.submit(blocker.wait, 5)
    first = DesignPipeline(store, pool)
    second = None
    try:
        active = first.create(request())
        with pytest.raises(RuntimeError, match="active design-pipeline owner"):
            DesignPipeline(store)
        assert first.get(active["id"])["status"] == "queued"
        first.close()
        second = DesignPipeline(store, pool)
        successor_run = second.create(request())
        assert second.get(active["id"])["status"] == "interrupted"
        first.close()
        assert second.get(successor_run["id"])["status"] == "queued"
        with pytest.raises(RuntimeError, match="active design-pipeline owner"):
            DesignPipeline(store)
        blocker.set()
        pool.shutdown(wait=True)
        assert second.get(successor_run["id"])["status"] == "completed"
    finally:
        blocker.set()
        first.close()
        if second is not None:
            second.close()
        pool.shutdown(wait=True)


def test_properties_and_evidence_are_concurrent(engine, monkeypatch):
    barrier = threading.Barrier(2)
    original_properties, original_evidence = engine._properties, engine._evidence

    def properties(*args):
        barrier.wait(5)
        return original_properties(*args)

    def evidence(*args):
        barrier.wait(5)
        return original_evidence(*args)

    monkeypatch.setattr(engine, "_properties", properties)
    monkeypatch.setattr(engine, "_evidence", evidence)
    assert engine.create(request(), background=False)["status"] == "completed"


def test_broken_cache_remains_unknown_and_refreshes_after_new_receipt(tmp_path):
    root = tmp_path / "validation"
    (root / "bio-validation").mkdir(parents=True)
    path = root / "bio-validation/curated-records.json"
    path.write_text("not-json")
    cache = CachedObservations(root)
    assert cache.lookup("Oc1ccccc1", "P35354") == []
    assert any("could not be parsed" in note for note in cache.notes)
    seed_observations(tmp_path)
    assert len(cache.lookup("Oc1ccccc1", "P35354")) == 2


def test_unexpected_specialist_error_becomes_terminal_failure(engine, monkeypatch):
    def broken_lookup(*args):
        raise TypeError("software-fixture malformed cached field")

    monkeypatch.setattr(engine.observations, "lookup", broken_lookup)
    run = engine.create(request(), background=False)
    assert run["status"] == "failed"
    assert "TypeError" in run["error"]
    assert run["result"] is None
    assert all(s["status"] != "running" for s in run["stages"])


def test_api_contract_returns_durable_run_and_handles_unknown(tmp_path):
    app = FastAPI()
    router = make_router(Store(tmp_path))
    app.include_router(router)
    try:
        with TestClient(app) as client:
            options = client.get("/api/design-pipeline/options").json()
            assert {m["id"] for m in options["modes"]} == {"combination", "hybrid", "transform"}
            assert options["execution"] == "deterministic_specialist_agents"
            assert client.get("/api/design-pipeline/runs/unknown").status_code == 404
            assert client.post("/api/design-pipeline/runs/unknown/cancel").status_code == 404
            invalid = client.post("/api/design-pipeline/runs", json={"mode": "transform", "compounds": [compound()], "max_candidates": 100})
            assert invalid.status_code == 422
            response = client.post("/api/design-pipeline/runs", json=request().model_dump())
            assert response.status_code == 202
            row = response.json()
            assert client.get("/api/design-pipeline/runs/" + row["id"]).status_code == 200
            assert client.get("/api/design-pipeline/runs").json()["items"][0]["id"] == row["id"]
            assert client.post(f"/api/design-pipeline/runs/{row['id']}/cancel").status_code == 200
    finally:
        router.engine.close()
