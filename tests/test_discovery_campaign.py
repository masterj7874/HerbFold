"""Behavioral checks for durable, honest, bounded candidate campaigns."""

import json
import sqlite3

import pytest
from rdkit import Chem

from herbfold.chemistry import canonical_smiles, load_catalog
from herbfold.discovery_campaign import TERMINAL_STATUSES, CampaignStore


@pytest.fixture
def parents():
    return load_catalog()


def finish(store, campaign_id, batch=50):
    for _ in range(100):
        row = store.run_batch(campaign_id, max_attempts=batch, max_seconds=30)
        if row["status"] in TERMINAL_STATUSES:
            return row
    pytest.fail("Finite seed search should exhaust within bounded work")


def test_hundred_million_is_only_a_target_and_does_not_preallocate(tmp_path, parents):
    store = CampaignStore(tmp_path)
    row = store.create({"target_candidates": 100_000_000}, parents)
    assert row["requested"] == 100_000_000
    assert row["retained"] == row["generated"] == row["attempted"] == 0
    assert row["total_pairs"] == 12
    assert store.path.stat().st_size < 1_000_000
    final = finish(store, row["id"])
    assert final["status"] == "exhausted"
    assert final["reason"] == "bounded_search_space_exhausted"
    assert 0 < final["retained"] < final["requested"]
    assert final["progress_fraction"] < 1
    assert "not new medicines" in final["interpretation"]


def test_pause_resume_and_new_store_continue_identical_cursor(tmp_path, parents):
    store = CampaignStore(tmp_path / "resumed")
    row = store.create({"target_candidates": 1000, "seed": 712}, parents)
    row = store.run_batch(row["id"], max_attempts=3)
    assert row["attempted"] == 3
    paused = store.pause(row["id"])
    restarted = CampaignStore(tmp_path / "resumed")
    assert restarted.run_batch(row["id"])["cursor"] == paused["cursor"]
    assert restarted.get(row["id"])["status"] == "paused"
    restarted.resume(row["id"])
    split = finish(restarted, row["id"], batch=2)
    other = CampaignStore(tmp_path / "continuous")
    full = finish(other, other.create({"target_candidates": 1000, "seed": 712}, reversed(parents))["id"])
    for field in ("attempted", "generated", "retained", "duplicates", "rejected", "cursor", "status"):
        assert split[field] == full[field]
    assert restarted.candidates(row["id"])["items"] == other.candidates(full["id"])["items"]


def test_atomic_checkpoint_rolls_back_failed_batch(tmp_path, parents, monkeypatch):
    store = CampaignStore(tmp_path)
    row = store.create({"target_candidates": 1000}, parents)
    original = store._candidate
    calls = 0

    def crash(product, seeds, request):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise InterruptedError("worker process interrupted")
        return original(product, seeds, request)

    monkeypatch.setattr(store, "_candidate", crash)
    with pytest.raises(InterruptedError):
        store.run_batch(row["id"], max_attempts=50)
    recovered = CampaignStore(tmp_path)
    assert recovered.get(row["id"])["attempted"] == 0
    assert recovered.candidates(row["id"])["total"] == 0
    assert finish(recovered, row["id"])["retained"] > 0


def test_products_are_valid_unique_and_keep_fragment_and_source_ancestry(tmp_path, parents):
    natural = dict(parents[0], category="natural_product")
    alias = dict(natural, id="another-database-id", source_url="https://example.org/record/123")
    store = CampaignStore(tmp_path)
    row = finish(store, store.create({"target_candidates": 1000}, [natural, alias, *parents[4:]])["id"])
    assert row["duplicate_seed_count"] == 1
    candidates = store.candidates(row["id"])["items"]
    assert candidates
    assert len({candidate["smiles"] for candidate in candidates}) == len(candidates)
    originals = {canonical_smiles(parent["smiles"]) for parent in [natural, *parents[4:]]}
    for candidate in candidates:
        Chem.SanitizeMol(Chem.MolFromSmiles(candidate["smiles"]))
        assert candidate["smiles"] not in originals
        assert all(parent["contributing_fragments"] for parent in candidate["parent_fragments"])
        assert candidate["natural_product_parent_id"] == natural["id"]
        assert candidate["herbal_parent_id"] is None
        assert candidate["source_lineage"][0]["category"] == "natural_product"
        assert len(candidate["source_lineage"][0]["source_records"]) == 2
        assert candidate["affinity"] is None
        assert candidate["novelty"] == "not_assessed"
        assert candidate["status"] == "unvalidated_computational_candidate"


