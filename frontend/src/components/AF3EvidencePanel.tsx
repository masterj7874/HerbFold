import { useEffect, useState } from "react";
import { ArrowRight, Check, CircleAlert, Clock3, Cpu, Database } from "lucide-react";
import { comparisonEntry, conditionDifferences, hasVerifiedPrediction, measuredMetric, metricDifference } from "../lib/predictionEvidence";
import { formatElapsed, gpuMemoryWarning, predictionExecutionProgress, predictionExecutionTimeline, recordedExecutionSteps, recordedStageDetail } from "../lib/predictionProgress";
import type { MSAFeatures, PredictionEnvelope, PredictionMetrics } from "../types/prediction";

const integer = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ko-KR") : "미기록";
const text = (value: unknown) => value == null || value === "" ? "미기록" : typeof value === "object" ? JSON.stringify(value) : String(value);
const timestamp = (value: string) => new Date(value).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const phaseLabels: Record<string, string> = {
  queued: "실행 대기", data_pipeline: "CPU · MSA·템플릿 검색", inference: "AF3 · 구조 추론", output_validation: "출력의 성분·표적 확인", completed: "계산·출력 확인 종료",
};
const phaseDescriptions: Record<string, string> = {
  queued: "앞선 작업이 끝나고 실행 자원이 배정되면 시작합니다.",
  data_pipeline: "진화 서열과 구조 템플릿을 검색합니다. 긴 CPU 검색은 로그가 잠시 갱신되지 않아도 진행 중일 수 있습니다.",
  inference: "입력 특징 처리와 모델 준비를 거쳐 선택한 성분·표적의 좌표를 계산합니다. 이 단계는 오래 걸릴 수 있으며, 검색 특징을 재사용해도 성분별 추론은 별도로 수행합니다.",
  output_validation: "실제 출력에서 성분·표적의 일치와 공유결합 길이를 확인합니다. 구조 정확도·약효 검증과는 별개입니다.",
  completed: "실행과 출력 처리가 끝났습니다. 내부 신뢰도와 구조 품질을 별도로 해석해야 합니다.",
};
const featureLabels: Record<MSAFeatures["status"], string> = { not_requested: "MSA·템플릿 생략", awaiting_search: "MSA 검색 대기", searching: "검색 중", ready: "검색 특징 준비됨", failed: "검색 실패", combined_uncached: "통합 검색·추론" };
const featureDescriptions: Partial<Record<MSAFeatures["status"], string>> = {
  searching: "완료된 검색 특징을 검증한 뒤 실제 서열·템플릿 수를 표시합니다. 검색 실패 시 MSA 없는 계산으로 자동 전환하지 않습니다.",
  awaiting_search: "실행 후 MSA·템플릿을 검색하거나 같은 조건의 검증된 검색 특징을 재사용합니다. 아직 검색 결과 수는 확인되지 않았습니다.",
  not_requested: "이 기록은 MSA·템플릿을 생략하는 탐색 모드입니다.",
  failed: "검색 오류를 확인하고 재시도해야 합니다. MSA 없는 결과로 대체하지 않습니다.",
  combined_uncached: "다중 단백질 입력은 검색·추론을 통합 실행하며, 단일 단백질 검색 캐시를 사용하지 않습니다.",
};
const featureWarningLabels: Record<string, string> = {
  "Completed MSA search returned only the query sequence; no unpaired homologous sequence was found.": "MSA 검색을 마쳤지만 Unpaired MSA에 표적 서열만 있어 상동 서열을 찾지 못했습니다.",
  "The paired MSA contains only the query sequence.": "Paired MSA에는 표적 서열만 포함되어 있습니다.",
  "Completed template search returned no usable templates at the requested date cutoff.": "템플릿 검색을 마쳤지만 지정한 날짜 기준에서 사용할 수 있는 템플릿을 찾지 못했습니다.",
  "Multi-protein inputs use the original combined AF3 pipeline without shared feature reuse.": "다중 단백질 입력은 검색 특징을 공유하지 않는 AF3 통합 검색·추론을 사용합니다.",
};
const jobLabels: Record<string, string> = { prepared: "입력 준비", preparing: "입력 확인 중", queued: "실행 대기", running: "계산 중", completed: "계산 완료", blocked: "실행 조건 미충족", failed: "계산 실패", interrupted: "중단", cancelled: "취소" };

