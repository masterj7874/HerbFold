import { tr, localeCode, getLanguage } from "../lib/i18n";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, FileCheck2, FolderOpen, GitBranch, LoaderCircle, RefreshCw } from "lucide-react";
import { api, ApiError } from "../lib/api";
import { rememberAF3Workflow } from "../lib/af3WorkflowSelection";
import { predictionRequest } from "../lib/predictionPolling";
import { clearPendingAF3, newRequestId, readPendingAF3, savePendingAF3, type AF3LaunchRequest } from "../lib/af3WorkflowSubmission";
import type { Compound } from "../types/app";
import type { PredictionEnvelope } from "../types/prediction";
import "./af3-calculation.css";
import "./af3-workflow-launcher.css";

type WorkflowReference = { id: string; request?: { request_id?: string } };
const STATUS: Record<string, string> = { prepared: "입력 준비됨", queued: "대기", running: "계산 중", completed: "계산 완료", blocked: "환경 확인 필요", failed: "실패", interrupted: "중단" };
export default function AF3WorkflowLauncher({ compound, targetAccession, targetName, preferredJobId, refreshKey = 0, onWorkflow, onOpenAgents }: {
  compound: Compound; targetAccession: string; targetName?: string; preferredJobId?: string | null; refreshKey?: number;
  onWorkflow: (id: string) => void; onOpenAgents: () => void;
}) {
  const [mode, setMode] = useState<"search" | "none">("search");
  const [seed, setSeed] = useState("1");
  const [ack, setAck] = useState(false);
  const [execute, setExecute] = useState(true);
  const [entries, setEntries] = useState<PredictionEnvelope[]>([]);
  const [selectedJob, setSelectedJob] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(readPendingAF3);
  const [revision, setRevision] = useState(0);
  const mounted = useRef(true);
  const mutation = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setListError(""); setEntries([]); setSelectedJob("");
    void predictionRequest(signal => api<{ items: PredictionEnvelope[] }>(`/molecular/predictions?smiles=${encodeURIComponent(compound.smiles)}&target_accession=${encodeURIComponent(targetAccession)}`, undefined, { signal }), { signal: controller.signal, timeoutMs: 20000 })
      .then(({ items }) => { if (!controller.signal.aborted) { setEntries(items); setSelectedJob(items.find(e => e.job.id === preferredJobId)?.job.id || items[0]?.job.id || ""); } })
      .catch(e => { if (!controller.signal.aborted) setListError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [compound.smiles, targetAccession, preferredJobId, refreshKey, revision]);

  async function submit(request: AF3LaunchRequest) {
    if (mutation.current) return;
    mutation.current = true; setBusy(true); setError("");
    setPending(request);
    const persisted = savePendingAF3(request);
    try {
      const workflow = await predictionRequest(signal => api<WorkflowReference>("/af3-workflows", request, { signal }), { timeoutMs: 15000 });
      rememberAF3Workflow(workflow.id);
      clearPendingAF3(request.request_id);
      if (mounted.current) { setPending(null); onWorkflow(workflow.id); }
    } catch (e) {
      if (e instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(e.status)) {
        clearPendingAF3(request.request_id);
        if (mounted.current) { setPending(null); setError(`요청이 접수되지 않았습니다. ${(e as Error).message}`); }
        return;
      }
      if (mounted.current) setError(`접수 결과를 확인하지 못했습니다. 요청 ID ${request.request_id}로 기록을 찾거나 같은 요청을 다시 접수할 수 있습니다. ${persisted ? "요청은 이 브라우저에 보관했습니다." : "브라우저 저장소를 사용할 수 없어 이 화면을 닫기 전에 요청 ID를 보관하세요."} ${(e as Error).message}`);
    } finally { mutation.current = false; if (mounted.current) setBusy(false); }
  }
  function start() {
    if (busy || pending) return;
    const number = Number(seed);
    if (!seed.trim() || !Number.isInteger(number) || number < 0 || number > 4294967295) { setError("시드는 0~4,294,967,295의 정수여야 합니다."); return; }
    if (mode === "none" && !ack) { setError("탐색 모드의 MSA·템플릿 생략을 확인해 주세요."); return; }
    void submit({ request_id: newRequestId(), compound: { id: compound.id, name: (compound.name_ko || compound.name).slice(0, 200), smiles: compound.smiles, category: compound.category }, target_accession: targetAccession, msa_mode: mode, seeds: [number], exploratory_ack: mode === "none" && ack, execute });
  }
  async function recoverPending() {
    if (!pending || mutation.current) return;
    mutation.current = true; setBusy(true); setError("");
    try {
      const workflow = await predictionRequest(signal => api<WorkflowReference>(`/af3-workflows/by-request/${encodeURIComponent(pending.request_id)}`, undefined, { signal }), { timeoutMs: 15000 });
      rememberAF3Workflow(workflow.id);
      clearPendingAF3(pending.request_id);
      if (mounted.current) { setPending(null); onWorkflow(workflow.id); }
    } catch { if (mounted.current) setError("아직 이 요청 ID의 접수 기록을 확인하지 못했습니다. 연결을 확인하거나 ‘같은 요청 다시 접수’를 누르세요. 같은 요청 ID는 중복 계산을 생성하지 않습니다."); }
    finally { mutation.current = false; if (mounted.current) setBusy(false); }
  }
  async function attach() {
    if (!selectedJob || mutation.current) return;
    mutation.current = true; setBusy(true); setError("");
    try {
      const workflow = await predictionRequest(signal => api<WorkflowReference>("/af3-workflows/attach", { job_id: selectedJob, compound: { id: compound.id, name: (compound.name_ko || compound.name).slice(0, 200), smiles: compound.smiles, category: compound.category }, target_accession: targetAccession }, { signal }), { timeoutMs: 15000 });
      rememberAF3Workflow(workflow.id);
      if (mounted.current) onWorkflow(workflow.id);
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { mutation.current = false; if (mounted.current) setBusy(false); }
  }
  return <section className="afc-panel af3-workflow-launcher" data-testid="af3-workflow-launcher">
    <header className="afc-heading"><div><span className="afc-eyebrow">ALPHAFOLD → AGENT WORKFLOW → STRUCTURE</span><h2 tabIndex={-1} data-testid="af3-calculation-heading">{getLanguage() === "en" ? compound.name || compound.name_ko : compound.name_ko || compound.name} × {tr(targetName || targetAccession)}</h2><p>{tr("조건을 선택하면 준비·실행·출력 검증은 에이전트 분석에서 이어집니다. 결과 구조는 이 스튜디오에서 확인합니다.")}</p></div><button className="afc-outline" onClick={onOpenAgents}><GitBranch size={16} />{tr("에이전트 분석 열기")}</button></header>
    <ol className="afw-launch-flow"><li><span>01</span>{tr("조건 선택")}</li><li><span>02</span>{tr("에이전트 준비·실행")}</li><li><span>03</span>{tr("출력 동일성 검증")}</li><li><span>04</span>{tr("스튜디오 결과 보기")}</li></ol>
    {tr(pending && <div className="afw-pending" role="status"><strong>{tr("접수 확인이 필요한 요청")}</strong><p>{pending.compound.name} · {tr(pending.target_accession)}</p><code>{tr(pending.request_id)}</code><div><button className="afc-outline" disabled={busy} onClick={() => void recoverPending()}><RefreshCw size={15} />{tr("접수된 작업 찾기")}</button><button className="afc-outline" disabled={busy} onClick={() => void submit(pending)}>{tr("같은 요청 다시 접수")}</button></div></div>)}
    <div className="afw-launch-layout"><fieldset disabled={busy || !!pending} className="afw-launch-form"><legend>{tr("계산 조건")}</legend><label className="afc-field">{tr("계산 모드")}<select value={mode} onChange={e => { setMode(e.target.value as "search" | "none"); setAck(false); }} data-testid="studio-af3-mode"><option value="search">{tr("표준 · MSA 검색")}</option><option value="none">{tr("탐색 · MSA·템플릿 없음")}</option></select></label><p className="afc-mode-scope">{tr(mode === "search" ? "공식 데이터베이스의 진화 정보·템플릿과 실행 환경을 먼저 확인합니다." : "MSA·템플릿을 생략하는 탐색 계산입니다. 표준 분석과 구분하며 구조 신뢰도는 효능을 뜻하지 않습니다.")}</p>{tr(mode === "none" && <label className="afc-ack"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} data-testid="studio-af3-exploratory-ack" /><span>{tr("MSA·템플릿 생략과 정확도 제한을 확인했습니다.")}</span></label>)}<div className="afw-launch-fields"><label className="afc-field">{tr("재현용 시드")}<input type="number" min={0} max={4294967295} step={1} value={seed} onChange={e => setSeed(e.target.value)} data-testid="studio-af3-seed" /></label><label className="afc-field">{tr("실행 범위")}<select value={execute ? "execute" : "prepare"} onChange={e => setExecute(e.target.value === "execute")}><option value="execute">{tr("준비 후 자동 실행")}</option><option value="prepare">{tr("입력 준비까지만")}</option></select></label></div><button className="afc-primary" onClick={start} disabled={busy || !!pending || (mode === "none" && !ack)} data-testid="studio-af3-workflow-start">{tr(busy ? <LoaderCircle size={17} className="spin" /> : <GitBranch size={17} />)}{tr(busy ? "워크플로우 접수 중" : execute ? "에이전트에서 준비·실행" : "에이전트에서 입력 준비")}<ArrowRight size={16} /></button><p className="afc-muted">{tr("접수 후 화면을 이동하거나 새로고침해도 서버의 작업 기록은 유지됩니다.")}</p></fieldset>
    <div className="afw-launch-existing"><div className="afw-launch-existing-title"><h3>{tr("이 성분의 저장 계산")}</h3><button className="afc-outline" aria-label={tr("저장된 AF3 계산 새로고침")} disabled={loading} onClick={() => setRevision(v => v + 1)}><RefreshCw size={15} /></button></div>{tr(loading ? <p role="status"><LoaderCircle size={16} className="spin" /> {tr(" 저장 기록을 찾고 있습니다.")}</p> : listError ? <p role="alert" className="afc-error">{tr(listError)}</p> : !entries.length ? <p className="afc-muted">{tr("선택한 성분·표적의 저장 계산이 없습니다. 새 워크플로우를 시작하세요.")}</p> : <><label className="afc-field">{tr("계산 기록")}<select value={selectedJob} onChange={e => setSelectedJob(e.target.value)} data-testid="studio-af3-job-select">{tr(entries.map(e => <option key={e.job.id} value={e.job.id}>{tr(new Date(e.job.created).toLocaleString(localeCode()))} · {tr(STATUS[e.job.status] || e.job.status)} · {tr(e.requested.msa_mode === "search" ? "표준" : "탐색")}</option>))}</select></label><p className="afc-muted"><Check size={15} />{tr("기존 작업 ID와 결과를 그대로 연결합니다.")}</p><button className="afc-outline" disabled={busy || !selectedJob} onClick={() => void attach()} data-testid="studio-af3-workflow-attach"><FolderOpen size={16} />{tr("에이전트에서 이어 보기")}<ArrowRight size={15} /></button></>)}<p className="afw-launch-note"><FileCheck2 size={16} />{tr("진행 로그·오류 원인·재연결·검증 결과는 에이전트 분석에 모입니다.")}</p></div></div>
    {tr(error && <p role="alert" className="afc-error">{tr(error)}</p>)}
  </section>;
}
