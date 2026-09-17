import { tr, msg, localeCode } from "../lib/i18n";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDownToLine, ChevronLeft, ChevronRight, CircleAlert, Cpu, LoaderCircle, Play, RefreshCw } from "lucide-react";
import { api, download } from "../lib/api";
import { globalKernelCollapsed, pairObservation, quantumMethod, quantumNumber as number, quantumOrigin, readQuantumSelection, saveQuantumSelection, validKernel } from "../lib/quantumEvidence";
import type { Analysis, Compound, Job } from "../types/app";
import type { QuantumPlan, QuantumRequest, QuantumResult } from "../types/quantum";
import "./quantum-panel.css";
import QuantumJourney from "./QuantumJourney";

const statuses: Record<string, string> = { completed: "계산 완료", submitted: "IBM 제출됨", submitting: "제출 처리 중", running: "진행 중", queued: "대기 중", ready: "계획 확인 완료", failed: "실패", partial_submission: "일부 작업만 제출됨", submission_failed: "제출 실패", credentials_required: "IBM 계정 연결 필요", off: "양자 단계 생략" };
const activeStatuses = new Set(["submitted", "submitting", "running", "queued"]);
const interval = (value?: number[]) => value?.length === 2 ? `[${number(value[0])}, ${number(value[1])}]` : "미기록";
const explanations: Record<string, string> = {
  "Projected kernel is descriptor similarity, not affinity, efficacy, safety, or demonstrated quantum advantage.": "이 커널은 입력 기술자의 유사도이며 친화도·약효·안전성·양자 우위의 증거가 아닙니다.",
  "Independent blocks are classically simulable; extra repeated descriptor qubits add no input information.": "독립 블록은 고전 계산으로도 시뮬레이션할 수 있습니다. 같은 기술자를 더 많은 큐빗에 반복해도 입력 정보가 늘어나지 않습니다.",
  "Raw XYZ projections are not readout-mitigated; finite-shot and hardware errors can shrink or inflate similarities.": "X·Y·Z 원시 측정에는 readout 보정을 적용하지 않았습니다. 유한 shots와 장비 오류로 유사도가 작아지거나 커질 수 있습니다.",
  "Diagonal 1 follows the RBF definition. The separately measured duplicate is the repeatability control.": "대각선 1은 RBF 정의값입니다. 재현성은 별도 반복 측정으로 확인합니다.",
  "whole_bitstring_empirical_percentile_bootstrap": "실제 전체 bitstring 재표집에 의한 백분위 bootstrap",
  "64-replicate empirical whole-bitstring shot bootstrap only; no calibration uncertainty, between-job drift, or systematic-bias coverage. Unobserved outcomes are absent; all-identical shots can yield a degenerate interval and do not establish zero uncertainty.": "실제 bitstring을 64회 재표집한 shot 불확실성입니다. 보정 불확실성·작업 간 드리프트·체계적 편향은 포함하지 않습니다. 관측되지 않은 결과는 재표집할 수 없으므로 구간 폭이 0이어도 불확실성이 없다는 뜻은 아닙니다.",
};
const explain = (value?: string) => value ? explanations[value] || value : "미기록";
const percentage = (value?: number) => typeof value === "number" && Number.isFinite(value) ? ` (${(value * 100).toFixed(2)}%)` : "";

