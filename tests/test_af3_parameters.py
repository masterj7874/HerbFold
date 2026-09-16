import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from herbfold import af3_parameters as parameters
from herbfold import file_identity


def record(scope, name, dtype, shape, payload):
    texts = [x.encode() for x in (scope, name, dtype)]
    return (
        struct.pack("<5i", *(len(x) for x in texts), len(shape), len(payload))
        + b"".join(texts)
        + struct.pack(f"<{len(shape)}i", *shape)
        + payload
    )


def container(*, zero=False):
    return record("__meta__", "__identifier__", "uint8", (64,), bytes(64) if zero else b"q" * 64) + record(
        "diffuser/example", "weights", "float32", (2,), struct.pack("<2f", 0.2, -0.3)
    )


def config(directory, python=sys.executable):
    return SimpleNamespace(model_dir=directory, python_bin=python)


def test_zero_identifier_is_rejected_without_exposing_values(tmp_path):
    (tmp_path / "af3.bin").write_bytes(container(zero=True))
    result = parameters.inspect_parameters(config(tmp_path))
    assert result["status"] == "test_parameters"
    assert not result["runnable"]
    assert "all-zero" in result["blockers"][0]
    assert "identifier_bytes" not in json.dumps(result)
    assert not result["provenance"]["trained_parameters_authenticated"]


def test_nonzero_identifier_does_not_claim_authentication_or_full_validation(tmp_path):
    (tmp_path / "af3.bin").write_bytes(container())
    result = parameters.inspect_parameters(config(tmp_path))
    assert result["runnable"]
    assert result["status"] == "unverified_parameters"
    assert result["provenance"]["identifier_status"] == "nonzero_unverified"
    assert not result["provenance"]["trained_parameters_authenticated"]
    assert not result["provenance"]["full_container_validated"]
    assert "q" * 64 not in json.dumps(result)


@pytest.mark.parametrize("payload", [b"", b"broken", container()[:-2], record("x", "w", "float32", (3,), b"1234")])
def test_empty_malformed_and_truncated_containers_fail_closed(tmp_path, payload):
    (tmp_path / "af3.bin").write_bytes(payload)
    result = parameters.inspect_parameters(config(tmp_path))
    assert not result["runnable"]
    assert result["status"] == "invalid_parameters"


def test_missing_identifier_and_metadata_only_are_rejected(tmp_path):
    p = tmp_path / "af3.bin"
    p.write_bytes(record("x", "w", "float32", (1,), b"1234"))
    assert not parameters.inspect_parameters(config(tmp_path))["runnable"]
    p.write_bytes(record("__meta__", "__identifier__", "uint8", (64,), b"q" * 64))
    assert not parameters.inspect_parameters(config(tmp_path))["runnable"]


@pytest.mark.parametrize("names", [("af3.0.bin", "af3.1.bin"), ("af3.bin].0", "af3.bin].1")])
def test_split_binary_records_cross_file_boundaries(tmp_path, names):
    data = container()
    for name, part in zip(names, (data[:37], data[37:]), strict=True):
        (tmp_path / name).write_bytes(part)
    result = parameters.inspect_parameters(config(tmp_path))
    assert result["runnable"]
    assert len(result["provenance"]["parameter_files"]) == 2


def test_documented_split_suffix_cannot_claim_pinned_runner_compatibility(tmp_path):
    data = container()
    (tmp_path / "af3.bin.0").write_bytes(data[:37])
    (tmp_path / "af3.bin.1").write_bytes(data[37:])
    result = parameters.inspect_parameters(config(tmp_path))
    assert not result["runnable"]
    assert result["status"] == "unsupported_parameters"
    assert ".N.bin" in result["blockers"][0]


