import { tr, localeCode, msg } from "../lib/i18n";
import { useEffect, useMemo, useState } from "react";
import { Activity, CircleAlert, Database, ExternalLink, FileCheck2, LoaderCircle, RefreshCw, ScanLine, ShieldQuestion } from "lucide-react";
import { api } from "../lib/api";
import { predictionRequest } from "../lib/predictionPolling";
import { af3ClashLabel, currentDiagnosticScene, diagnosticLink, diagnosticMatchesScene, diagnosticNumber, diagnosticRecord, diagnosticText, sceneConfidence } from "../lib/alphaFoldDiagnostics";
import type { AtomConfidenceSummary } from "../lib/alphaFoldDiagnostics";
import type { Compound } from "../types/app";
import type { MolecularScene, StructureMode } from "../types/molecular";
import type { PredictionEnvelope } from "../types/prediction";
import "./alphafold-diagnostics.css";

type DiagnosticResponse = Record<string, unknown> & {
  job_id?: string;
  structure_sha256?: string;
  status?: string;
  requested?: { canonical_smiles?: string; target_accession?: string };
};

export type AlphaFoldDiagnosticsProps = {
  compound: Compound | null;
  targetAccession: string;
  structureMode: StructureMode;
  scene: MolecularScene | null;
  loading?: boolean;
  refreshKey?: number;
  health?: unknown;
  issue?: string | null;
};

const SOURCE_LABELS: Record<StructureMode, string> = {
  alphafold3_prediction: "AlphaFold 3 예측 구조",
  experimental_pdb: "PDB 실험 구조",
  rdkit_conformer: "RDKit 단독 분자 좌표",
};
const officialConfidence = "https://github.com/google-deepmind/alphafold3/blob/main/docs/output.md#confidence-metrics";
const number = (value: unknown, digits = 2) => {
  const result = diagnosticNumber(value);
  return result === null ? "—" : result.toLocaleString(localeCode(), { maximumFractionDigits: digits, minimumFractionDigits: digits });
};
const count = (value: unknown) => {
  const result = diagnosticNumber(value, 0);
  return result === null || !Number.isInteger(result) ? "—" : result.toLocaleString(localeCode());
};
const text = (value: unknown) => diagnosticText(value) || "—";
const array = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const strings = (value: unknown) => array(value).filter((item): item is string => typeof item === "string");

function ValueCard({ label, value, detail, id }: { label: string; value: string; detail: string; id?: string }) {
  return <div className="afd-value" data-testid={id}><dt>{tr(label)}</dt><dd>{tr(value)}</dd><p>{tr(detail)}</p></div>;
}

function ConfidenceCard({ label, summary, reason, id }: { label: string; summary: AtomConfidenceSummary; reason: string; id: string }) {
  return <ValueCard label={label} value={number(summary.mean)} id={id} detail={summary.mean === null ? reason : `원자 ${count(summary.count)} / ${count(summary.total)}개 · 최소 ${number(summary.min)} / 최대 ${number(summary.max)}`} />;
}

function SourceLink({ href, children }: { href: unknown; children: React.ReactNode }) {
  const url = diagnosticLink(href);
  return url ? <a href={url} target="_blank" rel="noreferrer">{tr(children)}<ExternalLink size={13} /></a> : null;
}

