export type QuantumMethod = "projected" | "fidelity";
export type QuantumInterval = [number, number];
export type QuantumObservation = {
  pair?: [number, number]; zero_counts?: number; shots?: number;
  fidelity?: number; wilson_95?: QuantumInterval;
  [key: string]: unknown;
};
export type QuantumPlan = {
  status?: string; mode?: string; kernel_method?: QuantumMethod;
  backend_name?: string; n_qubits?: number; shots?: number; layers?: number;
  physical_qubits?: number[];
  block_size?: number; gamma?: number; circuit_count?: number; total_shots?: number;
  max_jobs?: number; job_count?: number; max_execution_time_per_job?: number;
  max_total_qpu_seconds?: number; feature_sha256?: string;
  warnings?: string[]; [key: string]: unknown;
};
export type QuantumResult = {
  schema_version?: number; kernel_method?: QuantumMethod; estimator?: string;
  mode?: string; status?: string; hardware_executed?: boolean | null;
  kernel?: number[][]; metadata?: QuantumPlan; plan?: QuantumPlan;
  sample_ids?: string[]; sample_labels?: string[]; source_analysis_id?: string;
  feature_definition?: { columns: string[]; divisors: number[]; features: number[][]; scope?: string };
  jobs?: { job_id: string; status?: string; observations?: QuantumObservation[]; quantum_seconds?: number | null; [key: string]: unknown }[];
  projected_features?: { axes: string[]; values: number[][][]; wilson_95?: QuantumInterval[][][] | null };
  controls?: { duplicate?: { sample_index?: number; kernel_to_original?: number; squared_distance?: number }; readout?: { zero_error_rates?: number[]; one_error_rates?: number[]; zero_error_wilson_95?: QuantumInterval[]; one_error_wilson_95?: QuantumInterval[]; mean_error?: number } };
  kernel_diagnostics?: { diagonal_definition?: string; off_diagonal_min?: number; off_diagonal_mean?: number; off_diagonal_max?: number; shot_noise_distance_floor?: number; signal_to_shot_noise?: number; collapsed?: boolean; mean_diagonal?: number; min_eigenvalue?: number; psd_projection_applied?: boolean; [key: string]: unknown };
  ideal_reference?: { status?: string; kernel?: number[][]; features?: number[][][]; hardware_executed?: boolean; [key: string]: unknown };
  classical_reference?: { kernel?: number[][]; estimator?: string; [key: string]: unknown };
  kernel_uncertainty?: { status?: string; lower_95?: number[][]; upper_95?: number[][]; method?: string; caveat?: string };
  compiled?: Record<string, unknown>; manifest_url?: string; summary?: string; warnings?: string[];
};
export type QuantumRequest = {
  features: number[][]; mode: "ibm"; qubits: "max"; kernel_method: "projected";
  source_analysis_id: string; sample_ids: string[]; sample_labels: string[];
  block_size: number; gamma: number; layers: number; shots: number;
  max_circuits: number; circuits_per_job: number; max_total_shots: number;
  max_jobs: number; max_execution_time: number;
};
