"""Explicit, identity-checked AF3 geometry enrichment of measured/query records."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re

from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from rdkit import Chem, rdBase

from . import alphafold
from .structure import pocket_features


def _identity(smiles):
    # Do not uncharge, remove salts, strip stereo, or canonicalize tautomers:
    # those operations could attach a different modeled species to a row.
    if not isinstance(smiles, str) or not smiles or len(smiles) > 20000 or any(c.isspace() for c in smiles):
        raise ValueError("A valid single-molecule smiles string is required")
    with rdBase.BlockLogs():
        molecule = Chem.MolFromSmiles(smiles)
    if molecule is None or molecule.GetNumAtoms() == 0 or len(Chem.GetMolFrags(molecule)) != 1:
        raise ValueError("A valid single-molecule smiles string is required")
    return Chem.MolToSmiles(molecule, isomericSmiles=True)


def _json_artifact(store, job_id, name):
    path = store.artifact(job_id, name)
    if path.stat().st_size > 4_000_000:
        raise ValueError(f"Oversized AF3 artifact: {name}")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Malformed AF3 artifact: {name}")
    return data, path


def enrich_records(records, store, min_iptm=0.6):
    """Add real coordinate features, preserving experimental labels unchanged.

    Every row must explicitly identify a locally completed, execution-verified
    native AF3 job and supply the exact modeled protein sequence and species.
    A failed row aborts the entire operation; no partially enriched data are
    returned. The configurable ipTM gate is a structure-quality heuristic, not
    a calibrated probability of binding or a measured-affinity threshold.
    """
    if not isinstance(records, list) or not 1 <= len(records) <= 5000:
        raise ValueError("Provide 1–5000 records for explicit AF3 enrichment")
    if (
        isinstance(min_iptm, bool)
        or not isinstance(min_iptm, (int, float))
        or not math.isfinite(min_iptm)
        or not 0 <= min_iptm <= 1
    ):
        raise ValueError("min_iptm must be a finite number between 0 and 1")
    enriched, provenance = [], []
    for index, row in enumerate(records):
        try:
            if not isinstance(row, dict):
                raise ValueError("Record must be an object")
            job_id = row.get("af3_job_id")
            if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
                raise ValueError("af3_job_id must explicitly identify a local AF3 job")
            job = store.get(job_id)
            result = job.get("result")
            if (
                job.get("kind") not in {"alphafold", "alphafold_smoke"}
                or job.get("status") != "completed"
                or not isinstance(result, dict)
                or result.get("execution_verified") is not True
            ):
                raise ValueError(
                    "AF3 job must be completed with execution_verified=true; imported artifacts are not sufficient"
                )
            data, msa_mode = alphafold.validate_input(job["payload"])
            sequences = data["sequences"]
            if len(sequences) != 2 or set(sequences[0]) != {"protein"} or set(sequences[1]) != {"ligand"}:
                raise ValueError("Feature enrichment requires exactly one protein and one ligand")
            protein, ligand = sequences[0]["protein"], sequences[1]["ligand"]
            if protein["id"] not in ("A", ["A"]) or ligand["id"] not in ("B", ["B"]):
                raise ValueError("Feature enrichment requires exactly protein chain A and ligand chain B")
            sequence = row.get("protein_sequence")
            if not isinstance(sequence, str) or not sequence:
                raise ValueError("protein_sequence is required to verify the modeled target")
            sequence = "".join(sequence.split()).upper()
            if sequence != protein["sequence"]:
                raise ValueError("protein_sequence differs from the AF3 modeled target/construct")
            if _identity(row.get("smiles")) != _identity(ligand["smiles"]):
                raise ValueError("smiles differs from the AF3 modeled ligand, charge, or stereochemistry")
            manifest, _ = _json_artifact(store, job_id, "af3_manifest.json")
            manifest_provenance = manifest.get("provenance")
            if not isinstance(manifest_provenance, dict) or manifest_provenance.get("runner") != "native":
                raise ValueError("Feature enrichment currently requires a verified native AF3 run")
            saved, saved_path = _json_artifact(store, job_id, "fold_input.json")
            saved_input, _ = alphafold.validate_input(saved)
            input_hash = hashlib.sha256(saved_path.read_bytes()).hexdigest()
            if saved_input != data or input_hash != manifest.get("input_sha256"):
                raise ValueError("AF3 input artifact/payload provenance mismatch")
            models = result.get("models")
            if not isinstance(models, list) or not models:
                raise ValueError("AF3 job contains no verified structure models")
            model_index = row.get("af3_model_index")
            if model_index is None:
                model_index = next(
                    (
                        i
                        for i, model in enumerate(models)
                        if isinstance(model, dict) and model.get("is_top_ranked_copy") is True
                    ),
                    0,
                )
            if type(model_index) is not int or not 0 <= model_index < len(models):
                raise ValueError("af3_model_index must select an existing model using a zero-based integer")
            model = models[model_index]
            if not isinstance(model, dict) or not isinstance(model.get("metrics"), dict):
                raise ValueError("Malformed AF3 model confidence record")
            metrics = model["metrics"]
            if metrics.get("has_clash") is not False:
                raise ValueError(
                    "AF3 structural quality gate failed: clash present or clash status unavailable"
                )
            iptm = metrics.get("iptm")
            if (
                isinstance(iptm, bool)
                or not isinstance(iptm, (int, float))
                or not math.isfinite(iptm)
                or not min_iptm <= iptm <= 1
            ):
                raise ValueError(f"AF3 structural quality gate failed: finite ipTM >= {min_iptm} required")
            relative = model.get("structure_path")
            if not isinstance(relative, str):
                raise ValueError("AF3 model has no structure_path")
            # Two independent boundaries: the output folder and the Store job.
            structure = alphafold.safe_output_path(store.directory(job_id) / "output", relative)
            if structure != store.artifact(job_id, "output/" + relative):
                raise ValueError("AF3 structure path does not resolve to the expected job artifact")
            with structure.open("rb") as stream:
                structure_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            if structure_hash != model.get("structure_sha256"):
                raise ValueError("AF3 structure hash differs from the execution-verified artifact")
            if structure.stat().st_size > 100_000_000:
                raise ValueError("AF3 structure exceeds 100 MB")
            cif = MMCIF2Dict(str(structure))
            if set(cif.get("_atom_site.label_asym_id", [])) != {"A", "B"}:
                raise ValueError("AF3 mmCIF must contain only modeled chains A and B")
            geometry = pocket_features(structure, ligand_chain="B", protein_chains=["A"])
            features = geometry["structure_features"]
            if not features or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in features.values()
            ):
                raise ValueError("AF3 coordinate features must all be finite numbers")
            audit = {
                "record_index": index,
                "af3_job_id": job_id,
                "af3_model_index": model_index,
                "structure_path": "output/" + relative,
                "structure_sha256": structure_hash,
                "input_sha256": input_hash,
                "protein_sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                "modeled_ligand_smiles": ligand["smiles"],
                "execution_verified": True,
                "msa_mode": msa_mode,
                "method": geometry["method"],
                "cutoff_angstrom": geometry["cutoff_angstrom"],
                "quality_gate": {
                    "min_iptm": float(min_iptm),
                    "observed_iptm": float(iptm),
                    "has_clash": False,
                },
                "af3_version": manifest.get("version"),
                "source_commit": manifest_provenance.get("source_commit"),
            }
            output = copy.deepcopy(row)
            output.update(protein_sequence=sequence, structure_features=features, structure_provenance=audit)
            enriched.append(output)
            provenance.append(audit)
        except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
            raise ValueError(f"Row {index + 1}: {exc}") from exc
    return {
        "records": enriched,
        "provenance": provenance,
        "quality_policy": "No clashes and ipTM threshold are structure-quality heuristics; passing does not validate binding or affinity.",
        "confidence_note": alphafold.CONFIDENCE_NOTE,
    }
