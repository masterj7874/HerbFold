"""Selected-compound preparation, durable identity indexing and explicit execution."""

from __future__ import annotations

import fcntl
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import af3_databases, af3_diagnostics, af3_parameters, alphafold, molecular_selection
from .af3_execution import process_is_alive

EXPLORATORY_NOTE = "MSA·template를 사용하지 않는 명시적 탐색 계산입니다. 표준 AF3 분석보다 정확도가 크게 낮아질 수 있으며, 구조 신뢰도는 결합 친화도·효능·안전성 검증이 아닙니다."


class StudioPredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    smiles: str = Field(min_length=1, max_length=5000)
    target_accession: str = Field(default="P35354", pattern=r"^[A-Z0-9][A-Z0-9-]{1,19}$")
    msa_mode: Literal["search", "none"] = "search"
    seeds: list[int] = Field(default_factory=lambda: [1], min_length=1, max_length=5)
    exploratory_ack: bool = False
    retry: bool = False


class StudioPredictions:
    def __init__(self, store, queue):
        self.store, self.queue = store, queue
        with store.connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS studio_predictions ("
                "job_id TEXT PRIMARY KEY, request_key TEXT NOT NULL, canonical_smiles TEXT NOT NULL, "
                "target_accession TEXT NOT NULL, request TEXT NOT NULL, retry_of TEXT)"
            )
            con.execute("CREATE INDEX IF NOT EXISTS studio_request_key ON studio_predictions(request_key)")
            con.execute(
                "CREATE INDEX IF NOT EXISTS studio_selection ON studio_predictions(canonical_smiles,target_accession)"
            )
        queue.after_complete = self.validate_output

    def _record(self, job_id):
        self.store.directory(job_id)  # reject traversal before any artifact access
        with self.store.connect() as con:
            row = con.execute("SELECT * FROM studio_predictions WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("This is not a molecular studio prediction job")
        return dict(row)

    def get(self, job_id, *, reused=False):
        record = self._record(job_id)
        job = self.store.get(job_id)
        result = job.get("result") or {}
        request = json.loads(record["request"])
        position = None
        if job["status"] == "queued":
            with self.store.connect() as con:
                position = con.execute(
                    "SELECT COUNT(*) FROM jobs WHERE kind='alphafold' AND status='queued' "
                    "AND (created<? OR (created=? AND id<=?))",
                    (job["created"], job["created"], job_id),
                ).fetchone()[0]
        warnings = list(result.get("warnings") or [])
        if request["msa_mode"] == "none" and EXPLORATORY_NOTE not in warnings:
            warnings.append(EXPLORATORY_NOTE)
        return {
            "job": job,
            "requested": request,
            "reused": reused,
            "retry_of": record["retry_of"],
            "readiness": {
                "runnable": result.get("runnable", result.get("ready", False)),
                "blockers": result.get("blockers", []),
                "warnings": warnings,
                "parameter_status": result.get("parameter_status")
                or result.get("execution_provenance", {}).get("parameters", {}).get("status"),
                "parameters": result.get("provenance", result.get("execution_provenance", {})).get(
                    "parameters"
                ),
                "databases": result.get("provenance", result.get("execution_provenance", {})).get(
                    "databases"
                ),
            },
            "stage": {"name": job["status"], "label": job["status"]}
            if job["status"] in {"queued", "blocked", "failed", "interrupted"}
            else result.get("stage"),
            "msa_features": result.get("msa_features"),
            "queue_position": position,
            "orphan_process_active": process_is_alive(result.get("execution", {}).get("child"))
            if job["status"] == "interrupted"
            else False,
            "input_url": f"/api/jobs/{job_id}/artifacts/fold_input.json",
            "log_url": f"/api/molecular/predictions/{job_id}/log",
            "scene_url": f"/api/molecular/predictions/{job_id}/scene",
            "output_validation": result.get("output_validation"),
            "execution_note": "완료는 AF3 프로세스와 출력 읽기가 끝났다는 뜻입니다. 구조 품질·결합·효능·안전성 검증 통과를 의미하지 않습니다.",
        }

    def list(self, smiles, target_accession="P35354"):
        canonical = molecular_selection.exact_identity(smiles)
        target_accession = molecular_selection.canonical_target_accession(self.store, target_accession)
        with self.store.connect() as con:
            rows = con.execute(
                "SELECT p.job_id FROM studio_predictions p JOIN jobs j ON j.id=p.job_id "
                "WHERE p.canonical_smiles=? AND p.target_accession=? ORDER BY j.created DESC,j.id DESC LIMIT 20",
                (canonical, target_accession),
            ).fetchall()
        return {"items": [self.get(row[0]) for row in rows], "limit": 20}

    def prepare(self, request: StudioPredictionRequest):
        canonical = molecular_selection.exact_identity(request.smiles)
        target = molecular_selection.registered_target(self.store, request.target_accession)
        if not target:
            raise ValueError("이 표적의 서열이 등록되어 있지 않습니다. UniProt 표적을 먼저 조회·등록한 뒤 AF3 입력을 준비하세요.")
        sequence = target["sequence"]
        target_accession = target["accession"]
        sequence_sha256 = hashlib.sha256(sequence.encode()).hexdigest()
        if target.get("sequence_sha256") != sequence_sha256:
            raise ValueError("등록된 표적 서열과 해시가 일치하지 않아 AF3 입력을 만들 수 없습니다.")
        target_provenance = {key: value for key, value in target.items() if key != "sequence"}
        if request.msa_mode == "none" and not request.exploratory_ack:
            raise ValueError(
                "MSA·template 미사용 탐색 모드는 정확도 저하 설명을 확인하고 exploratory_ack=true로 명시해야 합니다."
            )
        config = alphafold.AF3Config.from_env()
        profile = {
            key: str(value) if isinstance(value, Path) else value for key, value in asdict(config).items()
        }
        parameter_inspection = af3_parameters.inspect_parameters(config)
        child_env = alphafold.execution_environment()
        submission_context = {
            "parameter_stat_fingerprint_sha256": parameter_inspection["provenance"].get(
                "stat_fingerprint_sha256"
            ),
            "cuda_environment": {
                key: child_env.get(key)
                for key in (
                    "LD_LIBRARY_PATH",
                    "CUDA_VISIBLE_DEVICES",
                    "XLA_PYTHON_CLIENT_PREALLOCATE",
                    "XLA_FLAGS",
                )
            },
        }
        if request.msa_mode == "search":
            database_inspection = af3_databases.inspect_databases(config)
            submission_context["database_fingerprint"] = database_inspection["provenance"].get(
                "fingerprint_sha256"
            )
        identity = {
            "canonical_smiles": canonical,
            "target_accession": target_accession,
            "target_sequence_sha256": sequence_sha256,
            "msa_mode": request.msa_mode,
            "seeds": request.seeds,
            "af3_version": alphafold.AF3_VERSION,
            "af3_commit": alphafold.AF3_COMMIT,
            "execution_profile": profile,
            "submission_context": submission_context,
        }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        payload = alphafold.build_input(
            name=f"studio_{target_accession}_{key[:12]}",
            proteins=[{"id": "A", "sequence": sequence}],
            ligands=[{"id": "B", "smiles": canonical}],
            seeds=request.seeds,
            msa_mode=request.msa_mode,
        )
        requested = {
            **identity,
            "smiles": request.smiles,
            "exploratory_ack": request.exploratory_ack,
            "target_provenance": target_provenance,
            "submitted_target_accession": request.target_accession,
        }
        # A filesystem lock covers creation + slow preflight without a long
        # SQLite write transaction. Concurrent tabs/processes reuse one job.
        with (self.store.root / "af3-studio-prepare.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.store.connect() as con:
                previous = con.execute(
                    "SELECT p.job_id FROM studio_predictions p JOIN jobs j ON j.id=p.job_id "
                    "WHERE p.request_key=? ORDER BY j.created DESC,j.id DESC LIMIT 1",
                    (key,),
                ).fetchone()
            retry_of = None
            if previous:
                prior = self.get(previous[0], reused=True)
                if prior["job"]["status"] == "prepared" and prior["job"]["result"] is None:
                    self.store.update(
                        previous[0],
                        "interrupted",
                        None,
                        "AF3 input preparation was interrupted before its manifest was saved; prepare an explicit retry.",
                    )
                    prior = self.get(previous[0], reused=True)
                if not request.retry or prior["job"]["status"] in {
                    "prepared",
                    "queued",
                    "running",
                    "completed",
                }:
                    return prior
                if prior["orphan_process_active"]:
                    raise ValueError("이전 AF3 프로세스가 아직 실행 중입니다. 종료를 확인한 뒤 재시도하세요.")
                retry_of = previous[0]
            job = self.store.create("alphafold", payload)
            with self.store.connect() as con:
                con.execute(
                    "INSERT INTO studio_predictions VALUES (?,?,?,?,?,?)",
                    (
                        job["id"],
                        key,
                        canonical,
                        target_accession,
                        json.dumps(requested, ensure_ascii=False),
                        retry_of,
                    ),
                )
            try:
                plan = alphafold.prepare_job(self.store.directory(job["id"]), payload, config)
                plan["execution_profile"] = profile
                plan["submission_context"] = submission_context
                plan["target_provenance"] = target_provenance
                plan["target_sequence_sha256"] = sequence_sha256
                self.store.write(job["id"], "af3_manifest.json", plan)
                status = "prepared" if plan.get("runnable", plan.get("ready", False)) else "blocked"
                self.store.update(job["id"], status, plan, "; ".join(plan.get("blockers", [])) or None)
            except Exception as exc:
                self.store.update(
                    job["id"],
                    "failed",
                    {"execution_profile": profile},
                    f"AF3 preparation failed ({type(exc).__name__})",
                )
            return self.get(job["id"])

    def execute(self, job_id):
        self._record(job_id)
        try:
            self.queue.submit(job_id)
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return self.get(job_id)

    def scene(self, job_id):
        requested = json.loads(self._record(job_id)["request"])
        response = molecular_selection.resolve(
            self.store,
            requested["canonical_smiles"],
            "alphafold3_prediction",
            requested["target_accession"],
            job_id=job_id,
        )
        scene = response.get("scene")
        if scene is not None:
            scene["metadata"]["prediction_request"] = requested
            scene["metadata"]["output_validation"] = (self.store.get(job_id).get("result") or {}).get(
                "output_validation"
            )
            if requested["msa_mode"] == "none":
                scene["warnings"].append(EXPLORATORY_NOTE)
        return response

    def diagnostics(self, job_id, **selection):
        requested = json.loads(self._record(job_id)["request"])
        return af3_diagnostics.selected_diagnostics(self.store, job_id, requested, **selection)

    def validate_output(self, job_id):
        # Legacy API jobs are not selected-compound jobs; their parse result
        # remains available without making an unsupported identity assertion.
        try:
            response = self.scene(job_id)
        except KeyError:
            return {
                "status": "not_assessed",
                "identity_verified": False,
                "quality_pass": None,
                "warnings": ["No studio selection contract is registered for this job"],
            }
        scene = response.get("scene")
        if scene is None:
            return {
                "status": "identity_failed",
                "identity_verified": False,
                "quality_pass": False,
                "warnings": [response.get("reason", "Actual target/ligand output identity did not match")],
            }
        geometry = scene.get("geometry") or {}
        metrics = scene["metadata"].get("summary_metrics", {})
        flagged = bool(
            geometry.get("long_bond_count") or geometry.get("short_bond_count") or metrics.get("has_clash")
        )
        return {
            "status": "geometry_warning" if flagged else "identity_verified_quality_unassessed",
            "identity_verified": True,
            "quality_pass": False if flagged else None,
            "geometry": geometry,
            "summary_metrics": metrics,
            "artifact": scene["metadata"]["artifact"],
            "sha256": scene["metadata"]["sha256"],
            "selected_ligand_atom_count": len(scene["metadata"]["selected_ligand_atom_ids"]),
            "warnings": scene["warnings"],
            "affinity": None,
            "efficacy": "not_assessed",
            "safety": "not_assessed",
        }

    def log(self, job_id):
        self._record(job_id)
        path = self.store.directory(job_id) / "run.log"
        if not path.exists():
            return {"text": "", "truncated": False, "status": self.store.get(job_id)["status"]}
        path = self.store.artifact(job_id, "run.log")
        size = path.stat().st_size
        with path.open("rb") as stream:
            stream.seek(max(0, size - 65536))
            text = stream.read(65536).decode("utf-8", errors="replace")
        return {"text": text, "truncated": size > 65536, "status": self.store.get(job_id)["status"]}


def make_router(store, queue):
    router = APIRouter(prefix="/api/molecular/predictions", tags=["molecular predictions"])
    service = StudioPredictions(store, queue)
    router.service = service

    @router.post("/prepare")
    def prepare(request: StudioPredictionRequest):
        return service.prepare(request)

    @router.get("")
    def selected_jobs(
        smiles: str = Query(min_length=1, max_length=5000),
        target_accession: str = Query(default="P35354", pattern=r"^[A-Z0-9][A-Z0-9-]{1,19}$"),
    ):
        return service.list(smiles, target_accession)

    @router.get("/{job_id}")
    def status(job_id: str):
        return service.get(job_id)

    @router.post("/{job_id}/execute")
    def execute(job_id: str):
        return service.execute(job_id)

    @router.get("/{job_id}/scene")
    def scene(job_id: str):
        return service.scene(job_id)

    @router.get("/{job_id}/log")
    def log(job_id: str):
        return service.log(job_id)

    @router.get("/{job_id}/diagnostics")
    def diagnostics(
        job_id: str,
        file: str | None = Query(default=None, max_length=1000),
        smiles: str | None = Query(default=None, min_length=1, max_length=5000),
        target_accession: str | None = Query(default=None, pattern=r"^[A-Z0-9][A-Z0-9-]{1,19}$"),
    ):
        return service.diagnostics(job_id, file=file, smiles=smiles, target_accession=target_accession)

    return router
