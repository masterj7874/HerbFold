import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  ArrowDownToLine, ArrowRight, BookOpen, Check, ChevronLeft, ChevronRight,
  Database, ExternalLink, FileCheck2, FlaskConical, GitBranch, Layers3,
  LoaderCircle, Pause, Play, Plus, RefreshCw, Search, ShieldCheck, Square,
} from "lucide-react";
import { api, download, formatNumber } from "../lib/api";
import { normalizeDiscoveryCompound } from "../lib/discoveryCompounds";
import type { Compound } from "../types/app";
import "./discovery-panel.css";

type Source = {
  id: string;
  name: string;
  url?: string;
  license?: string;
  automated: boolean;
  description?: string;
  kind?: "natural_product" | "drug" | "reference";
};
type Provenance = {
  source_id?: string;
  source?: string;
  source_name?: string;
  source_url?: string;
  url?: string;
  external_id?: string;
  license?: string;
  organism?: string;
  species?: string;
  taxon?: string;
  name?: string;
  organisms?: string;
  license_label?: string;
  metadata?: unknown;
  raw?: unknown;
  [key: string]: unknown;
};
type LibraryCompound = {
  id: string | number;
  canonical_smiles: string;
  inchikey?: string;
  display_name?: string;
  name?: string;
  formula?: string;
  provenance?: Provenance[];
  sources?: Provenance[];
  descriptors?: Record<string, unknown>;
};
type Ingestion = {
  id: string;
  source_id: string;
  status: string;
  processed?: number;
  inserted?: number;
  error?: string | null;
  created?: string;
  updated?: string;
  created_at?: string;
  updated_at?: string;
  downloaded_bytes?: number;
  total_bytes?: number;
  source_records?: number;
  invalid?: number;
};
type CounterName = "requested" | "attempted" | "generated" | "retained" | "rejected" | "duplicates";
type Campaign = {
  id: string;
  name?: string;
  status: string;
  reason?: string | null;
  error?: string | null;
  requested?: number;
  target_count?: number;
  attempted?: number;
  generated?: number;
  retained?: number;
  rejected?: number;
  duplicates?: number;
  max_attempts?: number;
  seed_limit?: number;
  created?: string;
  updated?: string;
  created_at?: string;
  updated_at?: string;
  counts?: Partial<Record<CounterName, number>>;
  counters?: Partial<Record<CounterName, number>>;
  config?: Record<string, unknown>;
  request?: Record<string, unknown>;
  bounded_product_ceiling?: number;
};
type Candidate = {
  id: string | number;
  name?: string;
  smiles: string;
  canonical_smiles?: string;
  descriptors?: Record<string, unknown>;
  lineage?: unknown;
  source_lineage?: unknown;
  parent_fragments?: unknown;
  parent_ids?: string[];
  parents?: string[];
};
type Summary = {
  catalog: {
    compound_count: number;
    provenance_count: number;
    sources?: unknown[];
    imports?: unknown[];
  };
  sources: Source[];
  campaigns: Campaign[];
  ingestions: Ingestion[];
};
type Page<T> = { items: T[]; total: number; limit: number; offset: number };
type Herb = { name_ko: string; query: string; taxa: string[]; source_url?: string; scope?: string };
type QueryResolution = { original_query: string; query: string; mapped: boolean; scope?: string; mapping_source_url?: string };
type PanelProps = {
  references: Compound[];
  onInspect: (compound: Compound) => void;
  notify: (text: string, error?: boolean) => void;
};

const PAGE_SIZE = 30;
const CANDIDATE_PAGE_SIZE = 20;
const ACTIVE = new Set(["queued", "running", "downloading", "importing", "parsing", "pausing", "pause_requested", "resuming", "cancel_requested"]);
const STATUSES: Record<string, string> = {
  queued: "실행 대기", running: "실행 중", downloading: "다운로드 중", importing: "자료 저장 중", parsing: "자료 처리 중",
  completed: "실행 종료", paused: "일시정지", pausing: "정지 중", pause_requested: "정지 요청됨",
  failed: "실행 실패", cancelled: "취소됨", cancel_requested: "취소 요청됨", interrupted: "중단됨",
  exhausted: "탐색 공간 소진", budget_exhausted: "실행 한도 도달", blocked: "확인 필요",
};
const COUNTERS: Array<{ key: CounterName; label: string; hint: string }> = [
  { key: "requested", label: "요청 목표", hint: "사용자가 설정한 목표 수이며 실제 생성 수가 아닙니다." },
  { key: "attempted", label: "시도", hint: "서버가 기록한 실제 구조 생성 시도 횟수" },
  { key: "generated", label: "생성", hint: "서버가 기록한 생성 구조 수" },
  { key: "retained", label: "보존", hint: "서버의 중복 제거·필터링 후 보존된 후보 수" },
  { key: "rejected", label: "필터 제외", hint: "화학 필터 등에서 제외된 후보 수" },
  { key: "duplicates", label: "중복", hint: "중복으로 분류된 구조 수" },
];

