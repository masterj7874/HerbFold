"""Queue integration uses an explicitly synthetic CPU script, never AF3/GPU data."""

import fcntl
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from herbfold import af3_databases, af3_parameters, alphafold, molecular_selection
from herbfold.af3_execution import AF3Queue, process_identity
from herbfold.af3_studio import StudioPredictionRequest, StudioPredictions
from herbfold.api import create_app
from herbfold.storage import Store

SYNTHETIC_RUNNER = r"""
import json, os, pathlib, sys, time
from collections import Counter
from rdkit import Chem
args = dict(a[2:].split("=", 1) for a in sys.argv[1:])
data = json.loads(pathlib.Path(args["json_path"]).read_text())
out = pathlib.Path(args["output_dir"])
mode = os.environ.get("SYNTHETIC_TEST_RUNNER_MODE", "ok")
print("SYNTHETIC CPU CONTRACT TEST; NOT A SCIENTIFIC PREDICTION", flush=True)
if mode == "delay": time.sleep(30)
if mode == "failed": sys.exit(7)
if mode == "empty": sys.exit(0)
if args.get("run_inference") == "false":
    assert args["run_data_pipeline"] == "true" and args["jax_backend"] == "cpu"
    assert os.environ.get("CUDA_VISIBLE_DEVICES") == ""
    protein = data["sequences"][0]["protein"]
    assert not {"unpairedMsa", "pairedMsa", "templates"}.intersection(protein)
    sequence = protein["sequence"]
    protein.update(unpairedMsa=">SYNTHETIC_QUERY\\n" + sequence + "\\n>SYNTHETIC_HIT\\n" + sequence[:-1] + "-\\n", pairedMsa=">SYNTHETIC_QUERY\\n" + sequence + "\\n", templates=[])
    protein["unpairedMsa"] = protein["unpairedMsa"].replace("\\n", "\n")
    protein["pairedMsa"] = protein["pairedMsa"].replace("\\n", "\n")
    if mode == "corrupt_msa": protein["unpairedMsa"] = ">SYNTHETIC_WRONG_QUERY\nCCC\n"
    (out / (data["name"].lower() + "_data.json")).write_text(json.dumps(data))
    sys.exit(0)
smiles = "CCN" if mode == "wrong_ligand" else data["sequences"][1]["ligand"]["smiles"]
text = "data_synthetic\n_chem_comp.id LIG_B\n_chem_comp.pdbx_smiles '" + smiles + "'\nloop_\n"
cols = ["type_symbol", "label_atom_id", "label_comp_id", "label_asym_id", "label_seq_id", "auth_seq_id", "Cartn_x", "Cartn_y", "Cartn_z", "B_iso_or_equiv"]
text += "\n".join("_atom_site." + key for key in cols) + "\n"
rows = [("C", "CA", res, "A", i, i, i * 4, 0, 0, 60) for i, res in enumerate(("ALA", "CYS", "ASP"), 1)]
counts = Counter()
for i, atom in enumerate(Chem.MolFromSmiles(smiles).GetAtoms()):
    symbol = atom.GetSymbol()
    counts[symbol] += 1
    rows.append((symbol, symbol.upper() + str(counts[symbol]), "LIG_B", "B", ".", 1, i * 1.4, 4, 0, 70))
text += "\n".join(" ".join(map(str, row)) for row in rows) + "\n#\n"
(out / "synthetic_model.cif").write_text(text)
(out / "synthetic_summary_confidences.json").write_text(json.dumps({"ptm":0.4,"iptm":0.3,"ranking_score":0.32,"has_clash":False}))
"""