def test_duplicate_products_across_parent_pairs_are_not_counted_twice(tmp_path, parents):
    store = CampaignStore(tmp_path)
    row = finish(store, store.create({"target_candidates": 1000}, parents)["id"])
    assert row["duplicates"] > 0
    assert row["attempted"] == row["retained"] + row["rejected"] + row["duplicates"]
    assert store.candidates(row["id"])["total"] == row["retained"]
    items = store.candidates(row["id"], order="created")["items"]
    assert store.candidates(row["id"], limit=3, offset=2, order="created")["items"] == items[2:5]


def test_attempt_budget_applies_to_unsplittable_pairs_and_failed_products(tmp_path):
    store = CampaignStore(tmp_path)
    seeds = [
        {"id": "h1", "smiles": "CCO", "category": "herbal"},
        {"id": "h2", "smiles": "CCCO", "category": "natural_product"},
        {"id": "d1", "smiles": "CCCC", "category": "drug"},
    ]
    row = store.create({"target_candidates": 1_000_000, "max_attempts": 1}, seeds)
    row = store.run_batch(row["id"], max_attempts=50)
    assert row["status"] == "budget_exhausted"
    assert row["reason"] == "attempt_budget"
    assert row["attempted"] == row["rejected"] == row["pairs_visited"] == 1
    assert row["cursor"] == {"pair": 1, "product": 0}
    assert row["retained"] == 0


def test_descriptor_filter_excludes_candidates_without_inventing_activity(tmp_path, parents):
    store = CampaignStore(tmp_path)
    row = store.create({"target_candidates": 1000, "filters": {"min_qed": 1}}, parents)
    result = finish(store, row["id"])
    assert result["generated"] > 0
    assert result["retained"] == 0
    assert result["rejected"] == result["attempted"]
    assert result["status"] == "exhausted"
    screened = finish(store, store.create({"target_candidates": 1000, "reject_alerts": True}, parents)["id"])
    assert all(not item["descriptors"]["alerts"] for item in store.candidates(screened["id"])["items"])


def test_target_terminal_and_cancel_never_resume(tmp_path, parents):
    store = CampaignStore(tmp_path)
    first = finish(store, store.create({"target_candidates": 1}, parents)["id"])
    assert first["status"] == "completed"
    assert first["retained"] == 1
    assert store.resume(first["id"])["status"] == "completed"
    row = store.create({"target_candidates": 1000}, parents)
    assert store.cancel(row["id"])["status"] == "cancelled"
    assert store.run_batch(row["id"])["attempted"] == 0
    assert store.resume(row["id"])["status"] == "cancelled"


def test_runtime_and_storage_budgets(tmp_path, parents):
    store = CampaignStore(tmp_path)
    row = store.create({"target_candidates": 1000, "max_runtime_seconds": 1}, parents)
    with sqlite3.connect(store.path) as con:
        con.execute("UPDATE campaigns SET elapsed_seconds=1 WHERE id=?", (row["id"],))
    result = store.run_batch(row["id"])
    assert result["reason"] == "runtime_budget"
    assert result["attempted"] == 0
    row = store.create({"target_candidates": 1000, "max_storage_mb": 1}, parents)
    with sqlite3.connect(store.path) as con:
        con.execute("UPDATE campaigns SET payload_bytes=? WHERE id=?", (1024**2 - 1, row["id"]))
    result = finish(store, row["id"])
    assert result["reason"] == "payload_storage_budget"
    assert result["retained"] == 0


