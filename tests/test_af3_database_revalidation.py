"""Tiny synthetic receipts exercise revalidation without real downloads or DBs."""

import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from herbfold.af3_databases import DATABASE_FILES, MANIFEST_NAME, inspect_databases
from herbfold.alphafold import AF3_COMMIT, AF3_VERSION, AF3Config
from herbfold.file_identity import stat_record

SCRIPT = Path(__file__).parents[1] / "scripts/revalidate_af3_databases.py"
SPEC = importlib.util.spec_from_file_location("database_revalidation_under_test", SCRIPT)
revalidator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(revalidator)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def legacy_stat(path):
    value = path.stat()
    return {"size": value.st_size, "mtime_ns": value.st_mtime_ns,
            "ctime_ns": value.st_ctime_ns, "inode": value.st_ino, "device": -999}


def write_json(path, value):
    path.write_text(json.dumps(value))


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "database"
    root.mkdir()
    for relative in (".downloads", ".installation", "mmcif_files"):
        (root / relative).mkdir()
    ancillary = root / revalidator.ANCILLARY["preserved_relative_path"]
    ancillary.parent.mkdir()
    ancillary.write_bytes(bytes.fromhex(
        "1f8b0800000000000003edc1010d000000c2a0f74f6d0e37a00000000000000000008037039ade1d2700280000"))
    entries = []
    for name in ("1abc", "2def"):
        path = root / "mmcif_files" / (name + ".cif")
        body = f"data_{name}\n# SYNTHETIC TEST ONLY\n".encode()
        path.write_bytes(body)
        entries.append({"path": str(path.relative_to(root)), "size_bytes": len(body),
                        "sha256": sha(body), "stat": legacy_stat(path)})
    inventory = root / "pdb_inventory.jsonl"
    inventory.write_bytes(b"".join((json.dumps(entry) + "\n").encode() for entry in entries))
    components, objects = [], []
    for name in (*DATABASE_FILES, "mmcif_files"):
        tree = name == "mmcif_files"
        if tree:
            object_name = revalidator.PDB_OBJECT
            body = inventory.read_bytes()
            content_size = sum(entry["size_bytes"] for entry in entries)
        else:
            object_name = name + ".zst"
            body = (">SYNTHETIC_TEST_ONLY_" + name + "\nACDE\n").encode()
            (root / name).write_bytes(body)
            content_size = len(body)
        archive_body = b"SYNTHETIC_VERIFIED_ARCHIVE_ONLY:" + object_name.encode()
        archive = root / ".downloads" / object_name
        archive.write_bytes(archive_body)
        generation = revalidator.ANCILLARY["source_generation"] if tree else "synthetic-generation"
        download = {"url": revalidator.SOURCE + object_name, "generation": generation,
                    "size_bytes": len(archive_body), "sha256": sha(archive_body),
                    "crc32c": "synthetic-crc32c", "md5": "synthetic-md5",
                    "crc32c_verified": True, "md5_verified": True,
                    "stat": legacy_stat(archive), "verified_at": "SYNTHETIC_TEST_ONLY"}
        row = {"object": object_name, "kind": "mmcif_tree" if tree else "fasta",
               "relative_path": name, "size_bytes": content_size, "sha256": sha(body),
               "record_count": len(entries) if tree else 1, "stat": legacy_stat(root / name),
               "download": download, "validation_version": 2,
               "zstd_decode_verified": True, "zstd_frame_eof_verified": True}
        if tree:
            row["inventory"] = {"relative_path": inventory.name, "sha256": sha(body),
                                "stat": legacy_stat(inventory)}
            row["excluded_ancillary_members"] = [{**revalidator.ANCILLARY, "stat": legacy_stat(ancillary)}]
        components.append(row)
        objects.append({"object": object_name, "url": download["url"], "generation": generation,
                        "compressed_bytes": len(archive_body),
                        "hashes": {"crc32c": download["crc32c"], "md5": download["md5"]},
                        "zstd_first_frame": {"frame_content_size_bytes": content_size}})
    manifest = {"schema_version": 1, "status": "complete", "source_tag": f"v{AF3_VERSION}",
                "source_commit": AF3_COMMIT, "components": components, "installed_at": "SYNTHETIC_TEST_ONLY"}
    plan = {"object_count": 9, "objects": objects, "source_tag": f"v{AF3_VERSION}", "source_commit": AF3_COMMIT}
    save_receipts(root, manifest)
    return root, manifest, plan


def save_receipts(root, manifest):
    write_json(root / MANIFEST_NAME, manifest)
    for row in manifest["components"]:
        write_json(root / ".installation" / (row["object"] + ".expanded.json"), row)
        write_json(root / ".downloads" / (row["object"] + ".receipt.json"), row["download"])