export function AF3StageEvidence({ entry, live, checkedAt }: { entry: PredictionEnvelope; live: boolean; checkedAt: string | null }) {
  const [, updateClock] = useState(0);
  const now = Date.now();
  const progress = predictionExecutionProgress(entry.job.result, entry.stage, entry.job.status, checkedAt, now);
  const active = live && progress.active;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => updateClock((tick) => tick + 1), 1000);
    return () => window.clearInterval(timer);
  }, [active, entry.job.id]);
  const features = entry.msa_features;
  const stage = entry.stage;
  const memoryWarning = gpuMemoryWarning(entry.job.result);
  const steps = recordedExecutionSteps(entry.job.result);
  const timeline = predictionExecutionTimeline(entry.job.result, stage, entry.job.status, entry.requested.msa_mode);
  const detail = recordedStageDetail(stage);
  const inferenceDevice = [...steps].reverse().find((step) => step.name === "inference")?.device;
  const stageLabel = stage?.name === "inference" && inferenceDevice ? `${inferenceDevice} · AF3 구조 추론` : stage ? phaseLabels[stage.name] || stage.label || "작업 진행 중" : entry.job.status === "queued" ? "실행 대기" : "AF3 작업 진행 중";
  const stale = progress.heartbeatState === "stale";
  const responseStale = active && progress.responseAge !== null && progress.responseAge >= 30;
  const showExecution = active || steps.length > 0 || entry.job.status === "completed";
  const processLabel = progress.heartbeatState === "recent" ? "실행 프로세스 확인됨" : stale ? "최근 실행 확인 없음" : progress.heartbeatState === "waiting" ? "실행 준비·대기" : "프로세스 확인 기록 대기";
  const description = stage ? phaseDescriptions[stage.name] : entry.job.status === "queued" ? phaseDescriptions.queued : "서버에서 AF3 작업을 처리하고 있습니다. 이 기록에는 세부 실행 단계가 아직 제공되지 않습니다.";
  return <>
    {showExecution && <div className="afc-stage" data-testid="studio-af3-stage" data-stage={stage?.name || entry.job.status} data-status-stale={stale} data-execution-status={entry.job.status}>
      <div className="afc-stage-heading" role="status">{active ? entry.job.status === "queued" ? <Clock3 size={17} /> : <Cpu size={17} /> : entry.job.status === "completed" ? <Check size={17} /> : <CircleAlert size={17} />}<strong>{active ? stageLabel : entry.job.status === "completed" ? "계산과 출력 처리 종료" : "실행 종료 기록"}</strong><span>{active ? processLabel : "저장된 실행 기록"}</span></div>
      {active && memoryWarning && <div className="afc-stage-runtime-warning" role="alert" data-testid="studio-af3-runtime-warning" data-warning-code="gpu_memory_pressure"><CircleAlert size={16} /><div><strong>GPU 메모리 부족 감지</strong><p>{memoryWarning.message}</p>{memoryWarning.observedAt && <small>경고 감지 {timestamp(memoryWarning.observedAt)}</small>}</div></div>}
      {active && <p>{description}</p>}
      {active && detail && <div className="afc-stage-detail" data-testid="studio-af3-stage-detail" data-detail={detail.name}><span>로그에서 확인한 단계</span><strong>{detail.label}</strong>{detail.activity && <p>{detail.activity}</p>}{detail.completedDatabases.length > 0 && <p data-testid="studio-af3-search-databases">서열 검색 완료가 확인된 DB {detail.completedDatabases.length}개: {detail.completedDatabases.join(", ")}</p>}<small>로그 관측 {timestamp(detail.observedAt)}</small></div>}
      <dl className="afc-stage-timing" aria-live="off">
        <div><dt><Clock3 size={13} />{active ? "계산 시작 후 전체 경과" : "전체 실행 소요 시간"}</dt><dd data-testid="studio-af3-total-elapsed">{formatElapsed(progress.totalSeconds)}</dd></div>
        {active ? <div><dt>현재 단계 시작 후 경과</dt><dd data-testid="studio-af3-stage-elapsed">{formatElapsed(progress.stageSeconds)}</dd></div> : progress.finishedAt && <div><dt>실행 종료 시각</dt><dd className="afc-stage-finished-at">{timestamp(progress.finishedAt)}</dd></div>}
      </dl>
      <ol className="afc-stage-timeline" data-testid="studio-af3-stage-timeline">{timeline.map((item) => <li key={item.name} data-step={item.name} data-state={item.state}><span className="afc-timeline-marker">{item.state === "completed" ? <Check size={13} /> : item.state === "active" ? <Cpu size={13} /> : item.state === "failed" ? <CircleAlert size={13} /> : <span />}</span><span className="afc-timeline-label">{item.label}<small>{item.note}</small></span><strong>{item.elapsedSeconds !== null ? formatElapsed(item.elapsedSeconds) : ""}</strong></li>)}</ol>
      {active && <div className="afc-execution-freshness" data-heartbeat-state={progress.heartbeatState}>
        <p data-testid="studio-af3-worker-checked"><strong>실행 프로세스 마지막 확인</strong><span>{progress.heartbeatAge === null ? "확인 기록 없음" : `${formatElapsed(progress.heartbeatAge)} 전`}{progress.heartbeatAt && <> · {timestamp(progress.heartbeatAt)}</>}</span></p>
        <p data-testid="studio-af3-last-checked"><strong>화면 데이터 최근 수신</strong><span>{progress.responseAge === null ? "응답 확인 중" : `${formatElapsed(progress.responseAge)} 전`}</span></p>
        <small>화면 데이터 수신은 서버 접속 확인입니다. 실행 프로세스 확인 시각과 계산 경과는 따로 표시합니다.</small>
      </div>}
      {active && stale && <p className="afc-stage-stale" data-testid="studio-af3-stage-stale">실행 프로세스의 새 확인 기록이 30초 이상 없습니다. 실패로 확정된 상태는 아니며, 마지막 단계와 로그를 확인하세요.</p>}
      {responseStale && <p className="afc-stage-stale">화면 데이터 수신이 지연되고 있습니다. 작업 새로고침으로 서버 연결을 다시 확인하세요.</p>}
      <small>{active ? "아래 ‘최근 실행 로그’를 펼치면 상세 기록을 볼 수 있습니다. 로그가 잠시 늘지 않아도 계산이 진행 중일 수 있습니다." : "단계별 시간은 저장된 실행 기록입니다. 계산 종료와 구조·약효 검증은 별개입니다."}</small>
    </div>}
    {features && <div className="afc-features" data-testid="studio-af3-features" data-feature-status={features.status}>
      <div className="afc-feature-heading"><Database size={16} /><strong>{featureLabels[features.status] || features.status}</strong>{features.status === "ready" && <span>{features.cache_hit === true ? "기존 검색 결과 재사용" : features.cache_hit === false ? "이 작업에서 검색" : "재사용 여부 미기록"}</span>}</div>
      {features.status === "ready" ? <>
        <dl className="afc-feature-counts">
          <div><dt>Unpaired MSA</dt><dd>{integer(features.unpaired_msa_sequences)}<small>서열</small></dd></div>
          <div><dt>Paired MSA</dt><dd>{integer(features.paired_msa_sequences)}<small>서열</small></dd></div>
          <div><dt>Unpaired 표적 외</dt><dd>{integer(features.non_query_sequences)}<small>서열</small></dd></div>
          <div><dt>템플릿</dt><dd>{integer(features.template_count)}<small>개</small></dd></div>
        </dl>
        <p>실제 검색 특징의 MSA 수는 표적 서열을 포함합니다. ‘Unpaired 표적 외’는 표적 1개를 제외한 수입니다. 0과 미기록을 구분합니다.</p>
      </> : <p>{featureDescriptions[features.status] || "검색 특징 상태를 확인하고 있습니다."}</p>}
      {features.warnings && features.warnings.length > 0 && <ul className="afc-feature-warnings">{features.warnings.map((warning, index) => <li key={index}>{featureWarningLabels[warning] || warning}</li>)}</ul>}
    </div>}
  </>;
}

