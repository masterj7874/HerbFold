"""Command-line bulk workflows, suitable for a persistent terminal or batch job."""

from __future__ import annotations

import json
from pathlib import Path


def add_parser(commands):
    parser = commands.add_parser("discovery", help="Bulk natural-product catalog and candidate campaigns")
    parser.add_argument("--data-dir", default=None)
    sub = parser.add_subparsers(dest="discovery_command", required=True)
    sub.add_parser("status")
    fetch = sub.add_parser("fetch", help="Download and ingest a pinned official release")
    fetch.add_argument("source", choices=["lotus-2026-04", "coconut-2026-09", "chembl-approved"])
    fetch.add_argument("--max-records", type=int, default=None)
    imported = sub.add_parser("import", help="Stream a local CSV/TSV/SMI/SDF(.gz) with explicit provenance")
    imported.add_argument("file", type=Path)
    imported.add_argument("--source-id", required=True)
    imported.add_argument("--source-url", required=True)
    imported.add_argument("--license", required=True)
    imported.add_argument("--max-records", type=int, default=None)
    campaign = sub.add_parser("campaign", help="Create and execute a bounded candidate campaign")
    campaign.add_argument("--name", default="천연물 기반 후보 탐색")
    campaign.add_argument("--target-count", type=int, default=1_000_000)
    campaign.add_argument("--seed-limit", type=int, default=200)
    campaign.add_argument("--seed-source", default=None)
    campaign.add_argument("--seed-search", default="")
    campaign.add_argument("--seed-offset", type=int, default=0)
    campaign.add_argument("--seed-after-id", type=int, default=0)
    campaign.add_argument("--reference-limit", type=int, default=50)
    campaign.add_argument("--max-attempts", type=int, default=100_000)
    campaign.add_argument("--max-runtime-seconds", type=int, default=3600)
    campaign.add_argument("--max-storage-mb", type=int, default=1024)
    resume = sub.add_parser("resume", help="Resume a paused campaign from its committed cursor")
    resume.add_argument("campaign_id")
    export = sub.add_parser("export", help="Export a bounded top-QED shortlist, not an efficacy ranking")
    export.add_argument("campaign_id")
    export.add_argument("--limit", type=int, default=1000)
    export.add_argument("--output", type=Path, required=True)


def run(args):
    from .discovery_api import CampaignRequest, DiscoveryService, IngestRequest
    from .discovery_sources import extract_csv
    from .storage import Store

    def show(data):
        print(json.dumps(data, ensure_ascii=False, allow_nan=False), flush=True)

    service = DiscoveryService(Store(args.data_dir))
    try:
        if args.discovery_command == "status":
            show(service.summary())
        elif args.discovery_command == "fetch":
            job = service.start_ingestion(
                IngestRequest(source_id=args.source, max_records=args.max_records), background=False
            )
            show(job)
            if job["status"] != "completed":
                raise SystemExit(1)
        elif args.discovery_command == "import":
            show(
                service.catalog.ingest_file(
                    extract_csv(args.file),
                    source_id=args.source_id,
                    source_url=args.source_url,
                    license_label=args.license,
                    max_records=args.max_records,
                    on_progress=show,
                )
            )
        elif args.discovery_command == "export":
            if not 1 <= args.limit <= 100_000:
                raise ValueError("Export limit must be 1–100,000; export bounded shortlists")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("w") as output:
                offset = 0
                while offset < args.limit:
                    page = service.campaigns.candidates(
                        args.campaign_id, limit=min(200, args.limit - offset), offset=offset
                    )
                    for candidate in page["items"]:
                        output.write(json.dumps(candidate, ensure_ascii=False) + "\n")
                    offset += len(page["items"])
                    if not page["items"] or offset >= page["total"]:
                        break
            show(
                {
                    "output": str(args.output),
                    "exported": offset,
                    "rank": "QED descriptor only; affinity and efficacy not inferred",
                }
            )
        else:
            if args.discovery_command == "resume":
                state = service.campaigns.resume(args.campaign_id)
            else:
                request = CampaignRequest(
                    **{
                        key: getattr(args, key)
                        for key in (
                            "name",
                            "target_count",
                            "seed_limit",
                            "seed_source",
                            "seed_search",
                            "seed_offset",
                            "seed_after_id",
                            "reference_limit",
                            "max_attempts",
                            "max_runtime_seconds",
                            "max_storage_mb",
                        )
                    }
                )
                state = service.campaigns.create(
                    {
                        "name": request.name,
                        "target_candidates": request.target_count,
                        "max_attempts": request.max_attempts,
                        "max_runtime_seconds": request.max_runtime_seconds,
                        "max_storage_mb": request.max_storage_mb,
                    },
                    service._seeds(request),
                )
            show(state)
            try:
                while state["status"] in {"queued", "running"}:
                    state = service.campaigns.run_batch(state["id"], max_attempts=100, max_seconds=10)
                    show(state)
            except KeyboardInterrupt:
                show(service.campaigns.pause(state["id"]))
    finally:
        service.close()
