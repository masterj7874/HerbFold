#!/usr/bin/env python3
"""Run genuine, resumable molecular enumeration; never label attempts as unique."""

import argparse
import json
from pathlib import Path

from herbfold.scale_validation import ScaleValidation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("runtime/validation/scale"))
    parser.add_argument("--catalog", type=Path, default=Path("runtime/discovery/discovery.sqlite3"))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--max-parents", type=int)
    parser.add_argument("--attempts", type=int, default=1_000_000)
    parser.add_argument("--max-seconds", type=int, default=1800)
    parser.add_argument("--max-storage-gb", type=int, default=100)
    args = parser.parse_args()
    validator = ScaleValidation(args.root, args.catalog)
    print(json.dumps(validator.prepare(workers=args.workers, max_parents=args.max_parents)), flush=True)
    if not args.prepare_only:
        print(
            json.dumps(
                validator.run(
                    args.attempts,
                    workers=args.workers,
                    max_seconds=args.max_seconds,
                    max_storage_gb=args.max_storage_gb,
                )
            ),
            flush=True,
        )
        print(json.dumps(validator.audit()), flush=True)


if __name__ == "__main__":
    main()
