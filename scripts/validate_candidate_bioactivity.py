#!/usr/bin/env python3
"""Validate a fixed JSONL candidate snapshot against public ChEMBL assay evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

# Limit numerical worker pools before importing NumPy/scikit-learn.
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"

from herbfold.bio_validation import _write, run_bio_validation  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=Path("runtime/validation/candidate-inputs.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("runtime/validation/bio-validation"))
    parser.add_argument("--catalog", type=Path, default=Path("runtime/discovery/discovery.sqlite3"))
    parser.add_argument("--max-activities", type=int, default=20_000,
                        help="Maximum exact activity rows per target; default covers currently available endpoints")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    digest = hashlib.sha256(args.candidates.read_bytes()).hexdigest()
    with args.candidates.open(encoding="utf-8") as stream:
        candidates = (json.loads(line) for line in stream if line.strip())
        report = run_bio_validation(candidates, args.output, max_activity_records=args.max_activities,
                                    refresh=args.refresh, discovery_db=args.catalog)
    report["input_snapshot"] = {"path": str(args.candidates), "sha256": digest}
    _write(args.output / "summary.json", report)
    print(json.dumps({"status": report["status"], "counts": report["counts"],
                      "output": str(args.output), "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
