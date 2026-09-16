import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import {
  ArrowDownToLine, ArrowLeft, ArrowRight, Binary, Check, ChevronLeft,
  ChevronRight, CircuitBoard, Clock3, Cpu, Database, FileSearch, FlaskConical,
  History, Layers3, LoaderCircle, RefreshCw, Search, SlidersHorizontal, Target,
} from "lucide-react";
import { api, download } from "../lib/api";
import { quantumMethod, quantumNumber, quantumOrigin, readQuantumSelection, saveQuantumSelection } from "../lib/quantumEvidence";
import type { Analysis, Compound, Job } from "../types/app";
import type { QuantumResult } from "../types/quantum";
import QuantumPanel, { AnalysisQuantumWorkspace } from "./QuantumPanel";
import "./quantum-studio.css";
import QuantumJourney, { QuantumHeroGraphic } from "./QuantumJourney";

export type QuantumStudioProps = {
  analyses: Analysis[];
  activeAnalysis: Analysis | null;
  jobs: Job[];
  onSelectAnalysis: (id: string) => void;
  onRefreshJobs: () => void | Promise<void>;
  onNotify: (text: string, error?: boolean) => void;
};

type View = "analysis" | "history";
type Filter = "all" | "projected" | "fidelity" | "pending";
const viewStorageKey = "herbfold.quantum.studio.view";
const restoredView = (): View => {
  try { return sessionStorage.getItem(viewStorageKey) === "analysis" ? "analysis" : "history"; }
  catch { return "history"; }
};
const activeStatuses = new Set(["submitted", "submitting", "running", "queued"]);
const labels: Record<string, string> = {
  completed: "완료", running: "진행 중", queued: "대기 중", submitted: "IBM 제출됨",
  submitting: "제출 처리 중", failed: "실패", partial_submission: "일부 제출",
  submission_failed: "제출 실패", cancelled: "취소됨", blocked: "실행 차단",
  off: "양자 단계 생략", prepared: "준비됨", interrupted: "중단됨",
};
const columns: Record<string, string> = {
  molecular_weight: "분자량", logp: "LogP", tpsa: "TPSA", hbd: "H-bond donor",
  hba: "H-bond acceptor", qed: "QED",
};
const date = (value?: string) => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "시간 미기록";
const methodLabel = (value?: QuantumResult | null) => !value || value.status === "off" ? "양자 결과 없음"
  : quantumMethod(value) === "projected" ? "관측량 기반" : "전역 Fidelity";
const originLabel = (value?: QuantumResult | null) => !value ? "측정 미기록"
  : quantumOrigin(value) === "hardware" ? "IBM 실측" : quantumOrigin(value) === "local" ? "로컬 계산" : "실행 근거 확인 전";
const compoundsOf = (analysis?: Analysis | null): Compound[] => {
  const rows = [...(Array.isArray(analysis?.result?.candidates) ? analysis.result.candidates : []),
    ...(Array.isArray(analysis?.request?.compounds) ? analysis.request.compounds : [])];
  return [...new Map(rows.filter(row => typeof row?.id === "string").map(row => [row.id, row])).values()];
};
const jobValue = (job?: Job | null): QuantumResult | null => job ? {
  ...job.result, status: job.result?.status || job.status, mode: job.result?.mode || job.payload?.mode,
  sample_ids: job.result?.sample_ids || job.payload?.sample_ids,
  sample_labels: job.result?.sample_labels || job.payload?.sample_labels,
  source_analysis_id: job.result?.source_analysis_id || job.payload?.source_analysis_id,
} : null;
const isRows = (rows: unknown): rows is number[][] => Array.isArray(rows) && rows.length > 0
  && rows.every(row => Array.isArray(row) && row.length === rows[0].length
    && row.length > 0 && row.every(value => typeof value === "number" && Number.isFinite(value)));