@pytest.fixture
def configured(tmp_path, monkeypatch):
    repo = tmp_path / "SYNTHETIC_CPU_RUNNER_NOT_AF3"
    repo.mkdir()
    (repo / "run_alphafold.py").write_text(SYNTHETIC_RUNNER)
    config = alphafold.AF3Config(
        repo_dir=repo, python_bin=sys.executable, model_dir=tmp_path, database_dir=tmp_path
    )
    monkeypatch.setattr(alphafold.AF3Config, "from_env", classmethod(lambda cls: config))
    monkeypatch.setattr(
        alphafold,
        "capabilities",
        lambda *a, **kw: {
            "runnable": True,
            "ready": True,
            "blockers": [],
            "warnings": [],
            "provenance": {
                "test_fixture": True,
                "parameters": {"stat_fingerprint_sha256": "synthetic-test-only"},
                "databases": {"fingerprint_sha256": "f" * 64},
            },
        },
    )
    monkeypatch.setattr(
        af3_parameters,
        "inspect_parameters",
        lambda config: {
            "status": "test_fixture_only",
            "runnable": True,
            "blockers": [],
            "provenance": {"stat_fingerprint_sha256": "synthetic-test-only"},
        },
    )
    monkeypatch.setattr(
        af3_databases,
        "inspect_databases",
        lambda config: {
            "status": "test_fixture_only",
            "runnable": True,
            "blockers": [],
            "provenance": {"fingerprint_sha256": "f" * 64},
        },
    )
    monkeypatch.setattr(
        molecular_selection, "_target_sequence", lambda acc: "ACD" if acc == "P35354" else None
    )
    monkeypatch.setattr(
        molecular_selection,
        "registered_target",
        lambda store, accession: {
            "accession": "P35354",
            "sequence": "ACD",
            "sequence_sha256": hashlib.sha256(b"ACD").hexdigest(),
            "name": "Synthetic CPU fixture target",
            "length": 3,
            "source": "synthetic_test_fixture",
        } if accession == "P35354" else None,
    )
    monkeypatch.setenv("SYNTHETIC_TEST_RUNNER_MODE", "ok")
    return config


@pytest.fixture
def service(tmp_path, configured):
    store = Store(tmp_path / "journal")
    queue = AF3Queue(store)
    service = StudioPredictions(store, queue)
    yield service
    queue.close()


def request(smiles="CCO", **kwargs):
    return StudioPredictionRequest(smiles=smiles, **kwargs)


