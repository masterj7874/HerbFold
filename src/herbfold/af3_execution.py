"""Durable local AF3 execution queue shared by studio and legacy API submissions.

The advisory execution lock is also used by the analysis orchestrator. A native
child inherits it so a crashed API cannot launch another inference beside an
orphaned child. Interrupted inference is never silently restarted or certified.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import threading
import time
from contextlib import suppress

from . import af3_features, alphafold
from .af3_failure import AllocationRetryWatchdog, describe_process_failure, has_gpu_allocation_warning
from .af3_progress import ProgressObserver
from .storage import utcnow

PROCESS_HEARTBEAT_SECONDS = 10


def process_identity(pid=None):
    """Linux PID plus boot/start identity prevents confusing a reused process ID."""
    pid = os.getpid() if pid is None else pid
    try:
        from pathlib import Path

        stat = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return {
            "pid": int(pid),
            "start_ticks": stat[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
    except (OSError, ValueError, IndexError):
        return None


def process_is_alive(identity):
    return bool(identity and process_identity(identity.get("pid")) == identity)


class AF3Queue:
    """One lightweight queue thread; no GPU allocation until an explicit execute."""

    def __init__(self, store):
        self.store = store
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._guard = threading.Lock()
        self._thread = None
        self.after_complete = None

    def _active(self):
        with self.store.connect() as con:
            return con.execute(
                "SELECT id,status FROM jobs WHERE kind='alphafold' AND status IN ('queued','running') ORDER BY created,id"
            ).fetchall()

    def recover(self):
        for row in self._active():
            job = self.store.get(row["id"])
            execution = (job.get("result") or {}).get("execution", {})
            if row["status"] == "running" and not process_is_alive(execution.get("owner")):
                self.store.update(
                    job["id"],
                    "interrupted",
                    job["result"],
                    "Server execution monitoring was interrupted. Existing outputs are preserved; this inference will not be automatically repeated or certified.",
                )
        if any(row["status"] == "queued" for row in self._active()):
            self.wake()

    def wake(self):
        with self._guard:
            if self._stop.is_set():
                raise ValueError("AF3 worker is shutting down")
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="herbfold-af3", daemon=True)
                self._thread.start()
            self._wake.set()

    def submit(self, job_id):
        job = self.store.get(job_id)
        if job["kind"] != "alphafold":
            raise ValueError("Not an AlphaFold execution job")
        if job["status"] in {"queued", "running", "completed"}:
            return job
        if job["status"] != "prepared":
            raise ValueError("Prepare an explicit retry to execute a blocked, failed or interrupted job")
        plan = job.get("result") or {}
        if not plan.get("runnable", plan.get("ready", False)):
            raise ValueError("; ".join(plan.get("blockers", ["AF3 runtime unavailable"])))
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            active = con.execute(
                "SELECT COUNT(*) FROM jobs WHERE kind='alphafold' AND status IN ('queued','running')"
            ).fetchone()[0]
            if active >= 32:
                raise ValueError("AF3 queue has reached its 32-job capacity; wait for an existing job")
            con.execute(
                "UPDATE jobs SET status='queued',updated=? WHERE id=? AND status='prepared'",
                (utcnow(), job_id),
            )
        self.wake()
        return self.store.get(job_id)

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=7)

    def _loop(self):
        while not self._stop.is_set():
            with self.store.connect() as con:
                row = con.execute(
                    "SELECT id FROM jobs WHERE kind='alphafold' AND status='queued' ORDER BY created,id LIMIT 1"
                ).fetchone()
            if row is None:
                self._wake.wait(1)
                self._wake.clear()
                continue
            try:
                self.run_one(row[0])
            except Exception as exc:
                # A failed plan/parser/check must never strand the durable queue.
                job = self.store.get(row[0])
                if job["status"] in {"queued", "running"}:
                    detail = (
                        str(exc)[:1500]
                        if isinstance(exc, alphafold.AF3ValidationError)
                        else "inspect the job log and manifest"
                    )
                    self.store.update(
                        job["id"],
                        "failed",
                        job["result"],
                        f"AF3 worker error: {type(exc).__name__}; {detail}",
                    )

    @staticmethod
    def _terminate(process):
        if process.poll() is not None:
            return
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)

    def run_one(self, job_id):
        with (self.store.root / "af3-execution.lock").open("a") as lock:
            while not self._stop.is_set():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self._stop.wait(0.25)
            if self._stop.is_set():
                return
            job = self.store.get(job_id)
            if job["status"] != "queued":
                return
            old_plan = job.get("result") or {}
            profile = old_plan.get("execution_profile")
            config = alphafold.AF3Config(**profile) if profile else alphafold.AF3Config.from_env()
            plan = alphafold.prepare_job(self.store.directory(job_id), job["payload"], config)
            if profile:
                plan["execution_profile"] = profile
            context = old_plan.get("submission_context")
            if context:
                plan["submission_context"] = context
                current_context = {
                    "parameter_stat_fingerprint_sha256": plan["provenance"]
                    .get("parameters", {})
                    .get("stat_fingerprint_sha256"),
                    "cuda_environment": plan["provenance"].get("cuda_environment"),
                }
                if "database_fingerprint" in context:
                    current_context["database_fingerprint"] = (
                        plan["provenance"].get("databases", {}).get("fingerprint_sha256")
                    )
                if current_context != context:
                    plan.setdefault("blockers", []).append(
                        "AF3 parameter files, databases or child execution settings changed after preparation. Prepare a new selected-compound job before running."
                    )
                    plan.update(runnable=False, ready=False)
            if not plan.get("runnable", plan.get("ready", False)):
                self.store.update(job_id, "blocked", plan, "; ".join(plan.get("blockers", [])))
                return
            plan["execution"] = {"owner": process_identity(), "started_at": utcnow()}
            self.store.update(job_id, "running", plan)
            try:
                timeout = int(os.getenv("AF3_TIMEOUT_SECONDS", "86400"))
                if not 1 <= timeout <= 604800:
                    raise ValueError("AF3 timeout must be between 1 second and 7 days")
                deadline = time.monotonic() + timeout
                if plan["msa_mode"] == "search" and af3_features.supported(job["payload"]):
                    cache = af3_features.FeatureCache(self.store.root)
                    expected = af3_features.identity(job["payload"], config, plan["provenance"])
                    sequence = af3_features.protein(job["payload"])["sequence"]
                    loaded = cache.load(expected, sequence)
                    if loaded is None:
                        plan["msa_features"] = {
                            **expected,
                            "status": "searching",
                            "cache_hit": False,
                            "cache_key": af3_features.cache_key(expected),
                            "unpaired_msa_sequences": None,
                            "paired_msa_sequences": None,
                            "non_query_sequences": None,
                            "template_count": None,
                        }
                        data_output = self.store.directory(job_id) / "data_pipeline"
                        if data_output.exists():
                            raise alphafold.AF3ValidationError(
                                "Existing partial data pipeline output is preserved; prepare a new retry job"
                            )
                        data_output.mkdir()
                        command, cwd = self._command(
                            config, plan, plan["input_path"], data_output, "data_pipeline"
                        )
                        step = self._run_process(job_id, plan, command, cwd, "data_pipeline", lock, deadline)
                        if step is None:
                            return
                        # A database replacement during a long CPU search must
                        # never publish features under the previous identity.
                        from .af3_databases import inspect_databases

                        inspection = inspect_databases(config)
                        if (
                            not inspection["runnable"]
                            or inspection["provenance"].get("fingerprint_sha256")
                            != expected["database_fingerprint"]
                        ):
                            raise alphafold.AF3ValidationError(
                                "Database installation changed during MSA search; results cannot populate the cache"
                            )
                        loaded = cache.publish(expected, job["payload"], data_output, job_id, step)
                    features, metadata = loaded
                    plan["msa_features"] = metadata
                    plan.update(
                        af3_features.write_inference_input(
                            self.store.directory(job_id) / "inference_input.json", job["payload"], features
                        )
                    )
                    plan["command"], plan["cwd"] = self._command(
                        config, plan, plan["inference_input_path"], plan["output_dir"], "inference"
                    )
                    self.store.write(job_id, "msa_features.json", metadata)
                    # CPU search may take hours. Parameters/settings must still
                    # be the prepared ones when the GPU inference starts.
                    from .af3_parameters import inspect_parameters

                    parameters = inspect_parameters(config)
                    current_env = alphafold.execution_environment()
                    planned_env = plan["provenance"].get("cuda_environment", {})
                    if (
                        not parameters["runnable"]
                        or parameters["provenance"].get("stat_fingerprint_sha256")
                        != plan["provenance"].get("parameters", {}).get("stat_fingerprint_sha256")
                        or any(current_env.get(key) != value for key, value in planned_env.items())
                    ):
                        raise alphafold.AF3ValidationError(
                            "Model parameters or child execution settings changed during MSA preparation; prepare a new job. Completed protein features are preserved."
                        )
                elif plan["msa_mode"] == "search":
                    plan["msa_features"] = {
                        "status": "combined_uncached",
                        "cache_hit": False,
                        "warnings": [
                            "Multi-protein inputs use the original combined AF3 pipeline without shared feature reuse."
                        ],
                    }
                self.store.write(job_id, "af3_manifest.json", plan)
                step = self._run_process(
                    job_id, plan, plan["command"], plan.get("cwd"), "inference", lock, deadline
                )
                if step is None:
                    return
                plan["execution"].update(finished_at=utcnow(), return_code=0)
                plan["stage"] = {
                    "name": "output_validation",
                    "label": "선택 물질·표적 및 출력 확인",
                    "started_at": utcnow(),
                }
                self.store.update(job_id, "running", plan)
                result = alphafold.parse_outputs(plan["output_dir"])
                result.update(
                    execution_verified=True,
                    execution_provenance=plan["provenance"],
                    execution=plan["execution"],
                    execution_profile=profile,
                    msa_mode=plan["msa_mode"],
                    model_seeds=plan["model_seeds"],
                    samples_per_seed=plan["samples_per_seed"],
                    num_recycles=plan["num_recycles"],
                    max_template_date=plan["max_template_date"],
                    return_code=0,
                    submission_context=context,
                    msa_features=plan["msa_features"],
                    stage=plan["stage"],
                    inference_input_sha256=plan.get("inference_input_sha256"),
                )
                if not result.get("models"):
                    self.store.update(job_id, "failed", result, "AF3 exited without model outputs")
                    return
                # Completed means process + parse succeeded. Identity/geometry is
                # an additional persisted result; it is not an efficacy test.
                self.store.update(job_id, "completed", result)
                if self.after_complete:
                    try:
                        result["output_validation"] = self.after_complete(job_id)
                    except Exception as exc:
                        result["output_validation"] = {
                            "status": "unavailable",
                            "identity_verified": False,
                            "quality_pass": None,
                            "warnings": [
                                f"Output integrity validation could not finish ({type(exc).__name__})"
                            ],
                        }
                result["stage"] = {
                    "name": "completed",
                    "label": "계산 완료 · 전체 구조 품질 별도 평가",
                    "finished_at": utcnow(),
                }
                self.store.write(job_id, "result.json", result)
                self.store.update(job_id, "completed", result)
            except Exception:
                if (plan.get("msa_features") or {}).get("status") == "searching":
                    plan["msa_features"]["status"] = "failed"
                # Persist diagnostic stage metadata before the outer loop marks
                # failure. Child cleanup is handled inside _run_process.
                current = self.store.get(job_id)
                if current["status"] == "running":
                    self.store.update(job_id, "running", plan)
                raise

    @staticmethod
    def _command(config, plan, input_path, output_dir, stage):
        command, cwd = alphafold.build_command(
            config, input_path, output_dir, msa_mode=plan["msa_mode"], stage=stage
        )
        image_id = plan["provenance"].get("docker_image_id")
        if config.runner == "docker" and image_id:
            command[command.index(config.docker_image)] = image_id
        return command, cwd

    def _run_process(self, job_id, plan, command, cwd, stage, lock, deadline):
        """Run one bounded child under the shared, inherited execution lock."""
        label = "CPU MSA·템플릿 검색" if stage == "data_pipeline" else "AF3 구조 추론"
        device = next((arg.split("=", 1)[1] for arg in command if arg.startswith("--jax_backend=")), None)
        step = {"name": stage, "label": label, "started_at": utcnow(), "command": command, "cwd": cwd}
        plan.pop("runtime_warning", None)
        plan["stage"] = {key: step[key] for key in ("name", "label", "started_at")}
        plan["execution"].setdefault("steps", []).append(step)
        self.store.update(job_id, "running", plan)
        child_env = alphafold.execution_environment()
        # AF3's progress prints otherwise remain buffered until the process exits
        # when stdout is the job log. This does not alter scientific settings.
        child_env["PYTHONUNBUFFERED"] = "1"
        if stage == "data_pipeline":
            child_env["CUDA_VISIBLE_DEVICES"] = ""
            child_env["JAX_PLATFORMS"] = "cpu"
        step["execution_environment"] = {
            key: child_env.get(key)
            for key in (
                "CUDA_VISIBLE_DEVICES",
                "JAX_PLATFORMS",
                "XLA_FLAGS",
                "XLA_PYTHON_CLIENT_PREALLOCATE",
                "PYTHONUNBUFFERED",
            )
        }
        process = None
        started = time.monotonic()
        next_heartbeat = started + PROCESS_HEARTBEAT_SECONDS
        try:
            retry_timeout = int(os.getenv("AF3_GPU_ALLOCATION_RETRY_TIMEOUT_SECONDS", "180"))
            if retry_timeout != 0 and not 30 <= retry_timeout <= 86400:
                raise ValueError("AF3 GPU allocation retry timeout must be 0 or between 30 and 86400 seconds")
            if self._stop.is_set() or started >= deadline:
                self.store.update(
                    job_id,
                    "interrupted" if self._stop.is_set() else "failed",
                    plan,
                    "AF3 stopped before its next execution stage; prepare an explicit retry.",
                )
                return None
            with (self.store.directory(job_id) / "run.log").open("a") as output:
                output.write(f"\n[HerbFold stage: {stage}]\n")
                output.flush()
                step["log_start_bytes"] = output.tell()
                progress = ProgressObserver(stage, log_start=step["log_start_bytes"])
                retry_watchdog = AllocationRetryWatchdog(
                    retry_timeout if stage == "inference" and device == "gpu" else 0,
                    log_start=step["log_start_bytes"],
                )
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=child_env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    start_new_session=True,
                    pass_fds=(lock.fileno(),),
                )
                step["child"] = plan["execution"]["child"] = process_identity(process.pid)
                self.store.update(job_id, "running", plan)
                while process.poll() is None:
                    stopping = self._stop.wait(0.25)
                    now = time.monotonic()
                    # Observe only on the bounded heartbeat; never infer a stall
                    # from repeatedly rereading an old warning or log silence.
                    allocation_timeout = (
                        not stopping
                        and now < deadline
                        and now >= next_heartbeat
                        and retry_watchdog.observe(self.store.directory(job_id) / "run.log", now)
                    )
                    if stopping or now >= deadline or allocation_timeout:
                        # The child may have completed during the wait above.
                        if not stopping and process.poll() is not None:
                            break
                        self._terminate(process)
                        step.update(
                            finished_at=utcnow(),
                            return_code=process.returncode,
                            elapsed_seconds=time.monotonic() - started,
                        )
                        if stage == "data_pipeline":
                            plan["msa_features"]["status"] = "failed"
                        failure = None
                        if not stopping:
                            failure = describe_process_failure(
                                self.store.directory(job_id) / "run.log",
                                stage,
                                process.returncode,
                                log_start=step["log_start_bytes"],
                                timed_out=True,
                                device=device,
                            )
                            if allocation_timeout:
                                failure.update(
                                    code="resource_retry_timeout",
                                    message=(
                                        f"GPU 메모리 할당 재시도만 {retry_timeout}초 이상 반복되어 "
                                        "AF3 계산을 중단했습니다. GPU 메모리를 확보한 뒤 새 계산으로 다시 실행하세요. "
                                        "기존 입력·검색 결과·로그는 보존됩니다."
                                    ),
                                    retry_timeout_seconds=retry_timeout,
                                )
                            step["failure"] = plan["execution"]["failure"] = failure
                        plan["execution"].update(finished_at=utcnow(), return_code=process.returncode)
                        self.store.update(
                            job_id,
                            "interrupted" if stopping else "failed",
                            plan,
                            "Server stopped this AF3 stage; an explicit retry is required."
                            if stopping
                            else failure["message"],
                        )
                        return None
                    now = time.monotonic()
                    if now >= next_heartbeat:
                        checked_at = utcnow()
                        step["elapsed_seconds"] = now - started
                        plan["stage"].update(checked_at=checked_at, elapsed_seconds=now - started)
                        plan["execution"]["last_checked_at"] = checked_at
                        detail = progress.observe(self.store.directory(job_id) / "run.log", checked_at)
                        if detail is not None:
                            plan["stage"]["detail"] = step["detail"] = detail
                        if has_gpu_allocation_warning(
                            self.store.directory(job_id) / "run.log",
                            log_start=step["log_start_bytes"],
                        ):
                            plan.setdefault(
                                "runtime_warning",
                                {
                                    "code": "gpu_memory_pressure",
                                    "message": (
                                        "실행 로그에서 GPU 메모리 할당 경고가 감지되었습니다. "
                                        "메모리를 확보하지 못하면 계산이 오래 지연되거나 실패할 수 있습니다. "
                                        "GPU 사용량을 확인하세요."
                                    ),
                                    "observed_at": checked_at,
                                },
                            )
                        if "runtime_warning" in plan:
                            plan["runtime_warning"]["checked_at"] = checked_at
                        self.store.update(job_id, "running", plan)
                        next_heartbeat = now + PROCESS_HEARTBEAT_SECONDS
            step.update(
                finished_at=utcnow(),
                return_code=process.returncode,
                elapsed_seconds=time.monotonic() - started,
            )
            detail = progress.observe(self.store.directory(job_id) / "run.log", utcnow())
            if detail is not None:
                plan["stage"]["detail"] = step["detail"] = detail
            if process.returncode:
                if stage == "data_pipeline":
                    plan["msa_features"]["status"] = "failed"
                failure = describe_process_failure(
                    self.store.directory(job_id) / "run.log",
                    stage,
                    process.returncode,
                    log_start=step["log_start_bytes"],
                    device=device,
                )
                step["failure"] = plan["execution"]["failure"] = failure
                plan["execution"].update(finished_at=utcnow(), return_code=process.returncode)
                self.store.update(
                    job_id,
                    "failed",
                    plan,
                    failure["message"],
                )
                return None
            plan.pop("runtime_warning", None)
            return step
        except Exception:
            if process is not None:
                self._terminate(process)
            raise
