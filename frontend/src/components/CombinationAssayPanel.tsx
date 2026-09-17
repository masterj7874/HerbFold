import { tr } from "../lib/i18n";
import { useState, type FormEvent } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowDownToLine, BarChart3, FlaskConical, LoaderCircle } from "lucide-react";
import { api, download } from "../lib/api";
import "./combination-assay-panel.css";

type Point = { index: number; concentration_a: number; concentration_b: number; inhibition_a: number; inhibition_b: number; inhibition_combination: number; bliss_expected: number; hsa_expected: number; bliss_excess_percentage_points: number; hsa_excess_percentage_points: number };
type AssayReport = { points: Point[]; input: { component_a: string; component_b: string; concentration_unit: string; assay_context: string; source: string }; limitations: string[]; reference_url: string };
const HEADERS = "concentration_a,concentration_b,inhibition_a_pct,inhibition_b_pct,combination_pct";
const signed = (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(2)}`;

export default function CombinationAssayPanel() {
  const reduce = useReducedMotion();
  const [componentA, setComponentA] = useState("");
  const [componentB, setComponentB] = useState("");
  const [context, setContext] = useState("");
  const [source, setSource] = useState("");
  const [unit, setUnit] = useState("uM");
  const [csv, setCsv] = useState("");
  const [matched, setMatched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState<AssayReport | null>(null);

  async function calculate(event: FormEvent) {
    event.preventDefault();
    setError(""); setReport(null);
    try {
      const lines = csv.trim().split(/\r?\n/).filter(line => line.trim());
      if (lines[0]?.trim().toLowerCase() === HEADERS) lines.shift();
      if (!lines.length || lines.length > 96) throw new Error("1~96행의 측정값을 입력해 주세요.");
      const points = lines.map((line, index) => {
        const values = line.split(/[\t,]/).map(value => value.trim());
        if (values.length !== 5 || values.some(value => !value || !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(value)))
          throw new Error(`${index + 1}행에 농도 A, 농도 B, 단독 A 억제율, 단독 B 억제율, 병용 억제율을 숫자 5개로 입력해 주세요.`);
        const [a, b, ia, ib, combined] = values.map(Number);
        if (![a, b, ia, ib, combined].every(Number.isFinite) || a < 0 || b < 0 || [ia, ib, combined].some(value => value < 0 || value > 100))
          throw new Error(`${index + 1}행: 농도는 0 이상, 억제율은 0~100%여야 합니다.`);
        return { concentration_a: a, concentration_b: b, inhibition_a: ia / 100, inhibition_b: ib / 100, inhibition_combination: combined / 100 };
      });
      setBusy(true);
      setReport(await api<AssayReport>("/design-pipeline/combination-assay", { component_a: componentA, component_b: componentB, assay_context: context, source, concentration_unit: unit, matched_conditions: matched, points }));
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  return <details className="combination-assay" data-testid="combination-assay-panel">
    <summary><span><BarChart3 size={19} />{tr("실험값으로 병용 반응 비교")}</span><small>{tr("Bliss · HSA 기준 계산")}</small></summary>
    <div className="combination-assay-body">
      <p>{tr("같은 실험 조건에서 얻은 두 성분의 단독 억제율과 병용 억제율을 입력하세요. 이 도구는 입력값에 대한 기준 모델의 차이를 계산합니다.")}</p>
      <form onSubmit={calculate} onChange={() => { setReport(null); setError(""); }}>
        <fieldset disabled={busy}>
          <div className="ca-fields">
            <label>{tr("성분 A")}<input value={componentA} onChange={event => setComponentA(event.target.value)} required maxLength={200} placeholder={tr("측정한 성분명")} /></label>
            <label>{tr("성분 B")}<input value={componentB} onChange={event => setComponentB(event.target.value)} required maxLength={200} placeholder={tr("함께 측정한 성분명")} /></label>
            <label>{tr("농도 단위")}<select value={unit} onChange={event => setUnit(event.target.value)}><option value="uM">µM</option><option value="nM">nM</option><option value="ug/mL">µg/mL</option><option value="mg/mL">mg/mL</option></select></label>
          </div>
          <div className="ca-fields ca-context">
            <label>{tr("실험 조건")}<input value={context} onChange={event => setContext(event.target.value)} required maxLength={1000} placeholder={tr("세포계 · endpoint · 노출 시간 · 정규화 기준")} /></label>
            <label>{tr("원자료 출처")}<input value={source} onChange={event => setSource(event.target.value)} required maxLength={1000} placeholder={tr("논문 DOI, 실험 기록 또는 원자료 파일명")} /></label>
          </div>
          <label>{tr("측정값 붙여넣기 · CSV 또는 탭으로 구분")}<textarea rows={5} value={csv} onChange={event => setCsv(event.target.value)} required maxLength={50000} placeholder={tr(HEADERS)} /><small>{tr("열 순서: 농도 A, 농도 B, 단독 A 억제율(%), 단독 B 억제율(%), 병용 억제율(%). 0값도 유효합니다. 생존율은 100에서 빼서 억제율로 변환한 뒤 입력하세요.")}</small></label>
          <label className="ca-check"><input type="checkbox" checked={matched} onChange={event => setMatched(event.target.checked)} required />{tr("각 행의 단독·병용 측정은 같은 성분 농도와 실험 조건, 대조군 정규화를 사용했습니다.")}</label>
          <div className="ca-actions"><button type="submit" className="primary-button" disabled={busy}>{tr(busy ? <LoaderCircle size={16} className="spin" /> : <FlaskConical size={16} />)}{tr("기준 모델과 비교")}</button><button type="button" className="secondary-button" onClick={() => download(HEADERS + "\n", "herbfold-combination-assay-template.csv", "text/csv")}><ArrowDownToLine size={15} />{tr("CSV 양식")}</button></div>
        </fieldset>
      </form>
      {tr(error && <p role="alert" className="ca-error">{tr(error)}</p>)}
      {tr(report && <div className="ca-result" aria-live="polite">
        <div className="ca-result-heading"><h3>{tr(report.input.component_a)} + {tr(report.input.component_b)}</h3><button className="secondary-button" onClick={() => download(report, "herbfold-combination-assay-result.json")}><ArrowDownToLine size={15} />{tr("결과 JSON")}</button></div>
        <p>{tr(report.input.assay_context)} {tr(" · 출처: ")}{tr(report.input.source)}</p>
        <div className="ca-bars" aria-label={tr("첫 번째 측정점의 억제율 비교")}>
          {tr([{ label: "입력한 병용 억제율", value: report.points[0].inhibition_combination }, { label: "Bliss 기대 억제율", value: report.points[0].bliss_expected }, { label: "HSA 기대 억제율", value: report.points[0].hsa_expected }].map(({ label, value }) => <div key={label}><span>{tr(label)}</span><div className="ca-bar-track"><motion.i initial={{ width: reduce ? `${value * 100}%` : 0 }} animate={{ width: `${value * 100}%` }} transition={{ duration: reduce ? 0 : .6 }} /></div><b>{tr((value * 100).toFixed(2))}%</b></div>))}
        </div>
        <div className="ca-table-wrap"><table><thead><tr><th>{tr("농도 A (")}{tr(report.input.concentration_unit)})</th><th>{tr("농도 B")}</th><th>{tr("병용 억제율")}</th><th>{tr("Bliss 차이 (pp)")}</th><th>{tr("HSA 차이 (pp)")}</th></tr></thead><tbody>{tr(report.points.map(point => <tr key={point.index}><td>{tr(point.concentration_a)}</td><td>{tr(point.concentration_b)}</td><td>{tr((point.inhibition_combination * 100).toFixed(2))}%</td><td>{tr(signed(point.bliss_excess_percentage_points))}</td><td>{tr(signed(point.hsa_excess_percentage_points))}</td></tr>))}</tbody></table></div>
        <p>{tr("양수는 기준보다 높은 억제율, 음수는 낮은 억제율입니다. pp는 퍼센트포인트이며 임상 효능의 증가율을 뜻하지 않습니다.")}</p>
        <ul>{tr(report.limitations.map(item => <li key={item}>{tr(item)}</li>))}</ul>
        <a href={report.reference_url} target="_blank" rel="noreferrer">{tr("계산 방법: SynergyFinder 2.0 원논문")}</a>
      </div>)}
    </div>
  </details>;
}
