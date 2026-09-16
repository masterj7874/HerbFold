"""Versioned public bulk sources; streaming downloads with integrity receipts.

Dataset releases are pinned for reproducibility. Register a new reviewed release
instead of silently changing the meaning of an existing source identifier.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import zipfile
import zlib
from pathlib import Path

import httpx

from .storage import utcnow

SOURCES = [
    {
        "id": "coconut-2026-09",
        "name": "COCONUT · 2026.09",
        "automated": True,
        "url": "https://coconut.naturalproducts.net/download",
        "license": "CC0 (release); upstream collection notices retained",
        "license_url": "https://coconut.naturalproducts.net/download",
        "description": "천연물 구조·물성·생물종·문헌·원본 컬렉션을 포함한 전체 CSV. 모든 항목이 한약재에서 확인된 성분인 것은 아닙니다.",
        "download_url": "https://coconut.s3.uni-jena.de/prod/downloads/2026-09/coconut_csv-09-2026.zip",
        "filename": "coconut_csv-09-2026.zip",
        "format": "csv",
        "kind": "natural_product",
    },
    {
        "id": "lotus-2026-04",
        "name": "LOTUS · 2026.04",
        "automated": True,
        "url": "https://zenodo.org/records/19360665",
        "license": "CC BY 4.0 (Zenodo release)",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "description": "천연물–생물종–논문 연결과 분자 구조·분류·DOI/PMID. 같은 분자의 여러 발견 기록을 각각 보존합니다.",
        "download_url": "https://zenodo.org/api/records/19360665/files/260413_frozen_metadata.csv.gz/content",
        "filename": "260413_frozen_metadata.csv.gz",
        "format": "csv",
        "kind": "natural_product",
        "md5": "b17048b3b77daae9ab1e480b6591aabd",
    },
    {
        "id": "chembl-approved",
        "name": "ChEMBL · 승인 이력 약물",
        "automated": True,
        "url": "https://www.ebi.ac.uk/chembl/",
        "license": "CC BY-SA 3.0",
        "license_url": "https://chembl.gitbook.io/chembl-interface-documentation/about",
        "description": "max_phase=4의 구조·승인 이력·동의어·물성. 철회 이력도 보존하며 현재 판매 허가를 단정하지 않습니다.",
        "kind": "drug",
    },
    {
        "id": "imppat",
        "name": "IMPPAT 2.0",
        "automated": False,
        "url": "https://cb.imsc.res.in/imppat/",
        "license": "CC BY-NC 4.0",
        "license_url": "https://cb.imsc.res.in/imppat/",
        "description": "약용식물·사용 부위·성분·전통적 용도. 비상업적 이용 조건이 있어 별도 자료 연결 대상으로 표시합니다.",
        "kind": "reference",
    },
    {
        "id": "kiom-oasis",
        "name": "한국한의학연구원 OASIS",
        "automated": False,
        "url": "https://oasis.kiom.re.kr/",
        "license": "기관 제공·재이용 조건 확인 필요",
        "license_url": "https://oasis.kiom.re.kr/",
        "description": "국내 약재백과·기원종·약재 규격의 확인 출처. 공개 대량 배포 파일이 확인되지 않아 수집 완료로 표시하지 않습니다.",
        "kind": "reference",
    },
    {
        "id": "pubchem",
        "name": "PubChem",
        "automated": False,
        "url": "https://pubchem.ncbi.nlm.nih.gov/",
        "license": "Source-specific terms",
        "license_url": "https://pubchem.ncbi.nlm.nih.gov/docs/usage-guidelines",
        "description": "기존 분자 가져오기에서 CID·입체 구조를 조회합니다. 수백만 건은 PUG REST 반복 호출 대신 공식 bulk 파일을 사용합니다.",
        "kind": "reference",
    },
]


def source_by_id(source_id: str) -> dict:
    for item in SOURCES:
        if item["id"] == source_id:
            return dict(item)
    raise ValueError("Unknown data source")


def file_digest(path: Path, algorithm="sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_source(source: dict, directory: Path, progress=None, stopped=None) -> tuple[Path, dict]:
    """Fetch one fixed public release, caching verified complete files only."""
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / source["filename"]
    receipt_path = destination.with_suffix(destination.suffix + ".manifest.json")
    max_bytes = 2_000_000_000
    if destination.exists():
        if destination.stat().st_size > max_bytes:
            raise ValueError("Cached source exceeds the 2 GB download budget")
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if receipt and (
            receipt.get("source_id") != source["id"] or receipt.get("url") != source["download_url"]
        ):
            raise ValueError("Cached source receipt does not match this pinned release")
        if not receipt.get("sha256") and not source.get("md5"):
            raise ValueError(
                "Cached source has no verified receipt or publisher checksum; remove it and retry"
            )
        digest = file_digest(destination)
        if receipt.get("sha256") and receipt["sha256"] != digest:
            raise ValueError("Cached source checksum mismatch; remove the corrupt local download and retry")
        if source.get("md5") and file_digest(destination, "md5") != source["md5"]:
            raise ValueError("Downloaded release does not match the publisher checksum")
        receipt.update(
            file=str(destination),
            url=source["download_url"],
            sha256=digest,
            bytes=destination.stat().st_size,
            checked_at=utcnow(),
            source_id=source["id"],
        )
        receipt_path.write_text(json.dumps(receipt, indent=2))
        return destination, receipt
    if shutil.disk_usage(directory).free < max_bytes * 2:
        raise ValueError("At least 4 GB free space is required for this source download")
    temporary = destination.with_suffix(destination.suffix + ".part")
    last_error = None
    for attempt in range(4):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(90, connect=30),
                follow_redirects=True,
                headers={
                    "User-Agent": "HerbFold/0.2 natural-product research bulk importer",
                    "Accept-Encoding": "identity",
                },
            ) as client:
                with client.stream("GET", source["download_url"]) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length", "0"))
                    if total > max_bytes:
                        raise ValueError("Source exceeds the 2 GB download budget")
                    count = 0
                    with temporary.open("wb") as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            if stopped and stopped():
                                raise InterruptedError("Import interrupted before completion")
                            count += len(chunk)
                            if count > max_bytes:
                                raise ValueError("Source exceeds the 2 GB download budget")
                            output.write(chunk)
                            if progress:
                                progress({"downloaded_bytes": count, "total_bytes": total})
                    if total and count != total:
                        raise ValueError("Incomplete source download")
            if source.get("md5") and file_digest(temporary, "md5") != source["md5"]:
                raise ValueError("Downloaded release does not match the publisher checksum")
            temporary.replace(destination)
            receipt = {
                "file": str(destination),
                "url": source["download_url"],
                "source_id": source["id"],
                "bytes": destination.stat().st_size,
                "sha256": file_digest(destination),
                "retrieved_at": utcnow(),
                "license": source["license"],
            }
            receipt_path.write_text(json.dumps(receipt, indent=2))
            return destination, receipt
        except httpx.HTTPError as error:
            last_error = error
            if attempt < 3:
                time.sleep(min(2**attempt, 8))
    raise ValueError(f"Public source download failed: {type(last_error).__name__}; retry later")


def extract_csv(path: Path) -> Path:
    """Extract only a single regular CSV member, never trusting archive paths."""
    if path.suffix != ".zip":
        return path
    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if item.filename.lower().endswith(".csv")]
        if len(members) != 1 or members[0].file_size > 8_000_000_000:
            raise ValueError("Expected one CSV member of at most 8 GB")
        member = members[0]
        destination = path.parent / Path(member.filename).name
        # Cache is accepted only after checking the archive CRC on initial extraction.
        marker = destination.with_suffix(".csv.extracted.json")
        if destination.exists() and marker.exists():
            metadata = json.loads(marker.read_text())
            if destination.stat().st_size == member.file_size and metadata.get("crc") == member.CRC:
                # The receipt authenticates the expected archive CRC, not the
                # current extracted bytes. Detect same-size edits/bit corruption.
                checksum = 0
                with destination.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        checksum = zlib.crc32(chunk, checksum)
                if checksum & 0xFFFFFFFF == member.CRC:
                    return destination
        if shutil.disk_usage(path.parent).free < member.file_size + 1_000_000_000:
            raise ValueError("Insufficient space to extract this release")
        temporary = destination.with_suffix(".csv.part")
        with archive.open(member) as source, temporary.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        temporary.replace(destination)
        marker.write_text(json.dumps({"crc": member.CRC, "bytes": member.file_size}))
        return destination


def chembl_drug_records(directory: Path, max_records: int | None = None, progress=None, stopped=None):
    """Page through approved-history references; keep each original response."""
    directory.mkdir(parents=True, exist_ok=True)
    offset, emitted = 0, 0
    with httpx.Client(timeout=90, headers={"User-Agent": "HerbFold/0.2 research-workstation"}) as client:
        while max_records is None or emitted < max_records:
            if stopped and stopped():
                raise InterruptedError("Import interrupted before completion")
            url = "https://www.ebi.ac.uk/chembl/api/data/molecule.json"
            params = {"max_phase": 4, "limit": 500, "offset": offset}
            for attempt in range(4):
                response = client.get(url, params=params)
                if response.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                    response.raise_for_status()
                    break
                time.sleep(2**attempt)
            payload = response.json()
            (directory / f"approved-{offset:07d}.json").write_text(
                json.dumps(
                    {"source_url": str(response.url), "retrieved_at": utcnow(), "data": payload},
                    ensure_ascii=False,
                )
            )
            rows = payload["molecules"]
            if not rows:
                break
            for row in rows:
                if max_records is not None and emitted >= max_records:
                    return
                properties = row.get("molecule_properties") or {}
                structure = row.get("molecule_structures") or {}
                yield {
                    **row,
                    "id": row["molecule_chembl_id"],
                    "name": row.get("pref_name"),
                    "smiles": structure.get("canonical_smiles", ""),
                    "formula": properties.get("full_molformula", ""),
                    "category": "drug",
                    "record_url": f"https://www.ebi.ac.uk/chembl/compound_report_card/{row['molecule_chembl_id']}/",
                    "source_status": "approved_history_not_current_market_status",
                }
                emitted += 1
            offset += len(rows)
            if progress:
                progress(
                    {"remote_total": payload.get("page_meta", {}).get("total_count"), "fetched": emitted}
                )
            if not payload.get("page_meta", {}).get("next"):
                break
            time.sleep(1)
