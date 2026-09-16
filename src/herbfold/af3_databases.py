"""Bounded readiness checks against the installer's verified database receipt.

The installer verifies complete contents. Request-time checks validate its
receipt and all required file metadata, without repeatedly hashing 0.8 TB.
Operators must regenerate the receipt after changing the database installation.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .alphafold import AF3_COMMIT, AF3_VERSION
from .file_identity import stat_record

DATABASE_FILES = (
    "bfd-first_non_consensus_sequences.fasta",
    "mgy_clusters_2022_05.fa",
    "uniprot_all_2021_04.fa",
    "uniref90_2022_05.fa",
    "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta",
    "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta",
    "rnacentral_active_seq_id_90_cov_80_linclust.fasta",
    "pdb_seqres_2022_09_28.fasta",
)
MANIFEST_NAME = "official_databases_manifest.json"


def file_stat(path: Path) -> dict:
    return stat_record(path)


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Database receipt needs full SHA-256 digests")
    return value


def _path(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Invalid database receipt path")
    path = root / relative
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError("Database receipt path escapes its installation")
    return path


def inspect_databases(config) -> dict:
    """Return readiness and a manifest/content-stat fingerprint, never file data."""
    result = {
        "status": "unavailable",
        "runnable": False,
        "blockers": [],
        "provenance": {
            "inspection_method": "verified_install_receipt_and_required_file_stats_v1",
            "full_contents_rehashed_this_check": False,
        },
    }
    try:
        if not config.database_dir:
            raise ValueError("AF3_DB_DIR needs the verified official genetic/template databases")
        root = Path(config.database_dir).resolve()
        receipt = _path(root, MANIFEST_NAME)
        if not receipt.is_file():
            raise ValueError(
                "AF3_DB_DIR has no completed official_databases_manifest.json; database installation is incomplete or unverified"
            )
        if receipt.stat().st_size > 1_000_000:
            raise ValueError("Database receipt exceeds the supported size")
        encoded = receipt.read_bytes()
        manifest = json.loads(encoded)
        if manifest.get("schema_version") != 1 or manifest.get("status") != "complete":
            raise ValueError("Official database installation has not completed validation")
        if manifest.get("source_tag") != f"v{AF3_VERSION}" or manifest.get("source_commit") != AF3_COMMIT:
            raise ValueError("Database manifest is not for the pinned AF3 release")
        components = manifest.get("components")
        expected = set(DATABASE_FILES) | {"mmcif_files"}
        if not isinstance(components, list) or len(components) != 9:
            raise ValueError("Database manifest must contain all eight FASTAs and the mmCIF tree")
        if {row.get("relative_path") for row in components} != expected:
            raise ValueError("Database manifest is missing required official components")
        snapshots, public = [], []
        for row in sorted(components, key=lambda row: row["relative_path"]):
            relative = row["relative_path"]
            tree = relative == "mmcif_files"
            if row.get("kind") != ("mmcif_tree" if tree else "fasta"):
                raise ValueError("Database component kind does not match its path")
            if type(row.get("record_count")) is not int or row["record_count"] < 1:
                raise ValueError("Database component has no verified records")
            if type(row.get("size_bytes")) is not int or row["size_bytes"] < 1:
                raise ValueError("Database component has no verified content size")
            _sha(row.get("sha256"))
            download = row.get("download") or {}
            if download.get("crc32c_verified") is not True or download.get("md5_verified") is not True:
                raise ValueError("Database component lacks verified Google download checksums")
            _sha(download.get("sha256"))
            path = _path(root, relative)
            if not (path.is_dir() if tree else path.is_file()):
                raise ValueError(f"Required database component is missing: {relative}")
            actual = file_stat(path)
            if actual != row.get("stat"):
                raise ValueError(f"Database component changed after installation validation: {relative}")
            if not tree and actual["size"] != row["size_bytes"]:
                raise ValueError(f"Database content size differs from its receipt: {relative}")
            snapshots.append({"relative_path": relative, "stat": actual})
            if tree:
                inventory = row.get("inventory") or {}
                _sha(inventory.get("sha256"))
                inventory_path = _path(root, inventory.get("relative_path"))
                if not inventory_path.is_file() or file_stat(inventory_path) != inventory.get("stat"):
                    raise ValueError("mmCIF inventory changed or is missing; revalidate the installation")
                snapshots.append(
                    {"relative_path": inventory["relative_path"], "stat": file_stat(inventory_path)}
                )
            public.append(
                {key: row[key] for key in ("relative_path", "kind", "size_bytes", "sha256", "record_count")}
            )
        manifest_sha = hashlib.sha256(encoded).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(
                {"manifest_sha256": manifest_sha, "installation": str(root), "files": snapshots},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        result.update(status="ready", runnable=True)
        result["provenance"].update(
            fingerprint_sha256=fingerprint,
            manifest_sha256=manifest_sha,
            source_tag=manifest["source_tag"],
            source_commit=manifest["source_commit"],
            installed_at=manifest.get("installed_at"),
            components=public,
            expanded_size_bytes=sum(row["size_bytes"] for row in components),
        )
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        result["blockers"] = [
            str(exc)
            if isinstance(exc, ValueError)
            else f"Official database receipt cannot be validated ({type(exc).__name__})"
        ]
    return result
