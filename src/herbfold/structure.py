"""Geometric measurements of actual AF3 mmCIF coordinates, never affinity labels."""

from pathlib import Path

import numpy as np
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from scipy.spatial import cKDTree


def pocket_features(path, ligand_chain="B", protein_chains=None, cutoff=5.0):
    if not 2 <= cutoff <= 12:
        raise ValueError("Contact cutoff must be 2–12 angstrom")
    path = Path(path)
    if path.stat().st_size > 100_000_000:
        raise ValueError("Structure exceeds 100 MB limit")
    cif = MMCIF2Dict(str(path))
    chains = cif.get("_atom_site.label_asym_id", [])
    if not chains:
        raise ValueError("mmCIF contains no atom_site records")
    residues = cif["_atom_site.label_seq_id"]
    elements = cif["_atom_site.type_symbol"]
    models = cif.get("_atom_site.pdbx_PDB_model_num", ["1"] * len(chains))
    xyz = np.array([cif[f"_atom_site.Cartn_{axis}"] for axis in "xyz"], dtype=float).T
    ligand = [
        i
        for i, c in enumerate(chains)
        if c == ligand_chain and elements[i] not in ("H", "D") and models[i] == models[0]
    ]
    protein = [
        i
        for i, c in enumerate(chains)
        if c != ligand_chain
        and residues[i] not in (".", "?")
        and elements[i] not in ("H", "D")
        and models[i] == models[0]
        and (protein_chains is None or c in protein_chains)
    ]
    if not ligand or not protein or not np.isfinite(xyz).all():
        raise ValueError("Valid ligand and protein heavy-atom coordinates are required")
    tree = cKDTree(xyz[protein])
    contacts = tree.query_ball_point(xyz[ligand], r=cutoff)
    residue_set = {(chains[protein[p]], residues[protein[p]]) for nearby in contacts for p in nearby}
    distances, _ = tree.query(xyz[ligand])
    features = {
        "ligand_heavy_atoms": len(ligand),
        "contact_pairs": sum(map(len, contacts)),
        "pocket_residues": len(residue_set),
        "min_distance_angstrom": float(distances.min()),
        "mean_nearest_distance_angstrom": float(distances.mean()),
        "ligand_contact_fraction": float(np.mean([bool(x) for x in contacts])),
        "close_atom_pairs": sum(map(len, tree.query_ball_point(xyz[ligand], r=1.5))),
    }
    return {
        "structure_features": features,
        "ligand_chain": ligand_chain,
        "cutoff_angstrom": cutoff,
        "contact_residues": sorted([f"{a}:{b}" for a, b in residue_set]),
        "method": "heavy_atom_geometry_v1",
        "note": "Geometric proximity and close-contact flags are not calibrated affinity, hydrogen-bond assignments, or validated clash scores.",
    }
