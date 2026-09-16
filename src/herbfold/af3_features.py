"""Validated, immutable protein MSA/template reuse between ligand predictions.

Only the worker may publish a cache after a successful upstream data-pipeline
process. Public AF3 inputs still cannot supply paths or arbitrary custom MSAs.
The initial implementation supports one protein chain, as used by the studio.
Multi-protein legacy inputs retain the upstream combined pipeline.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
import re
import uuid
from itertools import islice
from pathlib import Path

from Bio.PDB.MMCIF2Dict import MMCIF2Dict

from . import alphafold
from .storage import utcnow

MAX_FEATURE_BYTES = 256_000_000
MAX_MSA_BYTES = 120_000_000
FEATURE_SCHEMA_VERSION = 1


def supported(payload):
    proteins = [entry["protein"] for entry in payload["sequences"] if "protein" in entry]
    return len(proteins) == 1 and isinstance(proteins[0]["id"], str)


def protein(payload):
    if not supported(payload):
        raise alphafold.AF3ValidationError("Reusable features require one protein chain")
    return next(entry["protein"] for entry in payload["sequences"] if "protein" in entry)


def identity(payload, config, provenance):
    database = provenance.get("databases", {})
    fingerprint = database.get("fingerprint_sha256")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise alphafold.AF3ValidationError("MSA reuse requires a verified database fingerprint")
    if provenance.get("source_modified"):
        raise alphafold.AF3ValidationError("Reusable MSA features require the unmodified pinned AF3 source")
    sequence = protein(payload)["sequence"]
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "protein_sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "protein_length": len(sequence),
        "database_fingerprint": fingerprint,
        "af3_version": alphafold.AF3_VERSION,
        "af3_commit": alphafold.AF3_COMMIT,
        "entrypoint_sha256": provenance.get("entrypoint_sha256"),
        "docker_image_id": provenance.get("docker_image_id"),
        "max_template_date": config.max_template_date,
        "hmmer_tools": provenance.get("hmmer_tools"),
    }


def cache_key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()


def _write(path, encoded):
    with path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _read(path, limit=MAX_FEATURE_BYTES):
    if path.stat().st_size > limit:
        raise alphafold.AF3ValidationError("MSA/template artifact exceeds the supported size limit")
    with path.open("rb") as stream:
        encoded = stream.read(limit + 1)
    if len(encoded) > limit:
        raise alphafold.AF3ValidationError("MSA/template artifact exceeds the supported size limit")
    try:
        return json.loads(encoded), encoded
    except (ValueError, UnicodeError) as exc:
        raise alphafold.AF3ValidationError("Malformed MSA/template JSON") from exc


def _msa(value, sequence):
    if not isinstance(value, str) or not value or len(value) > MAX_MSA_BYTES:
        raise alphafold.AF3ValidationError("A completed search must contain nonempty, bounded MSA alignments")
    count, residues, header = 0, [], False

    def finish():
        nonlocal count
        aligned = "".join(residues)
        if not aligned or not re.fullmatch(r"[A-Za-z.\-]+", aligned):
            raise alphafold.AF3ValidationError("MSA contains an invalid sequence")
        aligned = re.sub(r"[a-z.]", "", aligned)
        if len(aligned) != len(sequence) or (count == 0 and aligned != sequence):
            raise alphafold.AF3ValidationError(
                "MSA query/alignment does not match the full requested protein sequence"
            )
        count += 1
        if count > 100_000:
            raise alphafold.AF3ValidationError("MSA exceeds the supported sequence count")

    for line in io.StringIO(value):
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if header:
                finish()
            header, residues = True, []
        elif header:
            residues.append(line)
        else:
            raise alphafold.AF3ValidationError("MSA must begin with a FASTA query header")
    if not header:
        raise alphafold.AF3ValidationError("MSA has no query sequence")
    finish()
    return count


def _template(template, sequence):
    if not isinstance(template, dict) or set(template) != {"mmcif", "queryIndices", "templateIndices"}:
        raise alphafold.AF3ValidationError("Template must contain inline mmCIF and residue mappings only")
    text = template["mmcif"]
    query, mapping = template["queryIndices"], template["templateIndices"]
    if not isinstance(text, str) or not 1 <= len(text) <= 16_000_000:
        raise alphafold.AF3ValidationError("Template mmCIF is empty or oversized")
    if (
        not isinstance(query, list)
        or not isinstance(mapping, list)
        or not query
        or len(query) != len(mapping)
    ):
        raise alphafold.AF3ValidationError("Template residue mapping is missing or inconsistent")
    if any(type(i) is not int or not 0 <= i < len(sequence) for i in query) or len(set(query)) != len(query):
        raise alphafold.AF3ValidationError("Template query mapping is outside the requested sequence")
    try:
        cif = MMCIF2Dict(io.StringIO(text))
        sequence_ids = cif.get("_entity_poly_seq.num") or cif.get("_pdbx_poly_seq_scheme.seq_id")
        template_length = max(int(i) for i in sequence_ids)
        if any(type(i) is not int or not 0 <= i < template_length for i in mapping) or len(
            set(mapping)
        ) != len(mapping):
            raise ValueError("Invalid template indices")
        axes = [cif.get("_atom_site.Cartn_" + axis, []) for axis in "xyz"]
        if not axes[0] or len({len(axis) for axis in axes}) != 1:
            raise ValueError("Missing template coordinates")
        if any(not math.isfinite(float(value)) for axis in axes for value in axis):
            raise ValueError("Nonfinite coordinates")
        chains = set(cif.get("_atom_site.label_asym_id", []))
        if len(chains) != 1 or chains & {".", "?"}:
            raise ValueError("Template must have one observed chain")
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise alphafold.AF3ValidationError("Template mmCIF or residue mapping is invalid") from exc
    entry = cif.get("_entry.id", [])
    return {
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "entry_id": entry[0] if entry else None,
        "data_block_name": cif.get("data_"),
        "mapped_residues": len(query),
        "template_length": template_length,
        "mapping_sha256": hashlib.sha256(
            _json_bytes({"queryIndices": query, "templateIndices": mapping})
        ).hexdigest(),
        "query_indices": query,
        "template_indices": mapping,
    }


def validate_features(features, sequence):
    if not isinstance(features, dict) or set(features) != {
        "sequence",
        "unpairedMsa",
        "pairedMsa",
        "templates",
    }:
        raise alphafold.AF3ValidationError("Complete protein sequence, both MSAs and templates are required")
    if features["sequence"] != sequence:
        raise alphafold.AF3ValidationError("Cached features belong to another protein sequence")
    unpaired = _msa(features["unpairedMsa"], sequence)
    paired = _msa(features["pairedMsa"], sequence)
    templates = features["templates"]
    if not isinstance(templates, list) or len(templates) > 4:
        raise alphafold.AF3ValidationError("Pinned protein search supports at most four templates")
    template_records = [_template(item, sequence) for item in templates]
    warnings = []
    if unpaired == 1:
        warnings.append(
            "Completed MSA search returned only the query sequence; no unpaired homologous sequence was found."
        )
    if paired == 1:
        warnings.append("The paired MSA contains only the query sequence.")
    if not templates:
        warnings.append(
            "Completed template search returned no usable templates at the requested date cutoff."
        )
    return {
        "unpaired_msa_sequences": unpaired,
        "paired_msa_sequences": paired,
        "non_query_sequences": unpaired - 1,
        "paired_non_query_sequences": paired - 1,
        "template_count": len(templates),
        "templates": template_records,
        "warnings": warnings,
    }


class FeatureCache:
    def __init__(self, root):
        self.root = Path(root).resolve() / "af3-protein-features"
        if self.root.is_symlink():
            raise alphafold.AF3ValidationError("MSA feature cache root cannot be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)

    def _directory(self, key):
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise alphafold.AF3ValidationError("Invalid MSA cache identity")
        directory = self.root / key
        if directory.is_symlink():
            raise alphafold.AF3ValidationError("MSA cache directory cannot be a symlink")
        return directory

    def load(self, expected, sequence):
        key = cache_key(expected)
        directory = self._directory(key)
        if not (directory / "manifest.json").is_file():
            return None
        try:
            manifest_path = alphafold.safe_output_path(directory, "manifest.json")
            manifest, _ = _read(manifest_path, 1_000_000)
            if (
                manifest.get("status") != "complete"
                or manifest.get("identity") != expected
                or manifest.get("cache_key") != key
            ):
                return None
            features, encoded = _read(alphafold.safe_output_path(directory, "protein_features.json"))
            if hashlib.sha256(encoded).hexdigest() != manifest.get("feature_sha256"):
                return None
            metrics = validate_features(features, sequence)
            if (
                manifest.get("metrics") != metrics
                or manifest.get("search_execution", {}).get("return_code") != 0
            ):
                return None
            return features, {
                **expected,
                **metrics,
                "status": "ready",
                "cache_hit": True,
                "cache_key": key,
                "source_job_id": manifest["source_job_id"],
                "feature_sha256": manifest["feature_sha256"],
                "created_at": manifest["created_at"],
                "search_execution": manifest["search_execution"],
            }
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def publish(self, expected, payload, data_output, source_job_id, execution):
        if execution.get("return_code") != 0 or not execution.get("finished_at"):
            raise alphafold.AF3ValidationError(
                "Only a successfully completed data pipeline may publish features"
            )
        original, mode = alphafold.validate_input(payload)
        if mode != "search":
            raise alphafold.AF3ValidationError("MSA-free inputs cannot populate the search feature cache")
        files = list(islice(Path(data_output).rglob("*_data.json"), 2))
        if len(files) != 1:
            raise alphafold.AF3ValidationError("Data pipeline must produce exactly one completed *_data.json")
        path = alphafold.safe_output_path(data_output, str(files[0].relative_to(data_output)))
        processed, source_bytes = _read(path)
        if (
            processed.get("name") != original["name"]
            or processed.get("dialect") != "alphafold3"
            or processed.get("version") != alphafold.AF3_SCHEMA_VERSION
        ):
            raise alphafold.AF3ValidationError(
                "Data pipeline output identity or schema does not match its input"
            )
        requested = protein(original)
        actual = protein(processed)
        if actual.get("id") != requested["id"] or actual.get("modifications", []) != []:
            raise alphafold.AF3ValidationError("Data pipeline protein identity changed")
        features = {key: actual.get(key) for key in ("sequence", "unpairedMsa", "pairedMsa", "templates")}
        metrics = validate_features(features, requested["sequence"])
        key = cache_key(expected)
        if (
            expected.get("protein_sequence_sha256")
            != hashlib.sha256(requested["sequence"].encode()).hexdigest()
        ):
            raise alphafold.AF3ValidationError("Feature key differs from the searched protein")
        encoded = _json_bytes(features)
        if len(encoded) > MAX_FEATURE_BYTES:
            raise alphafold.AF3ValidationError("MSA/template features exceed the cache size limit")
        manifest = {
            "status": "complete",
            "identity": expected,
            "cache_key": key,
            "source_job_id": source_job_id,
            "created_at": utcnow(),
            "feature_sha256": hashlib.sha256(encoded).hexdigest(),
            "source_data_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_data_relative_path": str(path.relative_to(data_output)),
            "metrics": metrics,
            "search_execution": execution,
        }
        pending = self.root / (".pending-" + uuid.uuid4().hex)
        pending.mkdir()
        _write(pending / "protein_features.json", encoded)
        _write(pending / "manifest.json", _json_bytes(manifest))
        directory = self._directory(key)
        if directory.exists():
            # Preserve a corrupt/incomplete prior cache for inspection. The
            # shared AF3 execution lock serializes cache writers across workers.
            directory.rename(self.root / (".invalid-" + key + "-" + uuid.uuid4().hex))
        pending.rename(directory)
        loaded = self.load(expected, requested["sequence"])
        if loaded is None:
            raise alphafold.AF3ValidationError("Published MSA features failed verification")
        loaded[1]["cache_hit"] = False
        return loaded


def write_inference_input(path, original, features):
    validated, mode = alphafold.validate_input(original)
    if mode != "search":
        raise alphafold.AF3ValidationError("Enriched inference input requires explicit search mode")
    target = protein(validated)
    validate_features(features, target["sequence"])
    output = copy.deepcopy(validated)
    protein(output).update({key: features[key] for key in ("unpairedMsa", "pairedMsa", "templates")})
    encoded = _json_bytes(output)
    path = Path(path)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != encoded:
            raise alphafold.AF3ValidationError("Enriched inference input cannot overwrite different data")
    else:
        _write(path, encoded)
    return {"inference_input_sha256": hashlib.sha256(encoded).hexdigest(), "inference_input_path": str(path)}
