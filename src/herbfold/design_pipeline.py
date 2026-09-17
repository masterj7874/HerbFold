"""Durable, local specialist-agent workflow for bounded molecular proposals.

Agents are explicit deterministic computational roles, not language models. Only
stored observations are linked; descriptors never become efficacy predictions.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations, islice
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rdkit import Chem, rdBase
from rdkit.Chem.EnumerateStereoisomers import EnumerateStereoisomers, StereoEnumerationOptions

from . import chemistry
from .protein_targets import TargetRegistry, normalize_accession
from .storage import utcnow

TRANSFORMATIONS = [
    {"id": "o_methylation", "label": "Phenolic O-methylation",
     "description": "Replace one neutral phenolic O–H with O–CH3 per proposal."},
    {"id": "o_acetylation", "label": "Phenolic O-acetylation",
     "description": "Replace one neutral phenolic O–H with O–C(=O)CH3 per proposal."},
    {"id": "stereoisomers", "label": "Resolve unspecified stereochemistry",
     "description": "Enumerate up to 32 unspecified stereochemical assignments; retain specified stereo."},
]
STAGES = [
    ("identity", "Identity and input review", "Identity specialist"),
    ("design", "Bounded candidate design", "Molecular design specialist"),
    ("properties", "Calculated properties", "Physicochemical specialist"),
    ("evidence", "Cached observation linkage", "Evidence specialist"),
    ("review", "Review and report", "Scientific review specialist"),
]
STAGE_DEPENDENCIES = {"identity": [], "design": ["identity"], "properties": ["design"],
                      "evidence": ["design"], "review": ["properties", "evidence"]}
TERMINAL = {"completed", "cancelled", "failed", "interrupted"}
DESCRIPTOR_KEYS = ("molecular_weight", "logp", "tpsa", "hbd", "hba", "qed")
TOX21_ENDPOINTS = {"NR-AR", "NR-AR-LBD", "NR-AhR", "NR-Aromatase", "NR-ER", "NR-ER-LBD",
                   "NR-PPAR-gamma", "SR-ARE", "SR-ATAD5", "SR-HSE", "SR-MMP", "SR-p53"}
LIMITATIONS = [
    "Computational proposals are not synthesized compounds or validated medicines.",
    "No potency, efficacy, human safety, synergy or synthetic feasibility is established.",
    "Cached observations describe their original assays; absence of a match is not inactivity.",
    "Structure identity uses RDKit Cleanup + FragmentParent with charge/stereo retained; original inputs remain in the request.",
    "No external model, AF3, QPU, paid service or language model was called by this pipeline.",
]


class DesignCompound(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    smiles: str = Field(min_length=1, max_length=4096)
    category: Literal["herbal", "natural_product", "drug"]
    source_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_structure(self):
        chemistry.canonical_smiles(self.smiles)
        if self.source_url:
            parsed = urlparse(self.source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Source links require HTTP(S), a hostname and no credentials")
        return self


class DesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="Drug design pipeline", min_length=1, max_length=160)
    mode: Literal["combination", "hybrid", "transform"]
    compounds: list[DesignCompound] = Field(min_length=1, max_length=8)
    target_accession: str = Field(default="P35354", max_length=20)
    max_candidates: int = Field(default=12, ge=1, le=32, strict=True)
    transformations: list[Literal["o_methylation", "o_acetylation", "stereoisomers"]] = Field(
        default_factory=lambda: ["o_methylation", "o_acetylation", "stereoisomers"], max_length=3)
    seed: int = Field(default=42, ge=0, le=2**31 - 1, strict=True)

    @model_validator(mode="after")
    def validate_design(self):
        self.target_accession = normalize_accession(self.target_accession)
        if len({c.id for c in self.compounds}) != len(self.compounds):
            raise ValueError("Compound IDs must be distinct")
        canonical = [chemistry.canonical_smiles(c.smiles) for c in self.compounds]
        if len(set(canonical)) != len(canonical):
            raise ValueError("Choose distinct standardized molecular structures")
        if self.mode in {"combination", "hybrid"} and len(canonical) < 2:
            raise ValueError("Combination and hybrid modes require at least two distinct compounds")
        if self.mode == "hybrid" and not ({c.category for c in self.compounds} & {"herbal", "natural_product"}
                                           and any(c.category == "drug" for c in self.compounds)):
            raise ValueError("Hybrid design requires a natural/herbal parent and a drug parent")
        if len(set(self.transformations)) != len(self.transformations):
            raise ValueError("Transformations must be distinct")
        if self.mode == "transform" and not self.transformations:
            raise ValueError("Choose at least one transformation")
        return self


def _identifier(kind, value):
    return kind + "-" + hashlib.sha256(value.encode()).hexdigest()[:16]


def _parent(row):
    return {key: row[key] for key in ("id", "name", "smiles", "category", "source_url") if key in row}


def _candidate(kind, smiles, parents, provenance):
    return {"id": _identifier(kind, smiles), "name": f"{kind.title()} proposal", "kind": kind,
            "smiles": smiles, "parents": [_parent(p) for p in parents], "provenance": provenance,
            "evidence": [], "expected_effects": [], "limitations": list(LIMITATIONS)}


def design_candidates(request: DesignRequest, parents: list[dict], checkpoint=lambda: None, audit=None) -> list[dict]:
    """Enumerate real graphs or separate-constituent pairs; never invent a hit."""
    if request.mode == "combination":
        result = []
        for pair in islice(combinations(parents, 2), request.max_candidates):
            checkpoint()
            result.append({"id": _identifier("combination", "|".join(sorted(p["smiles"] for p in pair))),
                           "name": " + ".join(p["name"] for p in pair), "kind": "combination",
                           "parents": [_parent(p) for p in pair], "components": [_parent(p) for p in pair],
                           "evidence": [], "expected_effects": [
                               "Constituents remain separate molecules. Their combined biological effect is unknown."],
                           "limitations": [*LIMITATIONS, "No dose, ratio, interaction or synergy was evaluated."],
                           "provenance": {"method": "Unordered pairs of distinct selected constituents",
                                          "joint_structure_generated": False}})
        return result
    if request.mode == "hybrid":
        lookup = {p["smiles"]: p for p in parents}
        result, seen = [], set()
        pairs = [(left, right) for left in parents if left["category"] in {"herbal", "natural_product"}
                 for right in parents if right["category"] == "drug"]
        for left, right in pairs:
            checkpoint()
            rows = chemistry.generate_candidates([left["smiles"], right["smiles"]], request.max_candidates)
            for row in rows:
                if row["smiles"] in lookup or row["smiles"] in seen:
                    continue
                seen.add(row["smiles"])
                result.append(_candidate("hybrid", row["smiles"], [lookup[s] for s in row["parents"]], {
                    "method": row["method"], "parent_fragments": row["parent_fragments"],
                    "novelty_scope": row["novelty_scope"], "both_parent_fragments_verified": True}))
                if len(result) >= request.max_candidates:
                    return result
        return result
    products = {}
    parent_structures = {p["smiles"] for p in parents}
    for parent in parents:
        checkpoint()
        mol = chemistry.standardize_molecule(parent["smiles"])
        for transform in request.transformations:
            checkpoint()
            variants = []
            if transform == "stereoisomers":
                opts = StereoEnumerationOptions(onlyUnassigned=True, unique=True, maxIsomers=32, rand=request.seed)
                variants = [(item, None) for item in EnumerateStereoisomers(mol, options=opts)]
            else:
                # Eligible site is a neutral terminal phenol, not an acid/alcohol/ether oxygen.
                sites = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 8
                         and a.GetFormalCharge() == 0 and a.GetTotalNumHs() == 1 and a.GetDegree() == 1
                         and a.GetNeighbors()[0].GetIsAromatic() and a.GetNeighbors()[0].GetAtomicNum() == 6]
                for site in sites[:32]:
                    edited = Chem.RWMol(mol)
                    oxygen = edited.GetAtomWithIdx(site)
                    oxygen.SetNumExplicitHs(0)
                    oxygen.SetNoImplicit(True)
                    carbon = edited.AddAtom(Chem.Atom(6))
                    edited.AddBond(site, carbon, Chem.BondType.SINGLE)
                    if transform == "o_acetylation":
                        oxo = edited.AddAtom(Chem.Atom(8))
                        methyl = edited.AddAtom(Chem.Atom(6))
                        edited.AddBond(carbon, oxo, Chem.BondType.DOUBLE)
                        edited.AddBond(carbon, methyl, Chem.BondType.SINGLE)
                    variants.append((edited.GetMol(), site))
            for product, site in variants:
                checkpoint()
                try:
                    Chem.SanitizeMol(product)
                    smiles = chemistry.canonical_smiles(Chem.MolToSmiles(product, isomericSmiles=True))
                except (ValueError, RuntimeError) as exc:
                    if audit is not None:
                        audit["skipped_variant_count"] += 1
                        if len(audit["skipped_variants"]) < 100:
                            audit["skipped_variants"].append({"parent_id": parent["id"], "transformation": transform,
                                                               "atom_index": site, "reason": str(exc)[:200]})
                    continue
                if smiles in parent_structures or smiles in products:
                    continue
                products[smiles] = _candidate("transform", smiles, [parent], {
                    "method": transform, "parent_atom_index": site,
                    "atom_index_scope": "RDKit canonical parent graph; zero-based",
                    "single_step": True, "rdkit_version": rdBase.rdkitVersion,
                    "novelty_scope": "Distinct from selected input structures only"})
                if len(products) >= request.max_candidates:
                    return list(products.values())
    return list(products.values())


class CachedObservations:
    """Read-only exact joins to canonical curated records, never parent propagation."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.RLock()
        self._signature = None
        self._biology = {}
        self._pathways = {}
        self.sources = []
        self.notes = []

    def _refresh(self):
        paths = [self.root / "bio-validation/curated-records.json", self.root / "tox21/curated-labels.jsonl"]
        signature = tuple((p.stat().st_size, p.stat().st_mtime_ns) if p.is_file() else None for p in paths)
        if signature == self._signature:
            return
        biology, pathways, sources, notes = {}, {}, [], []
        for kind, path in zip(("target", "pathway"), paths, strict=True):
            if not path.is_file():
                notes.append(f"Cached {kind} observations are unavailable.")
                continue
            if path.stat().st_size > 64 * 1024 * 1024:
                notes.append(f"Cached {kind} file exceeds the 64 MiB local read bound; not linked.")
                continue
            try:
                raw = path.read_bytes()
                rows = json.loads(raw) if kind == "target" else [json.loads(line) for line in raw.splitlines() if line.strip()]
                if not isinstance(rows, list) or len(rows) > 200_000:
                    raise ValueError("Invalid or oversized cached record list")
                dest = biology if kind == "target" else pathways
                for row in rows:
                    if isinstance(row, dict) and isinstance(row.get("smiles"), str):
                        dest.setdefault(row["smiles"], []).append(row)
                sources.append({"file": str(path.relative_to(self.root)), "sha256": hashlib.sha256(raw).hexdigest(),
                                "records": len(rows), "scope": kind})
            except (ValueError, OSError):
                notes.append(f"Cached {kind} observations could not be parsed; no values inferred.")
        self._biology, self._pathways = biology, pathways
        self.sources, self.notes, self._signature = sources, notes, signature

    def lookup(self, smiles: str, target_accession: str) -> list[dict]:
        canonical = chemistry.canonical_smiles(smiles)
        with self._lock:
            self._refresh()
            observations = []
            target_ids = {"PTGS2": "P35354", "KCNH2": "Q12809"}
            for row in self._biology.get(canonical, []):
                accession = row.get("target_accession") or row.get("uniprot") or target_ids.get(row.get("target"))
                value = row.get("value_nM")
                if (accession != target_accession or row.get("endpoint") not in {"Ki", "Kd", "IC50"}
                        or row.get("status") not in {None, "observed", "exact_measured"}
                        or row.get("relation", "=") != "=" or row.get("standard_relation", "=") != "="
                        or (row.get("target") in target_ids and target_ids[row["target"]] != accession)
                        or type(value) not in {int, float} or not math.isfinite(value) or value <= 0
                        or not row.get("assay_id") or not row.get("source_url")):
                    continue
                item = {"status": "observed", "kind": "target_assay", "smiles": canonical,
                        "target_accession": accession, "endpoint": row["endpoint"], "value": value,
                        "unit": "nM", "relation": "=", "assay_id": row["assay_id"],
                        "source_url": row["source_url"], "source_ids": row.get("activity_ids", []),
                        "assay_description": row.get("assay_description"), "assay_type": row.get("assay_type"),
                        "match_scope": "Exact cached canonical ligand and selected target; assays are not pooled"}
                pactivity = row.get("pactivity")
                if type(pactivity) in {float, int} and math.isfinite(pactivity):
                    item["pactivity"] = pactivity
                observations.append(item)
            for row in self._pathways.get(canonical, []):
                labels = row.get("labels", {})
                if not isinstance(labels, dict):
                    continue
                for endpoint, label in sorted(labels.items()):
                    if endpoint in TOX21_ENDPOINTS and type(label) is int and label in {0, 1}:
                        observations.append({"status": "observed", "kind": "pathway_assay", "smiles": canonical,
                                             "target_accession": None, "endpoint": endpoint, "label": label,
                                             "source_ids": row.get("source_ids", []),
                                             "source_url": "https://tripod.nih.gov/tox21/challenge/",
                                             "match_scope": "Exact ligand; Tox21 pathway label, not selected-target activity"})
            return observations[:200]


