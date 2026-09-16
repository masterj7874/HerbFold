"""Filesystem UUID snapshots survive numbering changes without weakening guards."""

import stat
from types import SimpleNamespace

import pytest

from herbfold import file_identity as identity


class DeviceLink:
    def __init__(self, name, device, *, block=True):
        self.name = name
        self.device = device
        self.block = block
        self.exists = True
        self.stat_calls = 0

    def stat(self):
        self.stat_calls += 1
        if not self.exists:
            raise FileNotFoundError(self.name)
        return SimpleNamespace(st_mode=stat.S_IFBLK if self.block else stat.S_IFREG, st_rdev=self.device)


class UuidDirectory:
    def __init__(self, *entries):
        self.entries = list(entries)
        self.generation = 1
        self.scans = 0

    def stat(self):
        return SimpleNamespace(
            st_mode=stat.S_IFDIR, st_dev=1, st_ino=2,
            st_mtime_ns=self.generation, st_ctime_ns=self.generation,
        )

    def iterdir(self):
        self.scans += 1
        return iter(self.entries)


class File:
    def __init__(self, device):
        self.value = SimpleNamespace(
            st_size=30, st_mtime_ns=100, st_ctime_ns=200, st_ino=300, st_dev=device,
        )

    def stat(self):
        return self.value


@pytest.fixture(autouse=True)
def clear_uuid_cache():
    identity._find_uuid.cache_clear()
    yield
    identity._find_uuid.cache_clear()


def test_uuid_survives_device_renumbering_and_avoids_repeated_directory_scans(monkeypatch):
    link = DeviceLink("same-filesystem-uuid", 123)
    directory = UuidDirectory(link)
    monkeypatch.setattr(identity, "UUID_DIRECTORY", directory)
    path = File(123)
    expected = identity.stat_record(path)
    assert expected == {
        "size": 30, "mtime_ns": 100, "ctime_ns": 200, "inode": 300,
        "filesystem_uuid": "same-filesystem-uuid",
    }
    for _ in range(20):
        assert identity.stat_record(path) == expected
    assert directory.scans == 1
    assert link.stat_calls >= 21  # A cached match still checks the block device.

    path.value.st_dev = link.device = 456
    assert identity.stat_record(path) == expected
    assert directory.scans == 2


def test_cached_uuid_link_cannot_identify_a_different_device(monkeypatch):
    link = DeviceLink("old-uuid", 123)
    directory = UuidDirectory(link)
    monkeypatch.setattr(identity, "UUID_DIRECTORY", directory)
    path = File(123)
    old = identity.stat_record(path)
    link.device = 456  # Same directory metadata; a hotplug changed the target.
    actual = identity.stat_record(path)
    assert actual != old
    assert actual["device"] == 123
    assert "filesystem_uuid" not in actual


def test_disappearing_cached_link_is_replaced_only_by_current_device_uuid(monkeypatch):
    old_link = DeviceLink("old-uuid", 123)
    directory = UuidDirectory(old_link)
    monkeypatch.setattr(identity, "UUID_DIRECTORY", directory)
    path = File(123)
    old = identity.stat_record(path)
    old_link.exists = False
    directory.entries = [DeviceLink("new-uuid", 123)]
    actual = identity.stat_record(path)
    assert actual != old
    assert actual["filesystem_uuid"] == "new-uuid"


def test_directory_change_invalidates_cached_uuid_and_cached_miss(monkeypatch):
    directory = UuidDirectory()
    monkeypatch.setattr(identity, "UUID_DIRECTORY", directory)
    path = File(123)
    assert identity.stat_record(path)["device"] == 123
    directory.entries.append(DeviceLink("new-uuid", 123))
    directory.generation += 1
    assert identity.stat_record(path)["filesystem_uuid"] == "new-uuid"
    directory.entries = [DeviceLink("replacement-uuid", 123)]
    directory.generation += 1
    assert identity.stat_record(path)["filesystem_uuid"] == "replacement-uuid"


@pytest.mark.parametrize("entries", [[], [DeviceLink("not-a-device", 123, block=False)],
                                     [DeviceLink("one", 123), DeviceLink("ambiguous", 123)]])
def test_unavailable_or_ambiguous_uuid_uses_conservative_device_identity(monkeypatch, entries):
    monkeypatch.setattr(identity, "UUID_DIRECTORY", UuidDirectory(*entries))
    assert identity.stat_record(File(123))["device"] == 123


def test_missing_uuid_directory_falls_back_on_non_linux_hosts(monkeypatch, tmp_path):
    monkeypatch.setattr(identity, "UUID_DIRECTORY", tmp_path / "no-device-uuid-directory")
    assert identity.stat_record(File(123))["device"] == 123


@pytest.mark.parametrize("method", ["stat", "iterdir"])
def test_uuid_lookup_permissions_do_not_make_files_unreadable(monkeypatch, method):
    directory = UuidDirectory(DeviceLink("uuid", 123))

    def denied():
        raise PermissionError("restricted device directory")

    monkeypatch.setattr(directory, method, denied)
    monkeypatch.setattr(identity, "UUID_DIRECTORY", directory)
    assert identity.stat_record(File(123))["device"] == 123


@pytest.mark.parametrize("attribute", ["st_size", "st_mtime_ns", "st_ctime_ns", "st_ino"])
def test_uuid_identity_keeps_all_existing_file_metadata_guards(monkeypatch, attribute):
    monkeypatch.setattr(identity, "UUID_DIRECTORY", UuidDirectory(DeviceLink("uuid", 123)))
    path = File(123)
    before = identity.stat_record(path)
    setattr(path.value, attribute, getattr(path.value, attribute) + 1)
    assert identity.stat_record(path) != before


def test_fallback_device_number_changes_remain_detectable(monkeypatch, tmp_path):
    monkeypatch.setattr(identity, "UUID_DIRECTORY", tmp_path / "missing")
    path = File(123)
    before = identity.stat_record(path)
    path.value.st_dev = 456
    assert identity.stat_record(path) != before