function number(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ko-KR") : "—";
}
function count(campaign: Campaign, key: CounterName): number | undefined {
  const value = campaign[key] ?? campaign.counts?.[key] ?? campaign.counters?.[key]
    ?? (key === "requested" ? campaign.target_count : undefined);
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}
function sourceName(source: Provenance, sources: Source[]): string {
  const id = source.source_id ?? source.source;
  return source.source_name ?? sources.find((item) => item.id === id)?.name ?? id ?? "출처 기록";
}
function externalUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : undefined; }
  catch { return undefined; }
}
function datetime(value: string | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function reasonLabel(reason: string): string {
  return ({
    target_reached: "요청한 보존 목표에 도달했습니다.",
    bounded_search_space_exhausted: "현재 시드·비교군·생성 설정으로 탐색할 수 있는 조합을 모두 처리했습니다.",
    attempt_budget: "설정된 시도 횟수 한도에 도달해 종료했습니다.",
    runtime_budget: "설정된 실행 시간 한도에 도달해 종료했습니다.",
    payload_storage_budget: "설정된 후보 데이터 저장 한도에 도달해 종료했습니다.",
    low_free_disk: "남은 디스크 공간이 실행 기준보다 적어 종료했습니다.",
    user_pause: "사용자 요청으로 일시정지했습니다.", user_resume: "사용자 요청으로 재개했습니다.",
    user_cancel: "사용자 요청으로 취소했습니다.",
    enumerator_version_mismatch: "생성기 버전이 달라 재현성을 위해 일시정지했습니다.",
  } as Record<string, string>)[reason] || reason;
}
function normalizedCompound(row: LibraryCompound | Candidate, generated: boolean, sourceHint = ""): Compound {
  if (!generated) return normalizeDiscoveryCompound(row as LibraryCompound, { source: sourceHint });
  const smiles = "smiles" in row ? row.canonical_smiles || row.smiles : row.canonical_smiles;
  const provenance = "sources" in row ? row.sources ?? row.provenance ?? [] : [];
  const isDrugReference = sourceHint === "chembl-approved" || (provenance.length > 0 && provenance.every((record) => record.source_id === "chembl-approved"));
  const selectedSource = provenance.find((record) => record.source_id === sourceHint && externalUrl(record.source_url || record.url))
    ?? provenance.find((record) => externalUrl(record.source_url || record.url));
  return {
    ...row,
    id: `${generated ? "campaign" : "discovery"}_${row.id}`,
    name: ("display_name" in row && row.display_name) || row.name || (generated ? candidateName(row) : `구조 ${row.id}`),
    smiles,
    category: generated ? "candidate" : isDrugReference ? "drug" : "natural_product",
    generated,
    source_classification: generated ? "generated_hypothesis" : isDrugReference ? "drug_reference_record" : "natural_product_record",
    source_url: generated ? undefined : externalUrl(selectedSource?.source_url || selectedSource?.url),
    source_scope: generated ? "Unvalidated generated candidate; natural occurrence is not established"
      : isDrugReference ? "Drug reference record; approved history does not establish current marketing authorization"
      : "Source-reported natural product; specific herbal occurrence and efficacy are not inferred",
    herbal_association_verified: false,
    descriptors: row.descriptors,
  };
}
function candidateName(row: { id: string | number; name?: string }): string {
  return row.name || `HF-${String(row.id).replace(/^campaign-/, "").slice(0, 10).toUpperCase()}`;
}
function candidateLineage(row: Candidate): unknown {
  return row.lineage ?? (row.source_lineage || row.parent_fragments ? {
    parent_ids: row.parent_ids, source_lineage: row.source_lineage, parent_fragments: row.parent_fragments,
  } : undefined);
}

export default function DiscoveryPanel({ references, onInspect, notify }: PanelProps) {
  const [section, setSection] = useState<"library" | "campaigns">("library");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [summaryError, setSummaryError] = useState("");
  const [herbs, setHerbs] = useState<Herb[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [library, setLibrary] = useState<Page<LibraryCompound> & { query_resolution?: QueryResolution }>({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 });
  const [libraryLoading, setLibraryLoading] = useState(true);
  const [libraryError, setLibraryError] = useState("");
  const [libraryRefresh, setLibraryRefresh] = useState(0);
  const [ingestionLimit, setIngestionLimit] = useState("100000");
  const [ingestAll, setIngestAll] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [selectedCampaign, setSelectedCampaign] = useState<string | null>(null);
  const [campaignDetail, setCampaignDetail] = useState<Campaign | null>(null);
  const [campaignError, setCampaignError] = useState("");
  const [candidatePage, setCandidatePage] = useState<Page<Candidate>>({ items: [], total: 0, limit: CANDIDATE_PAGE_SIZE, offset: 0 });
  const [candidateOffset, setCandidateOffset] = useState(0);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [candidateError, setCandidateError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [campaignName, setCampaignName] = useState("천연물–약물 후보 탐색");
  const [targetCount, setTargetCount] = useState("1000000");
  const [seedLimit, setSeedLimit] = useState("200");
  const [maxAttempts, setMaxAttempts] = useState("100000");
  const [seedSource, setSeedSource] = useState("");
  const [seedSearch, setSeedSearch] = useState("");
  const [referenceLimit, setReferenceLimit] = useState("50");
  const [includeImportedReferences, setIncludeImportedReferences] = useState(true);
  const [maxRuntime, setMaxRuntime] = useState("3600");
  const [maxStorage, setMaxStorage] = useState("1024");
  const [referenceIds, setReferenceIds] = useState<string[]>(() => references.some((item) => item.id === "aspirin") ? ["aspirin"] : references.slice(0, 1).map((item) => item.id));
  const summaryInFlight = useRef(false);
  const summaryAbort = useRef<AbortController | null>(null);
  const previousCompoundCount = useRef<number | undefined>(undefined);
  const reducedMotion = useReducedMotion();
  const sources = summary?.sources ?? [];
  const campaigns = summary?.campaigns ?? [];
  const ingestions = summary?.ingestions ?? [];
  const campaign = campaignDetail?.id === selectedCampaign ? campaignDetail : campaigns.find((item) => item.id === selectedCampaign) ?? null;

  const refreshSummary = useCallback(async (background = false) => {
    if (summaryInFlight.current) return;
    summaryInFlight.current = true;
    const controller = new AbortController();
    summaryAbort.current = controller;
    if (!background) setRefreshing(true);
    try {
      const value = await api<Summary>("/discovery/summary", undefined, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setSummary(value);
      setSummaryError("");
      setSelectedCampaign((previous) => previous ?? value.campaigns?.[0]?.id ?? null);
      if (previousCompoundCount.current !== undefined && previousCompoundCount.current !== value.catalog.compound_count) setLibraryRefresh((previous) => previous + 1);
      previousCompoundCount.current = value.catalog.compound_count;
    } catch (error) {
      if (!controller.signal.aborted) setSummaryError((error as Error).message);
    } finally {
      if (summaryAbort.current === controller) { summaryInFlight.current = false; setRefreshing(false); }
    }
  }, []);

  useEffect(() => {
    void refreshSummary();
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refreshSummary(true); }, 5000);
    return () => { window.clearInterval(timer); summaryAbort.current?.abort(); summaryInFlight.current = false; };
  }, [refreshSummary]);

  useEffect(() => {
    const controller = new AbortController();
    void api<{ items: Herb[] }>("/discovery/herbs", undefined, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setHerbs(value.items); })
      .catch(() => { /* Optional reference dictionary; ordinary text search remains available. */ });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLibraryLoading(true);
    setLibraryError("");
    const params = new URLSearchParams({ search, source: sourceFilter, limit: String(PAGE_SIZE), offset: String(offset) });
    void api<Page<LibraryCompound> & { query_resolution?: QueryResolution }>(`/discovery/compounds?${params}`, undefined, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setLibrary(value); })
      .catch((error: Error) => { if (!controller.signal.aborted) setLibraryError(error.message); })
      .finally(() => { if (!controller.signal.aborted) setLibraryLoading(false); });
    return () => controller.abort();
  }, [search, sourceFilter, offset, libraryRefresh]);

  useEffect(() => {
    if (!selectedCampaign || section !== "campaigns") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const value = await api<Campaign>(`/discovery/campaigns/${encodeURIComponent(selectedCampaign!)}`, undefined, { signal: controller.signal });
        if (!controller.signal.aborted) { setCampaignDetail(value); setCampaignError(""); }
      } catch (error) { if (!controller.signal.aborted) setCampaignError((error as Error).message); }
      if (!controller.signal.aborted) timer = setTimeout(load, 4000);
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [selectedCampaign, section]);

  const retainedCount = campaign ? count(campaign, "retained") : undefined;
  useEffect(() => {
    if (!selectedCampaign || section !== "campaigns") return;
    const controller = new AbortController();
    setCandidateLoading(true);
    setCandidateError("");
    void api<Page<Candidate>>(`/discovery/campaigns/${encodeURIComponent(selectedCampaign)}/candidates?limit=${CANDIDATE_PAGE_SIZE}&offset=${candidateOffset}`, undefined, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setCandidatePage(value); })
      .catch((error: Error) => { if (!controller.signal.aborted) setCandidateError(error.message); })
      .finally(() => { if (!controller.signal.aborted) setCandidateLoading(false); });
    return () => controller.abort();
  }, [selectedCampaign, section, candidateOffset, retainedCount]);

  const actualRetained = useMemo(() => campaigns.reduce((total, item) => total + (count(item, "retained") ?? 0), 0), [campaigns]);
  const activeCampaignCount = campaigns.filter((item) => ACTIVE.has(item.status)).length;
  const activeIngestions = ingestions.filter((item) => ACTIVE.has(item.status));
  const transition = { duration: reducedMotion ? 0 : 0.18 };

  async function ingest(source: Source) {
    const limit = Number(ingestionLimit);
    if (!ingestAll && (!Number.isSafeInteger(limit) || limit < 1 || limit > 100000000)) { notify("수집 한도를 1 이상의 정수로 입력하세요.", true); return; }
    setBusy(`ingestion:${source.id}`);
    try {
      const value = await api<Ingestion>("/discovery/ingestions", { source_id: source.id, max_records: ingestAll ? null : limit });
      setSummary((previous) => previous ? { ...previous, ingestions: [value, ...previous.ingestions.filter((item) => item.id !== value.id)] } : previous);
      notify(`${source.name} 수집을 시작했습니다. 처리·추가 수치를 실행 기록에서 확인할 수 있습니다.`);
      await refreshSummary();
    } catch (error) { notify((error as Error).message, true); }
    finally { setBusy(null); }
  }
  async function createCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const requested = Number(targetCount), seeds = Number(seedLimit), attempts = Number(maxAttempts), drugLimit = Number(referenceLimit);
    if (![requested, seeds, attempts, drugLimit, Number(maxRuntime), Number(maxStorage)].every((value) => Number.isSafeInteger(value) && value > 0)) {
      notify("목표·시드·시도 한도를 1 이상의 정수로 입력하세요.", true); return;
    }
    if (!includeImportedReferences && !referenceIds.length) { notify("비교할 기존 약물을 하나 이상 선택하세요.", true); return; }
    setBusy("create-campaign");
    try {
      const value = await api<Campaign>("/discovery/campaigns", {
        name: campaignName.trim(), target_count: requested, seed_limit: seeds,
        ...(includeImportedReferences ? {} : { reference_ids: referenceIds }),
        reference_limit: drugLimit, max_attempts: attempts,
        seed_source: seedSource || null, seed_search: seedSearch.trim(),
        max_runtime_seconds: Number(maxRuntime), max_storage_mb: Number(maxStorage),
      });
      setSelectedCampaign(value.id); setCampaignDetail(value); setCandidateOffset(0); setShowCreate(false);
      setSummary((previous) => previous ? { ...previous, campaigns: [value, ...previous.campaigns.filter((item) => item.id !== value.id)] } : previous);
      setSection("campaigns");
      notify("생성 캠페인을 시작했습니다. 설정 목표와 실제 보존 수를 구분해 표시합니다.");
      await refreshSummary();
    } catch (error) { notify((error as Error).message, true); }
    finally { setBusy(null); }
  }
  async function campaignAction(action: "pause" | "resume" | "cancel") {
    if (!campaign) return;
    setBusy(`campaign:${action}`);
    try {
      const value = await api<Campaign>(`/discovery/campaigns/${encodeURIComponent(campaign.id)}/${action}`, {});
      setCampaignDetail(value);
      await refreshSummary();
      notify(action === "pause" ? "일시정지를 요청했습니다." : action === "resume" ? "캠페인 재개를 요청했습니다." : "캠페인 취소를 요청했습니다.");
    } catch (error) { notify((error as Error).message, true); }
    finally { setBusy(null); }
  }
  function selectCampaign(id: string) {
    if (id === selectedCampaign) return;
    setSelectedCampaign(id); setCampaignDetail(null); setCandidateOffset(0); setCandidatePage({ items: [], total: 0, offset: 0, limit: CANDIDATE_PAGE_SIZE });
  }

  return <div className="discovery-page" data-testid="discovery-panel">
    <datalist id="discovery-herb-suggestions">{herbs.map((herb) => <option key={herb.name_ko} value={herb.name_ko}>{herb.taxa.join(" · ")}</option>)}</datalist>
    <div className="ds-intro">
      <div><span className="ds-eyebrow">DISCOVERY AT SCALE</span><h2>자료에서 시작하는 대규모 후보 탐색</h2><p>천연물 구조와 출처를 축적하고, 기존 약물과 비교할 후보를 캠페인별로 생성합니다.</p></div>
      <button className="ds-button ds-secondary" disabled={refreshing} onClick={() => { void refreshSummary(); setLibraryRefresh((value) => value + 1); }} data-testid="discovery-refresh">
        <RefreshCw size={17} className={refreshing ? "ds-spin" : ""} />새로 고침
      </button>
    </div>
    {summaryError && <div className="ds-error" role="alert"><ShieldCheck size={18} /><span>자료 연결을 확인할 수 없습니다. {summaryError}</span></div>}
    <div className="ds-metrics" aria-label="현재 저장된 실제 수치">
      <Metric icon={<Database size={21} />} label="등록된 고유 구조" value={summary ? number(summary.catalog.compound_count) : "—"} detail="자료 라이브러리의 실제 레코드" />
      <Metric icon={<BookOpen size={21} />} label="출처 연결" value={summary ? number(summary.catalog.provenance_count) : "—"} detail="구조에 연결된 출처 기록" />
      <Metric icon={<FlaskConical size={21} />} label="캠페인별 보존 합계" value={summary ? number(actualRetained) : "—"} detail="캠페인 간 중복이 포함될 수 있음" />
      <Metric icon={<Layers3 size={21} />} label="실행 중인 캠페인" value={summary ? number(activeCampaignCount) : "—"} detail={`${number(activeIngestions.length)}개 자료 수집 작업 진행 중`} />
    </div>
    <div className="ds-tabs" role="tablist" aria-label="대규모 탐색 화면" onKeyDown={(event) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); const next = event.key === "Home" ? "library" : event.key === "End" ? "campaigns" : section === "library" ? "campaigns" : "library"; setSection(next); document.getElementById(`discovery-${next}-tab`)?.focus(); } }}>
      <button role="tab" id="discovery-library-tab" aria-controls="discovery-library" aria-selected={section === "library"} tabIndex={section === "library" ? 0 : -1} className={section === "library" ? "is-active" : ""} onClick={() => setSection("library")} data-testid="discovery-library-tab"><Database size={17} />자료 라이브러리<span>{summary ? number(summary.catalog.compound_count) : "—"}</span></button>
      <button role="tab" id="discovery-campaigns-tab" aria-controls="discovery-campaigns" aria-selected={section === "campaigns"} tabIndex={section === "campaigns" ? 0 : -1} className={section === "campaigns" ? "is-active" : ""} onClick={() => setSection("campaigns")} data-testid="discovery-campaigns-tab"><GitBranch size={17} />생성 캠페인<span>{number(campaigns.length)}</span></button>
    </div>

    {section === "library" && <div id="discovery-library" role="tabpanel" aria-labelledby="discovery-library-tab" className="ds-library-layout">
      <section className="ds-card ds-library-card">
        <div className="ds-section-heading"><div><span className="ds-eyebrow">STRUCTURE LIBRARY</span><h3>구조와 출처를 함께 탐색하세요</h3></div><span className="ds-tag"><FileCheck2 size={14} />출처 보존</span></div>
        <div className="ds-scope-note"><BookOpen size={18} /><p>천연물과 기존 약물의 구조 레코드입니다. 천연물 항목과 특정 한약재의 함유 근거는 구분해 확인하세요.</p></div>
        <form className="ds-library-search" onSubmit={(event) => { event.preventDefault(); setSearch(query.trim()); setOffset(0); }}>
          <label className="ds-search-field"><Search size={18} /><span className="ds-sr-only">이름·생물종·InChIKey 검색</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="약재 한글명 · 생물종 · InChIKey" list="discovery-herb-suggestions" data-testid="discovery-search" /></label>
          <label className="ds-source-filter"><span className="ds-sr-only">데이터 출처</span><select value={sourceFilter} onChange={(event) => { setSourceFilter(event.target.value); setOffset(0); }} data-testid="discovery-source-filter"><option value="">전체 출처</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
          <button type="submit" className="ds-button ds-primary" data-testid="discovery-search-submit">검색</button>
        </form>
        {!libraryLoading && library.query_resolution?.mapped && <div className="ds-query-resolution" data-testid="discovery-query-resolution"><BookOpen size={14} /><span>약재명 연결: <strong>{library.query_resolution.original_query}</strong> → {library.query_resolution.query}</span>{externalUrl(library.query_resolution.mapping_source_url) && <a href={externalUrl(library.query_resolution.mapping_source_url)} target="_blank" rel="noopener noreferrer" title={library.query_resolution.scope}>연결 근거<ExternalLink size={12} /></a>}</div>}<div className="ds-table-status" aria-live="polite"><span>{libraryLoading ? "자료를 불러오는 중입니다." : `검색 결과 ${number(library.total)}개`}</span>{libraryLoading && <LoaderCircle size={15} className="ds-spin" />}</div>
        {libraryError ? <div className="ds-error" role="alert">{libraryError}</div> : !libraryLoading && library.items.length === 0 ? <Empty icon={<Database size={30} />} title={search || sourceFilter ? "일치하는 구조가 없습니다" : "아직 등록된 구조가 없습니다"} description={search || sourceFilter ? "검색어와 데이터 출처를 바꿔 확인하세요." : "데이터 출처에서 수집을 시작하면 구조와 원문 연결이 이곳에 쌓입니다."} /> :
          <div className="ds-table-scroll" aria-busy={libraryLoading}><table className="ds-table"><thead><tr><th scope="col">분자 구조</th><th scope="col">출처와 연결 근거</th><th scope="col"><span className="ds-sr-only">구조 탐색</span></th></tr></thead><tbody>
            {library.items.map((row) => <tr key={row.id}><td><strong>{row.display_name || row.name || `구조 ${row.id}`}</strong><span className="ds-formula">{row.formula || "분자식 미제공"}</span><code className="ds-smiles" title={row.canonical_smiles}>{row.canonical_smiles}</code>{row.inchikey && <span className="ds-inchikey">{row.inchikey}</span>}</td><td><ProvenanceDetails compoundId={row.id} provenance={row.provenance ?? row.sources ?? []} sources={sources} /></td><td><button className="ds-open-button" aria-label={`${row.display_name || row.name || `구조 ${row.id}`} 스튜디오에서 보기`} onClick={() => onInspect(normalizedCompound(row, false, sourceFilter))}><span>3D 보기</span><ArrowRight size={17} /></button></td></tr>)}
          </tbody></table></div>}
        <Pagination total={library.total} offset={library.offset} count={library.items.length} size={PAGE_SIZE} loading={libraryLoading} onChange={setOffset} label="구조 라이브러리" />
      </section>

      <aside className="ds-sources">
        <section className="ds-card"><div className="ds-section-heading"><div><span className="ds-eyebrow">DATA SOURCES</span><h3>수집 가능한 자료</h3></div><BookOpen size={22} /></div>
          <label className="ds-collection-toggle"><input type="checkbox" checked={ingestAll} onChange={(event) => setIngestAll(event.target.checked)} data-testid="discovery-ingest-all" /><span>출처 전체 레코드 수집</span></label><label className="ds-field">수집 작업당 최대 레코드<input type="number" min={1} max={100000000} step={1} disabled={ingestAll} value={ingestionLimit} onChange={(event) => setIngestionLimit(event.target.value)} data-testid="discovery-ingestion-limit" /><small>{ingestAll ? "전체 수집은 원본 릴리스의 크기에 따라 시간과 저장 공간이 필요합니다." : "처리 레코드 한도이며, 중복 제거 후 추가되는 고유 구조 수와 다릅니다."} 각 출처의 라이선스를 확인하세요.</small></label>{activeIngestions.length > 0 && <p className="ds-muted">자료 수집은 한 번에 한 출처씩 진행합니다.</p>}
          <div className="ds-source-list">{sources.map((source) => {
            const running = ingestions.some((item) => item.source_id === source.id && ACTIVE.has(item.status));
            const url = externalUrl(source.url);
            return <article className="ds-source-card" key={source.id}><div className="ds-source-title"><h4>{source.name}</h4>{url && <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`${source.name} 원문 열기`}><ExternalLink size={16} /></a>}</div><p>{source.description || "자료의 범위와 수록 기준은 원문에서 확인하세요."}</p><div className="ds-license"><ShieldCheck size={13} /><span>{source.license || "라이선스는 원문에서 확인"}</span></div>{source.automated ? <button className="ds-button ds-secondary ds-wide" disabled={!!busy || activeIngestions.length > 0} onClick={() => void ingest(source)} data-testid={`discovery-ingest-${source.id}`}>{busy === `ingestion:${source.id}` || running ? <LoaderCircle size={15} className="ds-spin" /> : <ArrowDownToLine size={15} />}{running ? "수집 작업 진행 중" : "자료 수집 시작"}</button> : <span className="ds-manual-source">원문에서 접근·이용 조건 확인</span>}</article>;
          })}{!summary && !summaryError && <p className="ds-muted">데이터 출처를 불러오는 중입니다.</p>}</div>
        </section>
        <section className="ds-card ds-ingestions"><div className="ds-section-heading"><h3>자료 수집 기록</h3><span className="ds-small-count">{number(ingestions.length)}건</span></div>{ingestions.length ? <div className="ds-ingestion-list">{ingestions.slice(0, 12).map((job) => <article key={job.id}><div className="ds-job-heading"><strong>{sources.find((source) => source.id === job.source_id)?.name ?? job.source_id}</strong><Status value={job.status} /></div><p>처리 <b>{number(job.processed)}</b> · 고유 구조 추가 <b>{number(job.inserted)}</b></p>{job.downloaded_bytes != null && job.status === "downloading" && <p>다운로드 <b>{(job.downloaded_bytes / 1024 / 1024).toFixed(1)} MB</b>{job.total_bytes ? ` / ${(job.total_bytes / 1024 / 1024).toFixed(1)} MB` : ""}</p>}{job.source_records != null && <p>출처 기록 추가 {number(job.source_records)}건{job.invalid != null ? ` · 구조 누락·검증 제외 ${number(job.invalid)}건` : ""}</p>}<small>{datetime(job.updated || job.created)}</small>{job.error && <div className="ds-inline-error">{job.error}</div>}</article>)}</div> : <p className="ds-muted">자료 수집을 시작하면 실제 처리·추가 수가 표시됩니다.</p>}</section>
      </aside>
    </div>}

    {section === "campaigns" && <div id="discovery-campaigns" role="tabpanel" aria-labelledby="discovery-campaigns-tab" className="ds-campaign-page">
      <div className="ds-campaign-banner"><div><span className="ds-eyebrow">BOUNDED, TRACEABLE GENERATION</span><h3>큰 목표를 설정하고, 실제 결과를 추적하세요.</h3><p>100만·1억은 요청 목표입니다. 실제 보존 수는 입력 다양성, 중복 제거, 화학 필터와 시도 한도에 따라 달라집니다.</p></div><button className="ds-button ds-primary" onClick={() => setShowCreate(!showCreate)} aria-expanded={showCreate || !campaigns.length} aria-controls="discovery-create-campaign" data-testid="discovery-new-campaign"><Plus size={17} />새 캠페인</button></div>
      <AnimatePresence>{(showCreate || !campaigns.length) && <motion.form id="discovery-create-campaign" className="ds-card ds-create-form" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={transition} onSubmit={createCampaign} data-testid="discovery-campaign-form">
        <div className="ds-section-heading"><div><span className="ds-eyebrow">NEW GENERATION CAMPAIGN</span><h3>탐색 범위와 실행 한도를 정하세요</h3></div><GitBranch size={23} /></div>
        <div className="ds-form-columns"><div><label className="ds-field">캠페인 이름<input value={campaignName} onChange={(event) => setCampaignName(event.target.value)} maxLength={120} required data-testid="discovery-campaign-name" /></label><fieldset className="ds-target-presets"><legend>요청 목표 빠른 선택</legend>{[{ value: "10000", label: "1만" }, { value: "1000000", label: "100만" }, { value: "100000000", label: "1억" }].map((preset) => <button type="button" key={preset.value} className={targetCount === preset.value ? "is-active" : ""} aria-pressed={targetCount === preset.value} onClick={() => setTargetCount(preset.value)} data-testid={`discovery-target-${preset.value}`}>{preset.label}<small>개 목표</small></button>)}</fieldset><label className="ds-field">요청 목표 수<input type="number" min={1} max={100000000} step={1} required value={targetCount} onChange={(event) => setTargetCount(event.target.value)} data-testid="discovery-target-count" /></label><div className="ds-budget-fields"><label className="ds-field">천연물 시드 출처<select value={seedSource} onChange={(event) => setSeedSource(event.target.value)} data-testid="discovery-seed-source"><option value="">모든 천연물 출처</option>{sources.filter((source) => source.kind === "natural_product").map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label><label className="ds-field">시드 이름 · 생물종 검색<input value={seedSearch} onChange={(event) => setSeedSearch(event.target.value)} maxLength={500} placeholder="예: 감초 / Glycyrrhiza" list="discovery-herb-suggestions" data-testid="discovery-seed-search" />{herbs.some((herb) => herb.name_ko === seedSearch.trim()) && <small>보고된 기원종으로 연결: {herbs.find((herb) => herb.name_ko === seedSearch.trim())?.taxa.join(" · ")}</small>}</label></div></div>
          <div><div className="ds-budget-fields"><label className="ds-field">사용할 천연물 시드 한도<input type="number" min={1} max={9000} step={1} required value={seedLimit} onChange={(event) => setSeedLimit(event.target.value)} data-testid="discovery-seed-limit" /></label><label className="ds-field">이번 실행의 최대 시도 횟수<input type="number" min={1} max={1000000000} step={1} required value={maxAttempts} onChange={(event) => setMaxAttempts(event.target.value)} data-testid="discovery-max-attempts" /></label></div><label className="ds-field">기존 약물 비교군<select value={includeImportedReferences ? "imported" : "manual"} onChange={(event) => setIncludeImportedReferences(event.target.value === "imported")} data-testid="discovery-reference-mode"><option value="imported">수집된 ChEMBL + 내장 비교군</option><option value="manual">내장 비교군 직접 선택</option></select></label><label className="ds-field">사용할 기존 약물 한도<input type="number" min={1} max={500} step={1} required value={referenceLimit} onChange={(event) => setReferenceLimit(event.target.value)} data-testid="discovery-reference-limit" /><small>수집된 ChEMBL 자료는 승인 이력 비교군입니다. 현재 판매 허가를 단정하지 않습니다.</small></label>{!includeImportedReferences && <fieldset className="ds-references"><legend>직접 선택할 비교군</legend>{references.length ? references.map((reference) => <label key={reference.id}><input type="checkbox" checked={referenceIds.includes(reference.id)} onChange={(event) => setReferenceIds((previous) => event.target.checked ? [...previous, reference.id] : previous.filter((id) => id !== reference.id))} /><span>{reference.name_ko || reference.name}</span></label>) : <p>내장 약물 비교군이 아직 로드되지 않았습니다.</p>}</fieldset>}<div className="ds-budget-explanation"><ShieldCheck size={18} /><p>목표 <strong>{number(Number(targetCount))}개</strong> · 시도 한도 <strong>{number(Number(maxAttempts))}회</strong><br />시도 한도에 도달하면 목표를 채우기 전에도 실행이 종료될 수 있습니다.</p></div></div>
        </div><details className="ds-execution-limits"><summary>추가 실행 한도 · 최대 {number(Number(maxRuntime))}초 / 후보 데이터 {number(Number(maxStorage))}MB</summary><div className="ds-budget-fields"><label className="ds-field">최대 실행 시간 (초)<input type="number" min={1} max={604800} step={1} required value={maxRuntime} onChange={(event) => setMaxRuntime(event.target.value)} data-testid="discovery-max-runtime" /></label><label className="ds-field">후보 데이터 저장 한도 (MB)<input type="number" min={1} max={1000000} step={1} required value={maxStorage} onChange={(event) => setMaxStorage(event.target.value)} data-testid="discovery-max-storage" /><small>SQLite 인덱스와 작업 파일의 실제 디스크 사용량은 별도입니다.</small></label></div></details><div className="ds-create-footer"><p>계산 범위: 구조 생성·중복 제거·화학 필터. LLM·AlphaFold·QPU 계산은 선택한 후보의 개별 분석에서 진행합니다.</p><button type="submit" className="ds-button ds-primary" disabled={!!busy || (!includeImportedReferences && !referenceIds.length)} data-testid="discovery-start-campaign">{busy === "create-campaign" ? <LoaderCircle size={17} className="ds-spin" /> : <Play size={17} />}생성 캠페인 시작</button></div>
      </motion.form>}</AnimatePresence>

      {campaigns.length > 0 && <div className="ds-campaign-layout"><aside className="ds-card ds-campaign-history"><div className="ds-section-heading"><h3>생성 캠페인</h3><span className="ds-small-count">{number(campaigns.length)}건</span></div><div>{campaigns.map((item) => <button key={item.id} className={`ds-campaign-item ${selectedCampaign === item.id ? "is-active" : ""}`} aria-pressed={selectedCampaign === item.id} onClick={() => selectCampaign(item.id)} data-testid={`discovery-campaign-${item.id}`}><div><Status value={item.status} /><small>{datetime(item.created_at || item.created)}</small></div><strong>{item.name || `캠페인 ${item.id.slice(0, 8)}`}</strong><p>실제 보존 <b>{number(count(item, "retained"))}</b>개</p><span>요청 목표 {number(count(item, "requested"))}개</span></button>)}</div></aside>
        <div className="ds-campaign-main">{campaignError && <div className="ds-error" role="alert">{campaignError}</div>}{campaign ? <>
          <section className="ds-card ds-campaign-detail" data-testid="discovery-campaign-detail"><div className="ds-section-heading"><div><span className="ds-eyebrow">CAMPAIGN / {campaign.id.slice(0, 12)}</span><h3>{campaign.name || "후보 생성 캠페인"}</h3></div><Status value={campaign.status} /></div>
            <div className="ds-campaign-counts">{COUNTERS.map((item) => <div key={item.key} className={item.key === "retained" ? "ds-counter-retained" : ""} title={item.hint}><span>{item.label}</span><strong data-testid={`discovery-count-${item.key}`}>{number(count(campaign, item.key))}</strong></div>)}</div>
            <CampaignProgress campaign={campaign} />
            {(campaign.reason || campaign.error) && <div className="ds-run-reason"><FileCheck2 size={17} /><p>{campaign.reason ? reasonLabel(campaign.reason) : campaign.error}</p></div>}
            <div className="ds-campaign-actions"><span>최종 기록 {datetime(campaign.updated_at || campaign.updated || campaign.created_at || campaign.created)}</span><div>{ACTIVE.has(campaign.status) && !["pause_requested", "pausing", "cancel_requested"].includes(campaign.status) && <button className="ds-button ds-secondary" disabled={!!busy} onClick={() => void campaignAction("pause")} data-testid="discovery-pause-campaign"><Pause size={16} />일시정지</button>}{["paused", "interrupted"].includes(campaign.status) && <button className="ds-button ds-primary" disabled={!!busy} onClick={() => void campaignAction("resume")} data-testid="discovery-resume-campaign"><Play size={16} />재개</button>}{(ACTIVE.has(campaign.status) || campaign.status === "paused") && <button className="ds-button ds-danger" disabled={!!busy} onClick={() => void campaignAction("cancel")} data-testid="discovery-cancel-campaign"><Square size={14} />취소</button>}<button className="ds-button ds-secondary" onClick={() => download(campaign, `campaign-${campaign.id}.json`)}><ArrowDownToLine size={16} />실행 기록</button></div></div>
          </section>
          <section className="ds-card ds-candidates"><div className="ds-section-heading"><div><span className="ds-eyebrow">RETAINED CANDIDATES</span><h3>보존된 후보 구조</h3></div><span className="ds-tag">계산 가설</span></div><div className="ds-scope-note"><FlaskConical size={18} /><p>생성된 구조는 연구 후보입니다. 신규성·합성 가능성·표적 결합·약효·안전성 검증을 거쳐야 합니다.</p></div>{candidateError ? <div className="ds-error" role="alert">{candidateError}</div> : candidateLoading && !candidatePage.items.length ? <Empty icon={<LoaderCircle size={28} className="ds-spin" />} title="보존된 후보를 불러오는 중" description="저장된 구조와 생성 계보를 확인합니다." /> : candidatePage.items.length ? <><div className="ds-table-scroll" aria-busy={candidateLoading}><table className="ds-table ds-candidate-table"><thead><tr><th scope="col">후보와 생성 계보</th><th scope="col">분자량 / QED</th><th scope="col"><span className="ds-sr-only">구조 탐색</span></th></tr></thead><tbody>{candidatePage.items.map((row) => <tr key={row.id}><td><strong>{candidateName(row)}</strong>{typeof row.descriptors?.formula === "string" && <span className="ds-formula">{row.descriptors.formula}</span>}<code className="ds-smiles" title={row.canonical_smiles || row.smiles}>{row.canonical_smiles || row.smiles}</code>{candidateLineage(row) != null && <details className="ds-lineage"><summary><GitBranch size={13} />생성 계보 보기</summary><pre>{JSON.stringify(candidateLineage(row), null, 2)}</pre></details>}</td><td><span className="ds-descriptor"><small>MW</small>{formatNumber(row.descriptors?.molecular_weight, 1)}</span><span className="ds-descriptor"><small>QED</small>{formatNumber(row.descriptors?.qed, 3)}</span></td><td><button className="ds-open-button" aria-label={`${candidateName(row)} 스튜디오에서 보기`} onClick={() => onInspect(normalizedCompound(row, true))} data-testid="discovery-inspect-candidate"><span>3D 보기</span><ArrowRight size={17} /></button></td></tr>)}</tbody></table></div><div className="ds-page-export"><button className="ds-text-button" disabled={candidateLoading} onClick={() => download(candidatePage.items, `campaign-${campaign.id}-page-${Math.floor(candidatePage.offset / CANDIDATE_PAGE_SIZE) + 1}.json`)}><ArrowDownToLine size={14} />현재 페이지 JSON</button></div></> : <Empty icon={<FlaskConical size={30} />} title="아직 보존된 후보가 없습니다" description="요청 목표와 별개로, 실제 저장된 후보가 이곳에 표시됩니다." />}
            <Pagination total={candidatePage.total} offset={candidatePage.offset} count={candidatePage.items.length} size={CANDIDATE_PAGE_SIZE} loading={candidateLoading} onChange={setCandidateOffset} label="보존된 후보" />
          </section>
        </> : <section className="ds-card"><Empty icon={<Layers3 size={28} />} title="캠페인을 선택하세요" description="실제 실행 수치와 보존된 후보를 확인할 수 있습니다." /></section>}</div>
      </div>}
    </div>}
  </div>;
}

