"""Revalidate retained AF3 databases and migrate their file identity receipts.

Every compressed archive, expanded FASTA and CIF is checked against its existing
verified SHA-256. This does not download, decompress or change scientific data.
Only after all content and final file-state checks pass are backed-up receipts
replaced, with the installation manifest published last.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from herbfold.af3_databases import DATABASE_FILES, MANIFEST_NAME
from herbfold.alphafold import AF3_COMMIT, AF3_VERSION
from herbfold.file_identity import stat_record

CHUNK = 8 * 1024 * 1024
SOURCE = "https://storage.googleapis.com/alphafold-databases/v3.0/"
PDB_OBJECT = "pdb_2022_09_28_mmcif_files.tar.zst"
ANCILLARY = {
    "source_url": SOURCE + PDB_OBJECT,
    "source_generation": "1730832728463652",
    "archive_path": "mmcif_files/mmcif_files.tar.gz",
    "size_bytes": 45,
    "sha256": "85cea451eec057fa7e734548ca3ba6d779ed5836a3f9de14b8394575ef0d7d8e",
    "preserved_relative_path": ".installation/ancillary/mmcif_files.tar.gz",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
            "Receipt requires a full SHA-256 digest")
    return value


def raw_stat(value):
    return (value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino,
            value.st_dev, stat.S_IFMT(value.st_mode))


def safe_path(root, relative, *, directory=False):
    require(isinstance(relative, str), "Receipt path must be a string")
    parts = PurePosixPath(relative)
    require(relative and parts.parts and not parts.is_absolute() and ".." not in parts.parts
            and str(parts) == relative, "Invalid receipt path")
    path = root
    for index, part in enumerate(parts.parts):
        path = path / part
        value = path.lstat()
        is_directory = directory or index < len(parts.parts) - 1
        require(stat.S_ISDIR(value.st_mode) if is_directory else stat.S_ISREG(value.st_mode),
                f"Receipt path is missing, unsafe or has the wrong type: {relative}")
    require(path.resolve().is_relative_to(root), "Receipt path escapes the database installation")
    return path


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_bytes(path, encoded):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as output:
        pending = Path(output.name)
        try:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
            os.replace(pending, path)
            fsync_directory(path.parent)
        finally:
            pending.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())


class Revalidation:
    def __init__(self, root, plan, workers, staging):
        self.root, self.plan, self.workers, self.staging = root, plan, workers, staging
        self.snapshots = {}
        self.guard = threading.Lock()
        self.cancelled = threading.Event()
        self.started_at = now()
        self.verified_files = 0
        self.verified_bytes = 0

    def emit(self, event, **fields):
        with self.guard:
            print(json.dumps({"at": now(), "event": event, **fields}), flush=True)

    def remember(self, relative, snapshot):
        with self.guard:
            old = self.snapshots.setdefault(relative, snapshot)
        require(old == snapshot, f"File changed during revalidation: {relative}")

    def read_json(self, relative):
        path = safe_path(self.root, relative)
        before = raw_stat(path.stat())
        require(before[0] <= 1_000_000, f"Receipt exceeds supported size: {relative}")
        encoded = path.read_bytes()
        require(raw_stat(path.stat()) == before, f"Receipt changed while reading: {relative}")
        self.remember(relative, before)
        result = json.loads(encoded)
        require(isinstance(result, dict), "Receipt must be a JSON object")
        return result

    def hash_file(self, relative, expected_sha, expected_size, *, progress=True):
        digest(expected_sha)
        require(type(expected_size) is int and expected_size > 0, "Invalid verified file size")
        if self.cancelled.is_set():
            raise ValueError("Revalidation cancelled after another file failed")
        path = safe_path(self.root, relative)
        before = raw_stat(path.stat())
        require(before[0] == expected_size, f"File size differs from verified receipt: {relative}")
        if progress:
            self.emit("hash_started", path=relative, size_bytes=expected_size)
        sha, total, last_report = hashlib.sha256(), 0, time.monotonic()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            require(raw_stat(os.fstat(source.fileno())) == before,
                    f"File was replaced before hashing: {relative}")
            while chunk := source.read(CHUNK):
                if self.cancelled.is_set():
                    raise ValueError("Revalidation cancelled after another file failed")
                sha.update(chunk)
                total += len(chunk)
                if progress and time.monotonic() - last_report >= 30:
                    self.emit("hash_progress", path=relative, checked_bytes=total, size_bytes=expected_size)
                    last_report = time.monotonic()
            require(raw_stat(os.fstat(source.fileno())) == before,
                    f"File changed while hashing: {relative}")
        require(raw_stat(path.lstat()) == before, f"File changed while hashing: {relative}")
        require(total == expected_size and sha.hexdigest() == expected_sha,
                f"SHA-256 mismatch: {relative}")
        stable = stat_record(path)
        require(raw_stat(path.lstat()) == before, f"File changed after hashing: {relative}")
        self.remember(relative, before)
        with self.guard:
            self.verified_files += 1
            self.verified_bytes += total
        if progress:
            self.emit("hash_verified", path=relative, size_bytes=total, sha256=expected_sha)
        return stable

    def validate_receipts(self):
        manifest = self.read_json(MANIFEST_NAME)
        require(manifest.get("schema_version") == 1 and manifest.get("status") == "complete",
                "A complete verified schema-1 manifest is required")
        for source in (manifest, self.plan):
            require(source.get("source_tag") == f"v{AF3_VERSION}"
                    and source.get("source_commit") == AF3_COMMIT,
                    "Manifest and plan must match the pinned AF3 release")
        expected = {name + ".zst": name for name in DATABASE_FILES} | {PDB_OBJECT: "mmcif_files"}
        objects = self.plan.get("objects")
        require(isinstance(objects, list) and self.plan.get("object_count") == len(objects) == 9,
                "Plan must include nine official source objects")
        items = {item["object"]: item for item in objects}
        require(set(items) == set(expected), "Plan does not contain the exact official database objects")
        components = manifest.get("components")
        require(isinstance(components, list) and len(components) == 9,
                "Manifest must contain all nine components")
        require({row.get("object") for row in components} == set(expected),
                "Manifest components differ from the pinned plan")
        for row in components:
            name, item = row["object"], items[row["object"]]
            require(row.get("relative_path") == expected[name]
                    and row.get("kind") == ("mmcif_tree" if name == PDB_OBJECT else "fasta"),
                    "Component kind or path differs from the pinned plan")
            require(type(row.get("record_count")) is int and row["record_count"] > 0
                    and type(row.get("size_bytes")) is int and row["size_bytes"] > 0,
                    "Component requires verified records and size")
            digest(row.get("sha256"))
            require(row.get("validation_version", 0) >= 2
                    and row.get("zstd_decode_verified") is True
                    and row.get("zstd_frame_eof_verified") is True,
                    "Component lacks complete installer validation evidence")
            expanded = self.read_json(f".installation/{name}.expanded.json")
            require(expanded == row, f"Expanded receipt differs from completed manifest: {name}")
            download = self.read_json(f".downloads/{name}.receipt.json")
            require(download == row.get("download"), f"Download receipt differs from manifest: {name}")
            require(item.get("url") == SOURCE + name and download.get("url") == item["url"]
                    and download.get("generation") == item.get("generation")
                    and isinstance(item.get("generation"), str) and bool(item["generation"])
                    and type(item.get("compressed_bytes")) is int and item["compressed_bytes"] > 0
                    and download.get("size_bytes") == item["compressed_bytes"],
                    f"Source identity differs from pinned plan: {name}")
            for algorithm in ("crc32c", "md5"):
                require(download.get(algorithm + "_verified") is True
                        and isinstance(item.get("hashes", {}).get(algorithm), str)
                        and bool(item["hashes"][algorithm])
                        and download.get(algorithm) == item["hashes"][algorithm],
                        f"Source checksum evidence differs from pinned plan: {name}")
            digest(download.get("sha256"))
            if row["kind"] == "fasta":
                require(row["size_bytes"] == item.get("zstd_first_frame", {}).get("frame_content_size_bytes"),
                        f"Expanded size differs from pinned plan: {name}")
        return manifest

    def verify_tree(self, row):
        tree = safe_path(self.root, "mmcif_files", directory=True)
        before = raw_stat(tree.stat())
        inventory = row.get("inventory", {})
        require(inventory.get("relative_path") == "pdb_inventory.jsonl"
                and inventory.get("sha256") == row["sha256"], "PDB inventory identity differs from manifest")
        inventory_path = safe_path(self.root, inventory["relative_path"])
        self.hash_file(inventory["relative_path"], inventory["sha256"], inventory.get("stat", {}).get("size"))
        output_path = self.staging / "pdb_inventory.jsonl"
        seen, count, total, new_sha = set(), 0, 0, hashlib.sha256()
        with inventory_path.open("rb") as source, output_path.open("xb") as output:
            for line in source:
                require(len(line) <= 16384, "PDB inventory row exceeds supported size")
                entry = json.loads(line)
                relative = entry.get("path")
                require(isinstance(relative, str), "Invalid PDB inventory path")
                parts = PurePosixPath(relative)
                require(len(parts.parts) == 2 and parts.parts[0] == "mmcif_files"
                        and parts.name.endswith(".cif") and str(parts) == relative
                        and ".." not in parts.parts, "Invalid PDB inventory path")
                require(relative not in seen, "Duplicate PDB inventory entry")
                seen.add(relative)
                entry["stat"] = self.hash_file(relative, entry.get("sha256"), entry.get("size_bytes"), progress=False)
                encoded = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode()
                output.write(encoded)
                new_sha.update(encoded)
                count += 1
                total += entry["size_bytes"]
                if count % 10000 == 0:
                    self.emit("cif_progress", verified_files=count, expected_files=row["record_count"], checked_bytes=total)
            output.flush()
            os.fsync(output.fileno())
        require(raw_stat(inventory_path.stat()) == self.snapshots[inventory["relative_path"]],
                "PDB inventory changed while checking structures")
        require(count == row["record_count"] and total == row["size_bytes"],
                "PDB inventory count or total size differs from manifest")
        require(seen == {"mmcif_files/" + path.name for path in tree.iterdir()},
                "PDB tree has missing or unexpected entries")
        require(raw_stat(tree.stat()) == before, "PDB tree changed while revalidating")
        self.remember("mmcif_files", before)
        ancillary = row.get("excluded_ancillary_members", [])
        require(len(ancillary) == 1 and all(ancillary[0].get(key) == value for key, value in ANCILLARY.items()),
                "Preserved ancillary evidence differs from the official source")
        ancillary = copy.deepcopy(ancillary)
        entry = ancillary[0]
        entry["stat"] = self.hash_file(entry["preserved_relative_path"], entry["sha256"], entry["size_bytes"])
        self.emit("cif_verified", verified_files=count, checked_bytes=total)
        return {"stat": stat_record(tree), "sha256": new_sha.hexdigest(),
                "excluded_ancillary_members": ancillary}

    def check_snapshots(self):
        for relative, expected in self.snapshots.items():
            path = safe_path(self.root, relative, directory=relative == "mmcif_files")
            require(raw_stat(path.lstat()) == expected, f"File changed before receipt publication: {relative}")

    def run(self):
        manifest = self.validate_receipts()
        old_manifest_sha = hashlib.sha256((self.root / MANIFEST_NAME).read_bytes()).hexdigest()
        self.emit("validation_started", workers=self.workers,
                  expanded_bytes=sum(row["size_bytes"] for row in manifest["components"]),
                  compressed_bytes=sum(row["download"]["size_bytes"] for row in manifest["components"]))
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {}
            # Start the largest expanded sources first, including the many-file tree.
            for row in sorted(manifest["components"], key=lambda value: -value["size_bytes"]):
                if row["kind"] == "mmcif_tree":
                    future = pool.submit(self.verify_tree, row)
                else:
                    future = pool.submit(self.hash_file, row["relative_path"], row["sha256"], row["size_bytes"])
                futures[future] = row["relative_path"]
            for row in manifest["components"]:
                download = row["download"]
                relative = ".downloads/" + row["object"]
                futures[pool.submit(self.hash_file, relative, download["sha256"], download["size_bytes"])] = relative
            try:
                for future in concurrent.futures.as_completed(futures):
                    results[futures[future]] = future.result()
            except BaseException:
                self.cancelled.set()
                for future in futures:
                    future.cancel()
                raise
        self.emit("final_metadata_check", files=len(self.snapshots))
        self.check_snapshots()
        backup = self.root / ".installation" / ("revalidation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        backup.mkdir()
        receipt_paths = [MANIFEST_NAME, "pdb_inventory.jsonl"]
        for row in manifest["components"]:
            receipt_paths.extend((f".installation/{row['object']}.expanded.json", f".downloads/{row['object']}.receipt.json"))
        for relative in receipt_paths:
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(safe_path(self.root, relative), destination)
            with destination.open("rb") as copied:
                os.fsync(copied.fileno())
        for directory in (backup / ".installation", backup / ".downloads", backup, backup.parent):
            fsync_directory(directory)
        self.check_snapshots()
        report = {"status": "verified", "started_at": self.started_at, "verified_at": now(),
                  "database_dir": str(self.root), "backup_dir": str(backup),
                  "previous_manifest_sha256": old_manifest_sha,
                  "files_sha256_verified": self.verified_files,
                  "bytes_sha256_verified": self.verified_bytes,
                  "verified_file_counts": {"fasta": 8, "archive": 9, "inventory": 1, "ancillary": 1,
                                           "cif": next(row["record_count"] for row in manifest["components"] if row["kind"] == "mmcif_tree")},
                  "expanded_content_bytes": sum(row["size_bytes"] for row in manifest["components"]),
                  "compressed_bytes": sum(row["download"]["size_bytes"] for row in manifest["components"]),
                  "cif_files": next(row["record_count"] for row in manifest["components"] if row["kind"] == "mmcif_tree"),
                  "content_modified": False, "downloads_performed": 0,
                  "preserved_inputs": [row["relative_path"] for row in manifest["components"]]
                                      + [".downloads/" + row["object"] for row in manifest["components"]]
                                      + [ANCILLARY["preserved_relative_path"]],
                  "identity_transitions": [
                      {"path": row["relative_path"], "previous_stat": row["stat"],
                       "current_numeric_device": self.snapshots[row["relative_path"]][4],
                       "revalidated_stat": results[row["relative_path"]]["stat"]
                       if row["kind"] == "mmcif_tree" else results[row["relative_path"]]}
                      for row in manifest["components"]],
                  "method": "full_existing_content_sha256_and_pinned_source_receipts",
                  "checksum_scope": "SHA-256 rechecked for complete retained archives, FASTAs, inventory, CIFs and ancillary. Original verified CRC32C/MD5 evidence preserved and matched to pinned plan; no repeat decode."}
        atomic_json(backup / "revalidation.json", report)
        self.emit("publishing_receipts", backup_dir=str(backup))
        # Publishing the inventory first makes the old manifest fail closed until
        # every component receipt is updated and the final manifest is replaced.
        inventory_path = self.root / "pdb_inventory.jsonl"
        os.replace(self.staging / "pdb_inventory.jsonl", inventory_path)
        fsync_directory(inventory_path.parent)
        self.snapshots["pdb_inventory.jsonl"] = raw_stat(inventory_path.stat())
        new_manifest = copy.deepcopy(manifest)
        revalidated_at = now()
        for row in new_manifest["components"]:
            if row["kind"] == "mmcif_tree":
                row.update(results[row["relative_path"]])
                row["inventory"].update(sha256=row["sha256"], stat=stat_record(inventory_path))
            else:
                row["stat"] = results[row["relative_path"]]
            row["download"]["stat"] = results[".downloads/" + row["object"]]
            row["download"]["content_revalidated_at"] = revalidated_at
            row["content_revalidated_at"] = revalidated_at
            for relative, value in ((f".downloads/{row['object']}.receipt.json", row["download"]),
                                    (f".installation/{row['object']}.expanded.json", row)):
                atomic_json(self.root / relative, value)
                self.snapshots[relative] = raw_stat((self.root / relative).stat())
        new_manifest["revalidation"] = {"completed_at": revalidated_at, "method": report["method"],
                                        "previous_manifest_sha256": old_manifest_sha,
                                        "backup_relative_path": str(backup.relative_to(self.root))}
        self.check_snapshots()
        atomic_json(self.root / MANIFEST_NAME, new_manifest)
        report.update(status="complete", completed_at=now(),
                      manifest_sha256=hashlib.sha256((self.root / MANIFEST_NAME).read_bytes()).hexdigest())
        atomic_json(backup / "revalidation.json", report)
        self.emit("revalidation_complete", **{key: report[key] for key in ("backup_dir", "manifest_sha256", "cif_files")})
        return report


def revalidate(root, plan, workers=4, report_path=None):
    root = Path(root).expanduser().resolve(strict=True)
    require(root.is_dir(), "Database root is not a directory")
    require(type(workers) is int and 1 <= workers <= 16, "Workers must be between 1 and 16")
    if isinstance(plan, (str, Path)):
        plan = json.loads(Path(plan).read_text())
    require(isinstance(plan, dict), "Plan must be a JSON object")
    if report_path:
        report_path = Path(report_path).expanduser().resolve()
        require(not report_path.is_relative_to(root), "Report output must be outside the database installation")
        require(report_path.parent.is_dir(), "Report output directory is missing")
    work = safe_path(root, ".installation", directory=True)
    safe_path(root, ".downloads", directory=True)
    lock_path = root / ".installation.lock"
    require(not lock_path.is_symlink(), "Unsafe installer lock")
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(dir=work, prefix="revalidation-pending-") as staging:
            try:
                report = Revalidation(root, plan, workers, Path(staging)).run()
            except BaseException as exc:
                if report_path:
                    atomic_json(Path(report_path), {"status": "failed", "failed_at": now(), "error": f"{type(exc).__name__}: {exc}"})
                raise
    if report_path:
        atomic_json(Path(report_path), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=Path("docs/af3-msa-db-plan.json"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    revalidate(args.directory, args.plan, args.workers, args.report)


if __name__ == "__main__":
    main()