export function QuantumMatrix({ matrix, labels, caption, onSelect, selected, definitionDiagonal = false }: {
  matrix: number[][]; labels: string[]; caption: string; onSelect?: (a: number, b: number) => void;
  selected?: [number, number]; definitionDiagonal?: boolean;
}) {
  return <div className="qp-table-scroll" tabIndex={0} aria-label={msg("{0} 가로 스크롤", tr(caption))}><table className="qp-matrix"><caption>{tr(caption)}</caption><thead><tr><th scope="col">{tr("분자")}</th>{tr(matrix.map((_, i) => <th scope="col" key={i} title={tr(labels[i])}>M{tr(i + 1)}</th>))}</tr></thead><tbody>{tr(matrix.map((row, i) => <tr key={i}><th scope="row"><b>M{tr(i + 1)}</b><span>{tr(labels[i])}</span></th>{tr(row.map((cell, j) => <td key={j} style={{ backgroundColor: `rgba(47, 99, 191, ${0.025 + Math.max(0, Math.min(1, cell)) * 0.17})` }}>{tr(onSelect ? <button type="button" className={selected?.[0] === i && selected?.[1] === j ? "selected" : ""} onClick={() => onSelect(i, j)} aria-label={`${tr(labels[i])} × ${tr(labels[j])}: ${number(cell)}${definitionDiagonal && i === j ? tr(", 정의상 대각값") : ""}`} data-testid={`quantum-cell-${i}-${j}`}>{tr(number(cell))}{tr(definitionDiagonal && i === j && <small>{tr("정의값")}</small>)}</button> : <span>{tr(number(cell))}</span>)}</td>))}</tr>))}</tbody></table></div>;
}

