import { tr, localeCode, msg } from "../lib/i18n";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import {
  Activity, ArrowDownToLine, ArrowRight, BookOpen, ChevronLeft, ChevronRight,
  CircleHelp, Clock3, Database, ExternalLink, FileCheck2, FlaskConical,
  Gauge, HardDrive, LoaderCircle, Microscope, RefreshCw, Search, ShieldAlert,
} from "lucide-react";
import { api, download } from "../lib/api";
import type { Compound } from "../types/app";
import "./validation-panel.css";

type Values = Record<string, unknown>;
type Source = { id?: string; name?: string; label?: string; url?: string; source_url?: string; license?: string };
type Scale = {
  status?: string; phase?: string; updated_at?: string; requested_attempts?: number;
  attempted?: number; sanitized?: number; passed_filters?: number; retained_unique?: number;
  duplicates?: number; rejected?: number; known_parent_matches?: number;
  compatible_pair_slots_upper_bound?: number; prepared_parents?: number; invalid_parents?: number;
  natural_fragments?: number; drug_fragments?: number; elapsed_seconds?: number;
  generation_seconds?: number; attempts_per_second?: number; retained_per_second?: number;
  process_peak_rss_mb?: number; children_peak_rss_mb?: number; database_bytes?: number;
  aggregate_rss_mb?: number; sampled_peak_aggregate_rss_mb?: number; distinct_catalog_parent_ids?: number;
  retained_milestones?: Array<{ retained_threshold: number; committed_retained_unique: number; attempted: number; generation_seconds: number; measured: boolean }>;
  workers?: number; interpretation?: string;
  projection?: { is_measured: boolean; target_attempts?: number; estimated_seconds?: number; basis_attempts?: number; finite_space_allows_target?: boolean };
  [key: string]: unknown;
};
type BiologicalTarget = { id: string; label?: string; chembl_id?: string; uniprot?: string; source_url?: string };
type Model = {
  id: string; target?: string; endpoint?: string; assay_id?: string; assay_type?: string;
  assay_description?: string; assay_source_url?: string;
  quality_status?: string; status?: string; data_counts?: Values; split_counts?: Values;
  metrics?: Record<string, Values>; selected_model?: string;
  applicability?: { threshold?: number; test_coverage?: number; [key: string]: unknown };
  uncertainty?: { method?: string; nominal_coverage?: number; empirical_test_coverage?: number; interval_half_width?: number; [key: string]: unknown };
  reason?: string | string[]; [key: string]: unknown;
};
type Biology = {
  status?: string; created_at?: string; updated_at?: string; scope?: string;
  counts?: { candidates?: number; alerted_candidates?: number; catalog_exact_matches?: number; exact_measured_candidates?: number; predicted_candidates?: number; abstained_candidates?: number };
  targets?: BiologicalTarget[]; models?: Model[]; limitations?: string[]; [key: string]: unknown;
};
type Evidence = {
  target?: string; endpoint?: string; assay_id?: string; status: string;
  value_pactivity?: number; interval?: number[] | { lower?: number; upper?: number };
  nearest_similarity?: number; reason?: string; source_url?: string;
  [key: string]: unknown;
};
type SafetyReport = {
  status?: string;
  dataset?: { source_url?: string; raw_rows?: number; curated_structures?: number; sha256?: string };
  endpoints?: Array<{ endpoint: string; labeled_count?: number; train_count?: number; test_count?: number; test_positive?: number;
    metrics?: { roc_auc?: number; average_precision?: number; brier?: number; prevalence?: number; roc_auc_ci95?: number[] };
    model_gate_passed?: boolean; applicability?: { threshold?: number; test_coverage?: number } }>;
  candidates?: { total?: number; exact_dataset_matches?: number; in_domain?: number; out_of_domain?: number; report_path?: string;
    cohorts?: Record<string, { total?: number; in_domain?: number; out_of_domain?: number; observed_assay_matches?: number; with_any_prediction?: number }>;
  };
  limitations?: string[];
  [key: string]: unknown;
};
type CandidateSafety = {
  nearest_training_similarity?: number; in_domain?: boolean;
  observed_assays?: Array<{ endpoint: string; label: number; source_ids?: Array<string | number> }>;
  predictions?: Array<{ endpoint: string; status: string; score?: number | null; reason?: string }>;
  interpretation?: string;
};
type ValidatedCandidate = {
  id: string; name?: string; smiles: string; canonical_smiles?: string;
  cohorts?: string[];
  structural_alerts?: { PAINS?: unknown[]; BRENK?: unknown[] };
  catalog_identity?: { known?: boolean; matched_ids?: Array<string | number> };
  evidence?: Evidence[]; safety?: CandidateSafety; [key: string]: unknown;
};
type Summary = { scale?: Scale; biology?: Biology; safety?: SafetyReport; sources?: Source[]; limitations?: string[]; updated_at?: string;
  cohort?: { scale_population?: number; scale_sample_count?: number; prior_unique_count?: number; combined_unique_count?: number; overlap?: number; method?: string; [key: string]: unknown };
};
type CandidatePage = { items: ValidatedCandidate[]; total: number; limit: number; offset: number; status?: string };
type Props = { onInspect: (compound: Compound) => void };
type Section = "scale" | "biology" | "candidates";
const PAGE_SIZE = 30;
const SECTIONS: Array<{ id: Section; label: string; icon: typeof Gauge }> = [
  { id: "scale", label: "규모·성능 실측", icon: Gauge },
  { id: "biology", label: "약효·독성 근거", icon: Microscope },
  { id: "candidates", label: "후보별 검증", icon: FlaskConical },
];
const STATUS: Record<string, string> = {
  pending: "결과 대기", not_started: "미실행", preparing: "입력 준비 중", ready: "실행 준비됨",
  not_run: "미실행", report_unavailable: "보고서 확인 필요",
  running: "실행 중", paused: "일시정지", completed: "실행 종료", exhausted: "탐색 공간 소진",
  budget_exhausted: "실행 한도 도달", failed: "실행 실패", unavailable: "보고서 없음",
  qualified: "모델 검증 기준 충족", insufficient_data: "학습 자료 부족", failed_validation: "모델 검증 미충족",
  exact_measured: "동일 구조 실측 기록", predicted: "모델 예측", abstained: "판정 보류",
};
const VALUE_LABELS: Record<string, string> = {
  train: "학습", tune: "모델 선택", calibration: "보정", test: "평가", train_count: "학습 구조", calibration_count: "보정 구조", test_count: "평가 구조",
  unique_structures: "고유 실측 구조", scaffolds: "서로 다른 분자 골격",
  raw: "원본 레코드", filtered: "선별 레코드", unique: "고유 구조", n: "표본 수", count: "표본 수", mae: "MAE", rmse: "RMSE", r2: "R²",
  spearman: "Spearman ρ", pearson: "Pearson r", roc_auc: "ROC-AUC", pr_auc: "PR-AUC", average_precision: "Average precision",
  brier: "Brier score", brier_score: "Brier score", ece: "ECE", n_train: "학습 구조", n_test: "평가 구조", n_calibration: "보정 구조",
};
const EXPLANATIONS: Record<string, string> = {
  "Insufficient unique measured structures or distinct scaffolds within this assay": "이 시험의 고유 실측 구조 또는 서로 다른 분자 골격 수가 부족합니다.",
  "Unable to construct four scaffold-disjoint partitions": "분자 골격이 겹치지 않는 4개 자료 집합을 구성할 수 없습니다.",
  "Scaffold-disjoint partitions are too small; no random-split substitute used": "골격별로 분리한 자료 집합이 너무 작아 모델 평가를 보류했습니다.",
  "Insufficient in-domain calibration or independent test coverage": "적용 범위 내 보정 또는 독립 평가 자료가 부족합니다.",
  "Calibration interval exceeds allowed width": "보정된 예측 구간 폭이 사전 기준을 초과합니다.",
  "Independent test interval coverage below threshold": "독립 평가에서 관측한 구간 포함률이 기준에 미달합니다.",
  "Independent test MAE does not pass the predeclared threshold": "독립 평가의 평균 절대 오차가 사전 기준을 충족하지 못합니다.",
  "Does not improve the training-median baseline on independent in-domain test data": "적용 범위 내 독립 평가에서 학습 중앙값 기준 모델보다 충분히 개선되지 않았습니다.",
  "Independent in-domain R2 is not positive": "적용 범위 내 독립 평가의 R²가 양수가 아닙니다.",
  "No comparable high-confidence exact measurements retained for this endpoint": "이 평가 항목에서 비교 가능한 고신뢰 실측 자료가 확보되지 않았습니다.",
  "No assay-specific model passed independent evaluation and calibration gates": "독립 평가·보정 기준을 통과한 해당 시험 모델이 없어 판정을 보류합니다.",
  "Outside the training-structure applicability threshold": "학습 구조에 대한 적용 범위 기준을 벗어나 판정을 보류합니다.",
  "Computational assay-specific estimate, not a measured candidate or clinical conclusion": "해당 시험의 계산 예측입니다. 후보의 신규 실측이나 임상 결과가 아닙니다.",
  "Database-reported measurement for the same standardized parent structure; not a new experiment": "동일한 표준화 부모 구조에 대해 데이터베이스가 보고한 실측입니다. 이번에 새로 수행한 실험은 아닙니다.",
  "Split calibration absolute-residual interval; scaffold shift makes coverage empirical, not guaranteed": "별도 보정 자료의 절대 잔차로 구간을 산정했습니다. 분자 골격 분포가 달라질 수 있어 실제 포함률을 함께 확인해야 합니다.",
  "Tox21 labels describe activity in 12 nuclear receptor/stress-response assays, not human safety.": "Tox21은 12개 핵 수용체·스트레스 반응 시험의 활성을 기록하며 사람에서의 안전성을 판정하지 않습니다.",
  "Inactive, missing or abstained results do not establish absence of toxicity.": "비활성·누락·판정 보류 결과는 독성이 없다는 근거가 아닙니다.",
  "No animal, clinical, Ames, organ toxicity, metabolism or exposure experiments were performed.": "동물·임상·Ames·장기 독성·대사·노출 실험은 수행하지 않았습니다.",
  "Model scores are uncalibrated estimates of assay activity; they are not probabilities of human toxicity.": "모델 활성 점수는 보정되지 않은 시험 예측값이며 사람의 독성 확률이 아닙니다.",
  "A scaffold holdout is retrospective and cannot establish prospective or clinical performance.": "분자 골격을 분리한 평가는 과거 자료에 대한 평가입니다. 향후 실험이나 임상 성능을 확정하지 않습니다.",
  "Applicability uses training-set Morgan similarity, not a confidence interval or validated safety cutoff.": "적용 범위는 학습 자료와의 Morgan 유사도로 정하며 신뢰구간이나 검증된 안전 기준이 아닙니다.",
  "Standardized-structure matches remove counterions; assay identity may differ in salt/formulation/exposure.": "표준화 과정에서 상대 이온이 제거됩니다. 동일 구조 기록도 염·제형·노출 조건이 다를 수 있습니다.",
  observed_assay_available: "동일 구조의 실측 기록을 우선합니다.",
  model_quality_gate_failed: "모델이 사전 평가 기준을 충족하지 못했습니다.",
  outside_training_domain: "해당 시험의 학습 자료 적용 범위를 벗어납니다.",
  insufficient_class_support: "활성·비활성 학습 또는 평가 자료가 부족합니다.",
};
function explanation(value: string): string { return EXPLANATIONS[value] || value; }
function combinedStatus(...states: Array<string | undefined>): string | undefined {
  const active = states.find((state) => state && ["running", "preparing", "ready", "queued"].includes(state));
  if (active) return active;
  if (states.every((state) => state === "completed")) return "completed";
  return states.find((state) => state !== "completed") || "pending";
}