function ParameterEvidence({ parameters, label }: { parameters: Record<string, unknown>; label: string }) {
  const status = diagnosticText(parameters.status);
  const labels: Record<string, string> = {
    test_parameters: "시험용 가중치 판정 · 예측 사용 제외",
    unverified_parameters: "제한된 파일 선두 검사 · 학습 출처 미인증",
    missing_parameters: "가중치 파일 없음",
    invalid_parameters: "파라미터 파일 검사 실패",
    ambiguous_parameters: "사용할 파라미터 파일을 특정하지 못함",
    inspection_unavailable: "파라미터 검사 불가",
    inspection_limit_reached: "파라미터 검사 범위 제한",
  };
  return <div className="afd-parameter"><h4>{tr(label)}</h4><p>{tr(status ? labels[status] || status : "— · 파라미터 검사 기록 없음")}</p>
    <dl className="afd-key-values">
      <div><dt>{tr("검사한 파라미터 기록")}</dt><dd>{tr(count(parameters.records_checked))}</dd></div>
      <div><dt>{tr("디코딩해 확인한 용량")}</dt><dd>{tr(count(parameters.decoded_bytes_checked))} bytes</dd></div>
      <div><dt>{tr("이 검사에서 전체 컨테이너 검증")}</dt><dd>{tr(parameters.full_container_validated === true ? "완료 기록 있음" : parameters.full_container_validated === false ? "수행하지 않음" : "— · 미기록")}</dd></div>
      <div><dt>{tr("학습 출처 인증")}</dt><dd>{tr(parameters.trained_parameters_authenticated === true ? "인증 기록 있음 · 원문 근거 확인 필요" : parameters.trained_parameters_authenticated === false ? "이 검사로 인증하지 않음" : "— · 미기록")}</dd></div>
      <div><dt>{tr("파일 stat 지문")}</dt><dd><code>{tr(text(parameters.stat_fingerprint_sha256))}</code></dd></div>
    </dl>
  </div>;
}

