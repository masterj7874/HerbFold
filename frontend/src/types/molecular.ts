/** Coordinates and distances use ångström (Å); indices refer to atoms, not array order. */
export interface MolecularAtom {
  id: number;
  index: number;
  element: string;
  x: number;
  y: number;
  z: number;
  name?: string;
  chain_id?: string | null;
  residue_id?: string | null;
  residue_name?: string | null;
  sequence_id?: string | number | null;
  is_protein?: boolean;
  is_ligand?: boolean;
  formal_charge?: number | null;
  aromatic?: boolean;
  /** pLDDT, when actually present in an AlphaFold prediction. Never an experimental B factor. */
  confidence?: number | null;
  b_factor?: number | null;
}

export interface MolecularBond {
  source: number;
  target: number;
  order: number;
  aromatic?: boolean;
  provenance?: string;
  length_angstrom?: number;
}

export interface MolecularScene {
  schema_version: number;
  source: "rdkit_conformer" | "alphafold3_prediction" | "experimental_pdb";
  label: string;
  atoms: MolecularAtom[];
  bonds: MolecularBond[];
  chains?: Array<{ id: string; atom_count: number; residue_count: number }>;
  residues?: Array<{
    id: string;
    chain_id: string;
    name: string;
    atom_indices: number[];
    sequence_id?: string | number | null;
  }>;
  metadata?: Record<string, unknown>;
  geometry?: {
    bond_count?: number;
    long_bond_count?: number;
    short_bond_count?: number;
    coord_span_angstrom?: number | number[];
    max_bond_length_angstrom?: number;
    [key: string]: unknown;
  };
  warnings?: string[];
  energy?: {
    method: string;
    value_kcal_mol: number | null;
    converged: boolean;
  } | null;
}

export type MolecularRepresentation =
  | "ballstick"
  | "stick"
  | "spacefill"
  | "cartoon";
export type MolecularColorMode = "element" | "confidence" | "chain";

export type StructureMode = MolecularScene["source"];
export type StructureMatch = {
  id: string;
  label: string;
  job_id?: string;
  pdb_id?: string;
  source_url?: string;
};
export type StructureResolution = {
  status: "matched" | "unavailable";
  source: "experimental_pdb" | "alphafold3_prediction";
  requested: { smiles: string; canonical_smiles: string; target_accession: string };
  scene: MolecularScene | null;
  matches: StructureMatch[];
  reason?: string;
  reason_code?: string;
  available_references?: ExperimentalLigandReference[];
};
export type ExperimentalLigandReference = {
  id: string;
  name: string;
  label: string;
  smiles: string;
  category: "drug" | "natural_product";
  source_status?: string;
  source_url?: string;
  pdb_id: string;
  target_accession: string;
  ligand_component: string;
};
