import { tr, localeCode, msg } from "../lib/i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Check, CircleAlert, Cpu, FileCheck2, FolderOpen, LoaderCircle, Play, RefreshCw, Terminal } from "lucide-react";
import { api, download, fetchText } from "../lib/api";
import { pollPrediction, predictionRequest, PredictionRequestTimeout } from "../lib/predictionPolling";
import type { Compound } from "../types/app";
import type { PredictionEnvelope, PredictionLog, PredictionMode, PredictionSelection } from "../types/prediction";
import AF3EvidencePanel, { AF3StageEvidence } from "./AF3EvidencePanel";
import "./af3-calculation.css";

const ACTIVE_STATUSES = new Set(["queued", "running", "preparing", "submitted"]);
const RETRY_STATUSES = new Set(["failed", "blocked", "interrupted", "cancelled"]);
const labels: Record<string, string> = { prepared: "입력 준비됨", queued: "실행 대기", running: "계산 실행 중", preparing: "입력 확인 중", submitted: "제출됨", completed: "계산 완료", blocked: "실행 조건 미충족", failed: "계산 실패", interrupted: "작업 중단", cancelled: "취소됨" };
const modeLabel = (mode: PredictionMode) => mode === "search" ? "표준 · MSA 검색" : "탐색 · MSA·템플릿 없음";
const dateLabel = (value: string) => { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : date.toLocaleString(localeCode(), { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
const localApiPath = (url: string, fallback: string) => url.startsWith("/api/") && !url.startsWith("//") ? url.slice(4) : fallback;
const TEST_WEIGHT_BLOCKER = "AF3 parameters contain an all-zero model identifier, the upstream random/test-parameter marker. Supply verified trained parameters before prediction.";
const DATABASE_BLOCKER_KO = "표준 MSA 계산에 필요한 공식 유전정보·템플릿 데이터베이스를 AF3_DB_DIR에 설정해야 합니다.";
const blockerTranslations: Record<string, string> = {
  [TEST_WEIGHT_BLOCKER]: "현재 모델 파일은 무작위 시험용 가중치입니다. 정식 학습 가중치를 제공해야 예측을 실행할 수 있습니다.",
  "AF3_DB_DIR needs the official genetic/template databases for MSA search": DATABASE_BLOCKER_KO,
  "AF3_DB_DIR must be configured for MSA search": DATABASE_BLOCKER_KO,
  "AF3_DB_DIR needs the verified official genetic/template databases": "표준 MSA 계산에 필요한 공식 검색 데이터베이스와 설치 검증 기록을 AF3_DB_DIR에 설정해야 합니다.",
  "AF3_DB_DIR has no completed official_databases_manifest.json; database installation is incomplete or unverified": "공식 데이터베이스의 설치 완료·검증 기록이 없습니다. 전체 설치와 검증을 마친 뒤 실행할 수 있습니다.",
  "Official database installation has not completed validation": "공식 데이터베이스 설치 검증이 아직 완료되지 않았습니다.",
};

export default function AF3CalculationPanel({ compound, targetAccession, targetName, preferredJobId, refreshKey = 0, onResult, onJobsChanged, onArchive }: {
  compound: Compound;
  targetAccession: string;
  targetName?: string;
  preferredJobId?: string | null;
  refreshKey?: number;
  onResult: (jobId: string, selection: PredictionSelection) => void;
  onJobsChanged: () => void;
  onArchive: () => void;
}) {
  const [entries, setEntries] = useState<PredictionEnvelope[]>([]);
  const [entriesSelection, setEntriesSelection] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mode, setMode] = useState<PredictionMode>("search");
  const [seed, setSeed] = useState("1");
  const [acknowledged, setAcknowledged] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"prepare" | "execute" | "retry" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [refreshRequired, setRefreshRequired] = useState<"prepare" | "execute" | null>(null);
  const [log, setLog] = useState<PredictionLog | null>(null);
  const [logError, setLogError] = useState(false);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [refreshRevision, setRefreshRevision] = useState(0);
  const [listRevision, setListRevision] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const mounted = useRef(true);
  const mutationRevision = useRef(0);
  const mutationController = useRef<AbortController | null>(null);
  const selectionKey = `${compound.smiles}\u001f${targetAccession}`;
  const latestSelection = useRef(selectionKey);
  latestSelection.current = selectionKey;
  const active = entriesSelection === selectionKey ? entries.find((entry) => entry.job.id === selectedId) || null : null;
  const latestId = useRef(selectedId);
  latestId.current = selectedId;
  const reported = useRef<string | null>(null);
  const callbacks = useRef({ onResult, onJobsChanged });
  callbacks.current = { onResult, onJobsChanged };

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    ++mutationRevision.current;
    setBusy(null); setNotice(null); setRefreshRequired(null);
    return () => { ++mutationRevision.current; mutationController.current?.abort(); };
  }, [selectionKey]);
  const upsert = useCallback((entry: PredictionEnvelope) => {
    setEntriesSelection(latestSelection.current);
    setEntries((previous) => {
      const existing = previous.find((item) => item.job.id === entry.job.id);
      if (existing && existing.job.updated > entry.job.updated) return previous;
      return [entry, ...previous.filter((item) => item.job.id !== entry.job.id)].sort((a, b) => b.job.created.localeCompare(a.job.created));
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setEntries([]); setSelectedId(null); setLog(null); setError(null); setStatusError(null);
    void predictionRequest((signal) => api<{ items: PredictionEnvelope[] }>(`/molecular/predictions?smiles=${encodeURIComponent(compound.smiles)}&target_accession=${encodeURIComponent(targetAccession)}`, undefined, { signal }), { signal: controller.signal })
      .then(({ items }) => {
        if (controller.signal.aborted) return;
        const ordered = [...items].sort((a, b) => b.job.created.localeCompare(a.job.created));
        const chosen = ordered.find((entry) => ACTIVE_STATUSES.has(entry.job.status)) || ordered.find((entry) => entry.job.id === preferredJobId) || ordered[0];
        setEntries(ordered);
        setEntriesSelection(selectionKey);
        setRefreshRequired(null);
        if (chosen) { setSelectedId(chosen.job.id); setMode(chosen.requested.msa_mode); setSeed(String(chosen.requested.seeds[0] ?? 1)); }
      })
      .catch((problem: Error) => { if (!controller.signal.aborted) setError(`저장 작업을 불러오지 못했습니다. ${problem.message}`); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [compound.smiles, targetAccession, listRevision, refreshKey]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refreshVisible = () => {
      if (document.visibilityState !== "visible") return;
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (latestId.current) setRefreshRevision((value) => value + 1);
        else setListRevision((value) => value + 1);
      }, 50);
    };
    window.addEventListener("online", refreshVisible);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("online", refreshVisible);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, []);

  useEffect(() => {
    if (!selectedId || !active) return;
    const controller = new AbortController();
    const capturedSelection = selectionKey;
    const jobId = selectedId;
    const capturedMutation = mutationRevision.current;
    const current = () => latestSelection.current === capturedSelection && latestId.current === jobId && mutationRevision.current === capturedMutation;
    let shouldRepeatLog = ACTIVE_STATUSES.has(active?.job.status || "");
    pollPrediction({
      signal: controller.signal, isCurrent: current,
      request: (signal) => api<PredictionEnvelope>(`/molecular/predictions/${encodeURIComponent(jobId)}`, undefined, { signal }),
      onValue: (entry) => {
        upsert(entry);
        setCheckedAt(new Date().toISOString());
        shouldRepeatLog = ACTIVE_STATUSES.has(entry.job.status);
        setStatusError(null);
        if (entry.job.status !== "prepared") setRefreshRequired((previous) => previous === "execute" ? null : previous);
      },
      onError: (problem) => setStatusError(`현재 작업 상태를 확인하지 못했습니다. 자동으로 연결을 다시 확인합니다. 표시된 상태는 마지막으로 확인한 기록입니다. ${problem.message}`),
      shouldRepeat: (entry) => ACTIVE_STATUSES.has(entry.job.status),
    });
    pollPrediction({
      signal: controller.signal, isCurrent: current,
      request: (signal) => api<PredictionLog>(`/molecular/predictions/${encodeURIComponent(jobId)}/log`, undefined, { signal }),
      onValue: (value) => { setLog(value); setLogError(false); },
      onError: () => setLogError(true),
      shouldRepeat: () => shouldRepeatLog,
    });
    return () => controller.abort();
  }, [selectedId, active?.job.status, selectionKey, refreshRevision, upsert]);

  useEffect(() => { setLog(null); setLogError(false); setCheckedAt(null); setStatusError(null); }, [selectedId, selectionKey]);

  useEffect(() => {
    if (!active || active.job.status !== "completed") { reported.current = null; return; }
    const key = `${selectionKey}\u001f${active.job.id}\u001f${active.job.updated}`;
    if (reported.current === key) return;
    reported.current = key;
    callbacks.current.onResult(active.job.id, { smiles: compound.smiles, targetAccession });
    callbacks.current.onJobsChanged();
  }, [active?.job.id, active?.job.status, active?.job.updated, compound.smiles, targetAccession, selectionKey]);

  async function prepare(retry = false) {
    if (busy || refreshRequired) return;
    const modeToUse = retry && active ? active.requested.msa_mode : mode;
    const seeds = retry && active ? active.requested.seeds : [Number(seed)];
    if (!retry && (!seed.trim() || !Number.isInteger(seeds[0]) || seeds[0] < 0 || seeds[0] > 4294967295)) { setError("시드는 0부터 4,294,967,295까지의 정수여야 합니다."); return; }
    if (modeToUse === "none" && !retry && !acknowledged) { setError("탐색 모드의 MSA·템플릿 생략과 정확도 제한을 확인해 주세요."); return; }
    const captured = selectionKey;
    const revision = ++mutationRevision.current;
    const controller = new AbortController();
    mutationController.current = controller;
    const current = () => mounted.current && latestSelection.current === captured && mutationRevision.current === revision;
    setBusy(retry ? "retry" : "prepare"); setError(null); setNotice(null);
    setRefreshRevision((value) => value + 1);
    try {
      const entry = await predictionRequest((signal) => api<PredictionEnvelope>("/molecular/predictions/prepare", {
        smiles: compound.smiles, target_accession: targetAccession, msa_mode: modeToUse, seeds,
        exploratory_ack: modeToUse === "none" && (acknowledged || retry), retry,
      }, { signal }), { signal: controller.signal, timeoutMs: 60_000 });
      if (!current()) return;
      upsert(entry); setSelectedId(entry.job.id); setMode(entry.requested.msa_mode); setRefreshRevision((value) => value + 1);
      setNotice(entry.reused ? "동일한 성분·표적·계산 조건의 기존 작업에 연결했습니다." : retry ? "이전 기록을 보존하고 재시도 입력을 준비했습니다." : "선택한 성분과 표적의 입력을 준비했습니다. 실행 조건을 확인하고 계산을 시작하세요.");
      callbacks.current.onJobsChanged();
    } catch (problem) {
      if (!current()) return;
      if (problem instanceof PredictionRequestTimeout) {
        setRefreshRequired("prepare");
        setNotice("입력 준비 응답이 지연되고 있습니다. 서버에서 준비가 계속될 수 있으므로 작업 새로고침으로 접수된 기록을 확인해 주세요. 자동으로 다시 제출하지 않습니다.");
      } else setError((problem as Error).message);
    } finally { if (current()) { setBusy(null); mutationController.current = null; } }
  }

  async function execute() {
    if (!active || busy || refreshRequired || !active.readiness.runnable || active.job.status !== "prepared") return;
    const captured = selectionKey;
    const jobId = active.job.id;
    const revision = ++mutationRevision.current;
    const controller = new AbortController();
    mutationController.current = controller;
    const current = () => mounted.current && latestSelection.current === captured && mutationRevision.current === revision;
    setBusy("execute"); setError(null); setNotice(null);
    setRefreshRevision((value) => value + 1);
    try {
      const entry = await predictionRequest((signal) => api<PredictionEnvelope>(`/molecular/predictions/${encodeURIComponent(jobId)}/execute`, {}, { signal }), { signal: controller.signal, timeoutMs: 30_000 });
      if (!current()) return;
      upsert(entry); setSelectedId(entry.job.id); setRefreshRevision((value) => value + 1);
      setNotice("실제 AF3 계산을 제출했습니다. 다른 성분으로 이동해도 서버 작업은 유지됩니다.");
      callbacks.current.onJobsChanged();
    } catch (problem) {
      if (!current()) return;
      if (problem instanceof PredictionRequestTimeout) {
        setRefreshRequired("execute");
        setNotice("계산 제출 응답이 지연되고 있습니다. 서버 계산은 이미 시작되었을 수 있습니다. 다시 제출하기 전에 작업 새로고침으로 실행 상태를 확인해 주세요.");
        setRefreshRevision((value) => value + 1);
      } else setError((problem as Error).message);
    } finally { if (current()) { setBusy(null); mutationController.current = null; } }
  }

  async function exportInput() {
    if (!active) return;
    try { download(await fetchText(localApiPath(active.input_url, `/jobs/${active.job.id}/artifacts/input.json`)), `${active.job.id}-af3-input.json`); }
    catch (problem) { if (mounted.current) setError(`입력 파일을 불러오지 못했습니다. ${(problem as Error).message}`); }
  }

  const blockers = active?.readiness.blockers || [];
  const visibleBlockers = [...new Set(blockers.map((value) => blockerTranslations[value] || value))];
  const hasTranslatedBlockers = blockers.some((value) => !!blockerTranslations[value]);
  const repeatedJobError = !!active?.job.error && (blockers.includes(active.job.error) || active.job.error === blockers.join("; "));
  const parameterStatus = active?.readiness.parameter_status;
  const needsTrainedWeights = (parameterStatus && parameterStatus !== "unverified_parameters") || blockers.some((value) => /가중치|AF3_MODEL_DIR|model (parameters|weights)|trained.*(weights|parameters)|synthetic.*parameters|random.*parameters|all-zero.*identifier|random\/test-parameter/i.test(value));
  const warnings = [...new Set([...(active?.readiness.warnings || []), ...(active?.output_validation?.warnings || [])])];
  const live = !!active && ACTIVE_STATUSES.has(active.job.status);
  const invalidOutput = active?.job.status === "completed" && ["identity_failed", "unavailable"].includes(active.output_validation?.status || "");
  const logLines = log?.text.split("\n") || [];
  return <section className="afc-panel" data-testid="af3-calculation-panel" aria-busy={loading}>
    <header className="afc-heading"><div><span className="afc-eyebrow">{tr("선택 성분별 실제 구조 계산")}</span><h2 tabIndex={-1} data-testid="af3-calculation-heading">{tr(compound.name_ko || compound.name)} × {tr(targetName ? `${targetName} · ` : targetAccession === "P35354" ? "COX-2 · " : "")}{tr(targetAccession)}</h2><p>{tr("준비 → 실행 → 진행 확인 → 해당 작업의 예측 구조. 결합 친화도·약효는 별도 검증 대상입니다.")}</p></div><div className="afc-header-actions"><button className="afc-outline" onClick={() => setListRevision((value) => value + 1)} disabled={loading || !!busy} data-testid="studio-af3-list-refresh"><RefreshCw size={16} />{tr("작업 새로고침")}</button><button className="afc-outline" onClick={onArchive}><FolderOpen size={16} />{tr("연구 기록")}</button></div></header>
    {tr(loading ? <p className="afc-loading"><LoaderCircle size={18} className="spin" />{tr("이 성분·표적의 저장 작업을 연결하는 중")}</p> : <>
      <div className="afc-layout">
        <div className="afc-inputs">
          <h3>{tr("1. 계산 조건과 입력 준비")}</h3>
          <label className="afc-field">{tr("계산 모드")}<select value={mode} onChange={(event) => { setMode(event.target.value as PredictionMode); setAcknowledged(false); }} disabled={!!busy} data-testid="studio-af3-mode"><option value="search">{tr("표준 · MSA 검색")}</option><option value="none">{tr("탐색 · MSA·템플릿 없음")}</option></select></label>
          <p className="afc-mode-scope">{tr(mode === "search" ? "진화 정보와 템플릿 검색을 포함합니다. 공식 검색 데이터베이스와 실행 환경이 필요합니다." : "진화 정보와 템플릿을 생략하는 탐색 계산입니다. 낮은 신뢰도와 왜곡된 구조가 나올 수 있어 표준 결과와 구분합니다.")}</p>
          {tr(mode === "none" && <label className="afc-ack"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} data-testid="studio-af3-exploratory-ack" /><span>{tr("MSA·템플릿 생략과 정확도 제한을 확인했으며 탐색 계산을 선택합니다.")}</span></label>)}
          <label className="afc-field afc-seed">{tr("재현용 시드")}<input type="number" min="0" max="4294967295" step="1" value={seed} onChange={(event) => setSeed(event.target.value)} disabled={!!busy} data-testid="studio-af3-seed" /></label>
          <button className="afc-primary" disabled={!!busy || !!refreshRequired || (mode === "none" && !acknowledged)} onClick={() => void prepare()} data-testid="studio-af3-prepare">{tr(busy === "prepare" ? <LoaderCircle size={17} className="spin" /> : <FileCheck2 size={17} />)}{tr(busy === "prepare" ? "입력 준비 중" : "이 조건으로 입력 준비")}</button>
          <small className="afc-muted">{tr("실행 버튼을 누르면 선택 성분의 실제 AF3 작업을 제출합니다.")}</small>
        </div>
        <div className="afc-job" data-testid="studio-af3-job" data-job-id={active?.job.id || ""} data-job-status={active?.job.status || "none"}>
          <div className="afc-job-heading"><h3>{tr("2. 실행과 결과")}</h3>{tr(active && <button className="afc-icon" aria-label={tr("선택 AF3 작업 새로고침")} onClick={() => setRefreshRevision((value) => value + 1)} data-testid="studio-af3-refresh"><RefreshCw size={17} /></button>)}</div>
          {tr(entries.length > 1 && <label className="afc-field">{tr("이 성분의 계산 기록")}<select value={selectedId || ""} disabled={!!busy} onChange={(event) => { setSelectedId(event.target.value); setLog(null); setNotice(null); }} data-testid="studio-af3-job-select">{tr(entries.map((entry) => <option key={entry.job.id} value={entry.job.id}>{tr(dateLabel(entry.job.created))} · {tr(modeLabel(entry.requested.msa_mode))} · {tr(labels[entry.job.status] || entry.job.status)}</option>))}</select></label>)}
          {tr(!active ? <div className="afc-empty"><FileCheck2 size={28} /><p>{tr(error ? "저장 작업을 확인하지 못했습니다. 작업 새로고침으로 다시 조회하세요." : <>{tr("이 성분과 표적의 계산 기록이 없습니다.")}<br />{tr("입력을 준비하면 실행 가능 여부를 확인할 수 있습니다.")}</>)}</p></div> : <>
            <div className="afc-status" data-status={invalidOutput ? "failed" : active.job.status} role="status">{tr(live && !statusError ? <Cpu size={17} /> : statusError ? <CircleAlert size={17} /> : invalidOutput ? <CircleAlert size={17} /> : active.job.status === "completed" ? <Check size={17} /> : RETRY_STATUSES.has(active.job.status) ? <CircleAlert size={17} /> : <FileCheck2 size={17} />)}<strong>{tr(statusError ? msg("연결 재확인 중 · 마지막 상태: {0}", tr(labels[active.job.status] || active.job.status)) : invalidOutput ? "계산 종료 · 출력 검증 실패" : labels[active.job.status] || active.job.status)}</strong>{tr(active.queue_position != null && <span>{tr("대기 순번 ")}{tr(active.queue_position)}</span>)}</div>
            <p className="afc-job-scope" data-testid="studio-af3-job-mode">{tr(modeLabel(active.requested.msa_mode))} {tr(" · 시드 ")}{tr(active.requested.seeds.join(", "))}</p>
            <dl className="afc-job-facts"><div><dt>{tr("작업 ID")}</dt><dd>{tr(active.job.id)}</dd></div><div><dt>{tr("마지막 갱신")}</dt><dd>{tr(dateLabel(active.job.updated))}</dd></div></dl>
            {tr(active.requested.msa_mode === "none" && <p className="afc-caution">{tr("탐색용 예측입니다. 계산 완료 자체가 구조 정확도나 결합을 입증하지 않습니다.")}</p>)}
            {tr(parameterStatus === "test_parameters" && !blockers.includes(TEST_WEIGHT_BLOCKER) && <p className="afc-caution" data-testid="studio-af3-parameter-status">{tr("현재 파일은 성능 시험용 무작위 가중치로 판정되어 예측 실행이 차단됩니다.")}</p>)}
            <AF3StageEvidence entry={active} live={live} checkedAt={checkedAt} />
            {tr(blockers.length > 0 && <div className="afc-blockers" data-testid="studio-af3-blockers"><strong>{tr(needsTrainedWeights ? "학습된 AF3 가중치 필요" : "실행을 막는 조건")}</strong><ul>{tr(visibleBlockers.map((value, index) => <li key={index} data-testid={value === blockerTranslations[TEST_WEIGHT_BLOCKER] ? "studio-af3-parameter-status" : undefined}>{tr(value)}</li>))}</ul>{tr(hasTranslatedBlockers && <details className="afc-raw-diagnostics" data-testid="studio-af3-raw-diagnostics"><summary>{tr("원문 진단 ")}{tr(blockers.length)}{tr("개")}</summary><ul>{tr(blockers.map((value, index) => <li key={index}>{tr(value)}</li>))}</ul></details>)}</div>)}
            {tr(active.job.error && !repeatedJobError && <p className="afc-error" data-testid="studio-af3-job-error">{tr(active.job.error)}</p>)}
            {tr(active.output_validation?.status === "geometry_warning" && <p className="afc-caution" data-testid="studio-af3-quality-warning">{tr("출력 구조에 기하학적 품질 경고가 있습니다. 좌표와 경고를 검토하세요.")}</p>)}
            {tr(invalidOutput && <p className="afc-caution" data-testid="studio-af3-output-invalid">{tr(active.output_validation?.status === "identity_failed" ? "출력에서 선택 성분·표적의 일치를 확인하지 못해 예측 구조 표시가 차단되었습니다." : "출력 검증을 완료하지 못했습니다. 계산 종료와 사용 가능한 예측 결과를 구분해야 합니다.")}</p>)}
            {tr(active.output_validation?.status === "identity_verified_quality_unassessed" && <p className="afc-muted">{tr("출력의 성분·표적 일치는 확인했으며 구조 품질은 별도 평가가 필요합니다.")}</p>)}
            {tr(warnings.length > 0 && <details className="afc-warnings"><summary>{tr("실행·구조 해석 주의사항 ")}{tr(warnings.length)}{tr("개")}</summary><ul>{tr(warnings.map((value, index) => <li key={index}>{tr(value)}</li>))}</ul></details>)}
            <div className="afc-job-actions">
              {tr(active.job.status === "prepared" && <button className="afc-primary" onClick={() => void execute()} disabled={!!busy || !!refreshRequired || !!statusError || !active.readiness.runnable} data-testid="studio-af3-execute">{tr(busy === "execute" ? <LoaderCircle size={17} className="spin" /> : <Play size={17} />)}{tr(busy === "execute" ? "계산 제출 중" : "이 성분 계산 시작")}</button>)}
              {tr(RETRY_STATUSES.has(active.job.status) && <button className="afc-primary" onClick={() => void prepare(true)} disabled={!!busy || !!refreshRequired} data-testid="studio-af3-retry">{tr(busy === "retry" ? <LoaderCircle size={17} className="spin" /> : <RefreshCw size={17} />)}{tr("같은 조건으로 다시 준비")}</button>)}
              {tr(active.job.status === "completed" && <button className="afc-primary" onClick={() => onResult(active.job.id, { smiles: compound.smiles, targetAccession })} data-testid="studio-af3-view-result">{tr(invalidOutput ? "출력 검증 결과 확인" : "이 작업의 구조 보기")}<ArrowRight size={16} /></button>)}
              <button className="afc-outline" onClick={() => void exportInput()} data-testid="studio-af3-input">{tr("입력 JSON")}</button>
            </div>
            {tr(active.job.status === "prepared" && <small className="afc-muted">{tr(active.readiness.runnable ? "실행 조건이 확인되었습니다. 계산 시작을 누르면 작업을 제출합니다." : "실행 조건을 충족한 뒤 입력을 다시 준비하세요.")}</small>)}
            {tr(active.job.status === "completed" && <small className="afc-muted">{tr("완료된 작업의 성분·표적과 출력을 다시 확인한 뒤 위 뷰어에 표시합니다.")}</small>)}
          </>)}
        </div>
      </div>
      <AF3EvidencePanel entries={entries} active={active} onSelect={(id) => { setSelectedId(id); setLog(null); setNotice(null); }} />
      {tr(notice && <p className="afc-notice" role="status">{tr(notice)}</p>)}
      {tr(statusError && <div className="afc-error" role="alert" data-testid="studio-af3-status-error"><CircleAlert size={17} /><span>{tr(statusError)}</span></div>)}
      {tr(error && <div className="afc-error" role="alert" data-testid="studio-af3-error"><CircleAlert size={17} /><span>{tr(error)}</span></div>)}
      {tr(active && <details className="afc-log"><summary><Terminal size={16} />{tr("최근 실행 로그")}{tr(live && <span>{tr("자동 갱신")}</span>)}</summary><div data-testid="studio-af3-log">{tr(logError ? <p>{tr("로그 연결을 다시 확인하고 있습니다. 작업 상태는 로그와 별도로 갱신됩니다.")}</p> : log?.text ? <><pre>{logLines.slice(-160).join("\n")}</pre>{tr((log.truncated || logLines.length > 160) && <small>{tr("최근 로그 일부를 표시합니다. 전체 기록은 연구 기록의 run.log에서 확인하세요.")}</small>)}</> : <p>{tr("아직 실행 로그가 없습니다. 실제 계산이 시작되면 이곳에 표시됩니다.")}</p>)}</div></details>)}
    </>)}
  </section>;
}

