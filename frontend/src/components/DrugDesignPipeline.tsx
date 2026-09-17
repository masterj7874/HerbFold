import { tr, localeCode, msg, getLanguage } from "../lib/i18n";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  ArrowDownToLine, ArrowRight, Atom, Beaker, Check, CheckCircle2, ChevronDown,
  Clock3, Dna, ExternalLink, FlaskConical, GitBranch, GitCompareArrows, History,
  Layers3, Leaf, LoaderCircle, Microscope, Plus, RefreshCw, Search, ShieldCheck,
  SlidersHorizontal, Sparkles, Square, Workflow, X,
} from "lucide-react";
import { api, download, fetchText, formatNumber } from "../lib/api";
import { normalizeDiscoveryCompound, type DiscoveryCompound } from "../lib/discoveryCompounds";
import type { ProteinTarget } from "../lib/proteinTargets";
import type { Compound } from "../types/app";
import type { MolecularScene } from "../types/molecular";
import type { DesignCandidate, DesignEvidence, DesignInput, DesignMode, DesignOptions, DesignRun, DesignStage } from "../types/designPipeline";
import "./drug-design-pipeline.css";
import CombinationAssayPanel from "./CombinationAssayPanel";

const MoleculeViewer = lazy(() => import("./MoleculeViewer"));
type Props = { catalog: Compound[]; refreshKey?: number; onInspect: (compound: Compound, targetAccession?: string) => void; notify: (text: string, error?: boolean) => void };
type Herb = { name_ko: string; taxa?: string[]; source_url?: string };
type LibraryPage = { items: DiscoveryCompound[]; total: number; query_resolution?: { mapped: boolean; scope?: string } };
const MODES: Array<{ id: DesignMode; number: string; label: string; subtitle: string; text: string; icon: typeof Leaf }> = [
  { id: "combination", number: "01", label: "성분 조합", subtitle: "함께 검토할 성분", text: "각 분자의 구조를 유지한 조합을 만듭니다. 혼합물의 시너지나 효능은 별도 검증이 필요합니다.", icon: Layers3 },
  { id: "hybrid", number: "02", label: "하이브리드 설계", subtitle: "두 부모에서 새 구조로", text: "천연물과 약물의 BRICS 조각을 재조합하고, 부모 계보와 구조 필터를 확인합니다.", icon: GitBranch },
  { id: "transform", number: "03", label: "한약 성분 구조 변환", subtitle: "한 구조에서 유도체로", text: "선택한 천연물에 지정한 구조 변환을 적용합니다. 합성·안정성·약효는 실험으로 확인해야 합니다.", icon: FlaskConical },
];
const TRANSFORMS = [{ id: "o_methylation", label: "O-메틸화" }, { id: "o_acetylation", label: "O-아세틸화" }, { id: "stereoisomers", label: "입체이성질체 열거" }];
const ACTIVE = new Set(["queued", "running", "cancelling", "cancel_requested"]);
const STAGE_NAMES: Record<string, [string, string]> = { identity: ["구조 · 입력 검증", "분자 동일성 작업자"], design: ["후보 구조 설계", "분자 설계 작업자"], properties: ["계산 물성 비교", "물성 분석 작업자"], evidence: ["관측 근거 연결", "근거 조회 작업자"], review: ["검토 · 결과 정리", "과학적 검토 작업자"] };
function stageSummary(s: DesignStage): string {
  if (!s.summary) return "";
  const mappings: Record<string, string> = { "Waiting": "실행 대기 중", "Calculating descriptors and parent-relative changes": "분자 물성과 부모별 차이를 계산하고 있습니다.", "Joining exact candidate identities to cached observed assays": "동일한 구조의 저장 관측 근거를 연결하고 있습니다.", "Exact cached joins finished; parent observations were not transferred to modified structures": "동일 구조의 근거 연결 완료. 변형 후보에 원본의 관측값을 옮기지 않습니다.", "Validating structures, categories, IDs and target accession": "구조, 입력 분류, 식별자와 표적을 확인하고 있습니다.", "Reviewing lineage, observed-evidence scope and unknowns": "부모 계보와 관측 근거의 범위·미확인 항목을 점검하고 있습니다.", "Report complete; computational changes and cached observations remain separate": "결과 정리 완료. 계산 변화와 실험 관측을 구분했습니다." };
  if (mappings[s.summary]) return mappings[s.summary];
  let match = s.summary.match(/^Validated (\d+) distinct canonical input structures$/);
  if (match) return `서로 다른 표준화 입력 구조 ${match[1]}개를 확인했습니다.`;
  match = s.summary.match(/^Generated (\d+) valid proposals/);
  if (match) return `유효한 후보 ${match[1]}개를 생성했습니다. 요청 수는 생성 상한입니다.`;
  match = s.summary.match(/^Calculated properties for (\d+) proposals/);
  if (match) return `후보 ${match[1]}개의 물성 계산 완료. 생물학적 효과는 미확인입니다.`;
  match = s.summary.match(/^Executing (\w+) design with a (\d+)-proposal bound$/);
  if (match) return msg("최대 {0}개 후보 범위에서 {1}를 실행합니다.", match[2], tr(MODES.find(m => m.id === match![1])?.label || "분자 설계"));
  return s.summary;
}
const STATUS: Record<string, string> = { queued: "대기", pending: "대기", running: "실행 중", completed: "완료", failed: "실패", cancelled: "취소", skipped: "생략", blocked: "지원 보류", cancelling: "취소 중", cancel_requested: "취소 요청됨", interrupted: "중단" };
const DESCRIPTORS = [{ key: "molecular_weight", label: "분자량", unit: "g/mol" }, { key: "logp", label: "LogP", unit: "" }, { key: "tpsa", label: "TPSA", unit: "Å²" }, { key: "qed", label: "QED", unit: "" }];
const nameOf = (c: { name?: string; name_ko?: string }) => c.name_ko || c.name || "이름 미지정";
const shortDate = (s: string) => { const d = new Date(s); return Number.isNaN(d.getTime()) ? "" : d.toLocaleString(localeCode(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); };
const safeURL = (value: unknown) => { if (typeof value !== "string") return undefined; try { const u = new URL(value); return ["https:", "http:"].includes(u.protocol) ? u.href : undefined; } catch { return undefined; } };
const inputOf = (c: Compound): DesignInput => ({ id: c.id, name: nameOf(c).slice(0, 200), smiles: c.smiles, category: c.category === "drug" ? "drug" : c.category === "herbal" ? "herbal" : "natural_product", ...(c.source_url ? { source_url: c.source_url } : {}) });
const printable = (v: unknown): string => typeof v === "string" ? v : typeof v === "number" || typeof v === "boolean" ? String(v) : v == null ? "" : JSON.stringify(v);
function evidenceText(e: DesignEvidence): string {
  if (typeof e === "string") return e;
  const message = e.message ?? e.summary ?? e.text ?? e.description ?? e.rationale ?? e.hypothesis;
  const pieces: unknown[] = [e.endpoint, e.title];
  if (e.label === 0 || e.label === "0" || e.label === false) pieces.push(tr("관측 라벨: 비활성 (0)"));
  else if (e.label === 1 || e.label === "1" || e.label === true) pieces.push(tr("관측 라벨: 활성 (1)"));
  else if (e.label != null && e.label !== "") pieces.push(e.label);
  if (e.value != null) pieces.push(`${printable(e.value)}${e.unit ? ` ${e.unit}` : ""}`);
  if (message != null) pieces.push(message);
  return pieces.filter(v => v !== undefined && v !== null && v !== "").map(printable).join(" · ") || "저장된 근거의 상세 설명이 없습니다.";
}
function storedRunId(): string | null { try { return localStorage.getItem("herbfold:design-pipeline:run"); } catch { return null; } }
function idSuffix(): string { return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`; }
function describeChange(delta: Record<string, unknown>): string[] {
  const labels: Record<string, [string, string]> = { molecular_weight: ["분자량", "g/mol"], logp: ["LogP", ""], tpsa: ["극성 표면적(TPSA)", "Å²"], hbd: ["수소 결합 공여체 수", "개"], hba: ["수소 결합 수용체 수", "개"] };
  return Object.entries(labels).flatMap(([key, [label, unit]]) => { const value = delta[key]; return typeof value === "number" && Number.isFinite(value) ? [msg("{0}: 선택한 원본 대비 {1}{2}{3}. 구조에서 계산한 변화입니다.", tr(label), value > 0 ? "+" : "", value.toFixed(key === "hbd" || key === "hba" ? 0 : 2), unit ? ` ${tr(unit)}` : "")] : []; });
}

function parentNames(candidate: DesignCandidate, inputs: DesignInput[], display: (c: { id?: string; smiles?: string; name?: string }) => string) {
  return (candidate.parents ?? []).map(p => typeof p === "string" ? display(inputs.find(c => c.id === p || c.smiles === p) || { name: p }) : display(p)).join(" + ");
}
function asCompound(c: DesignCandidate | DesignInput): Compound | null {
  if (!c.smiles) return null;
  return { ...c, id: c.id, name: c.name, smiles: c.smiles, category: "kind" in c ? "candidate" : c.category, generated: "kind" in c, parents: "kind" in c ? c.parents.map(p => typeof p === "string" ? p : p.id) : undefined };
}

function StructurePreview({ smiles, name }: { smiles: string; name: string }) {
  const [svg, setSVG] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    let current = true; setSVG(""); setError(false);
    void fetchText("/molecules/svg", { smiles }).then(value => { if (current) setSVG(value); }).catch(() => { if (current) setError(true); });
    return () => { current = false; };
  }, [smiles]);
  return <div className="ddp-structure-preview">{tr(svg ? <img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`} alt={tr(msg("{0}의 2차원 분자 구조", tr(name)))} /> : error ? <span>{tr("2D 구조를 불러오지 못했습니다.")}</span> : <LoaderCircle size={22} className="ddp-spin" aria-label={tr("2D 구조 불러오는 중")} />)}</div>;
}

