"""Verify actual local discovery artifacts. Read-only; never submits paid compute."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
from rdkit import Chem

from herbfold.chemistry import describe_molecule
from herbfold.discovery_campaign import CampaignStore
from herbfold.discovery_catalog import DiscoveryCatalog
from herbfold.discovery_sources import file_digest
from herbfold.herb_aliases import list_herb_aliases


def verify(root: Path, url: str):
    catalog = DiscoveryCatalog(root / "discovery")
    campaigns = CampaignStore(root / "discovery")
    summary = catalog.summary()
    with sqlite3.connect(catalog.db) as connection:
        connection.execute("BEGIN")
        stored_counts = connection.execute(
            "SELECT compound_count,provenance_count FROM discovery_stats"
        ).fetchone()
        actual_counts = (
            connection.execute("SELECT COUNT(*) FROM discovery_compounds").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM discovery_provenance").fetchone()[0],
        )
        assert stored_counts == actual_counts
        assert not connection.execute("PRAGMA foreign_key_check").fetchmany(1)
    receipts = []
    for path in (root / "discovery" / "downloads").glob("*.manifest.json"):
        receipt = json.loads(path.read_text())
        original = (
            Path(receipt["file"])
            if "file" in receipt
            else path.with_name(path.name.removesuffix(".manifest.json") + ".html")
        )
        if not original.is_absolute():
            original = Path.cwd() / original
        assert original.is_file()
        assert file_digest(original) == receipt["sha256"]
        receipts.append(
            {
                "file": original.name,
                "bytes": original.stat().st_size,
                "sha256": receipt["sha256"],
                "url": receipt["url"],
            }
        )
    campaign_results = []
    global_candidate_smiles = set()
    for run in campaigns.list():
        seen, sample_verified = set(), 0
        for candidate in campaigns.iter_candidates(run["id"], batch_size=100):
            assert candidate["smiles"] not in seen
            seen.add(candidate["smiles"])
            global_candidate_smiles.add(candidate["smiles"])
            assert Chem.MolFromSmiles(candidate["smiles"]) is not None
            assert candidate["affinity"] is None and candidate["novelty"] == "not_assessed"
            assert candidate["source_lineage"]
            assert "natural_product_parent_id" in candidate or "herbal_parent_id" in candidate
            if sample_verified < 25:
                desc = describe_molecule(candidate["smiles"])
                for key in ("molecular_weight", "qed", "logp", "tpsa"):
                    assert abs(desc[key] - candidate["descriptors"][key]) < 0.00001
                sample_verified += 1
        assert len(seen) == run["retained"]
        assert run["attempted"] == run["retained"] + run["rejected"] + run["duplicates"]
        campaign_results.append(
            {
                **run,
                "all_retained_structures_sanitized": len(seen),
                "descriptor_recomputations": sample_verified,
            }
        )
    searches = {}
    with httpx.Client(base_url=url, timeout=90) as client:
        response = client.get("/api/discovery/summary")
        response.raise_for_status()
        assert response.json()["catalog"]["compound_count"] == summary["compound_count"]
        for term in (
            "Scutellaria baicalensis",
            "Coptis chinensis",
            "Panax ginseng",
            "Glycyrrhiza",
            "Zingiber officinale",
        ):
            response = client.get(
                "/api/discovery/compounds", params={"search": term, "source": "lotus-2026-04", "limit": 2}
            )
            response.raise_for_status()
            searches[term] = response.json()["total"]
        assert client.get("/api/discovery/compounds", params={"limit": 100000000}).status_code == 422
        healthy = client.get("/api/health")
        healthy.raise_for_status()
    return {
        "verified_at": datetime.now(UTC).isoformat(),
        "url": url,
        "catalog": summary,
        "sqlite_counts_match": True,
        "foreign_keys_valid": True,
        "download_receipts": receipts,
        "taxon_search_counts": searches,
        "campaigns": campaign_results,
        "candidate_unique_across_verified_campaigns": len(global_candidate_smiles),
        "korean_search_aliases": len(list_herb_aliases()),
        "external_compute_submissions": {"llm": 0, "alphafold3": 0, "ibm_qpu": 0},
        "scope": "Actual imported source data and bounded local campaigns. Hundred-million capacity not load-tested; no drug efficacy or external novelty claim.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("runtime"))
    parser.add_argument("--url", default="http://127.0.0.1:8873")
    parser.add_argument("--output", type=Path, default=Path("docs/discovery-verification.json"))
    args = parser.parse_args()
    result = verify(args.data_dir, args.url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    print(
        json.dumps(
            {
                "output": str(args.output),
                "structures": result["catalog"]["compound_count"],
                "provenance": result["catalog"]["provenance_count"],
                "campaigns": [{"id": x["id"], "retained": x["retained"]} for x in result["campaigns"]],
            }
        )
    )


if __name__ == "__main__":
    main()
