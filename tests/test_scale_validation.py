"""Actual chemistry, durable checkpoints and honest large-scale accounting."""

import json

import pytest
from rdkit import Chem

from herbfold.chemistry import load_catalog
from herbfold.discovery_catalog import DiscoveryCatalog
from herbfold.scale_validation import ScaleValidation


@pytest.fixture
def catalog(tmp_path):
    store = DiscoveryCatalog(tmp_path / "catalog")
    rows = load_catalog()
    for role, source in (("herbal", "lotus-2026-04"), ("drug", "chembl-approved")):
        store.ingest_records(
            [row for row in rows if row["category"] == role],
            source_id=source,
            source_url="https://example.org/" + source,
            license_label="test provenance",
        )
    return store


def test_real_unique_candidates_have_compact_fragment_ancestry_and_finite_ceiling(tmp_path, catalog):
    scale = ScaleValidation(tmp_path / "scale", catalog.db)
    prepared = scale.prepare(workers=1, batch_size=2)
    assert prepared["prepared_parents"] == 7
    assert prepared["drug_fragments"] > 0 and prepared["natural_fragments"] > 0
    result = scale.run(1_000_000, workers=1, batch_size=7)
    assert result["status"] == "exhausted"
    assert result["attempted"] == result["compatible_pair_slots_upper_bound"] < 1_000_000
    assert (
        result["attempted"]
        == result["retained_unique"]
        + result["duplicates"]
        + result["rejected"]
        + result["known_parent_matches"]
    )
    assert result["retained_unique"] > 0
    assert (
        result["passed_filters"]
        == result["retained_unique"] + result["duplicates"] + result["known_parent_matches"]
    )
    with scale.connect() as con:
        candidates = con.execute("SELECT smiles,left_fragment,right_fragment FROM candidates").fetchall()
        assert len({item[0] for item in candidates}) == len(candidates) == result["retained_unique"]
        for smiles, left, right in candidates:
            Chem.SanitizeMol(Chem.MolFromSmiles(smiles))
            assert con.execute(
                "SELECT 1 FROM ancestry WHERE fragment_id=? AND role='natural_product'", (left,)
            ).fetchone()
            assert con.execute(
                "SELECT 1 FROM ancestry WHERE fragment_id=? AND role='drug'", (right,)
            ).fetchone()
    audit = scale.audit()
    assert audit["audit"]["actual_unique_rows"] == result["retained_unique"]
    assert len(audit["audit"]["sorted_structure_digest_sha256"]) == 64
    assert audit["projection"]["is_measured"] is False
    assert audit["projection"]["finite_space_allows_target"] is False


def test_preparation_restart_and_generation_target_never_overshoot(tmp_path, catalog):
    root = tmp_path / "resumed"
    scale = ScaleValidation(root, catalog.db)
    first = scale.prepare(workers=1, batch_size=2, max_parents=2)
    assert first["processed_parents"] == 2
    assert first["phase"] == "preparation"
    resumed = ScaleValidation(root, catalog.db)
    final = resumed.prepare(workers=1, batch_size=3)
    assert final["processed_parents"] == final["prepared_parents"] == 7
    first_run = resumed.run(5, workers=1, batch_size=20)
    assert first_run["attempted"] == first_run["cursor"] == 5
    assert first_run["status"] == "completed"
    continued = ScaleValidation(root, catalog.db)
    all_split = continued.run(1000, workers=1, batch_size=3)
    other = ScaleValidation(tmp_path / "whole", catalog.db)
    other.prepare(workers=1)
    all_once = other.run(1000, workers=1)
    for field in (
        "attempted",
        "sanitized",
        "retained_unique",
        "duplicates",
        "rejected",
        "known_parent_matches",
    ):
        assert all_split[field] == all_once[field]
    assert (
        continued.audit()["audit"]["sorted_structure_digest_sha256"]
        == other.audit()["audit"]["sorted_structure_digest_sha256"]
    )


def test_checkpoint_failure_does_not_commit_partial_candidates(tmp_path, catalog, monkeypatch):
    scale = ScaleValidation(tmp_path / "scale", catalog.db)
    scale.prepare(workers=1)
    save = scale._save

    def fail(con, state):
        if state["attempted"]:
            raise InterruptedError("crash at checkpoint")
        return save(con, state)

    monkeypatch.setattr(scale, "_save", fail)
    with pytest.raises(InterruptedError):
        scale.run(1000, workers=1)
    reopened = ScaleValidation(scale.root, catalog.db)
    assert reopened.state()["attempted"] == 0
    with reopened.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    assert reopened.run(1000, workers=1)["retained_unique"] > 0


def test_storage_budget_is_real_and_progress_file_distinguishes_counters(tmp_path, catalog):
    scale = ScaleValidation(tmp_path / "scale", catalog.db)
    scale.prepare(workers=1)
    result = scale.run(1000, workers=1, batch_size=2, max_storage_gb=0.000001)
    assert result["status"] == "budget_exhausted"
    assert result["reason"] == "disk_budget"
    report = json.loads(scale.progress_path.read_text())
    assert report["database_bytes"] > 0
    assert report["attempted"] <= 2
    assert "efficacy" in report["interpretation"]