class DesignPipeline:
    """Persist every lifecycle/event transition; parallel specialists use separate workers."""

    def __init__(self, store, pool=None):
        self.store = store
        # Hold one ownership lease for this runtime for the whole engine lifetime.
        # A second service must not recover or close the first service's live runs.
        # Never unlink this file: waiters must continue locking the same inode.
        self._lease = (store.root / "design-pipeline.lock").open("a+b")
        try:
            fcntl.flock(self._lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lease.close()
            raise RuntimeError(
                "This runtime already has an active design-pipeline owner. "
                "Use one application worker per runtime directory; stop the existing service before restarting."
            ) from exc
        self._owns_pool = pool is None
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.pool = self.specialists = None
        try:
            self.pool = pool or ThreadPoolExecutor(max_workers=2, thread_name_prefix="herbfold-design")
            self.specialists = ThreadPoolExecutor(max_workers=2, thread_name_prefix="herbfold-specialist")
            self.observations = CachedObservations(store.root / "validation")
            with store.connect() as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS design_pipeline_runs (id TEXT PRIMARY KEY, created TEXT, state TEXT NOT NULL)")
            self.recover()
        except Exception:
            if self.specialists is not None:
                self.specialists.shutdown(wait=False, cancel_futures=True)
            if self._owns_pool and self.pool is not None:
                self.pool.shutdown(wait=False, cancel_futures=True)
            self._lease.close()
            raise

    def options(self):
        return {"modes": [
            {"id": "combination", "label": "Separate-constituent combinations",
             "description": "Pair original constituents; retain individual properties and observations."},
            {"id": "hybrid", "label": "BRICS hybrid proposals",
             "description": "Generate bounded recombinations with verified fragments from both parents."},
            {"id": "transform", "label": "Single-step structural transformations",
             "description": "Enumerate phenolic substitutions or unspecified stereochemistry."}],
                "transformations": TRANSFORMATIONS, "targets": TargetRegistry(self.store).list(),
                "stages": [{"id": i, "label": label, "agent": agent, "depends_on": STAGE_DEPENDENCIES[i]}
                           for i, label, agent in STAGES],
                "limits": {"max_compounds": 8, "max_candidates": 32, "max_active_runs": 4},
                "default_target": "P35354", "execution": "deterministic_specialist_agents",
                "limitations": LIMITATIONS}

    def _get(self, run_id):
        with self.store.connect() as connection:
            row = connection.execute("SELECT state FROM design_pipeline_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("Design pipeline run not found")
        return json.loads(row[0])

    def get(self, run_id):
        with self.lock:
            return self._get(run_id)

    def list(self, limit=50):
        with self.store.connect() as connection:
            rows = connection.execute("SELECT state FROM design_pipeline_runs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _save(self, state):
        state["updated"] = utcnow()
        with self.store.connect() as connection:
            connection.execute("UPDATE design_pipeline_runs SET state=? WHERE id=?",
                               (json.dumps(state, ensure_ascii=False, allow_nan=False), state["id"]))

    @staticmethod
    def _event(state, stage, status, message):
        state["events"].append({"seq": len(state["events"]) + 1, "stage": stage,
                                "status": status, "message": message, "time": utcnow()})

    def _stage(self, run_id, stage_id, status, summary):
        with self.lock:
            state = self._get(run_id)
            if state["status"] in TERMINAL:
                raise InterruptedError(state["status"])
            stage = next(s for s in state["stages"] if s["id"] == stage_id)
            stage.update(status=status, summary=summary)
            stage["started" if status == "running" else "finished"] = utcnow()
            self._event(state, stage_id, status, summary)
            self._save(state)

    def _terminal(self, run_id, status, error=None):
        with self.lock:
            state = self._get(run_id)
            if state["status"] in TERMINAL:
                return state
            state.update(status=status, error=error)
            for stage in state["stages"]:
                if stage["status"] in {"pending", "running"}:
                    stage.update(status=status, finished=utcnow(), summary=error or status)
            self._event(state, "run", status, error or status)
            self._save(state)
            return state

    def recover(self):
        with self.store.connect() as connection:
            rows = connection.execute("SELECT id,state FROM design_pipeline_runs").fetchall()
        for row in rows:
            if json.loads(row["state"])["status"] not in TERMINAL:
                self._terminal(row["id"], "interrupted", "Server restarted; unfinished work was interrupted. Create a new run to retry.")

    def create(self, request: DesignRequest, *, background=True):
        with self.lock:
            if self.stopping.is_set():
                raise ValueError("Design pipeline is shutting down")
            with self.store.connect() as connection:
                active = connection.execute("SELECT count(*) FROM design_pipeline_runs WHERE json_extract(state,'$.status') IN ('queued','running')").fetchone()[0]
            if active >= 4:
                raise ValueError("Four design runs are already active; wait or cancel one")
            now, run_id = utcnow(), uuid.uuid4().hex
            state = {"id": run_id, "name": request.name, "status": "queued", "created": now, "updated": now,
                     "request": request.model_dump(), "stages": [{"id": i, "label": label, "agent": agent,
                         "status": "pending", "summary": "Waiting"} for i, label, agent in STAGES],
                     "events": [], "result": None, "error": None,
                     "execution": "deterministic_specialist_agents"}
            self._event(state, "run", "queued", "Local deterministic specialist workflow queued")
            with self.store.connect() as connection:
                connection.execute("INSERT INTO design_pipeline_runs VALUES(?,?,?)", (run_id, now, json.dumps(state)))
            if background:
                try:
                    self.pool.submit(self.run, run_id)
                except RuntimeError:
                    self._terminal(run_id, "interrupted", "Worker pool unavailable; create a new run to retry.")
        if not background:
            self.run(run_id)
        return self.get(run_id)

    def cancel(self, run_id):
        return self._terminal(run_id, "cancelled", "Cancelled by the user; any in-flight bounded computation will be discarded.")

    def close(self):
        with self.lock:
            # An old owner's repeated close must not touch its successor's runs.
            if self.stopping.is_set():
                return
            self.stopping.set()
            try:
                with self.store.connect() as connection:
                    rows = connection.execute(
                        "SELECT id FROM design_pipeline_runs WHERE json_extract(state,'$.status') IN ('queued','running')"
                    ).fetchall()
                for row in rows:
                    self._terminal(row["id"], "interrupted", "Server stopped during the workflow; create a new run to retry.")
            finally:
                self.specialists.shutdown(wait=False, cancel_futures=True)
                if self._owns_pool:
                    self.pool.shutdown(wait=False, cancel_futures=True)
                self._lease.close()

    shutdown = close

    def _checkpoint(self, run_id):
        if self.stopping.is_set() or self.get(run_id)["status"] in TERMINAL:
            raise InterruptedError("Run no longer active")

    def _properties(self, run_id, candidates, parents):
        self._stage(run_id, "properties", "running", "Calculating descriptors and parent-relative changes")
        parent_desc = {p["id"]: chemistry.describe_molecule(p["smiles"]) for p in parents}
        result = {}
        for candidate in candidates:
            self._checkpoint(run_id)
            if candidate["kind"] == "combination":
                result[candidate["id"]] = {"components": {p["id"]: parent_desc[p["id"]] for p in candidate["parents"]}}
            else:
                desc = chemistry.describe_molecule(candidate["smiles"])
                delta = {p["id"]: {key: round(desc[key] - parent_desc[p["id"]][key], 6)
                                   for key in DESCRIPTOR_KEYS} for p in candidate["parents"]}
                effects = [f"Relative to {p['name']}: calculated molecular weight change {delta[p['id']]['molecular_weight']:+.4f} g/mol; "
                           f"logP change {delta[p['id']]['logp']:+.4f}; TPSA change {delta[p['id']]['tpsa']:+.4f} Å²."
                           for p in candidate["parents"]]
                result[candidate["id"]] = {"descriptors": desc, "descriptor_delta": delta, "expected_effects": effects}
        self._stage(run_id, "properties", "completed", f"Calculated properties for {len(candidates)} proposals; biological effect remains unknown")
        return result

    def _evidence(self, run_id, candidates, target):
        self._stage(run_id, "evidence", "running", "Joining exact candidate identities to cached observed assays")
        result = {}
        for candidate in candidates:
            self._checkpoint(run_id)
            if candidate["kind"] == "combination":
                result[candidate["id"]] = {"components": {p["id"]: self.observations.lookup(p["smiles"], target)
                                                         for p in candidate["parents"]}}
            else:
                result[candidate["id"]] = {"evidence": self.observations.lookup(candidate["smiles"], target)}
        self._stage(run_id, "evidence", "completed", "Exact cached joins finished; parent observations were not transferred to modified structures")
        return result

    def run(self, run_id):
        with self.lock:
            state = self._get(run_id)
            if state["status"] != "queued":
                return
            state["status"] = "running"
            self._event(state, "run", "running", "Executing named local computational specialists")
            self._save(state)
        try:
            request = DesignRequest.model_validate(state["request"])
            self._stage(run_id, "identity", "running", "Validating structures, categories, IDs and target accession")
            parents = [{**c.model_dump(), "input_smiles": c.smiles, "smiles": chemistry.canonical_smiles(c.smiles)} for c in request.compounds]
            self._stage(run_id, "identity", "completed", f"Validated {len(parents)} distinct canonical input structures")
            self._stage(run_id, "design", "running", f"Executing {request.mode} design with a {request.max_candidates}-proposal bound")
            design_audit = {"skipped_variant_count": 0, "skipped_variants": [],
                            "scope": "Invalid/oversized graph edits are skipped individually; accepted candidates remain available"}
            candidates = design_candidates(request, parents, lambda: self._checkpoint(run_id), design_audit)
            self._stage(run_id, "design", "completed", f"Generated {len(candidates)} valid proposals; requested count is a cap, not a guarantee")
            properties = self.specialists.submit(self._properties, run_id, candidates, parents)
            evidence = self.specialists.submit(self._evidence, run_id, candidates, request.target_accession)
            property_rows, evidence_rows = properties.result(), evidence.result()
            self._stage(run_id, "review", "running", "Reviewing lineage, observed-evidence scope and unknowns")
            for index, candidate in enumerate(candidates, 1):
                self._checkpoint(run_id)
                p, e = property_rows[candidate["id"]], evidence_rows[candidate["id"]]
                if candidate["kind"] == "combination":
                    for component in candidate["components"]:
                        component["descriptors"] = p["components"][component["id"]]
                        component["evidence"] = e["components"][component["id"]]
                    candidate["evidence_status"] = "component_observations_only"
                else:
                    candidate.update(p, **e)
                    candidate["name"] = f"{request.mode.title()} {index}: {candidate['parents'][0]['name']}"
                    candidate["evidence_status"] = "exact_observations_available" if candidate["evidence"] else "no_exact_cached_observation"
                    candidate["expected_effects"].append("Changes in potency, efficacy, absorption and safety are unknown.")
            limitations = list(LIMITATIONS)
            if not candidates:
                limitations.append("No structures passed the selected bounded design rules. No substitute candidate was invented.")
            result = {"candidates": candidates, "parents": parents,
                      "summary": f"{len(candidates)} {request.mode} proposals reviewed from {len(parents)} input structures.",
                      "limitations": limitations, "evidence_sources": self.observations.sources,
                      "evidence_notes": [*self.observations.notes, "At most 200 observed records per structure are displayed; cached availability is not exhaustive."],
                      "target_accession": request.target_accession, "rdkit_version": rdBase.rdkitVersion,
                      "design_audit": design_audit,
                      "execution": {"agent_kind": "deterministic_specialist_agents", "parallel_stages": ["properties", "evidence"],
                                    "seed": request.seed, "seed_scope": "Stereoisomer subsampling only; BRICS and site edits are deterministic",
                                    "external_calls": 0, "llm_calls": 0}}
            self._stage(run_id, "review", "completed", "Report complete; computational changes and cached observations remain separate")
            with self.lock:
                state = self._get(run_id)
                if state["status"] not in TERMINAL:
                    state.update(status="completed", result=result)
                    self._event(state, "run", "completed", result["summary"])
                    self._save(state)
        except InterruptedError:
            self._terminal(run_id, "interrupted", "Workflow interrupted before completion")
        except Exception as exc:
            self._terminal(run_id, "failed", f"{type(exc).__name__}: {str(exc)[:600]}")
