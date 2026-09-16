"""Local bulk catalog and durable, bounded candidate campaigns."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from . import chemistry
from .discovery_campaign import CampaignStore
from .discovery_catalog import DiscoveryCatalog
from .discovery_sources import SOURCES, chembl_drug_records, download_source, extract_csv, source_by_id
from .herb_aliases import list_herb_aliases, resolve_herb_query


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(max_length=80)
    max_records: int | None = Field(default=None, ge=1, le=100_000_000)


class CampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="천연물 기반 후보 탐색", min_length=1, max_length=160)
    target_count: int = Field(default=1_000_000, ge=1, le=100_000_000)
    seed_limit: int = Field(default=200, ge=1, le=9000)
    reference_limit: int = Field(default=50, ge=1, le=500)
    reference_ids: list[str] | None = Field(default=None, max_length=500)
    seed_source: str | None = Field(default=None, max_length=80)
    seed_after_id: int = Field(default=0, ge=0)
    seed_offset: int = Field(default=0, ge=0, le=100_000_000)
    seed_search: str = Field(default="", max_length=500)
    max_attempts: int = Field(default=100_000, ge=1, le=1_000_000_000)
    max_runtime_seconds: int = Field(default=3600, ge=1, le=604800)
    max_storage_mb: int = Field(default=1024, ge=1, le=1_000_000)
    max_products_per_pair: int = Field(default=100, ge=1, le=500)


class DiscoveryService:
    def __init__(self, store):
        self.store = store
        self.root = store.root / "discovery"
        self.catalog = DiscoveryCatalog(self.root)
        self.campaigns = CampaignStore(self.root)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="herbfold-bulk")
        self.stopping = threading.Event()
        self.lock = threading.RLock()
        self.scheduled: set[str] = set()

    def recover(self):
        for job in self._ingest_jobs():
            if job["status"] in {"queued", "running", "downloading", "importing"}:
                worker_pid = (job["result"] or {}).get("worker_pid")
                if worker_pid and worker_pid != os.getpid():
                    try:
                        os.kill(worker_pid, 0)
                        continue
                    except ProcessLookupError:
                        pass
                self.store.update(
                    job["id"],
                    "interrupted",
                    job["result"],
                    "Server restarted. Retry the source; committed records are deduplicated.",
                )
        for campaign in self.campaigns.active():
            if campaign["status"] in {"running", "queued"}:
                self.campaigns.pause(campaign["id"])

    def close(self):
        self.stopping.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _ingest_jobs(self):
        with self.store.connect() as connection:
            ids = connection.execute(
                "SELECT id FROM jobs WHERE kind='bulk_import' ORDER BY created DESC LIMIT 50"
            ).fetchall()
        return [self.store.get(row[0]) for row in ids]

    @staticmethod
    def _public_import(job):
        result = job["result"] or {}
        return {
            **result,
            "id": job["id"],
            "source_id": job["payload"]["source_id"],
            "status": job["status"],
            "created": job["created"],
            "updated": job["updated"],
            "error": job["error"],
            "max_records": job["payload"].get("max_records"),
            "processed": result.get("processed_records", 0),
            "inserted": result.get("inserted_compounds", 0),
            "source_records": result.get("inserted_records", 0),
            "invalid": result.get("invalid_records", 0),
        }

    def ingestions(self):
        return [self._public_import(job) for job in self._ingest_jobs()]

    def summary(self):
        return {
            "catalog": self.catalog.summary(),
            "sources": SOURCES,
            "campaigns": self.campaigns.list(),
            "ingestions": self.ingestions(),
            "scale": {
                "max_target_count": 100_000_000,
                "execution": "bounded_batches",
                "benchmark_scope": "See docs/discovery-verification.json; target is not a measured capacity",
            },
        }

    def start_ingestion(self, request: IngestRequest, *, background=True):
        source = source_by_id(request.source_id)
        if not source["automated"]:
            raise ValueError("This reference is not an automated bulk source")
        with self.lock:
            # One import at a time bounds writes and network use; a repeated click
            # returns the existing work instead of downloading the same release.
            active = [
                job
                for job in self._ingest_jobs()
                if job["status"] in {"queued", "running", "downloading", "importing"}
            ]
            if active:
                if active[0]["payload"]["source_id"] == request.source_id:
                    return self._public_import(active[0])
                raise ValueError("A source import is already active; start this source after it finishes")
            job = self.store.create("bulk_import", request.model_dump())
            self.store.update(job["id"], "queued", {})
            if background:
                self.pool.submit(self.run_ingestion, job["id"])
        if not background:
            self.run_ingestion(job["id"])
        return self._public_import(self.store.get(job["id"]))

    def run_ingestion(self, job_id):
        job = self.store.get(job_id)
        source = source_by_id(job["payload"]["source_id"])
        result, status, last_saved = {"worker_pid": os.getpid()}, "downloading", 0.0

        def progress(fields):
            nonlocal last_saved
            if self.stopping.is_set():
                raise InterruptedError("Server stopped; committed catalog batches remain available")
            result.update(fields)
            if time.monotonic() - last_saved >= 1:
                self.store.update(job_id, status, result)
                last_saved = time.monotonic()

        try:
            directory = self.root / "downloads"
            self.store.update(job_id, status, result)
            args = {
                "source_id": source["id"],
                "source_url": source["url"],
                "license_label": source["license"],
                "max_records": job["payload"].get("max_records"),
                "on_progress": progress,
            }
            if source["id"] == "chembl-approved":
                status = "importing"
                rows = chembl_drug_records(
                    directory / "chembl-approved", args["max_records"], progress, self.stopping.is_set
                )
                report = self.catalog.ingest_records(rows, **args)
            else:
                path, receipt = download_source(source, directory, progress, self.stopping.is_set)
                self.store.write(job_id, "source_manifest.json", receipt)
                result["receipt"] = receipt
                path = extract_csv(path)
                status = "importing"
                self.store.update(job_id, status, result)
                report = self.catalog.ingest_file(path, format=source.get("format"), **args)
            result.update(report)
            self.store.write(job_id, "import_report.json", result)
            self.store.update(job_id, "completed", result)
        except InterruptedError as error:
            self.store.update(job_id, "interrupted", result, str(error))
        except Exception as error:
            self.store.update(job_id, "failed", result, f"{type(error).__name__}: {error}")

    def _seeds(self, request: CampaignRequest):
        reference_rows = [row for row in chemistry.load_catalog() if row["category"] == "drug"]
        if request.reference_ids is not None:
            requested = set(request.reference_ids)
            known = {row["id"] for row in reference_rows}
            if requested - known:
                raise ValueError("reference_ids must refer to drug references in the studio catalog")
            reference_rows = [row for row in reference_rows if row["id"] in requested]
        else:
            imported = self.catalog.list_compounds(source="chembl-approved", limit=request.reference_limit)
            reference_rows.extend(
                {
                    **row,
                    "id": f"catalog:{row['id']}",
                    "category": "drug",
                    "reference_status": "approved_history_not_current_market_status",
                    "selected_source_id": "chembl-approved",
                }
                for row in imported["items"]
            )
        if not reference_rows:
            raise ValueError("Select at least one drug reference")
        yield from reference_rows[: request.reference_limit]
        sources = [request.seed_source] if request.seed_source else ["lotus-2026-04", "coconut-2026-09"]
        known_sources = {source["id"]: source for source in SOURCES}
        registered = {source["id"]: source for source in self.catalog.summary()["sources"]}
        source_infos = {}
        for source in sources:
            if source in known_sources:
                source_info = known_sources[source]
                if source_info.get("kind") != "natural_product":
                    raise ValueError("Choose a natural-product source for the source compounds")
            elif source in registered:
                local = registered[source]
                source_info = {
                    "id": source,
                    "url": local["source_url"],
                    "license": local["license_label"],
                    "origin_status": "user_selected_source_parent_not_verified_as_natural_product",
                }
            else:
                raise ValueError("Unknown source; import the local dataset with explicit provenance first")
            source_infos[source] = source_info
        seen = set()
        remaining_skip = request.seed_offset
        for source in sources:
            source_info = source_infos[source]
            iterator = self.catalog.iter_compounds(
                search=resolve_herb_query(request.seed_search)["query"],
                source=source,
                after_id=request.seed_after_id,
                batch_size=500,
            )
            for row in iterator:
                if remaining_skip:
                    remaining_skip -= 1
                    continue
                if row["id"] in seen:
                    continue
                # Full corpus retains large structures; generation is a separate,
                # bounded small-molecule workflow with existing chemistry checks.
                try:
                    chemistry.standardize_molecule(row["canonical_smiles"])
                except (ValueError, RuntimeError):
                    continue
                seen.add(row["id"])
                yield {
                    **row,
                    "id": f"catalog:{row['id']}",
                    "category": "natural_product",
                    "selected_source_id": source,
                    "selected_source_url": source_info["url"],
                    "selected_source_license": source_info["license"],
                    "seed_query_resolution": resolve_herb_query(request.seed_search),
                    "source_scope": source_info.get(
                        "origin_status", "source-reported natural product; herbal use not inferred"
                    ),
                }
                if len(seen) >= request.seed_limit:
                    return
        if not seen:
            raise ValueError("Import a natural-product source first, or broaden the species/name search")

    def start_campaign(self, request: CampaignRequest):
        job = self.campaigns.create(
            {
                "name": request.name,
                "target_candidates": request.target_count,
                "max_attempts": request.max_attempts,
                "max_products_per_pair": request.max_products_per_pair,
                "max_runtime_seconds": request.max_runtime_seconds,
                "max_storage_mb": request.max_storage_mb,
            },
            self._seeds(request),
        )
        self.schedule_campaign(job["id"])
        return job

    def schedule_campaign(self, campaign_id):
        with self.lock:
            if campaign_id not in self.scheduled and not self.stopping.is_set():
                self.scheduled.add(campaign_id)
                self.pool.submit(self._run_campaign, campaign_id)

    def _run_campaign(self, campaign_id):
        try:
            while not self.stopping.is_set():
                state = self.campaigns.get(campaign_id)
                if state["status"] not in {"queued", "running"}:
                    break
                state = self.campaigns.run_batch(campaign_id, max_attempts=50, max_seconds=5)
                if state["status"] not in {"queued", "running"}:
                    break
        except Exception as error:
            self.campaigns.fail(campaign_id, f"{type(error).__name__}: {error}")
        finally:
            with self.lock:
                self.scheduled.discard(campaign_id)
                # Resume may race the final iteration of a paused worker.
                if not self.stopping.is_set() and self.campaigns.get(campaign_id)["status"] in {
                    "queued",
                    "running",
                }:
                    self.schedule_campaign(campaign_id)


def make_router(store):
    service = DiscoveryService(store)
    router = APIRouter(prefix="/api/discovery", tags=["bulk-discovery"])
    router.service = service

    @router.get("/summary")
    def summary():
        return service.summary()

    @router.get("/herbs")
    def herbs():
        items = [
            {**row, "name_ko": row["name"], "source_url": row["mapping_source_url"]}
            for row in list_herb_aliases()
        ]
        return {"items": items, "total": len(items)}

    @router.get("/compounds")
    def compounds(
        search: str = Query("", max_length=500),
        source: str | None = None,
        limit: int = Query(30, ge=1, le=500),
        offset: int = Query(0, ge=0),
        kind: Literal["natural_product", "drug"] | None = None,
    ):
        resolution = resolve_herb_query(search)
        page = service.catalog.list_compounds(
            search=resolution["query"], source=source, limit=limit, offset=offset, kind=kind
        )
        return {**page, "query_resolution": resolution}

    @router.get("/compounds/{compound_id}")
    def compound(compound_id: int):
        return service.catalog.get_compound(compound_id)

    @router.get("/ingestions")
    def ingestions():
        return service.ingestions()

    @router.post("/ingestions", status_code=202)
    def ingest(request: IngestRequest):
        return service.start_ingestion(request)

    @router.get("/campaigns")
    def campaigns():
        return service.campaigns.list()

    @router.post("/campaigns", status_code=202)
    def campaign(request: CampaignRequest):
        return service.start_campaign(request)

    @router.get("/campaigns/{campaign_id}")
    def get_campaign(campaign_id: str):
        return service.campaigns.get(campaign_id)

    @router.post("/campaigns/{campaign_id}/pause")
    def pause(campaign_id: str):
        return service.campaigns.pause(campaign_id)

    @router.post("/campaigns/{campaign_id}/resume", status_code=202)
    def resume(campaign_id: str):
        result = service.campaigns.resume(campaign_id)
        service.schedule_campaign(campaign_id)
        return result

    @router.post("/campaigns/{campaign_id}/cancel")
    def cancel(campaign_id: str):
        return service.campaigns.cancel(campaign_id)

    @router.get("/campaigns/{campaign_id}/candidates")
    def candidates(campaign_id: str, limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
        return service.campaigns.candidates(campaign_id, limit=limit, offset=offset)

    return router
