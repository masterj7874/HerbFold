"""Resume and verify the complete, pinned official AF3 database set.

Run with a Python providing httpx, google-crc32c and zstandard. Downloads are
generation-pinned, verified against Google's CRC32C/MD5, and retained separately
from atomically published expanded databases. An interrupted installation is not
a ready database. No GPU or AF3 prediction is launched by this installer.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import fcntl
import hashlib
import json
import os
import re
import shutil
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import google_crc32c
import httpx
import zstandard

from herbfold.file_identity import stat_record

CHUNK = 4 * 1024 * 1024
RESERVE = 100 * 1024**3
MIN_PDB_FILES = 100_000
SOURCE = "https://storage.googleapis.com/alphafold-databases/v3.0/"
VALIDATION_VERSION = 2
# The pinned official archive contains this one non-CIF regular file. Its
# identity was inspected locally; preserve its bytes outside the template tree.
# No nested archive is opened. See docs/af3-pdb-archive-member-audit.json.
PDB_ANCILLARY = {
    "source_url": SOURCE + "pdb_2022_09_28_mmcif_files.tar.zst",
    "source_generation": "1730832728463652",
    "archive_path": "mmcif_files/mmcif_files.tar.gz",
    "size_bytes": 45,
    "sha256": "85cea451eec057fa7e734548ca3ba6d779ed5836a3f9de14b8394575ef0d7d8e",
    "preserved_relative_path": ".installation/ancillary/mmcif_files.tar.gz",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    pending = path.with_suffix(path.suffix + ".tmp")
    with pending.open("w") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_all(output, chunk):
    """FileIO may return a short write, including on an otherwise healthy disk."""
    remaining = memoryview(chunk)
    while remaining:
        written = output.write(remaining)
        if written is None or written <= 0 or written > len(remaining):
            raise OSError("File write made no valid progress")
        remaining = remaining[written:]


def file_hashes(path):
    before = stat_record(path)
    sha, md5, crc, size = hashlib.sha256(), hashlib.md5(), google_crc32c.Checksum(), 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK):
            sha.update(chunk)
            md5.update(chunk)
            crc.update(chunk)
            size += len(chunk)
    require(before == stat_record(path), "File changed while being hashed")
    return sha, md5, crc, size


class ZstdFrameSource:
    """Validate frame/block/footer boundaries while the bounded decoder reads.

    stream_reader can return EOF after a truncated checksum without raising.
    This parser checks framing; libzstd still validates compressed data and the
    checksum. The pinned sources each declare one complete, non-skippable frame.
    https://github.com/facebook/zstd/blob/dev/doc/zstd_compression_format.md
    """

    def __init__(self, source):
        self.source, self.buffer, self.state = source, bytearray(), "header"
        self.remaining, self.total, self.last, self.checksum = 0, 0, False, False
        self.sha, self.md5, self.crc = hashlib.sha256(), hashlib.md5(), google_crc32c.Checksum()

    def read(self, size):
        data = self.source.read(size)
        self.total += len(data)
        self.sha.update(data)
        self.md5.update(data)
        self.crc.update(data)
        self.buffer.extend(data)
        offset = 0
        while True:
            available = len(self.buffer) - offset
            if self.state == "header":
                if available < 5:
                    break
                prefix = bytes(self.buffer[offset:offset + 18])
                require(prefix[:4] == b"\x28\xb5\x2f\xfd", "Unexpected zstd frame magic")
                header_size = zstandard.frame_header_size(prefix)
                if available < header_size:
                    break
                self.checksum = bool(prefix[4] & 4)
                offset += header_size
                self.state = "block"
            elif self.state == "block":
                if available < 3:
                    break
                header = int.from_bytes(self.buffer[offset:offset + 3], "little")
                offset += 3
                self.last, kind, size = bool(header & 1), (header >> 1) & 3, header >> 3
                require(kind != 3 and size <= 128 * 1024, "Invalid zstd block header")
                self.remaining = 1 if kind == 1 else size
                self.state = "payload"
            elif self.state == "payload":
                consumed = min(available, self.remaining)
                offset += consumed
                self.remaining -= consumed
                if self.remaining:
                    break
                self.state = ("checksum" if self.checksum else "finished") if self.last else "block"
            elif self.state == "checksum":
                if available < 4:
                    break
                offset += 4
                self.state = "finished"
            else:
                require(available == 0, "Unexpected trailing zstd data or concatenated frame")
                break
        del self.buffer[:offset]
        if not data:
            require(self.state == "finished" and not self.buffer, "Truncated zstd frame or footer")
        return data


class CheckedZstdReader:
    def __init__(self, source, item, download_sha256):
        self.source = ZstdFrameSource(source)
        self.source_path = Path(source.name)
        self.before = stat_record(self.source_path)
        self.hashes, self.expected_sha = item["hashes"], download_sha256
        self.expected_compressed = item["compressed_bytes"]
        self.expected_expanded = item["zstd_first_frame"]["frame_content_size_bytes"]
        self.total = 0
        self.reader = zstandard.ZstdDecompressor(max_window_size=32 * 1024 * 1024).stream_reader(
            self.source, closefd=False)

    def read(self, size):
        require(size >= 0, "Unbounded decompression is not supported")
        if size == 0:
            return b""
        data = self.reader.read(size)
        self.total += len(data)
        require(self.total <= self.expected_expanded, "Expanded data exceeds the declared frame size")
        if not data:
            require(not self.source.read(1), "Unexpected trailing compressed bytes")
            require(self.source.total == self.expected_compressed, "Compressed source size mismatch")
            require(self.total == self.expected_expanded, "Expanded source size mismatch")
            require(self.source.sha.hexdigest() == self.expected_sha, "Compressed source SHA256 changed")
            require(base64.b64encode(self.source.crc.digest()).decode() == self.hashes["crc32c"],
                    "Compressed source CRC32C mismatch")
            require(base64.b64encode(self.source.md5.digest()).decode() == self.hashes["md5"],
                    "Compressed source MD5 mismatch")
            require(stat_record(self.source_path) == self.before, "Compressed source changed during decode")
        return data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.reader.close()


class FastaValidator:
    """Validate/count complete lines in C; retain only partial-line state.

    Header substitution and byte translations operate on a bounded input chunk.
    No record, complete header, or sequence is accumulated across chunks.
    """

    # A literal newline prefix lets the regex engine skip sequence bytes using
    # its fast prefix search; a multiline ^ anchor scans them one at a time.
    # The trailing newline is deliberately not consumed, so adjacent headers
    # still match independently and empty records remain detectable.
    # Only nonempty headers match. Empty headers remain as stray '>' markers
    # and fail the marker-count check, avoiding a second header regex scan.
    _HEADERS = re.compile(rb"\n>[ \t\r\v\f]*[^ \t\r\v\f\n][^\n]*")
    _SYMBOLS = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz*.-"
    _WHITESPACE = b" \t\r\n"

    def __init__(self):
        self.records, self.symbols = 0, 0
        self.line_start, self.in_header = True, False
        self.header_nonempty, self.sequence_nonempty = False, False

    def _complete_lines(self, data):
        require(self.records > 0, "FASTA sequence before a header")
        normalized, count = self._HEADERS.subn(b">", data)
        compact = normalized.translate(None, self._WHITESPACE)
        # Only markers emitted by header substitution may remain. A '>' inside
        # a sequence line is invalid, even though markers are allowed below.
        require(compact.count(b">") == count, "Invalid character in FASTA sequence")
        require(not compact.translate(None, self._SYMBOLS + b">"), "Invalid character in FASTA sequence")
        require(b">>" not in compact, "Empty FASTA sequence record")
        self.symbols += len(compact) - count
        if count:
            first, last = compact.find(b">"), compact.rfind(b">")
            require(self.records == 0 or self.sequence_nonempty or first > 0,
                    "Empty FASTA sequence record")
            self.records += count
            self.header_nonempty = True
            self.sequence_nonempty = last < len(compact) - 1
        else:
            self.sequence_nonempty |= bool(compact)
        self.line_start, self.in_header = True, False

    def _partial_line(self, part, *, ends_line):
        if self.line_start:
            self.in_header = part.startswith(b">")
            if self.in_header:
                require(self.records == 0 or self.sequence_nonempty, "Empty FASTA sequence record")
                self.records += 1
                self.header_nonempty, self.sequence_nonempty = False, False
                part = part[1:]
            else:
                require(self.records > 0, "FASTA sequence before a header")
        if self.in_header:
            self.header_nonempty |= bool(part.strip())
        else:
            require(not part.translate(None, self._SYMBOLS + b" \t\r"),
                    "Invalid character in FASTA sequence")
            length = len(part.translate(None, b" \t\r"))
            self.symbols += length
            self.sequence_nonempty |= length > 0
        self.line_start = ends_line
        if ends_line and self.in_header:
            require(self.header_nonempty, "Empty FASTA header")

    def feed(self, chunk):
        if not chunk:
            return
        require(b"\x00" not in chunk, "NUL in FASTA data")
        start = 0
        if not self.line_start:
            end = chunk.find(b"\n")
            if end < 0:
                self._partial_line(chunk, ends_line=False)
                return
            self._partial_line(chunk[:end], ends_line=True)
            start = end + 1
        if start == len(chunk):
            return
        # Handle at most one initial header directly. Every other header has a
        # literal newline prefix; this avoids copying the entire chunk merely
        # to prepend a sentinel newline for the regex.
        if chunk[start] == ord(">"):
            end = chunk.find(b"\n", start)
            if end < 0:
                self._partial_line(chunk[start:], ends_line=False)
                return
            self._partial_line(chunk[start:end], ends_line=True)
            start = end + 1
        if start == len(chunk):
            return
        end = chunk.rfind(b"\n", start)
        if end >= start:
            self._complete_lines(memoryview(chunk)[start:end + 1])
        tail_start = max(start, end + 1)
        if tail_start < len(chunk):
            self._partial_line(chunk[tail_start:], ends_line=False)

    def finish(self):
        require(self.records > 0 and self.header_nonempty and self.sequence_nonempty,
                "Empty or incomplete FASTA record")


class Installer:
    def __init__(self, root, plan):
        self.root, self.plan = root, plan
        self.download_dir = root / ".downloads"
        self.work = root / ".installation"
        self.download_dir.mkdir(exist_ok=True)
        self.work.mkdir(exist_ok=True)
        self.guard = threading.RLock()
        self.state = {"started_at": now(), "status": "running", "root": str(root),
                      "components": {}, "total_compressed_bytes": plan["total_compressed_bytes"]}
        self.done = threading.Event()

    def update(self, name, **fields):
        with self.guard:
            self.state["components"].setdefault(name, {}).update(fields)

    def monitor(self):
        while not self.done.wait(15):
            self.publish()

    def publish(self):
        with self.guard:
            self.state["updated_at"] = now()
            atomic_json(self.work / "status.json", self.state)
            print(json.dumps({"at": self.state["updated_at"], "status": self.state["status"],
                              "components": self.state["components"]}), flush=True)

    def submit_expansion(self, executor, item, download):
        future = executor.submit(self.expand, item, download)
        name = item["object"]

        def finished(completed):
            if completed.cancelled():
                error = "CancelledError: Database expansion was cancelled"
            elif (exc := completed.exception()) is not None:
                error = f"{type(exc).__name__}: {exc}"
            else:
                return
            # Downloads may still be running when an expansion fails. Publish
            # the component failure now; run() collects final errors later.
            self.update(name, stage="failed", error=error)
            self.publish()

        future.add_done_callback(finished)
        return future

    def download(self, item):
        name, size = item["object"], item["compressed_bytes"]
        require(item["url"] == SOURCE + name and PurePosixPath(name).name == name,
                "Unexpected database source URL or filename")
        final = self.download_dir / name
        partial = self.download_dir / (name + ".partial")
        receipt_path = self.download_dir / (name + ".receipt.json")
        if final.exists() and receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if self.download_receipt_matches(item, receipt, final):
                self.update(name, stage="download_verified", downloaded_bytes=size)
                return receipt
        if final.exists():
            # Recover a crash after archive rename but before receipt publication.
            # A mismatching archive is preserved and rejected, never overwritten.
            sha, md5, crc, offset = file_hashes(final)
            receipt = self.finish_download(item, final, sha, md5, crc, offset)
            atomic_json(receipt_path, receipt)
            self.update(name, stage="download_verified", downloaded_bytes=size)
            return receipt
        sha, md5, crc = hashlib.sha256(), hashlib.md5(), google_crc32c.Checksum()
        offset = 0
        if partial.exists():
            self.update(name, stage="rehashing_partial", downloaded_bytes=partial.stat().st_size)
            sha, md5, crc, offset = file_hashes(partial)
        require(offset <= size, "Partial archive exceeds the pinned source size")
        self.update(name, stage="downloading", downloaded_bytes=offset, expected_bytes=size)
        with httpx.Client(timeout=httpx.Timeout(90, connect=30), follow_redirects=False,
                          headers={"Accept-Encoding": "identity"}) as client:
            for attempt in range(8):
                if offset == size:
                    break
                try:
                    headers = {"Range": f"bytes={offset}-"} if offset else {}
                    with client.stream("GET", item["url"], params={"generation": item["generation"]},
                                       headers=headers) as response:
                        response.raise_for_status()
                        require(response.headers.get("x-goog-generation") == item["generation"],
                                "Source generation changed")
                        require(response.headers.get("content-encoding", "identity") == "identity",
                                "Unexpected content encoding")
                        if offset:
                            require(response.status_code == 206, "Server did not honor resume Range")
                            require(response.headers.get("content-range") == f"bytes {offset}-{size - 1}/{size}",
                                    "Resume Content-Range does not match the pinned source")
                        else:
                            require(response.status_code == 200, "Unexpected initial download response")
                        with partial.open("ab", buffering=0) as output:
                            for chunk in response.iter_raw(chunk_size=CHUNK):
                                if offset + len(chunk) > size:
                                    raise ValueError("Download exceeds pinned object length")
                                if shutil.disk_usage(self.root).free < RESERVE:
                                    raise OSError("Database volume reserve reached")
                                write_all(output, chunk)
                                sha.update(chunk)
                                md5.update(chunk)
                                crc.update(chunk)
                                offset += len(chunk)
                                self.update(name, downloaded_bytes=offset)
                            os.fsync(output.fileno())
                    if offset != size:
                        raise httpx.ReadError("Truncated object")
                except (httpx.HTTPError, OSError) as exc:
                    self.update(name, retry=attempt + 1, last_error=type(exc).__name__)
                    if attempt == 7:
                        raise
                    # An OSError can occur after some bytes were written. Rebuild
                    # both offset and hashes from disk, never append using stale
                    # in-memory progress after an interrupted write.
                    if partial.exists():
                        self.update(name, stage="rehashing_partial")
                        sha, md5, crc, offset = file_hashes(partial)
                        require(offset <= size, "Partial archive exceeds the pinned source size")
                    time.sleep(min(2**attempt, 30))
        receipt = self.finish_download(item, partial, sha, md5, crc, offset)
        partial.replace(final)
        receipt["stat"] = stat_record(final)
        atomic_json(receipt_path, receipt)
        self.update(name, stage="download_verified", downloaded_bytes=size)
        return receipt

    @staticmethod
    def download_receipt_matches(item, receipt, path):
        return (
            receipt.get("url") == item["url"]
            and receipt.get("generation") == item["generation"]
            and receipt.get("size_bytes") == item["compressed_bytes"]
            and receipt.get("stat") == stat_record(path)
            and receipt["stat"]["size"] == item["compressed_bytes"]
            and receipt.get("crc32c_verified") is True and receipt.get("md5_verified") is True
            and receipt.get("crc32c") == item["hashes"]["crc32c"]
            and receipt.get("md5") == item["hashes"]["md5"]
            and re.fullmatch(r"[0-9a-f]{64}", receipt.get("sha256", "")) is not None
        )

    @staticmethod
    def finish_download(item, path, sha, md5, crc, size):
        crc_value, md5_value = base64.b64encode(crc.digest()).decode(), base64.b64encode(md5.digest()).decode()
        require(size == path.stat().st_size == item["compressed_bytes"], "Downloaded file size mismatch")
        require(crc_value == item["hashes"]["crc32c"], "Downloaded CRC32C mismatch")
        require(md5_value == item["hashes"]["md5"], "Downloaded MD5 mismatch")
        return {"url": item["url"], "generation": item["generation"], "size_bytes": size,
                "sha256": sha.hexdigest(), "crc32c": crc_value, "md5": md5_value,
                "crc32c_verified": True, "md5_verified": True, "verified_at": now(),
                "stat": stat_record(path)}

    def expand(self, item, download):
        name = item["object"]
        archive_path = self.download_dir / name
        require(self.download_receipt_matches(item, download, archive_path),
                "Compressed archive no longer matches its verified receipt")
        receipt_path = self.work / (name + ".expanded.json")
        expanded_path = self.root / ("mmcif_files" if name.endswith(".tar.zst") else name[:-4])
        # A process can stop after publishing data but before its receipt. Such
        # files are preserved and compared against the complete verified source.
        revalidate_existing = expanded_path.exists()
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            path = self.root / receipt["relative_path"]
            if path.exists() and receipt["stat"] == stat_record(path):
                require(receipt["download"]["sha256"] == download["sha256"], "Expanded source changed")
                if receipt.get("kind") == "mmcif_tree":
                    self.verify_inventory(receipt)
                if receipt.get("validation_version", 1) >= VALIDATION_VERSION:
                    self.update(name, stage="installed", expanded_bytes=receipt["size_bytes"])
                    return receipt
                # Upgrade old receipts by comparing decoded source bytes to the
                # existing files. No additional database copy or network GET.
                revalidate_existing = True
        self.update(name, stage="expanding", expanded_bytes=0)
        with archive_path.open("rb") as source, CheckedZstdReader(source, item, download["sha256"]) as stream:
            if name.endswith(".tar.zst"):
                result = self.expand_tar(item, stream, revalidate_existing=revalidate_existing)
            else:
                result = self.expand_fasta(item, stream, revalidate_existing=revalidate_existing)
        result.update(object=name, download=download, verified_at=now(), zstd_decode_verified=True,
                      validation_version=VALIDATION_VERSION, zstd_frame_eof_verified=True,
                      source_full_hashes_reverified_during_decode=True)
        atomic_json(receipt_path, result)
        self.update(name, stage="installed", expanded_bytes=result["size_bytes"],
                    record_count=result["record_count"])
        return result

    def preserve_ancillary(self, item, member, archive):
        expected = PDB_ANCILLARY
        require(item["url"] == expected["source_url"]
                and item["generation"] == expected["source_generation"]
                and member.name == expected["archive_path"]
                and member.type == tarfile.REGTYPE
                and member.size == expected["size_bytes"],
                "Unrecognized PDB ancillary source or member")
        source = archive.extractfile(member)
        require(source is not None, "Missing PDB ancillary body")
        with source:
            body = source.read(expected["size_bytes"] + 1)
        require(len(body) == expected["size_bytes"]
                and hashlib.sha256(body).hexdigest() == expected["sha256"],
                "PDB ancillary content mismatch")
        path = self.root / expected["preserved_relative_path"]
        path.parent.mkdir(exist_ok=True)
        require(path.parent.is_dir() and not path.parent.is_symlink(), "Unsafe ancillary directory")
        require(not path.is_symlink(), "Unsafe ancillary file")
        if path.exists():
            require(path.is_file() and path.stat().st_size == len(body)
                    and path.read_bytes() == body, "Preserved ancillary file changed")
        else:
            pending = path.with_suffix(path.suffix + ".part")
            if pending.exists():
                require(pending.is_file() and not pending.is_symlink(), "Unsafe ancillary partial")
                pending.unlink()
            with pending.open("xb") as output:
                write_all(output, body)
                output.flush()
                os.fsync(output.fileno())
            pending.replace(path)
        return dict(expected, stat=stat_record(path),
                    reason="Verified non-CIF member preserved without nested extraction")

    def verify_ancillary(self, receipt):
        excluded = receipt.get("excluded_ancillary_members", [])
        download = receipt["download"]
        official = (download["url"] == PDB_ANCILLARY["source_url"]
                    and download["generation"] == PDB_ANCILLARY["source_generation"])
        require(len(excluded) == (1 if official else 0), "Unexpected PDB ancillary inventory")
        for entry in excluded:
            require(all(entry.get(key) == value for key, value in PDB_ANCILLARY.items()),
                    "PDB ancillary receipt identity mismatch")
            path = self.root / entry["preserved_relative_path"]
            require(path.is_file() and not path.is_symlink() and not path.parent.is_symlink()
                    and path.stat().st_size == entry["size_bytes"]
                    and stat_record(path) == entry["stat"], "Preserved ancillary file changed")
            require(hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"],
                    "Preserved ancillary hash mismatch")

    def verify_inventory(self, receipt):
        self.verify_ancillary(receipt)
        inventory_info = receipt["inventory"]
        inventory = self.root / inventory_info["relative_path"]
        require(inventory.is_file() and stat_record(inventory) == inventory_info["stat"],
                "PDB inventory changed")
        sha, count, seen = hashlib.sha256(), 0, set()
        with inventory.open("rb") as source:
            for line in source:
                sha.update(line)
                row = json.loads(line)
                rel = PurePosixPath(row["path"])
                require(len(rel.parts) == 2 and rel.parts[0] == "mmcif_files"
                        and rel.name.endswith(".cif") and ".." not in rel.parts,
                        "Invalid PDB inventory path")
                require(rel.name not in seen, "Duplicate PDB inventory entry")
                seen.add(rel.name)
                path = self.root / row["path"]
                require(path.is_file() and not path.is_symlink(), "PDB file missing or replaced")
                require(path.stat().st_size == row["size_bytes"], "PDB file size changed")
                if "stat" in row:
                    require(stat_record(path) == row["stat"], "PDB file changed")
                # Legacy inventories are subsequently checked byte-for-byte
                # against their fully verified source during the upgrade.
                count += 1
        require(sha.hexdigest() == inventory_info["sha256"] == receipt["sha256"],
                "PDB inventory hash mismatch")
        require(count == receipt["record_count"]
                and seen == {path.name for path in (self.root / "mmcif_files").iterdir()},
                "PDB inventory and installed tree differ")

    def expand_fasta(self, item, stream, *, revalidate_existing=False):
        name = item["object"][:-4]
        final, pending = self.root / name, self.work / (name + ".expanding")
        if final.exists() and not revalidate_existing:
            raise ValueError(f"Existing expanded file has no matching completion receipt: {name}")
        sha, total, validator = hashlib.sha256(), 0, FastaValidator()
        before = stat_record(final) if revalidate_existing else None
        with (final.open("rb") if revalidate_existing else pending.open("wb")) as output:
            while chunk := stream.read(CHUNK):
                if total == 0:
                    require(chunk.startswith(b">"), "FASTA does not start with a header")
                validator.feed(chunk)
                total += len(chunk)
                if shutil.disk_usage(self.root).free < RESERVE:
                    raise OSError("Database volume reserve reached")
                sha.update(chunk)
                if revalidate_existing:
                    require(output.read(len(chunk)) == chunk, "Existing FASTA differs from verified source")
                else:
                    write_all(output, chunk)
                self.update(item["object"], expanded_bytes=total)
            if revalidate_existing:
                require(not output.read(1) and stat_record(final) == before, "Existing FASTA changed")
            else:
                output.flush()
                os.fsync(output.fileno())
        validator.finish()
        require(total == item["zstd_first_frame"]["frame_content_size_bytes"], "FASTA size mismatch")
        if not revalidate_existing:
            require(pending.stat().st_size == total, "Expanded FASTA write size mismatch")
            pending.replace(final)
        return {"kind": "fasta", "relative_path": name, "size_bytes": total,
                "sha256": sha.hexdigest(), "record_count": validator.records,
                "sequence_symbol_count": validator.symbols, "stat": stat_record(final)}

    def expand_tar(self, item, stream, *, revalidate_existing=False):
        final = self.root / "mmcif_files"
        pending = self.work / "pdb-tree.expanding"
        if final.exists() and not revalidate_existing:
            raise ValueError("Existing PDB tree has no matching completion receipt")
        # Only this installer's disposable partial extraction is replaced.
        if pending.exists() and not revalidate_existing:
            shutil.rmtree(pending)
        if not revalidate_existing:
            pending.mkdir()
        inventory_pending = self.work / "pdb_inventory.jsonl.expanding"
        inventory_sha, count, total = hashlib.sha256(), 0, 0
        seen, excluded = set(), []
        with inventory_pending.open("wb") as inventory, tarfile.open(fileobj=stream, mode="r|", bufsize=CHUNK) as archive:
            for member in archive:
                rel = PurePosixPath(member.name)
                if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] != "mmcif_files":
                    raise ValueError("Unexpected or unsafe PDB archive path")
                if member.isdir():
                    require(len(rel.parts) == 1, "Unexpected nested PDB directory")
                    continue
                if member.name == PDB_ANCILLARY["archive_path"]:
                    require(not excluded, "Duplicate PDB ancillary member")
                    excluded.append(self.preserve_ancillary(item, member, archive))
                    continue
                if not member.isfile() or len(rel.parts) != 2 or not rel.name.endswith(".cif"):
                    raise ValueError(f"Unexpected PDB archive member type: {member.name!r} ({member.type!r})")
                if str(rel) in seen or member.size <= 0:
                    raise ValueError("Duplicate or empty PDB member")
                seen.add(str(rel))
                target = (final if revalidate_existing else pending) / rel.name
                sha, written = hashlib.sha256(), 0
                body = archive.extractfile(member)
                require(body is not None, "Missing PDB archive member body")
                before = stat_record(target) if revalidate_existing else None
                with body, target.open("rb" if revalidate_existing else "xb") as output:
                    while chunk := body.read(CHUNK):
                        if written == 0:
                            require(chunk.lstrip().startswith(b"data_"), "PDB member is not mmCIF")
                        sha.update(chunk)
                        if revalidate_existing:
                            require(output.read(len(chunk)) == chunk, "Existing PDB differs from source")
                        else:
                            write_all(output, chunk)
                        written += len(chunk)
                        total += len(chunk)
                    if revalidate_existing:
                        require(not output.read(1) and stat_record(target) == before, "Existing PDB changed")
                require(written == member.size == target.stat().st_size, "PDB member write size mismatch")
                row = json.dumps({"path": str(rel), "size_bytes": written, "sha256": sha.hexdigest(),
                                  "stat": stat_record(target)},
                                 sort_keys=True, separators=(",", ":")).encode() + b"\n"
                inventory.write(row)
                inventory_sha.update(row)
                count += 1
                if count % 100 == 0:
                    if shutil.disk_usage(self.root).free < RESERVE:
                        raise OSError("Database volume reserve reached")
                    self.update(item["object"], expanded_bytes=total, record_count=count)
            # Consume the frame footer/checksum and any tar padding as well.
            while stream.read(CHUNK):
                pass
            inventory.flush()
            os.fsync(inventory.fileno())
        require(count > MIN_PDB_FILES, "PDB archive contains too few structures")
        official_ancillary = (item["url"] == PDB_ANCILLARY["source_url"]
                              and item["generation"] == PDB_ANCILLARY["source_generation"])
        require(len(excluded) == (1 if official_ancillary else 0), "Unexpected PDB ancillary inventory")
        if revalidate_existing:
            require({str(PurePosixPath("mmcif_files") / p.name) for p in final.iterdir()} == seen,
                    "Existing PDB tree has unexpected entries")
        else:
            pending.replace(final)
        inventory_path = self.root / "pdb_inventory.jsonl"
        inventory_pending.replace(inventory_path)
        return {"kind": "mmcif_tree", "relative_path": "mmcif_files", "size_bytes": total,
                "sha256": inventory_sha.hexdigest(), "record_count": count, "stat": stat_record(final),
                "excluded_ancillary_members": excluded,
                "inventory": {"relative_path": inventory_path.name, "sha256": inventory_sha.hexdigest(),
                              "stat": stat_record(inventory_path)}}

    def run(self):
        thread = threading.Thread(target=self.monitor, daemon=True)
        thread.start()
        expansions, results, errors = {}, [], []
        # Validate small components early while beginning the two largest reads.
        priority = {"pdb_seqres_2022_09_28.fasta.zst": 0, "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta.zst": 1,
                    "mgy_clusters_2022_05.fa.zst": 2, "pdb_2022_09_28_mmcif_files.tar.zst": 3}
        items = sorted(self.plan["objects"], key=lambda x: priority.get(x["object"], 4))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as downloads, \
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as unpack:
            futures = {downloads.submit(self.download, item): item for item in items}
            for future in concurrent.futures.as_completed(futures):
                item = futures[future]
                try:
                    expansions[self.submit_expansion(unpack, item, future.result())] = item
                except Exception as exc:
                    errors.append({"object": item["object"], "error": f"{type(exc).__name__}: {exc}"})
                    self.update(item["object"], stage="failed", error=errors[-1]["error"])
            for future in concurrent.futures.as_completed(expansions):
                item = expansions[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    errors.append({"object": item["object"], "error": f"{type(exc).__name__}: {exc}"})
                    self.update(item["object"], stage="failed", error=errors[-1]["error"])
        if errors or len(results) != 9:
            self.state.update(status="failed", errors=errors)
        else:
            manifest = {"schema_version": 1, "status": "complete", "source_tag": self.plan["source_tag"],
                        "source_commit": self.plan["source_commit"], "source_manifest_url": self.plan["source_manifest_url"],
                        "installed_at": now(), "components": sorted(results, key=lambda x: x["object"])}
            atomic_json(self.root / "official_databases_manifest.json", manifest)
            self.state["status"] = "complete"
        self.done.set()
        thread.join(timeout=2)
        self.publish()
        if self.state["status"] != "complete":
            raise RuntimeError("Some database components failed; inspect status.json and resume")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("docs/af3-msa-db-plan.json"))
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    plan = json.loads(args.plan.read_text())
    require(plan["object_count"] == len(plan["objects"]) == 9
            and len({item["object"] for item in plan["objects"]}) == 9,
            "The installation plan must contain nine distinct database objects")
    with (root / ".installation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Installer(root, plan).run()


if __name__ == "__main__":
    main()
