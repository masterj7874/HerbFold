"""Disk-backed, resumable and bounded computational candidate enumeration.

Requested library sizes are targets, never claims of generated drugs. Candidates are
BRICS proposals with traceable fragments; QED is only a structure heuristic. No LLM,
structure predictor, quantum service, or biological activity model runs here.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from itertools import islice
from pathlib import Path
from typing import Iterable, Iterator

from rdkit import Chem, rdBase
from rdkit.Chem import BRICS

from .chemistry import canonical_smiles, describe_molecule, standardize_molecule

TERMINAL_STATUSES = frozenset({"completed", "exhausted", "budget_exhausted", "cancelled", "failed"})
METHOD_VERSION = "brics-terminal-pair-stream-v1"
DEFAULT_FILTERS = {
    "min_mw": 120.0,
    "max_mw": 650.0,
    "min_logp": -2.0,
    "max_logp": 6.0,
    "max_tpsa": 180.0,
    "min_qed": 0.25,
}
INTERPRETATION = (
    "Unvalidated computational candidates, not new medicines. QED and descriptor filters do not predict "
    "affinity, efficacy, safety or synthesis feasibility. Novelty is only relative to the campaign input "
    "structures; no patent or external novelty search has been performed. Requested counts are targets; "
    "enumeration can exhaust its finite search space or resource budget before reaching them."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _request(raw: dict) -> dict:
    target = _integer(raw.get("target_candidates", 10000), "target_candidates", 1, 100_000_000)
    filters = {**DEFAULT_FILTERS, **raw.get("filters", {})}
    if set(filters) != set(DEFAULT_FILTERS):
        raise ValueError("Unknown descriptor filter")
    for key, value in filters.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} must be finite")
    if not 0 <= filters["min_mw"] < filters["max_mw"] <= 5000:
        raise ValueError("Molecular-weight bounds must satisfy 0 <= min_mw < max_mw <= 5000")
    if not -20 <= filters["min_logp"] < filters["max_logp"] <= 30:
        raise ValueError("logP bounds must satisfy -20 <= min_logp < max_logp <= 30")
    if not 0 <= filters["min_qed"] <= 1 or not 0 <= filters["max_tpsa"] <= 2000:
        raise ValueError("Invalid QED or TPSA threshold")
    if not isinstance(raw.get("reject_alerts", False), bool):
        raise ValueError("reject_alerts must be a boolean")
    return {
        "name": str(raw.get("name", "Herbal candidate campaign"))[:160],
        "target_candidates": target,
        "max_attempts": _integer(
            raw.get("max_attempts", min(1_000_000_000, max(1000, target * 100))),
            "max_attempts",
            1,
            1_000_000_000,
        ),
        "max_products_per_pair": _integer(
            raw.get("max_products_per_pair", 100),
            "max_products_per_pair",
            1,
            500,
        ),
        "max_runtime_seconds": _integer(
            raw.get("max_runtime_seconds", 3600),
            "max_runtime_seconds",
            1,
            31_536_000,
        ),
        "max_storage_mb": _integer(raw.get("max_storage_mb", 1024), "max_storage_mb", 1, 1_000_000),
        "seed": _integer(raw.get("seed", 42), "seed", 0, 2**32 - 1),
        "reject_alerts": raw.get("reject_alerts", False),
        "filters": filters,
    }


@lru_cache(maxsize=64)
def _fragments(smiles: str) -> frozenset[str]:
    # Break identified BRICS bonds once rather than recursively enumerating every
    # possible decomposition path; this keeps preparation bounded for glycosides.
    broken = BRICS.BreakBRICSBonds(standardize_molecule(smiles))
    return frozenset(
        Chem.MolToSmiles(fragment, isomericSmiles=True)
        for fragment in Chem.GetMolFrags(broken, asMols=True, sanitizeFrags=True)
        if fragment.GetNumHeavyAtoms() >= 2
    )


@lru_cache(maxsize=1)
def _joining_rules() -> dict:
    rules: dict[tuple[int, int], list] = {}
    for reaction in BRICS.reverseReactions:
        labels = tuple(
            next(
                atom.GetIsotope()
                for atom in reaction.GetReactantTemplate(i).GetAtoms()
                if atom.GetAtomicNum() == 0
            )
            for i in range(2)
        )
        rules.setdefault(labels, []).append((reaction, False))
        if labels[0] != labels[1]:
            rules.setdefault(labels[::-1], []).append((reaction, True))
    return rules


def _terminal_blocks(fragments: set[str]) -> list[tuple[Chem.Mol, int]]:
    result = []
    for fragment in sorted(fragments):
        mol = Chem.MolFromSmiles(fragment)
        attachments = [atom.GetIsotope() for atom in mol.GetAtoms() if atom.GetAtomicNum() == 0]
        if len(attachments) == 1:
            result.append((mol, attachments[0]))
    return result


def _pair_products(left: str, right: str):
    """One bounded operation per terminal-fragment pair, including incompatible pairs.

    Each operation applies only a matching BRICS joining rule and asks RDKit for
    at most one product. There is no recursive BRICSBuild combinatorial search.
    Remaining attachment atoms are never capped or silently removed. Limiting to
    two terminal blocks conservatively reduces the finite accessible library.
    """
    if left == right:
        return
    lf, rf = _fragments(left), _fragments(right)
    if not lf - rf or not rf - lf or len(lf | rf) > 32:
        return
    for lm, left_label in _terminal_blocks(lf - rf):
        for rm, right_label in _terminal_blocks(rf - lf):
            product = None
            for reaction, reverse in _joining_rules().get((left_label, right_label), []):
                reactants = (rm, lm) if reverse else (lm, rm)
                products = reaction.RunReactants(reactants, maxProducts=1)
                if products:
                    product = products[0][0]
                    break
            yield product


def _passes(desc: dict, request: dict) -> bool:
    f = request["filters"]
    return (
        f["min_mw"] <= desc["molecular_weight"] <= f["max_mw"]
        and f["min_logp"] <= desc["logp"] <= f["max_logp"]
        and desc["tpsa"] <= f["max_tpsa"]
        and desc["qed"] >= f["min_qed"]
        and (not request["reject_alerts"] or not desc["alerts"])
    )


class CampaignStore:
    """One SQLite writer serializes workers; cursor and candidate commit atomically.

    A process crash rolls back the current bounded batch. Replay is deterministic
    under the recorded RDKit version. Pause/cancel take effect at the next batch
    boundary (default 10 seconds). Each campaign keeps <= 10,000 immutable source
    snapshots in SQLite and retrieves two parents at a time. Storage budgets count
    serialized seed/candidate payload bytes; SQLite index/WAL overhead is additional.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "discovery-campaigns.sqlite3"
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    status TEXT NOT NULL, reason TEXT, request_json TEXT NOT NULL,
                    rdkit_version TEXT NOT NULL, method_version TEXT NOT NULL,
                    requested INTEGER NOT NULL, attempted INTEGER NOT NULL DEFAULT 0,
                    generated INTEGER NOT NULL DEFAULT 0, retained INTEGER NOT NULL DEFAULT 0,
                    rejected INTEGER NOT NULL DEFAULT 0, duplicates INTEGER NOT NULL DEFAULT 0,
                    pairs_visited INTEGER NOT NULL DEFAULT 0, cursor_pair INTEGER NOT NULL DEFAULT 0,
                    cursor_product INTEGER NOT NULL DEFAULT 0, total_pairs INTEGER NOT NULL,
                    plant_count INTEGER NOT NULL, drug_count INTEGER NOT NULL,
                    seed_count INTEGER NOT NULL, invalid_seed_count INTEGER NOT NULL,
                    duplicate_seed_count INTEGER NOT NULL, elapsed_seconds REAL NOT NULL DEFAULT 0,
                    payload_bytes INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS campaign_seeds (
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id), role TEXT NOT NULL,
                    position INTEGER NOT NULL, smiles TEXT NOT NULL, data_json TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, role, position)
                );
                CREATE INDEX IF NOT EXISTS campaign_seed_smiles ON campaign_seeds(campaign_id, smiles);
                CREATE TABLE IF NOT EXISTS campaign_candidates (
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id), id TEXT NOT NULL,
                    smiles TEXT NOT NULL, qed REAL NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    PRIMARY KEY(campaign_id, id), UNIQUE(campaign_id, smiles)
                );
                CREATE INDEX IF NOT EXISTS campaign_candidate_rank
                    ON campaign_candidates(campaign_id, qed DESC, id);
                CREATE INDEX IF NOT EXISTS campaign_candidate_order
                    ON campaign_candidates(campaign_id, ordinal);
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=35)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def _fetch(con: sqlite3.Connection, campaign_id: str) -> dict:
        row = con.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if row is None:
            raise KeyError(f"Campaign {campaign_id} not found")
        return dict(row)

    @staticmethod
    def _public(row: dict) -> dict:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["name"] = result["request"]["name"]
        result["target_candidates"] = result["requested"]
        result["cursor"] = {"pair": result["cursor_pair"], "product": result["cursor_product"]}
        result["bounded_product_ceiling"] = result["total_pairs"] * result["request"]["max_products_per_pair"]
        result["progress_fraction"] = min(1.0, result["retained"] / result["requested"])
        result["interpretation"] = INTERPRETATION
        result["count_definitions"] = {
            "requested": "User target, not an achieved molecule count",
            "attempted": "Product proposals plus one attempt for each empty/failed pair",
            "generated": "Sanitized product proposals, before lineage/filter checks; includes repeats",
            "retained": "Unique structures with two-parent fragment ancestry passing configured filters",
            "rejected": "Attempts rejected for invalid structure, lineage, parent identity or filters",
            "duplicates": "Otherwise admissible proposals already retained in this campaign",
            "payload_bytes": "Stored JSON source/candidate bytes; excludes SQLite index and WAL overhead",
        }
        return result

    def create(self, request: dict, seeds: Iterable[dict]) -> dict:
        request = _request(request)
        groups: dict[str, dict[str, dict]] = {"plant": {}, "drug": {}}
        invalid = duplicate = payload_bytes = 0
        for count, original in enumerate(seeds, 1):
            if count > 10000:
                raise ValueError(
                    "A campaign may snapshot at most 10,000 parents; create another shard for more"
                )
            row = dict(original)
            role = row.get("category", "unknown")
            category = "natural_product" if role == "plant" else role
            role = "plant" if category in {"herbal", "natural_product"} else category
            if role not in groups:
                invalid += 1
                continue
            try:
                smiles = canonical_smiles(row.get("smiles", row.get("canonical_smiles", "")))
            except (ValueError, RuntimeError):
                invalid += 1
                continue
            if smiles in groups[role]:
                # Preserve every supplied identity/source for identical structures.
                groups[role][smiles]["source_records"].append(row)
                duplicate += 1
                continue
            groups[role][smiles] = {
                "id": str(row.get("id", "source-" + hashlib.sha256(smiles.encode()).hexdigest()[:16])),
                "name": row.get("name", row.get("name_ko", smiles)),
                "category": category,
                "smiles": smiles,
                "source_records": [row],
            }
        if not groups["plant"] or not groups["drug"]:
            raise ValueError(
                "Provide at least one valid herbal/natural-product compound and one reference drug"
            )
        campaign_id = uuid.uuid4().hex
        prepared = []
        for role, entries in groups.items():
            ordered = sorted(
                entries.items(),
                key=lambda item: hashlib.sha256(f"{request['seed']}:{item[0]}".encode()).hexdigest(),
            )
            for position, (smiles, row) in enumerate(ordered):
                data = _json(row)
                payload_bytes += len(data.encode())
                prepared.append((campaign_id, role, position, smiles, data))
        if payload_bytes >= request["max_storage_mb"] * 1024**2:
            raise ValueError("Source snapshots exceed the campaign payload storage budget")
        plant_count, drug_count = len(groups["plant"]), len(groups["drug"])
        now = _now()
        with self._connect() as con:
            con.execute(
                "INSERT INTO campaigns(id,created_at,updated_at,status,request_json,rdkit_version,"
                "method_version,requested,total_pairs,plant_count,drug_count,seed_count,"
                "invalid_seed_count,duplicate_seed_count,payload_bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    campaign_id,
                    now,
                    now,
                    "queued",
                    _json(request),
                    rdBase.rdkitVersion,
                    METHOD_VERSION,
                    request["target_candidates"],
                    plant_count * drug_count,
                    plant_count,
                    drug_count,
                    plant_count + drug_count,
                    invalid,
                    duplicate,
                    payload_bytes,
                ),
            )
            con.executemany("INSERT INTO campaign_seeds VALUES(?,?,?,?,?)", prepared)
        return self.get(campaign_id)

    def get(self, campaign_id: str) -> dict:
        with self._connect() as con:
            return self._public(self._fetch(con, campaign_id))

    def list(self, limit: int = 50) -> list[dict]:
        _integer(limit, "limit", 1, 200)
        with self._connect() as con:
            return [
                self._public(dict(row))
                for row in con.execute("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT ?", (limit,))
            ]

    def active(self) -> list[dict]:
        """All runnable campaigns, without the UI list's 50-record history limit."""
        with self._connect() as con:
            return [
                self._public(dict(row))
                for row in con.execute(
                    "SELECT * FROM campaigns WHERE status IN ('queued','running') ORDER BY created_at,id"
                )
            ]

    def fail(self, campaign_id: str, error: str | BaseException) -> dict:
        """Persist a worker failure instead of disguising it as a user pause."""
        description = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = self._fetch(con, campaign_id)
            if row["status"] not in TERMINAL_STATUSES:
                con.execute(
                    "UPDATE campaigns SET status='failed',reason=?,updated_at=? WHERE id=?",
                    (("worker_error: " + description)[:2000], _now(), campaign_id),
                )
        return self.get(campaign_id)

    def _control(self, campaign_id: str, action: str) -> dict:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = self._fetch(con, campaign_id)
            if row["status"] not in TERMINAL_STATUSES:
                status = {"pause": "paused", "resume": "queued", "cancel": "cancelled"}[action]
                con.execute(
                    "UPDATE campaigns SET status=?, reason=?, updated_at=? WHERE id=?",
                    (status, "user_" + action, _now(), campaign_id),
                )
        return self.get(campaign_id)

    def pause(self, campaign_id: str) -> dict:
        return self._control(campaign_id, "pause")

    def resume(self, campaign_id: str) -> dict:
        return self._control(campaign_id, "resume")

    def cancel(self, campaign_id: str) -> dict:
        return self._control(campaign_id, "cancel")

    @staticmethod
    def _parent(con: sqlite3.Connection, campaign_id: str, role: str, position: int) -> dict:
        return json.loads(
            con.execute(
                "SELECT data_json FROM campaign_seeds WHERE campaign_id=? AND role=? AND position=?",
                (campaign_id, role, position),
            ).fetchone()[0]
        )

    @staticmethod
    def _candidate(product: Chem.Mol, parents: list[dict], request: dict) -> dict | None:
        Chem.SanitizeMol(product)
        smiles = canonical_smiles(Chem.MolToSmiles(product, isomericSmiles=True))
        left, right = (parent["smiles"] for parent in parents)
        lf, rf = _fragments(left), _fragments(right)
        parts = _fragments(smiles)
        contributions = [sorted(parts & (lf - rf)), sorted(parts & (rf - lf))]
        if not all(contributions):
            return {"smiles": smiles, "rejected": "fragment_lineage"}
        desc = describe_molecule(smiles)
        if not _passes(desc, request):
            return {"smiles": smiles, "rejected": "descriptor_filter"}
        return {
            "id": "campaign-" + hashlib.sha256(smiles.encode()).hexdigest(),
            "smiles": smiles,
            "canonical_smiles": smiles,
            "parents": [left, right],
            "parent_ids": [row["id"] for row in parents],
            "parent_fragments": [
                {"parent_smiles": row["smiles"], "parent_id": row["id"], "contributing_fragments": fragments}
                for row, fragments in zip(parents, contributions, strict=True)
            ],
            "source_lineage": parents,
            "herbal_parent_id": parents[0]["id"] if parents[0]["category"] == "herbal" else None,
            "natural_product_parent_id": parents[0]["id"]
            if parents[0]["category"] == "natural_product"
            else None,
            "reference_drug_id": parents[1]["id"],
            "affinity": None,
            "novelty": "not_assessed",
            "descriptors": desc,
            "heuristic_score": desc["qed"],
            "heuristic_score_name": "RDKit QED; not affinity or activity",
            "novel_relative_to_inputs": True,
            "novelty_scope": "Exact standardized campaign seed structures only; no database or patent search",
            "method": METHOD_VERSION,
            "status": "unvalidated_computational_candidate",
            "interpretation": INTERPRETATION,
        }

    def run_batch(self, campaign_id: str, max_attempts: int = 50, max_seconds: float = 10) -> dict:
        _integer(max_attempts, "batch max_attempts", 1, 1000)
        if (
            isinstance(max_seconds, bool)
            or not isinstance(max_seconds, (int, float))
            or not 0 < max_seconds <= 30
        ):
            raise ValueError("max_seconds must be greater than 0 and at most 30")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            row = self._fetch(con, campaign_id)
            if row["status"] == "paused" or row["status"] in TERMINAL_STATUSES:
                return self._public(row)
            request = json.loads(row["request_json"])
            if row["rdkit_version"] != rdBase.rdkitVersion or row["method_version"] != METHOD_VERSION:
                con.execute(
                    "UPDATE campaigns SET status='paused',reason='enumerator_version_mismatch' WHERE id=?",
                    (campaign_id,),
                )
                return self._public(self._fetch(con, campaign_id))
            row["status"], row["reason"] = "running", None
            baseline = row["attempted"]
            pair_iterator, active_pair, parents = None, None, None
            while row["attempted"] - baseline < max_attempts:
                elapsed = time.monotonic() - started
                if row["retained"] >= row["requested"]:
                    row["status"], row["reason"] = "completed", "target_reached"
                    break
                if row["cursor_pair"] >= row["total_pairs"]:
                    row["status"], row["reason"] = "exhausted", "bounded_search_space_exhausted"
                    break
                if row["attempted"] >= request["max_attempts"]:
                    row["status"], row["reason"] = "budget_exhausted", "attempt_budget"
                    break
                if row["elapsed_seconds"] + elapsed >= request["max_runtime_seconds"]:
                    row["status"], row["reason"] = "budget_exhausted", "runtime_budget"
                    break
                if row["payload_bytes"] >= request["max_storage_mb"] * 1024**2:
                    row["status"], row["reason"] = "budget_exhausted", "payload_storage_budget"
                    break
                if shutil.disk_usage(self.root).free < 16 * 1024**2:
                    row["status"], row["reason"] = "budget_exhausted", "low_free_disk"
                    break
                if elapsed >= max_seconds:
                    break
                if active_pair != row["cursor_pair"]:
                    active_pair = row["cursor_pair"]
                    parents = [
                        self._parent(con, campaign_id, "plant", active_pair // row["drug_count"]),
                        self._parent(con, campaign_id, "drug", active_pair % row["drug_count"]),
                    ]
                    try:
                        pair_iterator = iter(
                            islice(
                                _pair_products(*(p["smiles"] for p in parents)),
                                row["cursor_product"],
                                request["max_products_per_pair"],
                            )
                        )
                    except (ValueError, RuntimeError):
                        pair_iterator = iter(())
                try:
                    product = next(pair_iterator)
                except (StopIteration, ValueError, RuntimeError):
                    # Empty/failed pairs consume budget too, so unsplittable seeds cannot
                    # create an unbounded scan. Completed pairs with products cost no extra.
                    if row["cursor_product"] == 0:
                        row["attempted"] += 1
                        row["rejected"] += 1
                    row["cursor_pair"] += 1
                    row["cursor_product"] = 0
                    row["pairs_visited"] += 1
                    continue
                row["attempted"] += 1
                row["cursor_product"] += 1
                if product is None:
                    row["rejected"] += 1
                    continue
                try:
                    candidate = self._candidate(product, parents, request)
                    row["generated"] += 1
                    existing_parent = con.execute(
                        "SELECT 1 FROM campaign_seeds WHERE campaign_id=? AND smiles=? LIMIT 1",
                        (campaign_id, candidate["smiles"]),
                    ).fetchone()
                    if candidate.get("rejected") or existing_parent:
                        row["rejected"] += 1
                        continue
                    # Provenance is a frozen snapshot of both parents and all source aliases.
                    payload = _json(candidate)
                    size = len(payload.encode())
                    if row["payload_bytes"] + size > request["max_storage_mb"] * 1024**2:
                        row["rejected"] += 1
                        row["status"], row["reason"] = "budget_exhausted", "payload_storage_budget"
                        break
                    changed = con.execute(
                        "INSERT OR IGNORE INTO campaign_candidates VALUES(?,?,?,?,?,?,?)",
                        (
                            campaign_id,
                            candidate["id"],
                            candidate["smiles"],
                            candidate["descriptors"]["qed"],
                            payload,
                            _now(),
                            row["retained"] + 1,
                        ),
                    ).rowcount
                    if changed:
                        row["retained"] += 1
                        row["payload_bytes"] += size
                    else:
                        row["duplicates"] += 1
                except (ValueError, RuntimeError):
                    row["rejected"] += 1
            row["elapsed_seconds"] += time.monotonic() - started
            # Resolve limits even when the final product landed exactly at the batch edge.
            if row["status"] == "running":
                if row["retained"] >= row["requested"]:
                    row["status"], row["reason"] = "completed", "target_reached"
                elif row["attempted"] >= request["max_attempts"]:
                    row["status"], row["reason"] = "budget_exhausted", "attempt_budget"
                elif row["elapsed_seconds"] >= request["max_runtime_seconds"]:
                    row["status"], row["reason"] = "budget_exhausted", "runtime_budget"
                elif row["cursor_pair"] >= row["total_pairs"]:
                    row["status"], row["reason"] = "exhausted", "bounded_search_space_exhausted"
            fields = (
                "status",
                "reason",
                "attempted",
                "generated",
                "retained",
                "rejected",
                "duplicates",
                "pairs_visited",
                "cursor_pair",
                "cursor_product",
                "elapsed_seconds",
                "payload_bytes",
            )
            con.execute(
                "UPDATE campaigns SET "
                + ",".join(f"{name}=?" for name in fields)
                + ",updated_at=? WHERE id=?",
                (*[row[name] for name in fields], _now(), campaign_id),
            )
            result = self._public(self._fetch(con, campaign_id))
        return result

    def candidates(self, campaign_id: str, limit: int = 50, offset: int = 0, order: str = "qed") -> dict:
        _integer(limit, "limit", 1, 200)
        _integer(offset, "offset", 0, 100_000_000)
        if order not in {"qed", "created"}:
            raise ValueError("order must be qed or created")
        ordering = "qed DESC, id" if order == "qed" else "ordinal"
        with self._connect() as con:
            row = self._fetch(con, campaign_id)
            items = [
                json.loads(result[0])
                for result in con.execute(
                    f"SELECT payload_json FROM campaign_candidates WHERE campaign_id=? ORDER BY {ordering} LIMIT ? OFFSET ?",
                    (campaign_id, limit, offset),
                )
            ]
        return {
            "items": items,
            "total": row["retained"],
            "limit": limit,
            "offset": offset,
            "order": order,
            "interpretation": INTERPRETATION,
        }

    def iter_candidates(self, campaign_id: str, batch_size: int = 100) -> Iterator[dict]:
        """Stream a fixed snapshot using indexed keyset pages, without OFFSET scans.

        Suitable for NDJSON/CSV export of large libraries. The read transaction
        closes between pages so an export does not hold back WAL checkpoints.
        """
        _integer(batch_size, "batch_size", 1, 1000)
        ceiling = self.get(campaign_id)["retained"]
        after = 0
        while after < ceiling:
            with self._connect() as con:
                rows = con.execute(
                    "SELECT ordinal,payload_json FROM campaign_candidates "
                    "WHERE campaign_id=? AND ordinal>? AND ordinal<=? ORDER BY ordinal LIMIT ?",
                    (campaign_id, after, ceiling, batch_size),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                yield json.loads(row["payload_json"])
            after = rows[-1]["ordinal"]
