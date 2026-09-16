#!/usr/bin/env python3
"""Uniform, fixed-seed reservoir sample of persisted scale candidates, plus prior cohort."""
import argparse
import hashlib
import json
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale-root", type=Path, default=Path("runtime/validation/scale"))
    parser.add_argument("--existing", type=Path, default=Path("runtime/validation/candidate-inputs.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("runtime/validation/candidate-assessment-inputs.jsonl"))
    parser.add_argument("--sample-size", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260908)
    args = parser.parse_args()
    if not 1 <= args.sample_size <= 100000:
        parser.error("sample-size must be between 1 and 100000")
    progress = json.loads((args.scale_root / "scale-progress.json").read_text())
    if progress["status"] in {"running", "preparing"}:
        raise RuntimeError("Finish the scale run before fixing its assessment cohort")
    rows = {}
    for line in args.existing.read_text().splitlines():
        row = json.loads(line)
        rows[row["smiles"]] = {**row, "cohorts": ["prior_campaign_complete"]}
    prior_count = len(rows)
    rng = random.Random(args.seed)
    reservoir = []
    with sqlite3.connect(f"file:{(args.scale_root / 'scale.sqlite3').resolve()}?mode=ro", uri=True) as connection:
        # A read transaction fixes the population while the reservoir scans it.
        connection.execute("BEGIN")
        total = 0
        for total, (rowid,) in enumerate(connection.execute("SELECT rowid FROM candidates ORDER BY rowid"), 1):
            if total <= args.sample_size:
                reservoir.append(rowid)
            else:
                index = rng.randrange(total)
                if index < args.sample_size:
                    reservoir[index] = rowid
        if total != progress["retained_unique"]:
            raise RuntimeError("Persisted candidate count differs from the completed report")
        population_digest = hashlib.sha256()
        for (digest,) in connection.execute("SELECT digest FROM candidates ORDER BY digest"):
            population_digest.update(digest)
        selected = []
        for rowid in sorted(reservoir):
            digest, smiles, left, right = connection.execute(
                "SELECT digest,smiles,left_fragment,right_fragment FROM candidates WHERE rowid=?", (rowid,)
            ).fetchone()
            selected.append({"id": "scale-" + bytes(digest).hex(), "smiles": smiles,
                             "scale_rowid": rowid, "left_fragment": left, "right_fragment": right,
                             "cohorts": ["scale_uniform_sample"]})
    for row in selected:
        if row["smiles"] in rows:
            rows[row["smiles"]]["cohorts"].append("scale_uniform_sample")
            rows[row["smiles"]]["scale_rowid"] = row["scale_rowid"]
        else:
            rows[row["smiles"]] = row
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".jsonl.part")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows.values()))
    temporary.replace(args.output)
    receipt = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": "Uniform reservoir sampling without replacement over SQLite rows; fixed seed; no QED or prediction selection",
        "seed": args.seed, "scale_population": total, "scale_sample_count": len(selected),
        "scale_attempts": progress["attempted"], "prior_unique_count": prior_count,
        "combined_unique_count": len(rows), "overlap": prior_count + len(selected) - len(rows),
        "scale_structure_digest": population_digest.hexdigest(),
        "input_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "scope": "All prior campaign candidates plus a uniform sample of the measured scale population. Remaining scale candidates are not bioassessed.",
        "sample_rowids": sorted(reservoir),
    }
    args.output.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({key: value for key, value in receipt.items() if key != "sample_rowids"}, indent=2))


if __name__ == "__main__":
    main()
