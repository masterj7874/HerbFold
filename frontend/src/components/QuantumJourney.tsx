import { useEffect, useId, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useInView } from "motion/react";
import { ArrowDownToLine, ArrowRight, Binary, BookOpen, Check, ChevronLeft, ChevronRight, CircleHelp, CircuitBoard, FlaskConical, Focus, Gauge, Layers3, Pause, Play, ScanLine, Waves } from "lucide-react";
import type { Compound } from "../types/app";
import type { QuantumResult } from "../types/quantum";
import { download, fetchText } from "../lib/api";
import { quantumMethod, quantumNumber, quantumOrigin, validKernel } from "../lib/quantumEvidence";
import { encodingAngles, gateAngles, quantumProgress, readBloch, readBlocks } from "../lib/quantumJourney";
import { useMotionPreference } from "../lib/useMotionPreference";
import QuantumBlochSphere from "./QuantumBlochSphere";
import "./quantum-journey.css";

type Props = { value: QuantumResult | null | undefined; compounds?: Compound[]; inputFeatures?: number[][] };
type Axis = "X" | "Y" | "Z";
const stages = [
  { title: "분자 입력", en: "MOLECULAR INPUT", icon: FlaskConical, question: "어떤 정보를 비교하나요?", body: "분자 구조에서 얻은 수치 기술자를 정해진 순서로 전달합니다. 분자 자체나 단백질을 큐빗 안에 넣는 과정은 아닙니다." },
  { title: "각도 인코딩", en: "ANGLE ENCODING", icon: Binary, question: "분자 특징이 어떻게 회로가 되나요?", body: "전처리한 각 특징 x를 2 atan(x)로 바꾸어 회전 각도를 만듭니다. 입력 차원보다 큐빗이 많으면 같은 특징을 반복해서 배치합니다." },
  { title: "양자 회로", en: "QUANTUM CIRCUIT", icon: CircuitBoard, question: "회로 안에서는 무엇을 하나요?", body: "H 게이트로 시작해 Ry·Rz 회전으로 특징을 인코딩하고, 기록된 블록 안의 CZ 연결을 적용합니다. 블록을 선택하면 실제 논리 큐빗 구성을 확대해 볼 수 있습니다." },
  { title: "반복 측정", en: "OBSERVABLE READOUT", icon: ScanLine, question: "양자 상태를 어떻게 읽나요?", body: "X·Y·Z 방향을 각각 반복 측정해 큐빗별 기대값을 추정합니다. 한 번의 shot은 전체 비트열 한 개이며, 여러 shot에서 0과 1의 빈도를 집계합니다." },
  { title: "유사도 비교", en: "KERNEL & CONTROLS", icon: Gauge, question: "측정값은 어떻게 비교하나요?", body: "분자별 X·Y·Z 기대값 사이의 제곱 거리를 구한 뒤 고전적인 RBF 변환을 적용합니다. 이상적 회로와 고전 기술자 기준을 별도 결과로 비교합니다." },
];
const short = quantumNumber;
const statusNames: Record<string,string> = { completed: "완료", submitted: "제출됨", submitting: "제출 중", running: "진행 중", queued: "대기 중", failed: "실패", cancelled: "취소", off: "생략", blocked: "차단", ready: "계획 준비", prepared: "준비됨", partial_submission: "일부 제출", submission_failed: "제출 실패", interrupted: "중단" };
const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const featureNames: Record<string, string> = { molecular_weight: "분자량", logp: "LogP", tpsa: "TPSA", qed: "QED", hbd: "HBD", hba: "HBA" };
const sketchCache = new Map<string, Promise<string>>();

