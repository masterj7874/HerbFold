"""Immutable, per-workspace UniProt target registration with bounded public reads."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path

import httpx

from .storage import utcnow

# UniProt's documented six/ten-character accession grammar, plus exact isoforms.
ACCESSION_PATTERN = (
    r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?:-[1-9][0-9]{0,5})?"
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_BUDGET_SECONDS = 30
REST_BASE = "https://rest.uniprot.org/uniprotkb/"


class TargetRegistryError(ValueError):
    def __init__(self, message, *, status_code=502, code="uniprot_invalid_response"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def normalize_accession(accession):
    if not isinstance(accession, str) or not re.fullmatch(ACCESSION_PATTERN, accession.strip().upper()):
        raise TargetRegistryError(
            "UniProt 접근번호를 입력하세요. 예: P35354, P23219, P23219-2",
            status_code=422,
            code="invalid_accession",
        )
    return accession.strip().upper()


def _sequence(value):
    if not isinstance(value, str):
        raise TargetRegistryError("UniProt 응답에 단백질 서열이 없습니다.")
    sequence = "".join(value.split()).upper()
    if not sequence or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWYX]+", sequence):
        raise TargetRegistryError(
            "이 표적 서열에는 AF3 입력에서 지원하지 않는 아미노산 문자가 있습니다.",
            status_code=422,
            code="unsupported_sequence",
        )
    if len(sequence) > 10000:
        raise TargetRegistryError(
            "이 표적은 플랫폼 입력 한도인 아미노산 10,000개를 초과합니다.",
            status_code=422,
            code="sequence_too_long",
        )
    return sequence


def _positive_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _text(value, fallback=""):
    return value.strip()[:1000] if isinstance(value, str) and value.strip() else fallback


def _validated_record(record, expected_accession):
    """Validate pinned content again at persistence/read boundaries, never repair it."""
    try:
        if not isinstance(record, dict) or record.get("accession") != expected_accession:
            raise ValueError
        if normalize_accession(expected_accession) != expected_accession:
            raise ValueError
        sequence = record.get("sequence")
        if _sequence(sequence) != sequence:
            raise ValueError
        if type(record.get("length")) is not int or record["length"] != len(sequence):
            raise ValueError
        if record.get("sequence_sha256") != hashlib.sha256(sequence.encode()).hexdigest():
            raise ValueError
        if not isinstance(record.get("name"), str) or not 1 <= len(record["name"].strip()) <= 1000:
            raise ValueError
        if not isinstance(record.get("organism"), str) or len(record["organism"]) > 1000:
            raise ValueError
        gene = record.get("gene")
        if gene is not None and (not isinstance(gene, str) or len(gene) > 1000):
            raise ValueError
        if record.get("source") != f"https://www.uniprot.org/uniprotkb/{expected_accession}/entry":
            raise ValueError
        for key in ("retrieved_at", "registered_at"):
            value = record.get(key)
            if (
                not isinstance(value, str)
                or len(value) > 64
                or datetime.fromisoformat(value).utcoffset() is None
            ):
                raise ValueError
        if record.get("reviewed") is not None and type(record["reviewed"]) is not bool:
            raise ValueError
        for key in ("taxon_id", "sequence_version", "entry_version"):
            if record.get(key) is not None and _positive_int(record[key]) is None:
                raise ValueError
        provenance = record.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("kind") not in {
            "uniprot_rest",
            "bundled_uniprot_snapshot",
        }:
            raise ValueError
        hash_keys = (
            ("source_response_sha256",)
            if provenance["kind"] == "bundled_uniprot_snapshot"
            else ("metadata_response_sha256", "sequence_response_sha256")
        )
        if any(
            not isinstance(provenance.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", provenance[key])
            for key in hash_keys
        ):
            raise ValueError
        return record
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise TargetRegistryError(
            f"등록된 표적 {expected_accession}의 저장 기록을 검증할 수 없습니다. 기존 기록을 덮어쓰지 않았습니다. 저장소를 확인하세요.",
            status_code=409,
            code="registered_target_corrupt",
        ) from exc


def _decode_record(raw, accession):
    try:
        if not isinstance(raw, str) or len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError
        record = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise TargetRegistryError(
            f"등록된 표적 {accession}의 저장 JSON을 읽을 수 없습니다. 기존 기록은 보존되었습니다.",
            status_code=409,
            code="registered_target_corrupt",
        ) from exc
    return _validated_record(record, accession)


def _download(client, accession, suffix, deadline):
    """Never follow server redirects or accept caller-controlled hosts/paths."""
    accession = normalize_accession(accession)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TargetRegistryError(
            "UniProt 조회 시간이 초과되었습니다. 다시 시도하세요.", status_code=504, code="uniprot_timeout"
        )
    url = f"{REST_BASE}{accession}.{suffix}"
    try:
        with client.stream("GET", url, timeout=min(10, remaining)) as response:
            if response.status_code in {404, 410}:
                raise TargetRegistryError(
                    f"UniProt에서 {accession} 표적을 찾을 수 없습니다.",
                    status_code=404,
                    code="target_not_found",
                )
            # UniProt returns the explicit MERGED mapping as JSON with HTTP 303.
            if response.status_code != 200 and not (suffix == "json" and response.status_code == 303):
                raise TargetRegistryError(
                    f"UniProt 조회에 실패했습니다(HTTP {response.status_code}). 잠시 후 다시 시도하세요.",
                    code="uniprot_unavailable",
                )
            body = bytearray()
            # Check every incoming chunk, including trickle responses smaller
            # than a buffering threshold, against the overall request budget.
            for chunk in response.iter_bytes():
                if time.monotonic() >= deadline:
                    raise TargetRegistryError(
                        "UniProt 조회 시간이 초과되었습니다. 다시 시도하세요.",
                        status_code=504,
                        code="uniprot_timeout",
                    )
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise TargetRegistryError("UniProt 응답이 지원하는 다운로드 크기를 초과했습니다.")
            return bytes(body), url
    except httpx.TimeoutException as exc:
        raise TargetRegistryError(
            "UniProt 조회 시간이 초과되었습니다. 다시 시도하세요.", status_code=504, code="uniprot_timeout"
        ) from exc
    except httpx.HTTPError as exc:
        raise TargetRegistryError(
            "UniProt에 연결할 수 없습니다. 잠시 후 다시 시도하세요.", code="uniprot_unavailable"
        ) from exc


def _canonical_metadata(client, requested, deadline):
    current = requested
    visited = set()
    mappings = []
    for _ in range(4):
        if current in visited:
            break
        visited.add(current)
        raw, url = _download(client, current, "json", deadline)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
            primary = normalize_accession(data.get("primaryAccession"))
            if "-" in primary or primary != data["primaryAccession"]:
                raise ValueError
            secondary = data.get("secondaryAccessions", [])
            if not isinstance(secondary, list) or (primary != current and current not in secondary):
                raise ValueError
            if data.get("entryType") == "Inactive":
                reason = data.get("inactiveReason", {})
                targets = reason.get("mergeDemergeTo", [])
                if (
                    reason.get("inactiveReasonType") != "MERGED"
                    or not isinstance(targets, list)
                    or len(targets) != 1
                ):
                    raise TargetRegistryError(
                        f"{requested}는 더 이상 활성 UniProt 표적이 아닙니다. 현재 접근번호를 사용하세요.",
                        status_code=404,
                        code="target_not_found",
                    )
                replacement = normalize_accession(targets[0])
                if "-" in replacement or primary != current:
                    raise ValueError
                mappings.append(
                    {
                        "from": current,
                        "to": replacement,
                        "source": url,
                        "response_sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
                current = replacement
                continue
            if primary != current:
                mappings.append(
                    {
                        "from": current,
                        "to": primary,
                        "source": url,
                        "response_sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
            return data, raw, url, mappings
        except (TypeError, AttributeError, ValueError) as exc:
            if isinstance(exc, TargetRegistryError) and exc.code == "target_not_found":
                raise
            raise TargetRegistryError("UniProt 응답의 접근번호 또는 별칭 매핑을 확인할 수 없습니다.") from exc
    raise TargetRegistryError("UniProt 별칭 매핑이 반복되거나 너무 깁니다. 현재 기본 접근번호를 사용하세요.")


def _fetch_target(accession):
    """Fetch an exact canonical/isoform sequence, preserving its source identity."""
    requested = normalize_accession(accession)
    base, separator, _ = requested.partition("-")
    deadline = time.monotonic() + REQUEST_BUDGET_SECONDS
    with httpx.Client(
        follow_redirects=False,
        headers={"User-Agent": "HerbFold/0.2 research-workstation", "Accept-Encoding": "identity"},
    ) as client:
        data, metadata_raw, metadata_url, mappings = _canonical_metadata(client, base, deadline)
        primary = data["primaryAccession"]
        audit = data.get("entryAudit", {})
        description = data.get("proteinDescription", {})
        recommended = description.get("recommendedName") or next(
            iter(description.get("submissionNames", [])), {}
        )
        name = _text(recommended.get("fullName", {}).get("value"), primary)
        sequence_raw, sequence_url = metadata_raw, metadata_url
        isoform_name = None
        if separator:
            if primary != base:
                raise TargetRegistryError(
                    "별칭의 아이소폼 번호를 자동으로 치환할 수 없습니다. 현재 기본 접근번호의 정확한 아이소폼을 등록하세요.",
                    status_code=422,
                    code="ambiguous_isoform_alias",
                )
            isoforms = [
                isoform
                for comment in data.get("comments", [])
                if comment.get("commentType") == "ALTERNATIVE PRODUCTS"
                for isoform in comment.get("isoforms", [])
                if requested in isoform.get("isoformIds", [])
            ]
            if len(isoforms) != 1:
                raise TargetRegistryError(
                    f"UniProt에서 {requested} 아이소폼의 매핑을 확인할 수 없습니다.",
                    status_code=404,
                    code="target_not_found",
                )
            sequence_raw, sequence_url = _download(client, requested, "fasta", deadline)
            try:
                lines = sequence_raw.decode("utf-8").strip().splitlines()
            except UnicodeError as exc:
                raise TargetRegistryError("UniProt FASTA 응답을 해석할 수 없습니다.") from exc
            if (
                not lines
                or not re.match(rf">(?:sp|tr)\|{re.escape(requested)}\|\S+(?:\s|$)", lines[0])
                or any(line.startswith(">") for line in lines[1:])
            ):
                raise TargetRegistryError(
                    "UniProt FASTA 헤더가 요청한 아이소폼과 일치하지 않습니다. 기본 서열로 대체하지 않았습니다."
                )
            sequence = _sequence("".join(lines[1:]))
            accession = requested
            isoform_name = _text(isoforms[0].get("name", {}).get("value"), requested)
            name = f"{name} (isoform {isoform_name})"[:1000]
            version_match = re.search(r"(?:^|\s)SV=(\d+)(?:\s|$)", lines[0])
            sequence_version = int(version_match.group(1)) if version_match else None
        else:
            accession = primary
            sequence_data = data.get("sequence", {})
            sequence = _sequence(sequence_data.get("value"))
            if sequence_data.get("length") != len(sequence):
                raise TargetRegistryError("UniProt 응답의 서열 길이가 실제 서열과 일치하지 않습니다.")
            sequence_version = _positive_int(audit.get("sequenceVersion"))
        organism = data.get("organism", {})
        genes = data.get("genes", [])
        entry_type = data.get("entryType", "")
        return {
            "accession": accession,
            "name": name,
            "gene": next(
                (
                    _text(gene.get("geneName", {}).get("value"))
                    for gene in genes
                    if _text(gene.get("geneName", {}).get("value"))
                ),
                None,
            ),
            "organism": _text(organism.get("scientificName")),
            "taxon_id": _positive_int(organism.get("taxonId")),
            "length": len(sequence),
            "sequence": sequence,
            "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
            "source": f"https://www.uniprot.org/uniprotkb/{accession}/entry",
            "retrieved_at": utcnow(),
            "reviewed": True
            if entry_type == "UniProtKB reviewed (Swiss-Prot)"
            else False
            if entry_type == "UniProtKB unreviewed (TrEMBL)"
            else None,
            "sequence_version": sequence_version,
            "entry_version": _positive_int(audit.get("entryVersion")),
            "provenance": {
                "kind": "uniprot_rest",
                "requested_accession": requested,
                "metadata_url": metadata_url,
                "metadata_response_sha256": hashlib.sha256(metadata_raw).hexdigest(),
                "sequence_url": sequence_url,
                "sequence_response_sha256": hashlib.sha256(sequence_raw).hexdigest(),
                "alias_mappings": mappings,
                **(
                    {
                        "parent_accession": primary,
                        "isoform_name": isoform_name,
                        "parent_sequence_version": _positive_int(audit.get("sequenceVersion")),
                    }
                    if separator
                    else {}
                ),
            },
        }


def fetch_target(accession):
    try:
        return _fetch_target(accession)
    except TargetRegistryError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
        raise TargetRegistryError("UniProt 표적 응답의 서열 또는 메타데이터를 해석할 수 없습니다.") from exc


class TargetRegistry:
    def __init__(self, store):
        self.store = store
        with store.connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS protein_targets (accession TEXT PRIMARY KEY, record_json TEXT NOT NULL)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS protein_target_aliases (alias TEXT PRIMARY KEY, accession TEXT NOT NULL)"
            )
        self._seed()

    def _seed(self):
        # Existing corrupt data must not prevent application startup or be
        # silently replaced by the seed. Reads return its explicit integrity error.
        with self.store.connect() as con:
            if con.execute("SELECT 1 FROM protein_targets WHERE accession='P35354'").fetchone():
                return
        for root in (Path(__file__).resolve().parents[2] / "data", Path(__file__).resolve().parent / "data"):
            path = root / "ptgs2.json"
            if path.is_file():
                raw = path.read_bytes()
                original = json.loads(raw)
                if original.get("accession") != "P35354":
                    raise TargetRegistryError("기본 표적 파일의 접근번호가 P35354와 다릅니다.")
                sequence = _sequence(original.get("sequence"))
                record = {
                    **original,
                    "sequence": sequence,
                    "length": len(sequence),
                    "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                    "gene": original.get("gene"),
                    "taxon_id": original.get("taxon_id"),
                    "reviewed": original.get("reviewed"),
                    "sequence_version": original.get("sequence_version"),
                    "entry_version": original.get("entry_version"),
                    "provenance": {
                        "kind": "bundled_uniprot_snapshot",
                        "file": "data/ptgs2.json",
                        "source_response_sha256": hashlib.sha256(raw).hexdigest(),
                    },
                }
                self._persist("P35354", record)
                return

    def get(self, accession):
        accession = normalize_accession(accession)
        with self.store.connect() as con:
            row = con.execute(
                "SELECT accession,record_json FROM protein_targets WHERE accession=?", (accession,)
            ).fetchone()
            if row is None:
                row = con.execute(
                    "SELECT p.accession,p.record_json FROM protein_targets p JOIN protein_target_aliases a ON p.accession=a.accession WHERE a.alias=?",
                    (accession,),
                ).fetchone()
        if row is None:
            raise KeyError(accession)
        return _decode_record(row[1], row[0])

    def list(self):
        with self.store.connect() as con:
            rows = con.execute(
                "SELECT accession,record_json FROM protein_targets ORDER BY accession"
            ).fetchall()
        return [_decode_record(row[1], row[0]) for row in rows]

    def _persist(self, requested, record):
        record = {**record, "registered_at": utcnow()}
        primary = record["accession"]
        record = _validated_record(record, primary)
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            conflicting_alias = con.execute(
                "SELECT accession FROM protein_target_aliases WHERE alias=?", (primary,)
            ).fetchone()
            if conflicting_alias and conflicting_alias[0] != primary:
                raise TargetRegistryError(
                    "기존 표적 별칭과 기본 접근번호가 충돌합니다. 기존 등록은 보존되었습니다.",
                    status_code=409,
                    code="alias_conflict",
                )
            existing = con.execute(
                "SELECT record_json FROM protein_targets WHERE accession=?", (primary,)
            ).fetchone()
            if existing is None:
                con.execute(
                    "INSERT INTO protein_targets VALUES (?,?)",
                    (primary, json.dumps(record, ensure_ascii=False, allow_nan=False)),
                )
            else:
                record = _decode_record(existing[0], primary)
            if requested != primary:
                alias = con.execute(
                    "SELECT accession FROM protein_target_aliases WHERE alias=?", (requested,)
                ).fetchone()
                direct = con.execute(
                    "SELECT accession FROM protein_targets WHERE accession=?", (requested,)
                ).fetchone()
                if direct or (alias and alias[0] != primary):
                    raise TargetRegistryError(
                        "기존에 등록된 표적 별칭과 새 UniProt 매핑이 다릅니다. 기존 등록은 보존되었습니다.",
                        status_code=409,
                        code="alias_conflict",
                    )
                con.execute("INSERT OR IGNORE INTO protein_target_aliases VALUES (?,?)", (requested, primary))
        return record

    def register(self, accession):
        accession = normalize_accession(accession)
        try:
            return self.get(accession)
        except KeyError:
            return self._persist(accession, fetch_target(accession))
