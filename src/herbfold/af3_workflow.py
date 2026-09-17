"""Durable browser-independent bridge to the existing selected-compound AF3 queue.

Preparation and queue acknowledgement have separate persisted checkpoints.
Recovery observes existing jobs; it never repeats preparation or inference.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from . import molecular_selection
from .af3_execution import process_is_alive
from .af3_studio import StudioPredictionRequest
from .protein_targets import normalize_accession
from .storage import utcnow

Seed = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
Category = Literal["herbal", "natural_product", "drug", "candidate"]
STAGES = [
    ("input", "입력 준비", "분자·표적 입력 준비"),
    ("preflight", "실행 환경 확인", "AF3 실행 전 검사"),
    ("msa", "MSA·템플릿 검색", "CPU 서열·템플릿 검색"),
    ("inference", "AF3 구조 추론", "AF3 실행 큐"),
    ("validation", "출력 동일성 확인", "분자·표적 출력 검사"),
    ("results", "결과 연결", "저장 결과 연결"),
]
NOTE = "단계는 저장된 준비·실행 기록에서 확인합니다. 완료율을 추정하지 않으며 구조 신뢰도는 약효·안전성 검증이 아닙니다."


class WorkflowCompound(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    smiles: str = Field(min_length=1, max_length=4096)
    category: Category | None = None

    @field_validator("smiles")
    @classmethod
    def identity(cls, value):
        molecular_selection.exact_identity(value)
        return value


class AF3WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: uuid.UUID
    compound: WorkflowCompound
    target_accession: str = Field(default="P35354", max_length=20)
    msa_mode: Literal["search", "none"] = "search"
    seeds: list[Seed] = Field(default_factory=lambda: [1], min_length=1, max_length=5)
    exploratory_ack: StrictBool = False
    execute: StrictBool = True

    @model_validator(mode="after")
    def contract(self):
        self.target_accession = normalize_accession(self.target_accession)
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Model seeds must be distinct")
        if self.msa_mode == "none" and self.exploratory_ack is not True:
            raise ValueError("MSA·template 미사용 탐색 계산에는 exploratory_ack=true 확인이 필요합니다.")
        return self


class AttachCompound(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str | None = Field(default=None, min_length=1, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    smiles: str | None = Field(default=None, min_length=1, max_length=4096)
    category: Category | None = None


class AF3WorkflowAttach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    compound: AttachCompound | None = None
    target_accession: str | None = Field(default=None, max_length=20)


class AF3WorkflowResume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool

    @field_validator("confirm")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirm=true is required to prepare or submit an AF3 job")
        return value


def _payload(request):
    data = request.model_dump(mode="json")
    data.update(smiles=request.compound.smiles,
                canonical_smiles=molecular_selection.exact_identity(request.compound.smiles),
                compound_name=request.compound.name)
    return data


def _hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class AF3Workflows:
    def __init__(self, store, predictions):
        self.store, self.predictions = store, predictions
        self._lease = (store.root / "af3-workflows.lock").open("a+b")
        try:
            fcntl.flock(self._lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lease.close()
            raise RuntimeError("An AF3 workflow owner is already active for this runtime; use one application worker.") from exc
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.futures = {}
        self._recovered = False
        self.pool = None
        try:
            with store.connect() as con:
                con.execute("CREATE TABLE IF NOT EXISTS af3_workflows (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, created TEXT NOT NULL, state TEXT NOT NULL)")
            self.recover()
            self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="af3-workflow-prepare")
            self.monitor = threading.Thread(target=self._monitor, daemon=True, name="af3-workflow-observer")
            self.monitor.start()
        except Exception:
            if self.pool:
                self.pool.shutdown(wait=False, cancel_futures=True)
            self._lease.close()
            raise

    def _load(self, workflow_id):
        with self.store.connect() as con:
            row = con.execute("SELECT state FROM af3_workflows WHERE id=?", (workflow_id,)).fetchone()
        if row is None:
            raise KeyError("AF3 workflow not found")
        return json.loads(row[0])

    def _save(self, state):
        state["updated"] = utcnow()
        with self.store.connect() as con:
            con.execute("UPDATE af3_workflows SET state=? WHERE id=?",
                        (json.dumps(state, ensure_ascii=False, allow_nan=False), state["id"]))

    @staticmethod
    def _event(state, stage, status, message):
        state["events"].append({"seq": (state["events"][-1]["seq"] + 1) if state["events"] else 1, "stage": stage, "status": status,
                                "message": str(message)[:1500], "time": utcnow()})
        # A long running GPU job must not grow the browser payload without bound.
        if len(state["events"]) > 200:
            state["events"] = state["events"][-200:]

    def _stage(self, state, stage_id, status, summary, *, started=None, finished=None):
        stage = next(item for item in state["stages"] if item["id"] == stage_id)
        if (stage["status"], stage["summary"]) != (status, summary):
            stage.update(status=status, summary=summary)
            self._event(state, stage_id, status, summary)
        if started:
            stage["started_at"] = started
        if finished:
            stage["finished_at"] = finished

    @staticmethod
    def _actions(state):
        job = (state.get("prediction") or {}).get("job", {})
        status = job.get("status")
        orphan = (state.get("prediction") or {}).get("orphan_process_active") is True
        kind, reason = None, None
        if state["request"]["msa_mode"] == "none" and state["request"].get("exploratory_ack") is not True:
            reason = "이 과거 작업에 탐색 모드 확인이 없습니다. 설명을 확인한 새 요청을 생성하세요."
        elif orphan:
            reason = "기존 AF3 하위 프로세스가 실행 중입니다. 상태를 다시 연결해 확인하세요."
        elif state.get("recovery_candidates"):
            reason = "준비 확인이 불확실합니다. 저장된 작업을 확인하고 정확한 작업을 연결하세요."
        elif status in {"running", "queued", "completed"}:
            reason = "기존 작업을 추적합니다. 중복 실행하지 않습니다."
        elif state["status"] in {"queued", "preparing"}:
            reason = "입력 준비가 진행 중입니다."
        elif status == "prepared":
            kind = "execute"
        elif status in {"blocked", "failed", "interrupted"}:
            kind = "retry"
        elif not state.get("job_id") and state["status"] in {"blocked", "failed", "interrupted"}:
            kind = "prepare"
        return {"can_resume": kind is not None, "resume_kind": kind, "can_execute": kind == "execute",
                "can_retry": kind in {"retry", "prepare"}, "reason": reason,
                "cancel_supported": False, "reconnect_supported": True}

    def _new(self, request, request_hash, *, source="submitted"):
        now = utcnow()
        state = {"id": uuid.uuid4().hex, "request_id": request["request_id"], "created": now, "updated": now,
                 "status": "queued", "request": request, "job_id": None, "prediction": None,
                 "stages": [{"id": i, "label": label, "agent": agent, "status": "pending", "summary": "대기"}
                            for i, label, agent in STAGES], "events": [], "checkpoints": {"workflow_created": now},
                 "job_history": [], "blockers": [], "error": None, "recovery_candidates": [],
                 "source": source, "execution_note": NOTE, "actions": {}}
        self._event(state, "input", "queued", "브라우저와 독립된 준비 작업을 저장했습니다.")
        state["actions"] = self._actions(state)
        with self.store.connect() as con:
            con.execute("INSERT INTO af3_workflows VALUES(?,?,?,?,?)",
                        (state["id"], state["request_id"], request_hash, now, json.dumps(state, ensure_ascii=False)))
        return state

    def create(self, request: AF3WorkflowRequest, *, background=True):
        data = _payload(request)
        data["submitted_target_accession"] = data["target_accession"]
        data["target_accession"] = molecular_selection.canonical_target_accession(self.store, data["target_accession"])
        fingerprint = _hash(data)
        with self.lock:
            if self.stopping.is_set():
                raise ValueError("AF3 workflow service is stopping")
            with self.store.connect() as con:
                previous = con.execute("SELECT id,request_hash FROM af3_workflows WHERE request_id=?",
                                       (data["request_id"],)).fetchone()
            if previous:
                if previous["request_hash"] != fingerprint:
                    raise ValueError("request_id already belongs to a different payload")
                return self.get(previous["id"])
            if len(self.futures) >= 8:
                raise ValueError("Eight AF3 input-preparation workflows are already active")
            state = self._new(data, fingerprint)
            if background:
                self._schedule(state["id"], "prepare", execute=request.execute)
        if not background:
            self._work(state["id"], "prepare", request.execute)
        return self.get(state["id"])

    def by_request(self, request_id):
        canonical = str(uuid.UUID(str(request_id)))
        with self.store.connect() as con:
            row = con.execute("SELECT id FROM af3_workflows WHERE request_id=?", (canonical,)).fetchone()
        if row is None:
            raise KeyError("AF3 workflow request not found")
        return self.get(row[0])

    def jobs(self, limit=50):
        with self.store.connect() as con:
            rows = con.execute("SELECT p.job_id FROM studio_predictions p JOIN jobs j ON j.id=p.job_id ORDER BY j.created DESC,j.id DESC LIMIT ?", (limit,)).fetchall()
        return [self.predictions.get(row[0]) for row in rows]

    def list(self, limit=50):
        with self.store.connect() as con:
            rows = con.execute("SELECT id FROM af3_workflows ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [self.get(row[0]) for row in rows]

    def options(self):
        return {"stages": [{"id": i, "label": label, "agent": agent} for i, label, agent in STAGES],
                "limits": {"max_preparations": 8, "max_seeds": 5, "seed_max": 2**32 - 1},
                "automatic_restart_submission": False, "cancel_supported": False, "execution_note": NOTE}

    def _matches(self, state, prediction):
        requested, wanted = prediction["requested"], state["request"]
        accession = molecular_selection.canonical_target_accession(self.store, wanted["target_accession"])
        return (requested.get("canonical_smiles") == wanted["canonical_smiles"]
                and requested.get("target_accession") == accession
                and requested.get("msa_mode") == wanted["msa_mode"]
                and requested.get("seeds") == wanted["seeds"])

    def _link(self, state, prediction):
        if not self._matches(state, prediction):
            raise ValueError("Prepared prediction does not match the exact workflow ligand, target, MSA mode and seeds")
        old_id, new_id = state.get("job_id"), prediction["job"]["id"]
        if old_id and old_id != new_id and old_id not in state["job_history"]:
            state["job_history"].append(old_id)
        state.update(job_id=new_id, prediction=prediction, recovery_candidates=[])
        state["checkpoints"]["prepare_in_progress"] = False
        state["checkpoints"]["prediction_prepared"] = {"job_id": new_id, "time": utcnow()}
        self._event(state, "input", "completed", f"기존 실행 큐의 정확한 작업 ID를 저장했습니다: {new_id}")
        self._save(state)  # This commit MUST precede predictions.execute().

    def _discover(self, state):
        started = state["checkpoints"].get("prepare_started")
        if not started:
            return None
        rows = self.predictions.list(state["request"]["smiles"], state["request"]["target_accession"])["items"]
        matches = [row for row in rows if self._matches(state, row) and row["job"]["created"] >= started]
        if len(matches) == 1:
            self._link(state, matches[0])
            return matches[0]
        state["recovery_candidates"] = [row["job"]["id"] for row in matches]
        return None

    def _project(self, state, prediction):
        if not self._matches(state, prediction) or prediction["job"]["id"] != state["job_id"]:
            raise ValueError("Linked prediction identity differs from the persisted workflow")
        previous_stages = {s["id"]: (s["status"], s["summary"]) for s in state["stages"]}

        def set_stage(stage_id, status, summary, *, started=None, finished=None):
            item = next(s for s in state["stages"] if s["id"] == stage_id)
            item.update(status=status, summary=summary)
            if started:
                item["started_at"] = started
            if finished:
                item["finished_at"] = finished

        state["prediction"] = prediction
        job = prediction["job"]
        status, result = job["status"], job.get("result") or {}
        readiness = prediction.get("readiness") or {}
        blockers = [str(x) for x in readiness.get("blockers", [])]
        state["status"] = {"queued": "queued_for_execution"}.get(status, status)
        if status == "prepared" and state["checkpoints"].get("execution_intent_active"):
            state["status"] = "preparing"
        state["error"] = job.get("error")
        state["blockers"] = blockers
        if state["error"] and status in {"blocked", "failed", "interrupted"} and state["error"] not in blockers:
            state["blockers"].append(state["error"])
        set_stage("input", "completed", "분자·표적에 연결된 입력 작업이 저장되어 있습니다.", finished=job["created"])
        preflight_pass = readiness.get("runnable") is True or status in {"queued", "running", "completed"}
        set_stage("preflight", "completed" if preflight_pass else "blocked",
                    "저장된 실행 전 검사를 통과했습니다." if preflight_pass else "; ".join(blockers) or "실행 환경 확인이 완료되지 않았습니다.")
        # Reset downstream stages from the actual new job, retaining no prior-attempt success.
        for stage in ("msa", "inference", "validation", "results"):
            item = next(s for s in state["stages"] if s["id"] == stage)
            item.update(status="pending", summary="저장된 실행 기록 대기")
            item.pop("started_at", None)
            item.pop("finished_at", None)
        features = prediction.get("msa_features") or {}
        steps = (result.get("execution") or {}).get("steps", [])
        for step in steps:
            stage_id = {"data_pipeline": "msa", "inference": "inference"}.get(step.get("name"))
            if stage_id:
                finished, rc = step.get("finished_at"), step.get("return_code")
                step_status = "completed" if finished and rc == 0 else ("failed" if finished else "running")
                set_stage(stage_id, step_status, step.get("label", stage_id),
                            started=step.get("started_at"), finished=finished)
        if state["request"]["msa_mode"] == "none":
            set_stage("msa", "skipped", "사용자가 확인한 MSA·template 미사용 탐색 모드입니다.")
        elif features.get("status") in {"ready", "complete"}:
            set_stage("msa", "completed", "검증된 저장 서열 특징 사용" if features.get("cache_hit") else "서열 특징 생성·검증 완료")
        actual_stage = (result.get("stage") or {}).get("name")
        stage_map = {"data_pipeline": "msa", "inference": "inference", "output_validation": "validation"}
        if status == "running" and actual_stage in stage_map:
            detail = (result.get("stage") or {}).get("detail") or {}
            set_stage(stage_map[actual_stage], "running", detail.get("activity") or detail.get("label")
                        or result["stage"].get("label", actual_stage), started=result["stage"].get("started_at"))
        if status in {"failed", "interrupted", "blocked"}:
            failed_stage = stage_map.get(actual_stage, "preflight" if not preflight_pass else "inference")
            set_stage(failed_stage, status, job.get("error") or "; ".join(blockers) or status)
        if status == "completed":
            set_stage("inference", "completed", "AF3 프로세스와 출력 읽기가 완료되었습니다.")
            validation = prediction.get("output_validation")
            if validation:
                valid = validation.get("identity_verified") is True
                set_stage("validation", "completed" if valid else "failed",
                            validation.get("status", "출력 확인 기록"))
            else:
                set_stage("validation", "unknown", "출력 동일성 확인 기록이 없습니다. 품질 통과를 뜻하지 않습니다.")
            verified = bool(validation and validation.get("identity_verified") is True and result.get("execution_verified") is True)
            set_stage("results", "completed" if verified else ("blocked" if validation else "unknown"),
                      "동일성이 확인된 저장 구조를 연결합니다. 구조 품질은 별도 평가 대상입니다." if verified
                      else "실행 기록은 보존되어 있으나 확인된 구조 결과로 표시할 수 없습니다.", finished=job["updated"])
            # The queue persists process completion before its identity callback.
            # Only a recent, live-owner output-validation phase is active work;
            # legacy missing validation must not become an endless running state.
            phase = result.get("stage") or {}
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(phase.get("started_at", ""))).total_seconds()
            except (TypeError, ValueError):
                age = float("inf")
            pending_validation = (not validation and phase.get("name") == "output_validation"
                                  and 0 <= age < 300 and process_is_alive((result.get("execution") or {}).get("owner")))
            if pending_validation:
                state["status"] = "validating"
                set_stage("validation", "running", "AF3 출력 읽기가 끝났으며 실행 중인 서버의 동일성 확인 결과를 기다립니다.", started=phase.get("started_at"))
                set_stage("results", "pending", "출력 동일성 확인이 끝난 뒤 구조 결과를 연결합니다.")
            if state["request"]["msa_mode"] == "search" and not features and not any(s.get("name") == "data_pipeline" for s in steps):
                set_stage("msa", "unknown", "이 과거 작업에는 별도 MSA 단계 기록이 없습니다.")
        state["actions"] = self._actions(state)
        for item in state["stages"]:
            if previous_stages[item["id"]] != (item["status"], item["summary"]):
                self._event(state, item["id"], item["status"], item["summary"])

    def get(self, workflow_id):
        with self.lock:
            state = self._load(workflow_id)
            before = json.dumps(state, sort_keys=True)
            if state.get("job_id") and not state["checkpoints"].get("prepare_in_progress"):
                try:
                    self._project(state, self.predictions.get(state["job_id"]))
                except Exception as exc:
                    state.update(status="interrupted", error=f"저장된 작업 연결 확인 실패: {type(exc).__name__}: {str(exc)[:300]}")
                    state["actions"] = {"can_resume": False, "can_execute": False, "can_retry": False,
                                        "resume_kind": None, "reason": state["error"], "cancel_supported": False,
                                        "reconnect_supported": True}
            else:
                state["actions"] = self._actions(state)
            if json.dumps(state, sort_keys=True) != before:
                self._save(state)
            return state

    def attach(self, request: AF3WorkflowAttach):
        prediction = self.predictions.get(request.job_id)
        selected = prediction["requested"]
        metadata = request.compound.model_dump(exclude_none=True) if request.compound else {}
        if "smiles" in metadata and molecular_selection.exact_identity(metadata["smiles"]) != selected["canonical_smiles"]:
            raise ValueError("선택한 분자와 기존 AF3 작업의 정확한 구조가 다릅니다.")
        if request.target_accession and molecular_selection.canonical_target_accession(self.store, normalize_accession(request.target_accession)) != selected["target_accession"]:
            raise ValueError("선택한 표적과 기존 AF3 작업의 표적이 다릅니다.")
        with self.lock:
            with self.store.connect() as con:
                existing = con.execute("SELECT id FROM af3_workflows WHERE json_extract(state,'$.job_id')=? ORDER BY created LIMIT 1", (request.job_id,)).fetchone()
            if existing:
                return self.get(existing[0])
            compound = {"id": metadata.get("id", "studio-" + request.job_id),
                        "name": metadata.get("name", prediction["job"]["payload"].get("name", "Stored AF3 prediction")),
                        "smiles": selected["canonical_smiles"], "category": metadata.get("category")}
            data = {"request_id": str(uuid.uuid4()), "compound": compound,
                    "smiles": compound["smiles"], "canonical_smiles": compound["smiles"], "compound_name": compound["name"],
                    "target_accession": selected["target_accession"], "msa_mode": selected["msa_mode"],
                    "seeds": selected["seeds"], "exploratory_ack": selected.get("exploratory_ack", False), "execute": False}
            state = self._new(data, _hash(data), source="attached_existing_job")
            self._link(state, prediction)
            return self.get(state["id"])

    def _schedule(self, workflow_id, kind, *, execute=True):
        if workflow_id in self.futures:
            return
        future = self.pool.submit(self._work, workflow_id, kind, execute)
        self.futures[workflow_id] = future
        future.add_done_callback(lambda done: self._finished(workflow_id, done))

    def _finished(self, workflow_id, future):
        with self.lock:
            if self.futures.get(workflow_id) is future:
                self.futures.pop(workflow_id, None)
            if self.stopping.is_set() and not self.futures:
                self._lease.close()

    def _work(self, workflow_id, kind, execute):
        try:
            with self.lock:
                if self.stopping.is_set():
                    return
                state = self._load(workflow_id)
                if kind in {"prepare", "retry"}:
                    state.update(status="preparing", error=None, blockers=[], recovery_candidates=[])
                    state["checkpoints"]["prepare_started"] = utcnow()
                    state["checkpoints"]["prepare_in_progress"] = True
                    self._stage(state, "input", "running", "선택된 분자·표적 입력과 실행 전 검사를 준비합니다.", started=state["checkpoints"]["prepare_started"])
                    self._save(state)
                state["checkpoints"]["execution_intent_active"] = execute
                self._save(state)
            if kind in {"prepare", "retry"}:
                wanted = state["request"]
                prediction = self.predictions.prepare(StudioPredictionRequest(
                    smiles=wanted["smiles"], target_accession=wanted["target_accession"], msa_mode=wanted["msa_mode"],
                    seeds=wanted["seeds"], exploratory_ack=wanted["exploratory_ack"], retry=kind == "retry"))
                with self.lock:
                    state = self._load(workflow_id)
                    self._link(state, prediction)
            with self.lock:
                state = self._load(workflow_id)
                prediction = self.predictions.get(state["job_id"])
                self._project(state, prediction)
                self._save(state)
                should_submit = execute and not self.stopping.is_set() and prediction["job"]["status"] == "prepared"
                if should_submit:
                    state["checkpoints"]["submission_started"] = {"job_id": state["job_id"], "time": utcnow()}
                    self._event(state, "inference", "queued", "저장된 정확한 작업 ID를 실행 큐에 요청합니다.")
                    self._save(state)
            if should_submit:
                self.predictions.execute(state["job_id"])
                with self.lock:
                    state = self._load(workflow_id)
                    state["checkpoints"]["submission_acknowledged"] = {"job_id": state["job_id"], "time": utcnow()}
                    state["checkpoints"]["execution_intent_active"] = False
                    self._save(state)
            else:
                with self.lock:
                    state = self._load(workflow_id)
                    state["checkpoints"]["execution_intent_active"] = False
                    self._save(state)
            self.get(workflow_id)
        except Exception as exc:
            with self.lock:
                state = self._load(workflow_id)
                message = f"{type(exc).__name__}: {getattr(exc, 'detail', str(exc))}"
                state["checkpoints"]["last_error"] = {"time": utcnow(), "message": message[:1000]}
                state["checkpoints"]["execution_intent_active"] = False
                if not state.get("job_id") or state["checkpoints"].get("prepare_in_progress"):
                    try:
                        self._discover(state)
                    except Exception:
                        pass
                if state.get("job_id") and not state["checkpoints"].get("prepare_in_progress"):
                    try:
                        self._project(state, self.predictions.get(state["job_id"]))
                    except Exception:
                        state.update(status="interrupted", error=message[:1000])
                else:
                    state.update(status="interrupted" if isinstance(exc, TimeoutError) else "blocked", error=message[:1000], blockers=[message[:1000]])
                    self._stage(state, "preflight", state["status"], message[:1000])
                self._event(state, "input", "interrupted", "응답 확인 중 오류가 발생했습니다. 기존 작업을 확인하며 자동 재제출하지 않습니다.")
                state["actions"] = self._actions(state)
                self._save(state)

    def resume(self, workflow_id, *, background=True):
        with self.lock:
            if self.stopping.is_set():
                raise ValueError("AF3 workflow service is stopping")
            state = self.get(workflow_id)
            if workflow_id in self.futures or state["status"] in {"running", "validating", "queued_for_execution", "completed"}:
                return state
            action = state["actions"]
            if not action["can_resume"]:
                raise ValueError(action.get("reason") or "This workflow cannot be safely resumed")
            if len(self.futures) >= 8:
                raise ValueError("Eight input-preparation workflows are already active")
            kind = action["resume_kind"]
            # An attached legacy none-mode job may never have recorded acknowledgement.
            if state["request"]["msa_mode"] == "none" and state["request"].get("exploratory_ack") is not True:
                raise ValueError("기존 작업에 탐색 모드 확인이 없습니다. 설명을 확인한 새 요청을 생성하세요.")
            execute = kind == "execute" or state["request"]["execute"] is True
            # Persist the active phase BEFORE dispatch. The response or a concurrent
            # GET must not expose the old prepared/failed job as an idle workflow.
            now = utcnow()
            state.update(status="preparing", error=None, blockers=[])
            state["checkpoints"]["execution_intent_active"] = execute
            state["checkpoints"]["dispatch_requested"] = {"kind": kind, "execute": execute, "time": now}
            if kind in {"prepare", "retry"}:
                state["checkpoints"].update(prepare_in_progress=True, prepare_started=now)
                self._stage(state, "input", "running", "명시적으로 요청한 입력 준비를 시작합니다.", started=now)
            else:
                state["checkpoints"]["execution_confirmation"] = {"job_id": state["job_id"], "time": now}
            self._event(state, "input", "queued", f"사용자가 명시적으로 {kind} 동작을 요청했습니다.")
            state["actions"] = self._actions(state)
            self._save(state)
            if background:
                self._schedule(workflow_id, kind, execute=execute)
        if not background:
            self._work(workflow_id, kind, execute)
        return self.get(workflow_id)

    def recover(self):
        with self.lock:
            if self._recovered:
                return
            self._recovered = True
            with self.store.connect() as con:
                rows = con.execute("SELECT id FROM af3_workflows").fetchall()
            for row in rows:
                state = self._load(row[0])
                before = json.dumps(state, sort_keys=True)
                if state["checkpoints"].get("execution_intent_active"):
                    state["checkpoints"]["execution_intent_active"] = False
                if state["status"] in {"queued", "preparing"} or state["checkpoints"].get("prepare_in_progress"):
                    if not state.get("job_id") or state["checkpoints"].get("prepare_in_progress"):
                        try:
                            self._discover(state)
                        except Exception:
                            pass
                    if not state.get("job_id") or state["checkpoints"].get("prepare_in_progress"):
                        state.update(status="interrupted", error="준비 중 서버 연결이 중단되었습니다. 기존 작업을 확인한 뒤 명시적으로 재개하세요.")
                        self._stage(state, "input", "interrupted", state["error"])
                        state["actions"] = self._actions(state)
                if json.dumps(state, sort_keys=True) != before:
                    self._save(state)
                if state.get("job_id"):
                    self.get(state["id"])

    def _monitor(self):
        while not self.stopping.wait(1.0):
            with self.store.connect() as con:
                rows = con.execute("SELECT id FROM af3_workflows WHERE json_extract(state,'$.job_id') IS NOT NULL AND json_extract(state,'$.status') != 'completed'").fetchall()
            for row in rows:
                if self.stopping.is_set():
                    break
                try:
                    self.get(row[0])
                except Exception:
                    # Preserve the durable record if a concurrent storage read fails.
                    continue

    def close(self):
        with self.lock:
            if self.stopping.is_set():
                return
            self.stopping.set()
            with self.store.connect() as con:
                rows = con.execute("SELECT id FROM af3_workflows WHERE json_extract(state,'$.job_id') IS NULL AND json_extract(state,'$.status') IN ('queued','preparing')").fetchall()
            for row in rows:
                state = self._load(row[0])
                state.update(status="interrupted", error="서버의 입력 준비 추적이 종료되었습니다. 자동 재제출하지 않습니다.")
                self._stage(state, "input", "interrupted", state["error"])
                state["actions"] = self._actions(state)
                self._save(state)
            self.pool.shutdown(wait=False, cancel_futures=True)
            # Slow in-flight preparation retains the owner lease until its checkpoint is saved.
            if not self.futures:
                self._lease.close()
        if self.monitor is not threading.current_thread():
            self.monitor.join(timeout=2)