function number(value: unknown, digits = 0): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString(localeCode(), { maximumFractionDigits: digits }) : "—";
}
function precise(value: unknown, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}
function percent(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${number(value * 100, 1)}%` : "—";
}
function duration(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (value >= 86400) return `${number(value / 86400, 2)}일`;
  if (value >= 3600) return `${number(value / 3600, 2)}시간`;
  if (value >= 60) return `${number(value / 60, 2)}분`;
  return `${number(value, 2)}초`;
}
function bytes(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (value >= 1024 ** 3) return `${number(value / 1024 ** 3, 2)} GiB`;
  if (value >= 1024 ** 2) return `${number(value / 1024 ** 2, 2)} MiB`;
  return `${number(value / 1024, 2)} KiB`;
}
function date(value?: string): string {
  if (!value) return "기록 대기";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(localeCode());
}
function url(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try { const parsed = new URL(value); return ["https:", "http:"].includes(parsed.protocol) ? parsed.href : undefined; }
  catch { return undefined; }
}
function candidateName(candidate: ValidatedCandidate): string {
  return candidate.name || `HF-${candidate.id.replace(/^campaign[-_]/, "").slice(0, 12).toUpperCase()}`;
}
function asCompound(candidate: ValidatedCandidate): Compound {
  return { ...candidate, id: `validation_${candidate.id}`, name: candidateName(candidate),
    smiles: candidate.canonical_smiles || candidate.smiles, category: "candidate", generated: true,
    source_url: candidate.evidence?.map((item) => url(item.source_url)).find(Boolean),
    source_scope: "Computational candidate; assay records and model predictions do not establish clinical efficacy or safety" };
}

export default function ValidationPanel({ onInspect }: Props) {
  const reducedMotion = useReducedMotion();
  const [section, setSection] = useState<Section>("scale");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [candidateStatus, setCandidateStatus] = useState("all");
  const [offset, setOffset] = useState(0);
  const [refreshIndex, setRefreshIndex] = useState(0);
  const [page, setPage] = useState<CandidatePage>({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 });
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [candidateError, setCandidateError] = useState("");
  const summaryRequest = useRef(0);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const request = ++summaryRequest.current;
    setLoading(true);
    try {
      const data = await api<Summary>("/validation/summary", undefined, { signal });
      if (request === summaryRequest.current && !signal?.aborted) { setSummary(data); setError(""); }
    } catch (problem) {
      if (request === summaryRequest.current && !signal?.aborted) setError((problem as Error).message);
    } finally { if (request === summaryRequest.current && !signal?.aborted) setLoading(false); }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(controller.signal); }, 8000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [refresh]);
  useEffect(() => {
    if (section !== "candidates") return;
    const controller = new AbortController();
    setCandidateLoading(true);
    const params = new URLSearchParams({ search, status: candidateStatus, limit: String(PAGE_SIZE), offset: String(offset) });
    void api<CandidatePage>(`/validation/candidates?${params}`, undefined, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) { setPage(data); setCandidateError(""); } })
      .catch((problem: Error) => { if (!controller.signal.aborted) setCandidateError(problem.message); })
      .finally(() => { if (!controller.signal.aborted) setCandidateLoading(false); });
    return () => controller.abort();
  }, [section, search, candidateStatus, offset, refreshIndex]);
  const scale = summary?.scale;
  const biology = summary?.biology;
  const hasScale = !!scale && typeof scale.attempted === "number";
  const models = biology?.models ?? [];
  const biologicalModelCount = models.length + (summary?.safety?.endpoints?.length ?? 0);

  return <div className="validation-page" data-testid="validation-panel">
    <div className="vl-intro"><div><span className="vl-eyebrow">MEASURED PERFORMANCE · BIOLOGICAL EVIDENCE</span><h2>{tr("실행 결과와 생물학적 근거를 확인하세요")}</h2><p>{tr("실제 처리 성능, 모델의 평가 결과, 후보별 실측 근거를 각각 추적합니다.")}</p></div>
      <div className="vl-intro-actions"><button className="vl-button" onClick={() => { void refresh(); setRefreshIndex((value) => value + 1); }} disabled={loading} data-testid="validation-refresh"><RefreshCw size={17} className={loading ? "vl-spin" : ""} /><span>{tr("새로 고침")}</span></button><button className="vl-button" disabled={!summary} onClick={() => download(summary, "herbfold-validation-report.json")} aria-label={tr("검증 보고서 JSON 내려받기")}><ArrowDownToLine size={17} /></button></div>
    </div>
    {tr(error && <div className="vl-error" role="alert"><ShieldAlert size={18} /><span>{tr("검증 보고서 연결을 확인할 수 없습니다. ")}{tr(error)}</span></div>)}
    <div className="vl-overview">
      <article><span><Gauge size={20} />{tr("규모 검증")}</span><strong>{tr(hasScale ? number(scale.retained_unique) : "측정 대기")}</strong><small>{tr(hasScale ? "실제 중복 제거 후 보존 구조" : "저장된 실행 보고서가 표시됩니다.")}</small><Status status={scale?.status} /></article>
      <article><span><Microscope size={20} />{tr("약효·독성 모델")}</span><strong>{tr(biology?.models || summary?.safety?.endpoints ? msg("{0}개 평가", number(biologicalModelCount)) : "평가 대기")}</strong><small>{tr("표적·시험별 평가 · 적용 범위 확인")}</small><Status status={combinedStatus(biology?.status, summary?.safety?.status)} /></article>
      <article className="vl-clinical"><span><ShieldAlert size={20} />{tr("임상 유효성·안전성")}</span><strong>{tr("미확인")}</strong><small>{tr("구조 생성·모델 예측으로 확정할 수 없습니다.")}</small><span className="vl-status vl-caution">{tr("임상 검증 근거 없음")}</span></article>
    </div>
    <div className="vl-cohort-scope" data-testid="validation-cohort-scope"><BookOpen size={16} /><div><p>{tr("생물학적 평가는 별도로 선택한 후보 집합의 기록입니다. 규모 검증에서 생성한 모든 구조가 약효·독성 평가를 받은 것은 아닙니다.")}</p>{tr(typeof summary?.cohort?.combined_unique_count === "number" && <p>{tr("대규모 생성 ")}{tr(number(summary.cohort.scale_population))}{tr("개 중 표본 ")}{tr(number(summary.cohort.scale_sample_count))}{tr("개 + 기존 후보 ")}{tr(number(summary.cohort.prior_unique_count))}{tr("개 → 중복 제거한 평가 입력 ")}<strong>{tr(number(summary.cohort.combined_unique_count))}{tr("개")}</strong></p>)}<p>{tr("실제 보고된 평가 대상: COX-2/hERG ")}<strong>{tr(number(biology?.counts?.candidates))}{tr("개")}</strong> · Tox21 <strong>{tr(number(summary?.safety?.candidates?.total))}{tr("개")}</strong></p></div></div>
    <div className="vl-tabs" role="tablist" aria-label={tr("검증 결과 종류")} onKeyDown={(event) => {
      const index = SECTIONS.findIndex((item) => item.id === section);
      const next = event.key === "Home" ? 0 : event.key === "End" ? SECTIONS.length - 1
        : event.key === "ArrowRight" ? (index + 1) % SECTIONS.length : event.key === "ArrowLeft" ? (index + SECTIONS.length - 1) % SECTIONS.length : -1;
      if (next >= 0) { event.preventDefault(); setSection(SECTIONS[next].id); document.getElementById(`validation-tab-${SECTIONS[next].id}`)?.focus(); }
    }}>{tr(SECTIONS.map(({ id, label, icon: Icon }) => <button key={id} id={`validation-tab-${id}`} data-testid={`validation-tab-${id}`} role="tab" aria-selected={section === id} aria-controls={`validation-${id}`} tabIndex={section === id ? 0 : -1} className={section === id ? "is-active" : ""} onClick={() => setSection(id)}><Icon size={18} />{tr(label)}</button>))}</div>
    <motion.div key={section} id={`validation-${section}`} role="tabpanel" aria-labelledby={`validation-tab-${section}`} initial={reducedMotion ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .18 }}>
      {tr(section === "scale" && <ScaleSection scale={scale} loading={loading && !summary} />)}
      {tr(section === "biology" && <BiologySection biology={biology} safety={summary?.safety} loading={loading && !summary} />)}
      {tr(section === "candidates" && <div className="vl-stack">
        <div className="vl-card"><Heading eyebrow="CANDIDATE EVIDENCE" title={tr("후보별 실측·예측·판정 보류")} /><Note>{tr("동일 구조의 공개 실측 기록과 모델 예측을 구분합니다. 구조 경고가 없거나 예측값이 높아도 약효·안전성이 입증된 것은 아닙니다.")}</Note>
          <div className="vl-small-metrics"><MiniMetric label="표적 활성 검증 대상" value={biology?.counts?.candidates} /><MiniMetric label="표적 실측 기록 연결" value={biology?.counts?.exact_measured_candidates} /><MiniMetric label="표적 모델 예측 포함" value={biology?.counts?.predicted_candidates} /><MiniMetric label="표적 판정 보류 포함" value={biology?.counts?.abstained_candidates} /></div><p className="vl-muted">{tr("위 수치는 COX-2/hERG 표적 평가 기준이며, 같은 후보가 여러 근거 범주에 포함될 수 있습니다. 후보별 Tox21 결과는 각 기록에서 따로 확인합니다.")}</p>
          <form className="vl-search" onSubmit={(event) => { event.preventDefault(); setSearch(query.trim()); setOffset(0); }}><label><Search size={18} /><span className="vl-sr-only">{tr("후보 ID 또는 SMILES 검색")}</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tr("후보 ID · SMILES 검색")} maxLength={160} data-testid="validation-candidate-search" /></label><select aria-label={tr("후보 검증 근거 필터")} value={candidateStatus} onChange={(event) => { setCandidateStatus(event.target.value); setOffset(0); }} data-testid="validation-candidate-status"><option value="all">{tr("전체 기록")}</option><option value="measured">{tr("동일 구조 실측")}</option><option value="predicted">{tr("예측 포함")}</option><option value="alerts">{tr("구조 경고 포함")}</option><option value="abstained">{tr("판정 보류 포함")}</option></select><button type="submit" className="vl-button vl-primary" data-testid="validation-candidate-search-submit">{tr("검색")}</button></form>
        </div>
        {tr(candidateError && <div className="vl-error" role="alert">{tr("후보 검증 결과를 불러오지 못했습니다. ")}{tr(candidateError)}</div>)}
        <div className="vl-result-count" aria-live="polite">{tr(candidateLoading ? "후보 검증 기록을 불러오는 중입니다." : msg("{0}개 검증 기록", number(page.total)))}{tr(candidateLoading && <LoaderCircle size={16} className="vl-spin" />)}</div>
        {tr(!candidateError && candidateLoading && !page.items.length ? <Empty loading title={tr("후보 검증 기록을 불러오는 중")} description="현재 저장된 근거만 표시합니다." /> : !candidateError && !page.items.length ? <Empty title={tr(search ? "일치하는 검증 기록이 없습니다" : "아직 후보별 검증 결과가 없습니다")} description={search ? "후보 ID 또는 SMILES 검색어를 확인하세요." : "검증 실행이 기록을 저장하면 이곳에서 확인할 수 있습니다."} /> : <div className="vl-candidate-list" aria-busy={candidateLoading}>{tr(page.items.map((candidate) => <CandidateCard key={candidate.id} candidate={candidate} onInspect={onInspect} />))}</div>)}
        <div className="vl-pagination"><span>{tr(page.total ? `${number(page.offset + 1)}–${number(page.offset + page.items.length)} / ${number(page.total)}` : "결과 없음")}</span><div><button className="vl-button" disabled={candidateLoading || !page.items.length} onClick={() => download(page.items, `candidate-validation-page-${Math.floor(page.offset / PAGE_SIZE) + 1}.json`)}><ArrowDownToLine size={16} /><span>{tr("현재 페이지")}</span></button><button className="vl-button" aria-label={tr("후보 검증 이전 페이지")} disabled={candidateLoading || page.offset === 0} onClick={() => setOffset(Math.max(0, page.offset - PAGE_SIZE))}><ChevronLeft size={18} /></button><button className="vl-button" aria-label={tr("후보 검증 다음 페이지")} disabled={candidateLoading || page.offset + page.items.length >= page.total} onClick={() => setOffset(page.offset + PAGE_SIZE)}><ChevronRight size={18} /></button></div></div>
      </div>)}
    </motion.div>
    {tr((summary?.sources?.length || summary?.limitations?.length) ? <section className="vl-card vl-report-notes"><Heading eyebrow="PROVENANCE & SCOPE" title={tr("출처와 해석 범위")} />{tr(summary.sources?.length ? <div className="vl-source-links">{tr(summary.sources.map((source, index) => <div key={source.id || index}>{tr(url(source.url || source.source_url) ? <a href={url(source.url || source.source_url)} target="_blank" rel="noopener noreferrer">{tr(source.name || source.label || source.id || "원본 자료")}<ExternalLink size={14} /></a> : <span>{tr(source.name || source.label || source.id)}</span>)}{tr(source.license && <small>{tr(source.license)}</small>)}</div>))}</div> : null)}{tr(summary.limitations?.length ? <ul>{tr(summary.limitations.map((item, index) => <li key={index}>{tr(explanation(item))}</li>))}</ul> : null)}</section> : null)}
  </div>;
}

function ScaleSection({ scale, loading }: { scale?: Scale; loading: boolean }) {
  const hasMeasurements = !!scale && typeof scale.attempted === "number";
  if (!hasMeasurements) return <div className="vl-card"><Heading eyebrow="OBSERVED EXECUTION" title={tr("실제 실행 보고서")} status={scale?.status} /><Empty loading={loading || scale?.status === "preparing"} title={tr(scale?.status === "preparing" ? "측정 입력을 준비하고 있습니다" : "아직 저장된 성능 측정치가 없습니다")} description="시도 수·고유 보존 수·처리 시간·메모리·저장 공간을 실제 실행에서 수집합니다." /></div>;
  const measured = scale!;
  const fraction = typeof measured.requested_attempts === "number" && measured.requested_attempts > 0 ? Math.min(1, (measured.attempted || 0) / measured.requested_attempts) : null;
  const counters = [
    ["attempted", "생성 시도", measured.attempted], ["sanitized", "구조 검증 통과", measured.sanitized],
    ["passed_filters", "필터 통과", measured.passed_filters], ["retained_unique", "고유 구조 보존", measured.retained_unique],
    ["duplicates", "중복", measured.duplicates], ["rejected", "제외", measured.rejected],
  ] as const;
  return <div className="vl-stack"><section className="vl-card"><Heading eyebrow="OBSERVED EXECUTION" title={tr("실측 실행 현황")} status={measured.status} /><div className="vl-run-meta"><span><Clock3 size={15} />{tr("최종 기록 ")}{tr(date(measured.updated_at))}</span><span>{tr(measured.phase === "preparation" ? "입력 준비 단계" : measured.phase === "generation" ? "구조 생성 단계" : measured.phase || "단계 미기록")}</span></div>
    <div className="vl-measurement-grid">{tr(counters.map(([key, label, value]) => <div key={key} className={key === "retained_unique" ? "vl-retained" : ""}><span>{tr(label)}</span><strong data-testid={`validation-scale-${key}`}>{tr(number(value))}</strong><small>{tr(key === "retained_unique" ? "실제 중복 제거 후 저장" : key === "attempted" ? "반복·제외 구조 포함" : key === "passed_filters" ? "중복 제거 전 통과 수" : "서버 실행 기록")}</small></div>))}</div>
    <div className="vl-accounting" data-testid="validation-scale-accounting"><strong>{tr("기존 부모와 일치해 제외: ")}{tr(number(measured.known_parent_matches))}{tr("개")}</strong><span>{tr("시도 ")}{tr(number(measured.attempted))} {tr(" = 고유 보존 ")}{tr(number(measured.retained_unique))} {tr(" + 중복 ")}{tr(number(measured.duplicates))} {tr(" + 제외 ")}{tr(number(measured.rejected))} {tr(" + 기존 부모 일치 ")}{tr(number(measured.known_parent_matches))}</span></div>
    <div className="vl-progress"><div><span>{tr("이번 실행의 시도 목표")}</span><strong data-testid="validation-scale-requested">{tr(number(measured.requested_attempts))}{tr("회")}</strong></div>{tr(fraction !== null && <><div className="vl-progress-track" role="progressbar" aria-label={tr("요청한 시도 수 대비 실제 시도")} aria-valuenow={Number((fraction * 100).toFixed(2))} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${fraction * 100}%` }} /></div><small>{tr(number(measured.attempted))}{tr("회 실행 / ")}{tr(number(measured.requested_attempts))}{tr("회 요청 · ")}{tr(percent(fraction))}</small></>)}<p>{tr("시도 목표는 고유 후보 수와 다릅니다. 중복과 제외 구조가 포함되므로 실제 보존 수를 함께 확인하세요.")}</p></div>
  </section><div className="vl-two-columns"><section className="vl-card"><Heading eyebrow="MEASURED RESOURCES" title={tr("처리량과 자원 사용")} /><dl className="vl-facts"><Fact icon={<Activity size={17} />} label="실측 시도 처리량" value={`${number(measured.attempts_per_second, 2)} 회/초`} /><Fact label="실측 보존 처리량" value={`${number(measured.retained_per_second, 2)} 개/초`} /><Fact icon={<Clock3 size={17} />} label="준비·생성 누적 실행 시간" value={duration(measured.elapsed_seconds)} /><Fact label="생성 단계 시간" value={duration(measured.generation_seconds)} /><Fact icon={<HardDrive size={17} />} label="주 프로세스 최대 RSS" value={typeof measured.process_peak_rss_mb === "number" ? `${number(measured.process_peak_rss_mb, 2)} MiB` : "—"} /><Fact label="종료된 자식 최대 RSS" value={typeof measured.children_peak_rss_mb === "number" ? `${number(measured.children_peak_rss_mb, 2)} MiB` : "—"} /><Fact label="관측된 동시 RSS 합계 최고값" value={typeof measured.sampled_peak_aggregate_rss_mb === "number" ? `${number(measured.sampled_peak_aggregate_rss_mb, 2)} MiB` : "—"} /><Fact icon={<Database size={17} />} label="데이터베이스 기록 크기" value={bytes(measured.database_bytes)} /><Fact label="작업 프로세스 수" value={number(measured.workers)} /></dl><p className="vl-muted" data-testid="validation-timing-scope">{tr("CPU에서의 준비·구조 생성·저장 실측입니다. 중단·감사 시간과 AlphaFold 3·QPU 계산 시간은 포함하지 않습니다.")}</p><p className="vl-muted">{tr("주·종료 자식 RSS는 개별 최대값입니다. 동시 RSS 합계는 기록 시점의 부모·자식 관측값이며 공유 메모리가 중복 집계될 수 있습니다.")}</p></section>
    <section className="vl-card vl-projection"><Heading eyebrow="EXTRAPOLATION · NOT A COMPLETED RUN" title={tr("대규모 실행 시간 추정")} /><span className="vl-status vl-caution">{tr("외삽값 · 실측 완료 수치 아님")}</span>{tr(measured.projection && measured.projection.is_measured === false ? <><div className={`vl-projection-number ${measured.projection.finite_space_allows_target === false ? "is-infeasible" : ""}`} data-testid="validation-projection-time">{tr(measured.projection.finite_space_allows_target === false ? "현재 입력으로 도달 불가" : duration(measured.projection.estimated_seconds))}</div><p><strong>{tr(number(measured.projection.target_attempts))}{tr("회 시도")}</strong>{tr("에 대한 계산 추정")}</p><dl className="vl-facts">{tr(measured.projection.finite_space_allows_target === false && <Fact label="관측 속도를 단순 연장한 시간" value={duration(measured.projection.estimated_seconds)} />)}<Fact label="추정의 근거가 된 실제 시도" value={`${number(measured.projection.basis_attempts)}회`} /></dl>{tr(measured.projection.finite_space_allows_target === false && <div className="vl-reason" data-testid="validation-finite-space-warning"><ShieldAlert size={18} /><span>{tr("현재 입력의 조합 상한은 ")}{tr(number(measured.compatible_pair_slots_upper_bound))}{tr("회입니다. 이 입력만으로 ")}{tr(number(measured.projection.target_attempts))}{tr("회 시도에 도달할 수 없으며, 위 시간은 처리 속도의 산술 외삽입니다.")}</span></div>)}<Note>{tr("현재 관측한 처리량을 연장한 시간입니다. 입력 다양성·중복 증가·메모리·디스크 병목이 바뀌면 실제 소요 시간과 보존 수가 달라집니다.")}</Note></> : <Empty title={tr("외삽 보고서 대기")} description="실제 측정에 기반한 외삽 결과가 기록되면 표시합니다." />)}<p className="vl-muted">{tr("1억회 시도 추정은 1억개 고유 후보 생성 또는 약효 검증 완료를 뜻하지 않습니다.")}</p></section></div>
    <section className="vl-card"><Heading eyebrow="INPUT & REPRODUCIBILITY" title={tr("실행 입력과 재현 근거")} /><div className="vl-small-metrics"><MiniMetric label="부모 구조·역할 레코드" value={measured.prepared_parents} /><MiniMetric label="천연물 조각" value={measured.natural_fragments} /><MiniMetric label="약물 조각" value={measured.drug_fragments} /><MiniMetric label="가능한 조합 상한" value={measured.compatible_pair_slots_upper_bound} /></div><p className="vl-muted">{tr("고유 부모 구조 ")}{tr(number(measured.distinct_catalog_parent_ids))}{tr("개. 같은 구조가 천연물과 약물 역할에 각각 포함될 수 있습니다.")}</p>{tr(measured.retained_milestones?.some((item) => item.measured) && <><h4 className="vl-subheading vl-milestone-heading">{tr("실제 고유 구조 보존 도달 기록")}</h4><div className="vl-table-scroll"><table className="vl-table"><thead><tr><th scope="col">{tr("보존 기준")}</th><th scope="col">{tr("실제 저장")}</th><th scope="col">{tr("당시 시도")}</th><th scope="col">{tr("생성 단계 경과")}</th></tr></thead><tbody>{tr(measured.retained_milestones.filter((item) => item.measured).map((item) => <tr key={item.retained_threshold}><th scope="row">{tr(number(item.retained_threshold))}{tr("개")}</th><td>{tr(number(item.committed_retained_unique))}{tr("개")}</td><td>{tr(number(item.attempted))}{tr("회")}</td><td>{tr(duration(item.generation_seconds))}</td></tr>))}</tbody></table></div><p className="vl-muted">{tr("저장 배치가 완료된 시점의 기록입니다. 개별 구조의 생성 순간보다 늦게 기록될 수 있습니다.")}</p></>)}<RawDetails value={measured} label="실행 설정·전체 원본 보고서" /></section></div>;
}

