"""Progress is derived only from explicit log events, using no AF3/GPU execution."""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from herbfold import af3_execution, alphafold
from herbfold.af3_execution import AF3Queue, process_is_alive
from herbfold.af3_progress import READ_LIMIT_BYTES, ProgressObserver
from herbfold.storage import Store


def append(path, text):
    with path.open("a") as handle:
        handle.write(text + "\n")


def test_explicit_inference_events_use_observation_time_and_keep_quiet_phase(tmp_path):
    path = tmp_path / "run.log"
    observer = ProgressObserver("inference")
    cases = [
        ("Featurising data with 1 seed(s)...", "feature_preparation"),
        ("Running model inference with seed 1...", "model_inference"),
        ("Extracting inference results with seed 1...", "result_extraction"),
        ("Writing outputs with 1 seed(s)...", "writing_outputs"),
        ("Fold job fixture done, output written to /tmp/synthetic", "job_finished"),
    ]
    for index, (line, name) in enumerate(cases):
        append(path, line)
        observed = f"2026-09-14T03:05:{index:02d}+00:00"
        detail = observer.observe(path, observed)
        assert detail["name"] == name
        assert detail["observed_at"] == observed
        assert detail["timestamp_kind"] == "observed"
        assert detail["source"] == "run.log"
        assert detail["log_updated_at"]
        assert observer.observe(path, "later") == detail
    append(path, "Featurising data with seed 1.")
    assert observer.observe(path, "still later") == detail


