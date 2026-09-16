"""Tiny synthetic installer receipts test validation, never substitute real DBs."""

import hashlib
import json
from pathlib import Path

import pytest

from herbfold import af3_databases as databases
from herbfold import file_identity
from herbfold.alphafold import AF3_COMMIT, AF3_VERSION, AF3Config


def installation(root):
    components = []
    for name in databases.DATABASE_FILES:
        path = root / name
        content = b">SYNTHETIC_TEST_ONLY\nACD\n"
        path.write_bytes(content)
        components.append(
            {
                "object": name + ".zst",
                "relative_path": name,
                "kind": "fasta",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "record_count": 1,
                "stat": databases.file_stat(path),
                "download": {"crc32c_verified": True, "md5_verified": True, "sha256": "a" * 64},
            }
        )
    tree = root / "mmcif_files"
    tree.mkdir()
    inventory = root / "pdb_inventory.jsonl"
    inventory.write_text("SYNTHETIC_TEST_ONLY\n")
    components.append(
        {
            "object": "pdb_2022_09_28_mmcif_files.tar.zst",
            "relative_path": "mmcif_files",
            "kind": "mmcif_tree",
            "size_bytes": 42,
            "sha256": "b" * 64,
            "record_count": 1,
            "stat": databases.file_stat(tree),
            "download": {"crc32c_verified": True, "md5_verified": True, "sha256": "c" * 64},
            "inventory": {
                "relative_path": inventory.name,
                "sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
                "stat": databases.file_stat(inventory),
            },
        }
    )
    manifest = {
        "schema_version": 1,
        "status": "complete",
        "source_tag": f"v{AF3_VERSION}",
        "source_commit": AF3_COMMIT,
        "components": components,
        "installed_at": "SYNTHETIC_TEST_ONLY",
    }
    (root / databases.MANIFEST_NAME).write_text(json.dumps(manifest))
    return AF3Config(database_dir=root), manifest


def test_empty_or_partial_directory_does_not_count_as_installed(tmp_path):
    config = AF3Config(database_dir=tmp_path)
    assert databases.inspect_databases(config)["runnable"] is False
    (tmp_path / "uniref90_2022_05.fa.partial").write_text(">unfinished\nACD\n")
    result = databases.inspect_databases(config)
    assert not result["runnable"]
    assert "incomplete or unverified" in result["blockers"][0]


def test_complete_receipt_has_stable_bounded_fingerprint_and_counts(tmp_path):
    config, manifest = installation(tmp_path)
    first = databases.inspect_databases(config)
    assert first["runnable"] and first["status"] == "ready"
    assert len(first["provenance"]["components"]) == 9
    assert first["provenance"]["expanded_size_bytes"] == sum(
        row["size_bytes"] for row in manifest["components"]
    )
    assert first["provenance"]["full_contents_rehashed_this_check"] is False
    assert databases.inspect_databases(config) == first


@pytest.mark.parametrize(
    "change",
    ["partial", "missing", "wrong_version", "unchecked", "missing_rna", "wrong_kind", "empty_records"],
)
def test_incomplete_or_inconsistent_receipt_is_blocked(tmp_path, change):
    config, manifest = installation(tmp_path)
    if change == "partial":
        manifest["status"] = "installing"
    elif change == "missing":
        manifest["components"].pop()
    elif change == "wrong_version":
        manifest["source_commit"] = "0" * 40
    elif change == "unchecked":
        manifest["components"][0]["download"]["crc32c_verified"] = False
    elif change == "missing_rna":
        (tmp_path / "rnacentral_active_seq_id_90_cov_80_linclust.fasta").unlink()
    elif change == "wrong_kind":
        manifest["components"][0]["kind"] = "mmcif_tree"
    else:
        manifest["components"][0]["record_count"] = 0
    (tmp_path / databases.MANIFEST_NAME).write_text(json.dumps(manifest))
    assert not databases.inspect_databases(config)["runnable"]


@pytest.mark.parametrize("relative", [databases.DATABASE_FILES[0], "pdb_inventory.jsonl"])
def test_changed_database_or_inventory_is_not_reused_even_at_same_size(tmp_path, relative):
    config, _ = installation(tmp_path)
    path = tmp_path / relative
    original = path.read_bytes()
    path.write_bytes(b"X" * len(original))
    result = databases.inspect_databases(config)
    assert not result["runnable"]
    assert "changed" in result["blockers"][0]


def test_inventory_cannot_reference_external_path(tmp_path):
    config, manifest = installation(tmp_path)
    manifest["components"][-1]["inventory"]["relative_path"] = "../private"
    (tmp_path / databases.MANIFEST_NAME).write_text(json.dumps(manifest))
    assert not databases.inspect_databases(config)["runnable"]


def test_legacy_device_receipt_requires_revalidation_before_uuid_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: None)
    config, _ = installation(tmp_path)
    assert databases.inspect_databases(config)["runnable"]
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: "filesystem-uuid")
    result = databases.inspect_databases(config)
    assert not result["runnable"]
    assert "changed after installation validation" in result["blockers"][0]


def test_uuid_receipt_rejects_a_different_filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: "original-uuid")
    config, _ = installation(tmp_path)
    assert databases.inspect_databases(config)["runnable"]
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: "different-uuid")
    assert not databases.inspect_databases(config)["runnable"]


def test_uuid_receipt_and_readiness_fingerprint_survive_device_renumbering(tmp_path, monkeypatch):
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: "filesystem-uuid")
    config, _ = installation(tmp_path)
    before = databases.inspect_databases(config)
    original_stat = Path.stat

    class RenumberedStat:
        def __init__(self, value):
            self.value = value
            self.st_dev = value.st_dev + 1

        def __getattr__(self, attribute):
            return getattr(self.value, attribute)

    def renumbered_stat(path, *, follow_symlinks=True):
        return RenumberedStat(original_stat(path, follow_symlinks=follow_symlinks))

    monkeypatch.setattr(Path, "stat", renumbered_stat)
    assert databases.inspect_databases(config) == before


@pytest.mark.parametrize("relative", [databases.DATABASE_FILES[0], "pdb_inventory.jsonl"])
def test_uuid_receipt_still_rejects_same_size_content_changes(tmp_path, monkeypatch, relative):
    monkeypatch.setattr(file_identity, "_filesystem_uuid", lambda device: "filesystem-uuid")
    config, _ = installation(tmp_path)
    path = tmp_path / relative
    path.write_bytes(b"X" * path.stat().st_size)
    assert not databases.inspect_databases(config)["runnable"]