function InputEvidence({ value, source, job }: { value: QuantumResult | null; source: Analysis | null; job: Job | null }) {
  const [columnOffset, setColumnOffset] = useState(0);
  const identity = job?.id || source?.id || "";
  useEffect(() => setColumnOffset(0), [identity]);
  const compounds = compoundsOf(source);
  const ids = value?.sample_ids || [];
  const definition = value?.feature_definition;
  const actualRows = job?.payload?.features || definition?.features;
  const rows = isRows(actualRows) ? actualRows : null;
  const matchesDefinition = rows && definition && JSON.stringify(rows) === JSON.stringify(definition.features);
  const names = rows ? Array.from({ length: rows[0].length }, (_, i) => matchesDefinition
    ? columns[definition.columns?.[i]] || definition.columns?.[i] || `특징 ${i + 1}` : `특징 ${i + 1}`) : [];
  const target = source?.request?.target_id || job?.payload?.target_id;
  const plan = value?.plan || value?.metadata;
  const label = (index: number) => value?.sample_labels?.[index]
    || compounds.find(row => row.id === ids[index])?.name_ko
    || compounds.find(row => row.id === ids[index])?.name || ids[index] || `입력 ${index + 1}`;
  const count = rows?.length || ids.length;
  return <section className="qs-input-card" data-testid="quantum-studio-input">
    <header className="qs-card-heading"><div><span className="qs-kicker">INPUT IDENTITY</span><h3>비교한 입력과 연구 표적</h3></div><span className="qs-subtle-badge"><Database size={14} />{count ? `${count}개 입력` : "입력 미기록"}</span></header>
    <div className="qs-input-context"><div><Target size={17} /><span><small>원본 분석의 표적</small><strong>{target || "표적 미기록"}</strong></span></div><p>표적은 연구 맥락입니다. 아래 분자 기술자의 양자 커널이 단백질 결합을 직접 측정하는 것은 아닙니다.</p></div>
    {count > 0 && <ol className="qs-molecules">{Array.from({ length: count }, (_, i) => {
      const molecule = compounds.find(row => row.id === ids[i]);
      return <li key={`${ids[i] || "row"}-${i}`}><span className="qs-molecule-index">M{i + 1}</span><div><strong>{label(i)}</strong><small>{ids[i] || "분자 ID 미기록"}</small>{molecule?.smiles && <code title={molecule.smiles}>{molecule.smiles}</code>}</div></li>;
    })}</ol>}
    {rows ? <details className="qs-input-table" open><summary>실제로 전달된 특징 행렬 · {rows.length} × {names.length}</summary>
      <p>회로 인코딩 전 입력값입니다.{matchesDefinition ? " 열별 전처리 제수는 표 머리글에 표시합니다." : " 열 이름·전처리 정의가 저장되지 않은 입력은 임의로 추정하지 않습니다."}</p>
      <div className="qs-table-scroll" tabIndex={0} aria-label="양자 입력 특징 표 가로 스크롤"><table><thead><tr><th scope="col">입력 분자</th>{names.slice(columnOffset, columnOffset + 8).map((name, index) => <th scope="col" key={columnOffset + index}>{name}{matchesDefinition && typeof definition.divisors?.[columnOffset + index] === "number" && <small>원값 ÷ {quantumNumber(definition.divisors?.[columnOffset + index])}</small>}</th>)}</tr></thead><tbody>{rows.map((row, i) => <tr key={i}><th scope="row"><b>M{i + 1}</b><span>{label(i)}</span></th>{row.slice(columnOffset, columnOffset + 8).map((value, j) => <td key={j}>{quantumNumber(value)}</td>)}</tr>)}</tbody></table></div>
      {names.length > 8 && <div className="qs-column-pagination"><button onClick={() => setColumnOffset(Math.max(0, columnOffset - 8))} disabled={columnOffset === 0} aria-label="이전 입력 특징"><ChevronLeft size={16} /></button><span>특징 {columnOffset + 1}–{Math.min(columnOffset + 8, names.length)} / {names.length}</span><button onClick={() => setColumnOffset(columnOffset + 8)} disabled={columnOffset + 8 >= names.length} aria-label="다음 입력 특징"><ChevronRight size={16} /></button></div>}
    </details> : <p className="qs-empty-copy">이 기록에는 입력 특징 행렬이 저장되지 않았습니다. 기록된 결과와 출처만 확인할 수 있습니다.</p>}
    <dl className="qs-identity"><div><dt>특징 SHA-256</dt><dd>{plan?.feature_sha256 || "미기록"}</dd></div><div><dt>원본 분석 ID</dt><dd>{source?.id || value?.source_analysis_id || "연결된 원본 분석 없음"}</dd></div></dl>
  </section>;
}

