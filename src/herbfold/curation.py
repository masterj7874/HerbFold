"""Read-only measured-data curation proposals; never chooses between assays."""

from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy

from .benchmark import prepare_records


def report_records(records: list[dict]) -> dict:
    """Report invalid rows and duplicate ligand/target/endpoint groups.

    Input indices are zero-based. ``suggested_records`` is a proposal requiring
    researcher review: invalid rows and ALL members of nonidentical duplicate
    groups are excluded. Only identical full normalized rows are deduplicated.
    No affinity is averaged, no assay is preferred, and input is never mutated.
    """
    if not isinstance(records, list) or len(records) > 5000:
        raise ValueError("Provide a list of at most 5000 records")
    groups = defaultdict(list)
    invalid, changes = [], []
    for index, original in enumerate(records):
        try:
            if not isinstance(original, dict):
                raise TypeError("Expected a record object")
            if original.get("endpoint") not in ("Kd", "Ki"):
                raise ValueError(
                    "Only exact measured Kd or Ki rows are supported; other endpoints are not converted"
                )
            prepared, _ = prepare_records([original], endpoint=original["endpoint"])
            normalized = deepcopy(original)
            for field in ("smiles", "target_id", "source"):
                normalized[field] = prepared[0][field]
            normalized["is_measured"] = True
            if normalized.get("protein_sequence"):
                normalized["protein_sequence"] = prepared[0]["protein_sequence"]
            # Preserve original concentration/unit and all other provenance fields.
            # Full-row identity prevents metadata or assay differences being hidden.
            signature = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
            for field in ("smiles", "target_id", "source", "is_measured", "protein_sequence"):
                if original.get(field) != normalized.get(field):
                    changes.append(
                        {
                            "input_index": index,
                            "field": field,
                            "before": original.get(field),
                            "after": normalized.get(field),
                        }
                    )
            identity = (normalized["smiles"], normalized["target_id"], normalized["endpoint"])
            groups[identity].append(
                {
                    "index": index,
                    "record": normalized,
                    "signature": signature,
                    "pactivity": prepared[0]["pactivity"],
                }
            )
        except (ValueError, TypeError) as exc:
            invalid.append({"input_index": index, "reason": str(exc)})

    proposed = []
    duplicate_groups = []
    exclusions = [{**item, "category": "invalid_record"} for item in invalid]
    for (smiles, target, endpoint), members in groups.items():
        if len(members) == 1:
            proposed.append(members[0])
            continue
        identical = len({member["signature"] for member in members}) == 1
        all_fields = sorted(set().union(*(member["record"] for member in members)))
        differing_fields = [
            field
            for field in all_fields
            if len(
                {
                    json.dumps(member["record"].get(field), sort_keys=True, allow_nan=False)
                    for member in members
                }
            )
            > 1
        ]
        duplicate_groups.append(
            {
                "smiles": smiles,
                "target_id": target,
                "endpoint": endpoint,
                "input_indices": [member["index"] for member in members],
                "classification": "identical_full_rows" if identical else "requires_assay_review",
                "differing_fields": differing_fields,
                "rows": [
                    {
                        "input_index": member["index"],
                        "assay_id": member["record"].get("assay_id"),
                        "source": member["record"]["source"],
                        "value": member["record"]["value"],
                        "unit": member["record"]["unit"],
                        "pactivity": member["pactivity"],
                    }
                    for member in members
                ],
                "proposal": "Keep one identical row"
                if identical
                else "Exclude all members until researcher resolves assay/measurement differences",
            }
        )
        if identical:
            proposed.append(members[0])
            for member in members[1:]:
                exclusions.append(
                    {
                        "input_index": member["index"],
                        "category": "identical_duplicate",
                        "reason": f"Full normalized row identical to input index {members[0]['index']}",
                    }
                )
        else:
            for member in members:
                exclusions.append(
                    {
                        "input_index": member["index"],
                        "category": "requires_assay_review",
                        "reason": "Same standardized ligand/target/endpoint has nonidentical records; no assay or value was selected",
                    }
                )
    proposed.sort(key=lambda member: member["index"])
    exclusions.sort(key=lambda item: item["input_index"])
    return {
        "input_count": len(records),
        "valid_count": len(records) - len(invalid),
        "suggested_count": len(proposed),
        "suggested_records": [member["record"] for member in proposed],
        "suggested_input_indices": [member["index"] for member in proposed],
        "duplicate_groups": duplicate_groups,
        "invalid_records": invalid,
        "excluded_input_indices": [item["input_index"] for item in exclusions],
        "exclusions": exclusions,
        "standardization_changes": changes,
        "index_base": 0,
        "requires_review": bool(invalid or duplicate_groups or changes),
        "note": "Read-only curation proposal; original input is unchanged. Review before adopting suggested_records. No assay was preferred, no value averaged, and source truth was not verified. Additional dataset-wide sequence/feature-schema and split checks run during evaluation.",
    }
