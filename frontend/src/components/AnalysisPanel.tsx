import { tr, msg, localeCode } from "../lib/i18n";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowDownToLine,
  ArrowRight,
  Bot,
  Check,
  ChevronDown,
  CirclePause,
  Cpu,
  FlaskConical,
  GitBranch,
  Layers3,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  StopCircle,
  Workflow,
} from "lucide-react";
import { useState } from "react";
import type { Analysis, Compound } from "../types/app";
import { api, download } from "../lib/api";
import { useDialog } from "../lib/useDialog";
import { AnalysisQuantumWorkspace } from "./QuantumPanel";

const stageIcons: Record<string, any> = {
  coordinator: GitBranch,
  evidence: Layers3,
  chemistry: FlaskConical,
  candidate_design: Sparkles,
  structure: Workflow,
  quantum: Cpu,
  review: ShieldCheck,
  report: Bot,
};
const stageNames: Record<string, string> = {
  coordinator: "연구 계획",
  evidence: "근거 조사",
  chemistry: "분자 분석 단계",
  candidate_design: "후보 설계",
  structure: "구조 예측",
  quantum: "양자 특징",
  review: "교차 검토",
  report: "연구 보고서",
};
const statusNames: Record<string, string> = {
  pending: "대기",
  queued: "대기",
  running: "실행 중",
  completed: "완료",
  skipped: "생략",
  blocked: "확인 필요",
  cancelled: "취소됨",
  failed: "실패",
  interrupted: "중단",
};

