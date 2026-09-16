"""Deterministic, structure-only cheminformatics; no inferred efficacy or affinity."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from itertools import combinations, islice
from pathlib import Path

from rdkit import Chem, DataStructs, rdBase
from rdkit.Chem import BRICS, QED, Crippen, Descriptors, Lipinski, rdFingerprintGenerator
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams
from rdkit.Chem.MolStandardize import rdMolStandardize
from rdkit.Chem.Scaffolds import MurckoScaffold

_FINGERPRINT = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048, includeChirality=True)


def standardize_molecule(smiles: str) -> Chem.Mol:
    """Cleanup and choose the fragment parent, retaining charge and stereochemistry."""
    if not isinstance(smiles, str) or not smiles.strip() or len(smiles) > 4096:
        raise ValueError("SMILES must be a nonempty string of at most 4096 characters")
    mol = Chem.MolFromSmiles(smiles.strip())
    if mol is None or mol.GetNumAtoms() == 0:
        raise ValueError("Invalid SMILES")
    if mol.GetNumHeavyAtoms() > 256:
        raise ValueError("Small-molecule limit is 256 heavy atoms")
    if any(atom.GetAtomicNum() == 0 for atom in mol.GetAtoms()):
        raise ValueError("Molecules must not contain wildcard/attachment atoms")
    mol = rdMolStandardize.FragmentParent(rdMolStandardize.Cleanup(mol))
    Chem.SanitizeMol(mol)
    if not any(atom.GetAtomicNum() == 6 for atom in mol.GetAtoms()):
        raise ValueError("An organic molecule containing carbon is required")
    return mol


def canonical_smiles(smiles: str) -> str:
    return Chem.MolToSmiles(standardize_molecule(smiles), isomericSmiles=True)


def scaffold_key(smiles: str) -> str:
    mol = standardize_molecule(smiles)
    scaffold = MurckoScaffold.GetScaffoldForMol(mol)
    # All acyclic molecules belong to one conservative group; avoid random leakage.
    return Chem.MolToSmiles(scaffold, isomericSmiles=False) or "ACYCLIC"


@lru_cache(maxsize=1)
def _filter_catalogs() -> dict[str, FilterCatalog]:
    catalogs = {}
    for name in ("PAINS", "BRENK"):
        params = FilterCatalogParams()
        params.AddCatalog(getattr(FilterCatalogParams.FilterCatalogs, name))
        catalogs[name] = FilterCatalog(params)
    return catalogs


def describe_molecule(smiles: str) -> dict:
    mol = standardize_molecule(smiles)
    canonical = Chem.MolToSmiles(mol, isomericSmiles=True)
    alerts = [
        {"catalog": name, "description": match.GetDescription()}
        for name, catalog in _filter_catalogs().items()
        for match in catalog.GetMatches(mol)
    ]
    stereocenters = Chem.FindMolChiralCenters(mol, includeUnassigned=True)
    unspecified = sum(label == "?" for _, label in stereocenters)
    notes = ["Descriptors and filter alerts are computational; they do not establish safety or efficacy."]
    if "." in smiles:
        notes.append(
            "Fragment parent selected: counterions or other fragments were removed; original input retained."
        )
    if unspecified:
        notes.append(
            "Unspecified tetrahedral stereochemistry: enumerate/resolve stereoisomers before structure prediction."
        )
    if any(info.specified == Chem.StereoSpecified.Unspecified for info in Chem.FindPotentialStereo(mol)):
        notes.append(
            "At least one potential stereochemical element is unspecified (including possible bond stereo)."
        )
    mw, logp = Descriptors.MolWt(mol), Crippen.MolLogP(mol)
    hbd, hba = Lipinski.NumHDonors(mol), Lipinski.NumHAcceptors(mol)
    return {
        "input_smiles": smiles,
        "smiles": canonical,
        "canonical_smiles": canonical,
        "formula": Chem.rdMolDescriptors.CalcMolFormula(mol),
        "molecular_weight": round(mw, 4),
        "logp": round(logp, 4),
        "tpsa": round(Descriptors.TPSA(mol), 4),
        "hbd": hbd,
        "hba": hba,
        "rotatable_bonds": Lipinski.NumRotatableBonds(mol),
        "heavy_atoms": mol.GetNumHeavyAtoms(),
        "formal_charge": Chem.GetFormalCharge(mol),
        "qed": round(QED.qed(mol), 6),
        "lipinski_violations": sum((mw > 500, logp > 5, hbd > 5, hba > 10)),
        "scaffold": scaffold_key(canonical),
        "stereocenters": [{"atom_index": i, "assignment": label} for i, label in stereocenters],
        "unassigned_stereocenters": unspecified,
        "alerts": alerts,
        "standardization": "RDKit Cleanup + FragmentParent; charge/stereo retained; no tautomer or pH enumeration",
        "rdkit_version": rdBase.rdkitVersion,
        "notes": notes,
    }


def load_catalog() -> list[dict]:
    path = Path(__file__).resolve().parents[2] / "data" / "compounds.json"
    if not path.is_file():
        path = Path(__file__).resolve().parent / "data" / "compounds.json"
    return json.loads(path.read_text(encoding="utf-8"))


def compare_compounds(compounds: list[dict] | None = None) -> list[dict]:
    """Compare every herbal/reference pair with chirality-aware Morgan Tanimoto."""
    compounds = load_catalog() if compounds is None else compounds
    if len(compounds) > 200:
        raise ValueError("At most 200 compounds may be compared per request")
    herbs = [row for row in compounds if row.get("category") in {"herbal", "natural_product"}]
    drugs = [row for row in compounds if row.get("category") == "drug"]
    if not herbs or not drugs:
        raise ValueError("Provide compounds with both category='herbal' and category='drug'")
    prepared = {}
    for row in compounds:
        descriptor = describe_molecule(row["smiles"])
        prepared[id(row)] = (descriptor, _FINGERPRINT.GetFingerprint(standardize_molecule(row["smiles"])))
    result = []
    for herb in herbs:
        for drug in drugs:
            hd, hf = prepared[id(herb)]
            dd, df = prepared[id(drug)]
            result.append(
                {
                    "herbal_id": herb.get("id", hd["smiles"]),
                    "source_parent_category": herb["category"],
                    "source_parent_id": herb.get("id", hd["smiles"]),
                    "drug_id": drug.get("id", dd["smiles"]),
                    "herbal_name": herb.get("name", hd["smiles"]),
                    "drug_name": drug.get("name", dd["smiles"]),
                    "tanimoto": round(DataStructs.TanimotoSimilarity(hf, df), 6),
                    "fingerprint": "Morgan radius=2, bits=2048, chirality=True",
                    "descriptor_delta_herbal_minus_drug": {
                        key: round(hd[key] - dd[key], 6)
                        for key in ("molecular_weight", "logp", "tpsa", "hbd", "hba", "qed")
                    },
                    "sources": [row.get("source_url") for row in (herb, drug) if row.get("source_url")],
                    "interpretation": "Structural similarity only; not common indication, binding affinity, efficacy, or interchangeability.",
                }
            )
    return sorted(result, key=lambda row: (-row["tanimoto"], row["herbal_id"], row["drug_id"]))


def compare_structures(compounds: list[dict]) -> list[dict]:
    """Compare all selected ID pairs, independent of their source categories.

    Distinct catalog entries with the same standardized structure remain visible.
    Canonical equality is reported separately from fingerprint similarity because
    a Tanimoto score of one alone does not establish structural identity.
    """
    if not isinstance(compounds, list) or not 2 <= len(compounds) <= 8:
        raise ValueError("Provide between 2 and 8 compounds")
    prepared = {}
    for row in compounds:
        if not isinstance(row, dict):
            raise ValueError("Each compound must contain an id and SMILES")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 200:
            raise ValueError("Compound ids must contain 1 to 200 characters")
        if identifier != identifier.strip():
            raise ValueError("Compound ids must not contain leading or trailing whitespace")
        if identifier in prepared:
            raise ValueError("Compound ids must be distinct")
        name = row.get("name", identifier)
        if not isinstance(name, str) or not name.strip() or len(name) > 1000:
            raise ValueError("Compound names must contain 1 to 1000 characters")
        mol = standardize_molecule(row.get("smiles"))
        prepared[identifier] = {
            "id": identifier,
            "name": name,
            "canonical_smiles": Chem.MolToSmiles(mol, isomericSmiles=True),
            "fingerprint": _FINGERPRINT.GetFingerprint(mol),
        }
    results = []
    for left_id, right_id in combinations(sorted(prepared), 2):
        left, right = prepared[left_id], prepared[right_id]
        results.append(
            {
                "left_id": left_id,
                "left_name": left["name"],
                "right_id": right_id,
                "right_name": right["name"],
                "left_canonical_smiles": left["canonical_smiles"],
                "right_canonical_smiles": right["canonical_smiles"],
                "tanimoto": round(
                    DataStructs.TanimotoSimilarity(left["fingerprint"], right["fingerprint"]), 6
                ),
                "identical_structure": left["canonical_smiles"] == right["canonical_smiles"],
                "fingerprint": "Morgan radius=2, bits=2048, chirality=True",
                "standardization": (
                    "RDKit Cleanup + FragmentParent; charge/stereo retained; no tautomer or pH enumeration"
                ),
                "interpretation": (
                    "Structural similarity only; not common indication, binding affinity, efficacy, "
                    "or interchangeability. Identity refers to standardized structures, not original salts."
                ),
            }
        )
    return sorted(results, key=lambda row: (-row["tanimoto"], row["left_id"], row["right_id"]))


def generate_candidates(
    parent_smiles: list[str],
    max_candidates: int = 20,
    *,
    reject_alerts: bool = False,
) -> list[dict]:
    """Bounded pairwise BRICS enumeration with verified fragment-level ancestry.

    Only products with a distinct fragment from BOTH parents survive. A product is
    novel only relative to the standardized supplied parent set, not databases or patents.
    No prediction of synthetic feasibility, binding, or biological benefit is made.
    """
    if (
        not isinstance(max_candidates, int)
        or isinstance(max_candidates, bool)
        or not 1 <= max_candidates <= 100
    ):
        raise ValueError("max_candidates must be an integer between 1 and 100")
    if not isinstance(parent_smiles, list) or not 2 <= len(parent_smiles) <= 16:
        raise ValueError("Provide between 2 and 16 parent SMILES")
    parents = {canonical_smiles(smiles): standardize_molecule(smiles) for smiles in parent_smiles}
    if len(parents) < 2:
        raise ValueError("At least two distinct standardized parent molecules are required")
    fragments = {smiles: set(BRICS.BRICSDecompose(mol, minFragmentSize=2)) for smiles, mol in parents.items()}
    fingerprints = {smiles: _FINGERPRINT.GetFingerprint(mol) for smiles, mol in parents.items()}
    products: dict[str, dict] = {}
    remaining_budget = min(10000, max_candidates * 100)
    for left, right in combinations(sorted(parents), 2):
        if remaining_budget <= 0 or len(products) >= max_candidates:
            break
        left_only, right_only = fragments[left] - fragments[right], fragments[right] - fragments[left]
        if not left_only or not right_only:
            continue
        pool = sorted(fragments[left] | fragments[right])
        if len(pool) > 32:
            continue
        building_blocks = [Chem.MolFromSmiles(fragment) for fragment in pool]
        built = BRICS.BRICSBuild(building_blocks, maxDepth=2, scrambleReagents=False, uniquify=True)
        # Bound enumeration per pair as well as across the request.
        for product in islice(built, min(500, remaining_budget)):
            remaining_budget -= 1
            try:
                Chem.SanitizeMol(product)
                smiles = canonical_smiles(Chem.MolToSmiles(product, isomericSmiles=True))
                if smiles in parents or smiles in products:
                    continue
                parts = set(BRICS.BRICSDecompose(standardize_molecule(smiles), minFragmentSize=2))
                left_parts, right_parts = sorted(parts & left_only), sorted(parts & right_only)
                if not left_parts or not right_parts:
                    continue
                desc = describe_molecule(smiles)
                if not (
                    120 <= desc["molecular_weight"] <= 650
                    and -2 <= desc["logp"] <= 6
                    and desc["tpsa"] <= 180
                    and desc["qed"] >= 0.25
                ):
                    continue
                if reject_alerts and desc["alerts"]:
                    continue
                fp = _FINGERPRINT.GetFingerprint(standardize_molecule(smiles))
                products[smiles] = {
                    "id": "brics-" + hashlib.sha256(smiles.encode()).hexdigest()[:12],
                    "smiles": smiles,
                    "parents": [left, right],
                    "parent_fragments": [
                        {"parent_smiles": left, "contributing_fragments": left_parts},
                        {"parent_smiles": right, "contributing_fragments": right_parts},
                    ],
                    "descriptors": desc,
                    "max_parent_similarity": round(
                        max(DataStructs.TanimotoSimilarity(fp, item) for item in fingerprints.values()), 6
                    ),
                    "novel_relative_to_inputs": True,
                    "novelty_scope": "Exact standardized input structures only; no external database or patent search performed",
                    "method": "RDKit pairwise BRICS recombination; maxDepth=2; deterministic bounded enumeration",
                    "status": "unvalidated_computational_candidate",
                }
                if len(products) >= max_candidates:
                    break
            except (ValueError, RuntimeError):
                continue
    return sorted(products.values(), key=lambda row: (-row["descriptors"]["qed"], row["smiles"]))
