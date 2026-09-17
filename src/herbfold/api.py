"""Local-first API with durable jobs and real external-engine boundaries."""

from __future__ import annotations

import csv
import hmac
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import (
    __version__,
    alphafold,
    benchmark,
    chemistry,
    connectors,
    curation,
    features,
    kernel_model,
    quantum,
)
from .af3_execution import AF3Queue
from .storage import Store
from .structure import pocket_features

load_dotenv()
STATIC = Path(__file__).parent / "static"
WEB = Path(__file__).parent / "web"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MoleculeRequest(StrictModel):
    smiles: str = Field(min_length=1, max_length=5000)


class CompareRequest(StrictModel):
    compounds: list[dict] = Field(min_length=2, max_length=100)


class StructureCompareRequest(StrictModel):
    compounds: list[dict] = Field(min_length=2, max_length=8)


class GenerateRequest(StrictModel):
    parent_smiles: list[str] = Field(min_length=2, max_length=8)
    max_candidates: int = Field(default=12, ge=1, le=100)


class AF3Request(StrictModel):
    name: str = Field(default="herbfold_complex", min_length=1, max_length=100)
    proteins: list[dict] = Field(min_length=1, max_length=16)
    ligands: list[dict] = Field(min_length=1, max_length=16)
    seeds: list[int] = Field(default_factory=lambda: [1, 2, 3], min_length=1, max_length=20)
    msa_mode: str = "search"


class QuantumRequest(StrictModel):
    features: list[list[float]] = Field(min_length=2, max_length=32)
    mode: Literal["local", "ibm"] = "local"
    kernel_method: Literal["projected", "fidelity"] = "projected"
    block_size: int = Field(default=6, ge=1, le=6)
    gamma: float = Field(default=1.0, gt=0, le=100)
    qubits: int | str | None = None
    shots: int = Field(default=1024, ge=1, le=100000)
    max_circuits: int = Field(default=64, ge=1, le=512)
    circuits_per_job: int = Field(default=16, ge=1, le=64)
    max_total_shots: int = Field(default=65536, ge=1, le=1000000)
    max_jobs: int = Field(default=4, ge=1, le=16)
    max_execution_time: int = Field(default=120, ge=1, le=600)
    layers: int = Field(default=1, ge=1, le=4)
    backend_name: str | None = None
    source_analysis_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    sample_ids: list[str] = Field(default_factory=list, max_length=32)
    sample_labels: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def sample_metadata(self):
        for name in ("sample_ids", "sample_labels"):
            values = getattr(self, name)
            if values and len(values) != len(self.features):
                raise ValueError(f"{name} must match feature row count and order")
            if any(not item.strip() or len(item) > 200 for item in values):
                raise ValueError(f"{name} entries must contain 1 to 200 characters")
        if self.sample_ids and len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("sample_ids must be distinct")
        return self


class RecordsRequest(StrictModel):
    records: list[dict] = Field(min_length=1, max_length=5000)
    endpoint: str = "Kd"
    split: str = "scaffold"
    test_fraction: float = Field(default=0.25, gt=0, lt=1)
    seed: int = 42


class PredictRequest(StrictModel):
    model_id: str
    queries: list[dict] = Field(min_length=1, max_length=1000)


class ImportRequest(StrictModel):
    files: dict[str, str]


class WorkflowRequest(StrictModel):
    compound_ids: list[str] = Field(default_factory=list, max_length=8)
    compounds: list[dict] = Field(default_factory=list, max_length=8)
    max_candidates: int = Field(default=8, ge=1, le=50)
    protein_sequence: str = Field(default="", max_length=10000)
    target_id: str = Field(default="custom", max_length=100)


class KernelEvaluateRequest(StrictModel):
    quantum_job_id: str


