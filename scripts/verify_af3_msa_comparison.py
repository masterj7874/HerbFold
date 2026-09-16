"""Read-only, CPU-only audit of actual AF3 baseline/MSA outputs and 5IKR agreement.

No API calls, inference, coordinate changes, trimming, or production-store writes.
The only output is the requested verification JSON. A top-ranked copy is audited
but never counted as a sixth independent diffusion sample. RMSD to a potentially
used template is descriptive agreement, not independent held-out validation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from rdkit import Chem

ROOT = Path(__file__).resolve().parents[1]
AMINO = dict(zip(
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL".split(),
    "ARNDCQEGHILKMFPSTWYV", strict=True,
))
MISSING = {".", "?", ""}
SOURCES = {
    "af3_outputs": "https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/output.md",
    "sifts": "https://www.ebi.ac.uk/pdbe/docs/sifts/quick.html",
    "reference": "https://www.rcsb.org/structure/5IKR",
}
LIMITATIONS = [
    "pLDDT, pTM and ipTM are prediction confidence, not measured binding affinity, efficacy or safety.",
    "5IKR is a mefenamic-acid-bound experimental construct, not an aspirin/quercetin pose reference.",
    "5IKR agreement can be influenced by templates or AF3 training data; this is not independent held-out validation.",
    "Kabsch RMSD uses every observed sequence-verified matched C-alpha without trimming, weighting or outlier rejection.",
    "Five diffusion samples from one seed do not establish statistical independence or a population confidence interval.",
    "MSA uniqueness and coverage are descriptive counts, not Neff or effective evolutionary independence.",
    "No clinical efficacy, safety or general structure-accuracy certification is inferred from this audit.",
]


class AuditError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AuditError(message)


def load_json(path):
    return json.loads(Path(path).read_text())


def sha256(path):
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
            == (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino),
            f"File changed during audit: {path}")
    return digest.hexdigest()


def safe_artifact(directory, relative):
    require(isinstance(relative, str) and not Path(relative).is_absolute() and ".." not in Path(relative).parts,
            "Artifact path must be relative without traversal")
    path = (Path(directory) / relative).resolve()
    require(path.is_relative_to(Path(directory).resolve()) and path.is_file(), f"Artifact unavailable: {relative}")
    return path


def finite(value, minimum=None, maximum=None):
    require(not isinstance(value, bool), "Boolean is not a numeric measurement")
    number = float(value)
    require(np.isfinite(number), "Nonfinite measurement or coordinate")
    require(minimum is None or number >= minimum, "Measurement below its valid range")
    require(maximum is None or number <= maximum, "Measurement above its valid range")
    return number


def stats(values):
    values = np.asarray(values, dtype=float)
    require(values.ndim == 1 and len(values) > 0 and np.isfinite(values).all(), "Empty/nonfinite statistics")
    return {"count": len(values), "min": float(values.min()), "mean": float(values.mean()),
            "median": float(np.median(values)), "p95": float(np.quantile(values, .95, method="linear")),
            "max": float(values.max())}


def cif_column(cif, name, count, default=None):
    value = cif.get(name)
    if value is None:
        require(default is not None, f"Missing CIF column: {name}")
        return [default] * count
    require(isinstance(value, list) and len(value) == count, f"Inconsistent CIF column: {name}")
    return value


def atom_rows(cif):
    count = len(cif.get("_atom_site.id", []))
    require(count > 0, "CIF has no atoms")
    keys = {"id": "id", "chain": "label_asym_id", "position": "label_seq_id", "name": "label_atom_id",
            "element": "type_symbol", "residue": "label_comp_id", "x": "Cartn_x", "y": "Cartn_y", "z": "Cartn_z",
            "bfactor": "B_iso_or_equiv"}
    columns = {key: cif_column(cif, "_atom_site." + name, count) for key, name in keys.items()}
    columns.update({"alt": cif_column(cif, "_atom_site.label_alt_id", count, "."),
                    "occupancy": cif_column(cif, "_atom_site.occupancy", count, "1"),
                    "model": cif_column(cif, "_atom_site.pdbx_PDB_model_num", count, "1")})
    require(len(set(columns["model"])) == 1, "Multi-model CIF requires an explicit separate analysis")
    output = []
    for i in range(count):
        row = {key: values[i] for key, values in columns.items()}
        row["row_index"] = i
        row["xyz"] = [finite(row[key]) for key in ("x", "y", "z")]
        row["occupancy"] = finite(row["occupancy"], 0, 1)
        row["bfactor"] = finite(row["bfactor"])
        row["position"] = None if row["position"] in MISSING else int(row["position"])
        output.append(row)
    return output


def molecular_table_sha256(cif):
    """Ignore creation-time model descriptions, retain every atom/identity column."""
    tables = {key: value for key, value in cif.items()
              if key.startswith(("_atom_site.", "_chem_comp.", "_entity_poly.", "_entity_poly_seq.", "_struct_asym."))}
    return hashlib.sha256(json.dumps(tables, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_copy_metadata(sample_cif, top_cif):
    differences = {key: {"sample": sample_cif.get(key), "top_copy": top_cif.get(key)}
                   for key in set(sample_cif) | set(top_cif) if sample_cif.get(key) != top_cif.get(key)}
    require(set(differences) <= {"_ma_model_list.model_group_name"}, "Top copy differs outside creation-time model metadata")
    if differences:
        pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
        before = sample_cif["_ma_model_list.model_group_name"]
        after = top_cif["_ma_model_list.model_group_name"]
        require([re.sub(pattern, "<creation-time>", value) for value in before]
                == [re.sub(pattern, "<creation-time>", value) for value in after],
                "Top copy model metadata differs by more than creation timestamp")
    return differences


def select_ca(atoms, chain):
    """Highest occupancy per residue; ties blank, then A, then lexical alt ID."""
    groups = defaultdict(list)
    for atom in atoms:
        if atom["chain"] == chain and atom["name"] == "CA":
            require(atom["element"].upper() == "C" and atom["position"] is not None, "Invalid polymer C-alpha")
            groups[atom["position"]].append(atom)
    chosen, alternate = {}, []
    for position, rows in groups.items():
        require(len({row["residue"] for row in rows}) == 1, "Alternate locations disagree on residue identity")
        require(len({row["alt"] for row in rows}) == len(rows), "Duplicate C-alpha for the same alternate location")
        ranked = sorted(rows, key=lambda row: (-row["occupancy"], row["alt"] not in MISSING,
                                               row["alt"] != "A", row["alt"]))
        chosen[position] = ranked[0]
        if len(rows) > 1 or ranked[0]["alt"] not in MISSING:
            alternate.append({"label_seq_id": position, "chosen_alt_id": ranked[0]["alt"],
                              "chosen_occupancy": ranked[0]["occupancy"], "discarded_alt_ids": [r["alt"] for r in ranked[1:]]})
    require(chosen, f"No C-alpha in chain {chain}")
    return chosen, alternate


def canonical(smiles):
    mol = Chem.MolFromSmiles(smiles)
    require(mol is not None, "Invalid declared ligand SMILES")
    return Chem.MolToSmiles(Chem.RemoveHs(mol), canonical=True, isomericSmiles=True)


def verify_raw_identity(cif, atoms, sequence, smiles, protein_chain, ligand_chain):
    observed = defaultdict(set)
    for atom in atoms:
        if atom["chain"] == protein_chain:
            position = atom["position"]
            require(position is not None and 1 <= position <= len(sequence), "Predicted protein residue outside target")
            observed[position].add(atom["residue"])
    require(set(observed) == set(range(1, len(sequence) + 1)), "Predicted observed protein is not the full target sequence")
    for position, residues in observed.items():
        require(len(residues) == 1 and AMINO.get(next(iter(residues))) == sequence[position - 1],
                f"Predicted protein residue identity mismatch at {position}")
    ligand_atoms = [atom for atom in atoms if atom["chain"] == ligand_chain]
    require(ligand_atoms and len({a["residue"] for a in ligand_atoms}) == 1, "Selected ligand must be one complete component")
    component = ligand_atoms[0]["residue"]
    ids = cif.get("_chem_comp.id", [])
    declarations = dict(zip(ids, cif_column(cif, "_chem_comp.pdbx_smiles", len(ids)), strict=True))
    declared = declarations.get(component)
    require(declared and canonical(declared) == canonical(smiles), "Raw ligand graph identity differs from selection")
    counts, expected = Counter(), {}
    for atom in Chem.MolFromSmiles(declared).GetAtoms():
        element = atom.GetSymbol()
        counts[element] += 1
        if element not in {"H", "D"}:
            expected[f"{element.upper()}{counts[element]}"] = element.upper()
    heavy = [atom for atom in ligand_atoms if atom["element"].upper() not in {"H", "D"}]
    actual = {atom["name"]: atom["element"].upper() for atom in heavy}
    require(actual == expected and len(heavy) == len(expected), "Raw ligand named heavy atoms are missing, duplicated or substituted")
    return heavy, {"full_observed_sequence_verified": True, "sequence_length": len(sequence),
                   "canonical_isomeric_smiles": canonical(smiles), "component": component,
                   "ligand_heavy_atoms": len(heavy), "ligand_atom_names": sorted(actual),
                   "method": "Raw chem_comp SMILES with stereo/charge and complete AF3 named heavy-atom inventory; linked to persisted graph validation, not bonds inferred from distances"}


def kabsch(moving, reference):
    moving, reference = np.asarray(moving, dtype=float), np.asarray(reference, dtype=float)
    require(moving.shape == reference.shape and moving.ndim == 2 and moving.shape[1] == 3 and len(moving) >= 3,
            "Kabsch requires at least three corresponding 3D points")
    require(np.isfinite(moving).all() and np.isfinite(reference).all(), "Kabsch coordinates must be finite")
    a, b = moving.mean(axis=0), reference.mean(axis=0)
    x, y = moving - a, reference - b
    require(np.linalg.matrix_rank(x) >= 2 and np.linalg.matrix_rank(y) >= 2, "Collinear coordinates do not define a reliable rigid rotation")
    u, _, vt = np.linalg.svd(x.T @ y)
    correction = np.eye(3)
    correction[-1, -1] = 1 if np.linalg.det(u @ vt) >= 0 else -1
    rotation = u @ correction @ vt
    translation = b - a @ rotation
    distances = np.linalg.norm(moving @ rotation + translation - reference, axis=1)
    return {"rmsd_angstrom": float(np.sqrt(np.mean(distances ** 2))), "rotation": rotation.tolist(),
            "translation": translation.tolist(), "rotation_determinant": float(np.linalg.det(rotation)),
            "matched_ca_count": len(moving), "residual_distance_stats_angstrom": stats(distances),
            "per_residue_distance_angstrom": distances.tolist(), "outlier_rejection": False}


def reference_mapping(registry, sequence, accession):
    reference = next((r for r in registry["references"] if r["pdb_id"] == "5IKR" and r["target_accession"] == accession), None)
    require(reference is not None and "A" in reference["target_chains"], "Verified 5IKR chain A registry entry required")
    alignments = [a for a in reference["construct"]["reference_alignments"]
                  if a["provenance_source"] == "SIFTS" and a["reference_database_accession"] == accession]
    require(len(alignments) == 1, "Ambiguous/missing SIFTS alignment")
    mapping = {}
    for region in alignments[0]["aligned_regions"]:
        for offset in range(region["length"]):
            source, target = region["entity_beg_seq_id"] + offset, region["ref_beg_seq_id"] + offset
            require(source not in mapping and target not in mapping.values() and 1 <= target <= len(sequence), "Overlapping/out-of-range SIFTS mapping")
            mapping[source] = target
    require(len(mapping) == reference["construct"]["sample_sequence_length"], "SIFTS mapping does not cover the full construct")
    return reference, mapping


def load_reference(path, registry, sequence, accession):
    declaration, mapping = reference_mapping(registry, sequence, accession)
    cif = MMCIF2Dict(str(path))
    require(cif.get("_entry.id", [""])[0].upper() == "5IKR", "Reference CIF is not 5IKR")
    entities = dict(zip(cif["_struct_asym.id"], cif["_struct_asym.entity_id"], strict=True))
    require(entities.get("A") == declaration["target_entity_id"], "5IKR chain A polymer entity mismatch")
    sequences = dict(zip(cif["_entity_poly.entity_id"], cif["_entity_poly.pdbx_seq_one_letter_code_can"], strict=True))
    declared_sequence = re.sub(r"\s", "", sequences[entities["A"]])
    require(hashlib.sha256(declared_sequence.encode()).hexdigest() == declaration["construct"]["canonical_construct_sequence_sha256"],
            "Reference construct sequence hash differs from verified registry")
    require(len(declared_sequence) == len(mapping), "Reference declared sequence length mismatch")
    for position, target in mapping.items():
        require(declared_sequence[position - 1] == sequence[target - 1], "Reference SIFTS sequence identity mismatch")
    atoms = atom_rows(cif)
    for atom in atoms:
        if atom["chain"] == "A":
            require(atom["position"] in mapping and AMINO.get(atom["residue"]) == sequence[mapping[atom["position"]] - 1],
                    "Observed reference residue conflicts with SIFTS identity")
    ca, alternates = select_ca(atoms, "A")
    return {"declaration": declaration, "mapping": mapping, "ca": ca, "report": {
        "path": str(path), "sha256": sha256(path), "pdb_id": "5IKR", "label_chain": "A",
        "target_accession": accession, "source_url": declaration["source_url"],
        "sifts_regions": declaration["construct"]["reference_alignments"][0]["aligned_regions"],
        "construct_residues": len(mapping), "observed_ca": len(ca),
        "missing_reference_label_seq_ids": sorted(set(mapping) - set(ca)), "alternate_locations": alternates,
        "all_reference_residue_identities_verified": True,
        "reference_b_factors_are_not_plddt": True,
    }}


def align_ca(prediction_ca, reference, sequence):
    positions = sorted(reference["ca"])
    matched = [(position, reference["mapping"][position]) for position in positions
               if reference["mapping"][position] in prediction_ca]
    require(len(matched) >= 3, "Fewer than three matched C-alpha atoms")
    for ref_position, target_position in matched:
        for atom in (reference["ca"][ref_position], prediction_ca[target_position]):
            require(AMINO.get(atom["residue"]) == sequence[target_position - 1], "Aligned C-alpha residue identity mismatch")
    result = kabsch([prediction_ca[t]["xyz"] for _, t in matched], [reference["ca"][r]["xyz"] for r, _ in matched])
    result.update(matched_uniprot_positions=[t for _, t in matched],
                  missing_prediction_ca_uniprot_positions=[reference["mapping"][r] for r in positions if reference["mapping"][r] not in prediction_ca],
                  excluded_full_target_positions_outside_reference=sorted(set(range(1, len(sequence) + 1)) - set(reference["mapping"].values())),
                  inclusion_policy="All observed C-alpha pairs under the validated SIFTS mapping; no confidence or distance selection")
    return result


def msa_diagnostics(a3m, query):
    require(isinstance(a3m, str) and a3m, "MSA must contain a nonempty query alignment")
    aligned, rows, seen_header = [], [], False
    for line in io.StringIO(a3m):
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if seen_header:
                require(aligned, "Empty A3M sequence record")
                rows.append("".join(aligned))
            aligned = []
            seen_header = True
        else:
            require(line and re.fullmatch(r"[A-Za-z.\-]+", line), "Invalid A3M sequence characters")
            aligned.append(line)
    if seen_header:
        require(aligned, "Empty A3M sequence record")
        rows.append("".join(aligned))
    require(rows and a3m.lstrip().startswith(">"), "A3M must start with a FASTA header")
    rows = [re.sub(r"[a-z.]", "", row) for row in rows]
    require(rows[0] == query and all(len(row) == len(query) for row in rows), "A3M query or alignment width differs from requested full target")
    column_counts = np.zeros(len(query), dtype=np.int64)
    coverage = []
    for row in rows[1:]:
        mask = np.fromiter((letter != "-" for letter in row), dtype=bool, count=len(query))
        column_counts += mask
        coverage.append(float(mask.mean()))
    unique = set(rows)
    return {"aligned_width": len(query), "sequence_rows_including_query": len(rows),
            "aligned_unique_sequences_including_query": len(unique), "duplicate_aligned_rows": len(rows) - len(unique),
            "non_query_rows": len(rows) - 1, "non_query_unique_aligned_sequences": len(set(rows[1:])),
            "non_query_rows_identical_to_query": sum(row == query for row in rows[1:]),
            "non_query_coverage_fraction": stats(coverage) if coverage else None,
            "column_non_gap_non_query_row_count": {"min": int(column_counts.min()), "median": float(np.median(column_counts)), "max": int(column_counts.max())},
            "query_positions_with_non_query_support_fraction": float(np.mean(column_counts > 0)),
            "column_counts": column_counts.tolist(), "insertion_policy": "Remove lowercase insertions and periods; retain gaps; require exact full-target alignment width",
            "interpretation": "Raw duplicate/coverage diagnostics. Non-query alignment rows are not independent homologs and are not reweighted; this is not Neff."}


def execution_input_projection(payload):
    """Project semantic inputs; normalize only documented absent/empty defaults."""
    proteins = [row["protein"] for row in payload["sequences"] if "protein" in row]
    ligands = [row["ligand"] for row in payload["sequences"] if "ligand" in row]
    require(len(payload["sequences"]) == 2 and len(proteins) == len(ligands) == 1,
            "Output data requires one protein and the selected ligand only")
    protein, ligand = proteins[0], ligands[0]
    require(isinstance(protein["id"], str) and isinstance(ligand["id"], str),
            "Output data chain IDs must identify individual protein/ligand chains")
    return {"name": payload["name"], "modelSeeds": payload["modelSeeds"],
            "protein": {key: protein[key] for key in ("id", "sequence", "unpairedMsa", "pairedMsa", "templates")},
            "protein_modifications": protein.get("modifications") or [],
            "ligand": {"id": ligand["id"], "canonical_smiles": canonical(ligand["smiles"])},
            "bondedAtomPairs": payload.get("bondedAtomPairs") or None,
            "userCCD": payload.get("userCCD") or None}


def verify_output_data(directory, expected_payload):
    output = Path(directory) / "output"
    files = list(output.rglob("*_data.json"))
    require(len(files) == 1, "Exactly one actual AF3 output *_data.json is required")
    path = safe_artifact(output, str(files[0].relative_to(output)))
    actual = execution_input_projection(load_json(path))
    expected = execution_input_projection(expected_payload)
    require(actual == expected, "Actual AF3 output data semantics differ from the verified inference input")
    projection_sha = hashlib.sha256(json.dumps(actual, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {"path": str(path), "sha256": sha256(path), "semantic_projection_sha256": projection_sha,
            "matches_input": True, "name": actual["name"], "seeds": actual["modelSeeds"],
            "protein_chain": actual["protein"]["id"], "ligand_chain": actual["ligand"]["id"],
            "comparison_scope": "Name, seeds, chains, full sequence, selected isomeric ligand, modifications and exact unpaired/paired MSA/template fields; null/absent optional graph defaults normalized"}


def audit_msa_features(entry, directory, sequence):
    mode = entry["requested"]["msa_mode"]
    if mode == "none":
        protein = next(row["protein"] for row in entry["job"]["payload"]["sequences"] if "protein" in row)
        require(protein.get("unpairedMsa") == protein.get("pairedMsa") == "" and protein.get("templates") == [], "Baseline input did not explicitly omit MSA/templates")
        return {"status": "not_requested", "template_5ikr_explicitly_present": False, "templates": [],
                "output_input": verify_output_data(directory, entry["job"]["payload"])}
    metadata = entry.get("msa_features") or entry["job"]["result"].get("msa_features") or {}
    require(metadata.get("status") == "ready", "Standard result lacks verified ready MSA features")
    require(metadata.get("protein_sequence_sha256") == hashlib.sha256(sequence.encode()).hexdigest(),
            "MSA feature identity differs from full target sequence")
    require(metadata.get("max_template_date") == entry["requested"]["execution_profile"]["max_template_date"],
            "MSA feature template date differs from requested cutoff")
    database_fingerprint = metadata.get("database_fingerprint")
    require(database_fingerprint and database_fingerprint == entry["requested"]["submission_context"].get("database_fingerprint")
            == entry["job"]["result"]["execution_provenance"]["databases"].get("fingerprint_sha256"),
            "MSA features, submitted request and executed database installation differ")
    require(metadata.get("af3_version") == entry["requested"]["af3_version"]
            and metadata.get("af3_commit") == entry["requested"]["af3_commit"],
            "MSA features belong to a different AF3 version")
    path = safe_artifact(directory, "inference_input.json")
    payload = load_json(path)
    expected_sha = entry["job"]["result"].get("inference_input_sha256")
    require(expected_sha and sha256(path) == expected_sha, "Enriched inference input SHA mismatch/missing")
    original = entry["job"]["payload"]
    require(payload["modelSeeds"] == original["modelSeeds"] and payload["name"] == original["name"], "Enriched input seed/name changed")
    proteins = [row["protein"] for row in payload["sequences"] if "protein" in row]
    require(len(proteins) == 1 and proteins[0]["sequence"] == sequence, "Enriched input target sequence differs")
    original_protein = next(row["protein"] for row in original["sequences"] if "protein" in row)
    require(proteins[0]["id"] == original_protein["id"]
            and proteins[0].get("modifications", []) == original_protein.get("modifications", []),
            "Enriched input changed protein chain identity or modifications")
    require([row for row in payload["sequences"] if "ligand" in row] == [row for row in original["sequences"] if "ligand" in row], "Enriched input changed the selected ligand")
    output_input = verify_output_data(directory, payload)
    protein = proteins[0]
    features = {key: protein[key] for key in ("sequence", "unpairedMsa", "pairedMsa", "templates")}
    feature_bytes = (json.dumps(features, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
    require(hashlib.sha256(feature_bytes).hexdigest() == metadata.get("feature_sha256"),
            "Actual inference features differ from the validated feature content SHA")
    unpaired, paired = (msa_diagnostics(protein[key], sequence) for key in ("unpairedMsa", "pairedMsa"))
    require(metadata["unpaired_msa_sequences"] == unpaired["sequence_rows_including_query"]
            and metadata["paired_msa_sequences"] == paired["sequence_rows_including_query"]
            and metadata["non_query_sequences"] == unpaired["non_query_rows"], "Stored MSA count differs from actual inference input")
    templates = []
    require(len(protein["templates"]) == metadata["template_count"], "Template count differs from actual input")
    recorded = metadata.get("templates", [])
    require(len(recorded) == len(protein["templates"]), "Template provenance inventory mismatch")
    for template, stored in zip(protein["templates"], recorded, strict=True):
        digest = hashlib.sha256(template["mmcif"].encode()).hexdigest()
        require(digest == stored["sha256"], "Inline template content differs from provenance")
        query_indices, template_indices = template["queryIndices"], template["templateIndices"]
        require(query_indices and len(query_indices) == len(template_indices)
                and len(set(query_indices)) == len(query_indices) and len(set(template_indices)) == len(template_indices),
                "Template residue mapping invalid")
        require(all(type(i) is int and 0 <= i < len(sequence) for i in query_indices), "Template query mapping outside full target")
        cif = MMCIF2Dict(io.StringIO(template["mmcif"]))
        sequence_ids = cif.get("_entity_poly_seq.num") or cif.get("_pdbx_poly_seq_scheme.seq_id") or []
        require(sequence_ids, "Template CIF has no declared residue numbering")
        template_length = max(int(value) for value in sequence_ids)
        require(all(type(i) is int and 0 <= i < template_length for i in template_indices), "Template residue mapping outside template")
        mapping_bytes = (json.dumps({"queryIndices": query_indices, "templateIndices": template_indices}, separators=(",", ":")) + "\n").encode()
        mapping_sha = hashlib.sha256(mapping_bytes).hexdigest()
        require(mapping_sha == stored.get("mapping_sha256") and query_indices == stored.get("query_indices")
                and template_indices == stored.get("template_indices"), "Template mapping differs from saved provenance")
        entry_id = (cif.get("_entry.id") or [None])[0]
        data_name = cif.get("data_")
        require(entry_id == stored.get("entry_id"), "Template entry ID differs from raw CIF")
        templates.append({"entry_id": entry_id, "data_block_name": data_name, "sha256": digest,
                          "mapping_sha256": mapping_sha,
                          "mapped_residues": len(query_indices), "query_indices": query_indices,
                          "template_indices": template_indices})
    explicit = any(str(item["entry_id"]).upper() == "5IKR" for item in templates)
    return {"status": "ready", "input_path": str(path), "inference_input_sha256": expected_sha,
            "output_input": output_input,
            "cache_hit": metadata.get("cache_hit"), "source_job_id": metadata.get("source_job_id"),
            "feature_sha256": metadata.get("feature_sha256"), "database_fingerprint": metadata.get("database_fingerprint"),
            "max_template_date": metadata.get("max_template_date"), "unpaired": unpaired, "paired": paired,
            "templates": templates, "template_5ikr_explicitly_present": explicit,
            "unresolved_template_entry_ids": sum(item["entry_id"] in (None, ".", "?") for item in templates),
            "template_identity_note": "A missing entry_id does not establish that 5IKR was absent. Other templates and model training can also influence agreement."}


def verify_conditions(entry, sequence, mode, weights, hash_cache):
    job, requested = entry["job"], entry["requested"]
    result, profile = job["result"], requested["execution_profile"]
    require(job["status"] == "completed" and result.get("execution_verified") is True
            and result.get("prediction_eligible") is not False and result.get("return_code") == 0,
            "Only real eligible completed executions can be audited")
    validation = entry.get("output_validation") or result.get("output_validation") or {}
    require(validation.get("identity_verified") is True, "Persisted selected-output identity verification required")
    require(requested["msa_mode"] == mode and result["msa_mode"] == mode, "Calculation mode differs from report role")
    require(requested["seeds"] == job["payload"]["modelSeeds"] == result["model_seeds"] == [1], "Expected seed 1 in request/input/execution")
    require(profile["num_diffusion_samples"] == result["samples_per_seed"] == 5, "Expected five samples per seed")
    require(profile["num_recycles"] == result["num_recycles"] == 10, "Expected ten recycles")
    sequence_hash = hashlib.sha256(sequence.encode()).hexdigest()
    require(requested["target_sequence_sha256"] == sequence_hash, "Full target sequence SHA mismatch")
    require(requested["af3_commit"] == result["execution_provenance"]["source_commit"]
            and requested["af3_version"] == result["execution_provenance"]["expected_version"], "AF3 code provenance mismatch")
    require(result["execution_provenance"].get("source_modified") is False, "Modified AF3 code is outside this comparison")
    weight_path = Path(weights["file"]).resolve()
    require(weight_path.parent == Path(profile["model_dir"]).resolve(), "Verified model receipt differs from job model directory")
    if str(weight_path) not in hash_cache:
        hash_cache[str(weight_path)] = sha256(weight_path)
    require(weights.get("passed") is True and weights.get("full_container_decoded") is True
            and weights.get("matches_acquisition_receipt_sha256") is True
            and hash_cache[str(weight_path)] == weights["sha256"], "Actual model file differs from complete trained-weights verification receipt")
    acquisition_path = Path(weights["acquisition_receipt"])
    if not acquisition_path.is_absolute():
        acquisition_path = ROOT / acquisition_path
    acquisition = load_json(acquisition_path)
    require(acquisition.get("sha256") == weights["sha256"] and acquisition.get("source_url") == weights["source_url"]
            and acquisition.get("google_crc32c_verified") is True and acquisition.get("tls_certificate_verification") is True
            and acquisition.get("size_bytes") == weight_path.stat().st_size,
            "Full-file model validation is not linked to the verified source download receipt")
    stat = weight_path.stat()
    fingerprint = hashlib.sha256(json.dumps(((str(weight_path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns),)).encode()).hexdigest()
    expected_stat = requested["submission_context"]["parameter_stat_fingerprint_sha256"]
    require(fingerprint == expected_stat == result["execution_provenance"]["parameters"]["stat_fingerprint_sha256"], "Model file stat differs from captured execution identity")
    entrypoint = Path(profile["repo_dir"]) / "run_alphafold.py"
    require(sha256(entrypoint) == result["execution_provenance"]["entrypoint_sha256"], "Current AF3 entrypoint differs from executed code receipt")
    return {"target_sequence_sha256": sequence_hash, "sequence_length": len(sequence), "seeds": [1],
            "samples_per_seed": 5, "recycles": 10, "model_file": str(weight_path), "model_content_sha256": weights["sha256"],
            "model_stat_fingerprint_sha256": expected_stat, "af3_version": requested["af3_version"],
            "af3_commit": requested["af3_commit"], "entrypoint_sha256": result["execution_provenance"]["entrypoint_sha256"],
            "max_template_date": profile["max_template_date"], "flash_attention": profile.get("flash_attention"),
            "device": profile.get("device"), "cuda_environment": result["execution_provenance"].get("cuda_environment"),
            "msa_mode": mode, "weight_assurance": "Current complete content SHA matches prior full-file verification; file stat matches execution snapshot. This is not hardware attestation."}


def audit_model(model, directory, entry, sequence, reference, protein_chain, ligand_chain):
    path = safe_artifact(directory / "output", model["structure_path"])
    digest = sha256(path)
    require(digest == model.get("structure_sha256"), "CIF SHA differs from registered model")
    summary_path = safe_artifact(directory / "output", model["summary_path"])
    summary = load_json(summary_path)
    for metric in ("ptm", "iptm", "ranking_score"):
        value = finite(summary[metric], 0 if metric != "ranking_score" else -100, 1 if metric != "ranking_score" else 1.5)
        require(value == finite(model["metrics"][metric]), "Raw summary differs from registered confidence")
    cif = MMCIF2Dict(str(path))
    atoms = atom_rows(cif)
    heavy, identity = verify_raw_identity(cif, atoms, sequence, entry["requested"]["canonical_smiles"], protein_chain, ligand_chain)
    confidences_path = safe_artifact(directory / "output", model["confidence_path"])
    confidences = load_json(confidences_path)
    require(confidences["atom_chain_ids"] == [atom["chain"] for atom in atoms], "Confidence atom order/chain identity differs from CIF")
    values = [finite(value, 0, 100) for value in confidences["atom_plddts"]]
    for atom in atoms:
        finite(atom["bfactor"], 0, 100)
    require(len(values) == len(atoms), "Per-atom confidence length differs from CIF")
    require(max(abs(value - atom["bfactor"]) for value, atom in zip(values, atoms, strict=True)) <= .011,
            "CIF pLDDT differs from matching confidence JSON")
    ca, alternates = select_ca(atoms, protein_chain)
    alignment = align_ca(ca, reference, sequence)
    return {"name": model["name"], "seed": model["seed"], "sample": model["sample"],
            "is_top_ranked_copy": model["is_top_ranked_copy"], "structure_path": str(path), "structure_sha256": digest,
            "molecular_table_sha256": molecular_table_sha256(cif),
            "summary_sha256": sha256(summary_path), "confidence_sha256": sha256(confidences_path),
            "summary_metrics": {key: summary[key] for key in ("ptm", "iptm", "ranking_score", "fraction_disordered", "has_clash")},
            "raw_identity": identity, "full_protein_ca_plddt": stats([a["bfactor"] for a in ca.values()]),
            "reference_matched_ca_plddt": stats([ca[position]["bfactor"] for position in alignment["matched_uniprot_positions"]]),
            "ligand_heavy_atom_plddt": stats([a["bfactor"] for a in heavy]),
            "missing_full_target_ca_positions": sorted(set(range(1, len(sequence) + 1)) - set(ca)),
            "alternate_locations": alternates, "reference_alignment": alignment,
            "confidence_json_matches_cif": True}


def audit_job(entry, runtime, target, reference, weights, hash_cache, mode):
    job, sequence = entry["job"], target["sequence"]
    require(re.fullmatch(r"[a-f0-9]{32}", job["id"]), "Invalid job ID")
    require(entry["requested"]["target_accession"] == target["accession"], "Job target accession differs from pinned target")
    directory = runtime / job["id"]
    conditions = verify_conditions(entry, sequence, mode, weights, hash_cache)
    proteins = [row["protein"] for row in job["payload"]["sequences"] if "protein" in row]
    ligands = [row["ligand"] for row in job["payload"]["sequences"] if "ligand" in row]
    require(len(proteins) == len(ligands) == 1 and proteins[0]["sequence"] == sequence, "Expected one exact full target and one ligand")
    require(canonical(ligands[0]["smiles"]) == entry["requested"]["canonical_smiles"], "Payload ligand differs from selected identity")
    models = job["result"]["models"]
    samples = [model for model in models if model.get("is_top_ranked_copy") is False]
    copies = [model for model in models if model.get("is_top_ranked_copy") is True]
    require(len(models) == 6 and len(samples) == 5 and len(copies) == 1, "Exactly five diffusion samples and one top copy are required")
    require({(m["seed"], m["sample"]) for m in samples} == {(1, i) for i in range(5)}, "Missing or duplicate seed/sample records")
    declared_paths = {safe_artifact(directory / "output", m["structure_path"]) for m in models}
    require(len(declared_paths) == 6 and declared_paths == {p.resolve() for p in (directory / "output").rglob("*_model.cif")}, "Prediction CIF inventory differs from report; no unreported sample may be omitted")
    audited = [audit_model(m, directory, entry, sequence, reference, proteins[0]["id"], ligands[0]["id"]) for m in sorted(samples, key=lambda m: m["sample"])]
    top = audit_model(copies[0], directory, entry, sequence, reference, proteins[0]["id"], ligands[0]["id"])
    matching = [model["sample"] for model in audited if model["molecular_table_sha256"] == top["molecular_table_sha256"]]
    require(matching, "Top copy is not molecularly identical to a registered sample")
    ranking_paths = list((directory / "output").rglob("*ranking_scores.csv"))
    require(len(ranking_paths) == 1, "Exactly one raw ranking CSV is required")
    with ranking_paths[0].open(newline="") as stream:
        ranking_rows = list(csv.DictReader(stream))
    require(len(ranking_rows) == 5 and {(int(row["seed"]), int(row["sample"])) for row in ranking_rows}
            == {(1, i) for i in range(5)}, "Raw ranking CSV is missing or duplicating sample records")
    rankings = {int(row["sample"]): finite(row["ranking_score"], -100, 1.5) for row in ranking_rows}
    require(any(rankings[sample] == max(rankings.values()) for sample in matching), "Top copy does not match the highest unrounded ranking score")
    for model in audited:
        require(abs(rankings[model["sample"]] - model["summary_metrics"]["ranking_score"]) <= .005001,
                "Raw ranking CSV and rounded sample summary disagree")
    matched_sample = next(model for model in audited if model["sample"] in matching)
    require(matched_sample["summary_metrics"] == top["summary_metrics"]
            and matched_sample["confidence_sha256"] == top["confidence_sha256"], "Top copy confidence differs from matching sample")
    copy_metadata = verify_copy_metadata(MMCIF2Dict(matched_sample["structure_path"]), MMCIF2Dict(top["structure_path"]))
    validation = entry.get("output_validation") or job["result"]["output_validation"]
    require(validation["sha256"] == top["structure_sha256"], "Persisted selection validation belongs to another CIF")
    require(validation["selected_ligand_atom_count"] == top["raw_identity"]["ligand_heavy_atoms"], "Persisted selected atom count differs from raw ligand")
    require(all(validation["summary_metrics"][key] == top["summary_metrics"][key] for key in ("ptm", "iptm", "ranking_score")), "Persisted top summary differs from raw top copy")
    ranges = {key: stats([m["summary_metrics"][key] for m in audited]) for key in ("ptm", "iptm", "ranking_score")}
    ranges.update({"ca_mean_plddt": stats([m["full_protein_ca_plddt"]["mean"] for m in audited]),
                   "ligand_mean_plddt": stats([m["ligand_heavy_atom_plddt"]["mean"] for m in audited]),
                   "all_matched_ca_rmsd_angstrom": stats([m["reference_alignment"]["rmsd_angstrom"] for m in audited])})
    return {"job_id": job["id"], "mode": mode, "canonical_smiles": entry["requested"]["canonical_smiles"],
            "conditions": conditions, "sample_count": 5, "top_copy_matching_samples": matching,
            "top_copy_byte_identical_to_matched_sample": top["structure_sha256"] == matched_sample["structure_sha256"],
            "top_copy_creation_metadata_differences": copy_metadata,
            "raw_ranking": {"path": str(ranking_paths[0]), "sha256": sha256(ranking_paths[0]), "scores_by_sample": rankings},
            "samples": audited, "top_ranked_copy": top, "sample_ranges": ranges,
            "msa_features": audit_msa_features(entry, directory, sequence),
            "persisted_output_validation": validation, "quality_pass": None,
            "quality_status": "descriptive_audit_not_structure_accuracy_certification"}


def compare_conditions(baseline, msa):
    excluded = {"msa_mode", "weight_assurance"}
    keys = sorted((set(baseline) | set(msa)) - excluded)
    checks = {key: {"baseline": baseline.get(key), "msa": msa.get(key),
                    "equal": baseline.get(key) is not None and baseline.get(key) == msa.get(key)} for key in keys}
    return {"all_controlled_conditions_equal": all(value["equal"] for value in checks.values()),
            "checks": checks, "mismatches": [key for key, value in checks.items() if not value["equal"]]}


def comparison(a, b):
    require(a["canonical_smiles"] == b["canonical_smiles"], "Paired reports refer to different ligands")
    conditions = compare_conditions(a["conditions"], b["conditions"])
    require(conditions["all_controlled_conditions_equal"], "Confounded comparison settings: " + ", ".join(conditions["mismatches"]))
    before, after = a["top_ranked_copy"], b["top_ranked_copy"]
    baseline_alignment, msa_alignment = before["reference_alignment"], after["reference_alignment"]
    baseline_positions = set(baseline_alignment["matched_uniprot_positions"])
    msa_positions = set(msa_alignment["matched_uniprot_positions"])
    same_positions = bool(baseline_positions) and baseline_positions == msa_positions
    rmsd_comparison = {
        "comparable": same_positions,
        "status": "comparable" if same_positions else "not_comparable_residue_sets_differ",
        "baseline_rmsd_angstrom": baseline_alignment["rmsd_angstrom"],
        "msa_rmsd_angstrom": msa_alignment["rmsd_angstrom"],
        "baseline_matched_uniprot_positions": sorted(baseline_positions),
        "msa_matched_uniprot_positions": sorted(msa_positions),
        "reason": "Both rigid fits use the same complete matched residue set." if same_positions
        else "Matched residue sets differ. Individual RMSDs are retained, but their difference cannot represent a structural improvement.",
    }
    delta = {key: after["summary_metrics"][key] - before["summary_metrics"][key] for key in ("ptm", "iptm", "ranking_score")}
    delta.update(ca_mean_plddt=after["full_protein_ca_plddt"]["mean"] - before["full_protein_ca_plddt"]["mean"],
                 ligand_mean_plddt=after["ligand_heavy_atom_plddt"]["mean"] - before["ligand_heavy_atom_plddt"]["mean"],
                 reference_ca_rmsd_angstrom=msa_alignment["rmsd_angstrom"] - baseline_alignment["rmsd_angstrom"] if same_positions else None)
    return {"conditions": conditions, "top_copy_delta_msa_minus_baseline": delta,
            "reference_ca_rmsd_comparison": rmsd_comparison,
            "baseline_sample_ranges": a["sample_ranges"], "msa_sample_ranges": b["sample_ranges"],
            "template_5ikr_explicitly_present": b["msa_features"]["template_5ikr_explicitly_present"],
            "interpretation": "Observed confidence/agreement changes only. Not proof of accuracy improvement or ligand activity."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", type=Path, default=ROOT / "docs/af3-trained-selected-predictions.json")
    parser.add_argument("--msa-report", type=Path, default=ROOT / "docs/af3-msa-selected-predictions.json")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/af3-msa-structure-comparison.json")
    parser.add_argument("--baseline-only", action="store_true", help="Audit real baseline outputs while MSA results are not yet available")
    parser.add_argument("--runtime-root", type=Path, default=ROOT / "runtime")
    parser.add_argument("--reference-cif", type=Path, default=ROOT / "runtime/reference_structures/5IKR.cif")
    parser.add_argument("--reference-registry", type=Path, default=ROOT / "data/molecular_references.json")
    parser.add_argument("--target", type=Path, default=ROOT / "data/ptgs2.json")
    args = parser.parse_args(argv)
    report = {"schema_version": 1, "created_at": datetime.now(UTC).isoformat(), "mode": "baseline_only" if args.baseline_only else "baseline_vs_msa",
              "read_only_inputs": True, "gpu_used": False, "api_requests": 0, "sources": SOURCES,
              "limitations": LIMITATIONS, "passed": False, "baseline": {}, "msa": {}, "comparison": {}}
    try:
        baseline = load_json(args.baseline_report)
        msa = None if args.baseline_only else load_json(args.msa_report)
        target, registry = load_json(args.target), load_json(args.reference_registry)
        reference = load_reference(args.reference_cif, registry, target["sequence"], target["accession"])
        report["reference"] = reference["report"]
        report["input_reports"] = {"baseline": {"path": str(args.baseline_report), "sha256": sha256(args.baseline_report)},
                                   "msa": {"path": str(args.msa_report), "sha256": sha256(args.msa_report)} if msa else None}
        report["verification_inputs"] = {"script_sha256": sha256(Path(__file__)),
                                          "reference_registry_sha256": sha256(args.reference_registry),
                                          "pinned_target_json_sha256": sha256(args.target)}
        hash_cache = {}
        require(baseline.get("jobs"), "Baseline report has no jobs")
        if msa:
            require(set(baseline["jobs"]) == set(msa["jobs"]), "Baseline/MSA compounds do not match")
        for role, document, mode in (("baseline", baseline, "none"), ("msa", msa, "search")):
            if document is None:
                continue
            weights_path = Path(document.get("parameter_validation") or baseline["parameter_validation"])
            if not weights_path.is_absolute():
                weights_path = ROOT / weights_path
            weights = load_json(weights_path)
            report.setdefault("weight_validation_receipts", {})[role] = {"path": str(weights_path), "sha256": sha256(weights_path)}
            for name, entry in document["jobs"].items():
                print(f"Auditing {role}/{name}: raw five samples plus top copy", flush=True)
                report[role][name] = audit_job(entry, args.runtime_root, target, reference, weights, hash_cache, mode)
        if msa:
            report["comparison"] = {name: comparison(report["baseline"][name], report["msa"][name]) for name in baseline["jobs"]}
        report["passed"] = True
    except (AuditError, OSError, KeyError, ValueError, TypeError, StopIteration) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"passed": report["passed"], "mode": report["mode"], "output": str(args.output), "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