def test_version_change_pauses_instead_of_invalid_cursor_replay(tmp_path, parents):
    store = CampaignStore(tmp_path)
    row = store.create({}, parents)
    with sqlite3.connect(store.path) as con:
        con.execute("UPDATE campaigns SET rdkit_version='unknown' WHERE id=?", (row["id"],))
    result = store.run_batch(row["id"])
    assert result["status"] == "paused"
    assert result["reason"] == "enumerator_version_mismatch"
    assert result["attempted"] == 0


@pytest.mark.parametrize(
    "settings",
    [
        {"target_candidates": 100_000_001},
        {"target_candidates": True},
        {"max_attempts": -1},
        {"max_products_per_pair": 501},
        {"filters": {"min_qed": float("nan")}},
        {"filters": {"min_mw": 700, "max_mw": 650}},
    ],
)
def test_bad_budgets_and_filters_rejected_before_creating_campaign(tmp_path, parents, settings):
    store = CampaignStore(tmp_path)
    with pytest.raises(ValueError):
        store.create(settings, parents)
    assert store.list() == []


def test_invalid_seeds_are_counted_and_all_sources_are_json_serializable(tmp_path, parents):
    store = CampaignStore(tmp_path)
    row = store.create({}, [*parents, {"smiles": "not-a-smiles", "category": "herbal"}])
    assert row["invalid_seed_count"] == 1
    json.dumps(row, allow_nan=False)
    with pytest.raises(ValueError, match="reference drug"):
        store.create({}, parents[:4])
    with pytest.raises(KeyError):
        store.get("does-not-exist")


def test_large_export_streams_keyset_pages_in_creation_order(tmp_path, parents):
    store = CampaignStore(tmp_path)
    row = finish(store, store.create({"target_candidates": 1000}, parents)["id"])
    assert (
        list(store.iter_candidates(row["id"], batch_size=3))
        == store.candidates(row["id"], order="created")["items"]
    )
    assert len(list(store.iter_candidates(row["id"], batch_size=3))) == row["retained"]


def test_per_pair_ceiling_bounds_failed_and_valid_operations(tmp_path, parents):
    store = CampaignStore(tmp_path)
    row = finish(store, store.create({"max_products_per_pair": 1}, parents)["id"])
    assert row["attempted"] <= row["total_pairs"]
    assert row["status"] == "exhausted"
    assert row["pairs_visited"] == row["total_pairs"]


def test_recovery_lists_all_runnable_campaigns_beyond_recent_history_limit(tmp_path):
    store = CampaignStore(tmp_path)
    seeds = [{"smiles": "CCO", "category": "herbal"}, {"smiles": "CCCC", "category": "drug"}]
    ids = [store.create({}, seeds)["id"] for _ in range(55)]
    store.pause(ids[0])
    store.cancel(ids[1])
    assert len(store.list()) == 50
    assert {row["id"] for row in store.active()} == set(ids[2:])


def test_worker_failure_is_durable_and_terminal_with_a_bounded_diagnostic(tmp_path, parents):
    store = CampaignStore(tmp_path)
    row = store.create({}, parents)
    failed = store.fail(row["id"], RuntimeError("enumeration failed"))
    assert failed["status"] == "failed"
    assert failed["reason"] == "worker_error: RuntimeError: enumeration failed"
    assert CampaignStore(tmp_path).get(row["id"])["reason"] == failed["reason"]
    assert store.resume(row["id"])["status"] == "failed"
    assert store.run_batch(row["id"])["attempted"] == 0
    assert store.active() == []
    other = store.create({}, parents)
    assert len(store.fail(other["id"], "x" * 5000)["reason"]) == 2000
    cancelled = store.create({}, parents)
    store.cancel(cancelled["id"])
    assert store.fail(cancelled["id"], "late worker failure")["status"] == "cancelled"
