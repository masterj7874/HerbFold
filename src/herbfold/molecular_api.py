"""Bounded molecular scene endpoints sharing the application artifact store."""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import PurePosixPath
from typing import Literal

import httpx
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from . import molecular

_REFERENCE_LOCK = threading.Lock()
_AF3_JOB_KINDS = {"alphafold", "alphafold_smoke", "af3_import"}
_REFERENCES = {
    "5IKR": {
        "label": "Human COX-2 / PTGS2 with mefenamic acid — experimental reference 5IKR",
        "url": "https://files.rcsb.org/download/5IKR.cif",
        "source_url": "https://www.rcsb.org/structure/5IKR",
        "target": "PTGS2",
        "ligand": "mefenamic acid",
    },
}


class ConformerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smiles: str = Field(min_length=1, max_length=5000)
    seed: int = Field(default=42, ge=0, le=2**31 - 1, strict=True)
    include_hydrogens: bool = Field(default=True, strict=True)
    num_conformers: int = Field(default=4, ge=1, le=4, strict=True)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smiles: str = Field(min_length=1, max_length=5000)
    source: Literal["experimental_pdb", "alphafold3_prediction"]
    target_accession: str = Field(default="P35354", pattern=r"^[A-Z0-9][A-Z0-9-]{1,19}$")


def _download(url, destination, *, max_bytes):
    """Only called with fixed RCSB URLs; never accepts a user-supplied host/path."""
    try:
        with httpx.stream("GET", url, timeout=30, follow_redirects=False) as response:
            response.raise_for_status()
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("RCSB response exceeds the allowed size")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="RCSB reference is currently unavailable; retry later"
        ) from exc
    destination.write_bytes(b"".join(chunks))