def create_app(data_dir=None):
    store = Store(data_dir)
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="herbfold")
    af3_queue = AF3Queue(store)

    @asynccontextmanager
    async def lifespan(app):
        af3_queue.recover()
        app.state.discovery.recover()
        app.state.af3_workflows.recover()
        yield
        app.state.af3_workflows.close()
        af3_queue.close()
        app.state.discovery.close()
        app.state.design_pipeline.close()
        pool.shutdown(wait=False)

    app = FastAPI(title="HerbFold Research API", version=__version__, lifespan=lifespan)
    app.state.store = store
    app.state.af3_queue = af3_queue
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=os.getenv("HERBFOLD_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver").split(","),
    )

    @app.middleware("http")
    async def access_control(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            token = os.getenv("HERBFOLD_API_TOKEN")
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token and not hmac.compare_digest(token, supplied):
                return JSONResponse({"detail": "API authentication required"}, status_code=401)
            origin = request.headers.get("origin")
            if request.method != "GET" and origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin writes are disabled"}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 50_000_000:
                    return JSONResponse({"detail": "Request exceeds 50 MB"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid content length"}, status_code=400)
        result = await call_next(request)
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "same-origin"
        return result

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(RequestValidationError)
    async def invalid_schema(request, exc):
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]} for error in exc.errors()
        ]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.get("/")
    def index():
        return FileResponse((WEB if (WEB / "index.html").is_file() else STATIC) / "index.html")

    @app.get("/legacy")
    def legacy_index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/evidence/example")
    def evidence_example():
        path = Path(__file__).resolve().parents[2] / "data" / "benchmarks" / "ptgs2_ki.json"
        if not path.is_file():
            path = Path(__file__).parent / "data" / "benchmarks" / "ptgs2_ki.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": __version__,
            "alphafold": alphafold.capabilities(),
            "mode": "research",
            "affinity_model": "requires measured training data",
        }

    @app.get("/api/catalog")
    def catalog():
        return chemistry.load_catalog()

    @app.post("/api/molecules/describe")
    def describe(req: MoleculeRequest):
        return chemistry.describe_molecule(req.smiles)

    @app.post("/api/molecules/svg")
    def svg(req: MoleculeRequest):
        from rdkit import Chem
        from rdkit.Chem.Draw import rdMolDraw2D

        mol = Chem.MolFromSmiles(req.smiles)
        if mol is None:
            raise ValueError("Invalid SMILES")
        drawer = rdMolDraw2D.MolDraw2DSVG(360, 240)
        drawer.DrawMolecule(mol)
        drawer.FinishDrawing()
        return Response(drawer.GetDrawingText(), media_type="image/svg+xml")

    @app.post("/api/compare")
    def compare(req: CompareRequest):
        return chemistry.compare_compounds(req.compounds)

    @app.post("/api/compare/structures")
    def compare_structures(req: StructureCompareRequest):
        return chemistry.compare_structures(req.compounds)

    @app.post("/api/candidates")
    def candidates(req: GenerateRequest):
        return chemistry.generate_candidates(req.parent_smiles, req.max_candidates)

    @app.get("/api/sources/pubchem")
    def pubchem(query: str):
        return connectors.pubchem_lookup(query)

    @app.get("/api/sources/uniprot/{accession}")
    def uniprot(accession: str):
        return connectors.uniprot_lookup(accession)

    @app.get("/api/sources/chembl/{target_id}")
    def chembl(target_id: str, endpoint: str = "Kd", limit: int = 100):
        return connectors.chembl_activities(target_id, endpoint, limit)

    @app.get("/api/jobs")
    def jobs():
        return store.list()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return store.get(job_id)

    @app.get("/api/jobs/{job_id}/artifacts/{filename:path}")
    def artifact(job_id: str, filename: str):
        return FileResponse(store.artifact(job_id, filename), filename=Path(filename).name)

    @app.post("/api/af3/prepare")
    def prepare(req: AF3Request):
        data = alphafold.build_input(**req.model_dump())
        job = store.create("alphafold", data)
        result = alphafold.prepare_job(store.directory(job["id"]), data)
        return store.update(job["id"], "prepared", result)

    @app.post("/api/af3/{job_id}/execute")
    def execute(job_id: str):
        try:
            return af3_queue.submit(job_id)
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/af3/import")
    def import_outputs(req: ImportRequest):
        if not 1 <= len(req.files) <= 100 or sum(len(v) for v in req.files.values()) > 40_000_000:
            raise ValueError("Import 1–100 AF3 output files, at most 40 MB total")
        for name in req.files:
            if Path(name).name != name or not name.endswith((".json", ".cif")):
                raise ValueError("Only plain .json/.cif filenames are allowed")
        job = store.create("af3_import", {"files": list(req.files)})
        output = store.directory(job["id"]) / "output"
        output.mkdir()
        for name, content in req.files.items():
            (output / name).write_text(content)
        result = alphafold.parse_outputs(output)
        store.write(job["id"], "result.json", result)
        return store.update(job["id"], "completed" if result.get("models") else "failed", result)

    @app.get("/api/af3/{job_id}/pocket")
    def pocket(job_id: str, file: str, ligand_chain: str = "B"):
        return pocket_features(store.artifact(job_id, file), ligand_chain)

    @app.get("/api/quantum/backends")
    def backends():
        return quantum.inspect_backends()

    def quantum_request_parts(req: QuantumRequest):
        params = req.model_dump()
        context = {key: params.pop(key) for key in ("source_analysis_id", "sample_ids", "sample_labels")}
        if req.source_analysis_id:
            source = store.get(req.source_analysis_id)
            if source["kind"] != "analysis":
                raise ValueError("source_analysis_id must identify a stored analysis")
            previous = (source.get("result") or {}).get("quantum") or {}
            definition = previous.get("feature_definition") or {}
            if req.features != definition.get("features"):
                raise ValueError(
                    "Reanalysis features must exactly match the original stored row order and values"
                )
            ids = previous.get("sample_ids") or []
            if len(ids) != len(req.features) or (req.sample_ids and req.sample_ids != ids):
                raise ValueError("Reanalysis sample_ids must exactly match the original feature row order")
            context["sample_ids"] = ids
            context["feature_definition"] = definition
        return params, context

    @app.get("/api/quantum/results")
    def quantum_results(source_analysis_id: str | None = None):
        if source_analysis_id is not None:
            store.directory(source_analysis_id)  # Validate before querying or following a path.
        with store.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM jobs WHERE kind='quantum' "
                "AND (? IS NULL OR json_extract(payload, '$.source_analysis_id')=?) "
                "ORDER BY created DESC LIMIT 50",
                (source_analysis_id, source_analysis_id),
            ).fetchall()
        return {"items": [store.get(row["id"]) for row in rows]}

    @app.post("/api/quantum/plan")
    def quantum_plan(req: QuantumRequest):
        params, context = quantum_request_parts(req)
        return quantum.plan_quantum(**params) | context

    @app.post("/api/quantum/run")
    def quantum_run(req: QuantumRequest):
        params, context = quantum_request_parts(req)
        plan = quantum.plan_quantum(**params)
        if plan["status"] != "ready":
            raise ValueError(f"Quantum plan is not executable: {plan['status']}")
        job = store.create("quantum", params | context)
        try:
            if req.mode == "local":
                result = (
                    quantum.local_kernel(
                        req.features,
                        n_qubits=plan["n_qubits"],
                        layers=req.layers,
                        max_circuits=req.max_circuits,
                        kernel_method=req.kernel_method,
                        block_size=req.block_size,
                        gamma=req.gamma,
                    )
                    | context
                )
                store.write(job["id"], "kernel.json", result)
                return store.update(job["id"], "completed", result)
            result = (
                quantum.submit_kernel(
                    manifest_path=store.directory(job["id"]) / "quantum.json", execute=True, **params
                )
                | context
            )
        except Exception:
            store.update(
                job["id"],
                "failed",
                error="Quantum execution failed; inspect persisted artifacts before retrying to avoid duplicate jobs.",
            )
            raise
        return store.update(job["id"], result["status"], result)

    @app.post("/api/quantum/{job_id}/refresh")
    def refresh_quantum(job_id: str):
        job = store.get(job_id)
        if job["kind"] != "quantum" or job["payload"]["mode"] != "ibm":
            raise ValueError("Not an IBM Quantum job")
        result = quantum.retrieve_kernel(store.directory(job_id) / "quantum.json")
        result.update(
            {
                key: job["payload"].get(key)
                for key in ("source_analysis_id", "sample_ids", "sample_labels", "feature_definition")
                if key in job["payload"]
            }
        )
        status = "completed" if result.get("kernel") is not None else result.get("status", "submitted")
        return store.update(job_id, status, result)

    @app.post("/api/benchmark/evaluate")
    def evaluate(req: RecordsRequest):
        result = benchmark.evaluate_records(**req.model_dump())
        job = store.create("benchmark", req.model_dump())
        store.write(job["id"], "evaluation.json", result)
        return store.update(job["id"], "completed", result)

    @app.post("/api/quantum/experiments/prepare")
    def prepare_kernel_experiment(req: RecordsRequest):
        result = kernel_model.prepare_experiment(**req.model_dump())
        job = store.create("kernel_experiment", req.model_dump())
        store.write(job["id"], "experiment.json", result)
        return store.update(job["id"], "prepared", result)

    @app.post("/api/quantum/experiments/local")
    def local_kernel_experiment(req: RecordsRequest):
        result = kernel_model.run_local_experiment(**req.model_dump())
        job = store.create("kernel_experiment", req.model_dump())
        store.write(job["id"], "experiment.json", result["experiment"])
        store.write(job["id"], "evaluation.json", result)
        return store.update(job["id"], "completed", result)

    @app.post("/api/quantum/experiments/{experiment_id}/evaluate")
    def evaluate_kernel_experiment(experiment_id: str, req: KernelEvaluateRequest):
        experiment = json.loads(store.artifact(experiment_id, "experiment.json").read_text())
        kernel_job = store.get(req.quantum_job_id)
        if kernel_job["kind"] != "quantum" or kernel_job["status"] != "completed":
            raise ValueError("Provide a completed quantum kernel job ID")
        result = kernel_model.evaluate_experiment(experiment, kernel_job["result"])
        store.write(experiment_id, "evaluation.json", result)
        return store.update(
            experiment_id, "completed", {"evaluation": result, "quantum_job_id": req.quantum_job_id}
        )

    @app.post("/api/models/train")
    def train(req: RecordsRequest):
        # Evaluate on held-out groups before fitting the final model.
        evaluation = benchmark.evaluate_records(**req.model_dump())
        model = benchmark.train_model(req.records, endpoint=req.endpoint)
        job = store.create("model", {"endpoint": req.endpoint, "count": len(req.records)})
        store.write(job["id"], "model.json", model)
        store.write(job["id"], "training_records.json", req.records)
        return store.update(job["id"], "completed", {"model_id": job["id"], "evaluation": evaluation})

    @app.post("/api/models/predict")
    def predict(req: PredictRequest):
        model = json.loads(store.artifact(req.model_id, "model.json").read_text())
        return benchmark.predict_model(model, req.queries)

    @app.post("/api/data/csv")
    async def parse_csv(request: Request):
        raw = await request.body()
        if len(raw) > 10_000_000:
            raise ValueError("CSV exceeds 10 MB")
        try:
            decoded = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV must use UTF-8 encoding") from exc
        rows = list(csv.DictReader(io.StringIO(decoded)))
        if len(rows) > 5000:
            raise ValueError("CSV exceeds 5000 records")
        for row in rows:
            row["is_measured"] = str(row.get("is_measured", "")).lower() in {"true", "1", "yes"}
        return {"records": rows}

    @app.post("/api/data/audit")
    def audit_records(req: RecordsRequest):
        return curation.report_records(req.records)

    @app.post("/api/data/structures")
    def attach_structure_features(req: RecordsRequest):
        return features.enrich_records(req.records, store)

    @app.post("/api/workflows")
    def workflow(req: WorkflowRequest):
        catalog = {x["id"]: x for x in chemistry.load_catalog()}
        if req.compounds and req.compound_ids:
            raise ValueError("Use compound_ids or compounds, not both")
        selected = req.compounds or [catalog[x] for x in dict.fromkeys(req.compound_ids)]
        if len(selected) < 2 or len({x.get("id") for x in selected}) != len(selected):
            raise ValueError("Provide at least two molecules with distinct IDs")
        for molecule in selected:
            if not all(
                isinstance(molecule.get(k), str) and molecule[k] for k in ("id", "smiles", "category")
            ):
                raise ValueError("Each compound needs string id, smiles, and category")
            if molecule["category"] not in {"herbal", "natural_product", "drug"}:
                raise ValueError(
                    "Assign the imported compound category explicitly: herbal, natural_product or drug"
                )
            chemistry.describe_molecule(molecule["smiles"])
        if not any(c["category"] in {"herbal", "natural_product"} for c in selected) or not any(
            c["category"] == "drug" for c in selected
        ):
            raise ValueError("Select at least one herbal compound and one drug reference")
        generated_by_smiles = {}
        herbs = [x for x in selected if x["category"] in {"herbal", "natural_product"}]
        drugs = [x for x in selected if x["category"] == "drug"]
        for herb in herbs:
            for drug in drugs:
                for candidate in chemistry.generate_candidates(
                    [herb["smiles"], drug["smiles"]], req.max_candidates
                ):
                    candidate[
                        "herbal_parent_id" if herb["category"] == "herbal" else "natural_product_parent_id"
                    ] = herb["id"]
                    candidate["drug_parent_id"] = drug["id"]
                    generated_by_smiles.setdefault(candidate["smiles"], candidate)
        generated = sorted(
            generated_by_smiles.values(), key=lambda x: (-x["descriptors"]["qed"], x["smiles"])
        )[: req.max_candidates]
        result = {
            "compounds": [{**x, "descriptors": chemistry.describe_molecule(x["smiles"])} for x in selected],
            "comparison": chemistry.compare_compounds(selected),
            "candidates": generated,
            "target_id": req.target_id,
            "affinity": None,
            "affinity_status": "Measured target-specific training data required",
            "af3_inputs": [],
        }
        if req.protein_sequence:
            for molecule in selected + generated:
                result["af3_inputs"].append(
                    alphafold.build_input(
                        name=molecule["id"],
                        proteins=[{"id": "A", "sequence": req.protein_sequence}],
                        ligands=[{"id": "B", "smiles": molecule["smiles"]}],
                        seeds=[1, 2, 3],
                    )
                )
        job = store.create("discovery", req.model_dump())
        store.write(job["id"], "discovery.json", result)
        return store.update(job["id"], "completed", result)

    from .af3_studio import make_router as studio_router
    from .af3_workflow_api import make_router as af3_workflow_router
    from .combination_assay import make_router as combination_assay_router
    from .design_pipeline_api import make_router as design_pipeline_router
    from .discovery_api import make_router as discovery_router
    from .molecular_api import make_router as molecular_router
    from .orchestration_api import make_router as orchestration_router
    from .protein_targets_api import make_router as protein_targets_router
    from .validation_api import make_router as validation_router

    bulk_router = discovery_router(store)
    app.state.discovery = bulk_router.service
    app.include_router(bulk_router)
    app.include_router(molecular_router(store))
    app.include_router(protein_targets_router(store))
    prediction_router = studio_router(store, af3_queue)
    app.state.studio_predictions = prediction_router.service
    app.include_router(prediction_router)
    workflow_router = af3_workflow_router(store, prediction_router.service)
    app.state.af3_workflows = workflow_router.service
    app.include_router(workflow_router)
    analysis_router = orchestration_router(store, pool)
    app.state.orchestrator = analysis_router.orchestrator
    app.include_router(analysis_router)
    design_router = design_pipeline_router(store, pool)
    app.state.design_pipeline = design_router.engine
    app.include_router(design_router)
    app.include_router(combination_assay_router())
    app.include_router(validation_router(store))
    if WEB.is_dir():
        app.mount("/app", StaticFiles(directory=WEB), name="app")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
