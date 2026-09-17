import { tr, msg } from "./lib/i18n";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Atom,
  Bot,
  Check,
  ChevronRight,
  CircleHelp,
  Cpu,
  Database,
  ExternalLink,
  FlaskConical,
  GitBranch,
  GitCompareArrows,
  Layers3,
  Leaf,
  LoaderCircle,
  Maximize2,
  PanelsTopLeft,
  Plus,
  Search,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";
import { AnalysisComposer, AnalysisPanel } from "./components/AnalysisPanel";
import DataPanel from "./components/DataPanel";
import LanguageSwitcher from "./components/LanguageSwitcher";
import { useLanguage } from "./lib/useLanguage";
import ArchivePanel from "./components/ArchivePanel";
import DiscoveryPanel from "./components/DiscoveryPanel";
import ValidationPanel from "./components/ValidationPanel";
import AF3WorkflowLauncher from "./components/AF3WorkflowLauncher";
import AF3WorkflowWorkspace from "./components/AF3WorkflowWorkspace";
import { rememberedAF3Workflow, rememberAF3Workflow, trustedAF3WorkflowResult } from "./lib/af3WorkflowSelection";
import type { AF3Workflow } from "./types/af3Workflow";
import AlphaFoldDiagnostics from "./components/AlphaFoldDiagnostics";
import QuantumStudio from "./components/QuantumStudio";
import StudioWorkspaceHeader from "./components/StudioWorkspaceHeader";
import StudioCompoundLibrary from "./components/StudioCompoundLibrary";
import ComparisonWorkspace from "./components/ComparisonWorkspace";
import { StructureContext, StructureModeControls, StructureUnavailable } from "./components/StructureSelection";
import { useDialog } from "./lib/useDialog";
import { readStudioSelection, saveStudioSelection } from "./lib/studioSelection";
import { api, download, fetchText, formatNumber, setApiToken } from "./lib/api";
import { predictionRequest, PredictionRequestTimeout } from "./lib/predictionPolling";
import { mergeStudioCatalog } from "./lib/studioCatalog";
import { comparisonInputKey } from "./lib/comparisonSelection";
import { readQuantumSelection, saveQuantumSelection } from "./lib/quantumEvidence";
import { readWorkspace, workspaceFromHash, type WorkspaceTab } from "./lib/workspaceNavigation";
import type { Analysis, Compound, Job } from "./types/app";
import type { ExperimentalLigandReference, MolecularAtom, MolecularScene, StructureMatch, StructureMode, StructureResolution } from "./types/molecular";
import type { ProteinTarget } from "./lib/proteinTargets";
const MoleculeViewer = lazy(() => import("./components/MoleculeViewer"));
const DrugDesignPipeline = lazy(() => import("./components/DrugDesignPipeline"));
const navigation = [
  { id: "alphafold", icon: Atom, label: "AlphaFold 스튜디오" },
  { id: "design-pipeline", icon: FlaskConical, label: "신약 설계 파이프라인" },
  { id: "comparison", icon: GitCompareArrows, label: "성분 비교" },
  { id: "quantum", icon: Cpu, label: "양자 스튜디오" },
  { id: "discovery", icon: Layers3, label: "대규모 탐색" },
  { id: "validation", icon: Activity, label: "성능·약효 검증" },
  { id: "agents", icon: GitBranch, label: "에이전트 분석" },
  { id: "evidence", icon: Database, label: "실측 · 검증" },
  { id: "archive", icon: Layers3, label: "연구 기록" },
] as const;
const subtitles: Record<string, string> = {
  alphafold: "성분 하나를 선택해 분자를 분석하고, AF3 계산으로 표적과 함께 구조를 확인하세요.",
  "design-pipeline": "한약재 성분과 기존 의약품으로 후보를 설계하고, 구조 변화와 검증 근거를 함께 살펴보세요.",
  comparison: "비교할 성분을 고르고 구조 유사도와 비교 분석 결과를 확인하세요.",
  quantum: "분자 특징을 측정하고, 커널의 차이와 하드웨어 오차를 확인하세요.",
  discovery: "출처가 있는 자료를 모으고, 실제 생성 수치를 추적하세요.",
  validation: "실제 처리 성능과 후보별 생물학적 근거를 확인하세요.",
  agents: "AlphaFold의 준비·실행·출력 검증을 추적하고, 저장된 작업을 이어서 확인하세요.",
  evidence: "측정한 데이터로 예측의 범위를 검증합니다.",
  archive: "구조, 계산, 모델의 기록을 다시 탐색합니다.",
};
const sourceLabels = {
  rdkit_conformer: "RDKit 계산 conformer",
  alphafold3_prediction: "AlphaFold 3 예측 구조",
  experimental_pdb: "PDB 실험 구조",
};

