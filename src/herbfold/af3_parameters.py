"""Bounded, CPU-only inspection of AF3 parameter containers.

This rejects the all-zero identifier used by upstream's random-parameter
benchmark recipe. A nonzero identifier is NOT authentication of Google-issued
trained parameters. Only a bounded prefix is checked, never all weights.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

MAX_FILES = 1024
MAX_RECORDS = 512
MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_DECODED_BYTES = 32 * 1024 * 1024
# zstandard 0.25.0 cext/cffi pass this to ZSTD_DCtx_setMaxWindowSize in
# BYTES. Its Python API documentation incorrectly describes KiB. A real
# oversized-window regression below guards the installed backend behavior.
MAX_WINDOW_BYTES = 32 * 1024 * 1024
PROBE_TIMEOUT_SECONDS = 15
SOURCE_URL = "https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/model_parameters.md"


class _InspectionError(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def _select_files(directory):
    if not directory or not Path(directory).is_dir():
        raise _InspectionError("missing_parameters", "AF3 model parameter directory is missing")
    entries = []
    for entry in Path(directory).iterdir():
        entries.append(entry)
        if len(entries) > MAX_FILES:
            raise _InspectionError("inspection_limit_reached", "AF3 model directory exceeds inspection limit")
    files = [p for p in entries if p.is_file()]
    # Same precedence and lexicographic segment order as pinned upstream,
    # including its stray ] in the suffix-style uncompressed split regex.
    patterns = (
        (r"(?P<model>.+)\.[0-9]+\.bin\.zst", True),
        (r"(?P<model>.+)\.bin\.zst\.[0-9]+", True),
        (r"(?P<model>.+)\.[0-9]+\.bin", False),
        (r"(?P<model>.+)\.bin\]\.[0-9]+", False),
        (r"(?P<model>.+)\.bin\.zst", True),
        (r"(?P<model>.+)\.bin", False),
    )
    for pattern, compressed in patterns:
        models = {}
        for path in files:
            if match := re.fullmatch(pattern, path.name):
                models.setdefault(match.group("model"), []).append(path)
        if models:
            if len(models) != 1:
                raise _InspectionError("ambiguous_parameters", "Multiple AF3 parameter models match")
            selected = sorted(next(iter(models.values())))
            if any(path.stat().st_size == 0 for path in selected):
                raise _InspectionError("invalid_parameters", "AF3 parameter segment is empty")
            return selected, compressed
    if any(re.fullmatch(r".+\.bin\.[0-9]+", p.name) for p in files):
        raise _InspectionError(
            "unsupported_parameters",
            "AF3 3.0.4 cannot select .bin.N segments because of its filename regex; use .N.bin segments.",
        )
    raise _InspectionError("missing_parameters", "No AF3 .bin or .bin.zst parameter model was found")


class _JoinedFiles(io.RawIOBase):
    """Concatenate split containers without copying them or opening every part."""

    def __init__(self, paths):
        super().__init__()
        self.paths = iter(paths)
        self.handle = None
        self.consumed = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.consumed >= MAX_COMPRESSED_BYTES:
            raise _InspectionError("inspection_limit_reached", "AF3 compressed prefix limit reached")
        while True:
            if self.handle is None:
                path = next(self.paths, None)
                if path is None:
                    return 0
                self.handle = open(path, "rb")
            count = self.handle.readinto(memoryview(buffer)[: MAX_COMPRESSED_BYTES - self.consumed])
            if count:
                self.consumed += count
                return count
            self.handle.close()
            self.handle = None

    def close(self):
        if self.handle is not None:
            self.handle.close()
        super().close()


def _inspect_stream(stream):
    consumed, records, checked_parameters = 0, 0, 0
    identifier = None

    def read_exact(size, *, allow_eof=False):
        nonlocal consumed
        if size < 0 or consumed + size > MAX_DECODED_BYTES:
            raise _InspectionError("inspection_limit_reached", "AF3 decoded prefix limit reached")
        result = bytearray()
        while len(result) < size:
            chunk = stream.read(min(size - len(result), 65536))
            if not chunk:
                if allow_eof and not result:
                    return b""
                raise _InspectionError("invalid_parameters", "AF3 parameter record is truncated")
            result.extend(chunk)
            consumed += len(chunk)
        return bytes(result)

    while records < MAX_RECORDS:
        header = read_exact(20, allow_eof=True)
        if not header:
            break
        scope_size, name_size, dtype_size, dimensions, array_size = struct.unpack("<5i", header)
        if not (
            0 < scope_size <= 4096
            and 0 < name_size <= 256
            and 0 < dtype_size <= 32
            and 0 <= dimensions <= 8
            and 0 < array_size <= 2**31 - 1
        ):
            raise _InspectionError("invalid_parameters", "AF3 parameter record header is invalid")
        try:
            scope = read_exact(scope_size).decode("utf-8")
            name = read_exact(name_size).decode("utf-8")
            dtype = read_exact(dtype_size).decode("ascii")
        except UnicodeError as exc:
            raise _InspectionError("invalid_parameters", "AF3 parameter record text is invalid") from exc
        shape = struct.unpack(f"<{dimensions}i", read_exact(dimensions * 4))
        widths = {"uint8": 1, "float32": 4, "bfloat16": 2}
        if dtype not in widths or any(n <= 0 for n in shape) or math.prod(shape) * widths[dtype] != array_size:
            raise _InspectionError("invalid_parameters", "AF3 parameter shape, dtype or byte length is invalid")
        records += 1
        if scope == "__meta__" and name == "__identifier__":
            if dtype != "uint8" or shape != (64,) or identifier is not None:
                raise _InspectionError("invalid_parameters", "AF3 model identifier has an invalid layout")
            identifier = read_exact(array_size)
            if not any(identifier):
                raise _InspectionError(
                    "test_parameters",
                    "AF3 parameters contain an all-zero model identifier, the upstream random/test-parameter "
                    "marker. Supply verified trained parameters before prediction.",
                )
        else:
            # Discard bounded chunks; never instantiate model tensors or JAX.
            if consumed + array_size > MAX_DECODED_BYTES:
                raise _InspectionError("inspection_limit_reached", "AF3 decoded prefix limit reached")
            remaining = array_size
            while remaining:
                part = min(remaining, 65536)
                read_exact(part)
                remaining -= part
            checked_parameters += scope != "__meta__"
        if identifier is not None and checked_parameters:
            return {
                "status": "unverified_parameters",
                "runnable": True,
                "blockers": [],
                "provenance": {
                    "identifier_status": "nonzero_unverified",
                    "trained_parameters_authenticated": False,
                    "records_checked": records,
                    "decoded_bytes_checked": consumed,
                    "full_container_validated": False,
                    "inspection_scope": "Identifier and at least one parameter record; bounded prefix only",
                    "note": "A nonzero identifier does not prove trained weights, Google origin or usage rights.",
                },
            }
    raise _InspectionError("invalid_parameters", "AF3 model identifier or parameter record is missing")


def _probe_files(paths, compressed):
    with _JoinedFiles(paths) as joined, io.BufferedReader(joined) as buffered:
        if compressed:
            try:
                import zstandard
            except ImportError as exc:
                raise _InspectionError(
                    "inspection_unavailable", "AF3 parameter inspection requires zstandard in AF3_PYTHON"
                ) from exc
            with zstandard.ZstdDecompressor(max_window_size=MAX_WINDOW_BYTES).stream_reader(buffered) as stream:
                return _inspect_stream(stream)
        return _inspect_stream(buffered)


def _failure(status, message):
    return {
        "status": status,
        "runnable": False,
        "blockers": [message],
        "provenance": {"trained_parameters_authenticated": False, "full_container_validated": False},
    }


def _run_probe(files, compressed, python_bin):
    # An isolated interpreter allows using AF3's existing zstandard dependency
    # while avoiding JAX imports, GPU contexts and inherited API credentials.
    executable = shutil.which(python_bin) or (sys.executable if not compressed else None)
    if executable is None:
        return _failure("inspection_unavailable", "AF3_PYTHON is unavailable for parameter inspection")
    env = {
        "PATH": os.defpath,
        "CUDA_VISIBLE_DEVICES": "",
        "JAX_PLATFORMS": "cpu",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    try:
        completed = subprocess.run(
            [executable, "-I", str(Path(__file__).resolve()), "--inspect-parameter-prefix"],
            input=json.dumps({"paths": list(files), "compressed": compressed}),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            env=env,
            check=False,
            shell=False,
        )
        if completed.returncode or len(completed.stdout) > 16000:
            return _failure("inspection_unavailable", "AF3 parameter inspection subprocess failed")
        result = json.loads(completed.stdout)
        if not isinstance(result, dict) or not {"status", "runnable", "blockers", "provenance"} <= result.keys():
            raise ValueError("invalid probe response")
        return result
    except subprocess.TimeoutExpired:
        return _failure("inspection_limit_reached", "AF3 parameter prefix inspection timed out")
    except (OSError, ValueError):
        return _failure("inspection_unavailable", "AF3 parameter inspection could not complete")


@lru_cache(maxsize=64)
def _cached_probe(fingerprint, compressed, python_bin, interpreter_fingerprint):
    del interpreter_fingerprint
    return _run_probe(tuple(row[0] for row in fingerprint), compressed, python_bin)


def _file_identity(path):
    # This module also runs directly under `python -I` as the bounded probe.
    # Only the parent process needs package imports and filesystem identities.
    from .file_identity import stat_record

    value = stat_record(path)
    filesystem = (("filesystem_uuid", value["filesystem_uuid"])
                  if "filesystem_uuid" in value else value["device"])
    return (filesystem, value["inode"], value["size"], value["mtime_ns"], value["ctime_ns"])


def inspect_parameters(config):
    """Return status/runnable/blockers/provenance without model values or ID bytes.

    Successful prefix inspection permits execution but explicitly leaves trained
    model provenance unverified. Missing/malformed/test containers fail closed.
    Cache keys include stable filesystem identity, inode, size, mtime and ctime,
    and the interpreter's matching metadata.
    """
    try:
        files, compressed = _select_files(getattr(config, "model_dir", None))
        fingerprint = []
        for path in files:
            fingerprint.append((str(path.resolve()), *_file_identity(path)))
        fingerprint = tuple(fingerprint)
        python_bin = str(getattr(config, "python_bin", sys.executable))
        executable = shutil.which(python_bin)
        interpreter_fingerprint = _file_identity(Path(executable)) if executable else None
        result = copy.deepcopy(_cached_probe(fingerprint, compressed, python_bin, interpreter_fingerprint))
        # A replacement during the subprocess must never reuse its old verdict.
        for path, row in zip(files, fingerprint, strict=True):
            if _file_identity(path) != row[1:]:
                return _failure("inspection_unavailable", "AF3 parameter files changed during inspection")
        result["provenance"].update(
            compressed=compressed,
            parameter_files=[{"name": path.name, "size_bytes": row[3]} for path, row in zip(files, fingerprint)],
            stat_fingerprint_sha256=hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest(),
            inspection_method="bounded_parameter_prefix_cpu_v1",
            source_url=SOURCE_URL,
        )
        return result
    except _InspectionError as exc:
        return _failure(exc.status, str(exc))
    except OSError:
        return _failure("inspection_unavailable", "AF3 parameter files could not be inspected")


if __name__ == "__main__" and sys.argv[1:] == ["--inspect-parameter-prefix"]:
    try:
        request = json.loads(sys.stdin.read(65536))
        output = _probe_files(request["paths"], request["compressed"])
    except _InspectionError as exc:
        output = _failure(exc.status, str(exc))
    except Exception:
        # Binary/decompression errors must not leak payloads or paths to logs.
        output = _failure("invalid_parameters", "AF3 parameter container could not be decoded")
    print(json.dumps(output))
