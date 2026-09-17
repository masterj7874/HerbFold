import { tr, msg } from "../lib/i18n";
import { Atom, CircleHelp, FileCheck2, Layers3, RefreshCw } from "lucide-react";
import type { ExperimentalLigandReference, StructureMatch, StructureMode } from "../types/molecular";
import "./structure-selection.css";

export const STRUCTURE_MODES: Array<{ id: StructureMode; label: string; icon: typeof Atom }> = [
  { id: "rdkit_conformer", label: "자유 분자 구조", icon: Atom },
  { id: "experimental_pdb", label: "실험 복합체", icon: Layers3 },
  { id: "alphafold3_prediction", label: "AF3 계산·예측", icon: FileCheck2 },
];

export function StructureModeControls({ mode, name, target, archive, references, selectedId, onChange, onReference }: {
  mode: StructureMode; name?: string; target: string; archive: boolean; onChange: (mode: StructureMode) => void;
  references: ExperimentalLigandReference[]; selectedId?: string; onReference: (reference: ExperimentalLigandReference) => void;
}) {
  return <div className="library-reference ss-modes" aria-label={tr("선택 성분의 구조 보기 모드")}>
    <span className="eyebrow">{tr("선택 성분의 구조 보기")}</span>
    <div className="ss-mode-grid">{tr(STRUCTURE_MODES.map(({ id, label, icon: Icon }) => <button key={id} disabled={!name} aria-pressed={!archive && mode === id}
      aria-label={`${name || tr("성분 선택 대기")} · ${id === "experimental_pdb" ? `${target === "P35354" ? "COX-2" : target} ` : ""}${tr(label)}`}
      title={name ? `${name} · ${tr(label)}` : tr("먼저 성분을 선택하세요")}
      onClick={() => onChange(id)} data-testid={`studio-mode-${id === "rdkit_conformer" ? "conformer" : id === "experimental_pdb" ? "experimental" : "af3"}`}>
      <Icon size={16} /><span>{tr(id === "rdkit_conformer" ? "자유 분자" : id === "experimental_pdb" ? "실험 구조" : "AF3 예측")}</span>
    </button>))}</div>
    {tr(references.length > 0 && <label className="ss-reference-selector"><span>{tr("실험 구조 등록 물질")}</span><select aria-label={tr("실험 구조 등록 성분으로 선택 변경")} data-testid="studio-reference-select" value={references.some((reference) => reference.id === selectedId) ? selectedId : ""} onChange={(event) => { const reference = references.find((item) => item.id === event.target.value); if (reference) onReference(reference); }}><option value="">{tr("성분을 직접 바꿔서 보기")}</option>{tr(references.map((reference) => <option key={reference.id} value={reference.id}>{tr(reference.label)}</option>))}</select><small>{tr("선택하면 현재 성분과 보기가 바뀝니다. 실험 비교 성분이며 현재 허가 상태를 뜻하지 않습니다.")}</small></label>)}
  </div>;
}

export function StructureContext({ mode, name, target, archive, loading, matches, onChange }: {
  mode: StructureMode; name?: string; target: string; archive?: { jobId: string; file: string } | null;
  loading: boolean; matches: StructureMatch[]; onChange: (mode: StructureMode) => void;
}) {
  return <div className="ss-context" data-testid="selected-structure-context" data-structure-mode={archive ? "archive" : mode} aria-busy={loading}>
    <div><span className="ss-eyebrow">{tr(archive ? "연구 기록 · 파일 직접 열기" : "현재 선택 성분")}</span>
      <strong>{tr(archive ? archive.file || archive.jobId : name || "성분 선택 대기")}</strong>
      <small>{tr(archive ? msg("작업 {0} · 현재 성분에 대한 일치 조회와 별도입니다.", archive.jobId) : `${mode === "rdkit_conformer" ? tr("리간드 단독 좌표") : msg("표적 {0}{1}", target === "P35354" ? "COX-2 · " : "", target)} · ${tr(loading ? "구조 조회 중" : STRUCTURE_MODES.find((item) => item.id === mode)?.label)}`)}</small>
      {tr(!archive && matches.length > 0 && <span className="ss-match-source" data-testid="structure-match-source">{matches.map((match) => tr(match.label)).join(" · ")}</span>)}
    </div>
    <label><span className="ss-sr-only">{tr("분자 구조 보기 모드")}</span><select value={archive ? "archive" : mode} disabled={!name} onChange={(event) => onChange(event.target.value as StructureMode)} data-testid="structure-mode-select">
      {tr(archive && <option value="archive">{tr("연구 기록 파일")}</option>)}{tr(STRUCTURE_MODES.map((item) => <option key={item.id} value={item.id}>{tr(item.label)}</option>))}
    </select></label>
  </div>;
}

export function StructureUnavailable({ name, mode, error, reason, onFreeMolecule, onCalculateAF3, onRetry }: {
  name?: string; mode: StructureMode; error: boolean; reason: string;
  onFreeMolecule: () => void; onCalculateAF3: () => void; onRetry: () => void;
}) {
  return <div className="ss-unavailable" role="status" data-testid="studio-structure-unavailable" data-result-status={error ? "error" : "unavailable"}>
    <CircleHelp size={35} /><span className="ss-result-label">{tr(error ? "조회 오류" : "일치하는 저장 구조 없음")}</span>
    <h3>{tr(name ? msg("{0}의 {1}", tr(name), tr(mode === "experimental_pdb" ? "실험 복합체" : mode === "alphafold3_prediction" ? "AF3 예측 구조" : "분자 구조")) : "연구 기록 구조")}</h3>
    <p>{tr(reason)}</p>
    {tr(!error && <p className="ss-scope">{tr("선택한 성분과 표적이 일치하는 구조가 확인될 때만 복합체를 표시합니다.")}</p>)}
    <div className="ss-result-actions">
      {tr(name && mode !== "rdkit_conformer" && <button onClick={onFreeMolecule} data-testid="structure-fallback-conformer"><Atom size={16} />{tr("이 성분의 자유 분자 구조")}</button>)}
      {tr(name && <button onClick={onCalculateAF3} data-testid="studio-af3-calculate"><FileCheck2 size={16} />{tr("이 성분 AF3 계산")}</button>)}
      {tr(error && <button onClick={onRetry} data-testid="structure-retry"><RefreshCw size={16} />{tr("다시 조회")}</button>)}
    </div>
    {tr(name && <small className="ss-preparation-scope">{tr("계산 패널에서 선택 성분·표적의 입력과 실행 조건을 확인한 뒤 실제 AF3 계산을 시작할 수 있습니다.")}</small>)}
  </div>;
}

