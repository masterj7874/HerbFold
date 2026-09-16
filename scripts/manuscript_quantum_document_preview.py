"""Save only a new 240 dpi Word image from the frozen S4 Matplotlib builder.

Requires the completed sensitivity artifacts. The original figure function is
called unchanged; its save calls are filtered so only a new document PNG is
written directly from the Figure object. No raster image is read or resized.
"""
from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
from manuscript_quantum_sensitivity import NAME, ROOT, fingerprint, make_figure
from matplotlib.figure import Figure


def main():
    data = ROOT / "research/manuscript/q1_extension/quantum"
    output = ROOT / "output/manuscript/figures"
    summary = json.loads((data / "summary.json").read_text())
    manifest = json.loads((data / "artifact_manifest.json").read_text())
    frozen = {row["path"]: row for row in manifest["artifacts"] + summary["sources"]}
    frozen["research/manuscript/q1_extension/quantum/artifact_manifest.json"] = fingerprint(data / "artifact_manifest.json")
    for path, expected in frozen.items():
        if fingerprint(ROOT / path)["sha256"] != expected["sha256"]:
            raise ValueError(f"Frozen source changed: {path}")
    with (data / "gamma_sensitivity.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key, value in row.items():
            try:
                row[key] = json.loads(value)
            except json.JSONDecodeError:
                row[key] = value
    with (data / "qubit_controls.csv").open() as handle:
        controls = list(csv.DictReader(handle))
    errors = np.array([[float(row[key]) for row in controls]
                       for key in ("prepared_zero_error", "prepared_one_error")])
    destination = output / f"{NAME}.docx.png"
    publication_png = output / f"{NAME}.png"
    original_save = Figure.savefig
    writes, suppressed = [], []

    def save_document_only(figure, filename, *args, **kwargs):
        filename = Path(filename)
        if filename == publication_png:
            if writes:
                raise ValueError("Unexpected repeated figure export")
            kwargs["dpi"] = 240
            original_save(figure, destination, *args, **kwargs)
            writes.append(str(destination.relative_to(ROOT)))
        else:
            suppressed.append(str(filename.relative_to(ROOT)))

    with patch.object(Figure, "savefig", save_document_only):
        make_figure(output, rows, summary["exclusion_at_gamma_one"], errors)
    if len(writes) != 1 or len(suppressed) != 3:
        raise ValueError("Unexpected figure builder export schedule")
    for path, expected in frozen.items():
        if fingerprint(ROOT / path)["sha256"] != expected["sha256"]:
            raise ValueError(f"Frozen source changed during document export: {path}")
    receipt = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "passed",
        "method": "Unchanged original Matplotlib figure builder; savefig directly to new PNG at 240 dpi",
        "raster_resampling": False,
        "original_export_calls_suppressed": suppressed,
        "frozen_files_verified_unchanged": len(frozen),
        "frozen_files": list(frozen.values()),
        "output": fingerprint(destination),
        "requested_dpi": 240,
        "visual_review": "pending",
    }
    (data / "document-preview.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({key: receipt[key] for key in ("status", "output", "frozen_files_verified_unchanged")}))


if __name__ == "__main__":
    main()