export default function DrugDesignPipeline({ catalog, refreshKey = 0, onInspect, notify }: Props) {
  const [mode, setMode] = useState<DesignMode>("hybrid");
  const [selected, setSelected] = useState<Compound[]>([]);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("P35354");
  const [targets, setTargets] = useState<ProteinTarget[]>([]);
  const [maxCandidates, setMaxCandidates] = useState(12);
  const [transforms, setTransforms] = useState(["o_methylation", "o_acetylation"]);
  const [options, setOptions] = useState<DesignOptions | null>(null);
  const [optionsError, setOptionsError] = useState("");
  const [fallbackCatalog, setFallbackCatalog] = useState<Compound[]>([]);
  const [kind, setKind] = useState<"natural_product" | "drug">("natural_product");
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("");
  const [sources, setSources] = useState<Array<{ id: string; name: string; kind?: string }>>([]);
  const [herbs, setHerbs] = useState<Herb[]>([]);
  const [library, setLibrary] = useState<LibraryPage | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [offset, setOffset] = useState(0);
  const [customName, setCustomName] = useState("");
  const [customSmiles, setCustomSmiles] = useState("");
  const [customBusy, setCustomBusy] = useState(false);
  const [customCategory, setCustomCategory] = useState<"natural_product" | "drug">("natural_product");
  const [runs, setRuns] = useState<DesignRun[]>([]);
  const [run, setRun] = useState<DesignRun | null>(null);
  const [runId, setRunId] = useState<string | null>(storedRunId);
  const [runLoading, setRunLoading] = useState(false);
  const [runError, setRunError] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [candidateId, setCandidateId] = useState("");
  const [inspectId, setInspectId] = useState("");
  const [showParent, setShowParent] = useState(false);
  const [parentId, setParentId] = useState("");
  const [viewMode, setViewMode] = useState<"2d" | "3d">("2d");
  const [scene, setScene] = useState<MolecularScene | null>(null);
  const [sceneLoading, setSceneLoading] = useState(false);
  const [sceneError, setSceneError] = useState("");
  const [sceneRetry, setSceneRetry] = useState(0);
  const [sdfBusy, setSdfBusy] = useState(false);
  const generation = useRef(0);
  const mounted = useRef(true);
  const submitLock = useRef(false);
  const runSelection = useRef<string | null>(runId);
  const active = !!run && ACTIVE.has(run.status);
  const candidateLimit = Math.min(24, options?.limits?.max_candidates ?? options?.max_candidates ?? 24);
  const selectionLimit = options?.limits?.max_compounds ?? 6;
  const modeInfo = MODES.find(m => m.id === mode)!;
  const candidates = run?.result?.candidates ?? [];
  const candidate = candidates.find(c => c.id === candidateId) ?? candidates[0];
  const parentInputs = run?.request?.compounds ?? [];
  const inspectables = candidate?.components?.length ? candidate.components : candidate?.smiles ? [candidate] : [];
  const inspectable = inspectables.find(c => c.id === inspectId) ?? inspectables[0];
  const candidateParents: DesignInput[] = (candidate?.parents ?? []).flatMap(p => { if (typeof p !== "string") return [p]; const original = parentInputs.find(c => c.id === p || c.smiles === p); return original ? [original] : []; });
  const parent = candidateParents.find(p => p.id === parentId) ?? candidateParents[0];
  const visibleMolecule = showParent ? parent : inspectable;
  const smiles = visibleMolecule?.smiles ?? "";
  const componentMode = !!candidate?.components?.length;
  const displayedEvidence = componentMode ? inspectable?.evidence ?? [] : candidate?.evidence ?? [];
  const displayedDescriptors = componentMode ? inspectable?.descriptors : candidate?.descriptors;
  const deltaValue = parent ? candidate?.descriptor_delta?.[parent.id] : undefined;
  const descriptorDelta: Record<string, unknown> = deltaValue && typeof deltaValue === "object" ? deltaValue as Record<string, unknown> : {};
  const changes = describeChange(descriptorDelta);
  const hypotheses = componentMode
    ? ["성분별 구조와 관측 근거를 함께 검토할 수 있습니다. 조합의 용량 비율, 상호작용 및 시너지는 측정되지 않았습니다."]
    : ["구조 변화에 따라 물성·용해도·투과성의 변화 가능성을 검토할 수 있습니다. 실제 방향과 크기는 실험으로 확인해야 합니다.", "표적 결합, 세포 활성, 체내 노출과 독성에 대한 신규 측정 근거가 없으므로 치료 효과를 추정하지 않습니다."];

  const isNatural = (c: Compound) => c.category === "herbal" || c.category === "natural_product";
  const hasNatural = selected.some(isNatural), hasDrug = selected.some(c => c.category === "drug");
  const validInput = mode === "hybrid" ? hasNatural && hasDrug : mode === "combination" ? selected.length >= 2 : selected.length === 1 && hasNatural && transforms.length > 0;
  const selectedTarget = targets.find(t => t.accession === target);
  const availableCatalog = catalog.length ? catalog : fallbackCatalog;
  const displayNameOf = (c?: { id?: string; smiles?: string; name?: string; name_ko?: string }): string => {
    if (!c) return "";
    // Resolve only the same recorded input; changed candidate structures keep their own names.
    const source = availableCatalog.find(item => item.id === c.id && item.smiles === c.smiles
      && (c.name === item.name || c.name === item.name_ko));
    const record = source || c;
    return (getLanguage() === "en" ? record.name || record.name_ko : record.name_ko || record.name) || tr("이름 미지정");
  };
  const defaults = useMemo(() => availableCatalog.filter(c => kind === "drug" ? c.category === "drug" : isNatural(c)).filter(c => `${nameOf(c)} ${c.smiles}`.toLowerCase().includes(search.toLowerCase())).slice(0, 6), [availableCatalog, kind, search]);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current++; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setOptionsError("");
    void api<Compound[]>("/catalog", undefined, { signal: controller.signal }).then(v => { if (!controller.signal.aborted) setFallbackCatalog(v); }).catch(() => {});
    void api<DesignOptions>("/design-pipeline/options", undefined, { signal: controller.signal }).then(v => { if (!controller.signal.aborted) setOptions(v); }).catch(e => { if (!controller.signal.aborted) setOptionsError(e.message); });
    void api<{ items: ProteinTarget[] }>("/molecular/targets", undefined, { signal: controller.signal }).then(v => { if (!controller.signal.aborted) setTargets(v.items); }).catch(() => { /* Recorded COX-2 fallback stays explicit. */ });
    void api<{ items: Herb[] }>("/discovery/herbs", undefined, { signal: controller.signal }).then(v => { if (!controller.signal.aborted) setHerbs(v.items); }).catch(() => {});
    void api<{ sources: Array<{ id: string; name: string; kind?: string }> }>("/discovery/summary", undefined, { signal: controller.signal }).then(v => { if (!controller.signal.aborted) setSources(v.sources ?? []); }).catch(() => {});
    return () => controller.abort();
  }, [refreshKey]);
  const refreshHistory = useCallback(async () => {
    try { const value = await api<{ items: DesignRun[] }>("/design-pipeline/runs"); if (mounted.current) { setRuns(value.items ?? []); setHistoryError(""); } }
    catch (e) { if (mounted.current) setHistoryError((e as Error).message); }
  }, [refreshKey]);
  useEffect(() => { void refreshHistory(); }, [refreshHistory]);
  useEffect(() => { try { if (runId) localStorage.setItem("herbfold:design-pipeline:run", runId); } catch { /* Private storage may be unavailable. */ } }, [runId]);
  useEffect(() => {
    const controller = new AbortController(); setLibrary(null); setSearchLoading(true); setSearchError("");
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ search, kind, limit: "16", offset: String(offset), ...(source ? { source } : {}) });
      void api<LibraryPage>(`/discovery/compounds?${params}`, undefined, { signal: controller.signal }).then(v => { if (!controller.signal.aborted) setLibrary(v); }).catch(e => { if (!controller.signal.aborted) setSearchError(e.message); }).finally(() => { if (!controller.signal.aborted) setSearchLoading(false); });
    }, 280);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [search, kind, source, offset, refreshKey]);
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController(); let timer: number | undefined; setRunLoading(true); setRunError("");
    const poll = async () => {
      try {
        const next = await api<DesignRun>(`/design-pipeline/runs/${encodeURIComponent(runId)}`, undefined, { signal: controller.signal });
        if (controller.signal.aborted || runSelection.current !== runId) return;
        setRun(next); setRunLoading(false); setRunError("");
        if (ACTIVE.has(next.status)) timer = window.setTimeout(poll, 1400);
        else void refreshHistory();
      } catch (e) {
        if (!controller.signal.aborted) { setRunError((e as Error).message); setRunLoading(false); timer = window.setTimeout(poll, 5000); }
      }
    };
    void poll(); return () => { controller.abort(); window.clearTimeout(timer); };
  }, [runId, refreshHistory]);
  useEffect(() => { setCandidateId(""); setInspectId(""); setParentId(""); setShowParent(false); setViewMode("2d"); }, [runId]);
  useEffect(() => { setInspectId(""); setParentId(""); setShowParent(false); }, [candidateId]);
  useEffect(() => {
    generation.current++; const current = generation.current; const controller = new AbortController();
    setScene(null); setSceneError(""); setSceneLoading(false);
    if (!smiles || viewMode !== "3d") return () => controller.abort();
    setSceneLoading(true);
    const timeout = window.setTimeout(() => { controller.abort(); if (generation.current === current) { setSceneLoading(false); setSceneError("구조 계산 응답이 지연되고 있습니다. 다시 시도하거나 2D 구조를 확인하세요."); } }, 40000);
    void api<MolecularScene>("/molecular/conformer", { smiles, seed: 42, include_hydrogens: true, num_conformers: 4 }, { signal: controller.signal })
      .then(v => { if (!controller.signal.aborted && generation.current === current) setScene(v); })
      .catch(e => { if (!controller.signal.aborted && generation.current === current) setSceneError(e.message); })
      .finally(() => { window.clearTimeout(timeout); if (!controller.signal.aborted && generation.current === current) setSceneLoading(false); });
    return () => { controller.abort(); window.clearTimeout(timeout); };
  }, [smiles, viewMode, sceneRetry]);

  function addCompound(c: Compound) {
    if (selected.some(p => p.smiles === c.smiles)) { notify("이미 선택한 구조입니다."); return; }
    if (c.category === "candidate") { notify("출처 분류가 확인된 천연물 또는 약물 구조를 선택하세요.", true); return; }
    if (mode === "transform") { if (!isNatural(c)) { notify("구조 변환에는 천연물 성분을 선택하세요.", true); return; } setSelected([c]); }
    else if (selected.length >= selectionLimit) notify(`한 번에 최대 ${selectionLimit}개 성분을 선택할 수 있습니다.`, true);
    else setSelected(previous => [...previous, c]);
  }
  function changeMode(next: DesignMode) { setMode(next); if (next === "transform") { setSelected(p => p.filter(isNatural).slice(0, 1)); setKind("natural_product"); setSource(""); setOffset(0); } }
  async function addCustom(event: FormEvent) {
    event.preventDefault(); if (!customSmiles.trim() || customBusy) return; setCustomBusy(true);
    try {
      const descriptors = await api<Record<string, unknown>>("/molecules/describe", { smiles: customSmiles.trim() });
      if (!mounted.current) return;
      const canonical = typeof descriptors.canonical_smiles === "string" ? descriptors.canonical_smiles : customSmiles.trim();
      addCompound({ id: `custom-${idSuffix()}`, name: customName.trim() || "사용자 입력 구조", smiles: canonical, category: customCategory, descriptors }); setCustomSmiles(""); setCustomName("");
    } catch (e) { notify((e as Error).message, true); } finally { if (mounted.current) setCustomBusy(false); }
  }
  async function startRun() {
    if (!validInput || submitLock.current) return; submitLock.current = true; setSubmitting(true);
    try {
      const next = await api<DesignRun>("/design-pipeline/runs", { name: name.trim() || `${modeInfo.label} · ${new Date().toLocaleDateString(localeCode())}`, mode, compounds: selected.map(inputOf), target_accession: target, max_candidates: Math.min(maxCandidates, candidateLimit), transformations: mode === "transform" ? transforms : [], seed: 42 });
      if (!mounted.current) return;
      runSelection.current = next.id; setRun(next); setRunId(next.id); setRunError(""); void refreshHistory(); notify("설계 실행을 시작했습니다. 단계별 계산 결과를 확인하세요.");
    } catch (e) { notify((e as Error).message, true); } finally { submitLock.current = false; if (mounted.current) setSubmitting(false); }
  }
  async function cancelRun() {
    if (!run || cancelBusy) return; const id = run.id; setCancelBusy(true);
    try { const next = await api<DesignRun>(`/design-pipeline/runs/${encodeURIComponent(id)}/cancel`, {}); if (mounted.current && runSelection.current === id) setRun(next); notify("취소 요청을 전달했습니다."); }
    catch (e) { notify((e as Error).message, true); } finally { if (mounted.current) setCancelBusy(false); }
  }
  async function saveSDF() {
    if (!smiles || sdfBusy) return; setSdfBusy(true);
    try { const sdf = await fetchText("/molecular/sdf", { smiles, seed: 42, include_hydrogens: true, num_conformers: 4 }); download(sdf, `${visibleMolecule?.id ?? "candidate"}.sdf`, "chemical/x-mdl-sdfile"); }
    catch (e) { notify((e as Error).message, true); } finally { if (mounted.current) setSdfBusy(false); }
  }
  const stages: DesignStage[] = run?.stages ?? (options?.stages ?? []).map(s => ({ ...s, status: "pending" }));
  const completed = stages.filter(s => s.status === "completed").length;
  const candidateLimitations = [...(candidate?.limitations ?? []), ...(run?.result?.limitations ?? [])];

  return <div className="drug-design-pipeline" data-testid="drug-design-pipeline">
    <header className="ddp-hero">
      <div><span className="ddp-eyebrow"><Workflow size={15} /> DRUG DESIGN PIPELINE</span><h2>{tr("발견한 성분을,")}<br /><em>{tr("다음 구조의 가능성으로.")}</em></h2><p>{tr("한약 성분과 약물의 조합부터 구조 변환까지.")}<br className="ddp-desktop-break" /> {tr(" 실제 계산과 근거를 따라 설계 후보를 검토하세요.")}</p></div>
      <div className="ddp-hero-diagram" aria-hidden="true"><div className="ddp-orbit"><Leaf size={32} /><span>{tr("천연물")}</span></div><div className="ddp-diagram-link"><span /><GitBranch size={22} /><span /></div><div className="ddp-orbit ddp-orbit-next"><Atom size={37} /><span>{tr("설계 후보")}</span></div><div className="ddp-hero-tag"><ShieldCheck size={13} /> {tr(" 구조 · 출처 · 근거")}</div></div>
    </header>
    <div className="ddp-topline"><span><Beaker size={15} /> {tr(" 후보 설계는 효능 확정이 아닙니다. 관측 근거와 탐색 가설을 구분합니다.")}</span><button className="ddp-text-button" onClick={() => { setHistoryOpen(v => !v); void refreshHistory(); }} aria-expanded={historyOpen}><History size={15} /> {tr(" 실행 기록 ")}{tr(runs.length > 0 && `(${runs.length})`)}<ChevronDown size={14} /></button></div>
    {tr(historyOpen && <section className="ddp-card ddp-history" aria-label={tr("파이프라인 실행 기록")}><div className="ddp-section-title"><h3>{tr("저장된 실행")}</h3><button className="ddp-text-button" onClick={() => void refreshHistory()}><RefreshCw size={14} /> {tr(" 새로 고침")}</button></div>{tr(historyError && <p className="ddp-error">{tr(historyError)}</p>)}{tr(!runs.length ? <p className="ddp-muted">{tr("아직 저장된 실행이 없습니다.")}</p> : <div className="ddp-history-list">{tr(runs.map(item => <button key={item.id} className={runId === item.id ? "is-selected" : ""} onClick={() => { if (runId !== item.id) { runSelection.current = item.id; setRun(null); setRunId(item.id); } setHistoryOpen(false); }}><span><strong data-research-value="run-name">{item.name || tr("설계 실행")}</strong><small>{tr(shortDate(item.created))}</small></span><span className={`ddp-status ${item.status}`}>{tr(STATUS[item.status] || item.status)}</span></button>))}</div>)}</section>)}
    <div className="ddp-modes" role="group" aria-label={tr("분자 설계 방식")}>{tr(MODES.map(({ id, number, label, subtitle, icon: Icon }) => <button key={id} onClick={() => changeMode(id)} className={`ddp-mode ${mode === id ? "is-selected" : ""}`} aria-pressed={mode === id} data-testid={`design-mode-${id}`}><span className="ddp-mode-top"><Icon size={22} /><small>{tr(number)}</small></span><strong>{tr(label)}</strong><span>{tr(subtitle)}</span>{tr(mode === id && <CheckCircle2 className="ddp-mode-check" size={18} />)}</button>))}</div>
    <p className="ddp-mode-description">{tr(modeInfo.text)}</p>
    <div className="ddp-workspace">
      <aside className="ddp-card ddp-library" aria-labelledby="ddp-library-heading"><div className="ddp-section-title"><div><span className="ddp-eyebrow">STARTING MATERIALS</span><h3 id="ddp-library-heading">{tr("설계할 성분 선택")}</h3></div><Leaf size={19} /></div>
        <div className="ddp-segment" role="group" aria-label={tr("성분 출처 분류")}><button aria-pressed={kind === "natural_product"} onClick={() => { setKind("natural_product"); setSource(""); setOffset(0); }}>{tr("한약 · 천연물")}</button><button aria-pressed={kind === "drug"} disabled={mode === "transform"} onClick={() => { setKind("drug"); setSource(""); setOffset(0); }}>{tr("약물 비교군")}</button></div>
        <label className="ddp-search"><Search size={16} /><input aria-label={tr("한약재·성분 검색")} list="ddp-herbs" value={search} placeholder={tr(kind === "drug" ? "약물명 또는 구조 검색" : "한약재·성분명 검색")} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></label>
        <datalist id="ddp-herbs">{tr(herbs.filter(h => !search || h.name_ko.includes(search)).slice(0, 60).map((h, i) => <option key={`${h.name_ko}-${i}`} value={h.name_ko} />))}</datalist>
        {tr(sources.length > 0 && <label className="ddp-source-label">{tr("자료원")}<select aria-label={tr("성분 자료원")} value={source} onChange={e => { setSource(e.target.value); setOffset(0); }}><option value="">{tr("현재 분류의 전체 자료원")}</option>{tr(sources.filter(s => !s.kind || s.kind === kind).map(s => <option key={s.id} value={s.id}>{s.name}</option>))}</select></label>)}
        {tr(defaults.length > 0 && !source && offset === 0 && <div className="ddp-quick-picks"><span>{tr("앱의 기본 성분")}</span>{tr(defaults.map(c => <button key={c.id} onClick={() => addCompound(c)} disabled={selected.some(s => s.smiles === c.smiles)}><Plus size={12} />{displayNameOf(c)}</button>))}</div>)}
        <div className="ddp-library-caption"><span>{tr(library ? msg("자료원 구조 {0}개", library.total.toLocaleString(localeCode())) : "자료원 검색")}</span>{tr(searchLoading && <LoaderCircle className="ddp-spin" size={14} aria-label={tr("검색 중")} />)}</div>
        {tr(library?.query_resolution?.mapped && <p className="ddp-small-note">{tr("한약재 이름을 기록된 기원 분류군으로 연결했습니다. 실제 약재의 배치 조성은 확인하지 않습니다.")}</p>)}
        {tr(searchError && <p role="alert" className="ddp-error">{tr("검색 연결 오류: ")}{tr(searchError)}</p>)}
        <div className="ddp-compound-list" aria-busy={searchLoading}>{tr(!searchLoading && !library?.items?.length && <p className="ddp-muted">{tr("검색 결과가 없습니다. 다른 이름이나 직접 입력을 이용하세요.")}</p>)}{tr((library?.items ?? []).map(row => { const c = normalizeDiscoveryCompound(row, { kind, source }); const added = selected.some(s => s.smiles === c.smiles); return <button key={row.id} className={`ddp-compound ${added ? "is-selected" : ""}`} disabled={added || searchLoading} onClick={() => addCompound(c)}><span className="ddp-compound-symbol">{tr(kind === "drug" ? <Beaker size={16} /> : <Leaf size={16} />)}</span><span><strong>{displayNameOf(c)}</strong><small>{tr(printable(c.descriptors?.formula) || "출처 기반 분자 구조")}</small></span>{tr(added ? <Check size={16} /> : <Plus size={16} />)}</button>; }))}</div>
        {tr(library && library.total > 16 && <div className="ddp-pagination"><button disabled={!offset || searchLoading} onClick={() => setOffset(v => Math.max(0, v - 16))}>{tr("이전")}</button><span>{tr(Math.floor(offset / 16) + 1)} / {tr(Math.ceil(library.total / 16))}</span><button disabled={offset + 16 >= library.total || searchLoading} onClick={() => setOffset(v => v + 16)}>{tr("다음")}</button></div>)}
        <details className="ddp-custom"><summary><Plus size={15} /> {tr(" SMILES 직접 입력")}</summary><form onSubmit={addCustom}><label>{tr("성분 이름")}<input value={customName} onChange={e => setCustomName(e.target.value)} maxLength={180} placeholder={tr("입력 구조 이름")} /></label><label>SMILES<textarea value={customSmiles} onChange={e => setCustomSmiles(e.target.value)} maxLength={4096} rows={3} required placeholder={tr("분자 구조를 붙여 넣으세요")} /></label><label>{tr("입력 구조 분류")}<select value={customCategory} onChange={e => setCustomCategory(e.target.value as "natural_product" | "drug")}><option value="natural_product">{tr("천연물 성분으로 입력")}</option><option value="drug">{tr("약물 비교군으로 입력")}</option></select></label><p className="ddp-small-note">{tr("분류는 사용자 지정입니다. 천연 유래나 승인 상태를 자동 검증하지 않습니다.")}</p><button className="ddp-secondary" type="submit" disabled={customBusy || !customSmiles.trim()}>{tr(customBusy ? <LoaderCircle size={14} className="ddp-spin" /> : <Plus size={14} />)} {tr(" 구조 확인 후 추가")}</button></form></details>
      </aside>
      <div className="ddp-main">
        <section className="ddp-card ddp-setup" aria-labelledby="ddp-setup-heading"><div className="ddp-section-title"><div><span className="ddp-eyebrow">DESIGN BRIEF</span><h3 id="ddp-setup-heading">{tr("설계 조건")}</h3></div><span className="ddp-pill">{tr(modeInfo.label)}</span></div>
          <div className="ddp-selected">{tr(!selected.length ? <div className="ddp-selection-empty"><Plus size={19} /><span>{tr(mode === "transform" ? "변환할 천연물 성분 하나를 선택하세요." : "왼쪽 목록에서 출발 성분을 선택하세요.")}</span></div> : selected.map((c, i) => <div key={c.id} className="ddp-selected-chip"><span className="ddp-parent-label">P{tr(i + 1)}</span><div><strong>{displayNameOf(c)}</strong><small>{tr(c.category === "drug" ? "약물 비교군" : "천연물 성분")}</small></div><button aria-label={msg("{0} 선택 해제", displayNameOf(c))} onClick={() => setSelected(p => p.filter(x => x.id !== c.id))}><X size={15} /></button></div>))}</div>
          {tr(mode === "hybrid" && <p className="ddp-small-note">{tr("천연물 ")}{tr(hasNatural ? "선택됨" : "선택 필요")} {tr(" · 약물 비교군 ")}{tr(hasDrug ? "선택됨" : "선택 필요")}{tr(". 부모별 조각 기여를 확인하는 BRICS 설계입니다.")}</p>)}
          <div className="ddp-form-grid"><label>{tr("실행 이름")}<input aria-label={tr("설계 실행 이름")} value={name} onChange={e => setName(e.target.value)} placeholder={msg("{0} 탐색", tr(modeInfo.label))} maxLength={120} /></label><label>{tr("단백질 표적")}<select aria-label={tr("설계 단백질 표적")} value={target} onChange={e => setTarget(e.target.value)}>{tr(!targets.some(t => t.accession === "P35354") && <option value="P35354">COX-2 · P35354</option>)}{tr(targets.map(t => <option key={t.accession} value={t.accession}>{t.gene || t.name} · {t.accession}</option>))}</select></label></div>
          <p className="ddp-small-note"><Dna size={13} /> {tr(selectedTarget ? `${selectedTarget.organism} · ${selectedTarget.length.toLocaleString()} aa. ` : "기록된 표적을 근거 조회에 사용합니다. ")}{tr("표적 선택만으로 결합이나 약효를 예측하지 않습니다.")}</p>
          {tr(mode === "transform" && <fieldset className="ddp-transformations"><legend>{tr("적용할 구조 변환")}</legend>{tr((options?.transformations?.length ? options.transformations : TRANSFORMS).map(t => <label key={t.id}><input type="checkbox" checked={transforms.includes(t.id)} onChange={e => setTransforms(p => e.target.checked ? [...p, t.id] : p.filter(v => v !== t.id))} /><span>{tr(TRANSFORMS.find(known => known.id === t.id)?.label || t.label)}</span></label>))}</fieldset>)}
          <div className="ddp-run-bar"><label className="ddp-limit"><SlidersHorizontal size={15} /> {tr(" 최대 후보")}<select aria-label={tr("최대 설계 후보 수")} value={Math.min(maxCandidates, candidateLimit)} onChange={e => setMaxCandidates(Number(e.target.value))}>{tr([4, 8, 12, 24].filter(n => n <= candidateLimit).map(n => <option key={n} value={n}>{tr(n)}{getLanguage() === "en" ? " " : ""}{tr("개")}</option>))}</select></label><button className="ddp-primary" data-testid="design-run-start" onClick={() => void startRun()} disabled={!validInput || submitting || active}>{tr(submitting ? <LoaderCircle size={17} className="ddp-spin" /> : <Sparkles size={17} />)} {tr(submitting ? "실행 요청 중" : "자동 설계 실행")}<ArrowRight size={16} /></button></div>
          {tr(optionsError && <p className="ddp-small-note ddp-error">{tr("설계 설정 조회 실패: ")}{tr(optionsError)}</p>)}
        </section>
        <section className="ddp-card ddp-pipeline" aria-labelledby="ddp-pipeline-heading"><div className="ddp-section-title"><div><span className="ddp-eyebrow">SPECIALIST WORKFLOW</span><h3 id="ddp-pipeline-heading">{tr("전문 작업자 파이프라인")}</h3></div>{tr(run && <span className={`ddp-status ${run.status}`}>{tr(ACTIVE.has(run.status) && <LoaderCircle className="ddp-spin" size={13} />)}{tr(STATUS[run.status] || run.status)}</span>)}</div>
          <p className="ddp-small-note">{tr("구조 검증, 후보 생성, 근거 점검을 맡은 계산 작업자입니다. 진행 상태는 저장된 실행 기록에서 갱신됩니다.")}</p>
          {tr(stages.length > 0 ? <ol className="ddp-stage-grid">{tr(stages.map((s, i) => <li key={s.id} className={`ddp-stage ${s.status}`}><div className="ddp-stage-top"><span>{tr(s.status === "completed" ? <Check size={16} /> : s.status === "running" ? <LoaderCircle className="ddp-spin" size={16} /> : String(i + 1).padStart(2, "0"))}</span><small>{tr(run ? STATUS[s.status] || s.status : "실행 전")}</small></div><strong>{tr(STAGE_NAMES[s.id]?.[0] || s.label)}</strong><span className="ddp-agent">{tr(STAGE_NAMES[s.id]?.[1] || s.agent)}</span>{tr(s.summary && <p>{tr(stageSummary(s))}</p>)}{tr(s.depends_on?.length ? <small className="ddp-stage-dependency">{tr("이전 작업 ")}{s.depends_on.map(id => tr(STAGE_NAMES[id]?.[0] || stages.find(x => x.id === id)?.label || id)).join(" · ")}</small> : null)}</li>))}</ol> : <div className="ddp-pipeline-empty"><Workflow size={29} /><strong>{tr(runLoading ? "실행 기록을 불러오고 있습니다" : "설계를 시작하면 실행 단계가 표시됩니다")}</strong><p>{tr("실제 작업별 상태와 근거 점검 결과를 이곳에서 확인하세요.")}</p></div>)}
          {tr(run && <div className="ddp-progress-footer"><span><span data-research-value="run-name">{run.name}</span> {tr(" · 완료한 단계 ")}{tr(completed)}/{tr(stages.length)}</span>{tr(active && <button className="ddp-text-button" disabled={cancelBusy} onClick={() => void cancelRun()}><Square size={12} /> {tr(cancelBusy ? "취소 요청 중" : "실행 취소")}</button>)}</div>)}
          {tr(runError && <p className="ddp-error" role="alert">{tr("상태 조회 오류 · 재연결 중: ")}{tr(runError)}</p>)}{tr(run?.error && <p className="ddp-error" role="alert">{tr(run.error)}</p>)}
          {tr(!!run?.events?.length && <details className="ddp-event-log"><summary><Clock3 size={14} /> {tr(" 실행 이벤트 ")}{tr(run.events.length)}{getLanguage() === "en" ? " " : ""}{tr("개")}</summary><ol>{tr(run.events.slice().reverse().slice(0, 40).map(e => <li key={`${e.seq}-${e.time}`}><time>{tr(shortDate(e.time))}</time><span>{tr(e.message)}</span></li>))}</ol></details>)}
        </section>
      </div>
    </div>
    {tr(mode === "combination" && <div className="ddp-assay-slot"><CombinationAssayPanel /></div>)}
    <section className="ddp-card ddp-results" aria-labelledby="ddp-results-heading" data-testid="design-results"><div className="ddp-section-title"><div><span className="ddp-eyebrow">DESIGN OUTCOMES</span><h3 id="ddp-results-heading">{tr("설계 결과 ")}<span className="ddp-result-count">{tr(candidates.length)}</span></h3></div>{tr(run?.result && <button className="ddp-secondary" onClick={() => download(run, `herbfold-design-${run.id}.json`)}><ArrowDownToLine size={14} /> {tr(" 전체 결과 JSON")}</button>)}</div>
      {tr(run && <p className="ddp-saved-context">{tr("저장된 실행: ")}<span data-research-value="run-name">{run.name}</span> · {tr(MODES.find(m => m.id === run.request?.mode)?.label || run.request?.mode)} {tr(" · 표적 ")}{run.request?.target_accession}{tr(". 위의 설계 조건을 바꿔도 이 결과는 해당 실행의 입력을 유지합니다.")}</p>)}
      {tr(!candidates.length ? <div className="ddp-results-empty"><div className="ddp-empty-atom"><Atom size={40} /></div><h4>{tr(active ? "설계 작업이 진행 중입니다" : run?.status === "completed" ? "현재 조건에서 보존된 후보가 없습니다" : "설계 후보를 비교할 준비가 되었습니다")}</h4><p>{tr(run?.status === "completed" ? "입력 구조와 적용한 변환·필터를 확인하고 조건을 조정하세요." : "실행이 완료되면 실제 분자 구조, 물성 변화와 근거 상태를 확인할 수 있습니다.")}</p></div> : <div className="ddp-results-layout">
        <div className="ddp-candidate-list" role="group" aria-label={tr("설계 결과 선택")}>{tr(candidates.map((c, i) => <button key={c.id} className={candidate?.id === c.id ? "is-selected" : ""} onClick={() => setCandidateId(c.id)} aria-pressed={candidate?.id === c.id}><span className="ddp-candidate-index">{tr(String(i + 1).padStart(2, "0"))}</span><span><strong>{displayNameOf(c)}</strong><small>{tr(c.components?.length ? msg("{0}개 성분의 조합", c.components.length) : "계산으로 생성된 구조")}</small></span><ArrowRight size={15} /></button>))}</div>
        {tr(candidate && <div className="ddp-candidate-detail"><div className="ddp-candidate-heading"><div><span className="ddp-pill">{tr(candidate.components?.length ? "각 성분 구조 유지" : "설계 후보")}</span><h4>{displayNameOf(candidate)}</h4><p>{tr("부모: ")}{parentNames(candidate, parentInputs, displayNameOf) || tr("저장된 입력 참조")}</p></div><button className="ddp-text-button" onClick={() => download(candidate, `${candidate.id}.json`)}><ArrowDownToLine size={15} /> {tr(" 후보 JSON")}</button></div>
          {tr(candidate.components?.length ? <div className="ddp-component-picker" role="group" aria-label={tr("조합 내 확인할 성분")}>{tr(candidate.components.map(c => <button key={c.id} aria-pressed={inspectable?.id === c.id && !showParent} onClick={() => { setInspectId(c.id); setShowParent(false); }}>{displayNameOf(c)}</button>))}<p className="ddp-small-note">{tr("조합은 여러 분자로 유지됩니다. 개별 성분을 선택해 구조를 확인하세요.")}</p></div> : null)}
          {tr(candidateParents.length > 1 && !componentMode && <label className="ddp-parent-select">{tr("비교할 원본 성분")}<select aria-label={tr("물성 비교 기준 원본")} value={parent?.id ?? ""} onChange={e => setParentId(e.target.value)}>{tr(candidateParents.map(p => <option value={p.id} key={p.id}>{displayNameOf(p)}</option>))}</select></label>)}
          {tr(smiles && <><div className="ddp-view-controls"><div className="ddp-segment" role="group" aria-label={tr("비교 구조 선택")}><button aria-pressed={!showParent} onClick={() => setShowParent(false)}>{tr("설계 결과")}</button>{tr(!componentMode && <button aria-pressed={showParent} disabled={!parent} onClick={() => setShowParent(true)}>{tr("원본 성분")}</button>)}</div><div className="ddp-segment" role="group" aria-label={tr("분자 표현")}><button aria-pressed={viewMode === "2d"} onClick={() => setViewMode("2d")}>{tr("2D 구조")}</button><button aria-pressed={viewMode === "3d"} onClick={() => setViewMode("3d")}>{tr("3D 회전 · 확대")}</button></div></div>
            <div className="ddp-viewer" data-testid="design-molecule-viewer">{tr(viewMode === "2d" ? <StructurePreview key={smiles} smiles={smiles} name={displayNameOf(visibleMolecule || candidate)} /> : sceneError ? <div className="ddp-viewer-error"><Atom size={29} /><p>{tr(sceneError)}</p><div><button className="ddp-secondary" onClick={() => setSceneRetry(v => v + 1)}>{tr("3D 다시 시도")}</button><button className="ddp-secondary" onClick={() => setViewMode("2d")}>{tr("2D로 보기")}</button></div></div> : <Suspense fallback={<div className="ddp-viewer-loading"><LoaderCircle className="ddp-spin" />{tr("3D 뷰어 준비 중")}</div>}><MoleculeViewer scene={scene} loading={sceneLoading} className="ddp-molecule-canvas" displayLabel={displayNameOf(visibleMolecule)} /></Suspense>)}</div>
            <p className="ddp-small-note">{displayNameOf(visibleMolecule)} · {tr(viewMode === "3d" ? "RDKit 계산 배좌입니다. 드래그로 회전하고 휠·핀치로 확대하세요. 단백질 결합 자세는 아닙니다." : "분자 그래프를 표시합니다. 3D를 선택하면 실제 계산 배좌를 생성합니다.")}</p>
            <div className="ddp-structure-actions"><button className="ddp-secondary" onClick={() => void saveSDF()} disabled={sdfBusy}>{tr(sdfBusy ? <LoaderCircle className="ddp-spin" size={15} /> : <ArrowDownToLine size={15} />)} {tr(" SDF 저장")}</button><button className="ddp-primary" onClick={() => { const c = visibleMolecule && asCompound(visibleMolecule); if (c) onInspect(c, run?.request.target_accession ?? target); }}><Microscope size={16} /> {tr(" AF3 스튜디오에서 검토")}<ArrowRight size={15} /></button></div>
          </>)}
          {tr(componentMode && <p className="ddp-small-note">{tr("물성과 관측 근거: ")}{displayNameOf(inspectable)} {tr(" 개별 성분 기준. 조합 전체의 물성으로 합산하지 않습니다.")}</p>)}
          {tr(!componentMode && <p className="ddp-small-note">{tr("아래 물성과 관측 근거는 설계 후보 ")}<strong>{displayNameOf(candidate)}</strong> {tr(" 기준입니다.")}{tr(showParent ? msg(" 위 구조 뷰어는 비교 원본 {0}을 표시하고 있습니다.", displayNameOf(parent)) : "")}</p>)}
          {tr(!!displayedDescriptors && <div className="ddp-properties">{tr(DESCRIPTORS.map(d => { const value = displayedDescriptors?.[d.key]; const delta = descriptorDelta[d.key]; return <div key={d.key}><span>{tr(d.label)}</span><strong>{tr(formatNumber(value, d.key === "qed" ? 3 : 2))} <small>{tr(d.unit)}</small></strong>{tr(typeof delta === "number" && Number.isFinite(delta) && <small>{tr("원본 대비 ")}{tr(delta > 0 ? "+" : "")}{tr(delta.toFixed(2))}</small>)}</div>; }))}</div>)}
          {tr(candidate.smiles && <details className="ddp-smiles"><summary>{tr("설계 후보의 정확한 분자 구조 · SMILES")}</summary><code>{candidate.smiles}</code></details>)}
          <div className="ddp-evidence-grid"><section><h4><ShieldCheck size={17} /> {tr(componentMode ? "개별 성분 관측 근거" : "관측값 · 근거 상태")}</h4><div className="ddp-observation-list" tabIndex={0} role="region" aria-label={tr("관측 근거 목록")}>{tr(displayedEvidence.length ? displayedEvidence.map((e, i) => <article key={i} className="ddp-evidence-item">{tr(typeof e !== "string" && e.status && <span className="ddp-evidence-status">{tr(({ observed: "관측 근거", measured: "실험 관측", unsupported: "지원 근거 없음", withheld: "응답 보류", predicted: "모델 추정" } as Record<string, string>)[e.status] || e.status)}</span>)}<p className="ddp-evidence-scope">{tr(typeof e !== "string" && (e.kind === "pathway_assay" ? "Tox21 경로 관측 · 선택 표적 활성값 아님" : e.kind === "target_assay" ? msg("표적 실험 · {0}", printable(e.target_accession) || tr("표적 미지정")) : "관측 자료"))}{tr(typeof e !== "string" && e.assay_id != null ? ` · assay ${printable(e.assay_id)}` : "")}</p><p>{tr(evidenceText(e))}</p>{tr(typeof e !== "string" && safeURL(e.source_url) && <a href={safeURL(e.source_url)} target="_blank" rel="noreferrer">{tr("원자료 확인 ")}<ExternalLink size={11} /></a>)}</article>) : <p className="ddp-muted">{tr(componentMode ? msg("{0}에 연결된 관측 근거가 없습니다.", displayNameOf(inspectable) || tr("이 성분")) : "이 후보에 연결된 관측 근거가 없습니다.")} {tr(" 근거 없음은 비활성이나 안전성을 뜻하지 않습니다.")}</p>)}</div></section><section><h4><GitCompareArrows size={17} /> {tr(" 예상 변화 · 탐색 가설")}</h4><p className="ddp-small-note">{tr("계산 물성에서 검토할 방향을 설명합니다. 치료 효과나 시너지 예측값이 아닙니다.")}</p>{tr(changes.map((text, i) => <article key={`change-${i}`} className="ddp-evidence-item"><span className="ddp-evidence-status">{tr("계산된 물성 변화")}</span><p>{tr(text)}</p></article>))}{tr(hypotheses.map((text, i) => <article key={`hypothesis-${i}`} className="ddp-evidence-item"><span className="ddp-evidence-status hypothesis">{tr("검증할 가설")}</span><p>{tr(text)}</p></article>))}</section></div>
          {tr(candidateLimitations.length > 0 && <details className="ddp-limitations"><summary>{tr("해석 범위와 확인할 사항 ")}{tr(new Set(candidateLimitations).size)}{getLanguage() === "en" ? " " : ""}{tr("개")}</summary><ul>{tr([...new Set(candidateLimitations)].map((s, i) => <li key={i}>{tr(s)}</li>))}</ul></details>)}
        </div>)}
      </div>)}
    </section>
  </div>;
}

