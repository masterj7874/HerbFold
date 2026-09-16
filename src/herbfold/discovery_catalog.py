"""Streaming, provenance-preserving natural-product catalog.

The database intentionally stores source assertions rather than labeling every
natural product as a verified herbal ingredient. Import performs structure
identity checks only; affinity, efficacy and costly descriptors are not inferred.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing
from datetime import UTC, datetime
from functools import lru_cache
from itertools import islice
from pathlib import Path
from typing import Any

from rdkit import Chem, rdBase

from .discovery_sources import SOURCES

_MAX_PAGE = 500
_MAX_SMILES = 100_000
_COLUMNS = {
    "smiles": ("canonical_smiles", "isomeric_smiles", "smiles", "structure_smiles", "canonicalsmiles"),
    "external_id": ("identifier", "coconut_id", "coconutid", "compound_id", "id", "accession", "_name"),
    "name": ("name", "preferred_name", "compound_name", "traditional_name", "structure_nametraditional",
             "iupac_name", "structure_nameiupac", "title"),
    "formula": ("molecular_formula", "formula", "molecularformula", "structure_molecular_formula"),
    "organisms": ("organisms", "organism", "taxonomy", "taxa", "species", "organism_name", "biological_source"),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False, sort_keys=True)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value.strip() if isinstance(value, str) else _json(value)


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _value(record: Mapping, kind: str) -> str:
    keys = {_normalise_key(key): val for key, val in record.items()}
    for candidate in _COLUMNS[kind]:
        value = _text(keys.get(candidate))
        if value:
            return value
    return ""


@lru_cache(maxsize=8192)
def _identity(smiles: str) -> tuple[str, str | None]:
    if not smiles or len(smiles) > _MAX_SMILES:
        raise ValueError("Missing SMILES or structure exceeds 100,000 characters")
    with rdBase.BlockLogs():
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None or not molecule.GetNumAtoms():
            raise ValueError("Invalid SMILES")
        if any(atom.GetAtomicNum() == 0 for atom in molecule.GetAtoms()):
            raise ValueError("Wildcard/attachment atoms are not complete molecular structures")
        canonical = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
        # Preserve salts, charge and isotopes. Full InChIKey distinguishes resolved
        # stereoisomers; standard-InChI-equivalent tautomers share an identity.
        try:
            inchikey = Chem.MolToInchiKey(molecule) or None
        except (ValueError, RuntimeError):
            inchikey = None
    return canonical, inchikey


class DiscoveryCatalog:
    """Disk-backed catalog; every public operation owns and closes its connection."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "discovery.sqlite3"
        self._kind_counts: dict[tuple[str, ...], tuple[int, int]] = {}
        with closing(self.connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS discovery_compounds (
                    id INTEGER PRIMARY KEY,
                    canonical_smiles TEXT NOT NULL UNIQUE,
                    inchikey TEXT UNIQUE,
                    display_name TEXT NOT NULL,
                    formula TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discovery_sources (
                    id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    license_label TEXT NOT NULL,
                    first_fetched_at TEXT NOT NULL,
                    last_fetched_at TEXT NOT NULL,
                    record_count INTEGER NOT NULL DEFAULT 0,
                    compound_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS discovery_provenance (
                    id INTEGER PRIMARY KEY,
                    compound_id INTEGER NOT NULL REFERENCES discovery_compounds(id),
                    source_id TEXT NOT NULL REFERENCES discovery_sources(id),
                    external_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    organisms TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    license_label TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    UNIQUE(source_id, external_id, compound_id, record_hash)
                );
                CREATE INDEX IF NOT EXISTS discovery_provenance_compound
                    ON discovery_provenance(compound_id);
                CREATE INDEX IF NOT EXISTS discovery_provenance_source_compound
                    ON discovery_provenance(source_id, compound_id);
                CREATE INDEX IF NOT EXISTS discovery_provenance_external
                    ON discovery_provenance(external_id);
                CREATE TABLE IF NOT EXISTS discovery_imports (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    report_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS discovery_imports_started
                    ON discovery_imports(started_at DESC);
                CREATE TABLE IF NOT EXISTS discovery_stats (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    compound_count INTEGER NOT NULL,
                    provenance_count INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO discovery_stats VALUES(1, 0, 0);
                CREATE VIRTUAL TABLE IF NOT EXISTS discovery_search USING fts5(
                    external_id, name, organisms, inchikey,
                    content='', tokenize='unicode61'
                );
                """
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=60000")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA cache_size=-8192")
        return connection

    def summary(self) -> dict:
        with closing(self.connect()) as connection:
            counts = dict(connection.execute("SELECT compound_count,provenance_count FROM discovery_stats").fetchone())
            sources = [dict(row) for row in connection.execute("SELECT * FROM discovery_sources ORDER BY record_count DESC,id")]
            imports = [json.loads(row[0]) for row in connection.execute(
                "SELECT report_json FROM discovery_imports ORDER BY started_at DESC LIMIT 20"
            )]
        return {
            **counts,
            "sources": sources,
            "imports": imports,
            "database_bytes": self.db.stat().st_size,
            "max_page_size": _MAX_PAGE,
            "identity_policy": "RDKit canonical isomeric SMILES / full standard InChIKey; salts and isotopes retained",
            "evidence_scope": "Source-reported natural products; herbal origin and efficacy are not assumed",
        }

    def list_compounds(
        self, search: str = "", source: str | None = None, limit: int = 50, offset: int = 0,
        kind: str | None = None,
    ) -> dict:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_PAGE:
            raise ValueError(f"limit must be between 1 and {_MAX_PAGE}")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if not isinstance(search, str) or len(search) > 500:
            raise ValueError("search must be a string of at most 500 characters")
        if kind is not None and kind not in ("natural_product", "drug"):
            raise ValueError("kind must be natural_product or drug")
        empty = {"items": [], "total": 0, "limit": limit, "offset": offset}
        source_ids = tuple(row["id"] for row in SOURCES if row["kind"] == kind) if kind else ()
        if source:
            if kind and source not in source_ids:
                return empty
            source_ids = (source,)
        if kind and not source_ids:
            return empty
        placeholders = ",".join("?" for _ in source_ids)
        query = None
        if search.strip():
            tokens = re.findall(r"[^\W_]+", search, flags=re.UNICODE)
            if not tokens:
                return empty
            query = " AND ".join(f'"{token}"*' for token in tokens[:20])

        params: tuple[Any, ...] = ()
        if query:
            source_filter = f" AND p.source_id IN ({placeholders})" if source_ids else ""
            # Keep FTS on the outer side: SQLite otherwise may start with all
            # source records and re-run the MATCH constraint for every row.
            matched = (
                "SELECT p.compound_id FROM discovery_search "
                "CROSS JOIN discovery_provenance p ON p.id=discovery_search.rowid "
                "WHERE discovery_search MATCH ?" + source_filter + " GROUP BY p.compound_id"
            )
            params = (query, *source_ids)
        elif source_ids:
            # Ordered UNION can merge the covering (source_id, compound_id)
            # index streams and stop at the requested page. In particular, a
            # sparse drug source must not scan every natural-product identity.
            matched = " UNION ".join(
                "SELECT DISTINCT compound_id FROM discovery_provenance WHERE source_id=?"
                for _ in source_ids
            )
            params = source_ids
        else:
            matched = "SELECT id AS compound_id FROM discovery_compounds"

        with closing(self.connect()) as connection:
            # Counts, their invalidation key and page data share a read snapshot,
            # including when another process commits an import concurrently.
            connection.execute("BEGIN")
            stats = connection.execute("SELECT * FROM discovery_stats WHERE id=1").fetchone()
            if not query and not source_ids:
                total = stats["compound_count"]
            elif not query and len(source_ids) == 1:
                row = connection.execute(
                    "SELECT compound_count FROM discovery_sources WHERE id=?", source_ids,
                ).fetchone()
                total = row[0] if row else 0
            else:
                cached = self._kind_counts.get(source_ids) if not query else None
                if cached and cached[0] == stats["provenance_count"]:
                    total = cached[1]
                else:
                    total = connection.execute("SELECT COUNT(*) FROM (" + matched + ")", params).fetchone()[0]
                    if not query:
                        self._kind_counts[source_ids] = (stats["provenance_count"], total)
            rows = connection.execute(
                "SELECT c.* FROM (" + matched + " ORDER BY compound_id LIMIT ? OFFSET ?) selected "
                "JOIN discovery_compounds c ON c.id=selected.compound_id ORDER BY c.id",
                (*params, limit, offset),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["smiles"] = item["canonical_smiles"]
                # Source classes must include evidence beyond the ten-row
                # preview. Indexed existence checks also bound work for an
                # identity with thousands of occurrences in a single source.
                item["source_kinds"] = list(dict.fromkeys(
                    registered["kind"] for registered in SOURCES
                    if connection.execute(
                        "SELECT 1 FROM discovery_provenance WHERE source_id=? AND compound_id=? LIMIT 1",
                        (registered["id"], row["id"]),
                    ).fetchone()
                ))
                preview_order = (
                    f"CASE WHEN source_id IN ({placeholders}) THEN 0 ELSE 1 END, id"
                    if source_ids else "id"
                )
                item["sources"] = [dict(p) for p in connection.execute(
                    "SELECT source_id,external_id,name,organisms,source_url,license_label "
                    "FROM discovery_provenance WHERE compound_id=? ORDER BY " + preview_order + " LIMIT 10",
                    (row["id"], *source_ids),
                )]
                items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_compound(self, compound_id: int | str) -> dict:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM discovery_compounds WHERE id=?", (compound_id,)).fetchone()
            if row is None:
                raise KeyError("Discovery compound not found")
            result = dict(row)
            result["smiles"] = result["canonical_smiles"]
            # Bound unusually common identities while making truncation explicit.
            result["provenance_count"] = connection.execute(
                "SELECT COUNT(*) FROM discovery_provenance WHERE compound_id=?", (row["id"],)
            ).fetchone()[0]
            result["provenance"] = []
            for provenance in connection.execute(
                "SELECT * FROM discovery_provenance WHERE compound_id=? ORDER BY id LIMIT 1000", (row["id"],)
            ):
                record = dict(provenance)
                record["raw"] = json.loads(record.pop("raw_json"))
                result["provenance"].append(record)
            result["provenance_truncated"] = result["provenance_count"] > len(result["provenance"])
        return result

    def iter_compounds(
        self,
        search: str = "",
        source: str | None = None,
        batch_size: int = 500,
        after_id: int = 0,
        through_id: int | None = None,
        include_sources: bool = True,
    ) -> Iterator[dict]:
        """Iterate finite, bounded batches using the compound primary key.

        ``after_id`` is an exclusive resume cursor and ``through_id`` an inclusive
        upper bound. Without an upper bound, capture the largest committed id at
        iteration start. Concurrent imports therefore cannot extend this scan
        indefinitely. This is an identity-id snapshot, not a transaction snapshot
        of source evidence. Each batch closes its read connection before yielding
        to avoid holding a WAL reader open throughout a long campaign/export.

        No OFFSET or total COUNT is executed. Source-only scans use the existing
        (source_id, compound_id) index; repeated source occurrences are grouped
        before applying the batch limit. Search uses the FTS index, although very
        broad search terms can still examine many matches on each batch.
        """
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= _MAX_PAGE:
            raise ValueError(f"batch_size must be between 1 and {_MAX_PAGE}")
        for label, value in (("after_id", after_id), ("through_id", through_id)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{label} must be a non-negative integer")
        if after_id is None:
            raise ValueError("after_id must be a non-negative integer")
        if not isinstance(search, str) or len(search) > 500:
            raise ValueError("search must be a string of at most 500 characters")
        query = None
        if search.strip():
            tokens = re.findall(r"[^\W_]+", search, flags=re.UNICODE)
            if not tokens:
                return
            query = " AND ".join(f'"{token}"*' for token in tokens[:20])
        with closing(self.connect()) as connection:
            highest_id = connection.execute("SELECT COALESCE(MAX(id),0) FROM discovery_compounds").fetchone()[0]
        ceiling = highest_id if through_id is None else min(highest_id, through_id)
        cursor_id = after_id
        while cursor_id < ceiling:
            params: list[Any]
            if query:
                source_filter = " AND p.source_id=?" if source else ""
                sql = (
                    "SELECT c.* FROM discovery_compounds c JOIN ("
                    "SELECT p.compound_id FROM discovery_search "
                    "CROSS JOIN discovery_provenance p ON p.id=discovery_search.rowid "
                    "WHERE discovery_search MATCH ? AND p.compound_id>? AND p.compound_id<=?"
                    + source_filter
                    + " GROUP BY p.compound_id ORDER BY p.compound_id LIMIT ?"
                    ") selected ON c.id=selected.compound_id ORDER BY c.id"
                )
                params = [query, cursor_id, ceiling, *([source] if source else []), batch_size]
            elif source:
                sql = (
                    "SELECT c.* FROM discovery_compounds c JOIN ("
                    "SELECT compound_id FROM discovery_provenance "
                    "WHERE source_id=? AND compound_id>? AND compound_id<=? "
                    "GROUP BY compound_id ORDER BY compound_id LIMIT ?"
                    ") selected ON c.id=selected.compound_id ORDER BY c.id"
                )
                params = [source, cursor_id, ceiling, batch_size]
            else:
                sql = "SELECT * FROM discovery_compounds WHERE id>? AND id<=? ORDER BY id LIMIT ?"
                params = [cursor_id, ceiling, batch_size]
            with closing(self.connect()) as connection:
                rows = connection.execute(sql, params).fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    item["smiles"] = item["canonical_smiles"]
                    if include_sources:
                        item["sources"] = [dict(p) for p in connection.execute(
                            "SELECT source_id,external_id,name,organisms,source_url,license_label "
                            "FROM discovery_provenance WHERE compound_id=? ORDER BY id LIMIT 10", (row["id"],)
                        )]
                    items.append(item)
            if not items:
                break
            cursor_id = items[-1]["id"]
            yield from items

    def ingest_file(
        self,
        path: str | Path,
        source_id: str,
        source_url: str,
        license_label: str,
        format: str | None = None,
        max_records: int | None = None,
        on_progress: Callable[[dict], Any] | None = None,
        batch_size: int = 500,
    ) -> dict:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        file_type = (format or (path.with_suffix("").suffix if path.suffix.lower() == ".gz" else path.suffix)).lower().lstrip(".")
        if file_type not in {"csv", "tsv", "smi", "smiles", "sdf"}:
            raise ValueError("Supported formats: CSV, TSV, SMI, SMILES or SDF, optionally gzip-compressed")
        return self.ingest_records(
            self._file_records(path, file_type), source_id, source_url, license_label,
            max_records=max_records, on_progress=on_progress, batch_size=batch_size,
            _file_info={"file_name": path.name, "file_bytes": path.stat().st_size, "format": file_type},
        )

    @staticmethod
    def _file_records(path: Path, file_type: str) -> Iterator[dict | None]:
        opener = gzip.open if path.suffix.lower() == ".gz" else open
        with opener(path, "rb") as binary:
            if file_type == "sdf":
                with rdBase.BlockLogs():
                    for molecule in Chem.ForwardSDMolSupplier(binary, sanitize=True, removeHs=False):
                        if molecule is None:
                            yield None
                            continue
                        record = molecule.GetPropsAsDict(includePrivate=False, autoConvertStrings=False)
                        record["smiles"] = Chem.MolToSmiles(molecule, isomericSmiles=True)
                        if molecule.HasProp("_Name"):
                            record.setdefault("identifier", molecule.GetProp("_Name"))
                        yield record
                return
            text = io.TextIOWrapper(binary, encoding="utf-8-sig", errors="replace", newline="")
            if file_type in {"csv", "tsv"}:
                # Some open catalogs include extensive organism/reference arrays.
                csv.field_size_limit(max(csv.field_size_limit(), 8 * 1024 * 1024))
                yield from csv.DictReader(text, delimiter="\t" if file_type == "tsv" else ",")
            else:
                for line in text:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(maxsplit=1)
                    if parts[0].lower() in {"smiles", "canonical_smiles", "isomeric_smiles"}:
                        continue
                    yield {"smiles": parts[0], "identifier": parts[1] if len(parts) > 1 else ""}

    def ingest_records(
        self,
        records: Iterable[Mapping | None],
        source_id: str,
        source_url: str,
        license_label: str,
        max_records: int | None = None,
        on_progress: Callable[[dict], Any] | None = None,
        batch_size: int = 500,
        _file_info: dict | None = None,
    ) -> dict:
        for label, value in (("source_id", source_id), ("source_url", source_url), ("license_label", license_label)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} is required for provenance")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", source_id):
            raise ValueError("source_id must contain 1–120 letters, digits, dots, underscores, colons or hyphens")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 10_000:
            raise ValueError("batch_size must be between 1 and 10,000")
        if max_records is not None and (
            isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 1
        ):
            raise ValueError("max_records must be a positive integer or None")
        report = {
            "id": uuid.uuid4().hex, "source_id": source_id, "source_url": source_url,
            "license_label": license_label, "status": "running", "started_at": _now(),
            "completed_at": None, "processed_records": 0, "inserted_compounds": 0,
            "inserted_records": 0, "duplicate_records": 0, "invalid_records": 0,
            "invalid_examples": [], "max_records": max_records, "limit_reached": False,
            "rdkit_version": rdBase.rdkitVersion, **(_file_info or {}),
        }
        started = time.monotonic()
        record_iterator = iter(records) if max_records is None else islice(records, max_records)
        with closing(self.connect()) as connection:
            connection.execute(
                "INSERT INTO discovery_sources(id,source_url,license_label,first_fetched_at,last_fetched_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url, "
                "license_label=excluded.license_label,last_fetched_at=excluded.last_fetched_at",
                (source_id, source_url, license_label, report["started_at"], report["started_at"]),
            )
            connection.execute(
                "INSERT INTO discovery_imports VALUES(?,?,?,?)",
                (report["id"], source_id, report["started_at"], _json(report)),
            )
            connection.commit()

            def checkpoint() -> None:
                report["elapsed_seconds"] = round(time.monotonic() - started, 3)
                connection.execute("UPDATE discovery_imports SET report_json=? WHERE id=?", (_json(report), report["id"]))
                connection.commit()
                if on_progress:
                    # Give callbacks an isolated snapshot, not our live counters.
                    on_progress(json.loads(_json(report)))

            try:
                for record in record_iterator:
                    report["processed_records"] += 1
                    try:
                        if not isinstance(record, Mapping):
                            raise ValueError("Invalid source record")
                        raw_json = _json(dict(record))
                        canonical, inchikey = _identity(_value(record, "smiles"))
                    except (ValueError, TypeError, RuntimeError) as error:
                        report["invalid_records"] += 1
                        if len(report["invalid_examples"]) < 20:
                            report["invalid_examples"].append({
                                "record": report["processed_records"], "reason": str(error)[:240],
                                "external_id": _value(record, "external_id")[:120] if isinstance(record, Mapping) else "",
                            })
                    else:
                        self._insert_record(connection, record, canonical, inchikey, raw_json, report)
                    if report["processed_records"] % batch_size == 0:
                        checkpoint()
                report["limit_reached"] = max_records is not None and report["processed_records"] == max_records
                report["status"] = "completed"
                report["completed_at"] = _now()
                checkpoint()
            except BaseException as error:
                # Keep committed batches and make partial imports explicit.
                report["status"] = "failed"
                report["completed_at"] = _now()
                report["error"] = f"{type(error).__name__}: {error}"[:1000]
                report["elapsed_seconds"] = round(time.monotonic() - started, 3)
                connection.execute("UPDATE discovery_imports SET report_json=? WHERE id=?", (_json(report), report["id"]))
                connection.commit()
                raise
            finally:
                close = getattr(record_iterator, "close", None)
                if close:
                    close()
        return report

    @staticmethod
    def _insert_record(connection: sqlite3.Connection, record: Mapping, canonical: str, inchikey: str | None,
                       raw_json: str, report: dict) -> None:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        external_id = _value(record, "external_id") or "sha256:" + hashlib.sha256(raw_json.encode()).hexdigest()
        name = _value(record, "name") or external_id
        existing = connection.execute(
            "SELECT id FROM discovery_compounds WHERE canonical_smiles=? OR inchikey=? LIMIT 1", (canonical, inchikey)
        ).fetchone()
        if existing is None:
            cursor = connection.execute(
                "INSERT INTO discovery_compounds(canonical_smiles,inchikey,display_name,formula,created_at) VALUES(?,?,?,?,?)",
                (canonical, inchikey, name, _value(record, "formula") or None, report["started_at"]),
            )
            compound_id = cursor.lastrowid
            report["inserted_compounds"] += 1
            connection.execute("UPDATE discovery_stats SET compound_count=compound_count+1 WHERE id=1")
        else:
            compound_id = existing[0]
        source_id = report["source_id"]
        source_has_compound = connection.execute(
            "SELECT 1 FROM discovery_provenance WHERE source_id=? AND compound_id=? LIMIT 1", (source_id, compound_id)
        ).fetchone()
        cursor = connection.execute(
            "INSERT OR IGNORE INTO discovery_provenance"
            "(compound_id,source_id,external_id,name,organisms,source_url,license_label,fetched_at,record_hash,raw_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (compound_id, source_id, external_id, name, _value(record, "organisms"), report["source_url"],
             report["license_label"], report["started_at"],
             hashlib.sha256((report["source_url"] + "\n" + report["license_label"] + "\n" + raw_json).encode()).hexdigest(),
             raw_json),
        )
        if cursor.rowcount:
            report["inserted_records"] += 1
            connection.execute("UPDATE discovery_stats SET provenance_count=provenance_count+1 WHERE id=1")
            connection.execute(
                "UPDATE discovery_sources SET record_count=record_count+1,compound_count=compound_count+? WHERE id=?",
                (0 if source_has_compound else 1, source_id),
            )
            connection.execute(
                "INSERT INTO discovery_search(rowid,external_id,name,organisms,inchikey) VALUES(?,?,?,?,?)",
                (cursor.lastrowid, external_id,
                 name + " " + " ".join(_text(v) for k, v in record.items() if _normalise_key(k) == "synonyms"),
                 " ".join([_value(record, "organisms")] + [
                     _text(v) for k, v in record.items() if _normalise_key(k).startswith("organism_taxonomy_")
                 ]), inchikey or ""),
            )
        else:
            report["duplicate_records"] += 1