function countCell(entry: PredictionEnvelope | null, field: "unpaired_msa_sequences" | "paired_msa_sequences" | "non_query_sequences" | "template_count") {
  if (!entry) return "결과 대기";
  if (entry.requested.msa_mode === "none") return "생략";
  return entry.msa_features?.status === "ready" ? integer(entry.msa_features[field]) : entry.msa_features?.status === "failed" ? "검색 실패" : entry.job.status === "completed" ? "미기록" : "검색 결과 대기";
}

function metricCell(entry: PredictionEnvelope | null, key: keyof PredictionMetrics) {
  const value = measuredMetric(entry, key);
  return value === null ? entry?.job.status === "completed" ? "검증값 없음" : "결과 대기" : value.toFixed(2);
}

function qualityCell(entry: PredictionEnvelope | null) {
  if (!entry || entry.job.status !== "completed") return "결과 대기";
  if (entry.output_validation?.status === "identity_failed") return "성분·표적 불일치";
  if (!hasVerifiedPrediction(entry)) return "검증되지 않은 출력";
  return entry.output_validation?.status === "geometry_warning" ? "기하학 경고 · 정확도 미평가" : "성분·표적 일치 · 정확도 미평가";
}

function Provenance({ entry, label }: { entry: PredictionEnvelope | null; label: string }) {
  const profile = entry?.requested.execution_profile;
  const features = entry?.msa_features;
  const databases = entry?.readiness.databases;
  if (!entry) return <div><h4>{label}</h4><p>아직 비교할 작업이 없습니다.</p></div>;
  const facts: [string, unknown][] = [
    ["작업 ID", entry.job.id], ["생성 시각", timestamp(entry.job.created)],
    ["표적 서열 SHA-256", entry.requested.target_sequence_sha256 || features?.protein_sequence_sha256],
    ["AF3 버전 / 코드 commit", `${entry.requested.af3_version || "미기록"} / ${entry.requested.af3_commit || "미기록"}`],
    ["가중치 파일 stat 지문", entry.requested.submission_context?.parameter_stat_fingerprint_sha256],
    ["시드", entry.requested.seeds.join(", ")], ["시드당 표본 수", profile?.num_diffusion_samples], ["재순환 횟수", profile?.num_recycles],
    ["템플릿 최대 날짜", entry.requested.msa_mode === "none" ? "미사용" : features?.max_template_date || profile?.max_template_date],
    ["검색 DB 지문", entry.requested.msa_mode === "none" ? "미사용" : features?.database_fingerprint],
    ["검색 DB 설치 시각", entry.requested.msa_mode === "none" ? "미사용" : databases?.installed_at && timestamp(databases.installed_at)],
    ["검색 DB 기준 AF3 릴리스", entry.requested.msa_mode === "none" ? "미사용" : databases?.source_tag],
    ["검색 DB 설치 기록 SHA-256", entry.requested.msa_mode === "none" ? "미사용" : databases?.manifest_sha256],
    ["검색 특징 SHA-256", features?.feature_sha256], ["검색 특징 원본 작업", features?.source_job_id], ["검색 캐시 키", features?.cache_key],
    ["실제 추론 입력 SHA-256", entry.job.result?.inference_input_sha256],
    ["출력 CIF SHA-256", entry.output_validation?.sha256],
  ];
  return <div><h4>{label}</h4><dl>{facts.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{text(value)}</dd></div>)}</dl>{entry.requested.msa_mode === "search" && !!databases?.components?.length && <details><summary>검색 DB 구성·내용 SHA-256</summary><dl>{databases.components.map((component) => <div key={component.relative_path}><dt>{component.relative_path} · {integer(component.record_count)}개 기록</dt><dd>{component.sha256}</dd></div>)}</dl></details>}</div>;
}