def test_missing_and_ambiguous_parameter_models(tmp_path):
    assert parameters.inspect_parameters(config(tmp_path))["status"] == "missing_parameters"
    (tmp_path / "first.bin").write_bytes(container())
    (tmp_path / "second.bin").write_bytes(container())
    assert parameters.inspect_parameters(config(tmp_path))["status"] == "ambiguous_parameters"


def test_cache_reuses_unchanged_file_and_invalidates_replacement(tmp_path, monkeypatch):
    path = tmp_path / "af3.bin"
    path.write_bytes(container())
    original = parameters._run_probe
    calls = []

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(parameters, "_run_probe", counted)
    first = parameters.inspect_parameters(config(tmp_path))
    first["provenance"]["mutated_by_caller"] = True
    assert "mutated_by_caller" not in parameters.inspect_parameters(config(tmp_path))["provenance"]
    assert len(calls) == 1
    replacement = tmp_path / "replacement"
    replacement.write_bytes(container(zero=True))
    replacement.replace(path)
    assert parameters.inspect_parameters(config(tmp_path))["status"] == "test_parameters"
    assert len(calls) == 2


def test_uuid_keeps_parameter_fingerprint_and_probe_cache_stable_after_device_renumbering(tmp_path, monkeypatch):
    path = tmp_path / "af3.bin"
    path.write_bytes(container())
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: "same-filesystem-uuid")
    original_probe, original_stat = parameters._run_probe, Path.stat
    calls = []

    def counted(*args):
        calls.append(args)
        return original_probe(*args)

    class RenumberedStat:
        def __init__(self, value):
            self.value = value
            self.st_dev = value.st_dev + 1

        def __getattr__(self, attribute):
            return getattr(self.value, attribute)

    def renumbered_stat(path, *, follow_symlinks=True):
        return RenumberedStat(original_stat(path, follow_symlinks=follow_symlinks))

    monkeypatch.setattr(parameters, "_run_probe", counted)
    before = parameters.inspect_parameters(config(tmp_path))
    assert before["runnable"]
    monkeypatch.setattr(Path, "stat", renumbered_stat)
    assert parameters.inspect_parameters(config(tmp_path)) == before
    assert len(calls) == 1
    assert before["provenance"]["parameter_files"] == [{"name": "af3.bin", "size_bytes": len(container())}]


def test_changed_filesystem_uuid_invalidates_parameter_fingerprint_and_probe_cache(tmp_path, monkeypatch):
    (tmp_path / "af3.bin").write_bytes(container())
    original_probe, calls = parameters._run_probe, []

    def counted(*args):
        calls.append(args)
        return original_probe(*args)

    monkeypatch.setattr(parameters, "_run_probe", counted)
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: "original-uuid")
    before = parameters.inspect_parameters(config(tmp_path))
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: "replacement-uuid")
    after = parameters.inspect_parameters(config(tmp_path))
    assert before["runnable"] and after["runnable"]
    assert before["provenance"]["stat_fingerprint_sha256"] != after["provenance"]["stat_fingerprint_sha256"]
    assert len(calls) == 2


def test_same_size_write_with_restored_mtime_invalidates_uuid_parameter_cache(tmp_path, monkeypatch):
    path = tmp_path / "af3.bin"
    path.write_bytes(container())
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: "same-filesystem-uuid")
    original_probe, calls = parameters._run_probe, []

    def counted(*args):
        calls.append(args)
        return original_probe(*args)

    monkeypatch.setattr(parameters, "_run_probe", counted)
    before = parameters.inspect_parameters(config(tmp_path))
    initial = path.stat()
    path.write_bytes(container(zero=True))
    os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
    after = parameters.inspect_parameters(config(tmp_path))
    assert before["runnable"]
    assert after["status"] == "test_parameters" and not after["runnable"]
    assert before["provenance"]["stat_fingerprint_sha256"] != after["provenance"]["stat_fingerprint_sha256"]
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["filesystem", "content"])
def test_changes_during_parameter_probe_fail_closed_with_uuid_identity(tmp_path, monkeypatch, change):
    path = tmp_path / "af3.bin"
    path.write_bytes(container())
    filesystem = ["original-uuid"]
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: filesystem[0])
    original_probe = parameters._run_probe

    def changed_during_probe(*args):
        result = original_probe(*args)
        if change == "filesystem":
            filesystem[0] = "different-uuid"
        else:
            initial = path.stat()
            path.write_bytes(container(zero=True))
            os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        return result

    monkeypatch.setattr(parameters, "_run_probe", changed_during_probe)
    result = parameters.inspect_parameters(config(tmp_path))
    assert result["status"] == "inspection_unavailable" and not result["runnable"]
    assert "changed during inspection" in result["blockers"][0]