function BiologySection({ biology, safety, loading }: { biology?: Biology; safety?: SafetyReport; loading: boolean }) {
  return <div className="vl-stack"><section className="vl-card"><Heading eyebrow="ASSAY EVIDENCE & MODEL VALIDATION" title={tr("표적별 근거의 범위를 확인하세요")} status={biology?.status} /><Note>{tr("COX-2/PTGS2 활성은 특정 실험 조건의 표적 활성입니다. hERG/KCNH2는 심장 이온채널 관련 위험 평가의 한 항목이며, 두 결과만으로 사람에서의 약효나 전체 독성을 판단할 수 없습니다.")}</Note>
    {tr(biology?.targets?.length ? <div className="vl-targets">{tr(biology.targets.map((target) => <div key={target.id}><span className="vl-target-id">{tr(target.id)}</span><strong>{tr(target.label || target.id)}</strong><small>{tr([target.chembl_id, target.uniprot].filter(Boolean).join(" · "))}</small>{tr(url(target.source_url) && <a href={url(target.source_url)} target="_blank" rel="noopener noreferrer">{tr("표적 원본 ")}<ExternalLink size={13} /></a>)}</div>))}</div> : <p className="vl-muted">{tr("표적별 실험 자료와 모델 보고서가 저장되면 표시됩니다.")}</p>)}
  </section>{tr(biology?.models?.length ? biology.models.map((model, index) => <ModelCard key={model.id || `${model.target}-${index}`} model={model} />) : <div className="vl-card"><Empty loading={loading || biology?.status === "running"} title={tr("모델 평가 보고서 대기")} description="훈련과 분리된 평가 결과, 적용 범위, 불확실성 기록을 확인한 뒤 예측을 해석합니다." /></div>)}
  {tr(safety && <SafetySection safety={safety} />)}
  {tr(biology?.limitations?.length ? <section className="vl-card"><Heading eyebrow="MODEL LIMITATIONS" title={tr("모델 해석 제한")} /><ul className="vl-limitations">{tr(biology.limitations.map((item, index) => <li key={index}>{tr(explanation(item))}</li>))}</ul></section> : null)}</div>;
}

