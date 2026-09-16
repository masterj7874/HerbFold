"""Resolve a selected molecular identity to matching observed complex artifacts.

Identity is exact canonical isomeric SMILES, without neutralization, tautomer
merging or salt removal. Artifact names never identify proteins or ligands.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from fastapi import HTTPException
from rdkit import Chem

from . import molecular

_AF3_KINDS = ("alphafold", "alphafold_smoke", "af3_import")
_MAX_MODEL_CHECKS = 50
_AMINO = dict(
    zip(
        (
            "ALA",
            "CYS",
            "ASP",
            "GLU",
            "PHE",
            "GLY",
            "HIS",
            "ILE",
            "LYS",
            "LEU",
            "MET",
            "ASN",
            "PRO",
            "GLN",
            "ARG",
            "SER",
            "THR",
            "VAL",
            "TRP",
            "TYR",
        ),
        "ACDEFGHIKLMNPQRSTVWY",
        strict=True,
    )
)
_MISSING = {"", ".", "?"}


def exact_identity(smiles: str) -> str:
    return Chem.MolToSmiles(Chem.RemoveHs(molecular._small_molecule(smiles)), isomericSmiles=True)


def _data_file(name):
    for root in (Path(__file__).resolve().parents[2] / "data", Path(__file__).resolve().parent / "data"):
        path = root / name
        if path.is_file():
            return path
    return None


def _target_sequence(accession):
    path = _data_file("ptgs2.json")
    if not path:
        return None
    record = json.loads(path.read_text())
    return record["sequence"] if record.get("accession") == accession else None


def registered_target(store, accession):
    """Read a previously registered target without performing network I/O."""
    from .protein_targets import TargetRegistry

    try:
        return TargetRegistry(store).get(accession)
    except KeyError:
        return None


def canonical_target_accession(store, accession):
    accession = accession.strip().upper()
    target = registered_target(store, accession)
    return target["accession"] if target else accession


def _studio_request(store, job_id):
    """Legacy/imported jobs may predate the molecular studio index."""
    with closing(store.connect()) as con:
        try:
            row = con.execute(
                "SELECT request FROM studio_predictions WHERE job_id=?", (job_id,)
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table: studio_predictions" in str(exc):
                return None
            raise
    if row is None:
        return None
    request = json.loads(row[0])
    return request if isinstance(request, dict) else {}


def frozen_target_sequence(job, requested):
    """Validate the original input against its recorded hash, never today's registry."""
    digest = requested.get("target_sequence_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None
    provenance = requested.get("target_provenance")
    if provenance is not None and (
        not isinstance(provenance, dict)
        or provenance.get("accession") != requested.get("target_accession")
        or provenance.get("sequence_sha256") != digest
    ):
        return None
    entities = (job.get("payload") or {}).get("sequences") or []
    sequences = {
        entity["protein"].get("sequence")
        for entity in entities
        if isinstance(entity, dict)
        and isinstance(entity.get("protein"), dict)
        and isinstance(entity["protein"].get("sequence"), str)
    }
    matching = [
        sequence for sequence in sequences if hashlib.sha256(sequence.encode()).hexdigest() == digest
    ]
    return matching[0] if len(matching) == 1 else None


def reference_catalog(target_accession="P35354"):
    path = _data_file("molecular_references.json")
    if not path:
        return []
    rows = json.loads(path.read_text()).get("references", [])
    return [
        row
        for row in rows
        if row.get("target_accession") == target_accession
        and re.fullmatch(r"[0-9][A-Z0-9]{3}", row.get("pdb_id", ""))
        and re.fullmatch(r"[A-Z0-9]{1,3}", row.get("ligand_component", ""))
    ][:30]


def available_references(target_accession="P35354"):
    return [
        {
            "id": f"pdb-{row['pdb_id']}-{row['ligand_component']}",
            "name": row["ligand"],
            "label": f"{row['ligand']} · {row['pdb_id']}",
            "smiles": row["ligand_smiles"],
            "category": "drug",
            "source_status": "experimental_ligand_reference_not_current_approval_status",
            "note": "실험 구조의 대조 리간드입니다. 현재 판매 허가 상태나 선택 후보의 구조를 뜻하지 않습니다.",
            "pdb_id": row["pdb_id"],
            "target_accession": row["target_accession"],
            "ligand_component": row["ligand_component"],
            "source_url": row["source_url"],
            "construct": row.get("construct", {}),
        }
        for row in reference_catalog(target_accession)
    ]


def _rows(cif, prefix, keys):
    count = len(cif.get(prefix + keys[0], []))
    return zip(*(molecular._column(cif, prefix + key, count, required=True) for key in keys), strict=True)


def _chain_matches(scene, chain, sequence, *, complete, cif=None):
    mapping = dict(_AMINO)
    if cif:
        for name, parent in zip(
            cif.get("_chem_comp.id", []), cif.get("_chem_comp.mon_nstd_parent_comp_id", [])
        ):
            if parent in mapping:
                mapping[name] = mapping[parent]
    seen = {}
    for residue in scene["residues"]:
        if residue["chain_id"] != chain or residue.get("sequence_id") is None:
            continue
        position = str(residue["sequence_id"])
        if not position.isdigit() or not 1 <= int(position) <= len(sequence):
            return False
        letter = mapping.get(residue["name"])
        if letter is None or letter != sequence[int(position) - 1] or int(position) in seen:
            return False
        seen[int(position)] = letter
    return bool(seen) and (not complete or set(seen) == set(range(1, len(sequence) + 1)))


def _groups(scene):
    grouped = defaultdict(list)
    for atom in scene["atoms"]:
        if atom["is_ligand"]:
            grouped[(atom["chain_id"], atom["residue_id"], atom["residue_name"])].append(atom)
    return grouped


def _graph_matches(scene, atoms, expected_atoms, expected_bonds, expected_charges=None):
    heavy = [atom for atom in atoms if atom["element"] not in {"H", "D"}]
    names = {atom["name"]: atom["element"] for atom in heavy}
    if names != expected_atoms or len(names) != len(heavy):
        return False
    indices = {atom["id"]: atom["name"] for atom in heavy}
    all_indices = {atom["id"] for atom in atoms}
    for atom in heavy:
        charge = atom.get("formal_charge")
        expected_charge = (expected_charges or {}).get(atom["name"])
        if charge is not None and expected_charge is not None and charge != expected_charge:
            return False
    for bond in scene["bonds"]:
        left, right = bond["source"], bond["target"]
        if (left in all_indices) != (right in all_indices):
            # A covalent adduct is not the selected intact free-ligand graph.
            return False
        if left in indices and right in indices and not bond["bond_order_known"]:
            return False
    observed = {
        (tuple(sorted((indices[bond["source"]], indices[bond["target"]]))), round(bond["order"], 3))
        for bond in scene["bonds"]
        if bond["source"] in indices and bond["target"] in indices and bond["bond_order_known"]
    }
    expected = {
        (tuple(sorted((left, right))), round(order, 3))
        for left, right, order, *_ in expected_bonds
        if left in names and right in names
    }
    return observed == expected


def _af3_ligand_groups(scene, cif, requested):
    components = dict(_rows(cif, "_chem_comp.", ("id", "pdbx_smiles")))
    selected = []
    for (chain, residue, component), atoms in _groups(scene).items():
        smiles = components.get(component)
        if not smiles or smiles in _MISSING:
            continue
        try:
            if exact_identity(smiles) != requested:
                continue
            bonds = molecular._af3_smiles_template(smiles, atoms)
            mol = molecular._small_molecule(smiles)
            counter, expected, charges = Counter(), {}, {}
            for atom in mol.GetAtoms():
                symbol = atom.GetSymbol()
                counter[symbol] += 1
                if symbol not in {"H", "D"}:
                    name = f"{symbol.upper()}{counter[symbol]}"
                    expected[name] = symbol
                    charges[name] = atom.GetFormalCharge()
            if bonds is not None and _graph_matches(scene, atoms, expected, bonds, charges):
                selected.append((chain, residue, component, atoms))
        except (ValueError, RuntimeError):
            continue
    return selected


def _selection_metadata(scene, canonical, accession, target_chains, ligands, **extra):
    scene["metadata"].update(
        smiles=canonical,
        selected_ligand_atom_ids=sorted(atom["id"] for _, _, _, atoms in ligands for atom in atoms),
        selection={
            "canonical_smiles": canonical,
            "target_accession": accession,
            "target_chains": sorted(target_chains),
            "ligand_chains": sorted({row[0] for row in ligands}),
            "ligand_residues": [
                {"chain_id": c, "residue_id": r, "component": comp} for c, r, comp, _ in ligands
            ],
            "identity_policy": "Exact canonical isomeric SMILES; charge/stereo retained; observed full ligand atom/bond graph checked",
            **extra,
        },
    )


def _saved_jobs(store, maximum=5000):
    after = None
    remaining = maximum
    while remaining:
        with closing(store.connect()) as con:
            clause = " AND (created<? OR (created=? AND id<?))" if after else ""
            rows = con.execute(
                "SELECT id,created FROM jobs WHERE kind IN (?,?,?) AND status='completed'"
                + clause
                + " ORDER BY created DESC,id DESC LIMIT ?",
                (*_AF3_KINDS, *((after[0], after[0], after[1]) if after else ()), min(100, remaining)),
            ).fetchall()
        if not rows:
            return
        for row in rows:
            yield store.get(row["id"])
        remaining -= len(rows)
        after = (rows[-1]["created"], rows[-1]["id"])


def _resolve_saved(store, canonical, accession, sequence, *, jobs=None):
    from .molecular_api import _job_scene

    matches, selected_scene, scanned, checked = [], None, 0, 0
    for job in _saved_jobs(store) if jobs is None else jobs:
        if job.get("status") != "completed" or job.get("kind") not in _AF3_KINDS:
            continue
        if (job.get("result") or {}).get("prediction_eligible") is False:
            continue
        scanned += 1
        studio_request = _studio_request(store, job["id"])
        if studio_request is not None:
            if (
                studio_request.get("target_accession") != accession
                or studio_request.get("canonical_smiles") != canonical
            ):
                continue
            job_sequence = frozen_target_sequence(job, studio_request)
        else:
            job_sequence = sequence
        if not job_sequence:
            continue
        # A payload can exclude a known mismatch early, but never proves the
        # output identity. Imported artifacts may have no input payload at all.
        entities = job.get("payload", {}).get("sequences")
        if entities:
            ligands = [entity["ligand"].get("smiles", "") for entity in entities if "ligand" in entity]
            proteins = [entity["protein"].get("sequence", "") for entity in entities if "protein" in entity]
            try:
                if job_sequence not in proteins or not any(
                    exact_identity(value) == canonical for value in ligands
                ):
                    continue
            except (ValueError, RuntimeError):
                continue
        registered = (job.get("result") or {}).get("models") or []
        if not isinstance(registered, list):
            continue
        models = sorted(
            [model for model in registered if isinstance(model, dict)],
            key=lambda model: (
                not model.get("is_top_ranked_copy", False),
                str(model.get("structure_path", "")),
            ),
        )
        for model in models[:20]:
            if checked >= _MAX_MODEL_CHECKS:
                return selected_scene, matches, scanned, checked, True
            relative = model.get("structure_path", "")
            if not isinstance(relative, str):
                continue
            checked += 1
            try:
                scene = _job_scene(store, job["id"], "output/" + relative)
                path = store.artifact(job["id"], scene["metadata"]["artifact"])
                cif = molecular.read_cif(path)
                if hashlib.sha256(path.read_bytes()).hexdigest() != scene["metadata"]["sha256"]:
                    continue
                target_chains = [
                    chain["id"]
                    for chain in scene["chains"]
                    if _chain_matches(scene, chain["id"], job_sequence, complete=True, cif=cif)
                ]
                matched_ligands = _af3_ligand_groups(scene, cif, canonical)
                if not target_chains or not matched_ligands:
                    continue
                _selection_metadata(
                    scene,
                    canonical,
                    accession,
                    target_chains,
                    matched_ligands,
                    target_identity=(
                        "Full observed protein sequence equals the original AF3 input and its registered sequence hash"
                        if studio_request is not None
                        else "Full observed protein sequence equals the pinned UniProt sequence"
                    ),
                    target_sequence_sha256=hashlib.sha256(job_sequence.encode()).hexdigest(),
                    target_provenance=(studio_request or {}).get("target_provenance"),
                )
                execution = (
                    "AlphaFold 3"
                    if scene["metadata"]["execution_verified"]
                    else "AF3 형식 가져오기 · 실행 미검증"
                )
                scene["label"] = f"{execution} · {accession} · 선택 분자 일치"
                matches.append(
                    {
                        "id": job["id"],
                        "job_id": job["id"],
                        "label": scene["label"],
                        "artifact": scene["metadata"]["artifact"],
                        "source_url": f"/api/jobs/{job['id']}/artifacts/{scene['metadata']['artifact']}",
                    }
                )
                if selected_scene is None:
                    selected_scene = scene
                break
            except (ValueError, RuntimeError, OSError, HTTPException, KeyError, IndexError):
                continue
        if len(matches) >= 5:
            break
    return selected_scene, matches, scanned, checked, scanned >= 5000


def _experimental_target_chains(scene, cif, reference):
    accession = reference["target_accession"]
    entities = {
        entity
        for db, acc, entity in _rows(cif, "_struct_ref.", ("db_name", "pdbx_db_accession", "entity_id"))
        if db == "UNP" and acc == accession and entity == reference.get("target_entity_id")
    }
    sequences = {
        entity: re.sub(r"\s+", "", seq)
        for entity, seq in _rows(cif, "_entity_poly.", ("entity_id", "pdbx_seq_one_letter_code_can"))
    }
    expected_hash = reference.get("construct", {}).get("canonical_construct_sequence_sha256")
    chains = []
    for chain, entity in _rows(cif, "_struct_asym.", ("id", "entity_id")):
        sequence = sequences.get(entity, "")
        if (
            entity in entities
            and sequence
            and expected_hash
            and hashlib.sha256(sequence.encode()).hexdigest() == expected_hash
        ):
            if _chain_matches(scene, chain, sequence, complete=False, cif=cif):
                chains.append(chain)
    return chains


def _experimental_ligands(store, scene, reference, canonical):
    component = reference["ligand_component"]
    path = store.root / "reference_structures" / f"CCD_{component}.cif"
    if not path.is_file() or path.stat().st_size > 2_000_000:
        return []
    ccd = MMCIF2Dict(str(path))
    if ccd.get("_chem_comp.id", [""])[0] != component:
        return []
    identities = []
    for kind, smiles in _rows(ccd, "_pdbx_chem_comp_descriptor.", ("type", "descriptor")):
        if kind.upper() == "SMILES_CANONICAL":
            try:
                identities.append(exact_identity(smiles))
            except (ValueError, RuntimeError):
                pass
    if canonical not in identities:
        return []
    expected = {
        name: symbol.capitalize()
        for name, symbol in _rows(ccd, "_chem_comp_atom.", ("atom_id", "type_symbol"))
        if symbol.upper() not in {"H", "D"}
    }
    charges = {}
    for name, charge in zip(ccd.get("_chem_comp_atom.atom_id", []), ccd.get("_chem_comp_atom.charge", [])):
        if charge not in _MISSING:
            charges[name] = int(charge)
    bonds = molecular.ccd_templates(ccd).get(component)
    if not expected or bonds is None:
        return []
    return [
        (chain, residue, comp, atoms)
        for (chain, residue, comp), atoms in _groups(scene).items()
        if comp == component
        and chain in reference.get("ligand_chains", [])
        and _graph_matches(scene, atoms, expected, bonds, charges)
    ]


def resolve(store, smiles, source, target_accession="P35354", *, job_id=None):
    if source not in {"experimental_pdb", "alphafold3_prediction"}:
        raise ValueError("Unsupported molecular complex source")
    if job_id is not None and source != "alphafold3_prediction":
        raise ValueError("A saved job can only resolve AlphaFold prediction artifacts")
    canonical = exact_identity(smiles)
    direct_job = store.get(job_id) if job_id is not None else None
    direct_request = _studio_request(store, job_id) if direct_job else None
    target_accession = target_accession.strip().upper()
    if not direct_request or direct_request.get("target_accession") != target_accession:
        target_accession = canonical_target_accession(store, target_accession)
    requested = {"smiles": smiles, "canonical_smiles": canonical, "target_accession": target_accession}
    response = {
        "status": "unavailable",
        "source": source,
        "requested": requested,
        "scene": None,
        "matches": [],
        "available_references": available_references(target_accession),
    }
    if direct_request is not None:
        sequence = frozen_target_sequence(direct_job, direct_request)
    else:
        target = registered_target(store, target_accession)
        sequence = _target_sequence(target_accession) or (target or {}).get("sequence")
    if not sequence and source != "alphafold3_prediction":
        response["reason"] = "이 표적의 서열 기준이 등록되어 있지 않습니다. UniProt 표적을 조회·등록해 주세요."
        return response
    if source == "alphafold3_prediction":
        scene, matches, scanned, checked, truncated = _resolve_saved(
            store,
            canonical,
            target_accession,
            sequence,
            jobs=[direct_job] if direct_job is not None else None,
        )
        response.update(
            scene=scene,
            matches=matches,
            scanned_saved_jobs=scanned,
            checked_model_artifacts=checked,
            search_truncated=truncated,
            search_limits={"saved_jobs": 5000, "model_artifacts": _MAX_MODEL_CHECKS, "returned_matches": 5},
        )
        response["reason"] = (
            "선택한 분자와 표적에 모두 일치하고 실제 리간드 원자가 확인되는 저장된 AlphaFold 3 결과가 없습니다."
        )
        if truncated:
            response["reason"] += (
                f" 조회 상한(저장 작업 5,000개 / 모델 파일 {_MAX_MODEL_CHECKS}개)에 도달하여 더 오래된 자료는 아직 확인하지 못했습니다."
            )
            response["reason_code"] = "saved_search_limit_reached"
    else:
        from .molecular_api import _reference_scene

        registered_matches = source_failures = validation_failures = 0
        for reference in reference_catalog(target_accession):
            if exact_identity(reference["ligand_smiles"]) != canonical:
                continue
            registered_matches += 1
            try:
                scene = _reference_scene(store, reference["pdb_id"], reference=reference)
                path = store.root / "reference_structures" / f"{reference['pdb_id']}.cif"
                cif = molecular.read_cif(path)
                target_chains = _experimental_target_chains(scene, cif, reference)
                ligands = _experimental_ligands(store, scene, reference, canonical)
                if not target_chains or not ligands:
                    validation_failures += 1
                    continue
                _selection_metadata(
                    scene,
                    canonical,
                    target_accession,
                    target_chains,
                    ligands,
                    target_identity="RCSB UniProt entity mapping and pinned experimental construct sequence hash",
                    construct=reference.get("construct", {}),
                )
                scene["metadata"]["construct"] = reference.get("construct", {})
                scene["warnings"].append(
                    "실험 단백질은 등록된 절단·변이·서열 차이를 포함한 구조체입니다. construct 메타데이터를 확인하세요."
                )
                response["matches"].append(
                    {
                        "id": reference["pdb_id"],
                        "pdb_id": reference["pdb_id"],
                        "label": reference["label"],
                        "source_url": reference["source_url"],
                    }
                )
                if response["scene"] is None:
                    response["scene"] = scene
            except (OSError, HTTPException):
                source_failures += 1
                continue
            except (ValueError, RuntimeError, KeyError, IndexError):
                validation_failures += 1
                continue
        response["reason_code"] = "no_registered_exact_match"
        response["reason"] = (
            "등록된 실험 구조에서 선택 분자의 입체화학·전하와 표적이 모두 일치하는 복합체를 찾지 못했습니다. 아래 참조 성분은 직접 선택할 수 있습니다."
        )
        if registered_matches and response["scene"] is None:
            response["reason_code"] = (
                "reference_fetch_failed" if source_failures else "reference_validation_failed"
            )
            response["reason"] = (
                "선택 분자와 일치하는 등록 실험 자료가 있지만 좌표 또는 CCD 자료를 불러오지 못했습니다. 잠시 후 다시 조회하세요."
                if source_failures
                else "선택 분자와 일치하는 등록 실험 자료가 있지만 실제 표적·리간드 원자·결합의 일치 검증을 통과하지 못했습니다."
            )
    if response["scene"] is not None:
        response["status"] = "matched"
        response.pop("reason", None)
        response.pop("reason_code", None)
    return response