export default function AlphaFoldDiagnostics({ compound, targetAccession, structureMode, scene, loading = false, refreshKey = 0, health, issue }: AlphaFoldDiagnosticsProps) {
  const [loaded, setLoaded] = useState<{ key: string; report: DiagnosticResponse | null; entry: PredictionEnvelope | null; error: string | null } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const currentScene = currentDiagnosticScene(scene, structureMode, targetAccession, loading);
  const metadata = currentScene?.metadata || {};
  const jobId = diagnosticText(metadata.job_id);
  const artifact = diagnosticText(metadata.artifact);
  const sha = diagnosticText(metadata.sha256);
  const selection = diagnosticRecord(metadata.selection);
  const isPrediction = structureMode === "alphafold3_prediction";
  const isExperimental = structureMode === "experimental_pdb";
  const key = [compound?.smiles || "", targetAccession, structureMode, jobId || "", sha || "", artifact || ""].join("\u001f");
  const canFetch = !!currentScene && isPrediction && !!jobId && !!sha;

  useEffect(() => {
    if (!canFetch || !jobId) return;
    const controller = new AbortController();
    setLoaded(null);
    const params = new URLSearchParams({ target_accession: targetAccession });
    if (compound?.smiles) params.set("smiles", compound.smiles);
    if (artifact) params.set("file", artifact);
    const base = `/molecular/predictions/${encodeURIComponent(jobId)}`;
    void Promise.allSettled([
      predictionRequest((signal) => api<DiagnosticResponse>(`${base}/diagnostics?${params}`, undefined, { signal }), { signal: controller.signal }),
      predictionRequest((signal) => api<PredictionEnvelope>(base, undefined, { signal }), { signal: controller.signal }),
    ]).then(([diagnostics, prediction]) => {
      if (controller.signal.aborted) return;
      const report = diagnostics.status === "fulfilled" ? diagnostics.value : null;
      const entry = prediction.status === "fulfilled" ? prediction.value : null;
      setLoaded({ key, report, entry, error: diagnostics.status === "rejected" ? (diagnostics.reason as Error).message : prediction.status === "rejected" ? `계산 조건 기록을 불러오지 못했습니다. ${(prediction.reason as Error).message}` : null });
    });
    return () => controller.abort();
  }, [canFetch, jobId, sha, artifact, compound?.smiles, targetAccession, key, refresh, refreshKey]);

  const active = canFetch && loaded?.key === key ? loaded : null;
  const matches = !!active?.report && diagnosticMatchesScene(active.report, currentScene, targetAccession);
  const report = matches ? active!.report! : null;
  const entry = matches && active?.entry?.job.id === jobId && active.entry.requested.target_accession === targetAccession
    && active.entry.requested.canonical_smiles === selection.canonical_smiles ? active.entry : null;
  const fetching = canFetch && !active;
  const checked = !!currentScene && !!selection.canonical_smiles && selection.target_accession === targetAccession;
  const verified = matches && report?.status === "available" && report?.execution_verified === true && report?.identity_verified === true
    && metadata.execution_verified === true;
  const summary = diagnosticRecord(verified ? report?.summary_metrics : null);
  const validation = diagnosticRecord(metadata.output_validation);
  const rawGeometry = verified || (isExperimental && checked) ? currentScene?.geometry || diagnosticRecord(validation.geometry) : {};
  const confidence = useMemo(() => sceneConfidence(verified ? currentScene : null), [currentScene, verified]);
  const features = entry?.msa_features;
  const pae = diagnosticRecord(verified ? report?.pae : null);
  const pairs = array(pae.chain_pairs).map(diagnosticRecord);
  const targetChains = strings(selection.target_chains), ligandChains = strings(selection.ligand_chains);
  const relevantPairs = pairs.filter(pair => targetChains.includes(String(pair.frame_chain)) && ligandChains.includes(String(pair.target_chain))
    || ligandChains.includes(String(pair.frame_chain)) && targetChains.includes(String(pair.target_chain)));
  const request = entry?.requested;
  const featureRaw = diagnosticRecord(features);
  const templates = array(featureRaw.templates).map(diagnosticRecord);
  const runtime = diagnosticRecord(diagnosticRecord(health).alphafold);
  const runtimeProvenance = diagnosticRecord(runtime.provenance);
  const runtimeParameters = diagnosticRecord(runtimeProvenance.parameters);
  const jobParameters = diagnosticRecord(entry?.readiness.parameters);
  const construct = diagnosticRecord(metadata.construct || selection.construct);
  const unavailableReason = loading ? "선택한 구조를 불러오는 중입니다. 이전 구조의 진단을 표시하지 않습니다."
    : issue || (!currentScene ? "현재 선택한 성분·표적의 구조가 아직 없습니다." : "");
  const metricReason = !isPrediction ? isExperimental ? "실험 구조에는 AF3 신뢰도가 없습니다. B factor와 구분합니다." : "단독 분자 좌표에는 단백질 복합체의 AF3 신뢰도가 없습니다."
    : fetching ? "현재 좌표의 실행·선택 일치와 신뢰도 파일을 확인하는 중입니다."
      : !verified ? "선택한 작업·좌표와 일치하는 검증된 신뢰도 값을 확인하지 못했습니다." : "해당 출력에 수치가 기록되지 않았습니다.";
  const warnings = [...new Set([...strings(report?.warnings), ...strings(report?.notes), ...(currentScene?.warnings || [])])];

  return <section className="afd-board" data-testid="alphafold-diagnostics" data-source={structureMode} data-job-id={jobId || ""} data-structure-sha={sha || ""} data-diagnostic-status={verified ? "verified_identity_quality_unassessed" : fetching || loading ? "loading" : currentScene ? "limited" : "unavailable"} aria-busy={loading || fetching}>
    <header className="afd-heading"><div><span className="afd-eyebrow">{tr("현재 구조의 근거 확인")}</span><h2>{tr("구조·근거 진단")}</h2><p>{tr(compound?.name_ko || compound?.name || currentScene?.label || "성분 선택 대기")} <span>× {tr(targetAccession || "표적 선택 대기")}</span></p></div><div className="afd-heading-actions"><span className="afd-source"><ScanLine size={15} />{tr(SOURCE_LABELS[structureMode])}</span>{tr(canFetch && <button type="button" className="afd-icon" aria-label={tr("현재 구조 진단 새로고침")} data-testid="alphafold-diagnostics-refresh" onClick={() => { setLoaded(null); setRefresh(value => value + 1); }} disabled={fetching}><RefreshCw size={17} /></button>)}</div></header>

    {tr(unavailableReason && <div className="afd-notice" role="status" data-testid="alphafold-diagnostics-unavailable">{tr(loading ? <LoaderCircle size={19} className="spin" /> : <CircleAlert size={19} />)}<p>{tr(unavailableReason)}</p></div>)}
    {tr(fetching && <p className="afd-loading" role="status"><LoaderCircle size={17} className="spin" />{tr("현재 CIF와 진단 파일을 대조하고 있습니다.")}</p>)}
    {tr(active?.error && <p className="afd-warning" role="status">{tr("진단 조회를 완료하지 못했습니다. ")}{tr(active.error)}</p>)}
    {tr(active?.report?.status === "unavailable" ? <p className="afd-warning" data-testid="alphafold-diagnostics-unavailable-report">{tr(text(active.report.reason) === "—" ? "현재 좌표에 대한 진단을 제공할 수 없습니다." : text(active.report.reason))}</p> : active?.report && !matches && <p className="afd-warning" data-testid="alphafold-diagnostics-mismatch">{tr("현재 물질·표적·작업·좌표 파일과 진단 기록이 일치하지 않아 수치를 표시하지 않습니다.")}</p>)}

    <div className="afd-scope" data-testid="alphafold-diagnostics-scope"><ShieldQuestion size={19} /><p>{tr("실행 확인, 성분·표적 일치, 내부 신뢰도, 구조 검사를 각각 확인합니다. 이 보드는 결합 친화도·약효·안전성이나 환자의 질환을 판정하지 않습니다.")}</p></div>
    <dl className="afd-facts">
      <ValueCard label="구조 출처" value={currentScene ? SOURCE_LABELS[currentScene.source] : "—"} detail={currentScene ? isPrediction ? metadata.execution_verified === true ? "기록된 AF3 실행의 출력 좌표" : "실행 출처가 검증되지 않은 가져온 좌표" : isExperimental ? `PDB ${text(metadata.pdb_id || metadata.entry_id)} · 실험 시료의 구조` : "RDKit이 생성한 단독 분자 배치 · 결합 포즈 아님" : "선택 구조가 있어야 확인할 수 있습니다."} id="alphafold-diagnostic-source" />
      <ValueCard label="성분·표적 일치" value={verified || (isExperimental && checked) ? "선택과 일치" : isPrediction && fetching ? "확인 중" : "—"} detail={verified ? "실제 출력의 선택 리간드 원자·결합과 표적 서열 확인" : isExperimental && checked ? "등록된 실험 구조체 서열과 선택 리간드 확인" : structureMode === "rdkit_conformer" ? "단백질 표적을 포함하지 않는 단독 분자 좌표입니다." : "현재 선택과 연결되는 출력 검증 근거가 필요합니다."} id="alphafold-diagnostic-identity" />
      <ValueCard label="구조 정확도·약효 평가" value="미평가" detail="성분 일치나 높은 모델 신뢰도는 실험 검증을 대신하지 않습니다." id="alphafold-diagnostic-quality" />
    </dl>

    <div className="afd-section-heading"><Activity size={19} /><div><h3>{tr("AF3 내부 구조 신뢰도")}</h3><p>{tr("현재 뷰어에 열린 좌표 파일 한 개의 값입니다. 전체 실행의 표본 평균이나 실험 측정값이 아닙니다.")}</p></div></div>
    <dl className="afd-metrics">
      <ValueCard label="pTM · 전체 구조" value={number(diagnosticNumber(summary.ptm, 0, 1))} detail={diagnosticNumber(summary.ptm, 0, 1) === null ? metricReason : "0–1 · 예측된 전체 구조 배치의 모델 신뢰도"} id="alphafold-diagnostic-ptm" />
      <ValueCard label="ipTM · 사슬 간 배치" value={number(diagnosticNumber(summary.iptm, 0, 1))} detail={diagnosticNumber(summary.iptm, 0, 1) === null ? metricReason : "0–1 · 복합체의 사슬 간 상대 배치 신뢰도"} id="alphafold-diagnostic-iptm" />
      <ConfidenceCard label="표적 단백질 원자 pLDDT 평균" summary={confidence.protein} reason={metricReason} id="alphafold-diagnostic-protein-plddt" />
      <ConfidenceCard label="선택 리간드 원자 pLDDT 평균" summary={confidence.ligand} reason={metricReason} id="alphafold-diagnostic-ligand-plddt" />
    </dl>
    <p className="afd-explanation">{tr("pLDDT는 0–100의 원자별 모델 신뢰도입니다. 리간드 pLDDT는 리간드 원자와 고분자 사이의 거리 정확도를 추정하며, 리간드 내부 결합 구조의 검증 점수가 아닙니다. PAE는 값이 클수록 예상 배치 오차가 큽니다. ")}<SourceLink href={officialConfidence}>{tr("공식 AF3 해석 안내")}</SourceLink></p>

    <div className="afd-columns">
      <section className="afd-section" data-testid="alphafold-diagnostic-pae"><h3>{tr("PAE · 방향별 예상 배치 오차")}</h3>{tr(relevantPairs.length ? <><div className="afd-table-scroll" tabIndex={0} role="region" aria-label={tr("표적과 리간드의 방향별 PAE 표")}><table><thead><tr><th scope="col">{tr("정렬 기준 → 평가 사슬")}</th><th scope="col">{tr("평균 / 최소 / 최대 Å")}</th><th scope="col">{tr("토큰 쌍")}</th></tr></thead><tbody>{tr(relevantPairs.map((pair, index) => { const pairId = `${text(pair.frame_chain)}-${text(pair.target_chain)}`; return <tr key={index} data-testid={`alphafold-diagnostic-pae-row-${pairId}`}><th scope="row">{tr(text(pair.frame_chain))} → {tr(text(pair.target_chain))}</th><td><span data-testid={`pae-mean-${pairId}`}>{tr(number(pair.mean))}</span> / <span data-testid={`pae-min-${pairId}`}>{tr(number(pair.min))}</span> / <span data-testid={`pae-max-${pairId}`}>{tr(number(pair.max))}</span></td><td data-testid={`pae-count-${pairId}`}>{tr(count(pair.count))}</td></tr>; }))}</tbody></table></div><p>{tr("행의 첫 사슬을 정렬 기준으로 삼았을 때 둘째 사슬의 상대 위치에 대한 모델 오차 추정치입니다. 두 방향을 합치거나 최소값을 전체 오차로 표시하지 않습니다.")}</p><p>{tr("현재 PAE 파일의 SHA와 원자·사슬 매핑을 대조합니다. 기존 저장 시점의 PAE 해시가 없어 파일의 과거 원본 일치나 독립된 출처 인증까지 확인한 것은 아닙니다.")}</p></> : <p className="afd-missing">— · {tr(verified ? text(pae.reason) !== "—" ? text(pae.reason) : "선택 사슬의 PAE 배열과 토큰 매핑이 제공되지 않았습니다." : metricReason)}</p>)}</section>
      <section className="afd-section" data-testid="alphafold-diagnostic-geometry"><h3>{tr("좌표·충돌 검사 범위")}</h3><dl className="afd-key-values">
        <div><dt>{tr("긴 공유결합 / 짧은 공유결합")}</dt><dd>{tr(count(rawGeometry.long_bond_count))} / {tr(count(rawGeometry.short_bond_count))}</dd></div>
        <div><dt>AF3 has_clash</dt><dd>{tr(af3ClashLabel(summary.has_clash))}</dd></div>
        <div><dt>{tr("전체 입체화학·키랄성 검증")}</dt><dd>{tr("— · 이 진단에서 수행하지 않음")}</dd></div>
      </dl><p>{tr("공유결합 길이 표시는 등록된 결합에서 0.65 Å 미만·2.4 Å 초과를 확인한 수입니다. has_clash=false도 모든 충돌이 없다는 뜻은 아닙니다. 전체 구조 품질 통과 판정은 제공하지 않습니다.")}</p></section>
    </div>

    <section className="afd-section afd-feature-section" data-testid="alphafold-diagnostic-msa"><div className="afd-section-heading"><Database size={19} /><div><h3>{tr("해당 작업의 MSA·템플릿 근거")}</h3><p>{tr(request?.msa_mode === "none" ? "이 작업은 MSA와 템플릿을 생략한 탐색 모드입니다." : features?.status === "ready" ? features.cache_hit === true ? "검증된 검색 특징을 재사용하고 선택 성분의 추론을 수행했습니다." : "이 작업에서 검색한 특징을 사용했습니다." : "현재 구조와 연결된 검색 기록이 있어야 실제 수를 표시합니다.")}</p></div></div>
      <dl className="afd-metrics afd-counts">{tr([["Unpaired MSA", "unpaired_msa_sequences"], ["Paired MSA", "paired_msa_sequences"], ["Unpaired 표적 외", "non_query_sequences"], ["구조 템플릿", "template_count"]].map(([label, field]) => <ValueCard key={field} label={label} value={request?.msa_mode === "none" ? "생략" : features?.status === "ready" ? count(featureRaw[field]) : "—"} detail={request?.msa_mode === "none" ? "검색을 수행하지 않은 조건" : features?.status === "ready" ? field === "template_count" ? "사용한 구조 템플릿 수" : field === "non_query_sequences" ? "입력 표적 1개를 제외한 서열 수" : "표적 서열을 포함한 기록 수" : "검색 결과 미연결·미기록"} />))}</dl>
      {tr(!!templates.length && <p className="afd-template-list">{tr("사용 템플릿: ")}{tr(templates.map((template, index) => <span key={index}>{tr(index > 0 && " · ")}{tr(/^[0-9A-Za-z]{4}$/.test(text(template.entry_id)) ? <SourceLink href={`https://www.rcsb.org/structure/${text(template.entry_id)}`}>{tr(text(template.entry_id))}</SourceLink> : text(template.entry_id))}</span>))}</p>)}
      <p className="afd-explanation">{tr("서열 수는 독립된 진화 정보량이나 정확도 보장 수치가 아닙니다. 사용한 템플릿과의 구조 일치는 독립된 검증으로 간주할 수 없습니다.")}</p>
    </section>

    <div className="afd-columns">
      <section className="afd-section" data-testid="alphafold-diagnostic-experiment"><div className="afd-section-heading"><FileCheck2 size={18} /><h3>{tr("현재 구조의 실험 근거")}</h3></div>{tr(isExperimental && currentScene ? <><p><SourceLink href={metadata.source_url}>PDB {tr(text(metadata.pdb_id || metadata.entry_id))} {tr(" 원문")}</SourceLink></p><dl className="afd-key-values"><div><dt>{tr("실험 시료 서열 길이")}</dt><dd>{tr(count(construct.sample_sequence_length))} aa</dd></div><div><dt>{tr("완전한 기준 서열 여부")}</dt><dd>{tr(construct.full_length_canonical_sequence === true ? "전체 기준 서열" : construct.full_length_canonical_sequence === false ? "절단·변이 등을 확인할 실험 구조체" : "— · 미기록")}</dd></div><div><dt>{tr("등록된 변이 표기")}</dt><dd>{tr(construct.reported_mutation === null ? "등록 표기 없음" : text(construct.reported_mutation))}</dd></div></dl><p>{tr("해당 PDB 실험 시료와 리간드의 구조 근거입니다. 현재 후보의 약효·안전성이나 다른 분자의 결합을 입증하지 않습니다.")}</p></> : <p>{tr("— · 현재 ")}{tr(isPrediction ? "예측 좌표" : "단독 분자 좌표")} {tr(" 자체는 실험 구조가 아닙니다. 검색 템플릿의 존재와 선택 리간드 복합체의 실험 검증을 구분합니다.")}</p>)}</section>
      <section className="afd-section" data-testid="alphafold-diagnostic-runtime"><h3>{tr("현재 실행 환경")}</h3><dl className="afd-key-values"><div><dt>{tr("AF3 버전")}</dt><dd>{tr(text(runtime.version))}</dd></div><div><dt>{tr("실행 사전 조건")}</dt><dd>{tr(runtime.runnable === true ? "확인됨 · 결과 정확도 보장 아님" : runtime.runnable === false ? "실행 조건 미충족" : "— · 상태 미조회")}</dd></div><div><dt>{tr("현재 DB 준비 상태")}</dt><dd>{tr(diagnosticRecord(runtimeProvenance.databases).status === "ready" ? "설치 검증 기록 확인됨" : "— · 준비 기록 확인 필요")}</dd></div></dl><p>{tr("이 영역은 현재 서버 상태입니다. 아래 해당 작업 시점의 기록과 구분합니다.")}</p></section>
    </div>

    <details className="afd-details" data-testid="alphafold-diagnostic-provenance"><summary>{tr("입력·작업·좌표·검색 파일의 출처 확인")}</summary><dl className="afd-key-values">
      <div><dt>{tr("선택 물질 canonical SMILES")}</dt><dd><code>{tr(text(selection.canonical_smiles || metadata.smiles))}</code></dd></div>
      <div><dt>{tr("표적 / 리간드 사슬")}</dt><dd>{tr(targetChains.join(", ") || "—")} / {tr(ligandChains.join(", ") || "—")}</dd></div>
      <div><dt>{tr("작업 ID / 좌표 파일")}</dt><dd><code>{tr(jobId || "—")}<br />{tr(artifact || "—")}</code></dd></div>
      <div><dt>{tr("현재 CIF SHA-256")}</dt><dd><code>{tr(sha || "—")}</code></dd></div>
      <div><dt>{tr("표시한 좌표의 표본")}</dt><dd>{tr(diagnosticRecord(report?.sample).is_top_ranked_copy === true ? "최고 순위 표본의 복사본" : report?.sample ? msg("시드 {0} · 표본 {1}", count(diagnosticRecord(report.sample).seed), count(diagnosticRecord(report.sample).sample)) : "— · 표본 기록 미연결")}</dd></div>
      <div><dt>{tr("PAE 파일 / 현재 SHA-256")}</dt><dd><code>{tr(text(pae.artifact))}<br />{tr(text(pae.sha256))}</code></dd></div>
      <div><dt>{tr("표적 서열 SHA-256")}</dt><dd><code>{tr(text(request?.target_sequence_sha256))}</code></dd></div>
      <div><dt>{tr("AF3 코드 commit")}</dt><dd><code>{tr(text(request?.af3_commit))}</code></dd></div>
      <div><dt>{tr("시드 / 시드당 표본 / 재순환")}</dt><dd>{tr(request?.seeds.join(", ") || "—")} / {tr(count(request?.execution_profile?.num_diffusion_samples))} / {tr(count(request?.execution_profile?.num_recycles))}</dd></div>
      <div><dt>{tr("템플릿 최대 날짜")}</dt><dd>{tr(request?.msa_mode === "none" ? "미사용" : text(features?.max_template_date || request?.execution_profile?.max_template_date))}</dd></div>
      <div><dt>{tr("검색 특징 SHA-256")}</dt><dd><code>{tr(text(features?.feature_sha256))}</code></dd></div>
      <div><dt>{tr("검색 DB 지문")}</dt><dd><code>{tr(text(features?.database_fingerprint))}</code></dd></div>
    </dl></details>
    <details className="afd-details" data-testid="alphafold-diagnostic-parameters"><summary>{tr("가중치 검증 범위 · 현재 환경과 해당 작업")}</summary><p>{tr("파일 선두의 식별자 검사, 전체 파일 내용 검증, 학습 출처 인증은 서로 다릅니다. stat 지문은 파일 상태의 지문이며 전체 내용 SHA-256이 아닙니다.")}</p><div className="afd-columns"><ParameterEvidence parameters={jobParameters} label="해당 작업의 기록" /><ParameterEvidence parameters={runtimeParameters} label="현재 실행 환경 점검" /></div></details>
    {tr(!!warnings.length && <details className="afd-details"><summary>{tr("실행·구조 원문 경고 ")}{tr(warnings.length)}{tr("개")}</summary><ul>{tr(warnings.map((warning, index) => <li key={index}>{tr(warning)}</li>))}</ul></details>)}
  </section>;
}

