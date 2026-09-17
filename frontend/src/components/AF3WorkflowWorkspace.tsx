import { tr, msg, localeCode, getLanguage } from "../lib/i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDownToLine, ArrowRight, Atom, Check, ChevronRight, CircleAlert, Clock3, Cpu, Database, FileCheck2, FolderClock, Link2, LoaderCircle, Play, RefreshCw, ShieldCheck, Terminal, Wifi, WifiOff, Workflow } from "lucide-react";
import { api, download } from "../lib/api";
import { clearPendingAF3 } from "../lib/af3WorkflowSubmission";
import { pollPrediction, predictionRequest, PredictionRequestTimeout } from "../lib/predictionPolling";
import { elapsedSeconds, formatElapsed } from "../lib/predictionProgress";
import { rememberedAF3Workflow, rememberAF3Workflow, trustedAF3WorkflowResult } from "../lib/af3WorkflowSelection";
import type { AF3Workflow, AF3WorkflowStage } from "../types/af3Workflow";
import type { PredictionEnvelope, PredictionLog } from "../types/prediction";
import { AF3StageEvidence } from "./AF3EvidencePanel";
import "./af3-calculation.css";
import "./af3-workflow.css";

type Props = {
  workflowId: string | null;
  refreshKey?: number;
  onSelectWorkflow: (id: string) => void;
  onViewResult: (workflow: AF3Workflow) => void;
  notify: (message: string, error?: boolean) => void;
};
const ACTIVE = new Set(["queued", "preparing", "queued_for_execution", "running", "submitted", "validating", "postprocessing"]);
const STATUS: Record<string, string> = { queued: "요청 접수", preparing: "입력 준비 중", prepared: "실행 준비됨", queued_for_execution: "계산 대기", running: "실행 중", submitted: "제출됨", pending: "대기", validating: "출력 검증 중", postprocessing: "결과 처리 중", completed: "완료", blocked: "조건 확인 필요", failed: "실패", interrupted: "중단 · 기록 확인", cancelled: "취소", skipped: "생략 · 재사용", unknown: "기록 없음" };
const STAGES: Record<string, { label: string; agent: string; icon: typeof Atom }> = {
  input: { label: "분자·표적 입력 확인", agent: "입력 검증 작업자", icon: Atom },
  preflight: { label: "실행 환경·입력 파일 준비", agent: "실행 준비 작업자", icon: FileCheck2 },
  msa: { label: "MSA·템플릿 및 특징 준비", agent: "서열·특징 처리 작업자", icon: Database },
  inference: { label: "AlphaFold 구조 추론", agent: "AF3 실행 작업자", icon: Cpu },
  validation: { label: "출력 파일·성분·표적 확인", agent: "출력 검증 작업자", icon: ShieldCheck },
  results: { label: "결과 연결·검토 준비", agent: "결과 정리 작업자", icon: Workflow },
};
const dateLabel = (value?: string | null) => { if (!value) return "미기록"; const d = new Date(value); return Number.isNaN(d.valueOf()) ? "미기록" : d.toLocaleString(localeCode(), { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
const modeLabel = (mode: string) => mode === "none" ? "탐색 · MSA·템플릿 생략" : "표준 · MSA 검색";
const workflowName = (value: AF3Workflow) => {
  const sourceName = value.request.compound?.name;
  const displayName = value.request.compound_name || sourceName;
  // Prefer an explicitly stored English compound name only for a known alias.
  // Arbitrary researcher labels are never passed through display translation.
  if (getLanguage() === "en" && displayName && sourceName && !/[가-힣]/.test(sourceName)
    && tr(displayName).toLocaleLowerCase() === sourceName.toLocaleLowerCase()) return sourceName;
  return displayName || tr("AF3 구조 예측");
};
const shortStructure = (value: string) => value.length > 44 ? `${value.slice(0, 41)}…` : value;
function diagnosticText(value: string): string {
  if (value.includes("all-zero model identifier")) return "현재 모델 파일은 무작위 시험용 가중치로 확인되었습니다. 정식 학습 가중치를 제공해야 예측을 실행할 수 있습니다.";
  if (value.includes("AF3_DB_DIR") && /official|MSA search|manifest/.test(value)) return "공식 MSA·템플릿 데이터베이스의 설치와 검증 기록을 AF3_DB_DIR에서 확인해야 합니다.";
  return value;
}
function stageSummary(stage: AF3WorkflowStage): string {
  if (stage.id === "validation") {
    if (stage.summary === "geometry_warning") return "성분·표적 동일성은 확인했으나 좌표의 기하학적 주의사항이 있습니다.";
    if (stage.summary === "identity_verified_quality_unassessed") return "성분·표적 동일성을 확인했습니다. 전체 구조 품질은 별도 평가 대상입니다.";
    if (stage.summary === "identity_failed") return "출력이 요청한 성분·표적과 일치하는지 확인하지 못했습니다.";
  }
  return stage.summary || "해당 단계의 처리 기록을 기다리고 있습니다.";
}
function stageDuration(stage: AF3WorkflowStage): string | null {
  if (!stage.started_at || !stage.finished_at || !["completed", "failed", "interrupted"].includes(stage.status)) return null;
  return formatElapsed(elapsedSeconds(stage.started_at, Date.parse(stage.finished_at)));
}

export default function AF3WorkflowWorkspace({ workflowId, refreshKey = 0, onSelectWorkflow, onViewResult, notify }: Props) {
  const [localId, setLocalId] = useState<string | null>(rememberedAF3Workflow);
  const selectedId = workflowId || localId;
  const [workflowData, setWorkflowData] = useState<AF3Workflow | null>(null);
  const workflow = workflowData?.id === selectedId ? workflowData : null;
  const [items, setItems] = useState<AF3Workflow[]>([]);
  const [storedJobs, setStoredJobs] = useState<PredictionEnvelope[]>([]);
  const [historyTab, setHistoryTab] = useState<"workflows" | "jobs">("workflows");
  const [historyError, setHistoryError] = useState("");
  const [jobsError, setJobsError] = useState("");
  const [loading, setLoading] = useState(!!selectedId);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [statusError, setStatusError] = useState("");
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [online, setOnline] = useState(() => typeof navigator === "undefined" || navigator.onLine);
  const [logRecord, setLogRecord] = useState<{ jobId: string; data: PredictionLog } | null>(null);
  const [logError, setLogError] = useState("");
  const [busy, setBusy] = useState<"resume" | string | null>(null);
  const [notice, setNotice] = useState("");
  const [mutationError, setMutationError] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const mounted = useRef(true);
  const selection = useRef(selectedId);
  selection.current = selectedId;
  const callbacks = useRef({ onSelectWorkflow, onViewResult, notify });
  callbacks.current = { onSelectWorkflow, onViewResult, notify };
  const mutation = useRef<AbortController | null>(null);
  const mutationLock = useRef(false);
  const explicitRefreshRevision = useRef<number | null>(null);
  const active = !!workflow && ACTIVE.has(workflow.status);
  const prediction = workflow?.prediction ?? null;
  const jobId = workflow?.job_id ?? null;
  const log = logRecord?.jobId === jobId ? logRecord.data : null;
  const resultReady = trustedAF3WorkflowResult(workflow);
  const blockers = [...new Set([...(workflow?.blockers ?? []), ...(prediction?.readiness.blockers ?? [])])];
  const resumeKind = workflow?.actions.resume_kind;
  const executeAfterPreparation = workflow?.request.execute === true;
  const resumeLabel = resumeKind === "execute" ? "준비한 AF3 계산 실행" : resumeKind === "retry" ? executeAfterPreparation ? "이 조건으로 다시 준비·실행" : "이 조건으로 입력 다시 준비" : "입력 준비 이어가기";
  const resumeDescription = resumeKind === "execute"
    ? "준비된 입력을 사용해 실제 AF3 계산을 제출합니다."
    : resumeKind === "retry"
      ? executeAfterPreparation ? "기존 기록을 보존하면서 같은 조건으로 새 준비와 실행을 요청합니다. 계산 자원을 다시 사용할 수 있습니다." : "기존 기록을 보존하면서 같은 조건으로 입력 준비와 실행 환경 확인을 다시 요청합니다. AF3 추론은 별도 실행 요청이 필요합니다."
      : executeAfterPreparation ? "저장된 요청의 입력 준비를 이어가며, 준비가 완료되면 AF3 계산을 실행합니다." : "저장된 요청의 입력 준비와 실행 환경 확인을 이어갑니다. AF3 추론은 별도 실행 요청이 필요합니다.";
  const canResume = !!workflow?.actions.can_resume && !busy && !uncertain && online && (workflow.request.msa_mode !== "none" || acknowledged);

  const refreshStatus = () => setRevision(value => { explicitRefreshRevision.current = value + 1; return value + 1; });
  const select = useCallback((id: string) => {
    rememberAF3Workflow(id); setLocalId(id); callbacks.current.onSelectWorkflow(id);
  }, []);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; mutation.current?.abort(); }; }, []);
  useEffect(() => { if (workflowId) { rememberAF3Workflow(workflowId); setLocalId(workflowId); } else if (localId) callbacks.current.onSelectWorkflow(localId); }, [workflowId, localId]);
  useEffect(() => { setNotice(""); setMutationError(""); setUncertain(false); setAcknowledged(false); setCheckedAt(null); setStatusError(""); setLogError(""); }, [selectedId]);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const recover = () => { setOnline(navigator.onLine); if (navigator.onLine && document.visibilityState === "visible") { clearTimeout(timer); timer = setTimeout(() => setRevision(v => v + 1), 80); } };
    const offline = () => setOnline(false);
    window.addEventListener("online", recover); window.addEventListener("offline", offline); document.addEventListener("visibilitychange", recover);
    return () => { clearTimeout(timer); window.removeEventListener("online", recover); window.removeEventListener("offline", offline); document.removeEventListener("visibilitychange", recover); };
  }, []);
  useEffect(() => {
    if (!online) { setHistoryLoading(false); return; }
    const controller = new AbortController(); setHistoryLoading(true);
    pollPrediction({ signal: controller.signal, intervalMs: 10000,
      request: signal => api<{ items: AF3Workflow[] }>("/af3-workflows", undefined, { signal }),
      onValue: value => { setItems(value.items); setHistoryError(""); setHistoryLoading(false); if (!selection.current && value.items.length) { const latest = [...value.items].sort((a, b) => b.created.localeCompare(a.created))[0]; selection.current = latest.id; select(latest.id); } },
      onError: error => { setHistoryError(error.message); setHistoryLoading(false); },
      shouldRepeat: value => value.items.some(item => ACTIVE.has(item.status)),
    });
    void predictionRequest(signal => api<{ items: PredictionEnvelope[] }>("/af3-workflows/jobs", undefined, { signal }), { signal: controller.signal }).then(value => { if (!controller.signal.aborted) { setStoredJobs(value.items); setJobsError(""); } }).catch(error => { if (!controller.signal.aborted) setJobsError(error.message); });
    return () => controller.abort();
  }, [revision, refreshKey, online]);
  useEffect(() => {
    if (!selectedId || !online) { setLoading(false); return; }
    const controller = new AbortController(); setLoading(true);
    pollPrediction({ signal: controller.signal, intervalMs: 2500,
      request: signal => api<AF3Workflow>(`/af3-workflows/${encodeURIComponent(selectedId)}`, undefined, { signal }),
      isCurrent: () => selection.current === selectedId,
      onValue: value => { clearPendingAF3(value.request_id); setWorkflowData(value); setLoading(false); setStatusError(""); setCheckedAt(new Date().toISOString()); if (explicitRefreshRevision.current === revision) setUncertain(false); setItems(previous => [value, ...previous.filter(item => item.id !== value.id)].sort((a, b) => b.created.localeCompare(a.created))); },
      onError: error => { setStatusError(error.message); setLoading(false); },
      shouldRepeat: value => ACTIVE.has(value.status),
    });
    return () => controller.abort();
  }, [selectedId, revision, refreshKey, online]);
  useEffect(() => {
    if (!jobId || !online) return;
    const controller = new AbortController(); setLogError("");
    pollPrediction({ signal: controller.signal, intervalMs: 3500,
      request: signal => api<PredictionLog>(`/molecular/predictions/${encodeURIComponent(jobId)}/log`, undefined, { signal }),
      isCurrent: () => selection.current === selectedId,
      onValue: data => { setLogRecord({ jobId, data }); setLogError(""); },
      onError: error => setLogError(error.message),
      shouldRepeat: value => ACTIVE.has(value.status),
    });
    return () => controller.abort();
  }, [jobId, selectedId, revision, refreshKey, online]);

  async function resume() {
    if (!workflow || !canResume || mutationLock.current) return;
    const captured = workflow.id; const controller = new AbortController(); mutation.current = controller; mutationLock.current = true;
    setBusy("resume"); setMutationError(""); setNotice("");
    try {
      const value = await predictionRequest(signal => api<AF3Workflow>(`/af3-workflows/${encodeURIComponent(captured)}/resume`, { confirm: true }, { signal }), { signal: controller.signal, timeoutMs: 30000 });
      if (!mounted.current || selection.current !== captured) return;
      setWorkflowData(value); setRevision(v => v + 1); setAcknowledged(false); setNotice("저장된 조건으로 재개 요청을 전달했습니다. 실제 처리 상태를 확인하고 있습니다.");
    } catch (error) {
      if (!mounted.current || selection.current !== captured) return;
      if (error instanceof PredictionRequestTimeout) { setUncertain(true); setNotice("재개 응답이 지연되고 있습니다. 서버에 이미 접수되었을 수 있으므로 ‘상태 새로고침’으로 저장 기록을 확인하세요. 자동 재제출은 하지 않습니다."); }
      else setMutationError((error as Error).message);
    } finally { mutationLock.current = false; if (mounted.current) setBusy(null); mutation.current = null; }
  }
  async function attach(entry: PredictionEnvelope) {
    if (mutationLock.current || !online) return;
    const captured = selectedId; const controller = new AbortController(); mutation.current = controller; mutationLock.current = true;
    setBusy(entry.job.id); setMutationError(""); setNotice("");
    try {
      const value = await predictionRequest(signal => api<AF3Workflow>("/af3-workflows/attach", { job_id: entry.job.id }, { signal }), { signal: controller.signal, timeoutMs: 20000 });
      if (!mounted.current) return;
      setRevision(v => v + 1);
      if (selection.current === captured) { setWorkflowData(value); select(value.id); setHistoryTab("workflows"); setNotice("기존 AF3 작업의 기록을 연결했습니다. 새 계산은 시작하지 않았습니다."); }
      callbacks.current.notify("기존 AF3 작업을 에이전트 분석에 연결했습니다.");
    } catch (error) {
      if (!mounted.current || selection.current !== captured) return;
      if (error instanceof PredictionRequestTimeout) { setNotice("기록 연결 응답이 지연되고 있습니다. 상태 새로고침으로 연결된 기록을 확인하세요."); setRevision(v => v + 1); }
      else setMutationError((error as Error).message);
    } finally { mutationLock.current = false; if (mounted.current) setBusy(null); mutation.current = null; }
  }

  return <div className="af3-workspace" data-testid="af3-workflow-workspace">
    <header className="afw-hero"><div><span className="afw-eyebrow"><Workflow size={15} /> ALPHAFOLD · AGENT WORKSPACE</span><h2>{tr("구조 예측의 모든 과정을 한곳에.")}</h2><p>{tr("요청 접수부터 입력 준비, AF3 실행과 출력 확인까지.")}<br />{tr("실제 단계와 저장된 기록을 연결해 진행 상황을 확인합니다.")}</p></div><div className="afw-hero-mark" aria-hidden="true"><span><Atom size={28} /></span><ArrowRight size={19} /><span><Cpu size={27} /></span><ArrowRight size={19} /><span><ShieldCheck size={27} /></span></div></header>
    <div className={`afw-connection ${online ? "" : "offline"}`}><span>{tr(online ? <Wifi size={15} /> : <WifiOff size={15} />)}{tr(!online ? "브라우저가 오프라인입니다. 마지막 수신 기록을 표시하며, 연결되면 상태를 다시 확인합니다." : statusError ? "서버 응답을 다시 확인하고 있습니다. 마지막 수신 상태를 유지합니다." : "화면을 닫아도 저장된 요청을 다시 확인할 수 있습니다. 연결 복구는 상태 조회만 수행합니다.")}</span><button className="afw-text-button" onClick={refreshStatus} disabled={!online} data-testid="af3-workflow-refresh"><RefreshCw size={14} /> {tr(" 상태 새로고침")}</button></div>
    <div className="afw-layout">
      <aside className="afw-history afw-card"><div className="afw-heading"><h3>{tr("실행 기록")}<span className="afw-count">{tr(items.length)}</span></h3><FolderClock size={18} color="#8aa0af" /></div><div className="afw-tabs" role="tablist" aria-label={tr("실행 기록 종류")}><button role="tab" aria-selected={historyTab === "workflows"} onClick={() => setHistoryTab("workflows")}>{tr("에이전트 요청")}</button><button role="tab" aria-selected={historyTab === "jobs"} onClick={() => setHistoryTab("jobs")}>{tr("기존 AF3 작업")}</button></div>
        {tr(historyTab === "workflows" ? <>{tr(historyError && <p className="afw-error">{tr(historyError)}</p>)}<div className="afw-history-list" role="tabpanel" aria-label={tr("저장된 에이전트 요청")}>{tr(items.map(item => <button key={item.id} className={`afw-history-item ${selectedId === item.id ? "selected" : ""}`} onClick={() => select(item.id)} data-workflow-id={item.id}><span className={`afw-dot ${item.status}`} /><div><strong>{workflowName(item)}</strong><small>{item.request.target_accession} · {tr(STATUS[item.status] || item.status)}<br />{tr(dateLabel(item.created))}</small></div><ChevronRight size={13} /></button>))}</div>{tr(!items.length && <p className="afw-muted">{tr(historyLoading ? "저장된 요청을 불러오는 중입니다." : "저장된 요청이 없습니다. AlphaFold 스튜디오에서 성분과 표적을 선택해 요청하세요.")}</p>)}</> : <>{tr(jobsError && <p className="afw-error">{tr(jobsError)}</p>)}<p className="afw-muted">{tr("성분·표적 정보가 저장된 작업을 연결합니다. 이 동작은 새 계산을 실행하지 않습니다.")}</p><div className="afw-history-list" role="tabpanel" aria-label={tr("연결할 기존 AF3 작업")}>{tr(storedJobs.map(entry => <button key={entry.job.id} className="afw-history-item" onClick={() => void attach(entry)} disabled={!!busy || !online} title={`${entry.job.id}\n${entry.requested.canonical_smiles}`} data-existing-job-id={entry.job.id}>{tr(busy === entry.job.id ? <LoaderCircle size={15} className="afw-spinner" /> : <Link2 size={15} />)}<div><strong>{entry.requested.target_accession} · {tr(STATUS[entry.job.status] || entry.job.status)}</strong><small>{shortStructure(entry.requested.canonical_smiles)}<br />{tr(modeLabel(entry.requested.msa_mode))}<br />{tr(dateLabel(entry.job.created))} · {entry.job.id}</small></div></button>))}</div>{tr(!storedJobs.length && <p className="afw-muted">{tr("연결 가능한 스튜디오 작업이 없습니다.")}</p>)}</>)}
        <p className="afw-history-note">{tr("단계는 서버가 기록한 실행 사실입니다. 작업자는 입력·실행·검증을 처리하는 소프트웨어이며, LLM 해석과 구분합니다.")}</p>
      </aside>
      <div className="afw-main" aria-busy={loading}>
        {tr(notice && <p className="afw-notice" role="status">{tr(notice)}</p>)}{tr(mutationError && <p className="afw-error" role="alert">{tr(mutationError)}</p>)}{tr(statusError && <p className="afw-error">{tr("상태 조회: ")}{tr(statusError)}</p>)}
        {tr(!workflow ? <section className="afw-empty afw-card"><div className="afw-empty-icon">{tr(loading ? <LoaderCircle size={33} className="afw-spinner" /> : <Workflow size={35} />)}</div><h3>{tr(loading ? "저장된 실행에 연결하고 있습니다." : "성분과 표적에서 시작하는 구조 예측")}</h3><p>{tr(loading ? "요청과 계산 기록을 확인합니다. 새 계산은 제출하지 않습니다." : "AlphaFold 스튜디오에서 요청을 보내거나 왼쪽의 기존 AF3 작업을 연결하세요. 이후 과정은 이 화면에서 이어집니다.")}</p><div className="afw-empty-flow"><span>{tr("입력 준비")}</span><ArrowRight size={13} /><span>{tr("MSA·AF3 실행")}</span><ArrowRight size={13} /><span>{tr("출력 검토")}</span></div></section> : <>
          <section className="afw-overview afw-card" data-testid="af3-workflow-detail" data-workflow-id={workflow.id} data-workflow-status={workflow.status} data-job-id={workflow.job_id || ""}>
            <div className="afw-overview-top"><div><span className="afw-eyebrow">SAVED REQUEST · EXACT IDENTITY</span><h3>{workflowName(workflow)} <span aria-hidden="true">×</span> {workflow.request.target_accession}</h3><p>{tr(modeLabel(workflow.request.msa_mode))} {tr(" · 시드 ")}{tr(workflow.request.seeds.join(", "))}</p></div><span className={`afw-status ${workflow.status}`} role="status">{tr(active ? <LoaderCircle className="afw-spinner" size={13} /> : workflow.status === "completed" ? <Check size={13} /> : ["blocked", "failed", "interrupted"].includes(workflow.status) ? <CircleAlert size={13} /> : <Clock3 size={13} />)}{tr(STATUS[workflow.status] || workflow.status)}</span></div>
            <dl className="afw-facts"><div><dt>{tr("워크플로 ID")}</dt><dd>{workflow.id}</dd></div><div><dt>{tr("AF3 작업 ID")}</dt><dd>{jobId || tr("입력 준비 후 연결")}</dd></div><div><dt>{tr("요청 접수")}</dt><dd>{tr(dateLabel(workflow.created))}</dd></div><div><dt>{tr("서버 기록 갱신")}</dt><dd>{tr(dateLabel(workflow.updated))}</dd></div><div><dt>{tr("화면 최근 수신")}</dt><dd>{tr(dateLabel(checkedAt))}</dd></div><div><dt>{tr("결과 연결 상태")}</dt><dd>{tr(resultReady ? "성분·표적 일치 확인" : "검증된 출력 연결 대기")}</dd></div></dl>
            <details className="afw-exact"><summary>{tr("요청의 정확한 분자 구조 · SMILES")}</summary><code>{workflow.request.canonical_smiles || workflow.request.smiles}</code></details>
            {tr(workflow.request.msa_mode === "none" && <p className="afw-caution">{tr("MSA·템플릿을 생략한 탐색 계산입니다. 완료 여부와 별개로 구조 정확도와 결합·효능은 검증되지 않았습니다.")}</p>)}
            {tr(workflow.actions.can_resume && <><p className="afw-muted">{tr(resumeDescription)}</p>{tr(workflow.request.msa_mode === "none" && <label className="afw-ack"><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} data-testid="af3-workflow-exploratory-ack" /><span>{tr("MSA·템플릿 생략과 정확도 제한을 확인했으며, 이 탐색 조건으로 재개합니다.")}</span></label>)}</>)}
            <div className="afw-actions">{tr(workflow.actions.can_resume && <button className="afw-primary" onClick={() => void resume()} disabled={!canResume} data-testid="af3-workflow-resume">{tr(busy === "resume" ? <LoaderCircle size={15} className="afw-spinner" /> : <Play size={15} />)}{tr(resumeLabel)}</button>)}{tr(resultReady && <button className="afw-primary" onClick={() => { if (trustedAF3WorkflowResult(workflow)) callbacks.current.onViewResult(workflow); }} data-testid="af3-workflow-view-result"><Atom size={16} />{tr("이 작업의 구조를 스튜디오에서 보기")}<ArrowRight size={15} /></button>)}<button className="afw-secondary" onClick={() => download(workflow, `${workflow.id}-af3-workflow.json`)}><ArrowDownToLine size={14} />{tr("기록 JSON")}</button></div>
            {tr(workflow.status === "completed" && !resultReady && <p className="afw-caution">{tr("계산 종료 기록과 사용 가능한 예측 구조는 다릅니다. 이 요청의 성분·표적과 실행이 확인된 출력이 없어 구조로 이동할 수 없습니다.")}</p>)}
            {tr(resultReady && <p className="afw-muted">{tr("선택 성분·표적의 출력 일치를 확인했습니다. 이는 구조 정확도, 결합 친화도, 효능 또는 안전성 검증이 아닙니다.")}</p>)}
          </section>
          <section className="afw-progress afw-card"><div className="afw-heading"><h3>{tr("단계별 처리 과정")}</h3><span className="afw-status">{tr(workflow.stages.filter(stage => stage.status === "completed").length)} / {tr(workflow.stages.length)} {tr(" 단계 완료")}</span></div><ol className="afw-timeline" aria-label={tr("AF3 실제 실행 단계")}>{tr(workflow.stages.map((stage, index) => { const info = STAGES[stage.id]; const Icon = info?.icon || Workflow; const duration = stageDuration(stage); return <li key={stage.id} className={stage.status} data-stage={stage.id} data-status={stage.status}><div className="afw-stage-marker" aria-hidden="true">{tr(stage.status === "completed" ? <Check size={17} /> : stage.status === "running" ? <Icon size={17} /> : <span>{tr(String(index + 1).padStart(2, "0"))}</span>)}</div><div className="afw-stage-body"><div className="afw-stage-heading"><h4>{tr(info?.label || stage.label)}</h4><span>{tr(STATUS[stage.status] || stage.status)}</span></div><span className="afw-stage-agent"><Workflow size={10} />{tr(info?.agent || stage.agent)}</span><p>{tr(stageSummary(stage))}</p>{tr((stage.started_at || stage.finished_at) && <small className="afw-stage-timing">{stage.started_at ? msg("시작 {0}", dateLabel(stage.started_at)) : ""}{stage.finished_at ? ` ${msg("· 종료 {0}", dateLabel(stage.finished_at))}` : ""}{tr(duration ? ` · ${tr(duration)}` : "")}</small>)}</div></li>; }))}</ol><p className="afw-progress-caption">{tr("생략·재사용과 완료를 구분합니다. 기록이 없는 중간 단계는 추정해서 완료로 표시하지 않습니다.")}</p>{tr(prediction && <div className="afw-process-evidence"><AF3StageEvidence entry={prediction} live={ACTIVE.has(prediction.job.status)} checkedAt={checkedAt} /></div>)}</section>
          {tr((blockers.length > 0 || workflow.error || (["blocked", "failed", "interrupted"].includes(workflow.status) && workflow.actions.reason) || workflow.recovery_candidates.length > 0) && <section className="afw-diagnostics afw-card"><div className="afw-blockers"><h4><CircleAlert size={17} />{tr("실행 조건과 복구 확인")}</h4>{tr(blockers.length > 0 && <ul>{tr(blockers.map((value, index) => <li key={index}>{tr(diagnosticText(value))}</li>))}</ul>)}{tr(workflow.error && <p className="afw-error">{tr(diagnosticText(workflow.error))}</p>)}{tr(workflow.actions.reason && <p className="afw-muted">{tr(workflow.actions.reason)}</p>)}{tr(workflow.recovery_candidates.length > 0 && <p className="afw-caution">{tr("연결 여부를 확인할 기존 작업이 ")}{tr(workflow.recovery_candidates.length)}{tr("개 있습니다. ‘기존 AF3 작업’에서 ID와 성분·표적을 확인한 뒤 연결하세요.")}</p>)}{tr(blockers.some(value => diagnosticText(value) !== value) && <details className="afw-exact"><summary>{tr("실행 환경의 원문 진단")}</summary><code>{blockers.join("\n")}</code></details>)}</div></section>)}
          {tr(jobId && <section className="afw-log"><div className="afw-heading"><h3><Terminal size={16} />{tr("최근 실행 로그")}</h3><button className="afw-text-button" onClick={refreshStatus} disabled={!online}><RefreshCw size={13} />{tr("조회")}</button></div>{tr(logError && <p className="afw-caution">{tr("로그 조회가 지연되거나 제공되지 않습니다. 단계 상태 조회는 계속됩니다. ")}{tr(logError)}</p>)}<pre tabIndex={0} aria-label={tr("실제 AF3 실행 로그")} data-testid="af3-workflow-log">{log?.text || tr("기록된 실행 로그가 아직 없습니다.")}</pre><small>{tr(log?.truncated ? "최근 64 KiB 분량을 표시합니다. 전체 내용은 작업 원본 로그에 남습니다." : "서버 run.log의 실제 기록입니다.")} {tr(" 로그가 잠시 늘지 않아도 실패로 확정하지 않습니다.")}</small></section>)}
          <section className="afw-events afw-card"><details><summary>{tr("요청·작업 연결 이벤트 ")}{tr(workflow.events.length)}{tr("개")}</summary><ol className="afw-event-list">{tr(workflow.events.map(event => <li key={event.seq}><time dateTime={event.time}>{tr(dateLabel(event.time))}</time><span>{tr(event.message)}</span></li>))}</ol></details></section>
        </>)}
      </div>
    </div>
  </div>;
}
