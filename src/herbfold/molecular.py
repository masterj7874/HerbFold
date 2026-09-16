"""Chemically labelled molecular scenes with explicit coordinate/bond provenance.

RDKit conformers are isolated-molecule geometry, never AlphaFold predictions.
mmCIF geometry is kept verbatim, including bad predictions: connectivity comes
from named chemical templates, not visually plausible nearest neighbours.
"""

from __future__ import annotations

import io
import math
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from rdkit import Chem, rdBase
from rdkit.Chem import AllChem, rdMolDescriptors

MAX_ATOMS = 250
MAX_STRUCTURE_ATOMS = 30_000
MAX_STRUCTURE_BYTES = 50_000_000
_MISSING = {"", ".", "?", "\x00"}
_ORDER = {"SING": 1.0, "DOUB": 2.0, "TRIP": 3.0, "AROM": 1.5, "QUAD": 4.0}


def _small_molecule(smiles):
    if not isinstance(smiles, str) or not smiles.strip() or len(smiles) > 5000:
        raise ValueError("SMILES must contain 1–5000 characters")
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(smiles)
    if mol is None or mol.GetNumAtoms() == 0:
        raise ValueError("Invalid SMILES")
    if mol.GetNumAtoms() > MAX_ATOMS:
        raise ValueError(f"Molecular viewer limit is {MAX_ATOMS} atoms including hydrogens")
    if any(a.GetAtomicNum() == 0 for a in mol.GetAtoms()):
        raise ValueError("Wildcard atoms do not define a complete chemical structure")
    if len(Chem.GetMolFrags(mol)) != 1:
        raise ValueError("Use one connected molecule; counterion placement is not predicted")
    return mol


def _bond(atoms, left, right, order, aromatic, provenance, *, order_known=True):
    a, b = atoms[left], atoms[right]
    distance = math.dist([a[k] for k in "xyz"], [b[k] for k in "xyz"])
    return {
        "source": left,
        "target": right,
        "order": float(order),
        "aromatic": bool(aromatic),
        "provenance": provenance,
        "bond_order_known": order_known,
        "length_angstrom": round(distance, 6),
    }


def _geometry(atoms, bonds):
    xyz = np.array([[a[k] for k in "xyz"] for a in atoms], dtype=float)
    lengths = [b["length_angstrom"] for b in bonds]
    # Conservative visual geometry flags, not a stereochemical validation score.
    return {
        "atom_count": len(atoms),
        "bond_count": len(bonds),
        "long_bond_count": sum(length > 2.4 for length in lengths),
        "short_bond_count": sum(length < 0.65 for length in lengths),
        "max_bond_length_angstrom": max(lengths, default=0),
        "coord_span_angstrom": [round(float(x), 6) for x in np.ptp(xyz, axis=0)],
        "center_angstrom": [round(float(x), 6) for x in xyz.mean(axis=0)],
        "method": "Named covalent bonds flagged when length <0.65 or >2.4 Å; not a validated clash score",
    }


