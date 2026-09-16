"""File snapshots with a stable filesystem identity when Linux exposes one.

Block-device numbers can change after a reboot or a disk reconnect. The UUID
links maintained by udev identify the filesystem instead. Each cached lookup
still verifies that its block device backs the file being inspected; metadata
and inode checks remain unchanged. Unavailable or ambiguous UUIDs fall back to
the conservative device-number snapshot used by older installation receipts.
"""

from __future__ import annotations

import stat
from functools import lru_cache
from pathlib import Path

UUID_DIRECTORY = Path("/dev/disk/by-uuid")


def _block_device_number(path: Path) -> int | None:
    try:
        value = path.stat()
    except OSError:
        return None
    return value.st_rdev if stat.S_ISBLK(value.st_mode) else None


@lru_cache(maxsize=64)
def _find_uuid(
    directory: Path, directory_signature: tuple[int, ...], device: int
) -> Path | None:
    """Cache directory scans, including misses, until its metadata changes."""
    del directory_signature  # Included in the cache key to notice udev updates.
    candidates = [path for path in directory.iterdir() if _block_device_number(path) == device]
    return candidates[0] if len(candidates) == 1 else None


def _filesystem_uuid(device: int) -> str | None:
    try:
        value = UUID_DIRECTORY.stat()
        if not stat.S_ISDIR(value.st_mode):
            return None
        signature = (value.st_dev, value.st_ino, value.st_mtime_ns, value.st_ctime_ns)
        candidate = _find_uuid(UUID_DIRECTORY, signature, device)
        if candidate is None:
            return None
        if _block_device_number(candidate) != device:
            # A previously cached link may have disappeared or changed target
            # during a disk reconnect. Never trust the old device-to-UUID map.
            _find_uuid.cache_clear()
            candidate = _find_uuid(UUID_DIRECTORY, signature, device)
            if candidate is None or _block_device_number(candidate) != device:
                return None
        return candidate.name
    except OSError:
        # Containers, non-Linux hosts, and restricted /dev mounts may not expose
        # UUID links. Keeping st_dev in that case preserves the existing guard.
        return None


def stat_record(path: Path) -> dict:
    """Return strict metadata and either a filesystem UUID or a device number.

    The tagged identity deliberately differs from a legacy device-only record.
    Existing receipts require full content revalidation before being migrated.
    """
    value = path.stat()
    result = {
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
        "inode": value.st_ino,
    }
    filesystem_uuid = _filesystem_uuid(value.st_dev)
    if filesystem_uuid is None:
        result["device"] = value.st_dev
    else:
        result["filesystem_uuid"] = filesystem_uuid
    return result
