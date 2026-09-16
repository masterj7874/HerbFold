"""Auditable parallel chemistry benchmark, isolated from production campaigns.

Every counted attempt is a distinct compatible natural/drug terminal-fragment
pair slot. SQL deduplication counts unique canonical structures separately. This
measures computational enumeration, never efficacy, safety, or therapeutic novelty.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import multiprocessing
import os
import resource
import shutil
import sqlite3
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache, wraps
from pathlib import Path

from rdkit import Chem, rdBase
from rdkit.Chem import BRICS, QED

from .chemistry import standardize_molecule
from .discovery_campaign import _joining_rules

METHOD = "deduplicated-terminal-brics-parallel-v1"
INTERPRETATION = (
    "Measured chemical enumeration only. Attempts, sanitized proposals and unique retained structures are "
    "different counts. QED/physicochemical filters do not validate efficacy, safety, binding, synthesizability "
    "or patent novelty. The finite compatible-pair count is an upper bound, not a count of drugs."
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _serialized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with (self.root / "scale-worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    "Another scale-validation worker is already active in this directory"
                ) from error
            try:
                return method(self, *args, **kwargs)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    return locked


def _live_rss_bytes():
    parent = os.getpid()
    ids = [parent]
    try:
        ids.extend(map(int, Path(f"/proc/{parent}/task/{parent}/children").read_text().split()))
    except OSError:
        pass
    rss = 0
    for process in ids:
        try:
            rss += int(Path(f"/proc/{process}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            pass
    return rss


def _init_worker():
    rdBase.DisableLog("rdApp.warning")
    rdBase.DisableLog("rdApp.error")


def _bounded_parallel(executor, function, chunks, workers):
    iterator = iter(chunks)
    pending = deque()
    for _ in range(workers * 2):
        item = next(iterator, None)
        if item is None:
            break
        pending.append(executor.submit(function, item))
    while pending:
        yield pending.popleft().result()
        item = next(iterator, None)
        if item is not None:
            pending.append(executor.submit(function, item))


def _prepare_chunk(rows):
    results = []
    for catalog_id, original_smiles, role in rows:
        try:
            mol = standardize_molecule(original_smiles)
            canonical = Chem.MolToSmiles(mol, isomericSmiles=True)
            broken = BRICS.BreakBRICSBonds(mol)
            fragments = []
            for fragment in Chem.GetMolFrags(broken, asMols=True, sanitizeFrags=True):
                attachments = [a.GetIsotope() for a in fragment.GetAtoms() if a.GetAtomicNum() == 0]
                if len(attachments) == 1 and fragment.GetNumHeavyAtoms() >= 2:
                    fragments.append((attachments[0], Chem.MolToSmiles(fragment, isomericSmiles=True)))
            results.append((catalog_id, role, original_smiles, canonical, sorted(set(fragments))))
        except (ValueError, RuntimeError):
            results.append((catalog_id, role, original_smiles, None, []))
    return results


@lru_cache(maxsize=16384)
def _fragment_molecule(smiles):
    return Chem.MolFromSmiles(smiles)


def _generate_chunk(operations):
    counters = {"attempted": 0, "sanitized": 0, "passed_filters": 0, "rejected": 0}
    retained = []
    for index, left_id, left_smiles, left_label, right_id, right_smiles, right_label in operations:
        counters["attempted"] += 1
        try:
            if left_smiles == right_smiles:
                counters["rejected"] += 1
                continue
            left, right = _fragment_molecule(left_smiles), _fragment_molecule(right_smiles)
            product = None
            for reaction, reverse in _joining_rules().get((left_label, right_label), []):
                products = reaction.RunReactants((right, left) if reverse else (left, right), maxProducts=1)
                if products:
                    product = products[0][0]
                    break
            if product is None:
                counters["rejected"] += 1
                continue
            Chem.SanitizeMol(product)
            if product.GetNumHeavyAtoms() > 256 or any(a.GetAtomicNum() == 0 for a in product.GetAtoms()):
                counters["rejected"] += 1
                continue
            counters["sanitized"] += 1
            properties = QED.properties(product)
            score = QED.qed(product, qedProperties=properties)
            if not (
                120 <= properties.MW <= 650
                and -2 <= properties.ALOGP <= 6
                and properties.PSA <= 180
                and score >= 0.25
            ):
                counters["rejected"] += 1
                continue
            smiles = Chem.MolToSmiles(product, isomericSmiles=True)
            counters["passed_filters"] += 1
            retained.append(
                (
                    hashlib.sha256(smiles.encode()).digest(),
                    smiles,
                    left_id,
                    right_id,
                    properties.MW,
                    properties.ALOGP,
                    properties.PSA,
                    score,
                    properties.HBD,
                    properties.HBA,
                    properties.ROTB,
                    properties.ALERTS,
                    index,
                )
            )
        except (ValueError, RuntimeError):
            counters["rejected"] += 1
    return counters, retained, operations[-1][0] + 1


class ScaleValidation:
    def __init__(self, root: str | Path, catalog_path: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_path = Path(catalog_path).resolve()
        self.db = self.root / "scale.sqlite3"
        self.progress_path = self.root / "scale-progress.json"
        self._peak_aggregate_rss_mb = 0.0
        self._peak_process_rss_mb = 0.0
        self._peak_child_rss_mb = 0.0
        if self.progress_path.exists():
            try:
                previous_report = json.loads(self.progress_path.read_text())
                self._peak_aggregate_rss_mb = previous_report.get("sampled_peak_aggregate_rss_mb", 0.0)
                self._peak_process_rss_mb = previous_report.get("process_peak_rss_mb", 0.0)
                self._peak_child_rss_mb = previous_report.get("children_peak_rss_mb", 0.0)
            except (OSError, ValueError):
                pass
        with self.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS parents(
                    catalog_id INTEGER NOT NULL,role TEXT NOT NULL,original_smiles TEXT NOT NULL,
                    smiles TEXT NOT NULL,PRIMARY KEY(catalog_id,role));
                CREATE INDEX IF NOT EXISTS parent_smiles ON parents(smiles);
                CREATE TABLE IF NOT EXISTS fragments(
                    id INTEGER PRIMARY KEY,role TEXT NOT NULL,label INTEGER NOT NULL,smiles TEXT NOT NULL,
                    UNIQUE(role,smiles));
                CREATE TABLE IF NOT EXISTS ancestry(
                    fragment_id INTEGER NOT NULL,catalog_id INTEGER NOT NULL,role TEXT NOT NULL,
                    PRIMARY KEY(fragment_id,catalog_id,role));
                CREATE TABLE IF NOT EXISTS candidates(
                    digest BLOB PRIMARY KEY,smiles TEXT NOT NULL UNIQUE,left_fragment INTEGER NOT NULL,
                    right_fragment INTEGER NOT NULL,mw REAL,logp REAL,tpsa REAL,qed REAL,
                    hbd INTEGER,hba INTEGER,rotors INTEGER,qed_alerts INTEGER,attempt_index INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS candidate_qed ON candidates(qed DESC,digest);
            """)
            if not con.execute("SELECT 1 FROM metadata WHERE key='state'").fetchone():
                with sqlite3.connect(f"file:{self.catalog_path}?mode=ro", uri=True) as catalog:
                    max_id = catalog.execute(
                        "SELECT COALESCE(MAX(id),0) FROM discovery_compounds"
                    ).fetchone()[0]
                    max_provenance = catalog.execute(
                        "SELECT COALESCE(MAX(id),0) FROM discovery_provenance"
                    ).fetchone()[0]
                state = {
                    "schema_version": 1,
                    "method": METHOD,
                    "rdkit_version": rdBase.rdkitVersion,
                    "created_at": _now(),
                    "status": "preparing",
                    "phase": "preparation",
                    "catalog_path": str(self.catalog_path),
                    "catalog_max_id": max_id,
                    "catalog_max_provenance_id": max_provenance,
                    "preparation_role": "drug",
                    "preparation_cursor": 0,
                    "prepared_parents": 0,
                    "invalid_parents": 0,
                    "processed_parents": 0,
                    "natural_fragments": 0,
                    "drug_fragments": 0,
                    "attempted": 0,
                    "sanitized": 0,
                    "passed_filters": 0,
                    "retained_unique": 0,
                    "duplicates": 0,
                    "rejected": 0,
                    "known_parent_matches": 0,
                    "requested_attempts": 0,
                    "cursor": 0,
                    "generation_seconds": 0.0,
                    "preparation_seconds": 0.0,
                    "elapsed_seconds": 0.0,
                    "compatible_pair_slots_upper_bound": 0,
                    "workers": 0,
                    "interpretation": INTERPRETATION,
                }
                self._save(con, state)
        if self.state()["catalog_path"] != str(self.catalog_path):
            raise ValueError("This validation directory belongs to a different catalog snapshot")
        if not self.progress_path.exists():
            self._report(self.state())

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=60)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        try:
            with con:
                yield con
        finally:
            con.close()

    def state(self):
        with self.connect() as con:
            state = json.loads(con.execute("SELECT value FROM metadata WHERE key='state'").fetchone()[0])
        # Measured phase durations are authoritative, including for legacy rows
        # whose cached total was initialized to zero and never refreshed.
        state["elapsed_seconds"] = state["preparation_seconds"] + state["generation_seconds"]
        return state

    def _save(self, con, state):
        state["elapsed_seconds"] = state["preparation_seconds"] + state["generation_seconds"]
        state["updated_at"] = _now()
        con.execute("INSERT OR REPLACE INTO metadata VALUES('state',?)", (json.dumps(state),))

    def _report(self, state):
        report = dict(state)
        report["worker_pid"] = os.getpid()
        report["logical_cpu_count"] = os.cpu_count()
        report["aggregate_rss_mb"] = _live_rss_bytes() / 1024**2
        self._peak_aggregate_rss_mb = max(self._peak_aggregate_rss_mb, report["aggregate_rss_mb"])
        report["sampled_peak_aggregate_rss_mb"] = self._peak_aggregate_rss_mb
        report["aggregate_memory_scope"] = (
            "Sum of live parent and direct-child RSS sampled at commits; shared pages may be counted multiple times"
        )
        report["database_bytes"] = sum(
            path.stat().st_size for path in self.root.glob("scale.sqlite3*") if path.is_file()
        )
        report["elapsed_seconds"] = report["preparation_seconds"] + report["generation_seconds"]
        report["attempts_per_second"] = report["attempted"] / max(0.001, report["generation_seconds"])
        report["retained_per_second"] = report["retained_unique"] / max(0.001, report["generation_seconds"])
        self._peak_process_rss_mb = max(
            self._peak_process_rss_mb, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        )
        self._peak_child_rss_mb = max(
            self._peak_child_rss_mb, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
        )
        report["process_peak_rss_mb"] = self._peak_process_rss_mb
        report["children_peak_rss_mb"] = self._peak_child_rss_mb
        report["memory_measurement"] = (
            "Maximum recorded ru_maxrss across run segments: parent and largest reaped child, not summed live RSS"
        )
        if report["attempted"]:
            report["projection"] = {
                "is_measured": False,
                "target_attempts": 100_000_000,
                "estimated_seconds": 100_000_000 / report["attempts_per_second"],
                "basis_attempts": report["attempted"],
                "finite_space_allows_target": report["compatible_pair_slots_upper_bound"] >= 100_000_000,
                "limitation": "Linear projection of measured prefix only; later fragment-label groups can differ in runtime and filtering yield",
            }
        temporary = self.progress_path.with_suffix(".json.part")
        temporary.write_text(json.dumps(report, indent=2))
        temporary.replace(self.progress_path)
        return report

    def _check_version(self, state):
        if state["method"] != METHOD or state["rdkit_version"] != rdBase.rdkitVersion:
            raise ValueError("Enumerator/RDKit version changed; use a new validation directory")

    def _parent_chunks(self, role, after, maximum, batch_size, max_rows=None):
        sources = ("chembl-approved",) if role == "drug" else ("lotus-2026-04", "coconut-2026-09")
        emitted = 0
        max_provenance = self.state()["catalog_max_provenance_id"]
        with sqlite3.connect(f"file:{self.catalog_path}?mode=ro", uri=True) as catalog:
            while max_rows is None or emitted < max_rows:
                count = batch_size if max_rows is None else min(batch_size, max_rows - emitted)
                rows = catalog.execute(
                    "SELECT c.id,c.canonical_smiles FROM discovery_compounds c WHERE c.id>? AND c.id<=? "
                    "AND EXISTS(SELECT 1 FROM discovery_provenance p WHERE p.compound_id=c.id AND p.id<=? "
                    f"AND p.source_id IN ({','.join('?' for _ in sources)})) ORDER BY c.id LIMIT ?",
                    (after, maximum, max_provenance, *sources, count),
                ).fetchall()
                if not rows:
                    break
                yield [(row[0], row[1], role) for row in rows]
                after = rows[-1][0]
                emitted += len(rows)

    @staticmethod
    def _workers(workers):
        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 32:
            raise ValueError("workers must be an integer between 1 and 32")
        return min(workers, max(1, (os.cpu_count() or 2) // 2))

    @_serialized
    def prepare(self, workers=16, batch_size=256, max_parents=None):
        workers = self._workers(workers)
        if not 1 <= batch_size <= 1000 or (max_parents is not None and max_parents < 1):
            raise ValueError("Invalid preparation batch or parent budget")
        state = self.state()
        self._check_version(state)
        if state["phase"] == "generation":
            return self._report(state)
        state.update(status="preparing", workers=workers)
        started = time.monotonic()
        initial_time = state["preparation_seconds"]
        processed_start = state["processed_parents"]
        with self.connect() as con:
            fragment_ids = {
                (role, smiles): fragment_id
                for fragment_id, role, smiles in con.execute("SELECT id,role,smiles FROM fragments")
            }
        try:
            with ProcessPoolExecutor(
                max_workers=workers, initializer=_init_worker, mp_context=multiprocessing.get_context("spawn")
            ) as executor:
                for role in ("drug", "natural_product"):
                    if state["preparation_role"] == "natural_product" and role == "drug":
                        continue
                    remaining = (
                        None
                        if max_parents is None
                        else max_parents - (state["processed_parents"] - processed_start)
                    )
                    if remaining is not None and remaining <= 0:
                        break
                    chunks = self._parent_chunks(
                        role, state["preparation_cursor"], state["catalog_max_id"], batch_size, remaining
                    )
                    for results in _bounded_parallel(executor, _prepare_chunk, chunks, workers):
                        with self.connect() as con:
                            for catalog_id, item_role, original, canonical, fragments in results:
                                state["processed_parents"] += 1
                                if canonical is None:
                                    state["invalid_parents"] += 1
                                    continue
                                state["prepared_parents"] += 1
                                con.execute(
                                    "INSERT OR IGNORE INTO parents VALUES(?,?,?,?)",
                                    (catalog_id, item_role, original, canonical),
                                )
                                for label, smiles in fragments:
                                    key = (item_role, smiles)
                                    if key not in fragment_ids:
                                        fragment_ids[key] = con.execute(
                                            "INSERT INTO fragments(role,label,smiles) VALUES(?,?,?)",
                                            (item_role, label, smiles),
                                        ).lastrowid
                                        state[
                                            "drug_fragments" if item_role == "drug" else "natural_fragments"
                                        ] += 1
                                    con.execute(
                                        "INSERT OR IGNORE INTO ancestry VALUES(?,?,?)",
                                        (fragment_ids[key], catalog_id, item_role),
                                    )
                            state["preparation_cursor"] = results[-1][0]
                            state["preparation_seconds"] = initial_time + time.monotonic() - started
                            self._save(con, state)
                        self._report(state)
                    if (
                        max_parents is not None
                        and state["processed_parents"] - processed_start >= max_parents
                    ):
                        break
                    state["preparation_role"], state["preparation_cursor"] = "natural_product", 0
                    if role == "natural_product":
                        state["phase"], state["status"] = "generation", "ready"
                        state["compatible_pair_slots_upper_bound"] = sum(group[0] for group in self._groups())
                    with self.connect() as con:
                        self._save(con, state)
        except KeyboardInterrupt:
            state = self.state()
            state["status"] = "paused"
            with self.connect() as con:
                self._save(con, state)
        return self._report(self.state())

    def _groups(self):
        groups = {}
        with self.connect() as con:
            for fragment_id, role, label, smiles in con.execute("SELECT id,role,label,smiles FROM fragments"):
                groups.setdefault((role, label), []).append((fragment_id, smiles, label))
        for values in groups.values():
            values.sort(key=lambda row: hashlib.sha256(row[1].encode()).digest())
        result = []
        for left_label, right_label in sorted(_joining_rules()):
            left, right = (
                groups.get(("natural_product", left_label), []),
                groups.get(("drug", right_label), []),
            )
            if left and right:
                result.append((len(left) * len(right), left, right))
        return result

    def _single_parent_fragments(self):
        """Fragments whose entire ancestry is exactly one standardized structure.

        Two nonempty ancestry sets have no distinct-parent pairing precisely when
        both are the same singleton set. Catalog IDs and source roles alone do
        not establish that the parent structures differ.
        """
        with self.connect() as con:
            con.execute("PRAGMA cache_size=-262144")
            return {
                fragment_id: smiles
                for fragment_id, smiles in con.execute(
                    "SELECT a.fragment_id,MIN(p.smiles) FROM ancestry a JOIN parents p "
                    "ON p.catalog_id=a.catalog_id AND p.role=a.role GROUP BY a.fragment_id "
                    "HAVING COUNT(DISTINCT p.smiles)=1"
                )
            }

    def _operation_chunks(self, cursor, end, batch_size):
        start = 0
        batch = []
        for count, left, right in self._groups():
            if start + count <= cursor:
                start += count
                continue
            for relative in range(max(0, cursor - start), min(count, end - start)):
                lf, rf = left[relative // len(right)], right[relative % len(right)]
                batch.append((start + relative, lf[0], lf[1], lf[2], rf[0], rf[1], rf[2]))
                if len(batch) == batch_size:
                    yield batch
                    batch = []
            start += count
            if start >= end:
                break
        if batch:
            yield batch

    @_serialized
    def run(
        self,
        target_attempts=1_000_000,
        workers=16,
        batch_size=512,
        max_seconds=1800,
        max_storage_gb=100,
        reserve_disk_gb=20,
    ):
        workers = self._workers(workers)
        if (
            isinstance(target_attempts, bool)
            or not isinstance(target_attempts, int)
            or not 1 <= target_attempts <= 100_000_000
        ):
            raise ValueError("target_attempts must be between 1 and 100,000,000")
        if not 1 <= batch_size <= 4096 or max_seconds <= 0 or max_storage_gb <= 0 or reserve_disk_gb < 0:
            raise ValueError("Invalid execution budget")
        state = self.state()
        self._check_version(state)
        if state["phase"] != "generation":
            raise ValueError("Finish parent/fragment preparation first")
        if state["cursor"] >= target_attempts:
            # Reopening an already achieved benchmark must not add setup time to
            # its measured throughput or relabel a smaller target as a new run.
            return self._report(state)
        state.update(status="running", requested_attempts=target_attempts, workers=workers)
        state.pop("reason", None)
        state.pop("audit", None)
        if "distinct_catalog_parent_ids" not in state:
            with self.connect() as con:
                state["distinct_catalog_parent_ids"] = con.execute(
                    "SELECT COUNT(DISTINCT catalog_id) FROM parents"
                ).fetchone()[0]
                state["parent_role_counts"] = dict(
                    con.execute("SELECT role,COUNT(*) FROM parents GROUP BY role")
                )
            state["dual_role_parent_ids"] = state["prepared_parents"] - state["distinct_catalog_parent_ids"]
            state["parent_count_scope"] = (
                "prepared_parents counts (catalog_id, source_role); shared natural/drug identities can contribute two rows"
            )
        state.setdefault("lineage_rejected", 0)
        state.setdefault("retained_milestones", [])
        state["screening_filters"] = {
            "mw": [120, 650],
            "logp": [-2, 6],
            "max_tpsa": 180,
            "min_qed": 0.25,
            "max_heavy_atoms": 256,
            "qed_alert_rejection": False,
        }
        state["enumeration_order"] = (
            "BRICS attachment-label pair groups; SHA-256 ordering of unique fragment SMILES within each group"
        )
        state["timing_scope"] = (
            "Accumulated active preparation/generation wall seconds; excludes pauses between run invocations"
        )
        end = min(target_attempts, state["compatible_pair_slots_upper_bound"])
        initial_seconds, started = state["generation_seconds"], time.monotonic()
        with self.connect() as con:
            known_parents = {row[0] for row in con.execute("SELECT DISTINCT smiles FROM parents")}
        single_parent_fragments = self._single_parent_fragments()
        with self.connect() as con:
            self._save(con, state)
        try:
            with ProcessPoolExecutor(
                max_workers=workers, initializer=_init_worker, mp_context=multiprocessing.get_context("spawn")
            ) as executor:
                chunks = self._operation_chunks(state["cursor"], end, batch_size)
                for counts, candidates, cursor in _bounded_parallel(
                    executor, _generate_chunk, chunks, workers
                ):
                    with self.connect() as con:
                        for key, count in counts.items():
                            state[key] += count
                        for candidate in candidates:
                            if candidate[1] in known_parents:
                                state["known_parent_matches"] += 1
                                continue
                            left_parent = single_parent_fragments.get(candidate[2])
                            if left_parent is not None and left_parent == single_parent_fragments.get(
                                candidate[3]
                            ):
                                state["lineage_rejected"] += 1
                                state["rejected"] += 1
                                continue
                            inserted = con.execute(
                                "INSERT OR IGNORE INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                candidate,
                            ).rowcount
                            state["retained_unique" if inserted else "duplicates"] += 1
                        state["cursor"] = cursor
                        state["generation_seconds"] = initial_seconds + time.monotonic() - started
                        achieved = {item["retained_threshold"] for item in state["retained_milestones"]}
                        new_milestones = []
                        for threshold in (1_000_000, 2_000_000, 3_000_000, 5_000_000, 10_000_000):
                            if state["retained_unique"] >= threshold and threshold not in achieved:
                                milestone = {
                                    "retained_threshold": threshold,
                                    "committed_retained_unique": state["retained_unique"],
                                    "attempted": state["attempted"],
                                    "generation_seconds": state["generation_seconds"],
                                    "timing_resolution": f"committed batch boundary; at most {batch_size} attempts per batch",
                                    "measured": True,
                                }
                                state["retained_milestones"].append(milestone)
                                new_milestones.append(threshold)
                        self._save(con, state)
                    report = self._report(state)
                    for threshold in new_milestones:
                        (self.root / f"scale-retained-{threshold}.json").write_text(
                            json.dumps(report, indent=2)
                        )
                    if time.monotonic() - started >= max_seconds:
                        state.update(status="budget_exhausted", reason="runtime_budget")
                        break
                    if (
                        report["database_bytes"] >= max_storage_gb * 1024**3
                        or shutil.disk_usage(self.root).free < reserve_disk_gb * 1024**3
                    ):
                        state.update(status="budget_exhausted", reason="disk_budget")
                        break
                else:
                    state["status"] = "completed" if state["cursor"] >= target_attempts else "exhausted"
        except KeyboardInterrupt:
            state = self.state()
            state["status"] = "paused"
        state["generation_seconds"] = initial_seconds + time.monotonic() - started
        with self.connect() as con:
            self._save(con, state)
        return self._report(state)

    @_serialized
    def audit(self, sample_size=500, persist=True):
        """Materialized SQL accounting plus reproducible independent chemistry replay."""
        if not 1 <= sample_size <= 5000:
            raise ValueError("sample_size must be between 1 and 5000")
        state = self.state()
        digest = hashlib.sha256()
        sampled, chemistry_failures, lineage_failures = 0, 0, 0
        singleton_parents = self._single_parent_fragments()
        distinct_parent_failures = 0
        with self.connect() as con:
            actual, min_rowid, max_rowid = con.execute(
                "SELECT COUNT(*),MIN(rowid),MAX(rowid) FROM candidates"
            ).fetchone()
            fragment_roles = dict(con.execute("SELECT id,role FROM fragments"))
            parented_fragments = {
                row[0]
                for row in con.execute(
                    "SELECT DISTINCT a.fragment_id FROM ancestry a JOIN parents p ON p.catalog_id=a.catalog_id AND p.role=a.role"
                )
            }
            for left, right in con.execute("SELECT left_fragment,right_fragment FROM candidates"):
                missing = (
                    fragment_roles.get(left) != "natural_product"
                    or fragment_roles.get(right) != "drug"
                    or left not in parented_fragments
                    or right not in parented_fragments
                )
                parent = singleton_parents.get(left)
                same_only_parent = parent is not None and parent == singleton_parents.get(right)
                distinct_parent_failures += int(missing or same_only_parent)
            bad_filters = con.execute(
                "SELECT COUNT(*) FROM candidates WHERE mw<120 OR mw>650 OR logp< -2 OR logp>6 OR tpsa>180 OR qed<0.25"
            ).fetchone()[0]
            for row in con.execute("SELECT digest FROM candidates ORDER BY digest"):
                digest.update(row[0])
            top = [
                dict(zip(("smiles", "qed", "left_fragment", "right_fragment"), row))
                for row in con.execute(
                    "SELECT smiles,qed,left_fragment,right_fragment FROM candidates ORDER BY qed DESC,digest LIMIT 20"
                )
            ]
            sample = con.execute(
                "SELECT digest,smiles,left_fragment,right_fragment,attempt_index FROM candidates ORDER BY digest LIMIT ?",
                (sample_size,),
            ).fetchall()
            fragment_checks = {}
            for stored_digest, smiles, left_id, right_id, index in sample:
                sampled += 1
                blocks = []
                for fragment_id, expected_role in ((left_id, "natural_product"), (right_id, "drug")):
                    row = con.execute(
                        "SELECT smiles,label,role FROM fragments WHERE id=?", (fragment_id,)
                    ).fetchone()
                    if row is None or row[2] != expected_role:
                        lineage_failures += 1
                        break
                    blocks.append(row)
                    if fragment_id not in fragment_checks:
                        parent = con.execute(
                            "SELECT p.catalog_id,p.original_smiles,p.role FROM ancestry a JOIN parents p "
                            "ON p.catalog_id=a.catalog_id AND p.role=a.role WHERE a.fragment_id=? LIMIT 1",
                            (fragment_id,),
                        ).fetchone()
                        verified = bool(parent and (row[1], row[0]) in _prepare_chunk([parent])[0][4])
                        fragment_checks[fragment_id] = verified
                    if not fragment_checks[fragment_id]:
                        lineage_failures += 1
                if len(blocks) != 2:
                    continue
                operation = (index, left_id, blocks[0][0], blocks[0][1], right_id, blocks[1][0], blocks[1][1])
                _, reproduced, _ = _generate_chunk([operation])
                if not reproduced or reproduced[0][1] != smiles or reproduced[0][0] != stored_digest:
                    chemistry_failures += 1
        if actual != state["retained_unique"]:
            raise AssertionError("Persisted candidate count differs from committed accounting")
        if bad_filters or chemistry_failures or lineage_failures or distinct_parent_failures:
            raise AssertionError("Persisted structural/filter/lineage audit failed")
        state["audit"] = {
            "actual_unique_rows": actual,
            "rowid_range": [min_rowid, max_rowid],
            "rowids_contiguous": bool(actual and min_rowid == 1 and max_rowid == actual),
            "unique_constraint": "Full canonical isomeric SMILES plus SHA-256 key",
            "sorted_structure_digest_sha256": digest.hexdigest(),
            "all_rows_filter_violations": bad_filters,
            "all_rows_distinct_parent_lineage_failures": distinct_parent_failures,
            "distinct_parent_criterion": "Both ancestry sets are nonempty and are not the same singleton standardized-parent structure set",
            "sample_size": sampled,
            "sample_strategy": "Lowest structure SHA-256 values, deterministic hash sample",
            "sample_chemistry_replay_failures": chemistry_failures,
            "sample_parent_fragment_lineage_failures": lineage_failures,
            "sample_unique_fragments_checked": len(fragment_checks),
            "scope": "All stored row counts/filter columns checked; chemistry and original-parent fragment reconstruction sampled, not full biological validation",
            "top_qed": top,
            "checked_at": _now(),
        }
        if persist:
            with self.connect() as con:
                self._save(con, state)
            return self._report(state)
        # Read-only audit leaves both the SQLite checkpoint and published live
        # progress untouched; callers can write a separate signed-off artifact.
        report = json.loads(self.progress_path.read_text())
        report.update(state)
        report["elapsed_seconds"] = state["preparation_seconds"] + state["generation_seconds"]
        report.setdefault(
            "screening_filters",
            {
                "mw": [120, 650],
                "logp": [-2, 6],
                "max_tpsa": 180,
                "min_qed": 0.25,
                "max_heavy_atoms": 256,
                "qed_alert_rejection": False,
            },
        )
        report.setdefault(
            "enumeration_order",
            "BRICS attachment-label pair groups; SHA-256 ordering of unique fragment SMILES within each group",
        )
        report.setdefault(
            "timing_scope",
            "Accumulated active preparation/generation wall seconds; excludes pauses between run invocations",
        )
        if "projection" in report:
            report["projection"]["limitation"] = (
                "Linear projection of measured prefix only; later fragment-label groups can differ in runtime and filtering yield"
            )
        return report