export default function AF3EvidencePanel({ entries, active, onSelect }: { entries: PredictionEnvelope[]; active: PredictionEnvelope | null; onSelect: (id: string) => void }) {
  const none = comparisonEntry(entries, active, "none"), search = comparisonEntry(entries, active, "search");
  if (!entries.length) return null;
  const conditions = conditionDifferences(none, search);
  const rows: { key: string; label: string; a: string; b: string }[] = [
    { key: "status", label: "작업 상태", a: none ? jobLabels[none.job.status] || none.job.status : "기록 없음", b: search ? jobLabels[search.job.status] || search.job.status : "기록 없음" },
    { key: "unpaired", label: "Unpaired MSA 서열", a: countCell(none, "unpaired_msa_sequences"), b: countCell(search, "unpaired_msa_sequences") },
    { key: "paired", label: "Paired MSA 서열", a: countCell(none, "paired_msa_sequences"), b: countCell(search, "paired_msa_sequences") },
    { key: "non-query", label: "Unpaired 표적 외", a: countCell(none, "non_query_sequences"), b: countCell(search, "non_query_sequences") },
    { key: "templates", label: "템플릿 수", a: countCell(none, "template_count"), b: countCell(search, "template_count") },
    { key: "ptm", label: "pTM · 전체 구조", a: metricCell(none, "ptm"), b: metricCell(search, "ptm") },
    { key: "iptm", label: "ipTM · 사슬 간 배치", a: metricCell(none, "iptm"), b: metricCell(search, "iptm") },
    { key: "ranking", label: "AF3 순위 점수", a: metricCell(none, "ranking_score"), b: metricCell(search, "ranking_score") },
    { key: "quality", label: "구조 확인 범위", a: qualityCell(none), b: qualityCell(search) },
  ];
  const deltas = (["ptm", "iptm"] as const).map((key) => ({ key, value: metricDifference(none, search, key) })).filter((item) => item.value !== null);
  return <section className="afc-comparison" data-testid="studio-af3-comparison" aria-label="선택 성분의 MSA 조건별 실제 결과 비교">
    <header><h3>3. MSA 조건별 실제 결과 비교</h3><p>현재 선택 작업과 다른 모드의 최근 검증된 완료 작업을 표시합니다. 완료 작업이 없으면 최근 기록의 상태를 표시합니다. 내부 신뢰도는 구조 정확도·약효·안전성 검증 결과가 아닙니다.</p></header>
    <table><colgroup><col className="afc-comparison-label" /><col /><col /></colgroup><thead><tr><th scope="col">측정·확인 항목</th><th scope="col">MSA·템플릿 없음<small>탐색 기준</small></th><th scope="col">MSA·템플릿 검색<small>표준 입력</small></th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.key} data-testid={`studio-af3-compare-${row.key}`}><th scope="row">{row.label}</th><td>{row.a}</td><td>{row.b}</td></tr>)}
      <tr className="afc-comparison-links"><th scope="row">비교 기록</th>{[none, search].map((entry, index) => <td key={index} data-job-id={entry?.job.id || ""}>{entry ? <><code>{entry.job.id}</code><button className="afc-outline" onClick={() => onSelect(entry.job.id)} disabled={entry.job.id === active?.job.id} data-testid={index ? "studio-af3-compare-search" : "studio-af3-compare-none"}>{entry.job.id === active?.job.id ? "현재 선택" : hasVerifiedPrediction(entry) ? "이 구조 보기" : "이 기록 보기"}<ArrowRight size={14} /></button></> : "기록 없음"}</td>)}</tr>
    </tbody></table>
    {deltas.length > 0 && <p className="afc-delta" data-testid="studio-af3-confidence-delta">표준 입력 − 탐색 기준: {deltas.map(({ key, value }) => `${key === "ptm" ? "pTM" : "ipTM"} ${value! > 0 ? "+" : ""}${value!.toFixed(2)}`).join(" · ")}. 실제 출력값의 차이이며 정확도 향상을 입증하지 않습니다.</p>}
    {conditions.different.length > 0 && <p className="afc-caution" data-testid="studio-af3-condition-mismatch">함께 달라진 계산 조건: {conditions.different.join(", ")}. 수치 차이를 MSA만의 효과로 해석할 수 없습니다.</p>}
    {conditions.unknown.length > 0 && <p className="afc-muted">조건 비교에 필요한 기록이 없습니다: {conditions.unknown.join(", ")}.</p>}
    <details className="afc-comparison-provenance" data-testid="studio-af3-comparison-provenance"><summary>시드·표본·서열·검색 DB 및 파일 근거</summary><p>가중치 파일 stat 지문은 크기·수정 시각 등 파일 상태의 지문이며, 가중치 내용의 SHA-256이나 학습 출처 인증이 아닙니다. 검색 DB와 템플릿 날짜는 무MSA 기준에서 사용하지 않습니다.</p><div className="afc-provenance-columns"><Provenance entry={none} label="MSA·템플릿 없음" /><Provenance entry={search} label="MSA·템플릿 검색" /></div></details>
  </section>;
}