function SafetySection({ safety }: { safety: SafetyReport }) {
  return <section className="vl-card" data-testid="validation-tox21"><Heading eyebrow="TOX21 · ASSAY ACTIVITY SCREENING" title={tr("독성 관련 시험별 평가")} status={safety.status} /><Note>{tr("Tox21의 각 항목은 특정 수용체·세포 반응 시험의 활성 여부를 다룹니다. 모델 점수는 해당 시험의 활성 점수이며, 사람에서의 안전 확률이나 전체 독성 점수가 아닙니다.")}</Note>
    <div className="vl-small-metrics"><MiniMetric label="정제된 자료 구조" value={safety.dataset?.curated_structures} /><MiniMetric label="실측 자료 동일 구조" value={safety.candidates?.exact_dataset_matches} /><MiniMetric label="모델 적용 범위 내" value={safety.candidates?.in_domain} /><MiniMetric label="모델 적용 범위 밖" value={safety.candidates?.out_of_domain} /></div>
    {tr(safety.candidates?.cohorts && <><h4 className="vl-subheading">{tr("후보 집합별 평가 범위")}</h4><div className="vl-table-scroll"><table className="vl-table" data-testid="validation-tox21-cohorts"><thead><tr><th scope="col">{tr("후보 집합")}</th><th scope="col">{tr("평가 대상")}</th><th scope="col">{tr("범위 내")}</th><th scope="col">{tr("범위 밖")}</th><th scope="col">{tr("실측 기록")}</th><th scope="col">{tr("예측 포함")}</th></tr></thead><tbody>{tr(Object.entries(safety.candidates.cohorts).map(([cohort, values]) => <tr key={cohort}><th scope="row">{tr(({ prior_campaign_complete: "기존 캠페인 전체", scale_uniform_sample: "대규모 생성 균등 표본" } as Record<string, string>)[cohort] || cohort)}</th><td>{tr(number(values.total))}</td><td>{tr(number(values.in_domain))}</td><td>{tr(number(values.out_of_domain))}</td><td>{tr(number(values.observed_assay_matches))}</td><td>{tr(number(values.with_any_prediction))}</td></tr>))}</tbody></table></div><p className="vl-muted">{tr("두 집합에 속한 동일 구조는 각 행에 포함됩니다. 표본의 평가 결과를 대규모 생성 전체의 검증 완료로 해석할 수 없습니다.")}</p></>)}
    {tr(safety.endpoints?.length ? <><div className="vl-table-scroll"><table className="vl-table"><thead><tr><th scope="col">{tr("시험 항목")}</th><th scope="col">{tr("학습 / 평가")}</th><th scope="col">{tr("평가 활성 비율")}</th><th scope="col">{tr("ROC-AUC / 95% 구간")}</th><th scope="col">Average precision</th><th scope="col">Brier</th><th scope="col">{tr("범위 내 비율")}</th><th scope="col">{tr("모델 평가 기준")}</th></tr></thead><tbody>{tr(safety.endpoints.map((endpoint) => <tr key={endpoint.endpoint}><th scope="row">{tr(endpoint.endpoint)}</th><td>{tr(number(endpoint.train_count))} / {tr(number(endpoint.test_count))}</td><td>{tr(percent(endpoint.metrics?.prevalence))}</td><td>{tr(precise(endpoint.metrics?.roc_auc))}{tr(Array.isArray(endpoint.metrics?.roc_auc_ci95) && <small className="vl-table-secondary">{tr(precise(endpoint.metrics.roc_auc_ci95[0]))}–{tr(precise(endpoint.metrics.roc_auc_ci95[1]))}</small>)}</td><td>{tr(precise(endpoint.metrics?.average_precision))}</td><td>{tr(precise(endpoint.metrics?.brier))}</td><td>{tr(percent(endpoint.applicability?.test_coverage))}</td><td><span className={`vl-status ${endpoint.model_gate_passed === false ? "vl-caution" : ""}`}>{tr(endpoint.model_gate_passed === true ? "평가 기준 충족" : endpoint.model_gate_passed === false ? "평가 기준 미충족" : "평가 미기록")}</span></td></tr>))}</tbody></table></div><p className="vl-muted">{tr("Brier는 이 평가 자료에서의 활성 예측 오차입니다. 평가 기준 충족은 모델의 사용 조건이며 후보 물질의 안전 판정이 아닙니다.")}</p></> : <Empty title={tr("독성 시험 평가 보고서 대기")} description="측정된 모델 평가 지표가 기록되면 시험 항목별로 표시합니다." />)}
    {tr(url(safety.dataset?.source_url) && <a className="vl-evidence-link" href={url(safety.dataset?.source_url)} target="_blank" rel="noopener noreferrer">{tr("Tox21 원본 자료")}<ExternalLink size={14} /></a>)}
    {tr(safety.limitations?.length ? <ul className="vl-limitations">{tr(safety.limitations.map((item, index) => <li key={index}>{tr(explanation(item))}</li>))}</ul> : null)}<RawDetails value={safety} label="자료 해시·독성 시험 평가 원본 보고서" />
  </section>;
}