def _reference_scene(store, pdb_id, *, reference=None):
    pdb_id = pdb_id.upper()
    reference = reference or _REFERENCES.get(pdb_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="Reference is not in the supported catalog")
    cache = store.root / "reference_structures"
    cache.mkdir(exist_ok=True)
    path = cache / f"{pdb_id}.cif"
    with _REFERENCE_LOCK:
        if not path.is_file():
            pending = path.with_suffix(".pending")
            try:
                _download(
                    f"https://files.rcsb.org/download/{pdb_id}.cif",
                    pending,
                    max_bytes=molecular.MAX_STRUCTURE_BYTES,
                )
                cif = molecular.read_cif(pending)
                if cif.get("_entry.id", [""])[0].upper() != pdb_id:
                    raise ValueError("RCSB returned an unexpected structure identifier")
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)
        cif = molecular.read_cif(path)
        if cif.get("_entry.id", [""])[0].upper() != pdb_id:
            raise ValueError("Cached reference has an unexpected structure identifier")
        methods = cif.get("_exptl.method", [])
        if not methods or "X-RAY DIFFRACTION" not in methods:
            raise ValueError("Reference does not contain the expected experimental X-ray method")
        comp_ids = (
            set(cif.get("_atom_site.label_comp_id", []))
            - set(molecular.protein_templates())
            - {"HOH", "DOD", "WAT"}
        )
        if len(comp_ids) > 30:
            raise ValueError("Reference contains too many distinct nonstandard components")
        templates, omitted = {}, []
        for comp in sorted(comp_ids):
            if not re.fullmatch(r"[A-Z0-9]{1,3}", comp):
                omitted.append(comp)
                continue
            ccd_path = cache / f"CCD_{comp}.cif"
            try:
                if not ccd_path.is_file():
                    pending = ccd_path.with_suffix(".pending")
                    try:
                        _download(
                            f"https://files.rcsb.org/ligands/download/{comp}.cif",
                            pending,
                            max_bytes=2_000_000,
                        )
                        data = MMCIF2Dict(str(pending))
                        parsed = molecular.ccd_templates(data)
                        if data.get("_chem_comp.id", [""])[0] != comp:
                            raise ValueError("RCSB returned an unexpected component identifier")
                        pending.replace(ccd_path)
                    finally:
                        pending.unlink(missing_ok=True)
                else:
                    parsed = molecular.ccd_templates(MMCIF2Dict(str(ccd_path)))
                templates.update(parsed)
            except (HTTPException, ValueError, IndexError):
                omitted.append(comp)
    scene = molecular.structure_scene(
        path, source="experimental_pdb", label=reference["label"], templates=templates
    )
    scene["metadata"].update(
        pdb_id=pdb_id,
        source_url=reference["source_url"],
        target=reference["target"],
        ligand_name=reference["ligand"],
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    if omitted:
        scene["warnings"].append("RCSB component definitions unavailable for: " + ", ".join(omitted))
    return scene


def _job_scene(store, job_id, filename=None):
    job = store.get(job_id)
    if job["kind"] not in _AF3_JOB_KINDS:
        raise ValueError("Molecular job scene requires an AlphaFold execution or import job")
    directory = store.directory(job_id)
    result = job.get("result") or {}
    models = result.get("models") or []
    requested_path = store.artifact(job_id, filename) if filename is not None else None
    if not models:
        raise HTTPException(status_code=404, detail="This job has no validated AlphaFold 3 model artifacts")
    choices = []
    for model in models:
        relative = model.get("structure_path", "")
        if not isinstance(relative, str):
            raise ValueError("Invalid registered model artifact path")
        relative_path = PurePosixPath(relative)
        if not relative or relative_path.is_absolute() or ".." in relative_path.parts or "\\" in relative:
            raise ValueError("Registered model artifact must be relative to its output directory")
        artifact = (PurePosixPath("output") / relative_path).as_posix()
        candidate = store.artifact(job_id, artifact)
        # Store.artifact checks job confinement; also exclude a symlink from
        # output into a different artifact inside the same job directory.
        if not candidate.is_relative_to((directory / "output").resolve()):
            raise ValueError("Registered model artifact lies outside its output directory")
        if requested_path is None or candidate == requested_path:
            choices.append((candidate, model))
    if not choices:
        raise ValueError("Requested structure is not a validated model artifact for this job")
    path, model = min(choices, key=lambda item: not bool(item[1].get("is_top_ranked_copy")))
    filename = path.relative_to(directory).as_posix()
    if path.suffix.lower() not in {".cif", ".mmcif"}:
        raise ValueError("Only mmCIF structure artifacts are supported")
    if path.stat().st_size > molecular.MAX_STRUCTURE_BYTES:
        raise ValueError("Structure exceeds the 50 MB limit")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if model.get("structure_sha256") != digest:
        raise ValueError("Model artifact checksum differs from its validated import record")
    ligands = {}
    for entity in job["payload"].get("sequences", []):
        ligand = entity.get("ligand")
        if ligand and ligand.get("smiles"):
            ids = ligand["id"] if isinstance(ligand["id"], list) else [ligand["id"]]
            ligands.update(dict.fromkeys(ids, ligand["smiles"]))
    scene = molecular.structure_scene(path, source="alphafold3_prediction", ligand_smiles=ligands)
    scene["metadata"].update(
        job_id=job_id,
        artifact=filename,
        sha256=digest,
        job_kind=job["kind"],
        execution_verified=result.get("execution_verified") is True,
        provenance="recorded_local_execution"
        if result.get("execution_verified") is True
        else "user_supplied_af3_format_artifacts",
        summary_metrics=model.get("metrics", {}),
    )
    if result.get("execution_verified") is not True:
        scene["label"] = "Imported AF3-format structure — execution unverified"
        scene["warnings"].append(
            "Imported AF3-format artifacts; program execution and claimed coordinate provenance have not been independently verified. This is not an experimental structure."
        )
    if result.get("prediction_eligible") is False:
        scene["source"] = "structure_file"
        scene["label"] = "AF3 성능 시험 출력 · 예측 사용 제외"
        scene["metadata"].update(
            prediction_eligible=False,
            parameter_audit=result.get("parameter_audit"),
            exclusion_reason=result.get("exclusion_reason")
            or "성능 시험용 또는 검증되지 않은 파라미터로 생성되어 예측 사용에서 제외했습니다.",
            summary_metrics={},
            confidence_kind=None,
        )
        for atom in scene["atoms"]:
            atom["confidence"] = None
        scene["warnings"].append(scene["metadata"]["exclusion_reason"])
    return scene


def make_router(store):
    router = APIRouter(prefix="/api/molecular", tags=["molecular"])

    @router.post("/resolve")
    def selected_complex(req: ResolveRequest):
        from .molecular_selection import resolve

        return resolve(store, **req.model_dump())

    @router.get("/references")
    def available(target_accession: str = Query(default="P35354", pattern=r"^[A-Z0-9][A-Z0-9-]{1,19}$")):
        from .molecular_selection import available_references

        return {
            "target_accession": target_accession,
            "items": available_references(target_accession),
            "scope": "Registered experimental reference ligands; choose explicitly to change the selected molecule",
        }

    @router.post("/conformer")
    def generate(req: ConformerRequest):
        return molecular.conformer(**req.model_dump())

    @router.post("/sdf")
    def sdf(req: ConformerRequest):
        return Response(
            molecular.conformer_sdf(**req.model_dump()),
            media_type="chemical/x-mdl-sdfile",
            headers={"Content-Disposition": 'attachment; filename="herbfold-conformer.sdf"'},
        )

    @router.get("/jobs/{job_id}/scene")
    def job_scene(job_id: str, file: str | None = Query(default=None, max_length=1000)):
        return _job_scene(store, job_id, file)

    @router.get("/reference/{pdb_id}")
    def reference(pdb_id: str):
        return _reference_scene(store, pdb_id)

    @router.get("/samples/ptgs2")
    def sample():
        for job in store.list():
            names = [job["payload"].get("name", "")] + [
                item.get("name", "") for item in (job.get("result") or {}).get("models", [])
            ]
            if job["kind"] in _AF3_JOB_KINDS and any("ptgs2" in name.lower() for name in names):
                try:
                    return _job_scene(store, job["id"])
                except (ValueError, HTTPException):
                    continue
        raise HTTPException(status_code=404, detail="No saved PTGS2 AlphaFold 3 result is available")

    return router
