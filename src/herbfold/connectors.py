"""Bounded, public-source imports. No user-controlled request hosts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import quote

import httpx


def fetch_json(url, params=None):
    try:
        with httpx.Client(
            timeout=30, follow_redirects=True, headers={"User-Agent": "HerbFold/0.1 research-workstation"}
        ) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError(
            f"Public database request failed ({type(exc).__name__}); retry later or import your own data."
        ) from exc


def pubchem_lookup(query):
    if not query or len(query) > 160:
        raise ValueError("Compound name or CID must contain 1–160 characters")
    namespace = "cid" if query.isdigit() else "name"
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/{namespace}/{quote(query, safe='')}/property/SMILES,ConnectivitySMILES,InChIKey,MolecularFormula/JSON"
    data = fetch_json(url)["PropertyTable"]["Properties"][0]
    return {
        "name": query,
        "cid": data["CID"],
        "smiles": data.get("SMILES") or data["ConnectivitySMILES"],
        "inchikey": data["InChIKey"],
        "formula": data["MolecularFormula"],
        "source": f"https://pubchem.ncbi.nlm.nih.gov/compound/{data['CID']}",
        "retrieved_at": datetime.now(UTC).isoformat(),
    }


def uniprot_lookup(accession):
    accession = accession.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6,10}(?:-[0-9]+)?", accession):
        raise ValueError("Enter a UniProt accession, for example P35354")
    data = fetch_json(f"https://rest.uniprot.org/uniprotkb/{accession}.json")
    return {
        "accession": data["primaryAccession"],
        "name": data.get("proteinDescription", {})
        .get("recommendedName", {})
        .get("fullName", {})
        .get("value", accession),
        "sequence": data["sequence"]["value"],
        "organism": data.get("organism", {}).get("scientificName"),
        "source": f"https://www.uniprot.org/uniprotkb/{accession}/entry",
        "retrieved_at": datetime.now(UTC).isoformat(),
    }


def chembl_activities(target_id, endpoint="Kd", limit=100):
    if not re.fullmatch(r"CHEMBL[0-9]+", target_id):
        raise ValueError("Expected a ChEMBL target identifier")
    if endpoint not in ("Kd", "Ki") or not 1 <= limit <= 500:
        raise ValueError("Use Kd or Ki and a limit of 1–500")
    params = {
        "target_chembl_id": target_id,
        "standard_type": endpoint,
        "standard_relation": "=",
        "standard_units": "nM",
        "limit": limit,
    }
    raw = fetch_json("https://www.ebi.ac.uk/chembl/api/data/activity.json", params)
    records = []
    for row in raw["activities"]:
        if (
            not row.get("canonical_smiles")
            or row.get("standard_relation") != "="
            or row.get("data_validity_comment")
            or row.get("potential_duplicate")
        ):
            continue
        try:
            value = float(row["standard_value"])
        except (ValueError, TypeError):
            continue
        if not 0 < value < float("inf"):
            continue
        records.append(
            {
                "smiles": row["canonical_smiles"],
                "target_id": target_id,
                "endpoint": endpoint,
                "value": value,
                "unit": "nM",
                "relation": "=",
                "is_measured": True,
                "assay_id": row["assay_chembl_id"],
                "source": f"https://www.ebi.ac.uk/chembl/explore/activity/{row['activity_id']}",
                "molecule_id": row["molecule_chembl_id"],
                "document_id": row.get("document_chembl_id"),
            }
        )
    return {
        "records": records,
        "examined": len(raw["activities"]),
        "total_available": raw.get("page_meta", {}).get("total_count"),
        "retrieved_at": datetime.now(UTC).isoformat(),
        "note": "Exact endpoint/units only; verify assay comparability and target mapping before pooling. Bounded first page, not a complete dataset.",
    }
