import type { MolecularScene, StructureMode } from "../types/molecular";

/** Measurement guards shared by the board and isolated regression tests. */
export function diagnosticRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export function diagnosticNumber(value: unknown, min = -Infinity, max = Infinity): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max ? value : null;
}

export function diagnosticText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function diagnosticLink(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

export type AtomConfidenceSummary = {
  count: number;
  total: number;
  mean: number | null;
  min: number | null;
  max: number | null;
};

export function summarizeConfidence(values: unknown[], total = values.length): AtomConfidenceSummary {
  const valid = values.map(value => diagnosticNumber(value, 0, 100)).filter((value): value is number => value !== null);
  return {
    count: valid.length, total,
    mean: valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null,
    min: valid.length ? valid.reduce((minimum, value) => Math.min(minimum, value), Infinity) : null,
    max: valid.length ? valid.reduce((maximum, value) => Math.max(maximum, value), -Infinity) : null,
  };
}

export function sceneConfidence(scene: MolecularScene | null) {
  const unavailable = () => ({ all: summarizeConfidence([]), protein: summarizeConfidence([]), ligand: summarizeConfidence([]) });
  if (!scene || scene.source !== "alphafold3_prediction" || scene.metadata?.confidence_kind !== "pLDDT"
    || scene.metadata?.prediction_eligible === false) return unavailable();
  const rawIds = scene.metadata?.selected_ligand_atom_ids;
  const selectedIds = new Set(Array.isArray(rawIds) ? rawIds.filter((value): value is number => typeof value === "number" && Number.isInteger(value)) : []);
  const rawTargetChains = diagnosticRecord(scene.metadata?.selection).target_chains;
  const targetChains = new Set(Array.isArray(rawTargetChains) ? rawTargetChains.filter((value): value is string => typeof value === "string") : []);
  const protein = scene.atoms.filter(atom => atom.is_protein && typeof atom.chain_id === "string" && targetChains.has(atom.chain_id));
  // Cofactors and other ligands never enter the selected molecule's statistic.
  const ligand = scene.atoms.filter(atom => selectedIds.has(atom.id));
  return {
    all: summarizeConfidence(scene.atoms.map(atom => atom.confidence)),
    protein: summarizeConfidence(protein.map(atom => atom.confidence)),
    ligand: summarizeConfidence(ligand.map(atom => atom.confidence)),
  };
}

export function currentDiagnosticScene(scene: MolecularScene | null, mode: StructureMode, target: string, loading = false) {
  if (loading || !scene || scene.source !== mode) return null;
  const selection = diagnosticRecord(scene.metadata?.selection);
  if (selection.target_accession && selection.target_accession !== target) return null;
  return scene;
}

export function diagnosticMatchesScene(report: unknown, scene: MolecularScene | null, target: string) {
  if (!scene || scene.source !== "alphafold3_prediction") return false;
  const data = diagnosticRecord(report), requested = diagnosticRecord(data.requested);
  const metadata = scene.metadata || {}, selection = diagnosticRecord(metadata.selection);
  return typeof metadata.job_id === "string" && !!metadata.sha256
    && data.job_id === metadata.job_id && data.structure_sha256 === metadata.sha256
    && (typeof metadata.artifact !== "string" || data.artifact === metadata.artifact)
    && requested.target_accession === target && selection.target_accession === target
    && typeof selection.canonical_smiles === "string"
    && requested.canonical_smiles === selection.canonical_smiles;
}

export function af3ClashLabel(value: unknown): string {
  return value === true ? "대규모 원자 충돌 경고" : value === false ? "대규모 충돌 플래그 없음" : "— · 검사값 미기록";
}
