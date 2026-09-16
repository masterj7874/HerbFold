import { useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { motion } from "motion/react";
import { Move, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
import { quantumNumber } from "../lib/quantumEvidence";
import { useMotionPreference } from "../lib/useMotionPreference";
import "./quantum-bloch.css";

export type QuantumBlochSphereProps = {
  vector: { x: number; y: number; z: number; norm: number } | null;
  label: string;
  logical: number;
  physical: number | null;
  reducedMotion?: boolean;
};

type Point = { x: number; y: number; z: number };
const home = { yaw: 0.65, pitch: 0.3, zoom: 1 };
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
const display = quantumNumber;
const colors = { X: "#00799a", Y: "#138165", Z: "#7653ae" };

/** The unit sphere is a reference, not a reconstruction of a many-qubit state. */
export default function QuantumBlochSphere({ vector, label, logical, physical, reducedMotion = false }: QuantumBlochSphereProps) {
  const systemReducedMotion = useMotionPreference();
  const still = reducedMotion || systemReducedMotion;
  const [view, setView] = useState(home);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ id: number; x: number; y: number } | null>(null);
  const namespace = `qbs-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const titleId = `${namespace}-title`;
  const detailId = `${namespace}-detail`;
  const data = vector && [vector.x, vector.y, vector.z, vector.norm].every(Number.isFinite) ? vector : null;
  // Raw coordinates are preserved even when their estimated norm exceeds one.
  const norm = data ? Math.hypot(data.x, data.y, data.z) : null;
  const outside = norm !== null && norm > 1 + 1e-10;
  const radius = 80 * view.zoom;
  const center = { x: 160, y: 140 };
  const project = (point: Point) => {
    const x = point.x * Math.cos(view.yaw) + point.z * Math.sin(view.yaw);
    const yawDepth = -point.x * Math.sin(view.yaw) + point.z * Math.cos(view.yaw);
    const y = point.y * Math.cos(view.pitch) - yawDepth * Math.sin(view.pitch);
    const depth = point.y * Math.sin(view.pitch) + yawDepth * Math.cos(view.pitch);
    return { x: center.x + radius * x, y: center.y - radius * y, depth };
  };
  const circles = useMemo(() => ["XY", "XZ", "YZ"].map(plane => Array.from({ length: 65 }, (_, i) => {
    const angle = i / 64 * Math.PI * 2;
    return { x: plane === "YZ" ? 0 : Math.cos(angle), y: plane === "XZ" ? 0 : plane === "XY" ? Math.sin(angle) : Math.cos(angle), z: plane === "XY" ? 0 : Math.sin(angle) };
  })), []);
  const axes = (["X", "Y", "Z"] as const).map((axis, index) => {
    const unit = { x: Number(index === 0), y: Number(index === 1), z: Number(index === 2) };
    return { axis, negative: project({ x: -1.13 * unit.x, y: -1.13 * unit.y, z: -1.13 * unit.z }), positive: project({ x: 1.18 * unit.x, y: 1.18 * unit.y, z: 1.18 * unit.z }), text: project({ x: 1.36 * unit.x, y: 1.36 * unit.y, z: 1.36 * unit.z }) };
  });
  const tip = data ? project(data) : null;
  const changeZoom = (amount: number) => setView(previous => ({ ...previous, zoom: clamp(previous.zoom + amount, .55, 1.65) }));
  const reset = () => setView(home);
  const keyboard = (event: KeyboardEvent<SVGSVGElement>) => {
    const turn = event.shiftKey ? .2 : .09;
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      setView(previous => ({ ...previous,
        yaw: previous.yaw + (event.key === "ArrowLeft" ? -turn : event.key === "ArrowRight" ? turn : 0),
        pitch: clamp(previous.pitch + (event.key === "ArrowUp" ? turn : event.key === "ArrowDown" ? -turn : 0), -1.5, 1.5),
      }));
    } else if (event.key === "+" || event.key === "=") { event.preventDefault(); changeZoom(.15); }
    else if (event.key === "-" || event.key === "_") { event.preventDefault(); changeZoom(-.15); }
    else if (event.key === "0" || event.key === "Home") { event.preventDefault(); reset(); }
    else if (event.key === "Escape" && drag.current) {
      if (event.currentTarget.hasPointerCapture(drag.current.id)) event.currentTarget.releasePointerCapture(drag.current.id);
      drag.current = null; setDragging(false);
    }
  };
  const pointerDown = (event: PointerEvent<SVGSVGElement>) => {
    if (!event.isPrimary || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
    setDragging(true);
  };
  const pointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const previous = drag.current;
    if (!previous || previous.id !== event.pointerId) return;
    const dx = event.clientX - previous.x, dy = event.clientY - previous.y;
    drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
    setView(current => ({ ...current, yaw: current.yaw + dx * .009, pitch: clamp(current.pitch + dy * .009, -1.5, 1.5) }));
  };
  const pointerEnd = (event: PointerEvent<SVGSVGElement>) => {
    if (drag.current?.id !== event.pointerId) return;
    drag.current = null; setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const description = data
    ? `${label}, 논리 큐빗 ${logical}${physical === null ? ", 물리 큐빗 미기록" : `, 물리 큐빗 ${physical}`}. 원시 기대값 X ${display(data.x)}, Y ${display(data.y)}, Z ${display(data.z)}. 벡터 길이 ${display(norm!)}. 단위 구는 길이 1의 참고 경계이며 전체 양자 상태를 나타내지 않습니다.`
    : `${label}, 논리 큐빗 ${logical}. 원시 X, Y, Z 기대값이 기록되지 않아 벡터를 표시하지 않습니다.`;
  return <figure className="qbs-figure" data-testid="quantum-bloch-sphere" data-has-vector={!!data} data-vector-norm={norm ?? ""}>
    <figcaption className="qbs-heading"><div><span>LOCAL OBSERVABLES</span><strong id={titleId}>큐빗의 X · Y · Z 기대값</strong></div><span className="qbs-qubit">q{logical}<small>{physical === null ? "물리 번호 미기록" : `물리 q${physical}`}</small></span></figcaption>
    <div className="qbs-canvas-wrap">
      <svg className={`qbs-canvas${dragging ? " is-dragging" : ""}`} viewBox="0 0 320 300" role="group" tabIndex={0}
        aria-label="기대값 벡터 회전·확대 보기" aria-describedby={detailId} aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown + - 0 Home"
        onKeyDown={keyboard} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerEnd} onPointerCancel={pointerEnd}
        onLostPointerCapture={() => { drag.current = null; setDragging(false); }}
        data-testid="quantum-bloch-canvas" data-yaw={view.yaw.toFixed(3)} data-pitch={view.pitch.toFixed(3)} data-zoom={view.zoom.toFixed(2)}>
        <title>{description}</title>
        <defs>
          <radialGradient id={`${namespace}-surface`} cx="35%" cy="28%"><stop offset="0%" stopColor="#ffffff" /><stop offset="68%" stopColor="#e7f6fa" stopOpacity=".55" /><stop offset="100%" stopColor="#d7e9f3" stopOpacity=".75" /></radialGradient>
          <linearGradient id={`${namespace}-vector`} x1="0" y1="1" x2="1" y2="0"><stop stopColor="#086d88" /><stop offset="1" stopColor="#24a58b" /></linearGradient>
          <marker id={`${namespace}-arrow`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 Z" fill="#087e83" /></marker>
        </defs>
        <circle cx={center.x} cy={center.y} r={radius} fill={`url(#${namespace}-surface)`} stroke="#a9c3d8" strokeWidth="1.1" />
        {circles.flatMap((points, circle) => points.slice(0, -1).map((point, i) => {
          const a = project(point), b = project(points[i + 1]);
          return <line key={`${circle}-${i}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#698dad" strokeWidth=".9" opacity={(a.depth + b.depth) / 2 >= 0 ? .44 : .18} />;
        }))}
        {axes.map(({ axis, negative, positive, text }) => <g key={axis}>
          <line x1={negative.x} y1={negative.y} x2={center.x} y2={center.y} stroke={colors[axis]} strokeWidth="1" strokeDasharray="3 4" opacity=".5" />
          <line x1={center.x} y1={center.y} x2={positive.x} y2={positive.y} stroke={colors[axis]} strokeWidth="1.4" opacity=".85" />
          <circle cx={positive.x} cy={positive.y} r="2.1" fill={colors[axis]} />
          <text x={text.x} y={text.y} textAnchor="middle" dominantBaseline="central" fill={colors[axis]} fontSize="12" fontWeight="700">{axis}</text>
        </g>)}
        <circle cx={center.x} cy={center.y} r="3" fill="#315f7e" />
        {tip && data && <motion.g key={`${logical}-${data.x}-${data.y}-${data.z}`} initial={still ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: still ? 0 : .28 }} data-testid="quantum-bloch-raw-vector">
          <line x1={center.x} y1={center.y} x2={tip.x} y2={tip.y} stroke={`url(#${namespace}-vector)`} strokeWidth="3.2" strokeLinecap="round" markerEnd={norm! > .035 ? `url(#${namespace}-arrow)` : undefined} />
          <circle cx={tip.x} cy={tip.y} r="7" fill="#1d9d91" opacity=".12" />
          <circle cx={tip.x} cy={tip.y} r="3.6" fill="#007f83" stroke="#fff" strokeWidth="1.3" />
        </motion.g>}
        {!data && <g><rect x="65" y="122" width="190" height="38" rx="8" fill="#ffffff" fillOpacity=".95" stroke="#d6e1eb" /><text x="160" y="145" textAnchor="middle" fill="#607489" fontSize="12">기대값 미기록 · 벡터 없음</text></g>}
        <text x="160" y="276" textAnchor="middle" fill="#6a8196" fontSize="10">단위 구: |r| = 1 · 원시 좌표 유지</text>
      </svg>
      <div className="qbs-toolbar" aria-label="기대값 벡터 보기 조절"><span><Move size={13} aria-hidden="true" />드래그하여 회전</span><div>
        <button type="button" onClick={() => changeZoom(-.15)} disabled={view.zoom <= .551} aria-label="기대값 구 축소" title="축소 (−)"><ZoomOut size={17} aria-hidden="true" /></button>
        <output aria-label="확대 배율">{Math.round(view.zoom * 100)}%</output>
        <button type="button" onClick={() => changeZoom(.15)} disabled={view.zoom >= 1.649} aria-label="기대값 구 확대" title="확대 (+)"><ZoomIn size={17} aria-hidden="true" /></button>
        <button type="button" onClick={reset} aria-label="기대값 구 시점 초기화" title="시점 초기화 (0)"><RotateCcw size={16} aria-hidden="true" /></button>
      </div></div>
    </div>
    <p className="qbs-sample" title={label}>{label}</p>
    <dl className="qbs-values" aria-label="원시 기대값"><div style={{ color: colors.X }}><dt>⟨X⟩</dt><dd>{data ? display(data.x) : "—"}</dd></div><div style={{ color: colors.Y }}><dt>⟨Y⟩</dt><dd>{data ? display(data.y) : "—"}</dd></div><div style={{ color: colors.Z }}><dt>⟨Z⟩</dt><dd>{data ? display(data.z) : "—"}</dd></div><div><dt>|r|</dt><dd>{norm === null ? "—" : display(norm)}</dd></div></dl>
    <p id={detailId} className="qbs-explanation">{data ? "저장된 축별 기대값으로 구성한 벡터입니다. 이 구는 한 큐빗의 참고 좌표계이며 전체 얽힌 상태나 분자 궤도를 나타내지 않습니다." : "이 기록에는 해당 큐빗의 X·Y·Z 기대값이 없습니다. 참고 좌표계만 표시합니다."}</p>
    {outside && <p className="qbs-inconsistent" role="status">|r| &gt; 1: 유한 shots나 장비 오류로 축별 추정값이 단위 구와 일치하지 않을 수 있습니다. 벡터를 정규화하거나 잘라내지 않았습니다.</p>}
    <p className="qbs-keyboard">키보드: 그림에 초점을 맞춘 뒤 방향키로 회전, + / −로 확대·축소, 0으로 초기화합니다.</p>
  </figure>;
}
