import { tr, msg, localeCode, getLanguage } from "../lib/i18n";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Beaker, Check, ChevronLeft, ChevronRight, Database, Leaf, LoaderCircle, Plus, RefreshCw, ScanLine, Search, X } from "lucide-react";
import { api } from "../lib/api";
import { normalizeDiscoveryCompound, type DiscoveryCompound, type DiscoveryKind } from "../lib/discoveryCompounds";
import type { Compound } from "../types/app";
import "./studio-compound-library.css";

type Props = {
  purpose?: "structure" | "comparison";
  compounds: Compound[];
  selectedIds: string[];
  activeId?: string;
  refreshKey: number;
  onInspect: (compound: Compound) => void;
  onToggle: (compound: Compound) => void;
  onAdd: () => void;
  onCompose: () => void;
  onCalculate?: () => void;
};
type Source = { id: string; name: string; kind?: string };
type Summary = { catalog: { compound_count: number }; sources: Source[] };
type Page = {
  items: DiscoveryCompound[];
  total: number;
  limit: number;
  offset: number;
  query_resolution?: { mapped: boolean; query: string; scope?: string };
};
const PAGE_SIZE = 30;
const INPUT_LIMIT = 8;
const CATEGORIES: Array<{ kind: DiscoveryKind | ""; label: string }> = [
  { kind: "", label: "전체" }, { kind: "natural_product", label: "천연물" }, { kind: "drug", label: "기존 약물" },
];
const count = (value: number) => value.toLocaleString(localeCode());
const compoundName = (compound: Compound) => getLanguage() === "en" ? compound.name || compound.name_ko || "" : compound.name_ko || compound.name;

function categoryLabel(compound: Compound): string {
  if (compound.source_kinds?.includes("drug") && compound.source_kinds?.includes("natural_product")) return "천연물 · 약물 출처";
  if (compound.category === "herbal" || compound.category === "natural_product") return "천연물";
  if (compound.category === "drug") return "기존 약물";
  return compound.generated ? "생성 후보" : "분류 미확인";
}