export default function QuantumPanel({ value, compounds = [], title, visualJourney = false, inputFeatures }: { value: QuantumResult | null | undefined; compounds?: Compound[]; title?: string; visualJourney?: boolean; inputFeatures?: number[][] }) {
  const [pair, setPair] = useState<[number, number]>([0, 1]);
  const [sample, setSample] = useState(0);
  const [offset, setOffset] = useState(0);
  const [readoutOffset, setReadoutOffset] = useState(0);
  const identity = value?.jobs?.map(job => job.job_id).join(",") || value?.plan?.feature_sha256 || "";
  useEffect(() => { setPair([0, 1]); setSample(0); setOffset(0); setReadoutOffset(0); }, [identity]);
  if (!value) return null;
  const method = quantumMethod(value), origin = quantumOrigin(value), projected = method === "projected";
  const plan = value.plan || value.metadata || {};
  const matrix = validKernel(value.kernel) ? value.kernel : null;
  const labels = Array.from({ length: matrix?.length || value.projected_features?.values.length || value.sample_ids?.length || 0 }, (_, i) => {
    const id = value.sample_ids?.[i];
    return value.sample_labels?.[i] || compounds.find(item => item.id === id)?.name_ko || compounds.find(item => item.id === id)?.name || id || msg("분자 {0}", i + 1);
  });
  const diagnostic = value.kernel_diagnostics;
  const selectedPair: [number, number] = matrix && pair[0] < matrix.length && pair[1] < matrix.length ? pair : [0, 0];
  const observation = pairObservation(value, ...selectedPair);
  const features = value.projected_features;
  const featureRows = features?.values?.[sample] || [];
  const hasMeasured = origin === "hardware" && value.status === "completed";
  const readout = value.controls?.readout;
  const worstReadout = [readout?.zero_error_rates || [], readout?.one_error_rates || []].flatMap((rates, prepared) => rates.map((rate, logical) => ({ rate, logical, prepared }))).filter(row => Number.isFinite(row.rate)).sort((a, b) => b.rate - a.rate)[0];
  return <section className="qp-panel" data-testid="quantum-result" data-method={method} data-origin={origin}>
    <header className="qp-heading"><div><span className="qp-eyebrow">{tr(title || "양자 특징 분석")}</span><h4>{tr(projected ? "관측량 기반 분자 특징" : "전역 Fidelity kernel")}</h4></div><span className="qp-origin">{tr(origin === "hardware" ? projected ? "IBM 관측량 → 커널 계산" : "IBM 전역 반환 확률" : origin === "local" ? "로컬 이상적 계산" : "실행 근거 미확인")}</span></header>
    {tr(visualJourney && <QuantumJourney value={value} compounds={compounds} inputFeatures={inputFeatures} />)}
    <p className="qp-scope">{tr(projected ? "각 분자를 인코딩한 회로의 X·Y·Z 기대값에서 특징 거리를 구해 RBF 커널을 계산합니다. 대각선 1은 정의값이며 자기 충실도를 실측한 결과가 아닙니다." : "두 입력 회로의 전역 all-zero 반환 확률입니다. 원시 측정 대각선과 0 관측을 그대로 표시합니다.")} {tr(" 단백질 결합 친화도·약효·안전성을 뜻하지 않습니다.")}</p>
    {tr(projected && <p className="qp-scope">{tr("K = exp(−γ × Σ큐빗,XYZ(특징 차이)² / 2q). 최대 6큐빗의 독립 블록은 고전 계산으로도 비교할 수 있습니다. 같은 입력 기술자를 여러 큐빗에 반복하는 것이 정보량 증가나 양자 우위를 뜻하지 않습니다.")}</p>)}
    {tr(globalKernelCollapsed(value) && <div className="qp-warning" role="status" data-testid="quantum-global-collapse"><CircleAlert size={20} /><div><strong>{tr("전역 측정으로 분자 차이를 구분하지 못했습니다")}</strong><p>{tr("동일 입력의 대각선까지 0으로 관측됐습니다. 이 회로의 잡음·폭·shot 한계로 전역 반환 신호가 소실됐을 가능성이 있습니다. 분자 유사도나 약효가 0이라는 뜻은 아닙니다.")}</p></div></div>)}
    {tr(diagnostic?.collapsed === true && projected && <div className="qp-warning" data-testid="quantum-projected-collapse"><CircleAlert size={20} /><p>{tr("관측량 기반 특징도 분자 사이의 구분 신호가 부족합니다. 반복 측정과 shot 잡음 진단을 함께 확인하세요.")}</p></div>)}
    <dl className="qp-facts"><div><dt>{tr("상태")}</dt><dd>{tr(statuses[value.status || ""] || value.status || "저장 결과")}</dd></div><div><dt>{tr("장비 / 큐빗")}</dt><dd>{tr(plan.backend_name || (origin === "local" ? "로컬" : "미기록"))} / {tr(number(plan.n_qubits))}</dd></div><div><dt>{tr("회로당 shots")}</dt><dd>{tr(number(plan.shots))}</dd></div>{tr(projected && <div><dt>{tr("블록 크기 / γ")}</dt><dd>{tr(number(plan.block_size))} / {tr(number(plan.gamma))}</dd></div>)}</dl>
    {tr(!matrix ? <p className="qp-pending" data-testid="quantum-measurements-pending">{tr(activeStatuses.has(value.status || "") ? <LoaderCircle size={17} className="spin" /> : <Cpu size={17} />)}{tr("측정 결과가 아직 없습니다. 대기·실패한 결과를 0으로 채우지 않습니다.")}</p> : <>
      <QuantumMatrix matrix={matrix} labels={labels} caption={projected ? "측정 특징에서 계산한 RBF 커널" : "원시 전역 반환 확률"} selected={selectedPair} onSelect={(a, b) => setPair([a, b])} definitionDiagonal={projected} />
      <div className="qp-pair-detail" data-testid="quantum-pair-detail"><strong>M{tr(selectedPair[0] + 1)} × M{tr(selectedPair[1] + 1)}</strong><span>{tr(labels[selectedPair[0]])} × {tr(labels[selectedPair[1]])}</span><dl><div><dt>{tr(projected ? "계산된 커널 값" : "관측 확률")}</dt><dd>{tr(number(matrix[selectedPair[0]][selectedPair[1]]))}</dd></div>{tr(!projected && <><div><dt>{tr("all-zero 관측 / shots")}</dt><dd>{tr(number(observation?.zero_counts))} / {tr(number(observation?.shots))}</dd></div><div><dt>{tr("Wilson 95% 구간")}</dt><dd>{tr(interval(observation?.wilson_95))}</dd></div></>)}{tr(projected && <div><dt>{tr("95% shot 재표집 범위")}</dt><dd>{tr(selectedPair[0] === selectedPair[1] ? "대각선은 정의상 1" : interval(value.kernel_uncertainty?.lower_95 && value.kernel_uncertainty?.upper_95 ? [value.kernel_uncertainty.lower_95[selectedPair[0]]?.[selectedPair[1]], value.kernel_uncertainty.upper_95[selectedPair[0]]?.[selectedPair[1]]] : undefined))}</dd></div>)}</dl>{tr(projected && <><p>{tr("전체 shot을 재표집한 커널 분포의 2.5–97.5 백분위입니다. 재표집으로 제곱 거리에 잡음이 더해져 관측값이 범위 밖일 수 있습니다. 이상적 커널의 신뢰구간이 아니며 판독 편향·시간 변화는 포함하지 않습니다.")}</p><p>{tr(explain(value.kernel_uncertainty?.method))}{tr(value.kernel_uncertainty?.caveat && ` · ${explain(value.kernel_uncertainty.caveat)}`)}</p></>)}</div>
    </>)}
    {tr(projected && matrix && <>
      <div className="qp-diagnostics" data-testid="quantum-diagnostics"><h5>{tr("실제 관측의 분해능과 대조 측정")}</h5><dl><div><dt>{tr("비대각 커널 최소 / 평균 / 최대")}</dt><dd>{tr([diagnostic?.off_diagonal_min, diagnostic?.off_diagonal_mean, diagnostic?.off_diagonal_max].map(number).join(" / "))}</dd></div><div><dt>{tr("Shot 잡음 거리 기준")}</dt><dd>{tr(number(diagnostic?.shot_noise_distance_floor))}</dd></div><div><dt>{tr("신호 / shot 잡음")}</dt><dd>{tr(number(diagnostic?.signal_to_shot_noise))}</dd></div><div><dt>{tr("별도 반복 측정과의 커널")}</dt><dd>{tr(number(value.controls?.duplicate?.kernel_to_original))}</dd></div><div><dt>{tr("별도 반복 측정의 제곱 거리")}</dt><dd>{tr(number(value.controls?.duplicate?.squared_distance))}</dd></div><div><dt>{tr("상태 준비·readout 대조 평균 오류")}</dt><dd>{tr(number(value.controls?.readout?.mean_error))}{tr(percentage(value.controls?.readout?.mean_error))}</dd></div><div data-testid="quantum-readout-worst"><dt>{tr("최대 상태 준비·readout 대조 오류")}</dt><dd>{tr(number(worstReadout?.rate))}{tr(percentage(worstReadout?.rate))}{tr(worstReadout && <small>{msg(" · 상태 |{0}⟩ · 논리 q{1} / 물리 q{2}", worstReadout.prepared, worstReadout.logical, number(plan.physical_qubits?.[worstReadout.logical]))}</small>)}</dd></div></dl><p>{tr("반복 측정과 상태 준비·readout 대조는 회로·장비의 진단입니다. readout 대조에는 상태 준비 오류도 포함되며 검출기만의 오류율이 아닙니다. 두 거리 진단은 Σ/(2q)로 정규화한 값입니다. 생물학적 정확도나 양자 우위를 검증하지 않습니다. 원시 결과에 PSD 보정을 적용했는지: ")}{tr(diagnostic?.psd_projection_applied === false ? "아니요" : diagnostic?.psd_projection_applied === true ? "예 · 보정 기록 확인" : "미기록")}.</p></div>
      <details className="qp-details" data-testid="quantum-observables"><summary>{tr(hasMeasured ? "실측" : "계산")} {tr(" X·Y·Z 기대값과 95% 구간")}</summary><label className="qp-field">{tr("입력 분자")}<select value={sample} onChange={event => { setSample(Number(event.target.value)); setOffset(0); }} data-testid="quantum-feature-sample">{tr(labels.map((label, i) => <option key={i} value={i}>M{tr(i + 1)} · {tr(label)}</option>))}</select></label><p>{tr("논리 큐빗 번호와 측정축을 표시합니다. 각 구간은 해당 관측량의 shot 표본 불확실성이며, 게이트·보정 편향 전체를 포함하지 않습니다.")}</p><div className="qp-table-scroll" tabIndex={0}><table className="qp-observable-table"><thead><tr><th>{tr("논리 큐빗")}</th>{tr((features?.axes || ["X", "Y", "Z"]).map(axis => <th key={axis}>⟨{tr(axis)}{tr("⟩ / 95% 구간")}</th>))}</tr></thead><tbody>{tr(featureRows.slice(offset, offset + 12).map((row, i) => <tr key={offset + i}><th scope="row">q{tr(offset + i)}</th>{tr(row.map((val, axis) => <td key={axis}><strong>{tr(number(val))}</strong><small>{tr(interval(features?.wilson_95?.[sample]?.[offset + i]?.[axis]))}</small></td>))}</tr>))}</tbody></table></div><div className="qp-pagination"><button onClick={() => setOffset(Math.max(0, offset - 12))} disabled={!offset} aria-label={tr("이전 큐빗")}><ChevronLeft size={17} /></button><span>{tr(featureRows.length ? offset + 1 : 0)}–{tr(Math.min(offset + 12, featureRows.length))} / {tr(featureRows.length)} {tr(" 큐빗")}</span><button onClick={() => setOffset(offset + 12)} disabled={offset + 12 >= featureRows.length} aria-label={tr("다음 큐빗")}><ChevronRight size={17} /></button></div></details>
      {tr(readout?.zero_error_rates && readout.one_error_rates && <details className="qp-details" data-testid="quantum-readout"><summary>{tr("큐빗별 상태 준비·판독 대조와 개별 Wilson 95% 구간")}</summary><p>{tr("이상적으로 준비한 |0⟩, |1⟩과 다른 결과가 관측된 비율입니다. 상태 준비 오류를 포함하므로 검출기만의 오류율로 해석하지 않습니다. 원시 측정에 보정을 적용하지 않았습니다.")}</p><div className="qp-table-scroll" tabIndex={0}><table className="qp-observable-table"><thead><tr><th>{tr("논리 / 물리 큐빗")}</th><th>{tr("|0⟩ 대조 오류 / 95% 구간")}</th><th>{tr("|1⟩ 대조 오류 / 95% 구간")}</th></tr></thead><tbody>{tr(readout.zero_error_rates.slice(readoutOffset, readoutOffset + 12).map((rate, i) => <tr key={i}><th scope="row">q{tr(readoutOffset + i)} / q{tr(number(plan.physical_qubits?.[readoutOffset + i]))}</th><td><strong>{tr(number(rate))}</strong><small>{tr(interval(readout.zero_error_wilson_95?.[readoutOffset + i]))}</small></td><td><strong>{tr(number(readout.one_error_rates?.[readoutOffset + i]))}</strong><small>{tr(interval(readout.one_error_wilson_95?.[readoutOffset + i]))}</small></td></tr>))}</tbody></table></div><div className="qp-pagination"><button onClick={() => setReadoutOffset(Math.max(0, readoutOffset - 12))} disabled={!readoutOffset} aria-label={tr("이전 대조 큐빗")}><ChevronLeft size={17} /></button><span>{tr(readoutOffset + 1)}–{tr(Math.min(readoutOffset + 12, readout.zero_error_rates.length))} / {tr(readout.zero_error_rates.length)} {tr(" 큐빗")}</span><button onClick={() => setReadoutOffset(readoutOffset + 12)} disabled={readoutOffset + 12 >= readout.zero_error_rates.length} aria-label={tr("다음 대조 큐빗")}><ChevronRight size={17} /></button></div></details>)}
      <details className="qp-details" data-testid="quantum-references"><summary>{tr("같은 입력의 이상적 회로·고전 기준과 비교")}</summary><p>{tr("고전 기준과 이상적 시뮬레이션은 IBM 측정값을 대체하지 않습니다. 특징 인코딩·연결·잡음의 차이를 확인하기 위한 비교이며 지도학습 성능 검증이 아닙니다.")}</p>{tr(validKernel(value.ideal_reference?.kernel) ? <QuantumMatrix matrix={value.ideal_reference.kernel} labels={labels} caption="동일 블록·연결의 이상적 회로 계산" definitionDiagonal /> : <p>{tr("이상적 회로 기준: ")}{tr(value.ideal_reference?.status || "미기록")}</p>)}{tr(validKernel(value.classical_reference?.kernel) ? <QuantumMatrix matrix={value.classical_reference.kernel} labels={labels} caption={msg("고전 기준 · {0}", value.classical_reference.estimator || tr("방법 미기록"))} definitionDiagonal /> : <p>{tr("고전 기준 미기록")}</p>)}</details>
    </>)}
    <details className="qp-details" data-testid="quantum-provenance"><summary>{tr("입력 특징·장비·원시 측정 근거")}</summary><dl className="qp-provenance"><div><dt>{tr("입력 순서")}</dt><dd>{tr(value.sample_ids?.join(" → ") || "미기록")}</dd></div><div><dt>{tr("특징 SHA-256")}</dt><dd>{tr(plan.feature_sha256 || "미기록")}</dd></div><div><dt>{tr("방법")}</dt><dd>{tr(value.estimator || method)}</dd></div><div><dt>{tr("원본 분석")}</dt><dd>{tr(value.source_analysis_id || "이 분석의 원본 양자 단계")}</dd></div>{tr(value.jobs?.map(job => <div key={job.job_id}><dt>IBM job · {tr(job.status)}</dt><dd>{tr(job.job_id)} · QPU {tr(number(job.quantum_seconds))}{tr("초")}</dd></div>))}</dl><pre>{JSON.stringify({ feature_definition: value.feature_definition, plan, compiled: value.compiled, controls: value.controls, jobs: value.jobs, kernel_uncertainty: value.kernel_uncertainty }, null, 2)}</pre></details>
    <button className="qp-outline" onClick={() => download(value, `quantum-${method}-result.json`)} data-testid="quantum-export"><ArrowDownToLine size={16} />{tr("전체 결과 JSON")}</button>
  </section>;
}

