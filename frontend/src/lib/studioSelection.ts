import type { Compound } from "../types/app";
import type { StructureMode } from "../types/molecular";

const STORAGE_KEY = "herbfold.studio.selection.v1";
export type StoredStudioSelection = { compound: Compound; target: string; mode: StructureMode; predictionJobId: string | null };

export function readStudioSelection(): StoredStudioSelection | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
    const compound = value?.compound;
    if (!compound || typeof compound.id !== "string" || compound.id.length > 200 || typeof compound.name !== "string" || compound.name.length > 500 ||
      typeof compound.smiles !== "string" || !compound.smiles || compound.smiles.length > 5000 ||
      !["herbal", "natural_product", "drug", "candidate"].includes(compound.category) ||
      typeof value.target !== "string" || !/^[A-Z0-9][A-Z0-9-]{1,19}$/.test(value.target) ||
      !["rdkit_conformer", "experimental_pdb", "alphafold3_prediction"].includes(value.mode)) return null;
    return { compound, target: value.target, mode: value.mode, predictionJobId: typeof value.predictionJobId === "string" && /^[a-zA-Z0-9_-]{1,100}$/.test(value.predictionJobId) ? value.predictionJobId : null };
  } catch { return null; }
}

export function saveStudioSelection(value: StoredStudioSelection): void {
  try {
    const { id, name, name_ko, category, smiles, source_url, generated } = value.compound;
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ ...value, compound: { id, name, name_ko, category, smiles, source_url, generated } }));
  } catch { /* Storage restrictions do not prevent structure exploration or server job recovery. */ }
}