export default function StudioCompoundLibrary({ purpose = "structure", compounds, selectedIds, activeId, refreshKey, onInspect, onToggle, onAdd, onCompose, onCalculate }: Props) {
  const isComparison = purpose === "comparison";
  const [view, setView] = useState<"database" | "personal">("database");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<DiscoveryKind | "">("");
  const [source, setSource] = useState("");
  const [offset, setOffset] = useState(0);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [summaryError, setSummaryError] = useState(false);
  const [page, setPage] = useState<Page | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [settledRequest, setSettledRequest] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const requestKey = JSON.stringify([query.trim(), kind, source, offset, refreshKey, revision]);

  useEffect(() => {
    const controller = new AbortController();
    setSummaryError(false);
    void api<Summary>("/discovery/summary", undefined, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) setSummary(result);
    }).catch(() => {
      if (!controller.signal.aborted) setSummaryError(true);
    });
    return () => controller.abort();
  }, [refreshKey, revision]);

  useEffect(() => {
    if (view !== "database") return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
      if (query.trim()) params.set("search", query.trim());
      if (kind) params.set("kind", kind);
      if (source) params.set("source", source);
      void api<Page>(`/discovery/compounds?${params}`, undefined, { signal: controller.signal }).then((result) => {
        if (controller.signal.aborted) return;
        if (result.total > 0 && offset >= result.total) {
          setOffset(Math.floor((result.total - 1) / PAGE_SIZE) * PAGE_SIZE);
          return;
        }
        setPage(result);
        setSettledRequest(requestKey);
        setLoading(false);
        listRef.current?.scrollTo({ top: 0 });
      }).catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        setError(failure instanceof Error ? failure.message : "데이터베이스에 연결하지 못했습니다.");
        setSettledRequest(requestKey);
        setLoading(false);
      });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [view, query, kind, source, offset, refreshKey, revision, requestKey]);

  const uniqueCompounds = useMemo(() => [...new Map(compounds.map((compound) => [compound.id, compound])).values()], [compounds]);
  const selectedCompounds = useMemo(() => {
    const byId = new Map(uniqueCompounds.map((compound) => [compound.id, compound]));
    return selectedIds.flatMap((id) => { const compound = byId.get(id); return compound ? [compound] : []; });
  }, [uniqueCompounds, selectedIds]);
  const personalResults = useMemo(() => {
    const search = query.trim().toLocaleLowerCase();
    return uniqueCompounds.filter((compound) => {
      const matchesKind = !kind || compound.category === kind || (kind === "natural_product" && compound.category === "herbal") || compound.source_kinds?.includes(kind);
      return matchesKind && (!search || [compound.name, compound.name_ko, compound.smiles].some((value) => value?.toLocaleLowerCase().includes(search)));
    });
  }, [uniqueCompounds, query, kind]);
  const rows = view === "database" ? (page?.items ?? []).map((row) => normalizeDiscoveryCompound(row, { kind, source })) : personalResults.slice(offset, offset + PAGE_SIZE);
  const total = view === "database" ? page?.total ?? 0 : personalResults.length;
  const isLoading = view === "database" && (loading || settledRequest !== requestKey);
  const visibleError = view === "database" ? error : "";
  const atLimit = selectedIds.length >= INPUT_LIMIT;
  const matchingSources = (summary?.sources ?? []).filter((entry) => !kind || entry.kind === kind);
  const retry = () => setRevision((value) => value + 1);

  return <>
    <div className="section-heading studio-compound-heading">
      <div><span className="eyebrow">MOLECULE LIBRARY</span><h3>{tr(isComparison ? "비교할 성분" : "탐색할 성분")}</h3></div>
      <button className="icon-button" aria-label={tr("분자 추가")} title={tr("성분 직접 추가")} onClick={onAdd}><Plus size={18} /></button>
    </div>
    <div className="studio-compound-total" data-testid="studio-database-total">
      <Database size={12} />
      <span>{tr(summary ? msg("DB 총 {0}개", count(summary.catalog.compound_count)) : summaryError ? "DB 합계 조회 실패" : "DB 합계 조회 중")}</span>
      <button aria-label={tr("성분 데이터베이스 새로 고침")} title={tr("데이터베이스 새로 고침")} onClick={retry}><RefreshCw size={12} /></button>
    </div>
    {tr(summaryError && <p className="studio-compound-summary-error" role="status">{tr("DB 합계를 확인하지 못했습니다. 새로 고침으로 다시 조회하세요.")}</p>)}
    <div className="studio-compound-views" aria-label={tr("성분 목록 선택")}>
      <button aria-pressed={view === "database"} className={view === "database" ? "active" : ""} onClick={() => { setView("database"); setQuery(""); setOffset(0); }}>{tr("대규모 DB")}</button>
      <button aria-pressed={view === "personal"} className={view === "personal" ? "active" : ""} onClick={() => { setView("personal"); setQuery(""); setOffset(0); }}>{tr("내 목록 ")}{tr(count(uniqueCompounds.length))}</button>
    </div>
    <div className="search-field studio-compound-search"><Search size={14} /><input data-testid="studio-compound-query" value={query} placeholder={tr(view === "database" ? "성분·약재·생물종 검색" : "선택·추가·기본 성분 검색")} aria-label={tr("라이브러리 성분 검색")} onChange={(event) => { setQuery(event.target.value); setOffset(0); }} /></div>
    <div className="segmented-control studio-compound-categories" aria-label={tr("성분 분류")}>
      {tr(CATEGORIES.map((entry) => <button key={entry.kind} className={kind === entry.kind ? "active" : ""} aria-pressed={kind === entry.kind} onClick={() => { setKind(entry.kind); setSource(""); setOffset(0); }}>{tr(entry.label)}</button>))}
    </div>
    {tr(view === "database" && <select className="studio-compound-source" aria-label={tr("성분 데이터베이스 출처")} value={source} onChange={(event) => { setSource(event.target.value); setOffset(0); }}>
      <option value="">{tr("모든 출처")}</option>{tr(matchingSources.map((entry) => <option value={entry.id} key={entry.id}>{tr(entry.name)}</option>))}
    </select>)}
    {tr(view === "personal" && <p className="studio-compound-personal-note">{tr("선택·직접 추가·기본 제공 성분")}</p>)}
    {tr(isComparison && selectedCompounds.length > 0 && <div className="studio-compound-selected" aria-label={tr("선택한 비교 입력")}>
      <div className="studio-compound-selection-label">{tr("비교 입력 ")}{tr(selectedIds.length)}/{tr(INPUT_LIMIT)}{tr(atLimit ? " · 선택 해제 후 추가" : "")}</div>
      <div className="studio-compound-chips">{tr(selectedCompounds.map((compound) => <span className={activeId === compound.id ? "active" : ""} key={compound.id}>
        <button onClick={() => onInspect(compound)} title={compoundName(compound)} aria-label={msg("{0} 구조 보기", compoundName(compound))}>{compoundName(compound)}</button>
        <button onClick={() => onToggle(compound)} aria-label={msg("{0} 비교 입력 해제", compoundName(compound))} title={tr("비교 입력에서 해제")}><X size={11} /></button>
      </span>))}</div>
    </div>)}
    <div className="studio-compound-results-label" data-testid="studio-compound-count" role="status" aria-live="polite">
      {tr(isLoading ? "성분 조회 중…" : visibleError ? "성분 조회 실패" : msg("{0} {1}개", tr(view === "database" ? "DB 검색 결과" : "내 목록 결과"), count(total)))}
    </div>
    {tr(!isLoading && !visibleError && view === "database" && page?.query_resolution?.mapped && <p className="studio-compound-resolution" title={tr(page.query_resolution.scope)}>{tr("검색어 연결: ")}{page.query_resolution.query}</p>)}
    <div className={`compound-list studio-compound-list studio-compound-list-${purpose}`} ref={listRef} aria-busy={isLoading} data-testid="studio-compound-list">
      {tr(isLoading ? <div className="studio-compound-message" data-testid="studio-compound-loading" role="status"><LoaderCircle className="studio-compound-spinner" size={20} /><span>{tr("데이터베이스에서 불러오는 중")}</span></div>
        : visibleError ? <div className="studio-compound-message" role="alert"><p>{tr("성분 목록을 불러오지 못했습니다.")}</p><small>{tr(visibleError)}</small><button className="text-button" onClick={retry}><RefreshCw size={13} /> {tr(" 다시 시도")}</button></div>
        : rows.length === 0 ? <div className="studio-compound-message" role="status"><Search size={20} /><p>{tr(query || kind || source ? "조건에 맞는 성분이 없습니다." : view === "database" ? "데이터베이스에 저장된 성분이 없습니다." : "내 목록에 성분이 없습니다.")}</p><small>{tr(view === "database" ? "검색 조건을 바꾸거나 대규모 탐색에서 자료를 가져오세요." : "기본 성분과 직접 추가한 성분을 이곳에서 확인할 수 있습니다.")}</small></div>
        : rows.map((compound) => {
          const checked = selectedIds.includes(compound.id);
          const label = compoundName(compound);
          const fullLabel = typeof compound.display_name === "string" ? compound.display_name : label;
          const inspectReason = compound.smiles.length > 5000 ? "구조 보기는 SMILES 5,000자 이하 성분만 지원합니다." : "";
          const selectionReason = !isComparison ? "" : compound.smiles.length > 4096 ? "비교 입력은 SMILES 4,096자 이하만 지원합니다."
            : compound.category === "candidate" ? "분류 미확인 성분은 비교 입력으로 선택할 수 없습니다."
            : atLimit && !checked ? "비교 입력은 최대 8개입니다. 선택한 성분을 해제한 뒤 추가하세요." : "";
          const sourceNames = [...new Set((compound.sources ?? compound.provenance ?? []).map((record: { source_name?: string; source_id?: string; source?: string }) => record.source_name || summary?.sources.find((entry) => entry.id === (record.source_id || record.source))?.name || record.source_id || record.source).filter(Boolean))].join(" · ");
          const detail = `${tr(categoryLabel(compound))}${sourceNames ? ` · ${sourceNames}` : compound.name !== label ? ` · ${compound.name}` : ""}`;
          return <div className={`compound-row ${activeId === compound.id ? "active" : ""}`} key={compound.id} data-testid="studio-compound-row" data-compound-id={compound.id}>
            {tr(isComparison && <button className={`selection-check ${checked ? "checked" : ""}`} disabled={!checked && !!selectionReason} aria-label={msg("{0} 비교 입력 {1}{2}", tr(label), tr(checked ? "해제" : "선택"), !checked && selectionReason ? ` · ${tr(selectionReason)}` : "")} aria-pressed={checked} title={!checked && selectionReason ? tr(selectionReason) : msg("{0} 비교 입력 {1}", label, tr(checked ? "해제" : "선택"))} onClick={() => onToggle(compound)}>{tr(checked && <Check size={12} />)}</button>)}
            <button className="compound-main" disabled={!!inspectReason} onClick={() => onInspect(compound)} title={`${fullLabel}\n${inspectReason ? tr(inspectReason) : detail}${selectionReason ? `\n${tr(selectionReason)}` : ""}`} aria-label={msg("{0} 구조 보기{1}", label, inspectReason ? ` · ${tr(inspectReason)}` : "")} aria-pressed={isComparison ? undefined : activeId === compound.id}>
              <span className={`compound-glyph ${compound.category}`}>{tr(compound.category === "herbal" || compound.category === "natural_product" ? <Leaf size={17} /> : <Beaker size={17} />)}</span>
              <span><strong>{label}</strong><small>{inspectReason ? tr(inspectReason) : isComparison && (compound.category === "candidate" || compound.smiles.length > 4096) ? tr(selectionReason) : detail}</small></span>
            </button>
          </div>;
        }))}
    </div>
    <nav className="studio-compound-pagination" data-testid="studio-compound-pagination" aria-label={tr("성분 목록 페이지")}>
      <button aria-label={tr("이전 성분 페이지")} disabled={isLoading || !!visibleError || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}><ChevronLeft size={15} /></button>
      <span>{tr(isLoading || visibleError ? "—" : total ? `${count(offset + 1)}–${count(Math.min(offset + rows.length, total))} / ${count(total)}` : "0개")}</span>
      <button aria-label={tr("다음 성분 페이지")} disabled={isLoading || !!visibleError || offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}><ChevronRight size={15} /></button>
    </nav>
    {tr(isComparison ? <div className="library-footer studio-compound-footer"><span><strong>{tr(selectedIds.length)}</strong>{tr("개의 비교 입력")}</span><button className="text-button" disabled={selectedIds.length < 2} onClick={onCompose}>{tr("비교 분석 설정 ")}<ArrowRight size={14} /></button></div>
      : <div className="library-footer studio-compound-footer studio-compound-structure-footer"><span>{tr("성분을 누르면 분자 구조와 특성을 확인합니다.")}</span>{tr(onCalculate && <button className="text-button" disabled={!activeId} onClick={onCalculate}><ScanLine size={14} /> {tr(" 이 성분의 AF3 계산 ")}<ArrowRight size={14} /></button>)}</div>)}
  </>;
}