export function AnalysisQuantumWorkspace({ analysis, compounds, visualJourney = false }: { analysis: Analysis; compounds: Compound[]; visualJourney?: boolean }) {
  const original = analysis.result?.quantum as QuantumResult | undefined;
  const [jobs, setJobs] = useState<Job[]>([]), [selected, setSelected] = useState<string | null>(null);
  const [revision, setRevision] = useState(0), [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null), [plan, setPlan] = useState<QuantumPlan | null>(null);
  const [shots, setShots] = useState(1024), [showPrepare, setShowPrepare] = useState(false);
  const latest = useRef(analysis.id); latest.current = analysis.id;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const selectedJob = jobs.find(job => job.id === selected);
  const features = original?.feature_definition?.features;
  const sampleIds = original?.sample_ids || [];
  const request = useMemo<QuantumRequest | null>(() => features && features.length >= 2 && sampleIds.length === features.length ? {
    features, sample_ids: sampleIds, sample_labels: sampleIds.map(id => compounds.find(row => row.id === id)?.name_ko || compounds.find(row => row.id === id)?.name || id),
    source_analysis_id: analysis.id, mode: "ibm", kernel_method: "projected", qubits: "max", layers: 1, block_size: 6, gamma: 1,
    shots, max_circuits: 64, circuits_per_job: 64, max_jobs: 1, max_total_shots: 65536, max_execution_time: 30,
  } : null, [features, sampleIds, analysis.id, compounds, shots]);
  useEffect(() => {
    const controller = new AbortController(); setJobs([]); setSelected(null); setPlan(null); setError(null); setShowPrepare(false); setBusy(null);
    void api<{ items: Job[] }>(`/quantum/results?source_analysis_id=${analysis.id}`, undefined, { signal: controller.signal }).then(({ items }) => {
      if (!controller.signal.aborted) { setJobs(items); const remembered = readQuantumSelection(analysis.id); setSelected(items.find(job => job.id === remembered)?.id || items[0]?.id || null); }
    }).catch((problem: Error) => { if (!controller.signal.aborted) setError(`연결된 양자 기록 조회 실패: ${problem.message}`); });
    return () => controller.abort();
  }, [analysis.id, revision]);
  useEffect(() => {
    if (!selectedJob || !activeStatuses.has(selectedJob.status)) return;
    const id = analysis.id, jobId = selectedJob.id, controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const updated = await api<Job>(`/quantum/${jobId}/refresh`, {}, { signal: controller.signal });
        if (controller.signal.aborted || latest.current !== id) return;
        setJobs(previous => previous.map(job => job.id === jobId ? updated : job));
        if (activeStatuses.has(updated.status)) timer = setTimeout(() => void poll(), 6000);
      } catch (problem) { if (!controller.signal.aborted && latest.current === id) setError(`기존 IBM 작업 조회 실패: ${(problem as Error).message} 자동 재제출하지 않았습니다.`); }
    }
    timer = setTimeout(() => void poll(), 1500);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [analysis.id, selectedJob?.id, selectedJob?.status]);
  async function prepare() {
    if (!request || busy) return; const id = analysis.id; setBusy("plan"); setError(null); setPlan(null);
    try { const result = await api<QuantumPlan>("/quantum/plan", request); if (mounted.current && latest.current === id) setPlan(result); }
    catch (problem) { if (mounted.current && latest.current === id) setError((problem as Error).message); }
    finally { if (mounted.current && latest.current === id) setBusy(null); }
  }
  async function execute() {
    if (!request || busy || plan?.status !== "ready") return; const id = analysis.id; setBusy("execute"); setError(null);
    try { const job = await api<Job>("/quantum/run", { ...request, backend_name: plan.backend_name }); if (mounted.current && latest.current === id) { setJobs(previous => [job, ...previous.filter(row => row.id !== job.id)]); setSelected(job.id); saveQuantumSelection(id, job.id); setPlan(null); setShowPrepare(false); } }
    catch (problem) { if (mounted.current && latest.current === id) { setPlan(null); setError(`제출 확인 실패: ${(problem as Error).message} 중복 제출을 피하려면 저장 기록과 IBM job을 먼저 확인하세요.`); } }
    finally { if (mounted.current && latest.current === id) setBusy(null); }
  }
  if (!original || original.status === "off") return null;
  return <div className="qp-workspace" data-testid="quantum-workspace" data-source-analysis={analysis.id}>
    <QuantumPanel value={original} compounds={compounds} title={tr("원본 분석의 양자 결과 · 보존")} visualJourney={visualJourney} inputFeatures={original?.feature_definition?.features} />
    <section className="qp-recompute"><header className="qp-heading"><div><h4>{tr("같은 분자의 새 관측량 계산")}</h4><p>{tr("원본 입력·분자 순서를 유지하고 양자 단계만 별도 기록으로 계산합니다.")}</p></div><button className="qp-icon" onClick={() => setRevision(value => value + 1)} aria-label={tr("연결된 양자 기록 새로고침")} data-testid="quantum-linked-refresh" disabled={!!busy}><RefreshCw size={17} /></button></header>
      {tr(jobs.length > 0 && <label className="qp-field">{tr("같은 분석에서 파생된 실행")}<select value={selected || ""} onChange={event => { setSelected(event.target.value); saveQuantumSelection(analysis.id, event.target.value); }} data-testid="quantum-linked-job-select">{tr(jobs.map(job => <option key={job.id} value={job.id}>{tr(new Date(job.created).toLocaleString(localeCode()))} · {tr(job.payload.kernel_method === "projected" ? "관측량 기반" : "전역 Fidelity")} · {tr(statuses[job.status] || job.status)} · {tr(job.id.slice(0, 8))}</option>))}</select></label>)}
      <button className="qp-outline" disabled={!request || !!busy} onClick={() => setShowPrepare(value => !value)} data-testid="quantum-recompute-open"><Cpu size={17} />{tr("양자 단계만 다시 계산")}</button>
      {tr(!request && <p className="qp-muted">{tr("저장된 입력 특징과 분자 순서가 없어 동일 조건 재계산을 준비할 수 없습니다.")}</p>)}
      {tr(showPrepare && request && <div className="qp-plan"><p>{tr("IBM 최대 가용 큐빗을 최대 6큐빗 블록으로 사용합니다. 분자당 X·Y·Z 측정과 별도 반복·readout 대조를 포함합니다. 원본 Fidelity 결과는 덮어쓰지 않습니다.")}</p><label className="qp-field">{tr("회로당 shots")}<select value={shots} disabled={!!busy} onChange={event => { setShots(Number(event.target.value)); setPlan(null); }} data-testid="quantum-shots">{tr([128, 512, 1024, 2048].map(value => <option key={value} value={value}>{tr(value.toLocaleString())}</option>))}</select></label><p>{msg("{0}개 분자 · 예상 {1}회로 · 총 {2} shots. 1개 IBM 작업, QPU 실행 상한 30초, 전체 shot 예산 65,536. 대기 시간과 요금은 별도입니다.", sampleIds.length, 3 * sampleIds.length + 5, (shots * (3 * sampleIds.length + 5)).toLocaleString(localeCode()))}</p><button className="qp-outline" onClick={() => void prepare()} disabled={!!busy} data-testid="quantum-plan-button">{tr(busy === "plan" ? <LoaderCircle size={17} className="spin" /> : <Cpu size={17} />)}{tr("장비·실행 계획 확인")}</button>
        {tr(plan && <div data-testid="quantum-plan"><dl className="qp-facts"><div><dt>{tr("계획 상태")}</dt><dd>{tr(statuses[plan.status || ""] || plan.status)}</dd></div><div><dt>{tr("장비 / 실제 큐빗")}</dt><dd>{tr(plan.backend_name || "미정")} / {tr(number(plan.n_qubits))}</dd></div><div><dt>{tr("실제 계획 회로 / shots")}</dt><dd>{tr(number(plan.circuit_count))} / {tr(number(plan.total_shots))}</dd></div><div><dt>{tr("작업당 QPU 초 상한")}</dt><dd>{tr(number(plan.max_execution_time_per_job))}</dd></div></dl>{tr(plan.warnings?.length ? <ul>{tr(plan.warnings.map((warning, i) => <li key={i}>{tr(explain(warning))}</li>))}</ul> : null)}<button className="qp-primary" onClick={() => void execute()} disabled={!!busy || plan.status !== "ready"} data-testid="quantum-execute-button">{tr(busy === "execute" ? <LoaderCircle size={17} className="spin" /> : <Play size={17} />)}{tr("이 계획으로 IBM 계산 실행")}</button></div>)}
      </div>)}
      {tr(error && <p className="qp-error" role="alert" data-testid="quantum-workspace-error">{tr(error)}</p>)}
    </section>
    {tr(selectedJob && <div className="qp-selected-job" data-testid="quantum-linked-job" data-job-id={selectedJob.id} data-job-status={selectedJob.status}><p className="qp-job-id">{tr("별도 실행 ")}{tr(selectedJob.id)}{tr(activeStatuses.has(selectedJob.status) && <span> {tr(" · 기존 IBM 작업을 자동 조회 중")}</span>)}</p>{tr(selectedJob.error && <p className="qp-error">{tr(selectedJob.error)}</p>)}<QuantumPanel key={selectedJob.id} value={{ ...selectedJob.result, status: selectedJob.result?.status || selectedJob.status, mode: selectedJob.result?.mode || selectedJob.payload.mode, sample_ids: selectedJob.result?.sample_ids || selectedJob.payload.sample_ids, sample_labels: selectedJob.result?.sample_labels || selectedJob.payload.sample_labels }} compounds={compounds} title={tr("별도 계산 결과 · 원본과 비교")} visualJourney={visualJourney} inputFeatures={selectedJob.payload?.features} /></div>)}
  </div>;
}
