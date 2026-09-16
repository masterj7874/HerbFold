import type { Job } from "./app";

export type PredictionMode = "search" | "none";
export type PredictionStage = {
  name: string;
  label?: string;
  started_at?: string | null;
  finished_at?: string | null;
};
export type MSAFeatures = {
  status: "not_requested" | "awaiting_search" | "searching" | "ready" | "failed" | "combined_uncached";
  cache_hit?: boolean | null;
  cache_key?: string | null;
  protein_sequence_sha256?: string | null;
  unpaired_msa_sequences?: number | null;
  paired_msa_sequences?: number | null;
  non_query_sequences?: number | null;
  template_count?: number | null;
  database_fingerprint?: string | Record<string, unknown> | null;
  af3_version?: string | null;
  max_template_date?: string | null;
  source_job_id?: string | null;
  feature_sha256?: string | null;
  warnings?: string[];
};
export type PredictionMetrics = {
  ptm?: number | null;
  iptm?: number | null;
  ranking_score?: number | null;
  fraction_disordered?: number | null;
  has_clash?: boolean | null;
};
export type PredictionEnvelope = {
  job: Job;
  requested: {
    smiles: string; canonical_smiles: string; target_accession: string; msa_mode: PredictionMode; seeds: number[];
    target_sequence_sha256?: string;
    af3_version?: string;
    af3_commit?: string;
    execution_profile?: {
      num_diffusion_samples?: number;
      num_recycles?: number;
      max_template_date?: string;
      [key: string]: unknown;
    };
    submission_context?: { parameter_stat_fingerprint_sha256?: string; [key: string]: unknown };
  };
  reused: boolean;
  retry_of: string | null;
  readiness: {
    runnable: boolean;
    blockers: string[];
    warnings: string[];
    parameter_status?: "test_parameters" | "missing_parameters" | "invalid_parameters" | "ambiguous_parameters" | "inspection_unavailable" | "inspection_limit_reached" | "unverified_parameters" | null;
    parameters?: { trained_parameters_authenticated?: boolean; [key: string]: unknown } | null;
    databases?: {
      fingerprint_sha256?: string;
      manifest_sha256?: string;
      source_tag?: string;
      installed_at?: string | null;
      components?: { relative_path: string; record_count: number; sha256: string; size_bytes: number }[];
    } | null;
  };
  queue_position: number | null;
  input_url: string;
  log_url: string;
  scene_url: string;
  stage?: PredictionStage | null;
  msa_features?: MSAFeatures | null;
  output_validation?: {
    status?: string;
    warnings?: string[];
    geometry?: Record<string, unknown>;
    identity_verified?: boolean;
    quality_pass?: null | false;
    summary_metrics?: PredictionMetrics;
    sha256?: string;
  } | null;
};
export type PredictionLog = { text: string; truncated: boolean; status: string };
export type PredictionSelection = { smiles: string; targetAccession: string };