def receipts(root):
    paths = [root / MANIFEST_NAME, root / "pdb_inventory.jsonl"]
    paths += list((root / ".installation").glob("*.expanded.json"))
    paths += list((root / ".downloads").glob("*.receipt.json"))
    return {str(path.relative_to(root)): path.read_bytes() for path in paths}


def test_full_revalidation_preserves_data_and_backs_up_all_receipts(installation, tmp_path):
    root, original, plan = installation
    old_receipts = receipts(root)
    data_paths = [root / name for name in DATABASE_FILES]
    data_paths += list((root / "mmcif_files").iterdir())
    data_paths += list((root / ".downloads").glob("*.zst"))
    data_paths += [root / revalidator.ANCILLARY["preserved_relative_path"]]
    data = {path: (path.read_bytes(), revalidator.raw_stat(path.stat())) for path in data_paths}
    report_path = tmp_path / "report.json"
    report = revalidator.revalidate(root, plan, workers=4, report_path=report_path)
    assert report["status"] == "complete"
    assert report["files_sha256_verified"] == 8 + 9 + 2 + 1 + 1
    assert report["verified_file_counts"] == {"fasta": 8, "archive": 9, "cif": 2, "inventory": 1, "ancillary": 1}
    assert report["content_modified"] is False
    assert json.loads(report_path.read_text()) == report
    for path, (content, before) in data.items():
        assert path.read_bytes() == content
        assert revalidator.raw_stat(path.stat()) == before
    backup = Path(report["backup_dir"])
    assert all((backup / relative).read_bytes() == content for relative, content in old_receipts.items())
    updated = json.loads((root / MANIFEST_NAME).read_text())
    assert inspect_databases(AF3Config(database_dir=root))["runnable"]
    assert updated["installed_at"] == original["installed_at"]
    assert report["manifest_sha256"] == sha((root / MANIFEST_NAME).read_bytes())
    for row in updated["components"]:
        assert row["stat"] == stat_record(root / row["relative_path"])
        assert row["download"]["stat"] == stat_record(root / ".downloads" / row["object"])
        assert json.loads((root / ".installation" / (row["object"] + ".expanded.json")).read_text()) == row
        assert json.loads((root / ".downloads" / (row["object"] + ".receipt.json")).read_text()) == row["download"]
        assert row["download"]["crc32c_verified"] and row["download"]["md5_verified"]
    tree = next(row for row in updated["components"] if row["kind"] == "mmcif_tree")
    assert tree["sha256"] == tree["inventory"]["sha256"] == sha((root / "pdb_inventory.jsonl").read_bytes())
    for entry in (json.loads(line) for line in (root / "pdb_inventory.jsonl").read_text().splitlines()):
        assert entry["stat"] == stat_record(root / entry["path"])
    assert revalidator.revalidate(root, plan, workers=2)["status"] == "complete"


@pytest.mark.parametrize("relative", [DATABASE_FILES[0], ".downloads/" + DATABASE_FILES[0] + ".zst",
                                      "mmcif_files/1abc.cif", "pdb_inventory.jsonl",
                                      revalidator.ANCILLARY["preserved_relative_path"]])
def test_same_size_content_damage_never_changes_receipts(installation, relative):
    root, _, plan = installation
    path = root / relative
    original = path.read_bytes()
    path.write_bytes(b"X" + original[1:])
    before = receipts(root)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        revalidator.revalidate(root, plan)
    assert receipts(root) == before
    assert not list((root / ".installation").glob("revalidation-20*"))


def test_change_after_hashing_fails_final_metadata_check(installation, monkeypatch):
    root, _, plan = installation
    before = receipts(root)
    original = revalidator.Revalidation.hash_file

    def changed(self, relative, *args, **kwargs):
        result = original(self, relative, *args, **kwargs)
        if relative == DATABASE_FILES[0]:
            path = root / relative
            path.write_bytes(path.read_bytes())
        return result

    monkeypatch.setattr(revalidator.Revalidation, "hash_file", changed)
    with pytest.raises(ValueError, match="changed before receipt publication"):
        revalidator.revalidate(root, plan)
    assert receipts(root) == before


@pytest.mark.parametrize("field", ["generation", "url", "compressed_bytes", "crc32c", "md5"])
def test_pinned_plan_disagreement_is_rejected_before_hashing(installation, field, monkeypatch):
    root, _, plan = installation
    plan = copy.deepcopy(plan)
    if field in ("crc32c", "md5"):
        plan["objects"][0]["hashes"][field] = "other-checksum"
    else:
        plan["objects"][0][field] = 9000 if field == "compressed_bytes" else "other-value"
    before = receipts(root)
    monkeypatch.setattr(revalidator.Revalidation, "hash_file", lambda *args, **kwargs: pytest.fail("Must fail before hashing"))
    with pytest.raises(ValueError, match="pinned plan"):
        revalidator.revalidate(root, plan)
    assert receipts(root) == before