def _generate(smiles, seed=42, include_hydrogens=True, num_conformers=4):
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise ValueError("seed must be an integer from 0 through 2147483647")
    if type(include_hydrogens) is not bool:
        raise ValueError("include_hydrogens must be a boolean")
    if type(num_conformers) is not int or not 1 <= num_conformers <= 4:
        raise ValueError("num_conformers must be an integer from 1 through 4")
    input_mol = _small_molecule(smiles)
    canonical = Chem.MolToSmiles(input_mol, isomericSmiles=True)
    unknown_stereo = [
        {"type": str(item.type), "centered_on": int(item.centeredOn)}
        for item in Chem.FindPotentialStereo(input_mol)
        if item.specified == Chem.StereoSpecified.Unspecified
    ]
    mol = Chem.AddHs(input_mol)
    if mol.GetNumAtoms() > MAX_ATOMS:
        raise ValueError(f"Molecular viewer limit is {MAX_ATOMS} atoms including hydrogens")
    # Keep the input graph: MMFF typing has its own aromaticity perception.
    original_graph = Chem.Mol(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.enforceChirality = True
    params.numThreads = 1
    params.maxIterations = 200
    params.pruneRmsThresh = 0.25
    conf_ids = list(AllChem.EmbedMultipleConfs(mol, numConfs=num_conformers, params=params))
    if not conf_ids:
        raise ValueError("ETKDGv3 could not embed this molecule within the bounded attempt budget")
    method = None
    if AllChem.MMFFHasAllMoleculeParams(mol):
        method = "MMFF94s"
        optimized = AllChem.MMFFOptimizeMoleculeConfs(
            mol,
            numThreads=1,
            maxIters=500,
            mmffVariant=method,
        )
    elif AllChem.UFFHasAllMoleculeParams(mol):
        method = "UFF"
        optimized = AllChem.UFFOptimizeMoleculeConfs(mol, numThreads=1, maxIters=500)
    else:
        optimized = [(-1, None) for _ in conf_ids]
    usable = [
        (cid, status, float(energy) if energy is not None and math.isfinite(energy) else None)
        for cid, (status, energy) in zip(conf_ids, optimized, strict=True)
    ]
    chosen, status, value = min(
        usable,
        key=lambda row: (row[1] != 0, row[2] if row[2] is not None else math.inf, row[0]),
    )
    original_graph.RemoveAllConformers()
    original_graph.AddConformer(Chem.Conformer(mol.GetConformer(chosen)), assignId=True)
    output = original_graph if include_hydrogens else Chem.RemoveHs(original_graph)
    coordinates = output.GetConformer()
    if not np.isfinite(coordinates.GetPositions()).all():
        raise ValueError("Conformer contains non-finite coordinates")
    atoms = []
    for atom in output.GetAtoms():
        index = atom.GetIdx()
        point = coordinates.GetAtomPosition(index)
        atoms.append(
            {
                "id": index,
                "index": index,
                "element": atom.GetSymbol(),
                "name": f"{atom.GetSymbol()}{index + 1}",
                "x": float(point.x),
                "y": float(point.y),
                "z": float(point.z),
                "formal_charge": atom.GetFormalCharge(),
                "aromatic": atom.GetIsAromatic(),
                "chain_id": "A",
                "residue_id": "1",
                "residue_name": "LIG",
                "sequence_id": None,
                "is_protein": False,
                "is_ligand": True,
                "confidence": None,
                "b_factor": None,
                "stereo": atom.GetProp("_CIPCode") if atom.HasProp("_CIPCode") else None,
            }
        )
    bonds = [
        _bond(
            atoms,
            b.GetBeginAtomIdx(),
            b.GetEndAtomIdx(),
            b.GetBondTypeAsDouble(),
            b.GetIsAromatic(),
            "input_smiles_graph",
        )
        for b in output.GetBonds()
    ]
    warnings = ["Isolated molecule conformer; not an AlphaFold 3 complex or a binding pose."]
    if unknown_stereo:
        warnings.append(
            "Input stereochemistry is incomplete; this 3D conformer does not resolve unspecified stereoisomers."
        )
    if status != 0:
        warnings.append(
            "Force-field minimization did not converge."
            if method
            else "No complete MMFF/UFF parameters; ETKDG geometry shown without force-field energy."
        )
    energy = {
        "method": method,
        "value_kcal_mol": value,
        "converged": status == 0,
        "optimization_status": status,
        "max_iterations": 500,
        "interpretation": "Intramolecular force-field energy; not affinity or free energy of binding",
    }
    scene = {
        "schema_version": 1,
        "source": "rdkit_conformer",
        "label": "RDKit ETKDGv3 molecular conformer",
        "atoms": atoms,
        "bonds": bonds,
        "chains": [{"id": "A", "atom_count": len(atoms), "residue_count": 1}],
        "residues": [
            {
                "id": "1",
                "chain_id": "A",
                "name": "LIG",
                "sequence_id": None,
                "atom_indices": list(range(len(atoms))),
            }
        ],
        "metadata": {
            "input_smiles": smiles,
            "smiles": canonical,
            "formula": rdMolDescriptors.CalcMolFormula(input_mol),
            "formal_charge": Chem.GetFormalCharge(input_mol),
            "seed": seed,
            "conformers_requested": num_conformers,
            "conformers_embedded": len(conf_ids),
            "selected_conformer_id": chosen,
            "include_hydrogens": include_hydrogens,
            "rdkit_version": rdBase.rdkitVersion,
            "coordinate_units": "angstrom",
            "embedding": "ETKDGv3",
            "enforce_chirality": True,
            "unspecified_stereo": unknown_stereo,
        },
        "geometry": _geometry(atoms, bonds),
        "warnings": warnings,
        "energy": energy,
    }
    return output, scene


def conformer(smiles, seed=42, include_hydrogens=True, num_conformers=4):
    """Return the best converged conformer among at most four seeded embeddings."""
    return _generate(smiles, seed, include_hydrogens, num_conformers)[1]


def conformer_sdf(smiles, seed=42, include_hydrogens=True, num_conformers=4):
    mol, scene = _generate(smiles, seed, include_hydrogens, num_conformers)
    mol.SetProp("_Name", "HerbFold isolated molecule conformer")
    mol.SetProp("COORDINATE_SOURCE", scene["source"])
    mol.SetProp("INPUT_SMILES", smiles)
    mol.SetProp("WARNINGS", " ".join(scene["warnings"]))
    mol.SetProp("FORCE_FIELD", scene["energy"]["method"] or "unavailable")
    mol.SetProp("MINIMIZATION_CONVERGED", str(scene["energy"]["converged"]))
    if scene["energy"]["value_kcal_mol"] is not None:
        mol.SetProp("INTRAMOLECULAR_ENERGY_KCAL_MOL", str(scene["energy"]["value_kcal_mol"]))
    output = io.StringIO()
    writer = Chem.SDWriter(output)
    writer.write(mol)
    writer.flush()
    result = output.getvalue()
    writer.close()
    return result


def read_cif(path):
    path = Path(path)
    if path.stat().st_size > MAX_STRUCTURE_BYTES:
        raise ValueError("Structure exceeds the 50 MB limit")
    try:
        cif = MMCIF2Dict(str(path))
    except (ValueError, IndexError, StopIteration) as exc:
        raise ValueError("Malformed mmCIF structure") from exc
    if not cif.get("_atom_site.Cartn_x"):
        raise ValueError("mmCIF contains no atom_site coordinates")
    return cif


def _column(cif, key, length, default=None, *, required=False):
    value = cif.get(key)
    if value is None:
        if required:
            raise ValueError(f"mmCIF missing required column {key}")
        return [default] * length
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"mmCIF column length mismatch: {key}")
    return value