def test_unknown_autotuning_and_hlo_do_not_claim_a_compilation_phase(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("W0914 12:03:00 op.py:517] Autotuning cache miss\nHloModule jit_inference\nGPU 100%\n")
    observer = ProgressObserver("inference")
    assert observer.observe(path, "now") is None
    append(path, "Running model inference with seed 1...")
    expected = observer.observe(path, "started")
    append(path, "x" * (READ_LIMIT_BYTES * 2))
    assert observer.observe(path, "after huge unknown output") == expected


def test_multiple_seeds_progress_in_observed_order_without_numeric_assumptions(tmp_path):
    path = tmp_path / "run.log"
    observer = ProgressObserver("inference")
    append(path, "Running model inference with seed 42...")
    assert observer.observe(path, "a")["seed_ordinal"] == 1
    append(path, "Extracting inference results with seed 42...")
    assert observer.observe(path, "b")["name"] == "result_extraction"
    append(path, "Running model inference with seed 1...")
    second = observer.observe(path, "c")
    assert second["name"] == "model_inference"
    assert second["seed"] == 1
    assert second["seed_ordinal"] == 2
    append(path, "Running model inference with seed 42...")
    assert observer.observe(path, "old event") == second


def test_explicit_parallel_search_completions_accumulate_without_percentage(tmp_path):
    path = tmp_path / "run.log"
    observer = ProgressObserver("data_pipeline")
    databases = [
        ("bfd-first_non_consensus_sequences.fasta", "BFD", 95.538),
        ("mgy_clusters_2022_05.fa", "MGnify", 584.575),
        ("uniref90_2022_05.fa", "UniRef90", 617.244),
        ("uniprot_all_2021_04.fa", "UniProt", 863.605),
    ]
    for database, _, _ in databases:
        append(
            path,
            f'I0914 11:47:28.1 123 subprocess_utils.py:91] Launching subprocess "/tools/jackhmmer -A /tmp/output.sto /tmp/query.fasta /databases/{database}"',
        )
    initial = observer.observe(path, "started")
    assert initial["name"] == "msa_search"
    assert initial["completed_database_count"] == 0
    for index, (database, name, duration) in enumerate(databases):
        append(
            path,
            f"I0914 11:49:03.1 123 subprocess_utils.py:120] Finished Jackhmmer ({database}) in {duration} seconds",
        )
        detail = observer.observe(path, f"completed {index}")
        assert detail["completed_database_count"] == index + 1
        assert detail["completed_databases"][-1] == name
        assert detail["duration_seconds"] == duration
        assert detail["event"] == "search_completed"
        assert "percent" not in detail and "eta" not in detail
    append(
        path,
        'I0914 12:01:59.1 123 subprocess_utils.py:91] Launching subprocess "/tools/hmmsearch --cpu 8 /tmp/query.hmm /databases/pdb_seqres_2022_09_28.fasta"',
    )
    templates = observer.observe(path, "template search")
    assert templates["name"] == "template_search"
    assert templates["activity"] == "PDB 구조 서열 검색 시작"
    assert templates["completed_database_count"] == 4
    append(
        path,
        "I0914 12:02:22.1 123 subprocess_utils.py:120] Finished Hmmsearch (pdb_seqres_2022_09_28.fasta) in 23.027 seconds",
    )
    assert observer.observe(path, "template finished")["duration_seconds"] == 23.027


def test_unknown_database_never_inflates_known_search_completion_count(tmp_path):
    path = tmp_path / "run.log"
    path.write_text(
        "I0914 00:00:00 123 subprocess_utils.py:120] Finished Jackhmmer (unknown.fa) in 1.0 seconds\n"
    )
    detail = ProgressObserver("data_pipeline").observe(path, "now")
    assert detail["completed_databases"] == []
    assert detail["completed_database_count"] == 0


@pytest.mark.parametrize("change", ["truncate", "replace", "delete"])
def test_log_changes_and_read_failure_preserve_previous_observation(tmp_path, change):
    path = tmp_path / "run.log"
    path.write_text("Running model inference with seed 1...\n")
    observer = ProgressObserver("inference")
    prior = observer.observe(path, "first")
    if change == "truncate":
        path.write_text("Featurising data with 1 seed(s)...\n")
    elif change == "replace":
        path.rename(tmp_path / "old.log")
        path.write_text("Featurising data with 1 seed(s)...\n")
    else:
        path.unlink()
    assert observer.observe(path, "second") == prior
    if change != "delete":
        append(path, "Extracting inference results with seed 1...")
        assert observer.observe(path, "third")["name"] == "result_extraction"


def test_stage_offsets_and_new_observer_prevent_prior_stage_leakage(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("Fold job fixture done, output written to /tmp/data\n")
    offset = path.stat().st_size
    observer = ProgressObserver("inference", log_start=offset)
    assert observer.observe(path, "new stage") is None
    append(
        path,
        "I0914 00:00:00 123 subprocess_utils.py:120] Finished Jackhmmer (uniref90_2022_05.fa) in 1.0 seconds",
    )
    assert observer.observe(path, "wrong stage line") is None
    append(path, "Featurising data with 1 seed(s)...")
    detail = observer.observe(path, "feature phase")
    assert detail["name"] == "feature_preparation"
    assert "completed_databases" not in detail


def test_split_lines_are_observed_only_after_complete_and_large_tail_is_bounded(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("Running model inference with seed ")
    observer = ProgressObserver("inference")
    assert observer.observe(path, "partial") is None
    with path.open("a") as handle:
        handle.write("1...\n")
    assert observer.observe(path, "complete")["name"] == "model_inference"
    append(path, "x" * READ_LIMIT_BYTES * 3 + "\nWriting outputs with 1 seed(s)...")
    assert observer.observe(path, "tail event")["name"] == "writing_outputs"


@pytest.mark.parametrize("device", ["gpu", "cpu"])
def test_docker_child_explicitly_receives_unbuffered_python(tmp_path, monkeypatch, device):
    monkeypatch.setenv("PYTHONUNBUFFERED", "0")
    config = alphafold.AF3Config(runner="docker", model_dir=tmp_path, database_dir=tmp_path, device=device)
    command, _ = alphafold.build_command(config, tmp_path / "input.json", tmp_path / "output")
    assert "PYTHONUNBUFFERED=1" in command
    assert command[command.index("PYTHONUNBUFFERED=1") - 1] == "--env"


def test_running_native_child_print_is_visible_before_exit_and_persisted(tmp_path, monkeypatch):
    store = Store(tmp_path / "journal")
    queue = AF3Queue(store)
    job = store.create("alphafold", {"synthetic_cpu_contract_test": True})
    plan = {"execution": {}}
    release = tmp_path / "release"
    monkeypatch.setattr(alphafold, "execution_environment", lambda: {**os.environ, "PYTHONUNBUFFERED": "0"})
    monkeypatch.setattr(af3_execution, "PROCESS_HEARTBEAT_SECONDS", 0.01)
    # No flush=True: the worker's actual environment must make this visible.
    script = (
        "import pathlib, time\n"
        "print('Running model inference with seed 1...')\n"
        f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(0.01)\n"
        "print('Fold job fixture done, output written to /tmp/synthetic')\n"
    )

    def run():
        with (store.root / "test.lock").open("a") as lock:
            return queue._run_process(
                job["id"], plan, [sys.executable, "-c", script], None, "inference", lock, time.monotonic() + 5
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(run)
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    current = store.get(job["id"])
                    result = current.get("result") or {}
                    if result.get("stage", {}).get("detail"):
                        break
                    time.sleep(0.01)
                assert current["status"] == "running"
                assert result["stage"]["detail"]["name"] == "model_inference"
                assert result["execution"]["steps"][0]["execution_environment"]["PYTHONUNBUFFERED"] == "1"
                assert process_is_alive(result["execution"]["child"])
                assert not pending.done()
            finally:
                release.touch()
            step = pending.result(timeout=5)
            assert step["return_code"] == 0
            assert step["detail"]["name"] == "job_finished"
    finally:
        queue.close()
