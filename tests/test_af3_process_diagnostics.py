"""Child monitoring contracts use tiny CPU subprocesses, never real AF3 or GPUs."""

import copy
import os
import sys
import time

import pytest

from herbfold import af3_execution, alphafold
from herbfold.af3_execution import AF3Queue
from herbfold.af3_failure import (
    LOG_EVIDENCE_CHARS,
    LOG_SCAN_BYTES,
    AllocationRetryWatchdog,
    describe_process_failure,
    has_gpu_allocation_warning,
)
from herbfold.storage import Store

ALLOCATION_WARNING = (
    "E0914 cuda_executor.cc:1182] Failed to allocate device memory of 1.00GiB: "
    "RESOURCE_EXHAUSTED: : CUDA_ERROR_OUT_OF_MEMORY: out of memory"
)
FATAL_OOM = (
    "jaxlib._jax.XlaRuntimeError: RESOURCE_EXHAUSTED: "
    "Out of memory while trying to allocate 301989888 bytes."
)


@pytest.mark.parametrize(
    "fatal",
    [
        FATAL_OOM,
        "RuntimeError: CUDA out of memory. Tried to allocate 128 MiB.",
        "RESOURCE_EXHAUSTED: Failed to allocate request for 301989888 bytes.",
    ],
)
def test_terminal_gpu_memory_failure_has_actionable_evidence(tmp_path, fatal):
    log = tmp_path / "run.log"
    log.write_text(ALLOCATION_WARNING + "\nTraceback (most recent call last):\n" + fatal)
    result = describe_process_failure(log, "inference", 1, device="gpu")
    assert result["code"] == "gpu_out_of_memory"
    assert result["evidence"] == fatal
    assert "GPU 메모리를 확보" in result["message"]
    assert result["gpu_allocation_warning"]


