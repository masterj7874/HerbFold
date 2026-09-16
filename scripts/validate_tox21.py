#!/usr/bin/env python3
"""Run the real-data, fixed-protocol Tox21 assessment on a candidate snapshot."""
import argparse
import json
from pathlib import Path

from herbfold.toxicity_validation import run_tox21_validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("runtime/validation/sources/tox21.csv.gz"))
    parser.add_argument("--candidates", type=Path, default=Path("runtime/validation/candidate-inputs.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("runtime/validation/tox21"))
    args = parser.parse_args()
    result = run_tox21_validation(args.dataset, args.candidates, args.output)
    print(json.dumps({"status": result["status"], "candidates": result["candidates"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
