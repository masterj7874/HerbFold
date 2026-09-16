"""Durable, bounded scientific-agent DAG with provenance and explicit local mode.

Model decisions can select parent molecules, route optional work and rank verified
candidates. Only this module's named tools can perform calculations or execution.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from . import alphafold, chemistry, connectors, quantum
from .llm import MODEL, PROMPT_VERSION, AstraProvider, LLMError
from .storage import utcnow


class Budgets(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    max_llm_calls: int = Field(default=8, ge=1, le=16)
    max_output_tokens: int = Field(default=1800, ge=256, le=8000)
    max_candidates: int = Field(default=12, ge=1, le=32)
    max_af3_candidates: int = Field(default=1, ge=0, le=4)
    max_source_requests: int = Field(default=8, ge=0, le=16)
    max_qpu_jobs: int = Field(default=1, ge=1, le=4)
    max_qpu_shots: int = Field(default=4096, ge=384, le=65536)
    max_qpu_seconds: int = Field(default=30, ge=1, le=120)
    af3_timeout_seconds: int = Field(default=3600, ge=30, le=86400)


class AnalysisCompound(BaseModel):
    """Explicit imported chemical identity; extra UI fields are not scientific evidence."""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False, str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$")
    smiles: str = Field(min_length=1, max_length=4096)
    category: Literal["herbal", "natural_product", "drug"]
    name: str | None = Field(default=None, max_length=200)
    name_ko: str | None = Field(default=None, max_length=200)
    pubchem_cid: int | None = Field(
        default=None, ge=1, le=2147483647, validation_alias=AliasChoices("pubchem_cid", "cid")
    )
    source_url: str | None = Field(default=None, max_length=2048)
    source: str | None = Field(default=None, max_length=2048)
    retrieved_at: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def source_link(self):
        if not self.source_url and self.source and urlparse(self.source).scheme in {"http", "https"}:
            self.source_url = self.source
        if self.source_url:
            parsed = urlparse(self.source_url)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError(
                    "Source links must use HTTP or HTTPS with a hostname and no embedded credentials"
                )
        return self


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    goal: str = Field(
        default="한약 성분과 기존 약물의 구조를 비교하고 검증 가능한 후보를 설계하세요.",
        min_length=3,
        max_length=4000,
    )
    compound_ids: list[str] = Field(default_factory=list, max_length=8)
    compounds: list[AnalysisCompound] = Field(default_factory=list, max_length=8)
    protein_sequence: str = Field(default="", max_length=10000)
    target_id: str = Field(default="custom", min_length=1, max_length=100)
    max_candidates: int = Field(default=6, ge=1, le=32)
    mode: Literal["astra", "local"] = "astra"
    run_af3: bool = False
    msa_mode: Literal["search", "none"] = "search"
    quantum_mode: Literal["local", "ibm", "off"] = "local"
    budgets: Budgets = Field(default_factory=Budgets)

    @model_validator(mode="after")
    def consistent(self):
        if self.compound_ids and self.compounds:
            raise ValueError("Use compound_ids or compounds, not both")
        ids = self.compound_ids or [row.id for row in self.compounds]
        if len(ids) < 2:
            raise ValueError("Select at least two compounds")
        if len(set(ids)) != len(ids):
            raise ValueError("Select distinct compound IDs")
        if self.max_candidates > self.budgets.max_candidates:
            raise ValueError("max_candidates exceeds the candidate budget")
        self.protein_sequence = re.sub(r"\s+", "", self.protein_sequence).upper()
        if self.protein_sequence and not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWYX]+", self.protein_sequence):
            raise ValueError("Protein sequence must contain amino-acid letters only")
        if self.run_af3 and not self.budgets.max_af3_candidates:
            raise ValueError("AF3 execution needs a positive AF3 candidate budget")
        return self


STAGES = [
    ("coordinator", "Astra · 연구 계획", []),
    ("evidence", "근거 조사 에이전트", ["coordinator"]),
    ("chemistry", "화학 비교 에이전트", ["coordinator"]),
    ("candidate_design", "후보 설계 에이전트", ["evidence", "chemistry"]),
    ("structure", "AlphaFold 3 구조 에이전트", ["candidate_design"]),
    ("quantum", "양자 분석 에이전트", ["candidate_design"]),
    ("review", "독립 검증 에이전트", ["structure", "quantum"]),
    ("report", "연구 보고 에이전트", ["review"]),
]
ROLE_INSTRUCTIONS = {
    "coordinator": "Plan this exact user objective within its budgets. Select the most relevant provided parent IDs, retaining at least one herbal/natural-product and one drug. Pick candidate_policy to serve the goal. requested_tools controls optional routing: gather_evidence, prepare_af3, quantum_kernel. Include prepare_af3 when a target sequence is supplied or can be retrieved. Include quantum_kernel only when quantum_mode is not off. Include gather_evidence when source refresh/target lookup helps. Mandatory chemistry/design/review cannot be removed. All selected parents are used to generate cross-category BRICS candidates.",
    "evidence": "Audit retrieved source records and provenance, distinguish current fetches from catalog snapshots. Flag identity conflicts, missing target mapping and absence of measured target-specific affinity. Do not infer literature results from a URL.",
    "chemistry": "Interpret actual RDKit descriptors and computed herbal/natural-product versus drug comparisons. Compare assay-interference alerts, stereochemistry and physicochemical limits in relation to the goal. No biological or therapeutic equivalence can be inferred.",
    "candidate_design": "Rank and select actual generated candidate IDs for the user's objective using descriptors and verified source-parent/drug fragment ancestry. selected_ids is the ordered shortlist that will enter structure/quantum stages; return at least one if plausible candidates exist. Choosing stop blocks execution and still preserves generated data. Empty candidate pools are valid scientific results; never invent SMILES.",
    "review": "Act independently as a critical reviewer of the artifacts. Check whether AF3 executed versus was only prepared, and quantum hardware measured versus local/pending. Return a stop decision only for invalid scientific interpretation or artifact conflicts; missing efficacy evidence is an explicit limitation, not a reason to invent a score. Give experimental and data-validation next steps.",
    "report": "Write a concise Korean report answering the user objective from the completed artifacts and independent review. Separate measured, calculated, model-interpreted and not-yet-executed facts. Include the candidate shortlist and concrete next actions. Never claim discovery of a safe/effective/new drug or quantum advantage.",
}
ROLE_CONTEXT = {
    "coordinator": {
        "assigned_task": "Select parents, bounded candidate priority and optional routing only.",
        "already_executed_tools": [],
        "later_workers": [
            "evidence",
            "chemistry",
            "candidate_design",
            "structure",
            "quantum",
            "review",
            "report",
        ],
    },
    "evidence": {
        "assigned_task": "Audit the supplied source records, identity match flags and provenance. Return empty selected_ids/requested_tools. Missing target-specific candidate affinity is an expected evidence gap to record, not a stop condition. Do not select or generate candidates, prepare AF3 or execute quantum.",
        "already_executed_tools": [
            "bounded source retrieval when enabled by coordinator; otherwise explicitly marked snapshots/imports"
        ],
        "later_workers": ["candidate_design", "structure", "quantum", "review", "report"],
    },
    "chemistry": {
        "assigned_task": "Interpret supplied parent descriptors, alerts and comparisons only. No external source or candidate-generation tool is needed for this task. Evidence is audited concurrently by another worker. Missing future candidates, AF3 inputs and kernels is expected, not a stop condition.",
        "already_executed_tools": ["RDKit describe_molecule", "RDKit compare_compounds"],
        "later_workers": ["candidate_design", "structure", "quantum", "review", "report"],
    },
    "candidate_design": {
        "assigned_task": "Select and rank only the actual generated candidate IDs. Do not execute AF3 or quantum; later workers consume your shortlist. Empty pools are valid results. Stop only if supplied structures or provenance make all available candidates invalid for further computational consideration, not merely because efficacy/synthesis is unvalidated.",
        "already_executed_tools": [
            "RDKit cross-category BRICS candidate enumeration",
            "parent-fragment ancestry validation",
            "RDKit descriptors and filter alerts",
        ],
        "later_workers": ["structure", "quantum", "review", "report"],
    },
    "review": {
        "assigned_task": "Independently assess consistency of completed artifacts and requested-versus-actual execution. Preparation-only AF3 and pending IBM jobs can be legitimate request outcomes; preserve their actual status. Identify concrete conflicts and experimental limitations.",
        "already_executed_tools": [
            "all explicitly recorded prior scientific stages; execution may be prepared, blocked or pending exactly as shown"
        ],
        "later_workers": ["report"],
    },
    "report": {
        "assigned_task": "Report only supplied results and independent review. No further tool execution is expected from this authoring role.",
        "already_executed_tools": ["recorded prior stages and independent review"],
        "later_workers": [],
    },
}


class AnalysisBlocked(RuntimeError):
    pass


class AnalysisCancelled(RuntimeError):
    pass


class Orchestrator:
    def __init__(self, store, pool=None, *, provider=None):
        self.store = store
        self.pool = pool or ThreadPoolExecutor(max_workers=2, thread_name_prefix="herbfold-analysis")
        self.provider = provider or AstraProvider()
        self._schedule_lock = threading.Lock()
        self._futures = {}
        with store.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS analyses (id TEXT PRIMARY KEY, state TEXT NOT NULL)"
            )
        self._recover()

    def _recover(self):
        for state in self.list():
            if state["status"] not in {"queued", "running"}:
                continue
            with (self.store.directory(state["id"]) / "analysis.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue

                def recover(current):
                    current.update(
                        status="interrupted",
                        error="Worker restarted. Resume reuses completed checkpoints; uncertain external actions are never resubmitted.",
                    )
                    for stage in current["stages"]:
                        if stage["status"] == "running":
                            stage["status"] = "interrupted"
                    self._event(
                        current,
                        "system",
                        "interrupted",
                        "Worker interruption detected; saved checkpoints retained.",
                    )

                self._mutate(state["id"], recover)

    def create(self, request: AnalysisRequest, *, start=True):
        catalog = {row["id"]: row for row in chemistry.load_catalog()}
        if any(cid not in catalog for cid in request.compound_ids):
            raise ValueError("One or more compound IDs are not in the verified catalog")
        selected = [{**catalog[cid], "source_status": "catalog_snapshot"} for cid in request.compound_ids]
        for supplied in request.compounds:
            row = supplied.model_dump(exclude_none=True)
            # Validate actual chemistry, and recompute descriptors later rather than trusting UI data.
            canonical = chemistry.canonical_smiles(row["smiles"])
            known = catalog.get(row["id"])
            if (
                known
                and known["category"] == row["category"]
                and chemistry.canonical_smiles(known["smiles"]) == canonical
            ):
                selected.append({**known, "source_status": "catalog_snapshot"})
            else:
                if not row.get("source_url") and urlparse(row.get("source", "")).scheme in {"http", "https"}:
                    row["source_url"] = row["source"]
                row.update(
                    name=row.get("name") or row["id"],
                    source_status="user_provided_unverified",
                    input_smiles=row["smiles"],
                    smiles=canonical,
                    provenance_note="User-supplied identity and category; no botanical occurrence, source authenticity, or efficacy established.",
                )
                selected.append(row)
        if not any(row["category"] in {"herbal", "natural_product"} for row in selected) or not any(
            row["category"] == "drug" for row in selected
        ):
            raise ValueError("Select at least one herbal/natural-product compound and one drug reference")
        if len({chemistry.canonical_smiles(row["smiles"]) for row in selected}) < 2:
            raise ValueError("Select at least two distinct standardized molecules")
        job = self.store.create("analysis", request.model_dump())
        state = {
            "id": job["id"],
            "status": "queued",
            "mode": request.mode,
            "model": MODEL if request.mode == "astra" else None,
            "llm_used": False,
            "request": request.model_dump(),
            "compounds": selected,
            "created": job["created"],
            "updated": job["updated"],
            "cancel_requested": False,
            "error": None,
            "stages": [
                {
                    "id": sid,
                    "label": label,
                    "depends_on": deps,
                    "status": "pending",
                    "started": None,
                    "finished": None,
                    "result": None,
                    "error": None,
                }
                for sid, label, deps in STAGES
            ],
            "events": [],
            "result": None,
            "usage": {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "source_requests": 0},
        }
        self._event(
            state, "system", "queued", "Analysis queued with explicit budgets and durable checkpoints."
        )
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO analyses VALUES (?, ?)", (job["id"], json.dumps(state, allow_nan=False))
            )
        if start:
            self.schedule(job["id"])
        return self.get(job["id"])

    def get(self, run_id):
        with self.store.connect() as connection:
            row = connection.execute("SELECT state FROM analyses WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError("Analysis not found")
        return json.loads(row[0])

    def list(self):
        with self.store.connect() as connection:
            rows = connection.execute("SELECT state FROM analyses ORDER BY rowid DESC LIMIT 100").fetchall()
        return [json.loads(row[0]) for row in rows]

    def _mutate(self, run_id, callback):
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state FROM analyses WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError("Analysis not found")
            state = json.loads(row[0])
            callback(state)
            state["updated"] = utcnow()
            connection.execute(
                "UPDATE analyses SET state=? WHERE id=?",
                (json.dumps(state, ensure_ascii=False, allow_nan=False), run_id),
            )
        return state

    @staticmethod
    def _event(state, stage, status, summary, **extra):
        state["events"].append(
            {
                "seq": len(state["events"]) + 1,
                "time": utcnow(),
                "stage": stage,
                "type": extra.pop("type", "stage"),
                "status": status,
                "summary": summary,
                **extra,
            }
        )

    @staticmethod
    def _stage(state, sid):
        return next(stage for stage in state["stages"] if stage["id"] == sid)

    def schedule(self, run_id):
        with self._schedule_lock:
            prior = self._futures.get(run_id)
            if prior and not prior.done():
                return False
            self._futures[run_id] = self.pool.submit(self.run, run_id)
        return True

    def cancel(self, run_id):
        def mark(state):
            if state["status"] == "completed":
                return
            state["cancel_requested"] = True
            if state["status"] != "running":
                state["status"] = "cancelled"
            self._event(
                state,
                "system",
                "cancel_requested",
                "Stop requested. In-flight model requests finish within their timeout; no further work is dispatched. Submitted IBM jobs remain recorded.",
            )

        return self._mutate(run_id, mark)

    def resume(self, run_id):
        with self._schedule_lock:
            prior = self._futures.get(run_id)
            if prior and not prior.done():
                raise ValueError("Analysis is still active; wait for it to stop before resuming")

            def reset(state):
                if state["status"] not in {"blocked", "failed", "interrupted", "cancelled"}:
                    raise ValueError("Only a stopped analysis can be resumed")
                if (
                    state["mode"] == "astra"
                    and state["usage"]["llm_calls"] >= state["request"]["budgets"]["max_llm_calls"]
                ):
                    raise ValueError(
                        "LLM call budget exhausted; create a new analysis with a reviewed budget"
                    )
                state.update(status="queued", cancel_requested=False, error=None)
                for stage in state["stages"]:
                    if stage["status"] in {"failed", "blocked", "interrupted", "cancelled"}:
                        stage.update(status="pending", error=None)
                self._event(
                    state,
                    "system",
                    "queued",
                    "Resuming from durable checkpoints; external job identities are preserved.",
                )

            state = self._mutate(run_id, reset)
            self._futures[run_id] = self.pool.submit(self.run, run_id)
            return state

    def refresh_quantum(self, run_id):
        """Retrieve existing IBM job IDs only; never submit, execute or re-call an LLM."""
        state = self.get(run_id)
        if state["request"]["quantum_mode"] != "ibm":
            raise ValueError("This analysis did not request IBM Quantum")
        with (self.store.directory(run_id) / "analysis.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(
                    "Analysis is active; wait for the current worker before refreshing"
                ) from None
            state = self.get(run_id)
            if state["status"] in {"queued", "running"}:
                raise ValueError("Analysis is active; wait for it to stop before refreshing")
            manifest = self.store.directory(run_id) / "quantum.json"
            if not manifest.is_file():
                raise ValueError("No submitted IBM manifest exists for this analysis")
            previous = (
                self._stage(state, "quantum").get("result")
                or self._stage(state, "quantum").get("tool_result")
                or {}
            )
            retrieved = quantum.retrieve_kernel(manifest)
            intent = self.store.directory(run_id) / "quantum-intent.json"
            sample_ids = previous.get("sample_ids") or (
                json.loads(intent.read_text()).get("sample_ids", []) if intent.is_file() else []
            )
            result = {
                **retrieved,
                "mode": "ibm",
                "sample_ids": sample_ids,
                "feature_definition": previous.get("feature_definition"),
                "manifest_url": f"/api/jobs/{run_id}/artifacts/quantum.json",
                "summary": "Retrieved existing IBM job IDs; no new QPU job or LLM call was dispatched.",
            }
            changed = any(
                previous.get(key) != result.get(key) for key in ("status", "kernel", "hardware_executed")
            )
            artifact = self._artifact(run_id, "quantum", result)

            def save(current):
                self._stage(current, "quantum").update(
                    result=result, tool_result=result, status="completed", artifact=artifact, error=None
                )
                if changed:
                    for sid in ("review", "report"):
                        output = self._stage(current, sid).get("result")
                        if output:
                            output.update(
                                stale=True,
                                stale_reason="IBM execution state changed after this interpretation; inspect the refreshed quantum artifact.",
                            )
                current["result"] = self._collect(current)
                self._event(
                    current,
                    "quantum",
                    result.get("status", "refreshed"),
                    result["summary"],
                    type="quantum_refresh",
                    artifact=artifact,
                )

            current = self._mutate(run_id, save)
            for sid in ("review", "report"):
                output = self._stage(current, sid).get("result")
                if output and output.get("stale"):
                    refreshed_artifact = self._artifact(run_id, sid, output)
                    current = self._mutate(
                        run_id,
                        lambda value, stage_id=sid, saved=refreshed_artifact: self._stage(
                            value, stage_id
                        ).update(artifact=saved),
                    )
            self._artifact(run_id, "analysis", current["result"])
            self.store.update(run_id, current["status"], current["result"], current.get("error"))
            return current

    def _check_cancel(self, run_id):
        if self.get(run_id)["cancel_requested"]:
            raise AnalysisCancelled("Analysis cancelled at a dispatch boundary.")

    def _artifact(self, run_id, sid, data):
        artifact = self.store.write(run_id, sid + ".json", data)
        artifact["url"] = f"/api/jobs/{run_id}/artifacts/{artifact['file']}"
        return artifact

    def _decide(self, run_id, sid, context):
        state = self.get(run_id)
        stage = self._stage(state, sid)
        if stage.get("agent") and stage["agent"].get("prompt_version") == PROMPT_VERSION:
            return stage["agent"]["decision"]
        if state["mode"] == "local":
            return {
                "summary": "명시적으로 선택한 로컬 계산 워크플로입니다. LLM 해석은 실행하지 않았습니다.",
                "decision": "continue",
                "selected_ids": context.get("allowed_ids", []),
                "evidence_ids": context.get("evidence_ids", []),
                "requested_tools": context.get("allowed_tools", []),
                "candidate_policy": "balanced",
                "risks": [],
                "next_actions": [],
            }
        self._check_cancel(run_id)

        def reserve(current):
            if current["cancel_requested"]:
                raise AnalysisCancelled("Analysis cancelled before model dispatch.")
            if current["usage"]["llm_calls"] >= current["request"]["budgets"]["max_llm_calls"]:
                raise AnalysisBlocked(
                    "LLM call budget exhausted. No alternate model or local fallback was used."
                )
            current["usage"]["llm_calls"] += 1
            self._event(
                current,
                sid,
                "running",
                f"Calling {MODEL} for the {sid} role.",
                type="llm_request",
                model=MODEL,
            )

        self._mutate(run_id, reserve)
        context = {
            "goal": state["request"]["goal"],
            "target_id": state["request"]["target_id"],
            **context,
            "stage_context": {"current_stage": sid, "prompt_version": PROMPT_VERSION, **ROLE_CONTEXT[sid]},
        }
        try:
            result = self.provider.decide(
                sid,
                ROLE_INSTRUCTIONS[sid],
                context,
                max_output_tokens=state["request"]["budgets"]["max_output_tokens"],
                request_id=f"{run_id}:{sid}",
            )
        except LLMError as exc:

            def record_error(current, error=exc):
                for key in ("input_tokens", "output_tokens"):
                    current["usage"][key] += error.usage.get(key, 0)
                self._event(
                    current,
                    sid,
                    "blocked",
                    str(error),
                    type="llm_error",
                    response_id=error.response_id,
                    usage=error.usage,
                    code=error.code,
                )

            self._mutate(run_id, record_error)
            raise AnalysisBlocked(str(exc)) from exc

        def save(current):
            current["llm_used"] = True
            current_stage = self._stage(current, sid)
            if current_stage.get("agent"):
                current_stage.setdefault("agent_history", []).append(current_stage["agent"])
            current_stage["agent"] = {**result, "prompt_version": PROMPT_VERSION}
            for key in ("input_tokens", "output_tokens"):
                current["usage"][key] += result["usage"].get(key, 0)
            self._event(
                current,
                sid,
                "completed",
                result["decision"]["summary"],
                type="llm_response",
                response_id=result["response_id"],
                usage=result["usage"],
                model=MODEL,
            )

        self._mutate(run_id, save)
        return result["decision"]

    def _checkpoint(self, run_id, sid, data):
        self._mutate(run_id, lambda state: self._stage(state, sid).update(tool_result=data))
        return data

    def _execute_stage(self, run_id, sid, function):
        state = self.get(run_id)
        current = self._stage(state, sid)
        if current["status"] in {"completed", "skipped"}:
            return current["result"]
        self._check_cancel(run_id)

        def start(state):
            self._stage(state, sid).update(status="running", started=utcnow(), error=None)
            self._event(state, sid, "running", f"{sid} started.")

        self._mutate(run_id, start)
        try:
            result = function(run_id)
            artifact = self._artifact(run_id, sid, result)

            def finish(state):
                self._stage(state, sid).update(
                    status="completed", result=result, finished=utcnow(), artifact=artifact
                )
                self._event(
                    state,
                    sid,
                    "completed",
                    result.get("summary", f"{sid} checkpoint saved."),
                    artifact=artifact,
                )

            self._mutate(run_id, finish)
            self._check_cancel(run_id)
            return result
        except Exception as exc:
            status = (
                "cancelled"
                if isinstance(exc, AnalysisCancelled)
                else "blocked"
                if isinstance(exc, AnalysisBlocked)
                else "failed"
            )
            message = (
                str(exc)
                if isinstance(exc, (AnalysisCancelled, AnalysisBlocked))
                else f"{sid} failed ({type(exc).__name__}); completed artifacts were retained."
            )

            def fail(state):
                stage = self._stage(state, sid)
                if stage["status"] != "completed":
                    stage.update(status=status, error=message, finished=utcnow())
                self._event(state, sid, status, message)

            self._mutate(run_id, fail)
            raise

    def run(self, run_id):
        with (self.store.directory(run_id) / "analysis.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self.get(run_id)
            if self.get(run_id)["status"] == "completed":
                return self.get(run_id)
            try:
                self._check_cancel(run_id)
                if self.get(run_id)["mode"] == "astra":
                    status = self.provider.status(refresh=True)
                    if not status["available"]:
                        raise AnalysisBlocked(status.get("message", "Exact Astra model is unavailable."))
                self._mutate(run_id, lambda state: state.update(status="running", error=None))
                self._execute_stage(run_id, "coordinator", self._coordinator)
                # Independent roles have independent evidence and cannot see each other's intermediate output.
                with ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="herbfold-specialist"
                ) as specialists:
                    tasks = [
                        specialists.submit(self._execute_stage, run_id, sid, fn)
                        for sid, fn in (("evidence", self._evidence), ("chemistry", self._chemistry))
                    ]
                    failures = []
                    for task in tasks:
                        try:
                            task.result()
                        except Exception as exc:
                            failures.append(exc)
                    if failures:
                        raise failures[0]
                self._execute_stage(run_id, "candidate_design", self._candidate_design)
                # Execution boundaries are serialized to respect the workstation and QPU budget.
                self._execute_stage(run_id, "structure", self._structure)
                self._execute_stage(run_id, "quantum", self._quantum)
                self._execute_stage(run_id, "review", self._review)
                self._execute_stage(run_id, "report", self._report)

                def complete(state):
                    state.update(status="completed", result=self._collect(state), error=None)
                    self._event(
                        state,
                        "system",
                        "completed",
                        "Analysis finished; inspect execution states and evidence limits in the report.",
                    )

                final = self._mutate(run_id, complete)
                self._artifact(run_id, "analysis", final["result"])
                self.store.update(run_id, "completed", final["result"])
            except Exception as exc:
                status = (
                    "cancelled"
                    if isinstance(exc, AnalysisCancelled)
                    else "blocked"
                    if isinstance(exc, AnalysisBlocked)
                    else "failed"
                )
                message = (
                    str(exc)
                    if isinstance(exc, (AnalysisCancelled, AnalysisBlocked))
                    else f"Analysis failed ({type(exc).__name__}); use the persisted stage checkpoints."
                )

                def stopped(state):
                    state.update(status=status, error=message, result=self._collect(state))
                    self._event(state, "system", status, message)

                final = self._mutate(run_id, stopped)
                self.store.update(run_id, status, final["result"], message)
            return self.get(run_id)

    def _coordinator(self, run_id):
        state = self.get(run_id)
        allowed_tools = ["gather_evidence", "prepare_af3"]
        if state["request"]["quantum_mode"] != "off":
            allowed_tools.append("quantum_kernel")
        decision = self._decide(
            run_id,
            "coordinator",
            {
                "compounds": state["compounds"],
                "request": state["request"],
                "allowed_ids": [row["id"] for row in state["compounds"]],
                "evidence_ids": [],
                "allowed_tools": allowed_tools,
            },
        )
        if decision["decision"] == "stop":
            raise AnalysisBlocked("Coordinator stopped this objective: " + decision["summary"])
        ids = decision["selected_ids"] or [row["id"] for row in state["compounds"]]
        mapping = {row["id"]: row for row in state["compounds"]}
        selected = [mapping[cid] for cid in dict.fromkeys(ids)]
        if not any(row["category"] in {"herbal", "natural_product"} for row in selected) or not any(
            row["category"] == "drug" for row in selected
        ):
            raise AnalysisBlocked(
                "Planner must retain at least one herbal/natural-product parent and one drug reference."
            )
        return {
            "summary": decision["summary"],
            "decision": decision,
            "selected_compounds": selected,
            "routing": {
                "evidence": "gather_evidence" in decision["requested_tools"],
                "structure": "prepare_af3" in decision["requested_tools"],
                "quantum": "quantum_kernel" in decision["requested_tools"],
            },
            "candidate_policy": decision["candidate_policy"],
            "dag": [{"id": sid, "depends_on": deps} for sid, _, deps in STAGES],
        }

    def _plan(self, state):
        return self._stage(state, "coordinator")["result"]

    def _source_call(self, run_id, tool, argument):
        self._check_cancel(run_id)

        def reserve(state):
            if state["cancel_requested"]:
                raise AnalysisCancelled("Analysis cancelled before source dispatch.")
            if state["usage"]["source_requests"] >= state["request"]["budgets"]["max_source_requests"]:
                raise AnalysisBlocked("Source request budget exhausted")
            state["usage"]["source_requests"] += 1
            self._event(state, "evidence", "running", f"Reading {tool}: {argument}", type="tool")

        self._mutate(run_id, reserve)
        registry = {
            "pubchem_lookup": connectors.pubchem_lookup,
            "uniprot_lookup": connectors.uniprot_lookup,
            "chembl_activities": connectors.chembl_activities,
        }
        if tool not in registry:
            raise ValueError("Tool not allowed")
        return registry[tool](argument)

    def _evidence(self, run_id):
        state = self.get(run_id)
        cached = self._stage(state, "evidence").get("tool_result")
        if cached is None:
            compounds = self._plan(state)["selected_compounds"]
            records = [
                {
                    "id": (
                        "import:" if row.get("source_status") == "user_provided_unverified" else "catalog:"
                    )
                    + row["id"],
                    "kind": "user_import"
                    if row.get("source_status") == "user_provided_unverified"
                    else "catalog_snapshot",
                    "compound_id": row["id"],
                    "url": row.get("source_url"),
                    "retrieved_at": row.get("retrieved_at"),
                    "claim_scope": row.get(
                        "provenance_note",
                        "Chemical identity and supplied botanical occurrence only; no target affinity claim.",
                    ),
                    "source_status": row.get("source_status", "catalog_snapshot"),
                }
                for row in compounds
            ]
            warnings, target = [], None
            enabled = state["mode"] == "astra" and self._plan(state)["routing"]["evidence"]
            source_budget = state["request"]["budgets"]["max_source_requests"]
            # Retrieve the target first because it is a required input to structure preparation.
            target_id = state["request"]["target_id"].upper()
            calls = []
            if re.fullmatch(r"[A-Z0-9]{6,10}(?:-[0-9]+)?", target_id) and not target_id.startswith("CHEMBL"):
                calls.append(("uniprot_lookup", target_id, "target:" + target_id))
            elif re.fullmatch(r"CHEMBL[0-9]+", target_id):
                calls.append(("chembl_activities", target_id, "activities:" + target_id))
            calls += [
                ("pubchem_lookup", str(row["pubchem_cid"]), "pubchem:" + row["id"])
                for row in compounds
                if row.get("pubchem_cid")
            ]
            if enabled:
                for tool, argument, record_id in calls[:source_budget]:
                    try:
                        response = self._source_call(run_id, tool, argument)
                        record = {
                            "id": record_id,
                            "kind": "live_database",
                            "tool": tool,
                            "data": response,
                            "url": response.get("source"),
                            "retrieved_at": response.get("retrieved_at", utcnow()),
                        }
                        if tool == "pubchem_lookup":
                            original = next(row for row in compounds if "pubchem:" + row["id"] == record_id)
                            record["identity_matches_catalog"] = chemistry.canonical_smiles(
                                response["smiles"]
                            ) == chemistry.canonical_smiles(original["smiles"])
                            if not record["identity_matches_catalog"]:
                                warnings.append(
                                    f"Identity mismatch: {original['id']}; catalog input remains explicit, refreshed record requires review."
                                )
                        elif tool == "uniprot_lookup":
                            target = response
                        records.append(record)
                    except AnalysisCancelled:
                        raise
                    except (ValueError, KeyError, AnalysisBlocked):
                        warnings.append(
                            f"{tool} for {argument} unavailable; no source result was fabricated."
                        )
                if len(calls) > source_budget:
                    warnings.append("Source request budget truncated refreshes.")
            else:
                warnings.append(
                    "No live database refresh; provenance is the supplied catalog snapshot or explicitly unverified user import."
                )
            cached = self._checkpoint(
                run_id,
                "evidence",
                {
                    "records": records,
                    "target": target,
                    "warnings": warnings,
                    "affinity_status": "No target-specific measured affinity has been established for generated candidates.",
                },
            )
        decision = self._decide(
            run_id,
            "evidence",
            {
                **cached,
                "allowed_ids": [],
                "allowed_tools": [],
                "evidence_ids": [row["id"] for row in cached["records"]],
            },
        )
        if any(row.get("identity_matches_catalog") is False for row in cached["records"]):
            raise AnalysisBlocked(
                "A live PubChem identity differs from the catalog; resolve the source conflict before designing candidates."
            )
        if decision["decision"] == "stop":
            raise AnalysisBlocked("Evidence agent stopped the analysis: " + decision["summary"])
        return {**cached, "decision": decision, "summary": decision["summary"]}

    def _chemistry(self, run_id):
        state = self.get(run_id)
        cached = self._stage(state, "chemistry").get("tool_result")
        if cached is None:
            selected = self._plan(state)["selected_compounds"]
            cached = self._checkpoint(
                run_id,
                "chemistry",
                {
                    "compounds": [
                        {**row, "descriptors": chemistry.describe_molecule(row["smiles"])} for row in selected
                    ],
                    "comparisons": chemistry.compare_compounds(selected),
                    "method": "RDKit descriptors and chirality-aware Morgan Tanimoto",
                },
            )
        decision = self._decide(
            run_id,
            "chemistry",
            {
                **cached,
                "allowed_ids": [row["id"] for row in cached["compounds"]],
                "allowed_tools": [],
                "evidence_ids": [],
            },
        )
        if decision["decision"] == "stop":
            raise AnalysisBlocked("Chemistry agent stopped the analysis: " + decision["summary"])
        return {**cached, "decision": decision, "summary": decision["summary"]}

    def _candidate_design(self, run_id):
        state = self.get(run_id)
        cached = self._stage(state, "candidate_design").get("tool_result")
        if cached is None:
            selected = self._plan(state)["selected_compounds"]
            requested = state["request"]["max_candidates"]
            found = {}
            for herb in (row for row in selected if row["category"] in {"herbal", "natural_product"}):
                for drug in (row for row in selected if row["category"] == "drug"):
                    self._check_cancel(run_id)
                    remaining = requested - len(found)
                    if remaining <= 0:
                        break
                    for candidate in chemistry.generate_candidates(
                        [herb["smiles"], drug["smiles"]], remaining
                    ):
                        found.setdefault(
                            candidate["smiles"],
                            {
                                **candidate,
                                "herbal_parent_id"
                                if herb["category"] == "herbal"
                                else "natural_product_parent_id": herb["id"],
                                "drug_parent_id": drug["id"],
                            },
                        )
            candidates = list(found.values())
            policy = self._plan(state)["candidate_policy"]
            if policy == "diversity":
                candidates.sort(key=lambda row: (row["max_parent_similarity"], -row["descriptors"]["qed"]))
            elif policy == "qed":
                candidates.sort(key=lambda row: -row["descriptors"]["qed"])
            else:
                candidates.sort(
                    key=lambda row: (
                        len(row["descriptors"]["alerts"]),
                        row["descriptors"]["unassigned_stereocenters"],
                        -row["descriptors"]["qed"],
                    )
                )
            cached = self._checkpoint(
                run_id,
                "candidate_design",
                {
                    "generated_candidates": candidates,
                    "candidate_policy": policy,
                    "count": len(candidates),
                    "method": "Bounded cross-category BRICS enumeration with verified parent fragments",
                },
            )
        evidence = self._stage(state, "evidence")["result"]
        decision = self._decide(
            run_id,
            "candidate_design",
            {
                **cached,
                "chemistry_summary": self._stage(state, "chemistry")["result"]["summary"],
                "evidence_summary": evidence["summary"],
                "allowed_ids": [row["id"] for row in cached["generated_candidates"]],
                "allowed_tools": [],
                "evidence_ids": [row["id"] for row in evidence["records"]],
            },
        )
        if decision["decision"] == "stop":
            raise AnalysisBlocked("Candidate design halted execution: " + decision["summary"])
        mapping = {row["id"]: row for row in cached["generated_candidates"]}
        ids = list(dict.fromkeys(decision["selected_ids"]))
        if mapping and not ids:
            raise AnalysisBlocked(
                "Design agent selected no candidates; generated candidates remain in the checkpoint."
            )
        chosen = [mapping[cid] for cid in ids]
        return {**cached, "candidates": chosen, "decision": decision, "summary": decision["summary"]}

    def _structure(self, run_id):
        state = self.get(run_id)
        cached = self._stage(state, "structure").get("tool_result") or {"af3_jobs": []}
        if not self._plan(state)["routing"]["structure"]:
            return {
                **cached,
                "status": "not_requested_by_plan",
                "summary": "Planner omitted structure work for this analysis.",
            }
        target = self._stage(state, "evidence")["result"].get("target") or {}
        sequence = state["request"]["protein_sequence"] or target.get("sequence", "")
        if state["request"]["protein_sequence"] and target.get("sequence") and sequence != target["sequence"]:
            sequence_scope = (
                "User-supplied sequence differs from canonical UniProt; treated as an explicit construct."
            )
        else:
            sequence_scope = (
                "User-supplied sequence"
                if state["request"]["protein_sequence"]
                else "Retrieved UniProt sequence"
            )
        if not sequence:
            return {
                **cached,
                "status": "needs_sequence",
                "summary": "AF3 input requires a protein sequence; no protein coordinates were fabricated.",
            }
        candidates = self._stage(state, "candidate_design")["result"]["candidates"][
            : state["request"]["budgets"]["max_af3_candidates"]
        ]
        for candidate in candidates:
            self._check_cancel(run_id)
            existing = next(
                (row for row in cached["af3_jobs"] if row["candidate_id"] == candidate["id"]), None
            )
            if not existing:
                data = alphafold.build_input(
                    name=candidate["id"],
                    proteins=[{"id": "A", "sequence": sequence}],
                    ligands=[{"id": "B", "smiles": candidate["smiles"]}],
                    seeds=[1],
                    msa_mode=state["request"]["msa_mode"],
                )
                job = self.store.create("alphafold", data)
                existing = {
                    "job_id": job["id"],
                    "candidate_id": candidate["id"],
                    "status": "preparing",
                    "execution_requested": state["request"]["run_af3"],
                    "sequence_scope": sequence_scope,
                    "protein_sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                }
                cached["af3_jobs"].append(existing)
                self._checkpoint(run_id, "structure", cached)
            job = self.store.get(existing["job_id"])
            if job["status"] == "prepared" and not job.get("result"):
                plan = alphafold.prepare_job(self.store.directory(job["id"]), job["payload"])
                job = self.store.update(job["id"], "prepared", plan)
            if state["request"]["run_af3"]:
                job = self._run_af3(run_id, job)
            existing.update(
                status=job["status"],
                result=job["result"],
                error=job.get("error"),
                input_url=f"/api/jobs/{job['id']}/artifacts/fold_input.json",
            )
            self._checkpoint(run_id, "structure", cached)
        return {
            **cached,
            "status": "processed",
            "summary": f"{len(cached['af3_jobs'])} bounded AF3 job(s) processed; per-job state distinguishes preparation from inference.",
        }

    def _run_af3(self, run_id, job):
        if job["status"] == "completed":
            return job
        if job["status"] in {"running", "queued", "interrupted", "failed", "cancelled"}:
            # Even a crash before a PID was stored must not trigger a second native run.
            return self.store.update(
                job["id"],
                "interrupted",
                job["result"],
                "An earlier execution was recorded. Inspect saved outputs; automatic re-execution is disabled.",
            )
        plan = job["result"]
        if not plan.get("runnable", False):
            return self.store.update(job["id"], "blocked", plan, "; ".join(plan.get("blockers", [])))
        lock_path = self.store.root / "af3-execution.lock"
        timeout = self.get(run_id)["request"]["budgets"]["af3_timeout_seconds"]
        with lock_path.open("a") as lock:
            waiting = time.monotonic()
            while True:
                self._check_cancel(run_id)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - waiting > timeout:
                        raise AnalysisBlocked("AF3 worker wait exceeded the declared timeout.")
                    time.sleep(0.25)
            self._check_cancel(run_id)
            # A saved readiness manifest may predate parameter replacement or
            # stricter integrity checks. Recheck under the shared device lock.
            plan = alphafold.prepare_job(self.store.directory(job["id"]), job["payload"])
            if not plan.get("runnable", False):
                return self.store.update(job["id"], "blocked", plan, "; ".join(plan.get("blockers", [])))
            if not self.store.claim(job["id"]):
                raise AnalysisBlocked("AF3 job was already claimed; execution was not repeated.")
            try:
                self._check_cancel(run_id)
            except AnalysisCancelled:
                # No process has been launched; this prepared input remains resumable.
                self.store.update(job["id"], "prepared", plan)
                raise
            from .af3_execution import process_identity

            plan["execution"] = {"owner": process_identity()}
            self.store.update(job["id"], "running", plan)
            with (self.store.directory(job["id"]) / "run.log").open("a") as log:
                process = subprocess.Popen(
                    plan["command"],
                    cwd=plan.get("cwd"),
                    env=alphafold.execution_environment(),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    start_new_session=True,
                    pass_fds=(lock.fileno(),),
                )
                plan["execution"]["child"] = process_identity(process.pid)
                self.store.update(job["id"], "running", plan)
                started = time.monotonic()
                try:
                    while process.poll() is None:
                        self._check_cancel(run_id)
                        if time.monotonic() - started > timeout:
                            raise AnalysisBlocked("AF3 inference exceeded the declared wall-time budget.")
                        time.sleep(0.25)
                except BaseException:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    self.store.update(
                        job["id"],
                        "cancelled",
                        plan,
                        "Execution stopped; this job will not be automatically rerun.",
                    )
                    raise
            if process.returncode:
                return self.store.update(
                    job["id"], "failed", plan, f"AF3 exited with code {process.returncode}; see run.log."
                )
            result = alphafold.parse_outputs(plan["output_dir"])
            if not result.get("models"):
                return self.store.update(job["id"], "failed", result, "AF3 exited without model outputs.")
            result.update(
                execution_verified=True,
                execution_provenance=plan["provenance"],
                execution=plan.get("execution"),
                return_code=process.returncode,
            )
            self.store.write(job["id"], "result.json", result)
            return self.store.update(job["id"], "completed", result)

    def _quantum(self, run_id):
        state = self.get(run_id)
        if state["request"]["quantum_mode"] == "off" or not self._plan(state)["routing"]["quantum"]:
            return {
                "status": "off",
                "hardware_executed": False,
                "summary": "Quantum computation omitted by the explicit mode or planner.",
            }
        cached = self._stage(state, "quantum").get("tool_result")
        if cached:
            return cached
        items = (
            self._stage(state, "candidate_design")["result"]["candidates"]
            + self._stage(state, "chemistry")["result"]["compounds"]
        )
        mode = state["request"]["quantum_mode"]
        items = items[: 4 if mode == "ibm" else 8]
        features = [
            [
                row["descriptors"][key] / scale
                for key, scale in (("molecular_weight", 650), ("logp", 6), ("tpsa", 180), ("qed", 1))
            ]
            for row in items
        ]
        if len(features) < 2:
            return {
                "status": "insufficient_samples",
                "hardware_executed": False,
                "summary": "Quantum kernel requires two molecular samples.",
            }
        sample_ids = [row["id"] for row in items]
        feature_definition = {
            "columns": ["molecular_weight", "logp", "tpsa", "qed"],
            "divisors": [650, 6, 180, 1],
            "features": features,
            "scope": "RDKit molecular descriptors only; no protein, assay label or electronic Hamiltonian is encoded.",
        }
        if mode == "local":
            result = quantum.local_kernel(
                features, n_qubits=4, layers=1, max_circuits=36,
                kernel_method="projected", block_size=4, gamma=1.0,
            )
            result.update(
                status="completed",
                mode="local",
                sample_ids=sample_ids,
                feature_definition=feature_definition,
                hardware_executed=False,
                summary="4-qubit exact local X/Y/Z projected feature kernel; molecular descriptor similarity, no QPU execution or affinity inference.",
            )
            return self._checkpoint(run_id, "quantum", result)
        budgets = state["request"]["budgets"]
        manifest = self.store.directory(run_id) / "quantum.json"
        intent = self.store.directory(run_id) / "quantum-intent.json"
        parameters = dict(
            mode="ibm",
            qubits="max",
            kernel_method="projected",
            block_size=4,
            gamma=1.0,
            shots=min(1024, budgets["max_qpu_shots"] // (3 * len(items) + 5)),
            max_circuits=32,
            circuits_per_job=32,
            max_total_shots=budgets["max_qpu_shots"],
            max_jobs=budgets["max_qpu_jobs"],
            max_execution_time=budgets["max_qpu_seconds"],
            layers=1,
        )
        self._check_cancel(run_id)
        if manifest.exists():
            result = quantum.retrieve_kernel(manifest)
        elif intent.exists():
            raise AnalysisBlocked(
                "IBM submission intent exists without a recoverable manifest. Inspect IBM jobs before creating a new experiment; no duplicate submission was attempted."
            )
        else:
            plan = quantum.plan_quantum(features, **parameters)
            if plan["status"] != "ready":
                return {
                    "status": plan["status"],
                    "mode": "ibm",
                    "plan": plan,
                    "hardware_executed": False,
                    "summary": "IBM backend preflight did not permit execution; no QPU result was fabricated.",
                }

            # Cancellation can arrive during network-backed device inspection.
            # Recheck it atomically at the irreversible submission boundary.
            def reserve_submission(current):
                if current["cancel_requested"]:
                    raise AnalysisCancelled("Analysis cancelled after IBM preflight; no job was submitted.")
                self._event(
                    current,
                    "quantum",
                    "submitting",
                    "IBM submission dispatch reserved within the declared budget.",
                    type="external_dispatch",
                )

            self._mutate(run_id, reserve_submission)
            # Reserve before submission; protects the pre-manifest crash window as well.
            with intent.open("x") as output:
                json.dump({"created": utcnow(), "sample_ids": sample_ids, "plan": plan}, output)
                output.flush()
                os.fsync(output.fileno())
            result = quantum.submit_kernel(features, manifest, execute=True, **parameters)
            result = quantum.retrieve_kernel(manifest)
        result.update(
            mode="ibm",
            sample_ids=sample_ids,
            feature_definition=feature_definition,
            manifest_url=f"/api/jobs/{run_id}/artifacts/quantum.json",
            summary="Maximum accessible IBM width in small connected blocks; X/Y/Z features, duplicate control and readout checks within declared budgets. Submitted is distinct from measured.",
        )
        return self._checkpoint(run_id, "quantum", result)

    def _review(self, run_id):
        state = self.get(run_id)
        collected = self._collect(state)
        # Keep model context bounded: AF3 commands, raw coordinates and environments never enter prompts.
        context = self._report_context(state)
        decision = self._decide(run_id, "review", context)
        return {
            "decision": decision,
            "summary": decision["summary"],
            "verdict": "needs_revision" if decision["decision"] == "stop" else "research_only",
            "affinity_established": False,
            "efficacy_established": False,
            "external_novelty_verified": False,
            "candidate_count": len(collected["candidates"]),
        }

    def _report(self, run_id):
        state = self.get(run_id)
        decision = self._decide(
            run_id,
            "report",
            {**self._report_context(state), "review": self._stage(state, "review")["result"]},
        )
        return {
            "summary": decision["summary"],
            "decision": decision,
            "llm_used": state["llm_used"],
            "model": state["model"],
            "research_status": "computational_hypotheses",
            "generated_at": utcnow(),
        }

    def _report_context(self, state):
        result = self._collect(state)
        return {
            "allowed_ids": [row["id"] for row in result["candidates"]],
            "allowed_tools": [],
            "evidence_ids": [row["id"] for row in (result["evidence"] or {}).get("records", [])],
            "comparisons": result["comparisons"],
            "candidates": result["candidates"],
            "evidence_summary": (result["evidence"] or {}).get("summary"),
            "af3_jobs": [
                {
                    "job_id": row["job_id"],
                    "candidate_id": row["candidate_id"],
                    "status": row["status"],
                    "error": row.get("error"),
                    "execution_verified": (row.get("result") or {}).get("execution_verified", False),
                    "models": (row.get("result") or {}).get("models", []),
                    "msa_mode": state["request"]["msa_mode"],
                }
                for row in result["af3_jobs"]
            ],
            "quantum": {
                key: (result["quantum"] or {}).get(key)
                for key in (
                    "status", "mode", "kernel_method", "method", "estimator", "hardware_executed", "sample_ids",
                    "kernel", "kernel_diagnostics", "summary",
                )
            },
            "affinity": None,
            "affinity_status": "No measured target-specific candidate affinity has been supplied.",
        }

    def _collect(self, state):
        def data(sid):
            stage = self._stage(state, sid)
            return stage.get("result") or stage.get("tool_result") or {}

        return {
            "comparisons": data("chemistry").get("comparisons", []),
            "candidates": data("candidate_design").get(
                "candidates", data("candidate_design").get("generated_candidates", [])
            ),
            "af3_jobs": data("structure").get("af3_jobs", []),
            "quantum": data("quantum"),
            "evidence": data("evidence"),
            "review": data("review"),
            "report": data("report"),
            "affinity": None,
            "affinity_status": "Requires target-specific measured training and external validation",
            "model": state["model"],
            "llm_used": state["llm_used"],
        }