def test_probe_isolated_cpu_environment_and_timeout(tmp_path, monkeypatch):
    (tmp_path / "af3.bin").write_bytes(container())

    def timeout(argv, **kwargs):
        assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == ""
        assert kwargs["env"]["JAX_PLATFORMS"] == "cpu"
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert kwargs["timeout"] <= 15
        assert not kwargs["shell"]
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    result = parameters.inspect_parameters(config(tmp_path))
    assert result["status"] == "inspection_limit_reached"
    assert not result["runnable"]


def test_excessive_declared_tensor_stops_at_bounded_prefix(tmp_path):
    huge = record("large", "weights", "float32", (10_000_000,), b"1234")
    # Shape/length consistent, but payload is far beyond the bounded scanner.
    header = struct.pack("<5i", 5, 7, 7, 1, 40_000_000)
    (tmp_path / "af3.bin").write_bytes(header + huge[20:])
    result = parameters.inspect_parameters(config(tmp_path))
    assert not result["runnable"]
    assert result["status"] == "inspection_limit_reached"


@pytest.mark.parametrize("split", [False, True])
def test_actual_zstandard_container_and_split_compressed_frame(tmp_path, split):
    python = Path(__file__).resolve().parents[1] / "external/alphafold3/.venv/bin/python"
    compressor = shutil.which("zstd")
    if not python.is_file() or not compressor:
        pytest.skip("Optional local AF3 interpreter/zstd CLI unavailable")
    compressed = subprocess.run(
        [compressor, "-q", "-c"], input=container(zero=True), capture_output=True, check=True
    ).stdout
    if split:
        (tmp_path / "af3.bin.zst.0").write_bytes(compressed[:17])
        (tmp_path / "af3.bin.zst.1").write_bytes(compressed[17:])
    else:
        (tmp_path / "af3.bin.zst").write_bytes(compressed)
    result = parameters.inspect_parameters(config(tmp_path, str(python)))
    assert result["status"] == "test_parameters"
    assert result["provenance"]["compressed"]


def test_zstandard_backend_enforces_window_in_bytes(tmp_path):
    python = Path(__file__).resolve().parents[1] / "external/alphafold3/.venv/bin/python"
    if not python.is_file():
        pytest.skip("Optional local AF3 interpreter unavailable")
    # Streaming compression advertises a 64 MiB window even for this tiny
    # payload. A KiB interpretation of the configured limit would accept it.
    script = (
        "import sys,zstandard as z; "
        "p=z.ZstdCompressionParameters.from_level(3,window_log=26,write_content_size=0); "
        "c=z.ZstdCompressor(compression_params=p).compressobj(); "
        "b=c.compress(sys.stdin.buffer.read())+c.flush(); "
        "assert z.get_frame_parameters(b).window_size==64*1024*1024; "
        "sys.stdout.buffer.write(b)"
    )
    compressed = subprocess.run(
        [str(python), "-I", "-c", script], input=container(), capture_output=True, check=True
    ).stdout
    (tmp_path / "af3.bin.zst").write_bytes(compressed)
    result = parameters.inspect_parameters(config(tmp_path, str(python)))
    assert not result["runnable"]
    assert result["status"] == "invalid_parameters"