function ModelCard({ model }: { model: Model }) {
  const [evaluationScope, setEvaluationScope] = useState<"test" | "test_in_domain">("test");
  const rawEntries = Object.entries(model.metrics ?? {}).filter(([, value]) => value && typeof value === "object");
  const nestedMetrics = rawEntries.some(([, value]) => value.test && typeof value.test === "object");
  const entries = rawEntries.map(([name, values]) => [name, nestedMetrics ? (values[evaluationScope] as Values | undefined) ?? {} : values] as const);
  const metricNames = [...new Set(entries.flatMap(([, values]) => Object.keys(values).filter((key) => typeof values[key] === "number" || values[key] === null)))];
  return <section className="vl-card vl-model" data-testid="validation-model"><Heading eyebrow={`${model.target || "TARGET"} / ${model.endpoint || "ENDPOINT"}`} title={tr(model.assay_id || model.id || "표적 모델")} status={model.quality_status || model.status} /><div className="vl-run-meta"><span>{tr("선택 모델: ")}{tr(model.selected_model || "선정 전")}</span><span>{tr("실험 유형: ")}{tr(model.assay_type || "미기록")}</span></div>
    {tr(model.assay_description && <div className="vl-assay-description"><h4 className="vl-subheading">{tr("이 실험이 측정한 대상")}</h4><p>{tr(model.assay_description)}</p>{tr(model.assay_id === "CHEMBL1827362" && <small>{tr("hERG 리간드의 경쟁 결합 시험입니다. 이 모델은 채널 전류 차단이나 사람의 심장 안전성을 직접 예측한 것이 아닙니다.")}</small>)}{tr(url(model.assay_source_url) && <a href={url(model.assay_source_url)} target="_blank" rel="noopener noreferrer">{tr("실험 원본 기록")}<ExternalLink size={13} /></a>)}</div>)}
    {tr((Array.isArray(model.reason) ? model.reason.length > 0 : !!model.reason) && <div className="vl-reason"><CircleHelp size={17} /><span>{tr(Array.isArray(model.reason) ? model.reason.map(explanation).join(" · ") : explanation(model.reason || ""))}</span></div>)}
    <div className="vl-model-splits"><ValueGroup title={tr("자료 선별")} values={model.data_counts} /><ValueGroup title={tr("학습·보정·평가 분리")} values={model.split_counts} /></div>
    {tr(entries.length ? <><h4 className="vl-subheading">{tr("분리된 평가 자료의 모델 성능")}</h4>{tr(nestedMetrics && <div className="vl-metric-scope" aria-label={tr("모델 평가 범위")}><button aria-pressed={evaluationScope === "test"} onClick={() => setEvaluationScope("test")}>{tr("전체 평가 자료")}</button><button aria-pressed={evaluationScope === "test_in_domain"} onClick={() => setEvaluationScope("test_in_domain")}>{tr("적용 범위 내 평가 자료")}</button></div>)}<div className="vl-table-scroll"><table className="vl-table"><thead><tr><th scope="col">{tr("모델")}</th>{tr(metricNames.map((name) => <th scope="col" key={name}>{tr(VALUE_LABELS[name] || name)}</th>))}</tr></thead><tbody>{tr(entries.map(([name, values]) => <tr key={name} className={name === model.selected_model ? "is-selected" : ""}><th scope="row">{tr(({ median_baseline: "중앙값 기준 모델", ridge: "Ridge", random_forest: "Random Forest" } as Record<string, string>)[name] || name)}{tr(name === model.selected_model && <span className="vl-inline-label">{tr("선택")}</span>)}</th>{tr(metricNames.map((key) => <td key={key}>{tr(["n", "count"].includes(key) ? number(values[key]) : precise(values[key]))}</td>))}</tr>))}</tbody></table></div><p className="vl-muted">{tr("MAE·RMSE는 p")}{tr(model.endpoint || "Activity")} {tr(" 예측 오차이며 낮을수록 작습니다. 모델 선택용 자료와 분리된 평가 결과이고, 임상 성공률을 나타내지 않습니다.")}</p></> : <p className="vl-muted">{tr("분리 평가 지표가 아직 기록되지 않았습니다.")}</p>)}
    <div className="vl-two-columns vl-model-uncertainty"><div><h4 className="vl-subheading">{tr("적용 범위")}</h4><dl className="vl-facts"><Fact label="근접 구조 유사도 기준" value={precise(model.applicability?.threshold)} /><Fact label="평가 자료의 범위 내 비율" value={percent(model.applicability?.test_coverage)} /></dl></div><div><h4 className="vl-subheading">{tr("예측 불확실성")}</h4><dl className="vl-facts"><Fact label="목표 구간 포함률" value={percent(model.uncertainty?.nominal_coverage)} /><Fact label="평가 자료 실제 포함률" value={percent(model.uncertainty?.empirical_test_coverage)} /><Fact label="예측 구간 반폭" value={precise(model.uncertainty?.interval_half_width)} /></dl><small className="vl-muted">{tr(model.uncertainty?.method ? explanation(model.uncertainty.method) : "불확실성 산정법 미기록")}</small></div></div><RawDetails value={model} label="모델 설정·평가 전체 기록" />
  </section>;
}

