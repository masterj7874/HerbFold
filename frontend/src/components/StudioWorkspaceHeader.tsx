import { tr, localeCode, getLanguage } from "../lib/i18n";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Crosshair, Dna, ExternalLink, LoaderCircle, RefreshCw, ScanLine } from "lucide-react";
import { api } from "../lib/api";
import { predictionRequest, PredictionRequestTimeout } from "../lib/predictionPolling";
import { createTargetRequestGuard, mergeProteinTargets, normalizeTargetAccession, targetEntryUrl, targetSequencePreview, validTargetAccession } from "../lib/proteinTargets";
import type { ProteinTarget } from "../lib/proteinTargets";
import type { Compound } from "../types/app";
import "./studio-workspaces.css";

export default function StudioWorkspaceHeader({ compound, target, version, onTarget, onCalculate, refreshKey = 0, onTargetDetails }: {
  compound: Compound | null; target: string; version?: string;
  onTarget: (value: string) => void; onQuantum: () => void; onCalculate: () => void;
  refreshKey?: number; onTargetDetails?: (value: ProteinTarget | null) => void;
}) {
  const [draft, setDraft] = useState(target);
  const [error, setError] = useState("");
  const [lookupError, setLookupError] = useState("");
  const [listError, setListError] = useState("");
  const [targets, setTargets] = useState<ProteinTarget[]>([]);
  const [registering, setRegistering] = useState(false);
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const registration = useRef(createTargetRequestGuard());
  const detailsCallback = useRef(onTargetDetails);
  detailsCallback.current = onTargetDetails;
  const appliedTarget = targets.find((record) => record.accession === target) || null;
  const draftId = normalizeTargetAccession(draft);
  const targetPending = draftId !== target;
  const previewTarget = targets.find((record) => record.accession === draftId) || (!targetPending ? appliedTarget : null);
  const sequencePreview = previewTarget ? targetSequencePreview(previewTarget) : null;

  useEffect(() => { setDraft(target); }, [target]);
  useEffect(() => {
    registration.current.invalidate(); setRegistering(false); setError("");
    return () => registration.current.invalidate();
  }, [target, refreshKey]);
  useEffect(() => { detailsCallback.current?.(appliedTarget); }, [appliedTarget, target]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setLookupError(""); setListError("");
    void predictionRequest((signal) => api<ProteinTarget>(`/molecular/targets/${encodeURIComponent(target)}`, undefined, { signal }), { signal: controller.signal })
      .then((record) => {
        if (controller.signal.aborted) return;
        if (record.accession === target) setTargets((previous) => mergeProteinTargets(previous, [record]));
      })
      .catch((problem: Error) => { if (!controller.signal.aborted) setLookupError(`적용 표적의 정보를 확인하지 못했습니다. ${problem.message}`); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    void predictionRequest((signal) => api<{ items: ProteinTarget[] }>("/molecular/targets", undefined, { signal }), { signal: controller.signal })
      .then(({ items }) => { if (!controller.signal.aborted) setTargets((previous) => mergeProteinTargets(previous, items)); })
      .catch((problem: Error) => { if (!controller.signal.aborted) setListError(`등록된 표적 목록을 확인하지 못했습니다. ${problem.message}`); });
    return () => controller.abort();
  }, [target, refreshKey, revision]);

  function changeDraft(value: string) {
    registration.current.invalidate(); setRegistering(false); setError(""); setDraft(value);
  }

  async function applyTarget() {
    const accession = normalizeTargetAccession(draft);
    if (!validTargetAccession(accession)) { setError("표적의 UniProt 식별자를 입력하세요. 예: P35354, P23219"); return; }
    if (registering) return;
    const request = registration.current.begin();
    setRegistering(true); setError("");
    try {
      const record = await predictionRequest((signal) => api<ProteinTarget>("/molecular/targets/register", { accession }, { signal }), { signal: request.signal, timeoutMs: 30_000 });
      if (!request.isCurrent()) return;
      setTargets((previous) => mergeProteinTargets(previous, [record]));
      setDraft(record.accession); setLookupError("");
      onTarget(record.accession);
    } catch (problem) {
      if (!request.isCurrent()) return;
      setError(problem instanceof PredictionRequestTimeout
        ? "표적 조회 응답이 지연되고 있습니다. 현재 적용 표적은 유지했습니다. 같은 ID로 다시 적용하면 서버에 등록된 기록도 확인합니다."
        : `표적을 적용하지 못했습니다. 현재 표적은 유지됩니다. ${(problem as Error).message}`);
    } finally { if (request.isCurrent()) setRegistering(false); }
  }

  return <section className="sw-intro" aria-label={tr("AlphaFold 스튜디오 연구 입력")} data-testid="alphafold-studio-overview">
    <div className="sw-intro-copy">
      <span className="sw-kicker"><Dna size={17} /> ALPHAFOLD {tr(version || "3")} · MOLECULAR STRUCTURE</span>
      <h2>{tr("성분을 고르고, 분자 구조를 살펴보세요.")}</h2>
      <p>{tr("선택한 성분의 분자 특성을 분석하고, 단백질 표적과 함께 AF3를 계산해 예측 구조를 확인합니다.")}</p>
      <ol className="sw-steps" aria-label={tr("분자 구조 분석 순서")}><li><span>1</span> {tr(" 성분 선택")}</li><li><span>2</span> {tr(" 분자 분석")}</li><li><span>3</span> {tr(" AF3 구조 확인")}</li></ol>
    </div>
    <div className="sw-input-card">
      <span className="sw-input-label">{tr("분석할 성분")}</span>
      <strong data-testid="alphafold-selected-compound">{compound ? (getLanguage() === "en" ? compound.name || compound.name_ko : compound.name_ko || compound.name) : tr("라이브러리에서 성분 선택")}</strong>
      <form onSubmit={(event) => { event.preventDefault(); void applyTarget(); }}>
        <label htmlFor="alphafold-target"><Crosshair size={14} /> {tr(" 단백질 표적 · UniProt ID")}</label>
        <div className="sw-target-row"><input id="alphafold-target" value={draft} onChange={(event) => changeDraft(event.target.value)} maxLength={20} spellCheck={false} autoCapitalize="characters" aria-invalid={!!error} aria-describedby={error ? "alphafold-target-help alphafold-target-error" : "alphafold-target-help"} data-testid="alphafold-target-input" /><button type="submit" className="secondary-button" disabled={registering} data-testid="alphafold-target-apply">{tr(registering && <LoaderCircle size={15} className="spin" />)}{tr(registering ? "확인 중" : "적용")}</button></div>
        <p className="sw-target-help" id="alphafold-target-help">{tr("UniProt에서 단백질 정보와 서열을 확인해 적용합니다. AF3 계산은 별도로 시작합니다.")}</p>
        {tr(error && <p className="sw-input-error" id="alphafold-target-error" role="alert" data-testid="alphafold-target-error">{tr(error)}</p>)}
      </form>
      {tr(targets.length > 0 && <label className="sw-target-saved">{tr("등록된 표적")}<select aria-label={tr("등록된 단백질 표적")} value={targets.some((record) => record.accession === draftId) ? draftId : ""} onChange={(event) => changeDraft(event.target.value)} data-testid="alphafold-target-saved"><option value="" disabled>{tr("등록된 표적 선택")}</option>{tr(targets.map((record) => <option key={record.accession} value={record.accession}>{tr(record.accession)} · {tr(record.gene || record.name)} · {tr(record.organism)}</option>))}</select></label>)}
      {tr(previewTarget && <div className="sw-target-record" data-testid="alphafold-target-details" data-target-accession={previewTarget.accession}>
        <div className="sw-target-record-heading"><span>{tr(targetPending ? "적용 전 표적" : "현재 적용 표적")}</span><a href={targetEntryUrl(previewTarget.accession)} target="_blank" rel="noreferrer">UniProt <ExternalLink size={12} /></a></div>
        <strong>{tr(previewTarget.name)}</strong>
        <p>{tr(previewTarget.gene && <span>{tr(previewTarget.gene)} · </span>)}{tr(previewTarget.organism)}</p>
        <div className="sw-target-facts"><span>{tr(previewTarget.accession)}</span><span>{tr(previewTarget.length.toLocaleString(localeCode()))} {tr(" 아미노산")}</span>{tr(previewTarget.reviewed != null && <span>{tr(previewTarget.reviewed ? "검토된 항목" : "자동 주석 항목")}</span>)}</div>
        {tr(sequencePreview?.text && <details className="sw-target-sequence"><summary>{tr("아미노산 서열 미리보기")}</summary><p>{tr("전체 ")}{tr(previewTarget.length.toLocaleString(localeCode()))}{tr("개 중 ")}{tr(sequencePreview.shown)}{tr("개 표시")}</p><code>{tr(sequencePreview.text)}{tr(sequencePreview.truncated ? " …" : "")}</code></details>)}
      </div>)}
      {tr(loading && !appliedTarget && <p className="sw-target-loading" role="status"><LoaderCircle size={14} className="spin" />{tr("적용 표적 정보 확인 중")}</p>)}
      {tr((lookupError || listError) && <div className="sw-target-lookup-error" role="status"><p>{tr(lookupError || listError)}</p><button type="button" onClick={() => setRevision((value) => value + 1)}><RefreshCw size={13} />{tr("표적 정보 새로고침")}</button></div>)}
      {tr(targetPending && <p className="sw-target-pending" role="status">{tr("현재 적용 표적은 ")}{tr(target)}{tr("입니다. 새 표적을 확인하고 적용한 뒤 AF3 계산을 진행하세요.")}</p>)}
      <button className="primary-button sw-open-calculation" disabled={!compound || targetPending || registering} onClick={onCalculate} data-testid="alphafold-open-calculation"><ScanLine size={16} /> {tr(" 이 성분의 AF3 계산 ")}<ArrowRight size={15} /></button>
    </div>
  </section>;
}