export function AnalysisPanel({
  analyses,
  active,
  onSelect,
  onCreate,
  onUpdate,
  onCandidates,
  notify,
}: {
  analyses: Analysis[];
  active: Analysis | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onUpdate: (run: Analysis) => void;
  onCandidates: (items: Compound[]) => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function action(kind: "cancel" | "resume" | "quantum/refresh") {
    if (!active) return;
    setBusy(true);
    try {
      onUpdate(await api(`/analyses/${active.id}/${kind}`, {}));
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  const completed =
    active?.stages.filter(
      (stage) => stage.status === "completed" || stage.status === "skipped",
    ).length ?? 0;
  return (
    <div className="analysis-layout">
      <aside className="analysis-history card">
        <div className="card-heading">
          <span className="eyebrow">RESEARCH SESSIONS</span>
          <button
            className="icon-button"
            onClick={onCreate}
            aria-label={tr("새 분석 시작")}
          >
            <Sparkles size={17} />
          </button>
        </div>
        <h3>{tr("연구 세션")}</h3>
        <div className="analysis-list">
          {tr(analyses.map((run) => (
            <button
              key={run.id}
              className={`analysis-history-item ${run.id === active?.id ? "selected" : ""}`}
              onClick={() => onSelect(run.id)}
            >
              <span className={`status-dot ${run.status}`} />
              <div>
                <strong>
                  {tr(run.request?.goal?.slice(0, 34) || "분자 탐색 분석")}
                </strong>
                <small>
                  {tr(new Date(run.created).toLocaleString(localeCode(), {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  }))}{tr(" ")}
                  · {tr(run.mode === "astra" ? "GPT-6 Astra" : "Local workflow")}
                </small>
              </div>
              <ArrowRight size={14} />
            </button>
          )))}
        </div>
        {tr(!analyses.length && (
          <div className="empty-small">
            <GitBranch size={28} />
            <p>
              {tr("첫 연구 질문을 입력하면")}<br />
              {tr("에이전트들이 작업을 시작합니다.")}</p>
          </div>
        ))}
        <div className="small-callout">
          <ShieldCheck size={17} />
          <p>
            {tr("도구의 계산값과 LLM의 해석을 각각 기록합니다. 모든 단계에 입력과 실행 근거가 남습니다.")}</p>
        </div>
      </aside>
      <div className="analysis-main">
        {tr(!active ? (
          <div className="analysis-welcome card">
            <div className="orbit-mark">
              <GitBranch size={42} />
            </div>
            <span className="eyebrow">
              ONE QUESTION. A COORDINATED WORKFLOW.
            </span>
            <h2>
              {tr("연구 질문 하나에서")}<br />
              <em>{tr("다음 후보까지.")}</em>
            </h2>
            <p>
              {tr("계획, 분자 분석, 후보 설계, 구조·양자 계산, 교차 검토.")}<br />
              {tr("GPT-6 Astra가 각 단계의 근거를 연결합니다.")}</p>
            <button className="primary-button" onClick={onCreate}>
              <Sparkles size={16} /> {tr(" 새 분석 시작 ")}<ArrowRight size={16} />
            </button>
          </div>
        ) : (
          <>
            <div className="card analysis-overview">
              <div>
                <div className="eyebrow">
                  {tr(active.mode === "astra"
                    ? "GPT-6 ASTRA ORCHESTRATION"
                    : "LOCAL DETERMINISTIC WORKFLOW")}
                </div>
                <h2>{tr(active.request?.goal || "분자 탐색 분석")}</h2>
                <div className="run-metadata">
                  <span className={`status-pill ${active.status}`}>
                    {tr(statusNames[active.status] || active.status)}
                  </span>
                  <span>
                    {msg("{0} / {1} 단계", completed, active.stages.length)}</span>
                  <span>{msg("{0} LLM 호출", active.usage?.llm_calls ?? 0)}</span>
                  <span>
                    {tr((
                      (active.usage?.input_tokens || 0) +
                      (active.usage?.output_tokens || 0)
                    ).toLocaleString())}{tr(" ")}
                    tokens
                  </span>
                </div>
              </div>
              <div className="inline-actions">
                <button
                  className="icon-button"
                  title={tr("분석 JSON 내보내기")}
                  onClick={() => download(active, `analysis-${active.id}.json`)}
                >
                  <ArrowDownToLine size={18} />
                </button>
                {tr(["queued", "running"].includes(active.status) ? (
                  <button
                    className="secondary-button"
                    disabled={busy}
                    onClick={() => action("cancel")}
                  >
                    <StopCircle size={15} /> {tr(" 중단")}</button>
                ) : ["blocked", "failed", "cancelled", "interrupted"].includes(
                    active.status,
                  ) ? (
                  <button
                    className="secondary-button"
                    disabled={busy}
                    onClick={() => action("resume")}
                  >
                    <RefreshCw size={15} /> {tr(" 재개")}</button>
                ) : null)}
              </div>
            </div>
            {tr(active.error && (
              <div className="notice-inline warning">
                <CirclePause size={18} />
                <span>{tr(active.error)}</span>
              </div>
            ))}
            <div className="agent-pipeline card">
              <div className="section-heading">
                <div>
                  <span className="eyebrow">AGENT WORKFLOW</span>
                  <h3>{tr("협업하는 연구 에이전트")}</h3>
                </div>
                <span className="micro-label">CHECKPOINTED · TRACEABLE</span>
              </div>
              <div className="pipeline-grid">
                {tr(active.stages.map((stage, index) => {
                  const Icon = stageIcons[stage.id] || Bot;
                  return (
                    <motion.button
                      key={stage.id}
                      layout
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: index * 0.045 }}
                      className={`agent-node ${stage.status} ${expanded === stage.id ? "expanded" : ""}`}
                      onClick={() =>
                        setExpanded(expanded === stage.id ? null : stage.id)
                      }
                    >
                      <div className="node-top">
                        <span className="agent-symbol">
                          <Icon size={20} />
                        </span>
                        <span className="node-number">
                          {tr(String(index + 1).padStart(2, "0"))}
                        </span>
                      </div>
                      <strong>{tr(stageNames[stage.id] || stage.label)}</strong>
                      <small>{tr(stage.id.replace("_", " "))}</small>
                      <span className="node-status">
                        {tr(stage.status === "running" ? (
                          <LoaderCircle size={12} className="spin" />
                        ) : stage.status === "completed" ? (
                          <Check size={12} />
                        ) : (
                          <span className={`status-dot ${stage.status}`} />
                        ))}
                        {tr(statusNames[stage.status] || stage.status)}
                      </span>
                    </motion.button>
                  );
                }))}
              </div>
              <div className="pipeline-progress">
                <motion.span
                  animate={{
                    width: `${active.stages.length ? (completed / active.stages.length) * 100 : 0}%`,
                  }}
                  transition={{ duration: 0.5 }}
                />
              </div>
              <AnimatePresence>
                {tr(expanded && (
                  <motion.div
                    key={expanded}
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="stage-inspector"
                  >
                    <div className="section-heading">
                      <h4>{tr(stageNames[expanded])} {tr(" · 실행 결과")}</h4>
                      <button
                        className="icon-button"
                        onClick={() => setExpanded(null)}
                      >
                        <ChevronDown size={17} />
                      </button>
                    </div>
                    <pre>
                      {JSON.stringify(
                        active.stages.find((stage) => stage.id === expanded)
                          ?.result ??
                          active.stages.find((stage) => stage.id === expanded)
                            ?.error ?? { status: "대기 중" },
                        null,
                        2,
                      )}
                    </pre>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
            <div className="analysis-bottom-grid">
              <div className="card event-log">
                <div className="section-heading">
                  <h3>{tr("실시간 실행 기록")}</h3>
                  <span className="live-indicator">
                    <span className={`status-dot ${active.status}`} />
                    {tr(active.status === "running" ? "LIVE" : "RECORDED")}
                  </span>
                </div>
                <div className="event-scroll">
                  {tr([...(active.events || [])].reverse().map((event) => (
                    <motion.div
                      key={event.seq}
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      className="event-row"
                    >
                      <span className="event-time">
                        {tr(new Date(event.time).toLocaleTimeString(localeCode(), {
                          hour12: false,
                        }))}
                      </span>
                      <div>
                        <span className="event-stage">
                          {tr(stageNames[event.stage] || event.stage)}
                        </span>
                        <p>{tr(event.summary)}</p>
                        {tr(event.response_id && (
                          <small>
                            Response {tr(event.response_id.slice(0, 23))}…
                          </small>
                        ))}
                      </div>
                    </motion.div>
                  )))}
                </div>
              </div>
              <div className="card agent-report">
                <span className="eyebrow">RESEARCH OUTPUT</span>
                <h3>{tr("분석 보고서")}</h3>
                {tr(active.result ? (
                  <>
                    <ReportBody
                      value={active.result.report || active.result.review}
                    />
                    {tr(active.result.report?.stale && (
                      <p className="field-warning">
                        {tr(active.result.report.stale_reason ||
                          "하드웨어 결과가 갱신됐습니다. 기존 보고서는 갱신 전 상태에 대한 해석입니다.")}
                      </p>
                    ))}
                    {tr(active.request.quantum_mode === "ibm" &&
                      !["running", "queued"].includes(active.status) && (
                        <button
                          className="secondary-button"
                          disabled={busy}
                          onClick={() => action("quantum/refresh")}
                        >
                          <RefreshCw size={14} /> {tr(" IBM 제출 결과 조회")}</button>
                      ))}
                    <AnalysisQuantumWorkspace key={active.id} analysis={active} compounds={[...(active.result.candidates || []), ...(active.request.compounds || [])]} />
                    <div className="report-actions">
                      {tr(active.result.candidates?.length > 0 && (
                        <button
                          className="primary-button"
                          onClick={() => onCandidates(active.result.candidates)}
                        >
                          <FlaskConical size={15} /> {msg("후보 {0}개 탐색", active.result.candidates.length)}
                          <ArrowRight size={15} />
                        </button>
                      ))}
                      <button
                        className="text-button"
                        onClick={() =>
                          download(active.result, "research-report.json")
                        }
                      >
                        {tr("전체 결과 JSON ")}<ArrowDownToLine size={14} />
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="empty-small">
                    <Layers3 size={28} />
                    <p>
                      {tr("각 에이전트의 결과와")}<br />
                      {tr("검토가 끝나면 보고서가 생성됩니다.")}</p>
                  </div>
                ))}
              </div>
            </div>
          </>
        ))}
      </div>
    </div>
  );
}

function ReportBody({ value }: { value: any }) {
  if (!value) return <p className="muted">{tr("아직 종합 보고서가 없습니다.")}</p>;
  if (typeof value === "string") return <p className="report-text">{tr(value)}</p>;
  if (typeof value.summary === "string") {
    const decision =
      value.decision && typeof value.decision === "object"
        ? value.decision
        : value;
    return (
      <div className="report-readable human-report">
        {tr(value.summary
          .split(/\n+/)
          .filter(Boolean)
          .map((paragraph: string, i: number) => (
            <p key={i}>{tr(paragraph)}</p>
          )))}
        {tr(decision.risks?.length > 0 && (
          <div>
            <h4>{tr("검증이 필요한 한계")}</h4>
            <ul>
              {tr(decision.risks.map((risk: string, i: number) => (
                <li key={i}>{tr(risk)}</li>
              )))}
            </ul>
          </div>
        ))}
        {tr(decision.next_actions?.length > 0 && (
          <div>
            <h4>{tr("다음 연구 단계")}</h4>
            <ol>
              {tr(decision.next_actions.map((action: string, i: number) => (
                <li key={i}>{tr(action)}</li>
              )))}
            </ol>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="report-readable">
      {tr(Object.entries(value).map(([key, item]) => (
        <div key={key}>
          <h4>{tr(key.replaceAll("_", " "))}</h4>
          {tr(typeof item === "string" ? (
            <p>{tr(item)}</p>
          ) : Array.isArray(item) ? (
            <ul>
              {tr(item.map((entry, i) => (
                <li key={i}>
                  {tr(typeof entry === "string" ? entry : JSON.stringify(entry))}
                </li>
              )))}
            </ul>
          ) : (
            <pre>{JSON.stringify(item, null, 2)}</pre>
          ))}
        </div>
      )))}
    </div>
  );
}

export function AnalysisComposer({
  selected,
  onClose,
  onSubmit,
  sequence,
  setSequence,
  targetId,
  setTargetId,
  llmStatus,
  notify,
}: {
  selected: Compound[];
  onClose: () => void;
  onSubmit: (request: any) => Promise<void>;
  sequence: string;
  setSequence: (value: string) => void;
  targetId: string;
  setTargetId: (value: string) => void;
  llmStatus: any;
  notify: (value: string, error?: boolean) => void;
}) {
  const dialogRef = useDialog<HTMLElement>(onClose);
  const [goal, setGoal] = useState(
    "한약 유래 성분과 기존 약물을 비교하고 PTGS2 연구를 위한 후보 구조와 검증 계획을 제안해 주세요.",
  );
  const [mode, setMode] = useState("astra");
  const [maxCandidates, setMaxCandidates] = useState(4);
  const [runAF3, setRunAF3] = useState(false);
  const [msaMode, setMsaMode] = useState("search");
  const [quantumMode, setQuantumMode] = useState("local");
  const [busy, setBusy] = useState(false);
  const [fetching, setFetching] = useState(false);
  const hasDesignParents = selected.some((compound) => ["herbal", "natural_product"].includes(compound.category))
    && selected.some((compound) => compound.category === "drug");
  async function loadSequence() {
    setFetching(true);
    try {
      const data = await api(
        `/sources/uniprot/${encodeURIComponent(targetId)}`,
      );
      setSequence(data.sequence);
      notify(`${data.name} · ${data.sequence.length} aa 서열을 가져왔습니다.`);
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setFetching(false);
    }
  }
  async function submit() {
    setBusy(true);
    try {
      await onSubmit({
        goal,
        compounds: selected,
        protein_sequence: sequence.replace(/\s/g, ""),
        target_id: targetId,
        max_candidates: maxCandidates,
        mode,
        run_af3: runAF3,
        msa_mode: msaMode,
        quantum_mode: quantumMode,
        budgets: {
          max_llm_calls: 8,
          max_output_tokens: 1800,
          max_candidates: 12,
          max_af3_candidates: 1,
          max_qpu_jobs: 1,
          max_qpu_shots: 4096,
          max_qpu_seconds: 30,
        },
      });
      onClose();
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <motion.div
      className="modal-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onClose}
    >
      <motion.section
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={tr("새 에이전트 분석")}
        className="analysis-composer"
        initial={{ x: 60, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: 60, opacity: 0 }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="section-heading">
          <div>
            <span className="eyebrow">NEW RESEARCH SESSION</span>
            <h2 data-dialog-initial-focus tabIndex={-1}>
              {tr("무엇을 탐색할까요?")}</h2>
          </div>
          <button
            className="icon-button"
            aria-label={tr("분석 설정 닫기")}
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        <p className="muted">
          {tr("연구 목표와 입력을 확인하면 단계별 에이전트가 분석을 시작합니다.")}</p>
        <label>
          {tr("연구 질문")}<textarea
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            rows={4}
          />
        </label>
        <div className="selected-inputs">
          {tr(selected.map((m) => (
            <span key={m.id}>
              <span className={`tiny-type ${m.category}`} />
              {tr(m.name_ko || m.name)}
            </span>
          )))}
        </div>
        {tr(!hasDesignParents && (
          <p className="field-warning">
            {tr("후보 설계·에이전트 확장 분석에는 천연물과 기존 약물이 각각 필요합니다. 성분 비교 메뉴의 구조 비교는 같은 분류의 성분끼리도 사용할 수 있습니다.")}</p>
        ))}
        <div className="form-grid">
          <label>
            {tr("오케스트레이터")}<select
              value={mode}
              onChange={(event) => setMode(event.target.value)}
            >
              <option value="astra">{tr("GPT-6 Astra · LLM 분석")}</option>
              <option value="local">{tr("Local · 계산 흐름 검증")}</option>
            </select>
          </label>
          <label>
            {tr("생성 후보 수")}<input
              type="number"
              min={1}
              max={12}
              value={maxCandidates}
              onChange={(event) => setMaxCandidates(Number(event.target.value))}
            />
          </label>
        </div>
        <div className="model-info">
          <Bot size={17} />
          <div>
            <strong>{tr(mode === "astra" ? "gpt-6-astra" : "LLM 미사용")}</strong>
            <span>
              {tr(mode === "astra"
                ? "구조화된 계획 · 역할별 도구 · 교차 검토"
                : "결정적 도구 실행만 수행하는 로컬 모드")}
            </span>
          </div>
          <span
            className={`status-dot ${mode === "local" || llmStatus?.available ? "completed" : "blocked"}`}
          />
        </div>
        <div className="form-grid target-grid">
          <label>
            {tr("표적 UniProt ID")}<input
              value={targetId}
              onChange={(event) => setTargetId(event.target.value)}
            />
          </label>
          <button
            className="secondary-button"
            disabled={fetching}
            onClick={loadSequence}
          >
            {tr(fetching ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Layers3 size={15} />
            ))}{tr(" ")}
            {tr("서열 가져오기")}</button>
        </div>
        <label>
          {tr("단백질 서열")}{tr(" ")}
          <small>
            {tr(sequence.length
              ? `${sequence.replace(/\s/g, "").length} aa`
              : "구조 예측에 필요")}
          </small>
          <textarea
            className="sequence-input"
            rows={3}
            placeholder={tr("아미노산 서열")}
            value={sequence}
            onChange={(event) => setSequence(event.target.value)}
          />
        </label>
        <div className="compute-config">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={runAF3}
              onChange={(event) => setRunAF3(event.target.checked)}
            />
            <span>
              <strong>{tr("실제 AlphaFold 3 추론")}</strong>
              <small>{tr("선택하면 최대 1개 후보를 GPU에서 실행합니다.")}</small>
            </span>
          </label>
          <label>
            {tr("MSA / 템플릿")}<select
              value={msaMode}
              onChange={(event) => setMsaMode(event.target.value)}
            >
              <option value="search">{tr("전체 데이터베이스 검색")}</option>
              <option value="none">{tr("MSA 없음 · 탐색용")}</option>
            </select>
          </label>
          <label>
            {tr("양자 계산")}<select
              value={quantumMode}
              onChange={(event) => setQuantumMode(event.target.value)}
            >
              <option value="local">{tr("로컬 4큐빗 fidelity kernel")}</option>
              <option value="ibm">{tr("IBM 최대 가용 큐빗 · 실제 QPU")}</option>
              <option value="off">{tr("이번 분석에서 생략")}</option>
            </select>
          </label>
        </div>
        <p className="budget-note">
          {tr("분석당 최대 8 LLM 호출 · 응답당 1,800 토큰. IBM 선택 시 최대 1작업·4,096 shots·30 QPU초. 각 단계의 실제 사용량을 기록합니다.")}</p>
        <button
          className="primary-button wide"
          disabled={busy || !hasDesignParents || !goal.trim()}
          onClick={submit}
        >
          {tr(busy ? (
            <LoaderCircle size={17} className="spin" />
          ) : (
            <Sparkles size={17} />
          ))}{tr(" ")}
          {tr("에이전트 분석 시작 ")}<ArrowRight size={16} />
        </button>
      </motion.section>
    </motion.div>
  );
}