export default function QuantumStudio({ analyses, activeAnalysis, jobs, onSelectAnalysis, onRefreshJobs, onNotify }: QuantumStudioProps) {
  const reducedMotion = useReducedMotion();
  const [view, setView] = useState<View>(restoredView);
  const [analysisId, setAnalysisId] = useState(activeAnalysis?.id || analyses[0]?.id || "");
  const [history, setHistory] = useState<Job[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(() => readQuantumSelection("studio-job"));
  const [source, setSource] = useState<Analysis | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(false);
  const [refreshingJob, setRefreshingJob] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const selectedRef = useRef(selectedJobId); selectedRef.current = selectedJobId;
  useEffect(() => { try { sessionStorage.setItem(viewStorageKey, view); } catch { /* Selection storage is optional. */ } }, [view]);
  useEffect(() => { if (selectedJobId) saveQuantumSelection("studio-job", selectedJobId); }, [selectedJobId]);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const allAnalyses = useMemo(() => [...new Map([
    ...analyses, ...(activeAnalysis ? [activeAnalysis] : []),
  ].map(item => [item.id, item])).values()].sort((a, b) => b.created.localeCompare(a.created)), [analyses, activeAnalysis]);
  const selectedAnalysis = allAnalyses.find(item => item.id === analysisId) || null;
  useEffect(() => {
    // A restored parent selection and the list can arrive in the same render.
    // Resolve them in one update so the newest-record fallback cannot overwrite it.
    setAnalysisId(current => activeAnalysis?.id || current || allAnalyses[0]?.id || "");
  }, [activeAnalysis?.id, allAnalyses]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setHistoryError(null);
    void api<{ items: Job[] }>("/quantum/results", undefined, { signal: controller.signal }).then(result => {
      if (controller.signal.aborted) return;
      setHistory(result.items);
      setSelectedJobId(previous => result.items.some(job => job.id === previous) ? previous : result.items[0]?.id || null);
    }).catch((error: Error) => { if (!controller.signal.aborted) setHistoryError(error.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision]);
  const quantumJobs = useMemo(() => {
    const merged = new Map<string, Job>();
    [...jobs.filter(job => job.kind === "quantum"), ...history].forEach(job => {
      const existing = merged.get(job.id);
      if (!existing || job.updated >= existing.updated) merged.set(job.id, job);
    });
    return [...merged.values()].sort((a, b) => b.created.localeCompare(a.created)).slice(0, 50);
  }, [jobs, history]);
  const selectedJob = quantumJobs.find(job => job.id === selectedJobId) || null;
  const archivedValue = jobValue(selectedJob);
  const sourceId = archivedValue?.source_analysis_id;
  const knownSource = allAnalyses.find(item => item.id === sourceId) || null;
  useEffect(() => {
    setSource(null); setSourceError(null);
    if (view !== "history" || !sourceId || knownSource) return;
    const controller = new AbortController();
    void api<Analysis>(`/analyses/${encodeURIComponent(sourceId)}`, undefined, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted && result.id === sourceId) setSource(result); })
      .catch((error: Error) => { if (!controller.signal.aborted) setSourceError(error.message); });
    return () => controller.abort();
  }, [view, sourceId, knownSource]);
  const archiveSource = knownSource || (source?.id === sourceId ? source : null);
  const currentSource = view === "analysis" ? selectedAnalysis : archiveSource;
  const currentValue: QuantumResult | null = view === "analysis" ? selectedAnalysis?.result?.quantum || null : archivedValue;
  const currentPlan = currentValue?.plan || currentValue?.metadata;
  const currentStatus = view === "analysis" ? currentValue?.status || selectedAnalysis?.status : selectedJob?.status;
  const compounds = compoundsOf(currentSource);
  const search = query.trim().toLocaleLowerCase();
  const matches = (value: QuantumResult | null, status: string) => filter === "all"
    || (filter === "pending" ? activeStatuses.has(status) : !!value && value.status !== "off" && quantumMethod(value) === filter);
  const visibleAnalyses = allAnalyses.filter(analysis => matches(analysis.result?.quantum, analysis.result?.quantum?.status || analysis.status)
    && [analysis.id, analysis.request?.target_id, analysis.request?.goal,
      ...compoundsOf(analysis).map(item => `${item.id} ${item.name} ${item.name_ko || ""}`)].join(" ").toLocaleLowerCase().includes(search));
  const visibleJobs = quantumJobs.filter(job => matches(jobValue(job), job.status)
    && [job.id, job.payload?.source_analysis_id, job.result?.plan?.backend_name,
      ...(job.payload?.sample_ids || []), ...(job.payload?.sample_labels || [])].join(" ").toLocaleLowerCase().includes(search));
  const refreshRecords = async () => {
    setRevision(value => value + 1);
    try { await onRefreshJobs(); } catch (error) { onNotify((error as Error).message, true); }
  };
  const refreshSelected = useCallback(async () => {
    if (!selectedJobId || refreshingJob) return;
    const id = selectedJobId; setRefreshingJob(true);
    try {
      const result = await api<Job>(`/quantum/${encodeURIComponent(id)}/refresh`, {});
      if (mounted.current) setHistory(previous => [result, ...previous.filter(job => job.id !== result.id)]);
    } catch (error) { if (mounted.current && selectedRef.current === id) onNotify(`기존 양자 작업 조회 실패: ${(error as Error).message}`, true); }
    finally { if (mounted.current) setRefreshingJob(false); }
  }, [selectedJobId, refreshingJob, onNotify]);
  const selectAnalysis = (id: string) => { setAnalysisId(id); setView("analysis"); onSelectAnalysis(id); };
  const currentId = view === "analysis" ? analysisId : selectedJobId;
  return <motion.div className="quantum-studio" data-testid="quantum-studio" initial={reducedMotion ? false : { opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .25 }}>
    <header className="qs-hero"><div className="qs-hero-main"><span className="qs-kicker"><CircuitBoard size={15} />QUANTUM STUDIO / VISUAL LAB</span><h2>분자 특징이 양자 회로를 만나면.</h2><p>입력에서 회로, 측정과 비교까지 — 계산의 흐름을 그림으로 읽어보세요.<br className="qs-desktop-break" /> 기록을 선택하고 5단계를 탐색하면 실제 수치와 연결됩니다.</p><button className="qs-hero-records" onClick={() => { setView("history"); setQuery(""); setFilter("all"); }}><History size={14} />저장된 양자 실행 살펴보기<ArrowRight size={13} /></button></div><QuantumHeroGraphic /></header>
    <div className="qs-scope-note"><FileSearch size={17} /><p>이 공간은 <strong>분자 특징과 계산 신뢰성을 검토하는 연구 도구</strong>입니다. 질병 진단이나 약효·안전성 판정은 제공하지 않습니다.</p></div>
    <div className="qs-layout"><aside className="qs-library" aria-label="양자 연구 기록 선택">
      <div className="qs-library-heading"><h3>연구 기록</h3><button className="qs-icon-button" onClick={() => void refreshRecords()} disabled={loading} aria-label="양자 스튜디오 기록 새로고침" data-testid="quantum-studio-refresh">{loading ? <LoaderCircle size={17} className="spin" /> : <RefreshCw size={17} />}</button></div>
      <div className="qs-view-switch" aria-label="기록 유형"><button aria-pressed={view === "analysis"} className={view === "analysis" ? "active" : ""} onClick={() => { setView("analysis"); setQuery(""); setFilter("all"); }} data-testid="quantum-studio-analysis-tab"><FlaskConical size={15} />분석별</button><button aria-pressed={view === "history"} className={view === "history" ? "active" : ""} onClick={() => { setView("history"); setQuery(""); setFilter("all"); }} data-testid="quantum-studio-history-tab"><History size={15} />양자 실행</button></div>
      <label className="qs-search"><Search size={16} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="분자·기록 ID 검색" aria-label="양자 기록 검색" data-testid="quantum-studio-search" />{query && <button onClick={() => setQuery("")} aria-label="양자 기록 검색 지우기">×</button>}</label>
      <label className="qs-filter">방법 / 상태<select value={filter} onChange={event => setFilter(event.target.value as Filter)} data-testid="quantum-studio-filter"><option value="all">전체</option><option value="projected">관측량 기반</option><option value="fidelity">전역 Fidelity</option><option value="pending">진행·대기 중</option></select></label>
      <p className="qs-list-caption">{view === "history" ? `최신 양자 실행 최대 50개 · ${visibleJobs.length}개 표시` : `불러온 분석 ${allAnalyses.length}개 · ${visibleAnalyses.length}개 표시`}</p>
      {historyError && view === "history" && <p className="qs-error" role="alert">양자 기록을 불러오지 못했습니다. {historyError}</p>}
      <div className="qs-record-list" data-testid="quantum-studio-records">
        {view === "analysis" ? visibleAnalyses.map(analysis => <button key={analysis.id} className={`qs-record ${analysis.id === analysisId ? "selected" : ""}`} aria-pressed={analysis.id === analysisId} onClick={() => selectAnalysis(analysis.id)} data-testid={`quantum-studio-analysis-${analysis.id}`}><span className="qs-record-top"><span>{analysis.request?.target_id || "표적 미기록"}</span>{analysis.id === analysisId && <Check size={15} />}</span><strong>{compoundsOf(analysis).slice(0, 2).map(item => item.name_ko || item.name || item.id).join(" · ") || analysis.request?.goal || "분석 기록"}</strong><span className="qs-record-method">{methodLabel(analysis.result?.quantum)}</span><span className="qs-record-bottom"><time>{date(analysis.created)}</time><span>{labels[analysis.status] || analysis.status}</span></span><code>{analysis.id.slice(0, 12)}</code></button>) : visibleJobs.map(job => <button key={job.id} className={`qs-record ${job.id === selectedJobId ? "selected" : ""}`} aria-pressed={job.id === selectedJobId} onClick={() => setSelectedJobId(job.id)} data-testid={`quantum-studio-job-${job.id}`}><span className="qs-record-top"><span>{originLabel(jobValue(job))}</span>{job.id === selectedJobId && <Check size={15} />}</span><strong>{job.payload?.sample_labels?.slice(0, 2).join(" · ") || job.payload?.sample_ids?.slice(0, 2).join(" · ") || "양자 특징 실행"}</strong><span className="qs-record-method">{methodLabel(jobValue(job))}</span><span className="qs-record-bottom"><time>{date(job.created)}</time><span>{labels[job.status] || job.status}</span></span><code>{job.id.slice(0, 12)}</code></button>)}
        {(view === "analysis" ? visibleAnalyses.length === 0 : visibleJobs.length === 0) && <div className="qs-list-empty"><Search size={22} /><strong>{loading && view === "history" ? "양자 기록 조회 중" : query || filter !== "all" ? "조건에 맞는 기록이 없습니다" : "저장된 기록이 없습니다"}</strong><p>{query || filter !== "all" ? "검색어와 필터를 바꿔 확인하세요." : view === "analysis" ? "분석에 저장된 양자 입력이 있으면 이곳에서 비교하고 별도 계산을 준비할 수 있습니다." : "기존 양자 실행은 이 목록에 표시됩니다."}</p></div>}
      </div>
    </aside><section className="qs-workarea" aria-label="선택한 양자 연구" data-testid="quantum-studio-workarea" data-record-id={currentId || ""}>
      {(view === "analysis" ? !!selectedAnalysis : !!selectedJob) ? <>
        <section className="qs-selection-summary"><div className="qs-selection-title"><div><span className="qs-kicker">{view === "analysis" ? "ANALYSIS WORKSPACE" : "EXECUTION ARCHIVE"}</span><h3>{view === "analysis" ? "원본 분석과 연결된 양자 계산" : "선택한 양자 실행"}</h3><code>{currentId}</code></div><span className={`qs-status ${activeStatuses.has(currentStatus || "") ? "is-running" : ""}`}>{activeStatuses.has(currentStatus || "") ? <LoaderCircle size={14} className="spin" /> : <Clock3 size={14} />}{labels[currentStatus || ""] || currentStatus || "결과 미기록"}</span></div>
          <dl className="qs-selected-metrics"><div><dt>계산 출처</dt><dd>{originLabel(currentValue)}</dd></div><div><dt>장비 / 큐빗</dt><dd>{currentPlan?.backend_name || "미기록"}<span>{quantumNumber(currentPlan?.n_qubits)} qubits</span></dd></div><div><dt>입력 / 측정 방식</dt><dd>{currentValue?.sample_ids?.length || "—"}개 입력<span>{methodLabel(currentValue)}</span></dd></div></dl>
          {view === "history" && selectedJob && <div className="qs-selection-actions">{sourceId && <button onClick={() => selectAnalysis(sourceId)} data-testid="quantum-studio-open-source"><ArrowLeft size={16} />원본 분석으로 돌아가기</button>}<button onClick={() => void refreshSelected()} disabled={refreshingJob} data-testid="quantum-studio-refresh-job">{refreshingJob ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}기존 작업 상태 조회</button><button onClick={() => download(selectedJob, `quantum-job-${selectedJob.id}.json`)}><ArrowDownToLine size={16} />실행 기록 JSON</button></div>}
          {sourceError && view === "history" && <p className="qs-error">연결된 분석을 불러오지 못했습니다. {sourceError}</p>}
        </section>

        <div className="qs-result-area" data-testid="quantum-studio-results">
          {view === "analysis" && selectedAnalysis ? currentValue && currentValue.status !== "off" ? <AnalysisQuantumWorkspace key={selectedAnalysis.id} analysis={selectedAnalysis} compounds={compounds} visualJourney /> : <div><QuantumJourney value={currentValue} compounds={compounds} /><div className="qs-result-empty"><Layers3 size={30} /><h3>{currentValue?.status === "off" ? "이 분석은 양자 단계를 생략했습니다" : "이 분석의 양자 결과가 아직 없습니다"}</h3><p>{activeStatuses.has(selectedAnalysis.status) ? "분석이 진행 중입니다. 원본 분석에 양자 결과가 저장되면 이곳에서 확인할 수 있습니다." : "양자 입력과 결과가 있는 분석을 선택하면 같은 분자로 별도 계산을 준비할 수 있습니다."}</p><button onClick={() => setView("history")}><History size={16} />저장된 양자 실행 확인</button></div></div> : <>{selectedJob?.error && <p className="qs-error" role="alert">{selectedJob.error}</p>}<QuantumPanel key={selectedJobId} value={archivedValue} compounds={compounds} title="실행 기록에 저장된 결과" visualJourney inputFeatures={selectedJob?.payload?.features} /></>}
        </div>
        <details className="qs-input-disclosure"><summary><Database size={15} />입력 분자·특징 행렬·출처 자세히 보기</summary><InputEvidence key={currentId} value={currentValue} source={currentSource} job={view === "history" ? selectedJob : null} /></details>
      </> : <section className="qs-welcome"><div className="qs-welcome-icon"><CircuitBoard size={38} /></div><span className="qs-kicker">READY TO INSPECT</span><h3>확인할 연구 기록을 선택하세요</h3><p>분석별 보기에서는 원본 입력과 연결된 계산을 함께 살펴봅니다.<br />양자 실행 보기에서는 로컬·IBM 결과를 각각 열어 비교합니다.</p><button onClick={() => { setView("history"); setQuery(""); setFilter("all"); }}><History size={17} />양자 실행 기록 보기<ArrowRight size={17} /></button></section>}
    </section></div>
  </motion.div>;
}