@lru_cache(maxsize=1)
def protein_templates():
    templates = {}
    for letter in "ACDEFGHIKLMNPQRSTVWY":
        mol = Chem.MolFromSequence(letter)
        names = {a.GetIdx(): a.GetPDBResidueInfo().GetName().strip() for a in mol.GetAtoms()}
        comp = mol.GetAtomWithIdx(0).GetPDBResidueInfo().GetResidueName()
        templates[comp] = [
            (names[b.GetBeginAtomIdx()], names[b.GetEndAtomIdx()], b.GetBondTypeAsDouble(), b.GetIsAromatic())
            for b in mol.GetBonds()
        ]
    # RDKit MolFromSequence("I") names the methylene branch CG2, whereas
    # wwPDB ILE names it CG1. Coordinates in PDB/AF3 use the wwPDB names.
    # Use the official named graph, including the optional terminal OXT;
    # never relabel or move observed atoms to fit the RDKit template.
    # https://files.rcsb.org/ligands/download/ILE.cif
    templates["ILE"] = [
        ("N", "CA", 1.0, False),
        ("CA", "C", 1.0, False),
        ("C", "O", 2.0, False),
        ("C", "OXT", 1.0, False),
        ("CA", "CB", 1.0, False),
        ("CB", "CG1", 1.0, False),
        ("CB", "CG2", 1.0, False),
        ("CG1", "CD1", 1.0, False),
    ]
    return templates


def ccd_templates(cif):
    """Read explicit named component bonds, skipping unknown order assignments."""
    left = cif.get("_chem_comp_bond.atom_id_1", [])
    count = len(left)
    if count > 200_000:
        raise ValueError("Chemical template bond count exceeds limit")
    right = _column(cif, "_chem_comp_bond.atom_id_2", count, required=bool(count))
    components = _column(cif, "_chem_comp_bond.comp_id", count, required=bool(count))
    orders = _column(cif, "_chem_comp_bond.value_order", count, "?")
    aromatic = _column(cif, "_chem_comp_bond.pdbx_aromatic_flag", count, "N")
    output = defaultdict(list)
    for comp, a, b, order, aromatic_flag in zip(components, left, right, orders, aromatic, strict=True):
        if order.upper() in _ORDER:
            is_aromatic = aromatic_flag == "Y" or order.upper() == "AROM"
            output[comp].append((a, b, 1.5 if is_aromatic else _ORDER[order.upper()], is_aromatic))
    return dict(output)


