import type { PredictionEnvelope, PredictionMode } from "./prediction";

export type AF3WorkflowCompound = {
  id: string;
  name: string;
  smiles: string;
  category?: "herbal" | "natural_product" | "drug" | "candidate";
};
export type AF3WorkflowRequest = {
  request_id?: string;
  compound: AF3WorkflowCompound;
  compound_name?: string;
  smiles: string;
  canonical_smiles: string;
  target_accession: string;
  msa_mode: PredictionMode;
  seeds: number[];
  exploratory_ack: boolean;
  execute: boolean;
};
export type AF3WorkflowStage = {
  id: "input" | "preflight" | "msa" | "inference" | "validation" | "results";
  label: string;
  agent: string;
  status: string;
  summary: string;
  started_at?: string | null;
  finished_at?: string | null;
};
export type AF3Workflow = {
  id: string;
  request_id: string;
  status: string;
  created: string;
  updated: string;
  request: AF3WorkflowRequest;
  job_id: string | null;
  stages: AF3WorkflowStage[];
  events: Array<{ seq: number; stage: string; status: string; message: string; time: string }>;
  prediction: PredictionEnvelope | null;
  blockers: string[];
  error: string | null;
  checkpoints: Record<string, unknown>;
  actions: { can_resume: boolean; resume_kind: "prepare" | "execute" | "retry" | null; can_execute: boolean; can_retry: boolean; reason?: string | null };
  recovery_candidates: unknown[];
};
