import type { PredictionEnvelope, PredictionMetrics, PredictionMode } from "../types/prediction";

export function hasVerifiedPrediction(entry: PredictionEnvelope | null | undefined): entry is PredictionEnvelope {
  return !!entry && entry.job.status === "completed" && entry.job.result?.execution_verified === true
    && entry.output_validation?.identity_verified === true;
}

export function comparisonEntry(entries: PredictionEnvelope[], active: PredictionEnvelope | null, mode: PredictionMode) {
  const matching = entries.filter((entry) => entry.requested.msa_mode === mode).sort((a, b) => b.job.created.localeCompare(a.job.created));
  if (active?.requested.msa_mode === mode) return active;
  return matching.find(hasVerifiedPrediction) || matching[0] || null;
}

export function measuredMetric(entry: PredictionEnvelope | null, key: keyof PredictionMetrics) {
  if (!hasVerifiedPrediction(entry)) return null;
  const value = entry.output_validation?.summary_metrics?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function metricDifference(none: PredictionEnvelope | null, search: PredictionEnvelope | null, key: keyof PredictionMetrics) {
  const before = measuredMetric(none, key);
  const after = measuredMetric(search, key);
  return before === null || after === null ? null : after - before;
}

export function conditionDifferences(none: PredictionEnvelope | null, search: PredictionEnvelope | null) {
  if (!none || !search) return { different: [], unknown: [] };
  const fields: { label: string; get: (entry: PredictionEnvelope) => unknown }[] = [
    { label: "성분 식별자", get: (entry) => entry.requested.canonical_smiles },
    { label: "표적 서열", get: (entry) => entry.requested.target_sequence_sha256 },
    { label: "AF3 코드 버전", get: (entry) => entry.requested.af3_commit },
    { label: "가중치 파일 stat 지문", get: (entry) => entry.requested.submission_context?.parameter_stat_fingerprint_sha256 },
    { label: "시드", get: (entry) => entry.requested.seeds?.length ? [...entry.requested.seeds].sort((a, b) => a - b) : null },
    { label: "시드당 표본 수", get: (entry) => entry.requested.execution_profile?.num_diffusion_samples },
    { label: "재순환 횟수", get: (entry) => entry.requested.execution_profile?.num_recycles },
  ];
  const different: string[] = [], unknown: string[] = [];
  for (const field of fields) {
    const a = field.get(none), b = field.get(search);
    if (a == null || b == null || a === "" || b === "") unknown.push(field.label);
    else if (JSON.stringify(a) !== JSON.stringify(b)) different.push(field.label);
  }
  return { different, unknown };
}
