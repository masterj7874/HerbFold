import { tr, msg } from "../lib/i18n";
import type { ReactNode } from "react";
import { ArrowRight, GitCompareArrows, LoaderCircle, Microscope, X } from "lucide-react";
import type { Compound } from "../types/app";
import "./comparison-workspace.css";

type ComparisonResult = {
  left_id?: string;
  right_id?: string;
  left_name?: string;
  right_name?: string;
  herbal_id?: string;
  drug_id?: string;
  herbal_name?: string;
  drug_name?: string;
  tanimoto?: number;
  identical_structure?: boolean;
  descriptor_delta_left_minus_right?: Record<string, unknown>;
  descriptor_delta_herbal_minus_drug?: Record<string, unknown>;
};

type Props = {
  library: ReactNode;
  selected: Compound[];
  comparison: ComparisonResult[];
  comparisonCurrent?: boolean;
  busy: boolean;
  onCompare: () => void;
  onCompose: () => void;
  onInspect: (compound: Compound) => void;
  onRemove?: (compound: Compound) => void;
  children?: ReactNode;
};

const compoundName = (compound: Compound) => compound.name_ko || compound.name;
const numericValue = (value: unknown, digits: number, signed = false) => {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const rounded = Number(value.toFixed(digits));
  return `${signed && rounded > 0 ? "+" : ""}${rounded.toFixed(digits)}`;
};
const categoryLabel = (compound: Compound) => {
  if (compound.source_kinds?.includes("drug") && compound.source_kinds?.includes("natural_product")) return "천연물 · 약물 출처";
  if (compound.category === "herbal" || compound.category === "natural_product") return "천연물";
  if (compound.category === "drug") return "기존 약물";
  return compound.generated ? "계산 후보" : "성분";
};

