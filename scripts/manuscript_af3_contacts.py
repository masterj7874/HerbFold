"""Read-only secondary analysis of 20 archived AF3 diffusion samples.

No new inference, relaxation, repair, API calls or affinity estimation.
--self-test uses mathematical fixtures, never scientific observations.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
from itertools import combinations
import json
from pathlib import Path
import sys

import numpy as np
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from rdkit import Chem
from scipy.spatial.distance import cdist

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from verify_af3_msa_comparison import (  # noqa: E402
    atom_rows, canonical, kabsch, molecular_table_sha256, select_ca,
    verify_raw_identity,
)

DOC = "https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/output.md"
LIMITATIONS = [
    "Twenty unique diffusion samples are four jobs (two ligands x two conditions), five samples at seed 1 per job; top-ranked copies are not additional observations.",
    "Diffusion samples from one seed and their ten pairwise comparisons are not independent biological replicates. No population confidence interval, p-value or affinity estimate is inferred.",
    "Contacts are predicted inter-chain heavy-atom distances <=4.0 or <=5.0 angstrom, not identified hydrogen bonds, favorable interactions or measured binding events.",
    "Receptor-frame ligand RMSD uses all 604 sequence-matched C-alpha atoms without outlier removal; a separate 551-position SIFTS-construct alignment is a sensitivity analysis. Ligands receive only the receptor transformation, with no separate ligand fit.",
    "Atom correspondence requires identical declared stereo graph, element inventory and AF3 named graph. Fixed-name and exact graph-automorphism-minimized RMSDs are both reported; no cross-ligand RMSD is calculated.",
    "PAE is internal predicted error: row tokens define the alignment frame, columns the assessed position. Contact-restricted PAE depends on the same predicted coordinates, not external validation.",
    "Distances below 2.0 angstrom are an explicitly labeled proximity screen, not a complete steric/van-der-Waals test or PoseBusters validation. Zero screen counts do not establish physical plausibility.",
    "MSA and templates change together. No experimental aspirin or quercetin pose is used here. Contact consistency and low RMSD do not prove pose accuracy, affinity, efficacy or safety.",
]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compact_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def require(test, message):
    if not test:
        raise ValueError(message)


def stats(values):
    values = np.asarray(values, dtype=float)
    require(np.isfinite(values).all(), "Nonfinite summary input")
    if not values.size:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {"count": int(values.size), "min": float(values.min()), "median": float(np.median(values)),
            "mean": float(values.mean()), "max": float(values.max())}


def jaccard(left, right):
    union = left | right
    return len(left & right) / len(union) if union else None


def contacts(distances, positions, cutoff):
    mask = distances <= cutoff
    return mask, {int(x) for x in positions[mask.any(axis=1)]}


def named_graph(cif, component, ligand):
    """AF3 names are element counters in declared SMILES order, not geometry."""
    declared = dict(zip(cif["_chem_comp.id"], cif["_chem_comp.pdbx_smiles"], strict=True))[component]
    mol = Chem.MolFromSmiles(declared)
    require(mol is not None and all(a.GetAtomicNum() > 1 for a in mol.GetAtoms()),
            "This analysis requires an implicit-hydrogen declared ligand graph")
    counters, names = Counter(), []
    for atom in mol.GetAtoms():
        symbol = atom.GetSymbol().upper(); counters[symbol] += 1
        names.append(f"{symbol}{counters[symbol]}")
    order = sorted(a["name"] for a in ligand)
    actual = {a["name"]: a["element"].upper() for a in ligand}
    require(len(order) == len(set(order)) == mol.GetNumAtoms(), "Ligand names duplicated/missing")
    require(actual == {names[a.GetIdx()]: a.GetSymbol().upper() for a in mol.GetAtoms()}, "Named graph element mismatch")
    graph = {
        "canonical_isomeric_smiles": canonical(declared),
        "atoms": sorted((names[a.GetIdx()], a.GetSymbol(), a.GetFormalCharge(), str(a.GetChiralTag()), a.GetIsAromatic()) for a in mol.GetAtoms()),
        "bonds": sorted((tuple(sorted((names[b.GetBeginAtomIdx()], names[b.GetEndAtomIdx()]))), b.GetBondTypeAsDouble(), str(b.GetStereo()), b.GetIsAromatic()) for b in mol.GetBonds()),
    }
    matches = mol.GetSubstructMatches(mol, uniquify=False, useChirality=True, maxMatches=10001)
    require(0 < len(matches) < 10001, "Graph automorphism enumeration empty or exceeds bound")
    name_index, mol_index = {n: i for i, n in enumerate(order)}, {n: i for i, n in enumerate(names)}
    permutations = [[name_index[names[match[mol_index[name]]]] for name in order] for match in matches]
    require(list(range(len(order))) in permutations, "Graph automorphisms omit identity")
    return graph, permutations, order


def ligand_rmsd(moving_ca, fixed_ca, moving_ligand, fixed_ligand, permutations):
    alignment = kabsch(moving_ca, fixed_ca)
    xyz = moving_ligand @ np.asarray(alignment["rotation"]) + alignment["translation"]
    fixed_name = float(np.sqrt(np.mean(np.sum((xyz - fixed_ligand) ** 2, axis=1))))
    symmetric = min(float(np.sqrt(np.mean(np.sum((xyz - fixed_ligand[p]) ** 2, axis=1)))) for p in permutations)
    return fixed_name, symmetric, alignment


def self_test():
    # Explicit mathematical fixtures, separate from all archived observations.
    require(jaccard(set(), set()) is None, "Empty contact sets must not imply perfect agreement")
    require(jaccard({1}, set()) == 0 and jaccard({1, 2}, {2, 3}) == 1 / 3, "Jaccard mismatch")
    distances = np.array([[4.0, 5.0], [4.00001, 5.00001], [1.9, 7.0]])
    _, a = contacts(distances, np.array([1, 2, 3]), 4.0)
    _, b = contacts(distances, np.array([1, 2, 3]), 5.0)
    require(a == {1, 3} and b == {1, 2, 3}, "Inclusive contact threshold changed")
    receptor = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 2.0]])
    lig = np.array([[.5, .5, .5], [.7, .4, .8]])
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    shift = np.array([7., -3., 2.])
    value, _, fit = ligand_rmsd(receptor @ rotation + shift, receptor, lig @ rotation + shift, lig, [[0, 1]])
    require(value < 1e-12 and abs(fit["rotation_determinant"] - 1) < 1e-12, "Rigid-frame invariance failed")
    value, _, _ = ligand_rmsd(receptor, receptor, lig + [0, 0, 3], lig, [[0, 1]])
    require(abs(value - 3) < 1e-12, "Ligand must not be independently fitted")
    fixed, minimized, _ = ligand_rmsd(receptor, receptor, lig[::-1], lig, [[0, 1], [1, 0]])
    require(fixed > 0 and minimized < 1e-12, "Graph permutation minimization failed")
    try:
        named_graph({"_chem_comp.id": ["LIG"], "_chem_comp.pdbx_smiles": ["CO"]}, "LIG",
                    [{"name": "C1", "element": "C"}, {"name": "O1", "element": "N"}])
    except ValueError:
        pass
    else:
        raise ValueError("Element substitution must fail named-graph validation")
    return {"status": "passed", "scope": "Explicit mathematical fixtures: contact boundaries, empty Jaccard, rigid-frame invariance, no separate ligand fit, exact permutation minimization, rejected atom-element substitution"}


class Audit:
    def __init__(self, report, output):
        self.output = output; output.mkdir(parents=True, exist_ok=True)
        self.sources, self.checks, self.sample_rows, self.residue_rows = {}, [], [], []
        self.atom_contacts, self.pair_rows, self.pairs, self.samples = [], [], [], []
        self.graphs, self.group_data = {}, {}
        self.report = self.read(report)
        self.receipts = {
            "baseline": self.read(ROOT / "docs/af3-trained-selected-predictions.json"),
            "msa": self.read(ROOT / "docs/af3-msa-selected-predictions.json"),
        }
        self.target = self.read(ROOT / "data/ptgs2.json")
        self.sequence = self.target["sequence"]
        self.verify(self.report["passed"] and len(self.sequence) == 604, "Prior comparison passed; full target length 604")
        self.track(Path(__file__)); self.track(ROOT / "scripts/verify_af3_msa_comparison.py")
        self.verify(len({self.report[m][l]["conditions"]["model_content_sha256"] for m in ("baseline", "msa") for l in ("aspirin", "quercetin")}) == 1, "Four jobs have the same trained-model content SHA")
        for key in ("target_sequence_sha256", "seeds", "samples_per_seed", "recycles",
                    "af3_version", "af3_commit", "entrypoint_sha256", "max_template_date"):
            values = [self.report[m][l]["conditions"][key] for m in ("baseline", "msa") for l in ("aspirin", "quercetin")]
            self.verify(all(value == values[0] for value in values), "Four-condition equality: " + key)

    def track(self, path):
        path = Path(path).resolve(); key = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        self.sources[key] = {"sha256": digest(path), "bytes": path.stat().st_size}
        return path

    def read(self, path):
        return json.loads(self.track(path).read_text())

    def verify(self, condition, message):
        require(condition, message); self.checks.append({"check": message, "passed": True})

    def csv(self, name, rows):
        require(rows, f"Empty table: {name}")
        with (self.output / name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

    def sample(self, mode, molecule, entry, sample):
        label = f"{mode}/{molecule}/sample-{sample['sample']}"
        path = self.track(sample["structure_path"])
        confidence_path = path.with_name(path.name.replace("_model.cif", "_confidences.json"))
        summary_path = path.with_name(path.name.replace("_model.cif", "_summary_confidences.json"))
        confidence, summary = self.read(confidence_path), self.read(summary_path)
        self.verify(digest(path) == sample["structure_sha256"] and digest(confidence_path) == sample["confidence_sha256"] and digest(summary_path) == sample["summary_sha256"], label + ": three raw hashes match prior audit")
        cif = MMCIF2Dict(str(path)); atoms = atom_rows(cif)
        self.verify(molecular_table_sha256(cif) == sample["molecular_table_sha256"], label + ": raw molecular table matches audit")
        ligand, identity = verify_raw_identity(cif, atoms, self.sequence, entry["canonical_smiles"], "A", "B")
        self.verify(identity == sample["raw_identity"], label + ": full sequence and stereo graph revalidated")
        self.verify({a["chain"] for a in atoms} == {"A", "B"} and all(a["alt"] in {".", "?", ""} and a["occupancy"] == 1 for a in atoms), label + ": two chains without alternate/partial occupancy")
        graph, permutations, order = named_graph(cif, identity["component"], ligand)
        if molecule in self.graphs:
            self.verify(self.graphs[molecule] == graph, label + ": identical named stereo graph across samples")
        else:
            self.graphs[molecule] = graph
        named = {a["name"]: a for a in ligand}; ligand = [named[name] for name in order]
        protein = [a for a in atoms if a["chain"] == "A" and a["element"].upper() not in {"H", "D"}]
        inventory = [(a["position"], a["residue"], a["name"], a["element"]) for a in protein]
        self.verify(len(inventory) == len(set(inventory)), label + ": unique receptor heavy atoms")
        ca, alternatives = select_ca(atoms, "A")
        self.verify(set(ca) == set(range(1, 605)) and not alternatives, label + ": all 604 C-alpha atoms present")
        xyz, lig_xyz = np.array([a["xyz"] for a in protein]), np.array([a["xyz"] for a in ligand])
        distances = cdist(xyz, lig_xyz)
        self.verify(np.allclose(distances, np.linalg.norm(xyz[:, None] - lig_xyz[None, :], axis=2), rtol=0, atol=1e-12), label + ": independent all-pair distances agree")
        self.verify(confidence["atom_chain_ids"] == [a["chain"] for a in atoms] and np.allclose(confidence["atom_plddts"], [a["bfactor"] for a in atoms], rtol=0, atol=.011), label + ": atom order and pLDDT match CIF")
        tokens, token_res = confidence["token_chain_ids"], confidence["token_res_ids"]
        expected = Counter({("A", i): 1 for i in range(1, 605)}); expected[("B", 1)] = len(ligand)
        self.verify(Counter(zip(tokens, token_res, strict=True)) == expected, label + ": PAE token multiplicity verified")
        pae = np.asarray(confidence["pae"], dtype=float)
        self.verify(pae.shape == (len(tokens), len(tokens)) and np.isfinite(pae).all() and (pae >= 0).all(), label + ": finite correctly sized PAE")
        token_protein = {residue: i for i, (chain, residue) in enumerate(zip(tokens, token_res, strict=True)) if chain == "A"}
        pa = [token_protein[i] for i in range(1, 605)]; pb = [i for i, chain in enumerate(tokens) if chain == "B"]
        pair_minima = [[float(pae[np.ix_(a, b)].min()) for b in (pa, pb)] for a in (pa, pb)]
        self.verify(np.allclose(pair_minima, summary["chain_pair_pae_min"], atol=.055001, rtol=0), label + ": PAE minima agree with summary")
        positions = np.asarray([a["position"] for a in protein])
        res_min = {pos: float(distances[positions == pos].min()) for pos in range(1, 605)}
        common = {"condition": mode, "molecule": molecule, "job_id": entry["job_id"], "seed": sample["seed"], "sample": sample["sample"]}
        summary_row = {**common, "protein_heavy_atoms": len(protein), "ligand_heavy_atoms": len(ligand),
                       "minimum_interchain_distance_angstrom": float(distances.min()), "interchain_pairs_below_2_angstrom": int((distances < 2.0).sum()),
                       "ptm": summary["ptm"], "iptm": summary["iptm"], "has_clash_upstream": summary["has_clash"],
                       "ligand_mean_plddt": float(np.mean([a["bfactor"] for a in ligand])),
                       "pae_protein_frame_ligand_all_mean_angstrom": float(pae[np.ix_(pa, pb)].mean()),
                       "pae_ligand_frame_protein_all_mean_angstrom": float(pae[np.ix_(pb, pa)].mean())}
        self.verify(all(summary[k] == sample["summary_metrics"][k] for k in ("ptm", "iptm", "has_clash")) and abs(summary_row["ligand_mean_plddt"] - sample["ligand_heavy_atom_plddt"]["mean"]) < 1e-10, label + ": confidence agrees with audit")
        sets = {}
        for cutoff in (4.0, 5.0):
            mask, residues = contacts(distances, positions, cutoff); sets[int(cutoff)] = residues
            self.verify(residues == {pos for pos, value in res_min.items() if value <= cutoff}, label + f": {cutoff} A thresholds agree")
            indices = [token_protein[pos] for pos in sorted(residues)]
            summary_row.update({f"atom_pairs_le_{int(cutoff)}A": int(mask.sum()), f"residues_le_{int(cutoff)}A": len(residues),
                                f"contacting_ligand_atoms_le_{int(cutoff)}A": int(mask.any(axis=0).sum()),
                                f"pae_protein_frame_ligand_contacts_{int(cutoff)}A_mean_angstrom": float(pae[np.ix_(indices, pb)].mean()) if indices else None})
        self.verify(sets[4] <= sets[5], label + ": 4 A contacts subset 5 A contacts")
        for i, j in zip(*np.where(distances <= 5.0), strict=True):
            a, b = protein[i], ligand[j]
            self.atom_contacts.append({**common, "protein_chain": "A", "uniprot_position": a["position"], "residue_name": a["residue"], "protein_atom": a["name"], "protein_element": a["element"], "ligand_chain": "B", "ligand_atom": b["name"], "ligand_element": b["element"], "distance_angstrom": float(distances[i, j]), "within_4A": bool(distances[i, j] <= 4.0), "below_2A_screen": bool(distances[i, j] < 2.0)})
        for position in range(1, 605):
            token = token_protein[position]
            self.residue_rows.append({**common, "protein_chain": "A", "uniprot_position": position, "residue_name": ca[position]["residue"], "minimum_ligand_heavy_distance_angstrom": res_min[position], "contact_le_4A": position in sets[4], "contact_le_5A": position in sets[5], "ca_plddt": ca[position]["bfactor"], "pae_residue_frame_all_ligand_mean_angstrom": float(pae[token, pb].mean()), "pae_ligand_frames_residue_mean_angstrom": float(pae[pb, token].mean())})
        near_i, near_j = np.unravel_index(int(distances.argmin()), distances.shape)
        serial = {**summary_row, "structure_path": str(path.relative_to(ROOT)), "structure_sha256": digest(path),
                  "confidence_sha256": digest(confidence_path), "named_graph_sha256": compact_digest(graph),
                  "graph_automorphism_count": len(permutations), "ligand_atom_order": order,
                  "contact_residues": {str(k): sorted(v) for k, v in sets.items()},
                  "minimum_distance_atoms": {"protein_position": protein[near_i]["position"], "protein_residue": protein[near_i]["residue"], "protein_atom": protein[near_i]["name"], "ligand_atom": ligand[near_j]["name"]}}
        self.sample_rows.append(summary_row); self.samples.append(serial)
        return {"summary": serial, "ca": np.asarray([ca[i]["xyz"] for i in range(1, 605)]), "ligand": lig_xyz,
                "sets": sets, "residue_min": res_min, "residue_names": {i: ca[i]["residue"] for i in ca},
                "permutations": permutations, "graph": graph, "inventory": inventory}

    def run(self):
        for mode in ("baseline", "msa"):
            for molecule in ("aspirin", "quercetin"):
                entry = self.report[mode][molecule]
                receipt = self.receipts[mode]["jobs"][molecule]
                job = receipt["job"]
                self.verify(self.receipts[mode]["genuine_inference_completed"] is True
                            and job["id"] == entry["job_id"] and job["status"] == "completed"
                            and job["result"]["execution_verified"] is True and job["result"]["return_code"] == 0
                            and receipt["output_validation"]["identity_verified"] is True,
                            f"{mode}/{molecule}: selected receipt confirms completed verified execution")
                self.verify(not job["payload"].get("bondedAtomPairs"),
                            f"{mode}/{molecule}: no declared inter-chain covalent linkage")
                self.verify(entry["sample_count"] == 5 and [s["sample"] for s in entry["samples"]] == list(range(5)) and all(s["seed"] == 1 and not s["is_top_ranked_copy"] for s in entry["samples"]), f"{mode}/{molecule}: five unique seed-1 samples")
                self.verify(entry["conditions"]["target_sequence_sha256"] == hashlib.sha256(self.sequence.encode()).hexdigest() and entry["conditions"]["samples_per_seed"] == 5 and entry["conditions"]["recycles"] == 10 and entry["persisted_output_validation"]["identity_verified"] is True, f"{mode}/{molecule}: preserved sequence/settings and identity")
                data = [self.sample(mode, molecule, entry, sample) for sample in entry["samples"]]
                self.group_data[(mode, molecule)] = data
                self.verify(all(d["inventory"] == data[0]["inventory"] for d in data), f"{mode}/{molecule}: identical receptor heavy-atom inventory")
                construct = entry["samples"][0]["reference_alignment"]["matched_uniprot_positions"]
                self.verify(len(construct) == 551 and all(s["reference_alignment"]["matched_uniprot_positions"] == construct for s in entry["samples"]), f"{mode}/{molecule}: complete common SIFTS alignment set")
                subset = np.asarray(construct) - 1
                for i, j in combinations(range(5), 2):
                    a, b = data[i], data[j]
                    require(a["graph"] == b["graph"], "Cross-graph ligand RMSD prohibited")
                    fixed, minimized, fit = ligand_rmsd(a["ca"], b["ca"], a["ligand"], b["ligand"], a["permutations"])
                    rev, _, _ = ligand_rmsd(b["ca"], a["ca"], b["ligand"], a["ligand"], a["permutations"])
                    fixed_subset, sym_subset, fit_subset = ligand_rmsd(a["ca"][subset], b["ca"][subset], a["ligand"], b["ligand"], a["permutations"])
                    self.verify(abs(fixed - rev) < 1e-9 and minimized <= fixed + 1e-10 and abs(fit["rotation_determinant"] - 1) < 1e-9, f"{mode}/{molecule}/{i}-{j}: symmetric RMSD/proper rotation")
                    row = {"condition": mode, "molecule": molecule, "job_id": entry["job_id"], "seed": 1, "sample_i": i, "sample_j": j,
                           "contact_jaccard_4A": jaccard(a["sets"][4], b["sets"][4]), "contact_jaccard_5A": jaccard(a["sets"][5], b["sets"][5]),
                           "ligand_rmsd_receptor_604CA_fixed_names_angstrom": fixed, "ligand_rmsd_receptor_604CA_graph_min_angstrom": minimized,
                           "receptor_604CA_rmsd_angstrom": fit["rmsd_angstrom"], "ligand_rmsd_receptor_551CA_fixed_names_angstrom": fixed_subset,
                           "ligand_rmsd_receptor_551CA_graph_min_angstrom": sym_subset, "receptor_551CA_rmsd_angstrom": fit_subset["rmsd_angstrom"],
                           "graph_automorphism_count": len(a["permutations"])}
                    self.pair_rows.append(row)
                    self.pairs.append({**row, "receptor_604CA_alignment": fit, "receptor_551CA_alignment": fit_subset, "sensitivity_uniprot_positions": construct})
        self.verify(len(self.samples) == 20 and len({s["structure_sha256"] for s in self.samples}) == 20 and len(self.pairs) == 40, "Twenty unique CIFs/forty within-condition pairs; no top-copy inflation")
        self.csv("sample_contact_summary.csv", self.sample_rows)
        self.csv("residue_contact_profile.csv", self.residue_rows)
        self.csv("atom_contacts_within_5A.csv", self.atom_contacts)
        self.csv("sample_pair_stability.csv", self.pair_rows)
        groups = []
        for (mode, molecule), data in self.group_data.items():
            pair_rows = [p for p in self.pair_rows if p["condition"] == mode and p["molecule"] == molecule]
            result = {"condition": mode, "molecule": molecule, "samples": 5, "within_condition_pairs": 10,
                      "top_ranked_sample_index": self.report[mode][molecule]["top_copy_matching_samples"][0],
                      "minimum_interchain_distances_angstrom": stats([d["summary"]["minimum_interchain_distance_angstrom"] for d in data]),
                      "samples_with_interchain_pair_below_2A": sum(d["summary"]["interchain_pairs_below_2_angstrom"] > 0 for d in data),
                      "graph_automorphism_count": len(data[0]["permutations"])}
            for threshold in (4, 5):
                union = set.union(*(d["sets"][threshold] for d in data)); intersection = set.intersection(*(d["sets"][threshold] for d in data))
                result[f"contacts_{threshold}A"] = {"union_positions": sorted(union), "union_count": len(union), "all_five_positions": sorted(intersection), "all_five_count": len(intersection),
                    "per_sample_residue_counts": [len(d["sets"][threshold]) for d in data], "per_sample_atom_pair_counts": [d["summary"][f"atom_pairs_le_{threshold}A"] for d in data],
                    "pairwise_jaccard": stats([p[f"contact_jaccard_{threshold}A"] for p in pair_rows if p[f"contact_jaccard_{threshold}A"] is not None])}
            for field in ("ligand_rmsd_receptor_604CA_fixed_names_angstrom", "ligand_rmsd_receptor_551CA_fixed_names_angstrom", "receptor_604CA_rmsd_angstrom", "receptor_551CA_rmsd_angstrom"):
                result[field] = stats([p[field] for p in pair_rows])
            groups.append(result)
        return {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "status": "passed",
                "analysis": "Archived AF3 ligand contact and placement consistency", "new_inference_jobs": 0,
                "target": {"accession": self.target["accession"], "chain": "A", "sequence_length": len(self.sequence),
                           "residue_numbering": "AF3 label_seq_id 1..604 verified against full UniProt P35354 sequence; not legacy COX numbering"},
                "source_definition": "Audited raw mmCIF and same-sample confidence/summary JSON, rehashed and revalidated. Provenance assurance remains bounded by the original audit.",
                "contact_definition": "Inclusive Euclidean heavy-atom inter-chain distances <=4.0 and <=5.0 angstrom; no hydrogens, periodic images, symmetry mates or relaxation. Atom-pair counts differ from deduplicated residue counts.",
                "empty_contact_jaccard": "null when both empty; zero if only one empty",
                "pae_definition": "Serialized matrix at 0.1 angstrom precision; row is frame, column assessed token. All-chain means use all 604 protein tokens/all ligand heavy-atom tokens. Contact means restrict protein rows to that sample's contact residues, then average all ligand columns.",
                "sources": [{"title": "AF3 v3.0.4 output definitions", "url": DOC}], "limitations": LIMITATIONS,
                "self_tests": self_test(), "groups": groups, "samples": self.samples, "pairs": self.pairs,
                "named_ligand_graphs": self.graphs, "input_sources": self.sources, "numerical_checks": self.checks}


def figure(audit, report, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 10,
                         "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "pdf.fonttype": 42, "svg.fonttype": "none",
                         "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#B8C4CA",
                         "text.color": "#183B56", "axes.labelcolor": "#183B56"})
    navy, teal, ochre, magenta = "#183B56", "#158A88", "#C58B24", "#A54678"
    fig = plt.figure(figsize=(7.4, 8.6)); grid = fig.add_gridspec(3, 2, height_ratios=[1.4, 1, 1], hspace=.64, wspace=.52)
    fig.subplots_adjust(left=.12, right=.94, top=.92, bottom=.14)
    fig.suptitle("Ligand consistency within five archived MSA + template samples", x=.5, y=.985, fontsize=10)
    def title(ax, letter, label):
        ax.set_title(label, loc="left", pad=9)
        ax.text(-.15, 1.03, letter.lower(), transform=ax.transAxes, weight="bold", fontsize=12, va="bottom")
    cmap = ListedColormap([navy, teal, "#D8E9E6", "#F0F2F4"])
    for col, molecule in enumerate(("aspirin", "quercetin")):
        data = audit.group_data[("msa", molecule)]
        union = sorted(set.union(*(d["sets"][5] for d in data)))
        ax = fig.add_subplot(grid[0, col]); title(ax, "AB"[col], molecule.capitalize() + ": residue contacts")
        values = np.asarray([[d["residue_min"][pos] for d in data] for pos in union])
        bins = np.where(values <= 3, 0, np.where(values <= 4, 1, np.where(values <= 5, 2, 3)))
        ax.imshow(bins, cmap=cmap, vmin=0, vmax=3, aspect="auto", interpolation="nearest")
        ax.set_yticks(range(len(union)), [f"{data[0]['residue_names'][pos]} {pos}" for pos in union])
        ax.set_xticks(range(5), [f"s{i}" for i in range(5)]); ax.set_xlabel("Diffusion sample (seed 1)")
        ax.set_ylabel("P35354 sequence position")
        ax.set_xticks(np.arange(-.5, 5), minor=True); ax.set_yticks(np.arange(-.5, len(union)), minor=True)
        ax.grid(which="minor", color="white", linewidth=.7); ax.tick_params(which="minor", left=False, bottom=False)
        for i in range(len(union)):
            for j in range(5):
                if values[i, j] <= 5:
                    ax.text(j, i, f"{values[i,j]:.1f}", ha="center", va="center", fontsize=6.4, color="white" if bins[i,j] <= 1 else navy)
        matrix = np.zeros((5, 5))
        for row in audit.pair_rows:
            if row["condition"] == "msa" and row["molecule"] == molecule:
                i, j = row["sample_i"], row["sample_j"]
                matrix[i,j] = matrix[j,i] = row["ligand_rmsd_receptor_604CA_fixed_names_angstrom"]
        ax = fig.add_subplot(grid[1, col]); title(ax, "CD"[col], molecule.capitalize() + ": ligand RMSD")
        im = ax.imshow(matrix, cmap="magma_r", vmin=0, vmax=7)
        if col == 1:
            bar = fig.colorbar(im, ax=ax, fraction=.035, pad=.065, ticks=[0,3.5,7])
            bar.ax.tick_params(labelsize=7)
        ax.set_xticks(range(5), [f"s{i}" for i in range(5)]); ax.set_yticks(range(5), [f"s{i}" for i in range(5)])
        ax.set_xlabel("Pairwise RMSD (Å); 604-Cα receptor fit")
        for i in range(5):
            for j in range(5):
                ax.text(j, i, "0" if i == j else f"{matrix[i,j]:.2f}", ha="center", va="center", fontsize=7.2, color="white" if matrix[i,j] > 3.4 else navy)
    ax = fig.add_subplot(grid[2, 0]); title(ax, "E", "Residue-set overlap")
    for mi, molecule in enumerate(("aspirin", "quercetin")):
        pairs = [r for r in audit.pair_rows if r["condition"] == "msa" and r["molecule"] == molecule]
        for offset, cutoff, color in ((-.16, 4, ochre), (.16, 5, teal)):
            values = [r[f"contact_jaccard_{cutoff}A"] for r in pairs]
            ax.scatter(mi+offset+np.linspace(-.055,.055,10), values, s=17, color=color, alpha=.8,
                       edgecolors="none", label=f"≤{cutoff} Å" if mi == 0 else None)
    ax.set_xticks([0,1], ["Aspirin","Quercetin"]); ax.set_xlim(-.45,1.45); ax.set_ylim(-.025,1.08)
    ax.set_ylabel("Contact-residue Jaccard"); ax.grid(axis="y", color="#E4E9ED", linewidth=.5)
    ax.legend(frameon=False, fontsize=7, loc="lower right")
    ax = fig.add_subplot(grid[2, 1]); title(ax, "F", "Directional PAE")
    fields = [("pae_protein_frame_ligand_all_mean_angstrom",navy,-.22,"P→L, all"),
              ("pae_protein_frame_ligand_contacts_5A_mean_angstrom",teal,0,"P→L, contacts"),
              ("pae_ligand_frame_protein_all_mean_angstrom",magenta,.22,"L→P, all")]
    for mi, molecule in enumerate(("aspirin","quercetin")):
        data = audit.group_data[("msa",molecule)]
        for field,color,offset,label in fields:
            ax.scatter(mi+offset+np.linspace(-.04,.04,5), [d["summary"][field] for d in data], s=18,
                       color=color, alpha=.8, edgecolors="none", label=label if mi == 0 else None)
    ax.set_xticks([0,1],["Aspirin","Quercetin"]); ax.set_xlim(-.5,1.5); ax.set_ylim(0,24)
    ax.set_ylabel("Mean predicted error (Å)"); ax.grid(axis="y",color="#E4E9ED",linewidth=.5)
    ax.legend(frameon=False,fontsize=6.8,loc="center left",bbox_to_anchor=(.01,.51))
    fig.legend(handles=[Patch(facecolor=c,label=l) for c,l in zip([navy,teal,"#D8E9E6","#F0F2F4"], ["≤3 Å",">3–4 Å",">4–5 Å",">5 Å"],strict=True)],
               loc="lower center",bbox_to_anchor=(.5,.035),ncols=4,frameon=False,fontsize=8,
               title="a/b: nearest ligand–residue heavy-atom distance (Å)",title_fontsize=8)
    fig.text(.10,.014,"Descriptive diffusion variability; no independent replicates, affinity or experimental-pose accuracy.",fontsize=7.3)
    fig.canvas.draw(); renderer=fig.canvas.get_renderer();width,height=fig.canvas.get_width_height()
    outside=[]
    for text in fig.findobj(matplotlib.text.Text):
        if text.get_visible() and text.get_text() and not text.get_clip_on():
            b=text.get_window_extent(renderer)
            if b.x0 < -1 or b.y0 < -1 or b.x1 > width+1 or b.y1 > height+1: outside.append(text.get_text())
    require(not outside, f"Figure text outside canvas: {outside}")
    stem="figure-S3-ligand-contact-stability";files=[]
    for suffix in ("svg","pdf","png"):
        path=output/f"{stem}.{suffix}";fig.savefig(path,dpi=600,facecolor="white")
        files.append({"path":str(path.relative_to(ROOT)),"sha256":digest(path),"bytes":path.stat().st_size})
    docx_path=output/f"{stem}.docx.png";fig.savefig(docx_path,dpi=240,facecolor="white")
    files.append({"path":str(docx_path.relative_to(ROOT)),"sha256":digest(docx_path),"bytes":docx_path.stat().st_size,"dpi":240})
    preview=ROOT/"tmp/manuscript-figures";preview.mkdir(parents=True,exist_ok=True)
    fig.savefig(preview/f"{stem}.png",dpi=150,facecolor="white");plt.close(fig)
    caption=("Supplementary Figure S3. Ligand contact and placement consistency in the five archived MSA-plus-template diffusion samples for each of aspirin and quercetin with full-length human PTGS2. "
             "(a,b) Every receptor residue with a ligand heavy-atom contact within 5.0 Å in any of the five samples is shown, in full UniProt P35354 numbering (1–604). Cells report minimum heavy-atom distance rounded to 0.1 Å; colored categories use exact distances with inclusive upper thresholds, and gray exceeds 5.0 Å. No residue subset was chosen for apparent stability. "
             "(c,d) All ten within-condition sample pairs are represented symmetrically, with a shared 0–7 Å color scale. Numbers are ligand heavy-atom RMSD after unweighted Kabsch alignment of all 604 corresponding receptor Cα atoms, without outlier removal or a separate ligand fit. Named-atom correspondence was checked against the identical declared stereo graph and complete atom inventory; each ligand has one exact graph automorphism. Diagonal zero is defined and symmetric entries are not extra observations. A separate 551-position SIFTS-construct fit tests alignment sensitivity in the source tables. "
             "(e) Ten pairwise contact-residue Jaccard values at each cutoff. (f) Five per-sample PAE means: protein-frame to ligand over all 604 protein tokens; protein-frame to ligand restricted to that sample's ≤5 Å contact residues; and ligand-frame to protein over all 604 tokens. Every mean includes all ligand heavy-atom tokens. P is protein, L ligand; direction is row-frame to assessed column. Contact-restricted PAE is selected using the same coordinates, not independent evidence. "
             "All results are actual archived diffusion samples at seed 1, not independent biological replicates. No confidence interval or hypothesis test is assigned. No-MSA results, atom-pair contacts, minimum inter-chain distances and <2.0 Å proximity counts for all twenty unique samples are retained in the source tables. This screen is not PoseBusters or complete steric validation. Contact stability, confidence and placement consistency do not establish affinity, pose accuracy, efficacy or safety. No new inference or coordinate repair was performed.")
    (output/f"{stem}.caption.txt").write_text(caption+"\n")
    return {"id":stem,"files":files,"caption":caption,"png_dpi":600,"size_inches":[7.4,8.6],
            "source_summary":"research/manuscript/q1_extension/af3/contact_analysis.json"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report",type=Path,default=ROOT/"docs/af3-msa-structure-comparison.json")
    parser.add_argument("--output",type=Path,default=ROOT/"research/manuscript/q1_extension/af3")
    parser.add_argument("--figure-output",type=Path,default=ROOT/"output/manuscript/figures")
    parser.add_argument("--skip-figure",action="store_true")
    parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(),indent=2));return
    audit=Audit(args.report,args.output);report=audit.run()
    if not args.skip_figure: report["figure"]=figure(audit,report,args.figure_output)
    report["software"]={"python":sys.version.split()[0],"numpy":np.__version__,"rdkit":Chem.rdBase.rdkitVersion,
                        "scipy":version("scipy"),"biopython":version("biopython"),"matplotlib":version("matplotlib")}
    report["tables"]=[{"path":str(p.relative_to(ROOT)),"sha256":digest(p),"bytes":p.stat().st_size} for p in sorted(args.output.glob("*.csv"))]
    (args.output/"contact_analysis.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    summary={key:report[key] for key in ("schema_version","created_at","status","analysis","new_inference_jobs","target",
                                       "source_definition","contact_definition","empty_contact_jaccard","pae_definition",
                                       "sources","limitations","self_tests","groups","software")}
    summary["analysis_report"]={"path":str((args.output/"contact_analysis.json").relative_to(ROOT)),
                                "sha256":digest(args.output/"contact_analysis.json")}
    summary["numerical_checks_passed"]=len(report["numerical_checks"])
    summary["tables"]=report["tables"]
    if "figure" in report: summary["figure"]=report["figure"]
    (args.output/"summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"status":report["status"],"samples":len(report["samples"]),"sample_pairs":len(report["pairs"]),
                      "checks":len(report["numerical_checks"]),"groups":report["groups"]},indent=2))


if __name__=="__main__": main()