function MoleculeSketch({ compound }: { compound: Compound | undefined }) {
  const [image, setImage] = useState<{ smiles: string; url: string } | null>(null);
  const [failed, setFailed] = useState(false);
  const smiles = compound?.smiles;
  useEffect(() => {
    setFailed(false); setImage(null);
    if (!smiles) return;
    let cancelled = false, objectUrl: string | undefined;
    if (!sketchCache.has(smiles)) {
      if (sketchCache.size > 32) sketchCache.clear();
      sketchCache.set(smiles, fetchText("/molecules/svg", { smiles }).catch(error => { sketchCache.delete(smiles); throw error; }));
    }
    void sketchCache.get(smiles)!.then(svg => {
      if (cancelled) return;
      objectUrl = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
      setImage({ smiles, url: objectUrl });
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [smiles]);
  return <div className="qj-molecule-sketch">{image?.smiles === smiles && image ? <img src={image.url} alt={`${compound?.name_ko || compound?.name || compound?.id}의 저장된 SMILES를 그린 2차원 구조`} /> : <div><FlaskConical size={32} /><span>{failed ? "구조 그림을 불러오지 못했습니다" : smiles ? "저장된 구조를 그리는 중" : "연결된 분자 구조 없음"}</span></div>}</div>;
}

function MiniKernel({ matrix, name, selected, onSelect }: { matrix: unknown; name: string; selected?: [number, number]; onSelect?: (a: number, b: number) => void }) {
  const rows = validKernel(matrix) ? matrix : null;
  return <div className="qj-mini-kernel"><h6>{name}</h6>{rows ? <div className="qj-mini-scroll"><div className="qj-mini-grid" style={{ gridTemplateColumns: `26px repeat(${rows.length}, minmax(34px, 1fr))` }}>
    <span />{rows.map((_, i) => <span key={`h${i}`}>M{i + 1}</span>)}
    {rows.map((row, i) => <div className="qj-grid-row" key={i}><span>M{i + 1}</span>{row.map((cell, j) => <button key={j} type="button" disabled={!onSelect} aria-label={`${name} M${i + 1} × M${j + 1}: ${quantumNumber(cell)}`} aria-pressed={selected ? selected[0] === i && selected[1] === j : undefined} onClick={() => onSelect?.(i, j)} style={{ background: `rgba(43, 142, 167, ${.06 + Math.max(0, Math.min(1, cell)) * .72})`, color: cell > .6 ? "#fff" : "#173c52" }}>{short(cell)}</button>)}</div>)}
  </div></div> : <div className="qj-unavailable">이 기록에는 행렬이 없습니다.</div>}</div>;
}

export function QuantumHeroGraphic() {
  const uid = useId().replace(/:/g, "");
  return <svg className="qs-hero-diagram" viewBox="0 0 480 182" role="img" aria-label="입력, 회로, 측정을 연결한 설명용 개념도">
    <defs><linearGradient id={`${uid}-beam`}><stop stopColor="#7ee1cf" /><stop offset="1" stopColor="#99aeff" /></linearGradient></defs>
    <rect x="158" y="18" width="197" height="130" rx="12" fill="#97c0ff09" stroke="#7bdbcb70" strokeDasharray="5 5" />
    {[0, 1, 2, 3].map(i => <g key={i}><text x="39" y={49 + i * 26} fill="#98b8d1" fontSize="11">x{i + 1}</text><path d={`M65 ${45 + i * 26} H424`} stroke={`url(#${uid}-beam)`} strokeWidth="1.2" opacity=".6" /><rect x="102" y={34 + i * 26} width="32" height="22" rx="4" fill="#214b64" stroke="#77ccbf80" /><text x="118" y={49 + i * 26} fill="#b8f0e3" textAnchor="middle" fontSize="10">θ</text><rect x="179" y={34 + i * 26} width="34" height="22" rx="4" fill="#7160b7" /><text x="196" y={49 + i * 26} fill="#fff" textAnchor="middle" fontSize="10">Ry</text><rect x="235" y={34 + i * 26} width="34" height="22" rx="4" fill="#585795" /><text x="252" y={49 + i * 26} fill="#fff" textAnchor="middle" fontSize="10">Rz</text><circle cx="311" cy={45 + i * 26} r="4" fill="#a6e5d7" /><rect x="378" y={34 + i * 26} width="27" height="22" rx="4" fill="#d0eeec" /><path d={`M383 ${49 + i * 26} q8 -13 16 0 m-8 0 5 -10`} fill="none" stroke="#245365" strokeWidth="1.4" /></g>)}
    <path d="M311 45V71 M311 97V123" stroke="#a6e5d7" strokeWidth="2" />
    <text x="115" y="169" fill="#acd9d6" textAnchor="middle" fontSize="10">ENCODE</text><text x="251" y="169" fill="#c7b9f4" textAnchor="middle" fontSize="10">TRANSFORM</text><text x="392" y="169" fill="#acd9d6" textAnchor="middle" fontSize="10">MEASURE</text>
  </svg>;
}

export default function QuantumJourney({ value, compounds = [], inputFeatures }: Props) {
  const reduced = useMotionPreference();
  const [stage, setStage] = useState(2), [playing, setPlaying] = useState(false);
  const [sample, setSample] = useState(0), [blockIndex, setBlockIndex] = useState(0), [logical, setLogical] = useState(0);
  const [axis, setAxis] = useState<Axis>("Z"), [layer, setLayer] = useState(0), [pair, setPair] = useState<[number, number]>([0, 1]);
  const [pageVisible, setPageVisible] = useState(true);
  const section = useRef<HTMLElement>(null), figure = useRef<HTMLDivElement>(null);
  const visible = useInView(section, { amount: .15 });
  const uid = useId().replace(/:/g, "");
  const identity = [value?.source_analysis_id, value?.plan?.feature_sha256 || value?.metadata?.feature_sha256, value?.jobs?.map(j => j.job_id).join(","), value?.sample_ids?.join(",")].join("|");
  useEffect(() => { setSample(0); setBlockIndex(0); setLogical(0); setLayer(0); setPair([0, 1]); setPlaying(false); }, [identity]);
  useEffect(() => {
    const update = () => setPageVisible(!document.hidden);
    update(); document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  useEffect(() => { if (reduced) setPlaying(false); }, [reduced]);
  const animate = playing && visible && pageVisible && !reduced;
  useEffect(() => {
    if (!animate) return;
    const timer = setTimeout(() => { if (stage === 4) setPlaying(false); else setStage(s => s + 1); }, 5500);
    return () => clearTimeout(timer);
  }, [animate, stage]);
  const method = value ? quantumMethod(value) : "projected", projected = method === "projected";
  const plan = value?.plan || value?.metadata;
  const blocks = useMemo(() => readBlocks(value), [value]);
  const block = blocks[blockIndex] || blocks[0];
  const nQubits = finite(plan?.n_qubits) && Number.isInteger(plan.n_qubits) && plan.n_qubits > 0 ? plan.n_qubits : null;
  const definition = value?.feature_definition;
  const candidateRows = inputFeatures || definition?.features;
  const validRows = Array.isArray(candidateRows) && candidateRows.length > 0 && candidateRows.every(r => Array.isArray(r) && r.length === candidateRows[0].length && r.length > 0 && r.every(finite));
  const rows = validRows ? candidateRows : null;
  const definitionMatches = !!rows && JSON.stringify(rows) === JSON.stringify(definition?.features);
  const count = value?.sample_ids?.length || rows?.length || value?.kernel?.length || value?.projected_features?.values?.length || 0;
  const currentSample = sample < count ? sample : 0;
  const labels = Array.from({ length: count }, (_, i) => value?.sample_labels?.[i] || compounds.find(c => c.id === value?.sample_ids?.[i])?.name_ko || compounds.find(c => c.id === value?.sample_ids?.[i])?.name || value?.sample_ids?.[i] || `입력 ${i + 1}`);
  const compound = compounds.find(c => c.id === value?.sample_ids?.[currentSample]);
  const currentRows = rows?.length === count ? rows[currentSample] : null;
  const supportedEncoding = plan?.encoding === "atan_fold_or_repeat_v1";
  const supportedCircuit = plan?.feature_map === "independent_blocks_ry_rz_cz_v1";
  const angles = supportedEncoding && currentRows && nQubits ? encodingAngles(currentRows, nQubits) : [];
  const rotations = block ? gateAngles(angles, block, layer) : [];
  const currentLogical = nQubits && logical < nQubits ? logical : 0;
  const vector = readBloch(value, currentSample, currentLogical);
  const progressValue = value && rows ? { ...value, features: rows } : value;
  const progress = quantumProgress(progressValue);
  const origin = value ? quantumOrigin(value) : "unverified";
  const measured = origin === "hardware" && value?.status === "completed";
  const matrix = validKernel(value?.kernel) ? value.kernel : null;
  const selectedPair: [number, number] = matrix && pair.every(i => i < matrix.length) ? pair : [0, 0];
  const layerCount = finite(plan?.layers) && Number.isInteger(plan.layers) && plan.layers > 0 && plan.layers <= 8 ? plan.layers : null;
  const remainingLayers = layerCount ? Math.max(0, layerCount - layer - 1) : null;
  const globalObservations = value?.jobs?.flatMap(j => Array.isArray(j.observations) ? j.observations : []).filter(o => Array.isArray(o.pair) && o.pair.length === 2 && o.pair.every(i => Number.isInteger(i) && i >= 0 && i < count) && finite(o.shots) && o.shots > 0 && finite(o.zero_counts) && o.zero_counts >= 0 && o.zero_counts <= o.shots) || [];
  const go = (index: number) => { setPlaying(false); setStage(index); };
  const stageInfo = stages[stage];
  const graphHeight = block ? Math.max(240, block.logical_qubits.length * 58 + 102) : 290;
  const circuitWidth = 740;
  const caption = stage === 2 ? "저장된 블록 연결과 논리 게이트의 설명도입니다. 펄스 시간이나 실시간 상태 궤적이 아닙니다." : stage === 3 ? projected ? "구체와 막대는 선택한 큐빗의 X·Y·Z 기대값입니다. 전체 양자 상태를 복원한 그림은 아닙니다." : "전역 all-zero 관측 횟수와 shot 분모를 표시합니다. 전체 관측 목록과 구간은 아래 상세 행렬에서 확인합니다." : "선택 기록의 입력·결과를 바탕으로 그렸습니다. 설명 재생은 새 양자 계산을 실행하지 않습니다.";
  const exportFigure = () => {
    const svg = figure.current?.querySelector("svg[data-export-figure]");
    if (!svg) return;
    const copy = svg.cloneNode(true) as SVGElement;
    const ns = "http://www.w3.org/2000/svg";
    copy.setAttribute("xmlns", ns);
    const metadata = document.createElementNS(ns, "metadata");
    metadata.textContent = JSON.stringify({ kind: "logical-circuit-explanation", feature_sha256: plan?.feature_sha256, source_analysis_id: value?.source_analysis_id, provider_jobs: value?.jobs?.map(j => j.job_id), sample_index: currentSample, sample_id: value?.sample_ids?.[currentSample], sample_label: labels[currentSample], block_index: blockIndex, logical_qubits: block?.logical_qubits, layer_zero_based: layer, measurement_axis: axis, scope: caption });
    copy.insertBefore(metadata, copy.firstChild);
    const box = (copy.getAttribute("viewBox") || "0 0 740 350").split(/\s+/).map(Number);
    copy.setAttribute("viewBox", `0 0 ${box[2]} ${box[3] + 48}`);
    const note = document.createElementNS(ns, "text");
    note.setAttribute("x", "18"); note.setAttribute("y", String(box[3] + 18)); note.setAttribute("font-size", "10"); note.setAttribute("fill", "#536f85");
    note.textContent = `M${currentSample + 1} · ${(labels[currentSample] || "입력 미기록").slice(0, 60)} · ${axis} readout · logical circuit schematic`;
    copy.appendChild(note);
    const hash = document.createElementNS(ns, "text");
    hash.setAttribute("x", "18"); hash.setAttribute("y", String(box[3] + 35)); hash.setAttribute("font-size", "9"); hash.setAttribute("fill", "#73899a");
    hash.textContent = `Feature SHA-256: ${plan?.feature_sha256 || "not recorded"}`; copy.appendChild(hash);
    download(new XMLSerializer().serializeToString(copy), `quantum-${method}-${(plan?.feature_sha256 || "record").slice(0, 10)}-block-${blockIndex + 1}.svg`, "image/svg+xml");
  };
  return <section ref={section} className="qj" data-testid="quantum-journey" data-stage={stage} data-animation-playing={animate} data-feature-sha={plan?.feature_sha256 || ""}>
    <header className="qj-heading"><div><span className="qj-eyebrow"><BookOpen size={13} /> THE QUANTUM JOURNEY</span><h5>분자에서 측정값까지, 한 단계씩.</h5></div><div className="qj-play-controls"><button onClick={() => { if (!playing && stage === 4) setStage(0); setPlaying(p => !p); }} disabled={reduced} aria-pressed={playing} data-testid="quantum-explainer-play">{playing ? <Pause size={15} /> : <Play size={15} />}{playing ? "설명 일시정지" : "설명 재생"}</button><span>{reduced ? "동작 줄이기 · 수동 탐색" : "계산 실행과 별도"}</span></div></header>
    <div className="qj-steps" role="tablist" aria-label="양자 계산 설명 단계">{stages.map((item, i) => <button key={item.en} role="tab" id={`${uid}-tab-${i}`} aria-controls={`${uid}-panel`} aria-selected={stage === i} tabIndex={stage === i ? 0 : -1} className={stage === i ? "active" : ""} onClick={() => go(i)} onKeyDown={e => { const next = e.key === "ArrowRight" ? (i + 1) % 5 : e.key === "ArrowLeft" ? (i + 4) % 5 : e.key === "Home" ? 0 : e.key === "End" ? 4 : null; if (next !== null) { e.preventDefault(); go(next); document.getElementById(`${uid}-tab-${next}`)?.focus(); } }} data-testid={`quantum-stage-${i}`}><span className="qj-step-num">0{i + 1}</span><item.icon size={18} /><strong>{item.title}</strong><span className="qj-step-state">{progress[i]?.state === "available" ? "기록 있음" : progress[i]?.state === "pending" ? "대기 중" : progress[i]?.state === "error" ? "상태 확인" : "설명 보기"}</span></button>)}</div>
    <div className="qj-toolbar">{count > 0 ? <label>입력 분자<select value={currentSample} onChange={e => { setSample(Number(e.target.value)); setPlaying(false); }} data-testid="quantum-journey-sample">{labels.map((label, i) => <option key={i} value={i}>M{i + 1} · {label}</option>)}</select></label> : <span>기록을 선택하면 실제 입력과 결과가 연결됩니다.</span>}<span className={`qj-evidence-badge ${measured ? "measured" : ""}`}><span />{measured ? "저장된 IBM 측정" : origin === "local" ? "로컬 계산 기록" : "실행 근거 확인"}</span></div>
    <div className="qj-record-facts" aria-label="저장된 실행 규모"><span><b>{quantumNumber(nQubits)}</b> 큐빗</span><span><b>{quantumNumber(plan?.circuit_count)}</b> 계획 회로</span><span><b>{quantumNumber(plan?.shots)}</b> shots / 회로</span><span><b>{quantumNumber(plan?.total_shots)}</b> 계획 shots</span></div>
    <div id={`${uid}-panel`} role="tabpanel" aria-labelledby={`${uid}-tab-${stage}`} className="qj-stage-panel">
      <div className="qj-stage-copy"><span className="qj-panel-letter">{String.fromCharCode(97 + stage)}</span><div><span className="qj-eyebrow">{stageInfo.en}</span><h6>{stageInfo.question}</h6><p>{!projected && stage >= 2 ? stage === 2 ? "이 기록은 전역 Fidelity 방식입니다. 두 입력의 특징 회로를 합성해 역회로를 적용한 뒤, 모든 비트가 0으로 돌아온 빈도를 측정합니다." : stage === 3 ? "각 shot의 전체 비트열에서 all-zero 결과를 셉니다. 관측 횟수가 0인 것과 측정이 없는 것은 서로 다릅니다." : "전역 반환 확률을 분자 쌍별로 비교합니다. 대각선까지 0인 경우 자기 입력의 반환 신호도 분해하지 못했다는 의미입니다." : origin === "local" && stage === 3 ? "로컬 시뮬레이터에서 선택한 큐빗의 X·Y·Z 기대값을 계산한 결과입니다. 저장된 좌표를 구체와 막대로 확인합니다." : stageInfo.body}</p></div></div>
      <AnimatePresence mode="wait" initial={false}><motion.div ref={figure} key={`${identity}-${stage}`} className="qj-figure" initial={reduced ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={reduced ? undefined : { opacity: 0 }} transition={{ duration: .2 }}>
        {stage === 0 && <div className="qj-input-visual"><div className="qj-chemical-card"><span className="qj-small-label">{count ? `M${currentSample + 1} · 저장된 구조` : "분자 미선택"}</span><MoleculeSketch compound={compound} /><strong>{labels[currentSample] || "분자 미선택"}</strong>{compound?.smiles && <code>{compound.smiles}</code>}</div><ArrowRight className="qj-flow-arrow" size={25} /><div className="qj-descriptors"><span className="qj-small-label">회로에 전달된 전처리 값</span>{currentRows ? currentRows.slice(0, 8).map((n, i) => <div className="qj-descriptor" key={i}><span><b>x{i + 1}</b>{definitionMatches ? featureNames[definition!.columns?.[i]] || definition!.columns?.[i] || "특징" : "특징"}</span><div><motion.i initial={reduced ? false : { scaleX: 0 }} animate={{ scaleX: Math.min(.95, Math.abs(n) / (1 + Math.abs(n))) }} transition={{ duration: .4, delay: reduced ? 0 : i * .06 }} /></div><code>{quantumNumber(n)}</code></div>) : <div className="qj-unavailable">입력 특징값이 저장되지 않았습니다.</div>}<p>{definitionMatches ? "특징 표의 전처리 제수로 나눈 값입니다." : "열 이름·단위는 정의가 확인된 경우에만 붙입니다."} 막대 길이는 |x|/(1+|x|)로 표시합니다.{currentRows && currentRows.length > 8 ? ` 전체 ${currentRows.length}개 중 첫 8개입니다.` : ""}</p></div></div>}
        {stage === 1 && <div className="qj-encoding"><svg data-export-figure viewBox="0 0 580 290" role="img" aria-label="각도 인코딩 함수와 선택 입력값" style={{ width: "100%", background: "#fbfcff" }}>
          <rect x="0" y="0" width="580" height="290" fill="#fbfcff" /><path d="M54 145H540 M297 29V250" stroke="#9fb2c8" /><path d="M54 41H540 M54 249H540" stroke="#dae2ec" strokeDasharray="4 6" /><text x="34" y="46" fontSize="12" fill="#6b7b94">π</text><text x="25" y="253" fontSize="12" fill="#6b7b94">−π</text><text x="535" y="164" fontSize="12" fill="#6b7b94">x</text><text x="308" y="31" fontSize="12" fill="#6b7b94">θ</text>
          <motion.path d={Array.from({ length: 121 }, (_, i) => { const x = -4 + i / 15; return `${i ? "L" : "M"}${297 + x * 58},${145 - 2 * Math.atan(x) * 33}`; }).join(" ")} fill="none" stroke="#7761bd" strokeWidth="3" initial={reduced ? false : { pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: .6 }} />
          {(supportedEncoding ? currentRows : null)?.filter(x => Math.abs(x) <= 4).slice(0, 12).map((x, i) => <g key={i}><path d={`M${297 + x * 58} 145 V${145 - 2 * Math.atan(x) * 33}`} stroke="#24a592" strokeDasharray="3 3" /><circle cx={297 + x * 58} cy={145 - 2 * Math.atan(x) * 33} r="5" fill="#24a592" stroke="#fff" strokeWidth="2" /></g>)}
          <text x="58" y="72" fill="#514985" fontSize="21">θ = 2 atan(x)</text><text x="58" y="95" fill="#728099" fontSize="12">−π &lt; θ &lt; π</text><text x="56" y="275" fill="#60738c" fontSize="11">곡선: 인코딩 정의 · 점: |x| ≤ 4인 현재 입력값 (최대 12개)</text>
        </svg><div className="qj-encoding-values"><span className="qj-small-label">논리 큐빗별 각도 · rad</span><div className="qj-angle-grid">{angles.length ? angles.slice(0, 12).map((n, i) => <div key={i}><span>q{i}</span><code>{short(n)}</code></div>) : <p className="qj-unavailable">입력·큐빗 수·지원되는 인코딩 정의가 모두 있어야 실제 각도를 계산합니다.</p>}</div><p>{angles.length > 12 ? `${angles.length}개 중 첫 12개를 표시합니다. ` : ""}{currentRows && nQubits && currentRows.length < nQubits ? `${currentRows.length}개 특징을 ${nQubits}개 큐빗에 반복합니다. 반복은 입력 정보량을 늘리지 않습니다.` : "특징 차원이 더 크면 범위를 제한한 값을 큐빗별로 나눠 평균합니다."}</p></div></div>}
        {stage === 2 && projected && <><div className="qj-circuit-heading"><div><span className="qj-small-label">OVERVIEW → DETAIL</span><strong>{nQubits || "—"}개 큐빗 <span>/</span> {blocks.length || "—"}개 독립 블록</strong></div><div className="qj-circuit-controls"><label>층<select value={layer} onChange={e => { setLayer(Number(e.target.value)); setPlaying(false); }} disabled={!layerCount}>{Array.from({ length: layerCount || 1 }, (_, i) => <option key={i} value={i}>{i + 1}{!layerCount ? " · 미기록" : ""}</option>)}</select></label><label>측정 축<select value={axis} onChange={e => { setAxis(e.target.value as Axis); setPlaying(false); }} aria-label="회로 측정 축">{["X", "Y", "Z"].map(a => <option key={a}>{a}</option>)}</select></label></div></div>
          {blocks.length && supportedCircuit && layerCount ? <><div className="qj-block-map" aria-label="저장된 독립 회로 블록">{blocks.map((b, i) => <button key={i} aria-pressed={i === blockIndex} onClick={() => { setBlockIndex(i); setLogical(b.logical_qubits[0]); setPlaying(false); }} data-testid={`quantum-block-${i}`} title={`블록 ${i + 1} · 논리 ${b.logical_qubits.map(q => `q${q}`).join(", ")}`}><span>{String(i + 1).padStart(2, "0")}</span><i>{b.logical_qubits.length}q</i></button>)}</div><div className="qj-circuit-scroll" tabIndex={0} aria-label="선택 블록 회로 가로 스크롤"><svg data-export-figure viewBox={`0 0 ${circuitWidth} ${graphHeight}`} style={{ minWidth: 630, width: "100%", background: "#fbfcff" }} role="img" aria-label={`블록 ${blockIndex + 1}의 H, Ry, Rz, CZ, ${axis}축 측정 논리 회로`}>
            <rect width={circuitWidth} height={graphHeight} fill="#fbfcff" /><rect x="104" y="28" width="510" height={graphHeight - 68} rx="12" fill="#f4faf9" stroke="#82bbac" strokeDasharray="6 5" />
            <text x="128" y="52" fontSize="11" fill="#3f786b">BLOCK {String(blockIndex + 1).padStart(2, "0")} · 선택한 층 {layer + 1}{layer > 0 ? " (이전 층 출력부터)" : ""}</text>
            {block?.logical_qubits.map((q, i) => { const y = 88 + i * 58, gate = rotations[i]; return <g key={q}>
              <text x="17" y={y + 2} fontSize="13" fill="#294b66">q{q}</text><text x="17" y={y + 18} fontSize="9" fill="#7688a0">물리 {plan?.physical_qubits?.[q] ?? "—"}</text><path d={`M87 ${y} H696`} stroke="#6d8398" strokeWidth="1.2" />
              {layer === 0 && <><rect x="127" y={y - 16} width="34" height="32" rx="4" fill="#e5eefa" stroke="#b3c8e3" /><text x="144" y={y + 5} textAnchor="middle" fontSize="13" fill="#41618a">H</text></>}
              {[[203, "Ry", gate?.ry], [297, "Rz", gate?.rz]].map(([x, label, angle]) => <g key={String(label)}><rect x={Number(x)} y={y - 18} width="70" height="36" rx="5" fill={label === "Ry" ? "#dfd3f7" : "#eee5fb"} stroke="#ab91d1" /><text x={Number(x) + 35} y={y - 2} textAnchor="middle" fontSize="12" fill="#5a4089">{label}</text><text x={Number(x) + 35} y={y + 11} textAnchor="middle" fontSize="9" fill="#69508a">{short(angle)} rad</text></g>)}
              <rect x="541" y={y - 17} width="57" height="34" rx="4" fill="#e8f2f5" stroke="#a9c3ca" /><text x="569" y={y + 5} textAnchor="middle" fontSize="11" fill="#386a78">{remainingLayers ? `+${remainingLayers}층·${axis}` : axis === "X" ? "H" : axis === "Y" ? "S† → H" : "I"}</text>
              <rect x="638" y={y - 17} width="35" height="34" rx="5" fill="#153f53" /><path d={`M644 ${y + 6} q12 -24 24 0 M656 ${y + 6} l8 -18`} stroke="#c4eee7" strokeWidth="1.5" fill="none" />
            </g>; })}
            {[...(block?.edges || []).filter((_, i) => i % 2 === 0), ...(block?.edges || []).filter((_, i) => i % 2 === 1)].map(([a, b], i, all) => { const x = 405 + i * Math.min(21, 112 / Math.max(1, all.length - 1)); const y1 = 88 + (block?.logical_qubits.indexOf(a) || 0) * 58, y2 = 88 + (block?.logical_qubits.indexOf(b) || 0) * 58; return <g key={i}><line x1={x} x2={x} y1={y1} y2={y2} stroke="#23877e" strokeWidth="2" /><circle cx={x} cy={y1} r="4" fill="#23877e" /><circle cx={x} cy={y2} r="4" fill="#23877e" /></g>; })}
            <text x="460" y={graphHeight - 20} textAnchor="middle" fontSize="10" fill="#587e80">CZ · 저장된 연결 순서</text><text x="570" y={graphHeight - 20} textAnchor="middle" fontSize="10" fill="#587e80">{remainingLayers ? "후속 층·기저 생략" : "기저 변환"}</text><text x="655" y={graphHeight - 20} textAnchor="middle" fontSize="10" fill="#587e80">측정</text>
            {animate && <motion.rect x="105" y="58" width="12" height={graphHeight - 115} rx="5" fill="#40b6aa" opacity=".15" animate={{ x: [105, 685] }} transition={{ duration: 3.8, repeat: Infinity, ease: "linear" }} />}
          </svg></div><div className="qj-circuit-legend"><span><i className="qj-purple" />Ry / Rz · 각도 회전</span><span><i className="qj-teal" />CZ · 블록 내 상호작용</span><span><i className="qj-navy" />{axis}축 반복 측정</span><button onClick={exportFigure}><ArrowDownToLine size={13} />회로 SVG</button></div></> : <div className="qj-unavailable qj-large-empty"><Layers3 size={30} /><strong>지원되는 회로 구성을 확인할 수 없습니다.</strong><p>저장된 블록·층 수와 H/Ry/Rz/CZ feature-map 정의가 필요합니다. 이 정보가 있는 관측량 실행을 선택해 주세요.</p></div>}</>}
        {stage === 2 && !projected && <div className="qj-fidelity-diagram"><div><span>입력 i</span><strong>U(xᵢ)</strong><small>특징 회로</small></div><ArrowRight /><div><span>입력 j</span><strong>U(xⱼ)†</strong><small>역회로</small></div><ArrowRight /><div><span>측정</span><strong>00…0 ?</strong><small>전체 비트 반환 빈도</small></div><p>전역 Fidelity의 설명용 흐름입니다. 이 기록의 저장 정보로 확인되지 않는 블록·게이트 배치는 그리지 않습니다.</p></div>}
        {stage === 3 && projected && <><div className="qj-measurement-controls"><span className="qj-small-label">선택한 큐빗 확대</span><label>논리 큐빗<select value={currentLogical} onChange={e => { setLogical(Number(e.target.value)); setPlaying(false); }} data-testid="quantum-journey-qubit" disabled={!nQubits}>{Array.from({ length: Math.min(nQubits || 1, 2048) }, (_, q) => <option key={q} value={q}>q{q} · 물리 {plan?.physical_qubits?.[q] ?? "—"}</option>)}</select></label><span>{origin === "local" ? "로컬 회로 계산" : `${quantumNumber(plan?.shots)} shots / 회로`}</span></div><div className="qj-measurement-grid"><QuantumBlochSphere vector={vector} label={`M${currentSample + 1} · ${labels[currentSample] || "입력 미기록"}`} logical={currentLogical} physical={plan?.physical_qubits?.[currentLogical] ?? null} reducedMotion={reduced} /><div className="qj-observable-bars"><span className="qj-small-label">{origin === "local" ? "계산한 기대값 ⟨σ⟩" : "축별 기대값 ⟨σ⟩"}</span>{(["X", "Y", "Z"] as const).map((a, i) => { const n = vector?.[a.toLowerCase() as "x" | "y" | "z"]; const ai = Array.isArray(value?.projected_features?.axes) ? value.projected_features.axes.indexOf(a) : -1; const ci = value?.projected_features?.wilson_95?.[currentSample]?.[currentLogical]?.[ai]; return <div className="qj-axis-row" key={a}><div><strong>{a}</strong><code>{quantumNumber(n)}</code></div><svg viewBox="0 0 280 35" role="img" aria-label={`${a} 기대값 ${quantumNumber(n)}${ci ? `, 구간 ${ci.map(quantumNumber).join(' ~ ')}` : ''}`}><line x1="12" x2="268" y1="15" y2="15" stroke="#d9e1ea" strokeWidth="6" strokeLinecap="round" /><line x1="140" x2="140" y1="4" y2="28" stroke="#93a6b9" strokeDasharray="2 3" />{finite(n) && <><motion.line x1="140" x2={140 + n * 128} y1="15" y2="15" stroke={["#00799a", "#138165", "#7653ae"][i]} strokeWidth="6" strokeLinecap="round" initial={reduced ? false : { pathLength: 0 }} animate={{ pathLength: 1 }} />{ci?.length === 2 && ci.every(finite) && <><line x1={140 + ci[0] * 128} x2={140 + ci[1] * 128} y1="15" y2="15" stroke="#234963" strokeWidth="1.5" />{ci.map((x, j) => <line key={j} x1={140 + x * 128} x2={140 + x * 128} y1="9" y2="21" stroke="#234963" />)}</>}<circle cx={140 + n * 128} cy="15" r="5" fill={["#00799a", "#138165", "#7653ae"][i]} stroke="white" strokeWidth="1.5" /></>}</svg><small>−1<span>0</span>+1</small>{ci ? <p>Wilson 95% 구간 [{ci.map(quantumNumber).join(", ")}]</p> : <p>구간 미기록</p>}</div>; })}<div className="qj-equation">{origin === "local" ? "⟨σ⟩ = Tr(ρ σ) · 회로 상태의 관측량 기대값" : "⟨σ⟩ = (n₀ − n₁) / shots"}</div><p>막대는 저장된 기대값, 가는 선은 기록된 구간입니다. {origin === "local" ? "로컬 계산의 원시 수치는 아래 상세 근거에서 확인합니다." : "전체 bitstring과 원시 수치는 아래 상세 근거에서 확인합니다."}</p></div></div></>}
        {stage === 3 && !projected && <div className="qj-global-observation"><Waves size={33} /><h6>0회 관측과 측정 없음은 다릅니다.</h6><p>측정이 끝난 경우에만 관측 횟수를 표시합니다. 분자 쌍마다 저장된 all-zero 횟수, shots, Wilson 구간을 아래 행렬에서 선택해 확인하세요.</p><div>{globalObservations.length ? globalObservations.slice(0, 6).map((o, i) => <span key={i}>M{o.pair![0] + 1} × M{o.pair![1] + 1}<strong>{quantumNumber(o.zero_counts)} / {quantumNumber(o.shots)}</strong></span>) : <p>분자 쌍·횟수·shot 분모가 확인되는 관측이 없습니다.</p>}</div></div>}
        {stage === 4 && <><div className="qj-comparison"><MiniKernel matrix={value?.kernel} name={projected ? "관측 특징 → RBF" : "전역 반환 확률"} selected={selectedPair} onSelect={(a, b) => setPair([a, b])} /><MiniKernel matrix={value?.ideal_reference?.kernel} name="이상적 회로 · 고전 계산" /><MiniKernel matrix={value?.classical_reference?.kernel} name="기술자 RBF · 고전 계산" /></div><div className="qj-comparison-detail"><div><span className="qj-small-label">{matrix ? `M${selectedPair[0] + 1} × M${selectedPair[1] + 1}` : "입력 쌍 미선택"}</span><strong>{quantumNumber(matrix?.[selectedPair[0]]?.[selectedPair[1]])}</strong><p>{projected && selectedPair[0] === selectedPair[1] ? "대각선 1은 RBF 정의값입니다." : "선택한 두 입력의 비교값입니다."}</p></div><div><span className="qj-small-label">별도 반복 측정의 커널</span><strong>{quantumNumber(value?.controls?.duplicate?.kernel_to_original)}</strong><p>독립 반복으로 얻은 계산 재현성 대조입니다.</p></div><div><span className="qj-small-label">해석</span><p>유사도는 입력 특징의 가까움을 나타냅니다. 결합 친화도나 약효 점수로 읽지 않습니다.</p></div></div>{projected && <div className="qj-equation">양자 관측값 r → 거리 D = Σ(rᵢ − rⱼ)² / 2Q → 고전 변환 K = exp(−γD)</div>}</>}
      </motion.div></AnimatePresence>
      <div className="qj-caption"><CircleHelp size={14} /><p>{caption}</p></div>
      <footer className="qj-navigation"><button onClick={() => go(stage - 1)} disabled={stage === 0}><ChevronLeft size={15} />이전 단계</button><span>{stage + 1} / 5 · 설명 단계</span><button onClick={() => go(stage + 1)} disabled={stage === 4}>다음 단계<ChevronRight size={15} /></button></footer>
    </div>
    <details className="qj-progress"><summary><Check size={14} />실제 실행 기록은 어디까지 있나요?<span>{statusNames[value?.status || ""] || value?.status || "기록 미선택"}</span></summary><div>{progress.map(p => <article key={p.id} data-state={p.state}><strong>{p.label}</strong><span>{p.state === "available" ? "기록 있음" : p.state === "pending" ? "대기 중" : p.state === "error" ? "상태 확인 필요" : "미기록"}</span><p>{p.detail}</p></article>)}</div><p>위 설명 재생의 위치는 실제 IBM 작업 진행률이 아닙니다. 작업 상태와 수치의 존재를 따로 확인합니다.</p></details>
    <details className="qj-reference"><summary>그림의 참고 문헌과 이 프로그램의 방식<BookOpen size={13} /></summary><p><a href="https://doi.org/10.1038/s41587-024-02526-3" target="_blank" rel="noreferrer">Vakili et al., Nature Biotechnology (2025)</a>의 Fig. 1 및 Extended Data Figs. 3, 5에서 단계 구성과 회로 확대 방식을 참고했습니다. 이 화면의 도식은 HerbFold의 저장 기록과 게이트 정의로 새로 그렸습니다. 해당 논문의 QCBM–LSTM 생성 모델이나 실험 결과를 재현한 화면은 아닙니다.</p></details>
  </section>;
}