def test_unexpected_cif_entry_is_rejected(installation):
    root, _, plan = installation
    (root / "mmcif_files/unlisted.cif").write_text("data_extra\n")
    before = receipts(root)
    with pytest.raises(ValueError, match="unexpected entries"):
        revalidator.revalidate(root, plan)
    assert receipts(root) == before


def test_symlink_cannot_replace_a_verified_database_file(installation, tmp_path):
    root, _, plan = installation
    path = root / DATABASE_FILES[0]
    external = tmp_path / "external.fasta"
    path.rename(external)
    path.symlink_to(external)
    before = receipts(root)
    with pytest.raises(ValueError, match="unsafe"):
        revalidator.revalidate(root, plan)
    assert receipts(root) == before


def test_report_cannot_overwrite_any_database_input(installation):
    root, _, plan = installation
    before = receipts(root)
    with pytest.raises(ValueError, match="outside"):
        revalidator.revalidate(root, plan, report_path=root / MANIFEST_NAME)
    assert receipts(root) == before


@pytest.mark.parametrize("change", ["same_inode_restored_mtime", "replaced_inode"])
def test_file_mutation_during_hashing_is_detected(installation, monkeypatch, change):
    root, _, plan = installation
    path = root / DATABASE_FILES[0]
    body, prior = path.read_bytes(), path.stat()
    before = receipts(root)
    real_sha256 = hashlib.sha256
    mutated = False

    class Probe:
        def __init__(self, initial=b""):
            self.wrapped = real_sha256(initial)

        def update(self, data):
            nonlocal mutated
            self.wrapped.update(data)
            if data == body and not mutated:
                mutated = True
                if change == "replaced_inode":
                    replacement = path.with_name("replacement.fasta")
                    replacement.write_bytes(body)
                    replacement.replace(path)
                else:
                    path.write_bytes(body)
                    os.utime(path, ns=(prior.st_atime_ns, prior.st_mtime_ns))

        def hexdigest(self):
            return self.wrapped.hexdigest()

    monkeypatch.setattr(revalidator, "hashlib", SimpleNamespace(sha256=Probe))
    with pytest.raises(ValueError, match="changed while hashing"):
        revalidator.revalidate(root, plan, workers=1)
    assert mutated
    assert receipts(root) == before


@pytest.mark.parametrize("change", ["duplicate", "outside_path", "wrong_total", "wrong_count"])
def test_inventory_semantics_are_checked_even_with_matching_digest(installation, change):
    root, manifest, plan = installation
    inventory = root / "pdb_inventory.jsonl"
    entries = [json.loads(line) for line in inventory.read_text().splitlines()]
    tree = next(row for row in manifest["components"] if row["kind"] == "mmcif_tree")
    if change == "duplicate":
        entries.append(entries[0])
    elif change == "outside_path":
        entries[0]["path"] = "../outside.cif"
    elif change == "wrong_total":
        tree["size_bytes"] += 1
    elif change == "wrong_count":
        tree["record_count"] += 1
    inventory.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    tree["sha256"] = tree["inventory"]["sha256"] = sha(inventory.read_bytes())
    tree["inventory"]["stat"] = legacy_stat(inventory)
    save_receipts(root, manifest)
    before = receipts(root)
    with pytest.raises(ValueError, match="Duplicate|Invalid PDB|count or total size"):
        revalidator.revalidate(root, plan)
    assert receipts(root) == before


def test_stale_expanded_receipt_cannot_override_the_manifest(installation):
    root, manifest, plan = installation
    row = copy.deepcopy(manifest["components"][0])
    row["record_count"] += 1
    write_json(root / ".installation" / (row["object"] + ".expanded.json"), row)
    before = receipts(root)
    with pytest.raises(ValueError, match="Expanded receipt differs"):
        revalidator.revalidate(root, plan)
    assert receipts(root) == before


def test_interrupted_publication_keeps_old_manifest_blocked_and_backups(installation, monkeypatch):
    root, _, plan = installation
    before = receipts(root)
    original = revalidator.atomic_json

    def fail_component(path, value):
        if path.name.endswith(".expanded.json"):
            raise OSError("Simulated receipt publication failure")
        original(path, value)

    monkeypatch.setattr(revalidator, "atomic_json", fail_component)
    with pytest.raises(OSError, match="publication failure"):
        revalidator.revalidate(root, plan)
    assert (root / MANIFEST_NAME).read_bytes() == before[MANIFEST_NAME]
    assert not inspect_databases(AF3Config(database_dir=root))["runnable"]
    backup, = (root / ".installation").glob("revalidation-20*")
    assert all((backup / relative).read_bytes() == content for relative, content in before.items())


def test_installer_lock_prevents_concurrent_revalidation(installation):
    root, _, plan = installation
    before = receipts(root)
    with (root / ".installation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            revalidator.revalidate(root, plan)
    assert receipts(root) == before