def _af3_smiles_template(smiles, observed):
    """Reproduce upstream AF3 v3 atom-name assignment, then validate names/elements.

    This is NOT mapping the coordinate row order to RDKit order. AF3 assigns
    ELEMENT+per-element-counter names from its original SMILES graph, which can
    be safely reconstructed only with that exact SMILES and a full name match.
    """
    mol = _small_molecule(smiles)
    counts = Counter()
    names = {}
    expected = {}
    for atom in mol.GetAtoms():
        element = atom.GetSymbol()
        counts[element] += 1
        name = f"{element.upper()}{counts[element]}"
        names[atom.GetIdx()] = name
        if element not in {"H", "D"}:
            expected[name] = element
    actual = {a["name"]: a["element"] for a in observed if a["element"] not in {"H", "D"}}
    if actual != expected or len(actual) != len([a for a in observed if a["element"] not in {"H", "D"}]):
        return None
    return [
        (names[b.GetBeginAtomIdx()], names[b.GetEndAtomIdx()], b.GetBondTypeAsDouble(), b.GetIsAromatic())
        for b in mol.GetBonds()
    ]


def _optional_float(value):
    if value in _MISSING or value is None:
        return None
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("mmCIF contains a malformed numeric annotation") from exc
    if not math.isfinite(result):
        raise ValueError("mmCIF contains non-finite numeric annotations")
    return result