function CandidateCard({ candidate, onInspect }: { candidate: ValidatedCandidate; onInspect: Props["onInspect"] }) {
  const alerts = candidate.structural_alerts;
  const painCount = alerts?.PAINS?.length;
  const brenkCount = alerts?.BRENK?.length;
  return <article className="vl-card vl-candidate" data-testid="validation-candidate"><div className="vl-candidate-heading"><div><span className="vl-eyebrow">CANDIDATE RECORD</span><h3>{tr(candidateName(candidate))}</h3><span className="vl-candidate-id">{tr(candidate.id)}</span></div><button className="vl-button vl-primary" onClick={() => onInspect(asCompound(candidate))} data-testid="validation-inspect-candidate" aria-label={tr(msg("{0} 3D 구조 보기", candidateName(candidate)))}>{tr("3D 구조")}<ArrowRight size={16} /></button></div><code className="vl-smiles">{tr(candidate.canonical_smiles || candidate.smiles)}</code>
    <div className="vl-candidate-flags">{tr(candidate.cohorts?.map((cohort) => <span className="vl-status" key={cohort}>{tr(({ prior_campaign_complete: "기존 캠페인 후보", scale_uniform_sample: "대규모 생성 균등 표본" } as Record<string, string>)[cohort] || cohort)}</span>))}<span className={`vl-status ${candidate.catalog_identity?.known ? "vl-caution" : ""}`}>{tr(candidate.catalog_identity?.known === true ? "자료 내 동일 구조 존재" : candidate.catalog_identity?.known === false ? "현재 자료 내 동일 구조 없음" : "동일 구조 대조 미기록")}</span><span className={`vl-status ${(painCount || brenkCount) ? "vl-caution" : ""}`}>PAINS {tr(number(painCount))} · BRENK {tr(number(brenkCount))}</span></div>
    {tr(candidate.evidence?.length ? <div className="vl-evidence-list">{tr(candidate.evidence.map((evidence, index) => <EvidenceRow key={`${evidence.target}-${evidence.assay_id}-${index}`} evidence={evidence} />))}</div> : <p className="vl-muted">{tr("연결된 실험 기록 또는 모델 예측이 없습니다. 약효·독성 판정은 보류합니다.")}</p>)}
    {tr(candidate.safety && <CandidateSafetyEvidence safety={candidate.safety} />)}
    <RawDetails value={{ structural_alerts: candidate.structural_alerts, catalog_identity: candidate.catalog_identity, evidence: candidate.evidence, safety: candidate.safety }} label="구조 경고·동일 구조·근거 원본" />
  </article>;
}