def test_allocator_retries_do_not_prove_cause_of_an_unrelated_exit(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(ALLOCATION_WARNING + "\nValueError: invalid input shape\n")
    result = describe_process_failure(log, "inference", 1)
    assert result["code"] == "process_exit"
    assert result["evidence"] is None
    assert result["gpu_allocation_warning"]
    assert "GPU 메모리 할당 경고" in result["message"]
    with pytest.raises(ValueError, match="successful"):
        describe_process_failure(log, "inference", 0)


def test_cpu_allocation_error_does_not_claim_gpu_failure(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(FATAL_OOM)
    result = describe_process_failure(log, "data_pipeline", 1)
    assert result["code"] == "process_exit"
    assert not result["gpu_allocation_warning"]
    cpu_inference = describe_process_failure(log, "inference", 1, device="cpu")
    assert cpu_inference["code"] == "process_exit"
    assert not cpu_inference["gpu_allocation_warning"]


def test_bounded_diagnostics_exclude_prior_stage_and_old_log_content(tmp_path):
    log = tmp_path / "run.log"
    prior = (ALLOCATION_WARNING + "\n" + FATAL_OOM + "\n").encode()
    log.write_bytes(prior + b"current stage normal log\n")
    assert not has_gpu_allocation_warning(log, log_start=len(prior))
    result = describe_process_failure(log, "inference", 7, log_start=len(prior))
    assert result["code"] == "process_exit"
    assert result["log_tail"] == "current stage normal log\n"
    log.write_bytes(prior + b"x" * (LOG_SCAN_BYTES + 100) + b"\xff\nfinal exception")
    result = describe_process_failure(log, "inference", 7)
    assert result["code"] == "process_exit"
    assert len(result["log_tail"]) <= LOG_EVIDENCE_CHARS
    assert result["log_tail"].endswith("\ufffd\nfinal exception")
    assert not result["gpu_allocation_warning"]


def test_missing_log_does_not_mask_child_failure(tmp_path):
    result = describe_process_failure(tmp_path / "missing.log", "inference", -9)
    assert result["return_code"] == -9
    assert result["code"] == "process_exit"
    assert result["log_tail"] == ""


def test_retry_budget_requires_sustained_fresh_warnings_without_other_progress(tmp_path):
    log = tmp_path / "run.log"
    watcher = AllocationRetryWatchdog(30)

    def observe(text, now):
        with log.open("a") as handle:
            handle.write(text + "\n")
        return watcher.observe(log, now)

    assert not observe(ALLOCATION_WARNING, 0)
    assert not observe(ALLOCATION_WARNING, 10)
    # Real progress resets the budget even if the same read also has warnings.
    assert not observe("Running model inference\n" + ALLOCATION_WARNING, 20)
    assert not observe(ALLOCATION_WARNING, 30)
    assert not observe(ALLOCATION_WARNING, 40)
    assert not observe(ALLOCATION_WARNING, 50)
    assert observe(ALLOCATION_WARNING, 60)


def test_silence_and_missing_log_cannot_exhaust_allocation_retry_budget(tmp_path):
    log = tmp_path / "run.log"
    watcher = AllocationRetryWatchdog(30)
    log.write_text(ALLOCATION_WARNING + "\n")
    assert not watcher.observe(log, 0)
    assert not watcher.observe(log, 100)
    with log.open("a") as handle:
        handle.write(ALLOCATION_WARNING + "\n")
    assert not watcher.observe(log, 101)
    assert watcher.since == 101
    log.unlink()
    assert not watcher.observe(log, 120)
    assert watcher.since is None


def test_recovery_message_containing_error_name_is_progress_not_allocator_retry(tmp_path):
    log = tmp_path / "run.log"
    watcher = AllocationRetryWatchdog(30)
    log.write_text(ALLOCATION_WARNING + "\n")
    assert not watcher.observe(log, 0)
    with log.open("a") as handle:
        handle.write("Recovered from CUDA_ERROR_OUT_OF_MEMORY; model inference is proceeding\n")
    assert not watcher.observe(log, 30)
    assert watcher.since is None


@pytest.mark.parametrize("change", ["truncated", "replaced", "large_unread_gap"])
def test_unreliable_log_observation_resets_retry_budget(tmp_path, change):
    log = tmp_path / "run.log"
    watcher = AllocationRetryWatchdog(30)
    log.write_text(ALLOCATION_WARNING + "\n")
    assert not watcher.observe(log, 0)
    if change == "truncated":
        log.write_text("")
    elif change == "replaced":
        log.rename(tmp_path / "old.log")
        log.write_text(ALLOCATION_WARNING + "\n")
    else:
        with log.open("a") as handle:
            handle.write("g" * LOG_SCAN_BYTES + "\n" + ALLOCATION_WARNING + "\n")
    assert not watcher.observe(log, 30)
    assert watcher.since is None


@pytest.fixture
def monitored_child(tmp_path, monkeypatch):
    store = Store(tmp_path / "journal")
    queue = AF3Queue(store)
    monkeypatch.setattr(alphafold, "execution_environment", lambda: dict(os.environ))
    monkeypatch.setattr(af3_execution, "PROCESS_HEARTBEAT_SECONDS", 0.01)
    updates = []
    original_update = store.update

    def recording_update(*args, **kwargs):
        result = original_update(*args, **kwargs)
        updates.append(copy.deepcopy(result))
        return result

    monkeypatch.setattr(store, "update", recording_update)

    def run(script, timeout=5, previous_log="", device="gpu"):
        job = store.create("alphafold", {"synthetic_cpu_test": True})
        plan = {"execution": {}, "msa_features": {"status": "cached"}}
        (store.directory(job["id"]) / "run.log").write_text(previous_log)
        with (store.root / "test.lock").open("a") as lock:
            step = queue._run_process(
                job["id"],
                plan,
                [sys.executable, "-c", script, f"--jax_backend={device}"],
                None,
                "inference",
                lock,
                time.monotonic() + timeout,
            )
        return step, plan, store.get(job["id"]), updates

    yield run
    queue.close()


def test_live_allocator_warning_reports_pressure_but_does_not_stop_success(monitored_child):
    step, plan, job, updates = monitored_child(
        f"import time; print({ALLOCATION_WARNING!r}, flush=True); time.sleep(0.7)"
    )
    warnings = [update for update in updates if update["result"].get("runtime_warning")]
    assert warnings
    assert all(update["status"] == "running" and update["error"] is None for update in warnings)
    warning = warnings[-1]["result"]["runtime_warning"]
    assert warning["code"] == "gpu_memory_pressure"
    assert warning["checked_at"] >= warning["observed_at"]
    assert warnings[-1]["result"]["stage"]["checked_at"] == warning["checked_at"]
    assert step["return_code"] == 0
    assert "failure" not in plan["execution"]
    assert "runtime_warning" not in plan


def test_nonzero_child_persists_failure_and_finish_metadata(monitored_child):
    step, plan, job, updates = monitored_child(
        f"import sys; print({FATAL_OOM!r}, flush=True); sys.exit(3)"
    )
    assert step is None
    assert job["status"] == "failed"
    assert "GPU 메모리가 부족" in job["error"]
    execution = job["result"]["execution"]
    assert execution["failure"]["code"] == "gpu_out_of_memory"
    assert execution["return_code"] == 3
    assert execution["finished_at"]
    assert execution["steps"][-1]["failure"] == execution["failure"]


def test_prior_stage_oom_does_not_taint_later_success_or_failure(monitored_child):
    step, plan, job, updates = monitored_child(
        "import time, sys; print('ordinary error', flush=True); time.sleep(0.4); sys.exit(7)",
        previous_log=ALLOCATION_WARNING + "\n" + FATAL_OOM + "\n",
    )
    assert job["status"] == "failed"
    assert all("runtime_warning" not in update["result"] for update in updates)
    failure = job["result"]["execution"]["failure"]
    assert failure["code"] == "process_exit"
    assert not failure["gpu_allocation_warning"]
    assert ALLOCATION_WARNING not in failure["log_tail"]


def test_timeout_keeps_warning_as_observation_not_proven_oom(monitored_child):
    step, plan, job, updates = monitored_child(
        f"import time; print({ALLOCATION_WARNING!r}, flush=True); time.sleep(30)", timeout=0.4
    )
    assert step is None
    assert job["status"] == "failed"
    failure = job["result"]["execution"]["failure"]
    assert failure["code"] == "execution_timeout"
    assert failure["gpu_allocation_warning"]
    assert "AF3_TIMEOUT_SECONDS" in job["error"]


def test_sustained_retry_budget_stops_only_the_monitored_child(monitored_child, monkeypatch):
    # Accelerate only this policy clock for the synthetic subprocess contract.
    monkeypatch.setattr(
        af3_execution,
        "AllocationRetryWatchdog",
        lambda timeout, **kwargs: AllocationRetryWatchdog(0.3, **kwargs),
    )
    script = (
        "import time\n"
        "for _ in range(100):\n"
        f"    print({ALLOCATION_WARNING!r}, flush=True)\n"
        "    time.sleep(0.1)\n"
    )
    step, plan, job, updates = monitored_child(script)
    assert step is None
    assert job["status"] == "failed"
    failure = job["result"]["execution"]["failure"]
    assert failure["code"] == "resource_retry_timeout"
    assert failure["retry_timeout_seconds"] == 180
    assert failure["gpu_allocation_warning"]
    assert failure["evidence"] is None
    assert job["result"]["execution"]["return_code"] < 0
    assert "GPU 메모리 할당 재시도만" in job["error"]


def test_cpu_inference_disables_gpu_retry_watchdog(monitored_child, monkeypatch):
    created_timeouts = []

    def watcher(timeout, **kwargs):
        created_timeouts.append(timeout)
        return AllocationRetryWatchdog(timeout, **kwargs)

    monkeypatch.setattr(af3_execution, "AllocationRetryWatchdog", watcher)
    step, plan, job, updates = monitored_child("print('CPU inference finished')", device="cpu")
    assert created_timeouts == [0]
    assert step["return_code"] == 0