export default function App() {
  const language = useLanguage();
  const [restoredSelection] = useState(readStudioSelection);
  const [tab, setTabState] = useState<WorkspaceTab>(readWorkspace);
  const setTab = useCallback((next: WorkspaceTab) => {
    setTabState(next);
    if (window.location.hash !== `#${next}`) window.location.hash = next;
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  }, []);
  useEffect(() => {
    const onHashChange = () => setTabState(workspaceFromHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  useEffect(() => {
    document.querySelector<HTMLElement>(`.sidebar [data-workspace="${tab}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
  }, [tab]);
  const [catalog, setCatalog] = useState<Compound[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [activeMolecule, setActiveMolecule] = useState<Compound | null>(restoredSelection?.compound || null);
  const [scene, setScene] = useState<MolecularScene | null>(null);
  const [atom, setAtom] = useState<MolecularAtom | null>(null);
  const [descriptors, setDescriptors] = useState<any>(null);
  const [descriptorError, setDescriptorError] = useState("");
  const [sceneLoading, setSceneLoading] = useState(false);
  const [structureMode, setStructureMode] = useState<StructureMode>(restoredSelection?.mode || "alphafold3_prediction");
  const [archiveStructure, setArchiveStructure] = useState<{ jobId: string; file: string } | null>(null);
  const [structureIssue, setStructureIssue] = useState<{ error: boolean; reason: string } | null>(null);
  const [structureMatches, setStructureMatches] = useState<StructureMatch[]>([]);
  const [structureReferences, setStructureReferences] = useState<ExperimentalLigandReference[]>([]);
  const [structureRevision, setStructureRevision] = useState(0);
  const [predictionResultJobId, setPredictionResultJobId] = useState<string | null>(restoredSelection?.predictionJobId || null);
  const [calculationFocusRevision, setCalculationFocusRevision] = useState(0);
  const [wideStudio, setWideStudio] = useState(false);
  const [candidates, setCandidates] = useState<Compound[]>([]);
  const [comparison, setComparison] = useState<any[]>([]);
  const [structureComparison, setStructureComparison] = useState<any[]>([]);
  const [comparedInputKey, setComparedInputKey] = useState("");
  const [comparing, setComparing] = useState(false);
  const comparisonRequest = useRef(0);
  const [libraryRevision, setLibraryRevision] = useState(0);
  const [composer, setComposer] = useState(false);
  const [settings, setSettings] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [health, setHealth] = useState<any>(null);
  const [llmStatus, setLlmStatus] = useState<any>(null);
  const [af3WorkflowId, setAF3WorkflowId] = useState(rememberedAF3Workflow);
  const [agentsView, setAgentsView] = useState<"af3" | "research">("af3");
  const selectAF3Workflow = useCallback((id: string) => { rememberAF3Workflow(id); setAF3WorkflowId(id); }, []);
  const openAF3Workflow = useCallback((id: string) => {
    selectAF3Workflow(id); setAgentsView("af3"); setTab("agents");
  }, [selectAF3Workflow, setTab]);
  const [analyses, setAnalyses] = useState<Analysis[]>([]);
  const [activeAnalysis, setActiveAnalysis] = useState<Analysis | null>(null);
  const activeAnalysisSelection = useRef<string | null>(readQuantumSelection("analysis"));
  const [jobs, setJobs] = useState<Job[]>([]);
  const [sequence, setSequence] = useState("");
  const [targetId, setTargetId] = useState(restoredSelection?.target || "P35354");
  const [targetDetails, setTargetDetails] = useState<ProteinTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const sceneRequest = useRef(0);
  const sceneController = useRef<AbortController | null>(null);
  const currentStructureSelection = useRef<{ compound: Compound | null; target: string; mode: StructureMode }>({ compound: activeMolecule, target: targetId.trim().toUpperCase(), mode: structureMode });
  currentStructureSelection.current = { compound: activeMolecule, target: targetId.trim().toUpperCase(), mode: structureMode };
  const calculationPanel = useRef<HTMLDivElement>(null);
  const conformers = useRef(
    new Map<string, { scene: MolecularScene; descriptors: any }>(),
  );
  const completedRuns = useRef(new Set<string>());
  const selected = catalog.filter((m) => selectedIds.includes(m.id));
  const currentComparisonKey = comparisonInputKey(selected);
  const canGenerate =
    selected.some((m) => m.category === "herbal" || m.category === "natural_product") &&
    selected.some((m) => m.category === "drug");
  const notify = useCallback(
    (text: string, error = false) => setToast({ text, error }),
    [],
  );
  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(
      () => setToast(null),
      toast.error ? 12000 : 5500,
    );
    return () => clearTimeout(id);
  }, [toast]);
  const refreshJobs = useCallback(async () => {
    try {
      setJobs(await api("/jobs"));
    } catch (error) {
      notify((error as Error).message, true);
    }
  }, [notify]);
  const updateAnalysis = useCallback(
    (run: Analysis) => {
      if (activeAnalysisSelection.current && activeAnalysisSelection.current !== run.id) return;
      activeAnalysisSelection.current = run.id;
      saveQuantumSelection("analysis", run.id);
      setActiveAnalysis(run);
      setAnalyses((old) =>
        [run, ...old.filter((item) => item.id !== run.id)].sort((a, b) =>
          b.created.localeCompare(a.created),
        ),
      );
      if (run.status === "completed" && !completedRuns.current.has(run.id)) {
        completedRuns.current.add(run.id);
        if (run.result?.candidates)
          setCandidates(normalizeCandidates(run.result.candidates));
        if (run.result?.comparisons) setComparison(run.result.comparisons);
        void refreshJobs();
      }
    },
    [refreshJobs],
  );
  const selectAnalysis = useCallback((id: string) => {
    activeAnalysisSelection.current = id;
    saveQuantumSelection("analysis", id);
    setActiveAnalysis((current) => current?.id === id ? current : null);
    void api<Analysis>(`/analyses/${encodeURIComponent(id)}`)
      .then(updateAnalysis)
      .catch((error) => {
        if (activeAnalysisSelection.current === id) notify(error.message, true);
      });
  }, [notify, updateAnalysis]);
  function clearStructureRequest() {
    sceneController.current?.abort();
    ++sceneRequest.current;
    setAtom(null);
    setSceneLoading(true);
    setScene(null);
    setStructureIssue(null);
    setStructureMatches([]);
  }
  function inspectMolecule(molecule: Compound) {
    if (molecule.smiles.length > 5000) {
      notify("이 구조는 현재 AlphaFold 스튜디오의 입력 크기 한도(SMILES 5,000자)를 초과합니다.", true);
      return;
    }
    if (!molecule.generated) setCatalog((previous) => mergeStudioCatalog(previous, [molecule]));
    clearStructureRequest();
    setDescriptorError("");
    setActiveMolecule(molecule);
    setArchiveStructure(null);
    setPredictionResultJobId(null);
    setDescriptors(molecule.descriptors || conformers.current.get(molecule.smiles)?.descriptors || null);
    setStructureRevision((revision) => revision + 1);
  }
  function toggleAnalysisCompound(molecule: Compound) {
    if (!selectedIds.includes(molecule.id) && molecule.category === "candidate") {
      notify("비교 입력에는 천연물 또는 기존 약물로 출처가 분류된 성분을 선택해 주세요.", true);
      return;
    }
    if (!selectedIds.includes(molecule.id) && molecule.smiles.length > 4096) {
      notify("이 구조는 비교 입력 크기 한도(SMILES 4,096자)를 초과합니다.", true);
      return;
    }
    if (!selectedIds.includes(molecule.id) && selectedIds.length >= 8) {
      notify("비교 입력은 최대 8개까지 선택할 수 있습니다.", true);
      return;
    }
    setCatalog((previous) => mergeStudioCatalog(previous, [molecule]));
    setSelectedIds((previous) => previous.includes(molecule.id)
      ? previous.filter((id) => id !== molecule.id)
      : previous.length < 8 ? [...previous, molecule.id] : previous);
  }
  function chooseStructureMode(mode: StructureMode) {
    if (!activeMolecule) return;
    clearStructureRequest();
    setStructureMode(mode);
    setArchiveStructure(null);
    setPredictionResultJobId(null);
    setStructureRevision((revision) => revision + 1);
  }
  function changeStructureTarget(value: string) {
    if (value !== targetId && activeMolecule && !archiveStructure) clearStructureRequest();
    if (value !== targetId) { setStructureReferences([]); setPredictionResultJobId(null); }
    setTargetId(value);
  }
  function inspectExperimentalReference(reference: ExperimentalLigandReference) {
    const molecule: Compound = { ...reference, name: reference.name, generated: false };
    setCatalog((previous) => [molecule, ...previous.filter((item) => item.id !== molecule.id)]);
    setStructureMode("experimental_pdb");
    setTargetId(reference.target_accession);
    inspectMolecule(molecule);
  }
  function showAF3WorkflowResult(workflow: AF3Workflow) {
    if (!trustedAF3WorkflowResult(workflow)) {
      notify("선택한 분자·표적과 일치하는 검증된 완료 결과가 필요합니다.", true);
      return;
    }
    const saved = workflow.request.compound;
    const molecule: Compound = {
      ...saved, smiles: workflow.request.canonical_smiles, category: saved.category || "candidate", generated: saved.category === "candidate",
    };
    inspectMolecule(molecule);
    setTargetId(workflow.request.target_accession);
    setStructureMode("alphafold3_prediction");
    setPredictionResultJobId(workflow.job_id);
    setTab("alphafold");
    void refreshJobs();
  }
  function openSelectedCalculation() {
    chooseStructureMode("alphafold3_prediction");
    setCalculationFocusRevision((revision) => revision + 1);
  }
  useEffect(() => {
    if (activeMolecule && !archiveStructure) saveStudioSelection({ compound: activeMolecule, target: targetId.trim().toUpperCase(), mode: structureMode, predictionJobId: predictionResultJobId });
  }, [activeMolecule, targetId, structureMode, predictionResultJobId, archiveStructure]);
  useEffect(() => {
    if (calculationFocusRevision && structureMode === "alphafold3_prediction" && tab === "alphafold") {
      calculationPanel.current?.scrollIntoView({ block: "start", behavior: "instant" });
      calculationPanel.current?.querySelector<HTMLElement>("h2")?.focus({ preventScroll: true });
    }
  }, [calculationFocusRevision, structureMode, tab]);
  useEffect(() => {
    const controller = new AbortController();
    void api<{ items: ExperimentalLigandReference[] }>(`/molecular/references?target_accession=${encodeURIComponent(targetId.trim().toUpperCase())}`, undefined, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setStructureReferences(result.items); })
      .catch(() => { if (!controller.signal.aborted) setStructureReferences([]); });
    return () => controller.abort();
  }, [targetId]);
  useEffect(() => {
    if (tab !== "alphafold") return;
    if (!activeMolecule && !archiveStructure) return;
    const controller = new AbortController();
    sceneController.current?.abort();
    sceneController.current = controller;
    const requestId = ++sceneRequest.current;
    const current = () => !controller.signal.aborted && sceneRequest.current === requestId;
    setScene(null);
    setAtom(null);
    setStructureIssue(null);
    setStructureMatches([]);
    setSceneLoading(true);
    setDescriptorError("");
    async function loadStructure() {
      try {
        if (archiveStructure) {
          setDescriptors(null);
          const value = await predictionRequest((signal) => api<MolecularScene>(`/molecular/jobs/${encodeURIComponent(archiveStructure.jobId)}/scene?file=${encodeURIComponent(archiveStructure.file)}`, undefined, { signal }), { signal: controller.signal, timeoutMs: 30_000 });
          if (current()) setScene(value);
          return;
        }
        const molecule = activeMolecule!;
        const cached = conformers.current.get(molecule.smiles);
        setDescriptors(cached?.descriptors || molecule.descriptors || null);
        if (structureMode === "rdkit_conformer") {
          let data = cached;
          if (!data) {
            const [newScene, desc] = await Promise.all([
              predictionRequest((signal) => api<MolecularScene>("/molecular/conformer", { smiles: molecule.smiles, seed: 42, include_hydrogens: true }, { signal }), { signal: controller.signal, timeoutMs: 30_000 }),
              predictionRequest((signal) => api("/molecules/describe", { smiles: molecule.smiles }, { signal }), { signal: controller.signal, timeoutMs: 30_000 }),
            ]);
            data = { scene: newScene, descriptors: desc };
            if (current()) conformers.current.set(molecule.smiles, data);
          }
          if (current()) { setScene({ ...data.scene, label: molecule.name }); setDescriptors(data.descriptors); setDescriptorError(""); }
          return;
        }
        if (!cached?.descriptors && !molecule.descriptors) {
          void predictionRequest((signal) => api("/molecules/describe", { smiles: molecule.smiles }, { signal }), { signal: controller.signal, timeoutMs: 30_000 })
            .then((value) => { if (current()) { setDescriptors(value); setDescriptorError(""); } })
            .catch((error) => { if (current()) setDescriptorError((error as Error).message); });
        }
        const resolved = structureMode === "alphafold3_prediction" && predictionResultJobId
          ? await predictionRequest((signal) => api<StructureResolution>(`/molecular/predictions/${encodeURIComponent(predictionResultJobId)}/scene`, undefined, { signal }), { signal: controller.signal, timeoutMs: 30_000 })
          : await predictionRequest((signal) => api<StructureResolution>("/molecular/resolve", {
            smiles: molecule.smiles, source: structureMode, target_accession: targetId.trim().toUpperCase(),
          }, { signal }), { signal: controller.signal, timeoutMs: 30_000 });
        if (!current()) return;
        if (resolved.available_references) setStructureReferences(resolved.available_references);
        if (resolved.status === "matched" && resolved.scene) {
          if (resolved.source !== structureMode || resolved.scene.source !== structureMode) throw new Error("응답의 구조 출처가 선택한 보기 모드와 일치하지 않습니다.");
          setScene(resolved.scene);
          setStructureMatches(resolved.matches || []);
        } else {
          const retryable = resolved.reason_code === "reference_fetch_failed" || resolved.reason_code === "reference_validation_failed";
          setStructureIssue({ error: retryable, reason: resolved.reason || (retryable ? "등록된 실험 구조를 불러오거나 확인하지 못했습니다. 다시 조회해 주세요." : "선택한 성분과 표적에 일치하는 저장 구조를 찾지 못했습니다.") });
        }
      } catch (problem) {
        if (current()) setStructureIssue({ error: true, reason: problem instanceof PredictionRequestTimeout
          ? "구조 조회 응답 시간이 초과되었습니다. 계산 작업은 유지됩니다. 구조 다시 조회 또는 연결 상태 새로 고침으로 결과를 확인하세요."
          : (problem as Error).message });
      } finally { if (current()) setSceneLoading(false); }
    }
    void loadStructure();
    return () => controller.abort();
  }, [activeMolecule, structureMode, targetId, archiveStructure, structureRevision, predictionResultJobId, tab, libraryRevision]);
  async function refreshAll() {
    setLibraryRevision((revision) => revision + 1);
    const tasks = await Promise.allSettled([
      api<Compound[]>("/catalog"),
      api("/health"),
      api("/llm/status"),
      api<{ analyses: Analysis[] }>("/analyses"),
      api<Job[]>("/jobs"),
    ]);
    const [cat, h, llm, runs, records] = tasks;
    if (cat.status === "fulfilled") {
      const currentCompound = currentStructureSelection.current.compound;
      setCatalog((previous) => mergeStudioCatalog(previous,
        currentCompound && !currentCompound.generated ? [currentCompound, ...cat.value] : cat.value));
      if (!activeMolecule && !scene && !archiveStructure && sceneRequest.current === 0 && cat.value[0]) inspectMolecule(cat.value[0]);
    }
    if (h.status === "fulfilled") setHealth(h.value);
    if (llm.status === "fulfilled") setLlmStatus(llm.value);
    if (runs.status === "fulfilled") {
      setAnalyses(runs.value.analyses);
      if (!activeAnalysis && runs.value.analyses[0]) {
        const restored = runs.value.analyses.find(run => run.id === activeAnalysisSelection.current) || runs.value.analyses[0];
        activeAnalysisSelection.current = restored.id;
        updateAnalysis(restored);
      }
    }
    if (records.status === "fulfilled") setJobs(records.value);
    const failure = tasks.find((r) => r.status === "rejected");
    if (failure?.status === "rejected") notify(failure.reason.message, true);
  }
  useEffect(() => {
    void refreshAll();
  }, []);
  useEffect(() => {
    if (
      !activeAnalysis ||
      !["queued", "running"].includes(activeAnalysis.status)
    )
      return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const run = await api<Analysis>(`/analyses/${activeAnalysis!.id}`);
        if (!disposed) updateAnalysis(run);
      } catch (error) {
        if (!disposed) notify((error as Error).message, true);
      }
      if (!disposed) timer = setTimeout(poll, 2200);
    }
    timer = setTimeout(poll, 1200);
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [activeAnalysis?.id, activeAnalysis?.status, updateAnalysis, notify]);
  async function showArchivedStructure(jobId: string, file: string) {
    clearStructureRequest();
    setTab("alphafold");
    setActiveMolecule(null);
    setPredictionResultJobId(null);
    setArchiveStructure({ jobId, file });
    setDescriptors(null);
    setStructureRevision((revision) => revision + 1);
  }
  async function generate() {
    setBusy(true);
    try {
      const job = await api("/workflows", {
        compounds: selected,
        target_id: targetId,
        max_candidates: 8,
      });
      setCandidates(normalizeCandidates(job.result.candidates));
      setComparison(job.result.comparison);
      void refreshJobs();
      notify(
        `부모 분자 조각을 재조합해 ${job.result.candidates.length}개의 계산 후보를 생성했습니다.`,
      );
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  async function compareStructures() {
    if (selected.length < 2 || comparing) return;
    const requestId = ++comparisonRequest.current;
    const inputs = selected;
    const inputKey = comparisonInputKey(inputs);
    setComparing(true);
    try {
      const result = await api<any[]>("/compare/structures", { compounds: inputs });
      if (comparisonRequest.current !== requestId) return;
      setStructureComparison(result);
      setComparedInputKey(inputKey);
      notify(`${result.length}개 성분 쌍의 구조 비교를 완료했습니다.`);
    } catch (error) {
      if (comparisonRequest.current === requestId) notify((error as Error).message, true);
    } finally {
      if (comparisonRequest.current === requestId) setComparing(false);
    }
  }
  async function startAnalysis(request: any) {
    const run = await api<Analysis>("/analyses", request);
    activeAnalysisSelection.current = run.id;
    updateAnalysis(run);
    setAgentsView("research");
    setTab("agents");
    notify(
      "분석을 시작했습니다. 각 단계의 실행 상태가 자동으로 업데이트됩니다.",
    );
  }
  const selectedBonds =
    atom && scene
      ? scene.bonds.filter((b) => b.source === atom.id || b.target === atom.id)
      : [];
  const activeMoleculeDisplayName = language === "en"
    ? activeMolecule?.name || activeMolecule?.name_ko
    : activeMolecule?.name_ko || activeMolecule?.name;
  return (
    <div className={`app-shell page-${tab}`} data-language={language}>
      <aside className="sidebar">
        <a
          className="brand-symbol"
          href="#"
          aria-label={tr("HerbFold 홈")}
          onClick={(event) => {
            event.preventDefault();
            setTab("alphafold");
          }}
        >
          <Leaf size={25} />
        </a>
        <nav>
          {tr(navigation.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${tab === item.id ? "active" : ""}`}
              onClick={() => setTab(item.id)}
              title={tr(item.label)}
              data-workspace={item.id}
              aria-current={tab === item.id ? "page" : undefined}
            >
              <item.icon size={22} />
              <span>{tr(item.label)}</span>
              {tr(tab === item.id && (
                <motion.span layoutId="nav-active" className="nav-active" />
              ))}
            </button>
          )))}
        </nav>
        <div className="sidebar-bottom">
          <button
            className="nav-item"
            title={tr("연산 엔진 설정")}
            onClick={() => setSettings(true)}
          >
            <Settings2 size={21} />
            <span>{tr("엔진 설정")}</span>
          </button>
          <span className="sidebar-version">HF / 02</span>
        </div>
      </aside>
      <main className="main-shell">
        <header className="topbar">
          <div className="wordmark">
            HerbFold<span>Astra</span>
            <span className="research-badge">{tr("분자 연구 워크스페이스")}</span>
          </div>
          <div className="topbar-actions">
            <LanguageSwitcher />
            <button className="model-status" onClick={() => setSettings(true)}>
              <span
                className={`status-dot ${llmStatus?.available ? "completed" : "pending"}`}
              />
              GPT-6 Astra
              <small>{tr(llmStatus?.available ? "연결됨" : "연결 확인 중")}</small>
              <ChevronRight size={13} />
            </button>
            <span className="topbar-divider" />
            <button className="icon-button studio-mobile-settings" aria-label={tr("연산 엔진 설정")} onClick={() => setSettings(true)}><Settings2 size={18} /></button>
            {tr(tab !== "alphafold" && tab !== "design-pipeline" && <button className="primary-button"
              onClick={() => tab === "comparison" ? setComposer(true) : setTab("comparison")}
              disabled={tab === "comparison" && selected.length < 2}>
              <GitCompareArrows size={16} /> {tr(tab === "comparison" ? "비교 분석 설정" : "성분 비교")}
            </button>)}
          </div>
        </header>
        <div className="page-content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">DISCOVERY / {tr(tab.toUpperCase())}</div>
              <h1>{tr(navigation.find((n) => n.id === tab)?.label)}</h1>
              <p>{tr(subtitles[tab])}</p>
            </div>
            <div className="workspace-status">
              <span
                className={`status-dot ${health ? "completed" : "pending"}`}
              />
              {tr(health ? "로컬 연구 환경 연결됨" : "연구 환경 확인 중")}
              <small>AlphaFold 3 · IBM Quantum</small>
            </div>
          </div>
          <AnimatePresence mode="wait">
            <motion.div
              key={tab}
              data-testid={`workspace-${tab}`}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -5 }}
              transition={{ duration: 0.22 }}
            >
              {tr(tab === "alphafold" && (
                <>
                  <StudioWorkspaceHeader compound={activeMolecule} target={targetId.trim().toUpperCase()} version={health?.alphafold?.version}
                    refreshKey={libraryRevision} onTargetDetails={setTargetDetails}
                    onTarget={changeStructureTarget} onQuantum={() => setTab("quantum")} onCalculate={openSelectedCalculation} />
                  <div className="studio-toolbar">
                    <div className="studio-single-summary">
                      <h2><Atom size={18} /> {tr(" 분자 구조 분석")}</h2>
                      <p>{tr("선택한 성분의 분자식·물성과 구조를 확인하세요.")}</p>
                    </div>
                    <motion.button
                      className="secondary-button studio-expand"
                      whileTap={{ scale: 0.97 }}
                      aria-pressed={wideStudio}
                      data-testid="studio-expand"
                      onClick={() => setWideStudio(!wideStudio)}
                    >
                      {tr(wideStudio ? (
                        <PanelsTopLeft size={16} />
                      ) : (
                        <Maximize2 size={16} />
                      ))}
                      {tr(wideStudio ? "패널 펼치기" : "분자 화면 넓게")}
                    </motion.button>
                  </div>
                  <div className={`studio-grid ${wideStudio ? "is-wide" : ""}`}>
                    <section className="card compound-library">
                      <StudioCompoundLibrary
                        compounds={catalog} selectedIds={selectedIds} activeId={activeMolecule?.id}
                        refreshKey={libraryRevision} onInspect={inspectMolecule} onToggle={toggleAnalysisCompound}
                        purpose="structure"
                        onAdd={() => setImportOpen(true)} onCompose={() => setTab("comparison")}
                      />
                      <StructureModeControls
                        mode={structureMode} name={activeMoleculeDisplayName}
                        target={targetId.trim().toUpperCase()} archive={!!archiveStructure}
                        references={structureReferences} selectedId={activeMolecule?.id}
                        onChange={chooseStructureMode} onReference={inspectExperimentalReference}
                      />
                    </section>
                    <section className="molecule-workbench">
                      <StructureContext mode={structureMode} name={activeMoleculeDisplayName}
                        target={targetId.trim().toUpperCase()} archive={archiveStructure} loading={sceneLoading}
                        matches={structureMatches} onChange={chooseStructureMode} />
                      {tr(structureIssue && !sceneLoading ? (
                        <StructureUnavailable name={activeMoleculeDisplayName}
                          mode={structureMode} error={structureIssue.error} reason={structureIssue.reason}
                          onFreeMolecule={() => chooseStructureMode("rdkit_conformer")}
                          onCalculateAF3={openSelectedCalculation}
                          onRetry={() => { clearStructureRequest(); setStructureRevision((revision) => revision + 1); }}
                          />
                      ) : (
                        <Suspense fallback={<div className="viewer-placeholder"><LoaderCircle className="spin" />{tr("3D 엔진 불러오는 중")}</div>}>
                          <MoleculeViewer scene={scene} loading={sceneLoading} onAtomSelect={setAtom} className="studio-viewer" displayLabel={archiveStructure ? undefined : activeMoleculeDisplayName ? `${activeMoleculeDisplayName}${structureMode === "rdkit_conformer" ? "" : ` · ${targetId.trim().toUpperCase()}`}` : undefined} />
                        </Suspense>
                      ))}
                      <div className="viewer-bottomline">
                        <span>
                          <Atom size={13} />{tr(" ")}
                          {tr(scene?.atoms.length.toLocaleString() || "—")} atoms{tr(" ")}
                          <i />
                          {tr(scene?.bonds.length.toLocaleString() || "—")} bonds
                        </span>
                        <button
                          className="text-button"
                          disabled={!scene}
                          onClick={() =>
                            scene && download(scene, "molecular-scene.json")
                          }
                        >
                          {tr("구조 JSON ")}<ArrowDownToLine size={13} />
                        </button>
                      </div>
                    </section>
                    <aside className="card molecule-inspector" data-testid="single-compound-analysis" data-compound-id={activeMolecule?.id || ""}>
                      {tr(descriptorError && <p className="single-analysis-error" role="alert">{tr("분자 분석을 완료하지 못했습니다. ")}{tr(descriptorError)}</p>)}
                      <div className="section-heading">
                        <div>
                          <span className="eyebrow">MOLECULAR PROFILE</span>
                          <h3>
                            {tr(atom
                              ? msg("선택 원자 · {0}", atom.name || atom.element)
                              : "구조의 특성")}
                          </h3>
                        </div>
                        {tr(atom ? (
                          <button
                            className="icon-button"
                            title={tr("원자 선택 해제")}
                            onClick={() => setAtom(null)}
                          >
                            <X size={16} />
                          </button>
                        ) : (
                          <FlaskConical size={21} />
                        ))}
                      </div>
                      {tr(atom ? (
                        <>
                          <div className="atom-symbol-card">
                            <strong>{tr(atom.element)}</strong>
                            <span>
                              Atom {tr(atom.id)}
                              <small>
                                {atom.residue_name} {atom.residue_id}{tr(" ")}
                                {tr(atom.chain_id
                                  ? `· Chain ${atom.chain_id}`
                                  : "")}
                              </small>
                            </span>
                          </div>
                          <dl className="property-list">
                            <div>
                              <dt>{tr("전하")}</dt>
                              <dd>{tr(atom.formal_charge ?? "—")}</dd>
                            </div>
                            {tr(["x", "y", "z"].map((key) => (
                              <div key={key}>
                                <dt>{tr(key.toUpperCase())}</dt>
                                <dd>{tr(formatNumber((atom as any)[key], 3))} Å</dd>
                              </div>
                            )))}
                            <div>
                              <dt>
                                {tr(scene?.source === "experimental_pdb"
                                  ? "B factor"
                                  : "pLDDT")}
                              </dt>
                              <dd>
                                {tr(formatNumber(
                                  scene?.source === "experimental_pdb"
                                    ? atom.b_factor
                                    : atom.confidence,
                                  2,
                                ))}
                                {tr(scene?.source === "experimental_pdb"
                                  ? " Å²"
                                  : "")}
                              </dd>
                            </div>
                          </dl>
                          <h4>{tr("공유 결합 연결")}</h4>
                          <div className="bond-details">
                            {tr(selectedBonds.map((bond, i) => {
                              const neighbor = scene?.atoms.find(
                                (a) =>
                                  a.id ===
                                  (bond.source === atom.id
                                    ? bond.target
                                    : bond.source),
                              );
                              return (
                                <div key={i}>
                                  <span>
                                    {neighbor?.name || neighbor?.element}{tr(" ")}
                                    <small>#{tr(neighbor?.id)}</small>
                                  </span>
                                  <span>
                                    {tr(bond.aromatic
                                      ? "방향족"
                                      : msg("{0}차", bond.order))}
                                    <small>
                                      {tr(formatNumber(bond.length_angstrom, 3))} Å
                                    </small>
                                  </span>
                                </div>
                              );
                            }))}
                            {tr(!selectedBonds.length && (
                              <p className="muted">
                                {tr("확인된 공유 결합이 없습니다.")}</p>
                            ))}
                          </div>
                        </>
                      ) : descriptors ? (
                        <>
                          <div className="formula-display">
                            <ChemicalFormula value={descriptors.formula} />
                            <span>MOLECULAR FORMULA</span>
                          </div>
                          <dl className="property-list">
                            {tr([
                              ["분자량", "molecular_weight", "g/mol"],
                              ["LogP", "logp", ""],
                              ["극성 표면적", "tpsa", "Å²"],
                              ["수소 결합 공여 / 수용", "hbond", ""],
                              ["회전 가능 결합", "rotatable_bonds", ""],
                              ["형식 전하", "formal_charge", ""],
                            ].map(([label, key, unit]) => (
                              <div key={key}>
                                <dt>{tr(label)}</dt>
                                <dd>
                                  {tr(key === "hbond"
                                    ? `${descriptors.hbd} / ${descriptors.hba}`
                                    : formatNumber(
                                        descriptors[key],
                                        [
                                          "rotatable_bonds",
                                          "formal_charge",
                                        ].includes(key)
                                          ? 0
                                          : 2,
                                      ))}{tr(" ")}
                                  <small>{tr(unit)}</small>
                                </dd>
                              </div>
                            )))}
                          </dl>
                          <div className="qed-indicator">
                            <div>
                              <span>{tr("QED · 화학적 적합도")}</span>
                              <strong>
                                {tr(formatNumber(descriptors.qed, 3))}
                              </strong>
                            </div>
                            <div className="indicator-track">
                              <motion.span
                                animate={{ width: `${descriptors.qed * 100}%` }}
                              />
                            </div>
                            <small>{tr("분자 특성 지표 · 결합 친화도 아님")}</small>
                          </div>
                          {tr(descriptors.alerts?.length > 0 && (
                            <div className="alert-box">
                              <strong>
                                {tr(descriptors.alerts.length)}{language === "en" ? " " : ""}{tr("개 구조 경고")}</strong>
                              <span>
                                {tr(descriptors.alerts
                                  .map((a: any) => a.description)
                                  .join(" · "))}
                              </span>
                            </div>
                          ))}
                          {tr(activeMolecule?.source_url && (
                            <a
                              className="source-link"
                              href={activeMolecule.source_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {tr("원본 자료 출처 ")}<ExternalLink size={13} />
                            </a>
                          ))}
                          <details className="smiles-details">
                            <summary>Canonical SMILES</summary>
                            <code>{tr(descriptors.canonical_smiles)}</code>
                          </details>
                          <button
                            className="secondary-button wide"
                            onClick={async () => {
                              try {
                                download(
                                  await fetchText("/molecular/sdf", {
                                    smiles: activeMolecule!.smiles,
                                    seed: 42,
                                  }),
                                  `${activeMolecule!.id}.sdf`,
                                  "chemical/x-mdl-sdfile",
                                );
                              } catch (error) {
                                notify((error as Error).message, true);
                              }
                            }}
                          >
                            <ArrowDownToLine size={14} /> {tr(" 단독 분자 3D SDF")}</button>
                        </>
                      ) : (
                        <>
                          <div className="empty-small">
                            <Atom size={33} />
                            <p>
                              {tr("원자를 클릭하면 좌표와")}<br />
                              {tr("실제 결합 연결을 확인합니다.")}</p>
                          </div>
                          {tr(scene && (
                            <dl className="property-list">
                              <div>
                                <dt>{tr("출처")}</dt>
                                <dd>{tr(sourceLabels[scene.source])}</dd>
                              </div>
                              <div>
                                <dt>{tr("체인")}</dt>
                                <dd>{tr(scene.chains?.length || "—")}</dd>
                              </div>
                              <div>
                                <dt>{tr("잔기")}</dt>
                                <dd>{tr(scene.residues?.length || "—")}</dd>
                              </div>
                            </dl>
                          ))}
                        </>
                      ))}
                      {tr(scene?.energy && (
                        <div className="energy-note">
                          <span>
                            {tr(scene.energy.method)} ·{tr(" ")}
                            {tr(scene.energy.converged ? "최적화 수렴" : "미수렴")}
                          </span>
                          <strong>
                            {tr(formatNumber(scene.energy.value_kcal_mol))} kcal/mol
                          </strong>
                          <small>
                            {tr("Conformer 내부 에너지 · 단백질 결합 에너지 아님")}</small>
                        </div>
                      ))}
                      {tr(scene?.warnings?.length ? (
                        <details className="geometry-warnings">
                          <summary>
                            {tr("구조·해석 주의사항 ")}{tr(scene.warnings.length)}{language === "en" ? " " : ""}{tr("개")}</summary>
                          {tr(scene.warnings.map((warning, i) => (
                            <p key={i}>{tr(warning)}</p>
                          )))}
                        </details>
                      ) : null)}
                    </aside>
                  </div>
                  {tr(activeMolecule && structureMode === "alphafold3_prediction" && !archiveStructure && <div ref={calculationPanel} className="afc-anchor">
                    <AF3WorkflowLauncher key={`${activeMolecule.smiles}\u001f${targetId.trim().toUpperCase()}`}
                      compound={activeMolecule} targetAccession={targetId.trim().toUpperCase()} preferredJobId={predictionResultJobId} refreshKey={libraryRevision}
                      targetName={targetDetails?.accession === targetId.trim().toUpperCase() ? targetDetails.name : undefined}
                      onWorkflow={openAF3Workflow} onOpenAgents={() => { setAgentsView("af3"); setTab("agents"); }} />
                  </div>)}
                  <AlphaFoldDiagnostics key={`${activeMolecule?.smiles || archiveStructure?.jobId || ""}|${targetId.trim().toUpperCase()}|${structureMode}`}
                    compound={activeMolecule} targetAccession={targetId.trim().toUpperCase()} structureMode={structureMode}
                    scene={scene} loading={sceneLoading} refreshKey={libraryRevision} health={health} issue={structureIssue?.reason || null} />


                </>
              ))}
              {tr(tab === "comparison" && <ComparisonWorkspace
                selected={selected} comparison={structureComparison} busy={comparing}
                comparisonCurrent={!!comparedInputKey && comparedInputKey === currentComparisonKey}
                onCompare={() => void compareStructures()} onCompose={() => setComposer(true)}
                onInspect={(compound) => { inspectMolecule(compound); setTab("alphafold"); }}
                onRemove={toggleAnalysisCompound}
                library={<section className="card compound-library">
                  <StudioCompoundLibrary purpose="comparison"
                    compounds={catalog} selectedIds={selectedIds} activeId={activeMolecule?.id}
                    refreshKey={libraryRevision} onInspect={(compound) => { inspectMolecule(compound); setTab("alphafold"); }}
                    onToggle={toggleAnalysisCompound} onAdd={() => setImportOpen(true)}
                    onCompose={() => setComposer(true)} />
                </section>}>
                <details className="comparison-advanced">
                  <summary>{tr("비교 성분으로 후보 설계 · 조각 재조합")}</summary>
                  <section className="candidate-section">
                    <div className="section-heading">
                      <div>
                        <span className="eyebrow">
                          FROM STRUCTURE TO POSSIBILITY
                        </span>
                        <h2>
                          {tr("다음 후보를 탐색하세요")}{tr(" ")}
                          <span className="count-badge">
                            {tr(candidates.length)}
                          </span>
                        </h2>
                        <p className="muted">
                          {tr("천연물과 기존 약물의 조각을 재조합한 계산 후보입니다. 신규성·합성 가능성은 후속 검증이 필요합니다.")}</p>
                      </div>
                      <div className="inline-actions">
                        <button
                          className="secondary-button"
                          disabled={busy || !canGenerate}
                          onClick={() => void generate()}
                        >
                          {tr(busy ? (
                            <LoaderCircle className="spin" size={16} />
                          ) : (
                            <FlaskConical size={16} />
                          ))}{tr(" ")}
                          {tr("조각 재조합")}</button>
                        <button
                          className="primary-button"
                          disabled={selected.length < 2} onClick={() => setComposer(true)}
                        >
                          <Sparkles size={16} /> {tr(" Astra와 탐색")}{tr(" ")}
                          <ArrowRight size={15} />
                        </button>
                      </div>
                    </div>
                    {tr(candidates.length ? (
                      <div className="candidate-grid">
                        {tr(candidates.map((candidate, index) => (
                          <motion.button
                            layout
                            key={candidate.id}
                            className={`candidate-card ${activeMolecule?.id === candidate.id ? "active" : ""}`}
                            initial={{ opacity: 0, y: 15 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: index * 0.035 }}
                            whileHover={{ y: -4 }}
                            whileTap={{ scale: 0.985 }}
                            onClick={() => { inspectMolecule(candidate); setTab("alphafold"); }}
                          >
                            <div className="candidate-card-top">
                              <span>
                                {tr("계산 후보 ")}{tr(String(index + 1).padStart(2, "0"))}
                              </span>
                              <ArrowRight size={16} />
                            </div>
                            <div className="candidate-motif">
                              <MoleculeSketch smiles={candidate.smiles} />
                            </div>
                            <h3>{candidate.name}</h3>
                            <p>
                              <ChemicalFormula
                                value={candidate.descriptors?.formula}
                              />
                              <small className="candidate-identifier">
                                {tr(candidate.id)}
                              </small>
                            </p>
                            <div className="candidate-stats">
                              <span>
                                MW
                                <strong>
                                  {tr(formatNumber(
                                    candidate.descriptors?.molecular_weight,
                                    1,
                                  ))}
                                </strong>
                              </span>
                              <span>
                                QED
                                <strong>
                                  {tr(formatNumber(candidate.descriptors?.qed, 3))}
                                </strong>
                              </span>
                              <span>
                                {tr("경고")}<strong>
                                  {tr(candidate.descriptors?.alerts?.length ?? "—")}
                                </strong>
                              </span>
                            </div>
                          </motion.button>
                        )))}
                      </div>
                    ) : (
                      <div className="candidate-empty">
                        <div className="candidate-empty-icon">
                          <FlaskConical size={28} />
                          <Plus size={12} />
                        </div>
                        <div>
                          <h3>{tr("입력 분자에서 시작하는 후보 설계")}</h3>
                          <p>
                            {tr("라이브러리에서 천연물과 기존 약물을 선택하고, 조각 재조합 또는 에이전트 분석을 실행하세요.")}</p>
                        </div>
                        <span className="micro-label">
                          SOURCE → COMPARE → DESIGN
                        </span>
                      </div>
                    ))}
                    {tr(comparison.length > 0 && (
                      <div className="comparison-strip">
                        {tr(comparison.slice(0, 6).map((row, i) => (
                          <div key={i}>
                            <span>
                              {row.herbal_name} <span className="muted">×</span>{tr(" ")}
                              {row.drug_name}
                            </span>
                            <strong>{tr(formatNumber(row.tanimoto, 3))}</strong>
                            <small>{tr("Morgan Tanimoto · 구조 유사도")}</small>
                          </div>
                        )))}
                      </div>
                    ))}
                  </section>
                </details>
              </ComparisonWorkspace>)}
              {tr(tab === "quantum" && <QuantumStudio analyses={analyses} activeAnalysis={activeAnalysis} jobs={jobs}
                onSelectAnalysis={selectAnalysis} onRefreshJobs={refreshJobs} onNotify={notify} />)}
              {tr(tab === "design-pipeline" && <Suspense fallback={<div className="card" role="status">{tr("신약 설계 도구를 불러오는 중…")}</div>}>
                <DrugDesignPipeline catalog={catalog} refreshKey={libraryRevision} notify={notify} onInspect={(compound, accession) => {
                  if (accession) setTargetId(accession);
                  if (compound.generated) setCandidates((previous) => [compound, ...previous.filter((item) => item.id !== compound.id)]);
                  setStructureMode("rdkit_conformer");
                  setTab("alphafold");
                  inspectMolecule(compound);
                }} />
              </Suspense>)}
              {tr(tab === "discovery" && (
                <DiscoveryPanel
                  references={catalog.filter((item) => item.category === "drug" && !item.id.startsWith("discovery_") && !item.id.startsWith("custom_"))}
                  onInspect={(compound) => {
                    if (compound.generated) setCandidates((previous) => [compound, ...previous.filter((item) => item.id !== compound.id)]);
                    else setCatalog((previous) => [compound, ...previous.filter((item) => item.id !== compound.id)]);
                    setTab("alphafold");
                    void inspectMolecule(compound);
                  }}
                  notify={notify}
                />
              ))}
              {tr(tab === "validation" && (
                <ValidationPanel onInspect={(compound) => {
                  setCandidates((previous) => [compound, ...previous.filter((item) => item.id !== compound.id)]);
                  setTab("alphafold");
                  void inspectMolecule(compound);
                }} />
              ))}
              {tr(tab === "agents" && <>
                <div className="agent-workspace-tabs" role="group" aria-label={tr("에이전트 분석 종류")}>
                  <button aria-pressed={agentsView === "af3"} onClick={() => setAgentsView("af3")}><Atom size={16} />{tr("AlphaFold 워크플로우")}</button>
                  <button aria-pressed={agentsView === "research"} onClick={() => setAgentsView("research")}><GitBranch size={16} />{tr("기존 연구·양자 분석")}</button>
                </div>
                {tr(agentsView === "af3" ? <AF3WorkflowWorkspace workflowId={af3WorkflowId} refreshKey={libraryRevision}
                  onSelectWorkflow={selectAF3Workflow} onViewResult={showAF3WorkflowResult} notify={notify} /> : <AnalysisPanel
                  analyses={analyses}
                  active={activeAnalysis}
                  onSelect={selectAnalysis}
                  onCreate={() => setTab("comparison")}
                  onUpdate={updateAnalysis}
                  onCandidates={(items) => {
                    setCandidates(normalizeCandidates(items));
                    setTab("alphafold");
                    if (items[0])
                      void inspectMolecule(normalizeCandidates(items)[0]);
                  }}
                  notify={notify}
                />)}
              </>)}
              {tr(tab === "evidence" && (
                <DataPanel candidates={candidates} notify={notify} />
              ))}
              {tr(tab === "archive" && (
                <ArchivePanel
                  jobs={jobs}
                  refresh={refreshJobs}
                  onViewStructure={showArchivedStructure}
                  notify={notify}
                />
              ))}
            </motion.div>
          </AnimatePresence>
          <footer className="workspace-footer">
            <span>
              <Leaf size={13} /> HerbFold Astra <i /> Evidence-led molecular
              research
            </span>
            <span>{tr("스튜디오 조건 선택 → 에이전트 준비·실행·검증 → 구조 결과")}</span>
          </footer>
        </div>
      </main>
      <AnimatePresence>
        {tr(composer && (
          <AnalysisComposer
            selected={selected}
            onClose={() => setComposer(false)}
            onSubmit={startAnalysis}
            sequence={sequence}
            setSequence={setSequence}
            targetId={targetId}
            setTargetId={changeStructureTarget}
            llmStatus={llmStatus}
            notify={notify}
          />
        ))}
        {tr(settings && (
          <EngineSettings
            health={health}
            llm={llmStatus}
            onClose={() => setSettings(false)}
            onRefresh={refreshAll}
            notify={notify}
          />
        ))}
        {tr(importOpen && (
          <ImportMolecule
            onClose={() => setImportOpen(false)}
            onImport={(m) => {
              setCatalog((old) => [...old.filter((c) => c.id !== m.id), m]);
              void inspectMolecule(m);
              setImportOpen(false);
              notify(
                "성분을 추가했습니다. 분자 분석과 이 성분의 AF3 계산을 확인하세요.",
              );
            }}
            notify={notify}
          />
        ))}
        {tr(toast && (
          <motion.div
            className={`toast ${toast.error ? "error" : ""}`}
            role={toast.error ? "alert" : "status"}
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 18 }}
          >
            {tr(toast.error ? <CircleHelp size={18} /> : <Check size={18} />)}
            <span>{tr(toast.text)}</span>
            <button onClick={() => setToast(null)} aria-label={tr("알림 닫기")}>
              <X size={16} />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
function ChemicalFormula({ value }: { value?: string }) {
  if (!value) return null;
  return (
    <span aria-label={tr(value)}>
      {tr(value
        .split(/(\d+)/)
        .map((part, i) =>
          /^\d+$/.test(part) ? (
            <sub key={i}>{tr(part)}</sub>
          ) : (
            <span key={i}>{tr(part)}</span>
          ),
        ))}
    </span>
  );
}
function normalizeCandidates(items: any[]): Compound[] {
  return items.map((item, i) => ({
    ...item,
    name: item.name || `HF-${String(i + 1).padStart(3, "0")}`,
    category: item.category || "herbal",
    generated: true,
  }));
}
function MoleculeSketch({ smiles }: { smiles: string }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let disposed = false;
    let url: string | undefined;
    void fetchText("/molecules/svg", { smiles })
      .then((svg) => {
        if (!disposed) {
          url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
          setSrc(url);
        }
      })
      .catch(() => {});
    return () => {
      disposed = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [smiles]);
  return src ? (
    <img src={src} alt={tr("RDKit 2D 분자 연결 구조")} />
  ) : (
    <Atom size={40} />
  );
}
function EngineSettings({ health, llm, onClose, onRefresh, notify }: any) {
  const dialogRef = useDialog<HTMLElement>(onClose);
  const [backends, setBackends] = useState<any>(null);
  const [checking, setChecking] = useState(false);
  const [token, setToken] = useState("");
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <motion.section
        role="dialog"
        aria-modal="true"
        aria-label={tr("연산 엔진 설정")}
        ref={dialogRef}
        tabIndex={-1}
        className="settings-dialog"
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="section-heading">
          <div>
            <span className="eyebrow">CONNECTED ENGINES</span>
            <h2>{tr("연산 환경")}</h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label={tr("엔진 설정 닫기")}
          >
            <X size={19} />
          </button>
        </div>
        <div className="engine-block">
          <Bot size={23} />
          <div>
            <h3>GPT-6 Astra</h3>
            <p>{tr(llm?.message || "모델 연결 상태를 확인 중입니다.")}</p>
            <span
              className={`status-pill ${llm?.available ? "completed" : "blocked"}`}
            >
              {tr(llm?.available ? "모델 접근 확인됨" : "모델 접근 미확인")}
            </span>
            <small>{tr("OpenAI Responses API · 정확한 모델 ID gpt-6-astra")}</small>
          </div>
        </div>
        <div className="engine-block">
          <Atom size={23} />
          <div>
            <h3>AlphaFold 3</h3>
            <p>{tr("공식 v3.0.4 실행 환경과 모델 파라미터를 사용합니다.")}</p>
            <details>
              <summary>{tr("설치·GPU·데이터베이스 상태")}</summary>
              <pre>{JSON.stringify(health?.alphafold || {}, null, 2)}</pre>
            </details>
          </div>
        </div>
        <div className="engine-block">
          <Cpu size={23} />
          <div>
            <h3>IBM Quantum</h3>
            <p>
              {tr("접근 가능한 실제 장비 중 가장 많은 큐빗을 가진 백엔드를 선택합니다. 회로 깊이와 QPU 사용 한도를 함께 적용합니다.")}</p>
            <button
              className="secondary-button"
              disabled={checking}
              onClick={async () => {
                setChecking(true);
                try {
                  setBackends(await api("/quantum/backends"));
                } catch (error) {
                  notify((error as Error).message, true);
                } finally {
                  setChecking(false);
                }
              }}
            >
              {tr(checking ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <Activity size={15} />
              ))}{tr(" ")}
              {tr("가용 장비 조회")}</button>
            {tr(backends && <pre>{JSON.stringify(backends, null, 2)}</pre>)}
          </div>
        </div>
        <label>
          {tr("로컬 API 인증 토큰")}{tr(" ")}
          <small>{tr("필요한 환경에서만 입력 · 메모리에만 보관")}</small>
          <input
            type="password"
            autoComplete="off"
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
        </label>
        <button
          className="primary-button wide"
          onClick={() => {
            setApiToken(token);
            void onRefresh();
            notify("연결 상태를 다시 확인합니다.");
          }}
        >
          {tr("연결 상태 새로 고침")}</button>
        <p className="field-help">
          {tr("OpenAI·IBM 비밀 키는 서버 환경에서 읽으며 브라우저로 전송하지 않습니다.")}</p>
      </motion.section>
    </div>
  );
}
function ImportMolecule({
  onClose,
  onImport,
  notify,
}: {
  onClose: () => void;
  onImport: (m: Compound) => void;
  notify: (text: string, error?: boolean) => void;
}) {
  const dialogRef = useDialog<HTMLElement>(onClose);
  const [name, setName] = useState("");
  const [smiles, setSmiles] = useState("");
  const [category, setCategory] = useState<"herbal" | "drug">("herbal");
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState<any>(null);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section
        role="dialog"
        aria-modal="true"
        aria-label={tr("분자 추가")}
        ref={dialogRef}
        tabIndex={-1}
        className="settings-dialog compact-dialog"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="section-heading">
          <h2>{tr("분자를 추가하세요")}</h2>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label={tr("분자 추가 닫기")}
          >
            <X size={19} />
          </button>
        </div>
        <label>
          {tr("분자 이름 / PubChem CID")}<input
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <button
          className="secondary-button"
          disabled={busy || !name}
          onClick={async () => {
            setBusy(true);
            try {
              const found = await api(
                `/sources/pubchem?query=${encodeURIComponent(name)}`,
              );
              setSource(found);
              setSmiles(found.smiles);
              if (found.name) setName(found.name);
            } catch (error) {
              notify((error as Error).message, true);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Search size={15} /> {tr(" PubChem 구조 확인")}</button>
        <label>
          SMILES
          <textarea
            rows={4}
            value={smiles}
            onChange={(event) => {
              setSmiles(event.target.value);
              setSource(null);
            }}
          />
        </label>
        <label>
          {tr("연구 입력 분류")}<select
            value={category}
            onChange={(event) =>
              setCategory(event.target.value as "herbal" | "drug")
            }
          >
            <option value="herbal">{tr("천연물")}</option>
            <option value="drug">{tr("기존 약물 비교군")}</option>
          </select>
        </label>
        <p className="field-help">
          {tr("분류는 연구자가 지정합니다. PubChem 조회가 한약재 내 함유나 효능을 입증하지는 않습니다.")}</p>
        <button
          className="primary-button wide"
          disabled={busy || !smiles || !name}
          onClick={async () => {
            setBusy(true);
            try {
              const desc = await api("/molecules/describe", { smiles });
              onImport({
                ...source,
                id: `custom_${crypto.randomUUID().slice(0, 8)}`,
                name,
                category,
                smiles: desc.canonical_smiles,
                descriptors: desc,
              });
            } catch (error) {
              notify((error as Error).message, true);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Plus size={16} /> {tr(" 분자 추가")}</button>
      </section>
    </div>
  );
}