def structure_scene(path, *, source="structure_file", label=None, ligand_smiles=None, templates=None):
    """First-model mmCIF atom graph; coordinates are not repaired or synthesized."""
    if source not in {"structure_file", "alphafold3_prediction", "experimental_pdb"}:
        raise ValueError("Unrecognized structure source")
    cif = read_cif(path)
    count = len(cif["_atom_site.Cartn_x"])
    if count > MAX_STRUCTURE_ATOMS:
        raise ValueError(
            f"Interactive scene limit is {MAX_STRUCTURE_ATOMS} atom records; use the original mmCIF for larger structures"
        )
    specs = {
        "x": ("Cartn_x", None, True),
        "y": ("Cartn_y", None, True),
        "z": ("Cartn_z", None, True),
        "element": ("type_symbol", None, True),
        "name": ("label_atom_id", None, True),
        "chain_id": ("label_asym_id", None, True),
        "residue_name": ("label_comp_id", None, True),
        "sequence_id": ("label_seq_id", ".", False),
        "auth_residue_id": ("auth_seq_id", ".", False),
        "b_factor": ("B_iso_or_equiv", None, False),
        "model": ("pdbx_PDB_model_num", "1", False),
        "altloc": ("label_alt_id", ".", False),
        "occupancy": ("occupancy", "1", False),
        "insertion_code": ("pdbx_PDB_ins_code", "?", False),
        "formal_charge": ("pdbx_formal_charge", None, False),
    }
    columns = {
        k: _column(cif, f"_atom_site.{col}", count, default, required=req)
        for k, (col, default, req) in specs.items()
    }
    all_templates = dict(protein_templates())
    provenance = dict.fromkeys(all_templates, "rdkit_standard_residue_template")
    provenance["ILE"] = "wwpdb_ILE_residue_template"
    for extra in (templates or {}, ccd_templates(cif)):
        all_templates.update(extra)
        provenance.update(dict.fromkeys(extra, "wwpdb_chemical_component_bond"))
    atoms, residue_map, groups = [], {}, defaultdict(list)
    warnings = []
    # Keep one alternative location per residue: largest summed occupancy,
    # lexicographic tie-break. Shared blank atoms are retained.
    alternatives = defaultdict(lambda: defaultdict(float))
    rows = []
    for i in range(count):
        if columns["model"][i] != columns["model"][0]:
            continue
        row = {k: v[i] for k, v in columns.items()}
        seq = row["sequence_id"]
        res_id = seq if seq not in _MISSING else row["auth_residue_id"]
        if res_id in _MISSING:
            res_id = "1"
        insertion = row["insertion_code"] if row["insertion_code"] not in _MISSING else ""
        row["residue_id"] = f"{res_id}{insertion}"
        key = (row["chain_id"], row["residue_id"], row["residue_name"])
        row["residue_key"] = key
        if row["altloc"] not in _MISSING:
            alternatives[key][row["altloc"]] += _optional_float(row["occupancy"]) or 0
        rows.append(row)
    selected = {key: min(values, key=lambda v: (-values[v], v)) for key, values in alternatives.items()}
    for row in rows:
        key = row.pop("residue_key")
        if row["altloc"] not in _MISSING and row["altloc"] != selected[key]:
            continue
        index = len(atoms)
        try:
            xyz = {axis: float(row[axis]) for axis in "xyz"}
        except (ValueError, TypeError) as exc:
            raise ValueError("mmCIF contains invalid Cartesian coordinates") from exc
        if not all(math.isfinite(value) for value in xyz.values()):
            raise ValueError("mmCIF contains non-finite Cartesian coordinates")
        bf = _optional_float(row["b_factor"])
        charge = _optional_float(row["formal_charge"])
        is_protein = row["residue_name"] in protein_templates() and row["sequence_id"] not in _MISSING
        atom = {
            **xyz,
            "id": index,
            "index": index,
            "element": row["element"].capitalize(),
            "name": row["name"],
            "chain_id": row["chain_id"],
            "residue_id": row["residue_id"],
            "residue_name": row["residue_name"],
            "sequence_id": row["sequence_id"] if row["sequence_id"] not in _MISSING else None,
            "is_protein": is_protein,
            "is_ligand": not is_protein and row["residue_name"] not in {"HOH", "DOD", "WAT"},
            "formal_charge": int(charge) if charge is not None else None,
            "aromatic": False,
            "b_factor": bf,
            "confidence": bf
            if source == "alphafold3_prediction" and bf is not None and 0 <= bf <= 100
            else None,
            "occupancy": _optional_float(row["occupancy"]),
            "altloc": row["altloc"] if row["altloc"] not in _MISSING else None,
        }
        atoms.append(atom)
        groups[key].append(atom)
        residue_map.setdefault(
            key,
            {
                "id": row["residue_id"],
                "chain_id": row["chain_id"],
                "name": row["residue_name"],
                "sequence_id": atom["sequence_id"],
                "atom_indices": [],
            },
        )["atom_indices"].append(index)
    if not atoms:
        raise ValueError("mmCIF contains no usable atoms")
    if selected:
        warnings.append(
            "One alternate location per residue selected by summed occupancy; original mmCIF retains all alternatives."
        )
    bonds, bonded = [], set()

    def connect(left, right, order, aromatic, origin, *, order_known=True):
        pair = tuple(sorted((left, right)))
        if left != right and pair not in bonded:
            bonded.add(pair)
            bonds.append(_bond(atoms, left, right, order, aromatic, origin, order_known=order_known))
            if aromatic:
                atoms[left]["aromatic"] = atoms[right]["aromatic"] = True

    comp_smiles = dict(zip(cif.get("_chem_comp.id", []), cif.get("_chem_comp.pdbx_smiles", [])))
    unknown = Counter()
    sequence_groups = defaultdict(list)
    for (chain, resid, comp), observed in groups.items():
        if observed[0]["is_protein"]:
            sequence_groups[(chain, observed[0]["sequence_id"])].append(observed)
    for (chain, resid, comp), observed in groups.items():
        names = {a["name"]: a["index"] for a in observed}
        if len(names) != len(observed):
            raise ValueError("Ambiguous duplicate atom names within one residue")
        template = all_templates.get(comp)
        origin = provenance.get(comp)
        # Exact SMILES echoed by AF3 has priority over re-canonicalized input.
        if template is None and source == "alphafold3_prediction" and comp.startswith("LIG_"):
            smiles = comp_smiles.get(comp)
            if not smiles or smiles in _MISSING:
                smiles = (ligand_smiles or {}).get(chain)
            if smiles:
                try:
                    template = _af3_smiles_template(smiles, observed)
                except ValueError:
                    template = None
                origin = "af3_v3_smiles_atom_names_validated"
                if template is None:
                    warnings.append(
                        f"Ligand {chain}:{resid} SMILES atom-name/element mapping failed; covalent bonds were not guessed."
                    )
        if template is None:
            if len(observed) > 1:
                unknown[comp] += 1
            continue
        for left, right, order, aromatic in template:
            if left in names and right in names:
                connect(names[left], names[right], order, aromatic, origin)
    for (chain, resid, comp), observed in groups.items():
        first = observed[0]
        if not first["is_protein"] or not str(first["sequence_id"]).isdigit():
            continue
        next_id = str(int(first["sequence_id"]) + 1)
        # Label sequence IDs, not coordinate proximity, establish polymer links.
        next_groups = sequence_groups[(chain, next_id)]
        if len(next_groups) == 1:
            left = next((a for a in observed if a["name"] == "C"), None)
            right = next((a for a in next_groups[0] if a["name"] == "N"), None)
            if left and right:
                connect(left["index"], right["index"], 1, False, "consecutive_polymer_sequence")
    # Explicit covalent/disulfide connections, excluding symmetry mates absent
    # from the displayed asymmetric unit and non-covalent coordination records.
    conn_count = len(cif.get("_struct_conn.conn_type_id", []))
    if conn_count:
        conn = {
            key: _column(cif, "_struct_conn." + key, conn_count, "?")
            for key in (
                "conn_type_id",
                "ptnr1_label_asym_id",
                "ptnr1_label_seq_id",
                "ptnr1_label_atom_id",
                "ptnr2_label_asym_id",
                "ptnr2_label_seq_id",
                "ptnr2_label_atom_id",
                "ptnr1_symmetry",
                "ptnr2_symmetry",
                "pdbx_value_order",
            )
        }
        for i, kind in enumerate(conn["conn_type_id"]):
            if kind not in {"covale", "disulf"}:
                continue
            if any(conn[f"ptnr{n}_symmetry"][i] not in _MISSING | {"1_555"} for n in (1, 2)):
                continue
            partners = []
            for n in (1, 2):
                candidates = [
                    a
                    for a in atoms
                    if a["chain_id"] == conn[f"ptnr{n}_label_asym_id"][i]
                    and (a["sequence_id"] or ".") == conn[f"ptnr{n}_label_seq_id"][i]
                    and a["name"] == conn[f"ptnr{n}_label_atom_id"][i]
                ]
                if len(candidates) == 1:
                    partners.append(candidates[0]["index"])
            if len(partners) == 2:
                order = _ORDER.get(conn["pdbx_value_order"][i].upper())
                # Disulfide chemistry defines a single bond. A covalent link
                # with an absent order is rendered as one stick, marked unknown.
                if kind == "disulf":
                    order = 1.0
                connect(
                    *partners,
                    order or 1,
                    False,
                    "mmcif_explicit_covalent_connection",
                    order_known=order is not None,
                )
    if unknown:
        warnings.append(
            "No named chemical bond template for: "
            + ", ".join(sorted(unknown))
            + "; those bonds are omitted, not inferred from distances."
        )
    geometry = _geometry(atoms, bonds)
    if geometry["long_bond_count"] or geometry["short_bond_count"]:
        warnings.append(
            f"Coordinate geometry warning: {geometry['long_bond_count']} long and {geometry['short_bond_count']} short covalent bonds. Coordinates were retained verbatim; do not interpret a distorted model as a binding pose."
        )
    if source == "alphafold3_prediction":
        warnings.append(
            "AlphaFold 3 pLDDT is structural confidence, not binding affinity, efficacy, or binding probability."
        )
    if source == "experimental_pdb":
        warnings.append(
            "Experimental reference structure; this is not a prediction for a newly generated candidate. B factors are not pLDDT."
        )
    chains = []
    for chain in dict.fromkeys(a["chain_id"] for a in atoms):
        chains.append(
            {
                "id": chain,
                "atom_count": sum(a["chain_id"] == chain for a in atoms),
                "residue_count": sum(key[0] == chain for key in groups),
            }
        )
    return {
        "schema_version": 1,
        "source": source,
        "label": label or Path(path).name,
        "atoms": atoms,
        "bonds": bonds,
        "chains": chains,
        "residues": list(residue_map.values()),
        "metadata": {
            "coordinate_units": "angstrom",
            "coordinate_transform": "none",
            "model": columns["model"][0],
            "input_atom_records": count,
            "selected_atom_count": len(atoms),
            "confidence_kind": "pLDDT" if source == "alphafold3_prediction" else None,
            "entry_id": cif.get("_entry.id", [None])[0],
            "unknown_bond_components": dict(unknown),
        },
        "geometry": geometry,
        "warnings": warnings,
        "energy": None,
    }