def wait_status(service, job_id, states, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = service.get(job_id)
        if result["job"]["status"] in states:
            if result["job"]["status"] != "completed" or result["output_validation"] is not None:
                return result
        time.sleep(0.025)
    pytest.fail(f"Job did not reach {states}: {service.get(job_id)['job']}")


def test_preparation_is_explicit_and_identity_deduplicated(service):
    with ThreadPoolExecutor(max_workers=6) as pool:
        prepared = list(pool.map(lambda _: service.prepare(request("OCC")), range(6)))
    assert len({item["job"]["id"] for item in prepared}) == 1
    assert all(item["job"]["status"] == "prepared" for item in prepared)
    state = prepared[0]
    assert service.queue._thread is None
    assert state["requested"]["canonical_smiles"] == "CCO"
    assert state["requested"]["execution_profile"]["num_diffusion_samples"] == 5
    assert state["requested"]["execution_profile"]["num_recycles"] == 10
    protein = state["job"]["payload"]["sequences"][0]["protein"]
    assert "unpairedMsa" not in protein and "templates" not in protein
    assert service.prepare(request("CCO"))["job"]["id"] == state["job"]["id"]
    assert service.prepare(request("CCN"))["job"]["id"] != state["job"]["id"]
    left = service.prepare(request("C[C@H](O)F"))["job"]["id"]
    right = service.prepare(request("C[C@@H](O)F"))["job"]["id"]
    assert left != right


def test_no_msa_requires_acknowledgement_and_separate_input(service):
    with pytest.raises(ValueError, match="exploratory_ack"):
        service.prepare(request(msa_mode="none"))
    standard = service.prepare(request())
    free = service.prepare(request(msa_mode="none", exploratory_ack=True))
    assert standard["job"]["id"] != free["job"]["id"]
    protein = free["job"]["payload"]["sequences"][0]["protein"]
    assert protein["unpairedMsa"] == "" and protein["templates"] == []
    assert any("정확도" in warning for warning in free["readiness"]["warnings"])
    with pytest.raises(ValueError, match="서열"):
        service.prepare(request(target_accession="UNKNOWN"))


def test_registered_non_cox_target_prepares_exact_input_and_freezes_provenance(service, monkeypatch):
    target = {
        "accession": "P23219", "sequence": "ACG", "length": 3,
        "sequence_sha256": hashlib.sha256(b"ACG").hexdigest(),
        "name": "Synthetic alternate target", "organism": "Synthetic fixture only",
        "source": "synthetic_uniprot_fixture", "retrieved_at": "2026-01-01T00:00:00Z",
        "registered_at": "2026-01-02T00:00:00Z",
        "provenance": {"kind": "synthetic_test_fixture"},
    }
    monkeypatch.setattr(
        molecular_selection, "registered_target",
        lambda store, accession: dict(target) if accession in {"P23219", "Q16875"} else None,
    )
    prepared = service.prepare(request(target_accession="Q16875"))
    assert prepared["requested"]["target_accession"] == "P23219"
    assert prepared["requested"]["submitted_target_accession"] == "Q16875"
    assert prepared["requested"]["target_sequence_sha256"] == target["sequence_sha256"]
    assert prepared["requested"]["target_provenance"] == {
        key: value for key, value in target.items() if key != "sequence"
    }
    assert prepared["job"]["payload"]["sequences"][0]["protein"]["sequence"] == "ACG"
    manifest = json.loads((service.store.directory(prepared["job"]["id"]) / "af3_manifest.json").read_text())
    assert manifest["input_sha256"]
    assert manifest["target_sequence_sha256"] == target["sequence_sha256"]
    assert manifest["target_provenance"] == prepared["requested"]["target_provenance"]
    # The durable job plan and request preserve the target provenance with the original input.
    assert prepared["job"]["result"]["target_provenance"] == prepared["requested"]["target_provenance"]
    assert service.list("CCO", "P23219")["items"][0]["job"]["id"] == prepared["job"]["id"]
    assert service.list("CCO", "Q16875")["items"][0]["job"]["id"] == prepared["job"]["id"]
    assert service.prepare(request(target_accession="P23219"))["job"]["id"] == prepared["job"]["id"]
    target.update(sequence="ACD", sequence_sha256=hashlib.sha256(b"ACD").hexdigest())
    changed = service.prepare(request(target_accession="P23219"))
    assert changed["job"]["id"] != prepared["job"]["id"]
    original = service.get(prepared["job"]["id"])
    assert original["requested"]["target_sequence_sha256"] == hashlib.sha256(b"ACG").hexdigest()
    assert molecular_selection.frozen_target_sequence(original["job"], original["requested"]) == "ACG"
    assert service.queue._thread is None


def test_registration_hash_mismatch_is_rejected_before_job_creation(service, monkeypatch):
    monkeypatch.setattr(molecular_selection, "registered_target", lambda *args: {
        "accession": "P23219", "sequence": "ACD", "sequence_sha256": "0" * 64,
    })
    with pytest.raises(ValueError, match="해시"):
        service.prepare(request(target_accession="P23219"))
    assert service.store.list() == []


def test_blocked_preflight_has_no_fallback_or_launch(service, monkeypatch):
    monkeypatch.setattr(
        alphafold,
        "capabilities",
        lambda *a, **kw: {
            "runnable": False,
            "ready": False,
            "blockers": ["Parameters are synthetic performance-test weights"],
            "warnings": [],
            "provenance": {},
        },
    )
    prepared = service.prepare(request())
    assert prepared["job"]["status"] == "blocked"
    assert prepared["requested"]["msa_mode"] == "search"
    assert "synthetic" in prepared["readiness"]["blockers"][0]
    with pytest.raises(Exception) as exc:
        service.execute(prepared["job"]["id"])
    assert exc.value.status_code == 409
    assert service.queue._thread is None


def test_actual_cpu_subprocess_queue_finishes_with_selected_output(service):
    first = service.prepare(request())
    second = service.prepare(request("CCN"))
    for state in (first, second):
        service.execute(state["job"]["id"])
        service.execute(state["job"]["id"])  # idempotent repeated click
    completed = [wait_status(service, item["job"]["id"], {"completed"}) for item in (first, second)]
    assert [step["name"] for step in completed[0]["job"]["result"]["execution"]["steps"]] == [
        "data_pipeline",
        "inference",
    ]
    assert [step["name"] for step in completed[1]["job"]["result"]["execution"]["steps"]] == ["inference"]
    original_features, reused_features = [item["msa_features"] for item in completed]
    assert original_features["cache_hit"] is False and reused_features["cache_hit"] is True
    assert original_features["feature_sha256"] == reused_features["feature_sha256"]
    assert reused_features["source_job_id"] == first["job"]["id"]
    assert reused_features["unpaired_msa_sequences"] == 2
    for item in completed:
        directory = service.store.directory(item["job"]["id"])
        original_input = json.loads((directory / "fold_input.json").read_text())
        inference_input = json.loads((directory / "inference_input.json").read_text())
        assert "unpairedMsa" not in original_input["sequences"][0]["protein"]
        assert inference_input["sequences"][1]["ligand"] == original_input["sequences"][1]["ligand"]
    assert (
        completed[0]["job"]["result"]["execution"]["finished_at"]
        <= completed[1]["job"]["result"]["execution"]["started_at"]
    )
    for item in completed:
        assert item["output_validation"]["identity_verified"] is True
        assert item["output_validation"]["quality_pass"] is None
        assert item["output_validation"]["efficacy"] == "not_assessed"
        scene = service.scene(item["job"]["id"])["scene"]
        assert scene["metadata"]["job_id"] == item["job"]["id"]
        assert scene["metadata"]["smiles"] == item["requested"]["canonical_smiles"]
        assert "SYNTHETIC CPU" in service.log(item["job"]["id"])["text"]


def test_output_mismatch_stays_completed_execution_but_unavailable_prediction(service, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_TEST_RUNNER_MODE", "wrong_ligand")
    item = service.prepare(request())
    service.execute(item["job"]["id"])
    result = wait_status(service, item["job"]["id"], {"completed"})
    assert result["job"]["result"]["execution_verified"] is True
    assert result["output_validation"]["status"] == "identity_failed"
    assert result["output_validation"]["quality_pass"] is False
    assert service.scene(item["job"]["id"])["status"] == "unavailable"


def test_invalid_msa_output_blocks_inference_and_never_becomes_ready_cache(service, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_TEST_RUNNER_MODE", "corrupt_msa")
    prepared = service.prepare(request())
    service.execute(prepared["job"]["id"])
    result = wait_status(service, prepared["job"]["id"], {"failed"})
    assert result["msa_features"]["status"] == "failed"
    assert [step["name"] for step in result["job"]["result"]["execution"]["steps"]] == ["data_pipeline"]
    assert not list((service.store.root / "af3-protein-features").glob("*/manifest.json"))
    assert not (service.store.directory(prepared["job"]["id"]) / "inference_input.json").exists()


def test_completed_features_survive_worker_restart_and_damaged_cache_is_researched(service):
    first = service.prepare(request())
    service.execute(first["job"]["id"])
    completed = wait_status(service, first["job"]["id"], {"completed"})
    service.queue.close()
    service.queue = AF3Queue(service.store)
    service.queue.after_complete = service.validate_output
    second = service.prepare(request("CCN"))
    service.execute(second["job"]["id"])
    reused = wait_status(service, second["job"]["id"], {"completed"})
    assert reused["msa_features"]["cache_hit"] is True
    cached_file = (
        service.store.root
        / "af3-protein-features"
        / completed["msa_features"]["cache_key"]
        / "protein_features.json"
    )
    cached_file.write_text('{"damaged": true}')
    third = service.prepare(request("CCCO"))
    service.execute(third["job"]["id"])
    repaired = wait_status(service, third["job"]["id"], {"completed"})
    assert repaired["msa_features"]["cache_hit"] is False
    assert repaired["msa_features"]["source_job_id"] == third["job"]["id"]
    assert len(list(cached_file.parent.parent.glob(".invalid-*"))) == 1


def test_databases_changed_during_search_cannot_publish_features(service, monkeypatch):
    prepared = service.prepare(request())
    monkeypatch.setattr(
        af3_databases,
        "inspect_databases",
        lambda config: {"runnable": True, "provenance": {"fingerprint_sha256": "e" * 64}},
    )
    service.execute(prepared["job"]["id"])
    result = wait_status(service, prepared["job"]["id"], {"failed"})
    assert result["msa_features"]["status"] == "failed"
    assert not list((service.store.root / "af3-protein-features").glob("*/manifest.json"))


def test_parameters_changed_during_cpu_search_block_inference_but_preserve_features(service, monkeypatch):
    prepared = service.prepare(request())
    monkeypatch.setattr(
        af3_parameters,
        "inspect_parameters",
        lambda config: {
            "runnable": True,
            "provenance": {"stat_fingerprint_sha256": "changed-after-preflight"},
        },
    )
    service.execute(prepared["job"]["id"])
    result = wait_status(service, prepared["job"]["id"], {"failed"})
    assert result["msa_features"]["status"] == "ready"
    steps = result["job"]["result"]["execution"]["steps"]
    assert [step["name"] for step in steps] == ["data_pipeline"]
    assert steps[0]["execution_environment"]["CUDA_VISIBLE_DEVICES"] == ""
    assert list((service.store.root / "af3-protein-features").glob("*/manifest.json"))


@pytest.mark.parametrize("mode", ["failed", "empty"])
def test_failed_process_can_only_retry_in_new_job(service, monkeypatch, mode):
    monkeypatch.setenv("SYNTHETIC_TEST_RUNNER_MODE", mode)
    old = service.prepare(request())
    service.execute(old["job"]["id"])
    wait_status(service, old["job"]["id"], {"failed"})
    assert service.prepare(request())["job"]["id"] == old["job"]["id"]
    retry = service.prepare(request(retry=True))
    assert retry["job"]["id"] != old["job"]["id"]
    assert retry["retry_of"] == old["job"]["id"]
    assert service.store.get(old["job"]["id"])["status"] == "failed"


def test_execution_rechecks_changed_readiness_before_process(service, monkeypatch):
    prepared = service.prepare(request())
    monkeypatch.setattr(
        alphafold,
        "capabilities",
        lambda *a, **kw: {
            "runnable": False,
            "blockers": ["Model was replaced by random weights"],
            "provenance": {},
        },
    )
    service.execute(prepared["job"]["id"])
    result = wait_status(service, prepared["job"]["id"], {"blocked"})
    assert "Model was replaced by random weights" in result["job"]["error"]
    assert service.log(prepared["job"]["id"])["text"] == ""


def test_parameter_and_child_settings_change_identity_and_block_stale_plan(service, monkeypatch):
    monkeypatch.setenv("AF3_XLA_FLAGS", "--xla_gpu_autotune_level=4")
    original = service.prepare(request())
    monkeypatch.setenv("AF3_XLA_FLAGS", "--xla_gpu_autotune_level=3")
    changed_environment = service.prepare(request())
    assert changed_environment["job"]["id"] != original["job"]["id"]
    service.execute(original["job"]["id"])
    stale = wait_status(service, original["job"]["id"], {"blocked"})
    assert "settings changed" in stale["job"]["error"]
    assert service.log(original["job"]["id"])["text"] == ""
    monkeypatch.setattr(
        af3_parameters,
        "inspect_parameters",
        lambda config: {
            "status": "test_fixture_only",
            "runnable": True,
            "blockers": [],
            "provenance": {"stat_fingerprint_sha256": "different-synthetic-test-only"},
        },
    )
    changed_parameters = service.prepare(request())
    assert changed_parameters["job"]["id"] != changed_environment["job"]["id"]


def test_worker_exception_does_not_strand_next_job(service, monkeypatch):
    first = service.prepare(request())
    second = service.prepare(request("CCN"))
    original = alphafold.prepare_job

    def fail_first(directory, payload, config=None):
        if payload["sequences"][1]["ligand"]["smiles"] == "CCO":
            raise OSError("Synthetic test error")
        return original(directory, payload, config)

    monkeypatch.setattr(alphafold, "prepare_job", fail_first)
    service.execute(first["job"]["id"])
    service.execute(second["job"]["id"])
    failed = wait_status(service, first["job"]["id"], {"failed"})
    assert "OSError" in failed["job"]["error"]
    wait_status(service, second["job"]["id"], {"completed"})


def test_queue_respects_external_orchestration_lock(service):
    prepared = service.prepare(request())
    with (service.store.root / "af3-execution.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        service.execute(prepared["job"]["id"])
        time.sleep(0.15)
        assert service.get(prepared["job"]["id"])["job"]["status"] == "queued"
        assert service.log(prepared["job"]["id"])["text"] == ""
    wait_status(service, prepared["job"]["id"], {"completed"})


def test_queued_restart_resumes_but_running_without_owner_does_not(service):
    queued = service.prepare(request())
    interrupted = service.prepare(request("CCN"))
    service.store.claim(queued["job"]["id"])
    service.store.update(interrupted["job"]["id"], "running", interrupted["job"]["result"])
    # More than Store.list's 100 unrelated jobs must not hide active inference.
    for _ in range(105):
        service.store.create("unrelated", {})
    service.queue.recover()
    assert service.get(interrupted["job"]["id"])["job"]["status"] == "interrupted"
    wait_status(service, queued["job"]["id"], {"completed"})
    assert service.log(interrupted["job"]["id"])["text"] == ""


def test_interrupted_live_child_blocks_duplicate_retry(service):
    item = service.prepare(request())
    plan = item["job"]["result"]
    plan["execution"] = {"child": process_identity()}
    service.store.update(item["job"]["id"], "interrupted", plan)
    assert service.get(item["job"]["id"])["orphan_process_active"] is True
    with pytest.raises(ValueError, match="아직 실행"):
        service.prepare(request(retry=True))


def test_close_stops_only_own_process_and_preserves_retry_record(service, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_TEST_RUNNER_MODE", "delay")
    item = service.prepare(request())
    service.execute(item["job"]["id"])
    wait_status(service, item["job"]["id"], {"running"})
    service.queue.close()
    result = service.get(item["job"]["id"])
    assert result["job"]["status"] == "interrupted"
    assert result["orphan_process_active"] is False


def test_api_reconnects_selected_identity_and_bounds_log(tmp_path, configured):
    with TestClient(create_app(tmp_path / "api")) as client:
        prepared = client.post("/api/molecular/predictions/prepare", json={"smiles": "CCO"}).json()
        job_id = prepared["job"]["id"]
        items = client.get("/api/molecular/predictions", params={"smiles": "OCC"}).json()["items"]
        assert items[0]["job"]["id"] == job_id
        assert client.get("/api/molecular/predictions", params={"smiles": "CCN"}).json()["items"] == []
        assert client.get(prepared["scene_url"]).json()["scene"] is None
        (client.app.state.store.directory(job_id) / "run.log").write_text("a" * 100000)
        log = client.get(prepared["log_url"]).json()
        assert len(log["text"]) == 65536 and log["truncated"] is True
        assert (
            client.post(
                "/api/molecular/predictions/prepare", json={"smiles": "CCO", "msa_mode": "none"}
            ).status_code
            == 422
        )