function Metric({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: string; detail: string }) {
  return <div className="ds-metric"><div>{icon}<span>{label}</span></div><strong>{value}</strong><small>{detail}</small></div>;
}
function Status({ value }: { value: string }) {
  return <span className={`ds-status ${ACTIVE.has(value) ? "is-running" : value === "failed" || value === "blocked" ? "is-failed" : value === "completed" ? "is-completed" : ""}`}>{ACTIVE.has(value) ? <LoaderCircle size={12} className="ds-spin" /> : value === "completed" ? <Check size={12} /> : <span />}{STATUSES[value] || value}</span>;
}
function Empty({ icon, title, description }: { icon: React.ReactNode; title: string; description: string }) {
  return <div className="ds-empty">{icon}<h4>{title}</h4><p>{description}</p></div>;
}
function ProvenanceDetails({ compoundId, provenance, sources }: { compoundId: string | number; provenance: Provenance[]; sources: Source[] }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<{ provenance: Provenance[]; provenance_count: number; provenance_truncated: boolean } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open || detail) return;
    const controller = new AbortController();
    void api<{ provenance: Provenance[]; provenance_count: number; provenance_truncated: boolean }>(`/discovery/compounds/${encodeURIComponent(compoundId)}`, undefined, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setDetail(value); })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [compoundId, open, detail]);
  const records = detail?.provenance ?? provenance;
  const names = [...new Set(provenance.map((source) => sourceName(source, sources)))];
  return <details className="ds-provenance" onToggle={(event) => setOpen(event.currentTarget.open)}><summary>{names.slice(0, 2).join(" · ") || "출처 확인"}{names.length > 2 ? ` 외 ${names.length - 2}곳` : ""}<span>{detail ? `전체 ${number(detail.provenance_count)}건` : `요약 ${number(provenance.length)}건`}</span></summary>{error && <p className="ds-inline-error">{error}</p>}{open && !detail && !error && <p className="ds-muted">원본 연결 정보를 불러오는 중입니다.</p>}{detail?.provenance_truncated && <p className="ds-muted">전체 {number(detail.provenance_count)}건 중 앞 {number(records.length)}건을 표시합니다.</p>}<ul>{records.map((source, index) => {
    const url = externalUrl(source.source_url || source.url);
    return <li key={index}><strong>{sourceName(source, sources)}</strong>{source.external_id && <span>원본 ID {source.external_id}</span>}{(source.organisms || source.organism || source.species || source.taxon) && <span>기록된 생물: {source.organisms || source.organism || source.species || source.taxon}</span>}{(source.license_label || source.license) && <span>라이선스: {source.license_label || source.license}</span>}{url && <a href={url} target="_blank" rel="noopener noreferrer">원문 확인<ExternalLink size={12} /></a>}{source.raw != null && <details><summary>원본 메타데이터</summary><pre>{JSON.stringify(source.raw, null, 2)}</pre></details>}</li>;
  })}</ul></details>;
}
function Pagination({ total, offset, count: pageCount, size, loading, onChange, label }: { total: number; offset: number; count: number; size: number; loading: boolean; onChange: (offset: number) => void; label: string }) {
  return <nav className="ds-pagination" aria-label={`${label} 페이지`}><span>{total ? `${number(offset + 1)}–${number(Math.min(offset + pageCount, total))} / ${number(total)}` : "0개"}</span><div><button aria-label={`${label} 이전 페이지`} disabled={loading || offset === 0} onClick={() => onChange(Math.max(0, offset - size))}><ChevronLeft size={18} /></button><span>{Math.floor(offset / size) + 1} / {Math.max(1, Math.ceil(total / size))}</span><button aria-label={`${label} 다음 페이지`} disabled={loading || offset + pageCount >= total || pageCount === 0} onClick={() => onChange(offset + size)}><ChevronRight size={18} /></button></div></nav>;
}
function CampaignProgress({ campaign }: { campaign: Campaign }) {
  const requested = count(campaign, "requested") ?? 0;
  const retained = count(campaign, "retained") ?? 0;
  const percentage = requested ? Math.min(100, retained / requested * 100) : 0;
  const maxAttempts = campaign.max_attempts ?? (typeof campaign.request?.max_attempts === "number" ? campaign.request.max_attempts : typeof campaign.config?.max_attempts === "number" ? campaign.config.max_attempts : undefined);
  return <div className="ds-progress"><div><span>요청 목표 대비 실제 보존</span><strong>{percentage > 0 && percentage < 0.01 ? "0.01% 미만" : `${percentage.toFixed(2)}%`}</strong></div><div className="ds-progress-track" role="progressbar" aria-label="요청 목표 대비 실제 보존 수" aria-valuemin={0} aria-valuemax={requested || 1} aria-valuenow={Math.min(retained, requested || 1)}><span style={{ width: `${percentage}%` }} /></div><p>실제 {number(retained)}개 / 요청 {number(requested)}개{maxAttempts != null ? ` · 설정된 시도 한도 ${number(maxAttempts)}회` : ""}</p>{campaign.bounded_product_ceiling != null && <p>현재 입력 조합의 생성 상한: {number(campaign.bounded_product_ceiling)}개. 중복 제거·필터링 전의 계산상 한도이며 보존 수를 보장하지 않습니다.</p>}</div>;
}