export default function ComparisonWorkspace({
  library, selected, comparison, comparisonCurrent = true, busy,
  onCompare, onCompose, onInspect, onRemove, children,
}: Props) {
  const hasResults = comparison.length > 0;
  const showResults = hasResults && comparisonCurrent && !busy;
  const hasDescriptorDeltas = comparison.some((row) => !!(row.descriptor_delta_left_minus_right || row.descriptor_delta_herbal_minus_drug));
  const nameFor = (id?: string, name?: string) => {
    const compound = selected.find((entry) => entry.id === id);
    return compound ? compoundName(compound) : name || id || "이름 미확인";
  };

  return <div className="comparison-workspace" data-testid="comparison-workspace">
    <div className="cw-intro">
      <div>
        <span className="eyebrow">MOLECULE COMPARISON</span>
        <h2>{tr("필요한 성분끼리 구조를 비교하세요")}</h2>
        <p>{tr("성분을 2개 이상 선택하면 각 쌍의 분자 지문 유사도를 확인할 수 있습니다.")}</p>
      </div>
      <span className="cw-intro-icon" aria-hidden="true"><GitCompareArrows size={27} /></span>
    </div>
    <div className="cw-layout">
      {tr(library)}
      <div className="cw-main">
        <section className="card cw-inputs" aria-labelledby="cw-input-heading">
          <div className="section-heading">
            <div><span className="eyebrow">COMPARISON INPUTS</span><h3 id="cw-input-heading">{tr("비교할 성분 ")}<span className="count-badge">{tr(selected.length)}</span></h3></div>
            <button className="primary-button" data-testid="compare-structures" disabled={busy || selected.length < 2} onClick={onCompare}>
              {tr(busy ? <LoaderCircle className="spin" size={16} /> : <GitCompareArrows size={16} />)}
              {tr(busy ? "비교 계산 중" : "구조 비교")}
            </button>
          </div>
          {tr(selected.length > 0 ? <div className="cw-selected-list">
            {tr(selected.map((compound, index) => <article key={compound.id} className="cw-selected-compound" data-testid="comparison-input">
              <span className="cw-input-number" aria-hidden="true">{tr(String(index + 1).padStart(2, "0"))}</span>
              <div className="cw-input-copy"><strong>{tr(compoundName(compound))}</strong><span>{tr(categoryLabel(compound))}</span></div>
              <button className="cw-inspect-button" onClick={() => onInspect(compound)} aria-label={msg("{0} 분자 분석 열기", tr(compoundName(compound)))} title={tr("분자 분석 열기")}><Microscope size={16} /><span>{tr("구조 보기")}</span></button>
              {tr(onRemove && <button className="icon-button cw-remove-button" onClick={() => onRemove(compound)} aria-label={msg("{0} 비교 선택 해제", tr(compoundName(compound)))} title={tr("비교 선택 해제")}><X size={15} /></button>)}
            </article>))}
          </div> : <p className="cw-input-empty">{tr("성분 목록에서 비교할 성분을 선택하세요.")}</p>)}
          <p className="cw-selection-note">{tr(selected.length < 2 ? msg("구조 비교를 위해 성분을 {0}개 더 선택하세요.", 2 - selected.length) : msg("선택한 {0}개 성분의 모든 쌍을 비교합니다.", selected.length))}</p>
        </section>
        <section className="card cw-results" aria-labelledby="cw-results-heading" data-testid="comparison-results" aria-busy={busy}>
          <div className="cw-results-heading"><span className="eyebrow">STRUCTURAL SIMILARITY</span><h3 id="cw-results-heading">{tr("구조 비교 결과")}{tr(showResults && <span className="count-badge">{tr(comparison.length)}{tr("쌍")}</span>)}</h3></div>
          {tr(busy ? <div className="cw-results-empty" role="status"><LoaderCircle className="spin" size={25} /><strong>{tr("분자 구조를 비교하고 있습니다")}</strong><p>{tr("선택한 성분의 분자 지문 유사도를 계산합니다.")}</p></div>
            : hasResults && !comparisonCurrent ? <div className="cw-results-empty" role="status"><GitCompareArrows size={25} /><strong>{tr("비교할 성분이 변경되었습니다")}</strong><p>{tr("구조 비교를 실행하면 현재 선택한 성분의 결과를 표시합니다.")}</p></div>
            : showResults ? <>
              <div className="cw-table-wrap" tabIndex={0} role="region" aria-label={tr("성분 쌍별 구조 유사도")}>
                <table className={`cw-results-table${hasDescriptorDeltas ? " cw-with-descriptors" : ""}`}>
                  <caption>{tr("분자 지문 유사도는 0–1 사이 값입니다.")}{tr(hasDescriptorDeltas && " 물성 차이는 첫 성분에서 두 번째 성분을 뺀 값입니다.")}</caption>
                  <thead><tr><th scope="col">{tr("비교 성분")}</th><th scope="col">Tanimoto</th>{tr(hasDescriptorDeltas && <><th scope="col">{tr("Δ 분자량")}<small>g/mol</small></th><th scope="col">Δ LogP</th><th scope="col">Δ TPSA<small>Å²</small></th></>)}</tr></thead>
                  <tbody>{tr(comparison.map((row, index) => {
                    const leftId = row.left_id ?? row.herbal_id;
                    const rightId = row.right_id ?? row.drug_id;
                    const delta = row.descriptor_delta_left_minus_right ?? row.descriptor_delta_herbal_minus_drug ?? {};
                    const similarity = typeof row.tanimoto === "number" && row.tanimoto >= 0 && row.tanimoto <= 1 ? row.tanimoto : undefined;
                    return <tr key={`${leftId ?? "left"}-${rightId ?? "right"}-${index}`} data-testid="comparison-result-row">
                      <th scope="row"><span>{tr(nameFor(leftId, row.left_name ?? row.herbal_name))}</span><span className="cw-pair-second"><span aria-hidden="true">↔</span> {tr(nameFor(rightId, row.right_name ?? row.drug_name))}</span></th>
                      <td className="cw-similarity">{tr(numericValue(similarity, 3))}{tr(row.identical_structure === true && <small>{tr("동일한 표준화 구조")}</small>)}</td>
                      {tr(hasDescriptorDeltas && <><td>{tr(numericValue(delta.molecular_weight, 2, true))}</td><td>{tr(numericValue(delta.logp, 2, true))}</td><td>{tr(numericValue(delta.tpsa, 2, true))}</td></>)}
                    </tr>;
                  }))}</tbody>
                </table>
              </div>
              <p className="cw-method-note">{tr("Morgan 분자 지문으로 계산한 구조 유사도입니다. 약효, 결합 친화도 또는 약물의 대체 가능성을 의미하지 않습니다.")}</p>
            </> : <div className="cw-results-empty" role="status"><GitCompareArrows size={27} /><strong>{tr("비교 결과가 여기에 표시됩니다")}</strong><p>{tr("성분을 선택하고 구조 비교를 실행하세요.")}</p></div>)}
        </section>
        <div className="cw-advanced-link"><span>{tr("선택한 성분으로 추가 분석이 필요할 때")}</span><button className="text-button" disabled={selected.length < 2} onClick={onCompose}>{tr("확장 분석 설정 ")}<ArrowRight size={14} /></button></div>
      </div>
    </div>
    {tr(children)}
  </div>;
}