def test_version_mismatch_and_bad_worker_target_configuration_are_rejected(tmp_path, catalog):
    scale = ScaleValidation(tmp_path / "scale", catalog.db)
    with pytest.raises(ValueError):
        scale.prepare(workers=128)
    with pytest.raises(ValueError):
        scale.run(100_000_001)
    state = scale.state()
    state["rdkit_version"] = "other-version"
    with scale.connect() as con:
        scale._save(con, state)
    with pytest.raises(ValueError, match="version changed"):
        scale.prepare(workers=1)


def test_concurrent_scale_worker_is_rejected_before_mutating_state(tmp_path, catalog):
    import fcntl

    scale = ScaleValidation(tmp_path / "scale", catalog.db)
    with (scale.root / "scale-worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="already active"):
            scale.prepare(workers=1)
    assert scale.state()["processed_parents"] == 0


def test_source_role_snapshot_ignores_provenance_added_after_run_creation(tmp_path, catalog):
    scale = ScaleValidation(tmp_path / "scale", catalog.db)
    aspirin = next(row for row in load_catalog() if row["id"] == "aspirin")
    catalog.ingest_records(
        [aspirin],
        source_id="lotus-2026-04",
        source_url="https://example.org/new-assertion",
        license_label="test",
    )
    result = scale.prepare(workers=1)
    assert result["prepared_parents"] == 7


def test_two_source_roles_or_different_salt_ids_do_not_fake_distinct_parent_structures(tmp_path):
    catalog = DiscoveryCatalog(tmp_path / "catalog")
    parent = "N=C1CCCN1Cc1[nH]c(=O)[nH]c(=O)c1Cl"
    for smiles, source in ((parent, "lotus-2026-04"), (parent + ".[Na+]", "chembl-approved")):
        catalog.ingest_records(
            [{"smiles": smiles}],
            source_id=source,
            source_url="https://example.org/" + source,
            license_label="test",
        )
    scale = ScaleValidation(tmp_path / "scale", catalog.db)
    scale.prepare(workers=1)
    result = scale.run(1000, workers=1)
    assert result["prepared_parents"] == 2
    assert result["lineage_rejected"] > 0
    assert result["retained_unique"] == 0
    assert scale.audit()["audit"]["all_rows_distinct_parent_lineage_failures"] == 0


def test_read_only_audit_does_not_replace_live_checkpoint_or_report(tmp_path, catalog):
    scale = ScaleValidation(tmp_path / "scale", catalog.db)
    scale.prepare(workers=1)
    scale.run(1000, workers=1)
    state = scale.state()
    progress = scale.progress_path.read_bytes()
    report = scale.audit(persist=False)
    assert report["audit"]["all_rows_distinct_parent_lineage_failures"] == 0
    assert scale.state() == state
    assert scale.progress_path.read_bytes() == progress


def test_repeating_achieved_target_preserves_measured_time(tmp_path, catalog):
    scale = ScaleValidation(tmp_path / "scale", catalog.db)
    scale.prepare(workers=1)
    original = scale.run(5, workers=1)
    repeated = scale.run(5, workers=1)
    assert repeated["generation_seconds"] == original["generation_seconds"]
    assert repeated["attempted"] == original["attempted"] == 5
    assert repeated["retained_unique"] == original["retained_unique"]


def test_elapsed_total_survives_legacy_zero_metadata_audits_and_completed_resume(tmp_path, catalog):
    scale = ScaleValidation(tmp_path / "scale", catalog.db)
    scale.prepare(workers=1)
    measured = scale.run(5, workers=1)
    expected = measured["preparation_seconds"] + measured["generation_seconds"]
    assert expected > 0

    # Reproduce the real historical defect in both stored state and live report.
    with scale.connect() as con:
        legacy = json.loads(con.execute("SELECT value FROM metadata WHERE key='state'").fetchone()[0])
        legacy["elapsed_seconds"] = 0.0
        con.execute("UPDATE metadata SET value=? WHERE key='state'", (json.dumps(legacy),))
    cached = json.loads(scale.progress_path.read_text())
    cached["elapsed_seconds"] = 0.0
    scale.progress_path.write_text(json.dumps(cached))
    reopened = ScaleValidation(scale.root, catalog.db)
    assert reopened.state()["elapsed_seconds"] == expected

    read_only = reopened.audit(persist=False)
    assert read_only["elapsed_seconds"] == expected
    # The read-only audit repairs its returned view without silently changing files.
    assert json.loads(reopened.progress_path.read_text())["elapsed_seconds"] == 0.0
    persisted = reopened.audit(persist=True)
    resumed = reopened.run(5, workers=1)
    for result in (persisted, resumed, reopened.state()):
        assert result["elapsed_seconds"] == expected
        assert result["preparation_seconds"] == measured["preparation_seconds"]
        assert result["generation_seconds"] == measured["generation_seconds"]
    with reopened.connect() as con:
        saved = json.loads(con.execute("SELECT value FROM metadata WHERE key='state'").fetchone()[0])
        assert saved["elapsed_seconds"] == expected