function CandidateSafetyEvidence({ safety }: { safety: CandidateSafety }) {
  const endpoints = [...new Set([...(safety.observed_assays ?? []).map((item) => item.endpoint), ...(safety.predictions ?? []).map((item) => item.endpoint)])];
  const predicted = safety.predictions?.filter((item) => item.status === "predicted").length;
  const abstained = safety.predictions?.filter((item) => item.status === "abstained").length;
  return <details className="vl-tox-evidence"><summary><ShieldAlert size={16} /><span>{tr("Tox21 시험 근거")}</span><span className="vl-status">{tr("예측 ")}{tr(number(predicted))} {tr(" · 보류 ")}{tr(number(abstained))}</span></summary><div className="vl-tox-domain"><span className={`vl-status ${safety.in_domain === false ? "vl-caution" : ""}`}>{tr(safety.in_domain === true ? "학습 자료 적용 범위 내" : safety.in_domain === false ? "학습 자료 적용 범위 밖" : "적용 범위 미기록")}</span><small>{tr("근접 학습 구조 유사도 ")}{tr(precise(safety.nearest_training_similarity))}</small></div>{tr(endpoints.length ? <div className="vl-table-scroll"><table className="vl-table"><thead><tr><th scope="col">{tr("시험")}</th><th scope="col">{tr("동일 구조 실측 기록")}</th><th scope="col">{tr("모델 판정 상태")}</th><th scope="col">{tr("시험 활성 점수")}</th></tr></thead><tbody>{tr(endpoints.map((endpoint) => {
    const observed = safety.observed_assays?.filter((item) => item.endpoint === endpoint) ?? [];
    const prediction = safety.predictions?.find((item) => item.endpoint === endpoint);
    return <tr key={endpoint}><th scope="row">{tr(endpoint)}</th><td>{tr(observed.length ? observed.map((item, index) => <span className="vl-table-secondary" key={index}>{tr(item.label === 1 ? "시험 활성" : item.label === 0 ? "시험 비활성" : msg("기록 {0}", item.label))}</span>) : "기록 없음")}</td><td><Status status={prediction?.status || "unavailable"} />{tr(prediction?.reason && <small className="vl-table-secondary">{tr(explanation(prediction.reason))}</small>)}</td><td>{tr(prediction?.status === "predicted" ? precise(prediction.score) : "—")}</td></tr>;
  }))}</tbody></table></div> : <p className="vl-muted">{tr("연결된 독성 시험 또는 예측 기록이 없습니다.")}</p>)}<p className="vl-muted">{tr("시험 비활성 기록은 다른 독성이나 임상 위험이 없다는 의미가 아닙니다.")}</p></details>;
}

