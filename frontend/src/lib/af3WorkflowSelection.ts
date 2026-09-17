import type { AF3Workflow } from "../types/af3Workflow";

const STORAGE_KEY = "herbfold:af3-workflow:selected";
export function rememberedAF3Workflow(): string | null {
  try { const value = localStorage.getItem(STORAGE_KEY); return value && /^[a-zA-Z0-9_-]{1,200}$/.test(value) ? value : null; } catch { return null; }
}
export function rememberAF3Workflow(id: string): void {
  try { localStorage.setItem(STORAGE_KEY, id); } catch { /* Browsing storage is optional. */ }
}

/** A result handoff must preserve the saved ligand, target and job together. */
export function trustedAF3WorkflowResult(workflow: AF3Workflow | null | undefined): boolean {
  const prediction = workflow?.prediction;
  return !!workflow && workflow.status === "completed" && !!prediction
    && workflow.job_id === prediction.job.id
    && prediction.job.status === "completed"
    && prediction.job.result?.execution_verified === true
    && prediction.output_validation?.identity_verified === true
    && !!workflow.request.canonical_smiles
    && workflow.request.canonical_smiles === prediction.requested.canonical_smiles
    && workflow.request.target_accession === prediction.requested.target_accession
    && workflow.request.msa_mode === prediction.requested.msa_mode
    && Array.isArray(workflow.request.seeds) && workflow.request.seeds.length > 0
    && Array.isArray(prediction.requested.seeds)
    && JSON.stringify([...workflow.request.seeds].sort((a, b) => a - b)) === JSON.stringify([...prediction.requested.seeds].sort((a, b) => a - b));
}