function EvidenceRow({ evidence }: { evidence: Evidence }) {
  const interval = Array.isArray(evidence.interval) ? evidence.interval : evidence.interval ? [evidence.interval.lower, evidence.interval.upper] : undefined;
  return <div className={`vl-evidence vl-evidence-${evidence.status}`}><div><strong>{tr(evidence.target || "표적 미기록")}</strong><small>{tr(evidence.endpoint || "평가 항목 미기록")}{tr(evidence.assay_id ? ` · ${evidence.assay_id}` : "")}</small>{tr(evidence.assay_id === "CHEMBL1827362" && <small>{tr("hERG 리간드 경쟁 결합 시험")}</small>)}<Status status={evidence.status} /></div><div className="vl-evidence-value">{tr(evidence.status === "abstained" ? <strong>{tr("판정 보류")}</strong> : <><span>p{tr(evidence.endpoint || "Activity")}</span><strong>{tr(precise(evidence.value_pactivity))}</strong></>)}{tr(interval && <small>{tr("구간 ")}{tr(precise(interval[0]))}–{tr(precise(interval[1]))}</small>)}{tr(typeof evidence.nearest_similarity === "number" && <small>{tr("근접 구조 유사도 ")}{tr(precise(evidence.nearest_similarity))}</small>)}</div>{tr(evidence.reason && <p>{tr(explanation(evidence.reason))}</p>)}{tr(url(evidence.source_url) && <a href={url(evidence.source_url)} target="_blank" rel="noopener noreferrer">{tr("근거 원문 ")}<ExternalLink size={13} /></a>)}</div>;
}

function Heading({ eyebrow, title, status }: { eyebrow: string; title: string; status?: string }) {
  return <div className="vl-heading"><div><span className="vl-eyebrow">{tr(eyebrow)}</span><h3>{tr(title)}</h3></div>{tr(status && <Status status={status} />)}</div>;
}
function Status({ status }: { status?: string }) {
  const value = status || "pending";
  return <span className={`vl-status ${["running", "preparing", "predicted", "qualified"].includes(value) ? "vl-active" : ["abstained", "insufficient_data", "failed_validation", "failed"].includes(value) ? "vl-caution" : ""}`} data-status={value}><span />{tr(STATUS[value] || value)}</span>;
}
function Note({ children }: { children: ReactNode }) { return <div className="vl-note"><BookOpen size={18} /><p>{tr(children)}</p></div>; }
function Empty({ title, description, loading = false }: { title: string; description: string; loading?: boolean }) { return <div className="vl-empty">{tr(loading ? <LoaderCircle size={30} className="vl-spin" /> : <FileCheck2 size={32} />)}<h4>{tr(title)}</h4><p>{tr(description)}</p></div>; }
function MiniMetric({ label, value }: { label: string; value: unknown }) { return <div><span>{tr(label)}</span><strong>{tr(number(value))}</strong></div>; }
function Fact({ label, value, icon }: { label: string; value: ReactNode; icon?: ReactNode }) { return <div><dt>{tr(icon)}{tr(label)}</dt><dd>{tr(value)}</dd></div>; }
function ValueGroup({ title, values }: { title: string; values?: Values }) {
  const entries = Object.entries(values ?? {}).filter(([, value]) => typeof value === "number" || typeof value === "string");
  return <div><h4 className="vl-subheading">{tr(title)}</h4>{tr(entries.length ? <dl className="vl-facts">{tr(entries.map(([key, value]) => <Fact key={key} label={VALUE_LABELS[key] || key} value={typeof value === "number" ? number(value, 2) : String(value)} />))}</dl> : <p className="vl-muted">{tr("분리된 자료 수 미기록")}</p>)}</div>;
}
function RawDetails({ value, label, open = false }: { value: unknown; label: string; open?: boolean }) { return <details className="vl-raw" open={open || undefined}><summary><FileCheck2 size={14} />{tr(label)}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>; }

export { ValidationPanel };

