import type { QuantumResult } from "../types/quantum";

export type QuantumBlock = { logical_qubits: number[]; edges: [number, number][] };
export type QuantumGateAngles = { logical: number; ry: number; rz: number };
export type QuantumProgressStage = {
  id: "descriptors" | "encoding" | "circuit" | "measurement" | "comparison";
  label: string;
  state: "available" | "pending" | "unavailable" | "error";
  detail: string;
  inputCount?: number;
};

const record = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
const integer = (value: unknown, lower = 0, upper = 100000): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= lower && value <= upper;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const finiteRows = (value: unknown): value is number[][] => Array.isArray(value) && value.length > 0
  && Array.isArray(value[0]) && value[0].length > 0
  && value.every(row => Array.isArray(row) && row.length === value[0].length && row.every(finite));
const square = (value: unknown): value is number[][] => finiteRows(value) && value.every(row => row.length === value.length);
const planOf = (value: QuantumResult | null | undefined): Record<string, unknown> | null =>
  record(value?.plan) || record(value?.metadata);

function blockOf(value: unknown, width: number): QuantumBlock | null {
  const candidate = record(value);
  if (!candidate || !Array.isArray(candidate.logical_qubits) || candidate.logical_qubits.length === 0
    || !candidate.logical_qubits.every(q => integer(q, 0, width - 1)) || !Array.isArray(candidate.edges)) return null;
  const nodes = candidate.logical_qubits as number[];
  if (new Set(nodes).size !== nodes.length) return null;
  const edges: [number, number][] = [], seen = new Set<string>();
  for (const edge of candidate.edges) {
    if (!Array.isArray(edge) || edge.length !== 2 || !edge.every(q => integer(q, 0, width - 1))
      || edge[0] === edge[1] || !nodes.includes(edge[0]) || !nodes.includes(edge[1])) return null;
    const key = `${Math.min(edge[0], edge[1])}:${Math.max(edge[0], edge[1])}`;
    if (seen.has(key)) return null;
    seen.add(key);
    edges.push([edge[0], edge[1]]);
  }
  return { logical_qubits: [...nodes], edges };
}

/** Return only a complete, internally consistent archived partition; never invent edges. */
export function readBlocks(value: QuantumResult | null | undefined): QuantumBlock[] {
  const plan = planOf(value);
  if (!plan || !integer(plan.n_qubits, 1) || !Array.isArray(plan.blocks) || !plan.blocks.length) return [];
  const blocks: QuantumBlock[] = [], used = new Set<number>();
  for (const candidate of plan.blocks) {
    const block = blockOf(candidate, plan.n_qubits);
    if (!block || block.logical_qubits.some(q => used.has(q))) return [];
    block.logical_qubits.forEach(q => used.add(q));
    blocks.push(block);
  }
  return used.size === plan.n_qubits ? blocks : [];
}

/** Backend atan_fold_or_repeat_v1; inputs have already undergone any saved descriptor scaling. */
export function encodingAngles(features: number[], nQubits: number): number[] {
  if (!Array.isArray(features) || !features.length || !features.every(finite) || !integer(nQubits, 1)) return [];
  const bounded = features.map(value => 2 * Math.atan(value));
  return Array.from({ length: nQubits }, (_, q) => {
    if (features.length < nQubits) return bounded[q % features.length];
    let sum = 0, count = 0;
    for (let i = q; i < bounded.length; i += nQubits) { sum += bounded[i]; count++; }
    return sum / count;
  });
}

/** Exact block-local RY/RZ indices in build_feature_map; layer is zero based. */
export function gateAngles(angles: number[], block: QuantumBlock, layer: number): QuantumGateAngles[] {
  if (!Array.isArray(angles) || !angles.length || !angles.every(finite) || !integer(layer, 0, 7)) return [];
  const checked = blockOf(block, angles.length);
  if (!checked) return [];
  const nodes = checked.logical_qubits, width = nodes.length;
  return nodes.map((logical, i) => ({
    logical,
    ry: angles[nodes[(i + layer) % width]],
    rz: angles[nodes[(i + 2 * layer + 1) % width]] / 2,
  }));
}

/** Raw separate-axis expectations. A norm above one is retained, not physically projected. */
export function readBloch(value: QuantumResult | null | undefined, sample: number, logical: number): {
  x: number; y: number; z: number; norm: number;
} | null {
  if (!integer(sample) || !integer(logical)) return null;
  const features = value?.projected_features, axes = features?.axes, values = features?.values;
  if (!Array.isArray(axes) || axes.length !== 3 || new Set(axes).size !== 3
    || !["X", "Y", "Z"].every(axis => axes.includes(axis)) || !Array.isArray(values)
    || !Array.isArray(values[sample]) || !Array.isArray(values[sample][logical])) return null;
  const row = values[sample][logical];
  if (row.length !== 3 || !row.every(item => finite(item) && item >= -1 && item <= 1)) return null;
  const x = row[axes.indexOf("X")], y = row[axes.indexOf("Y")], z = row[axes.indexOf("Z")];
  return { x, y, z, norm: Math.hypot(x, y, z) };
}

/** Evidence availability by stage, not elapsed progress, a percentage, or a live state trajectory. */
export function quantumProgress(value: QuantumResult | null | undefined): QuantumProgressStage[] {
  const root = record(value), plan = planOf(value), status = value?.status || "";
  const active = ["submitted", "submitting", "running", "queued", "prepared", "ready"].includes(status);
  const failed = ["failed", "submission_failed", "partial_submission", "cancelled", "interrupted", "blocked"].includes(status);
  const missing: QuantumProgressStage["state"] = failed ? "error" : active ? "pending" : "unavailable";
  const rawFeatures = root?.features ?? value?.feature_definition?.features;
  const hasRows = finiteRows(rawFeatures);
  const hasEncoding = hasRows && plan?.encoding === "atan_fold_or_repeat_v1" && integer(plan?.n_qubits, 1);
  const compiled = record(value?.compiled);
  const hasCompiled = !!compiled && (integer(compiled.depth) || ["X", "Y", "Z"].some(axis => integer(record(compiled[axis])?.depth)));
  const hasCircuit = hasCompiled || readBlocks(value).length > 0;
  const mode = value?.mode || plan?.mode;
  const local = mode === "local" && value?.hardware_executed !== true;
  const hardware = mode === "ibm" && value?.hardware_executed === true;
  const complete = status === "completed";
  const validOutput = square(value?.kernel);
  const hasObservables = !!readBloch(value, 0, 0);
  const observations = value?.jobs?.flatMap(job => Array.isArray(job.observations) ? job.observations : []) || [];
  const hasShots = observations.some(observation => integer(observation.shots, 1));
  const measured = complete && (hardware || local) && (validOutput || hasObservables || (hardware && hasShots));
  const compared = complete && (hardware || local) && validOutput;
  return [
    { id: "descriptors", label: "분자 특징", state: hasRows ? "available" : missing,
      inputCount: hasRows ? rawFeatures.length : undefined,
      detail: hasRows ? `${rawFeatures.length}개 입력의 저장된 특징 행렬` : "입력 특징 행렬을 확인할 수 없습니다." },
    { id: "encoding", label: "각도 인코딩", state: hasEncoding ? "available" : missing,
      detail: hasEncoding ? "저장된 입력과 atan 인코딩 규칙으로 각도를 확인합니다." : "입력·큐빗 수·인코딩 규칙이 모두 필요합니다." },
    { id: "circuit", label: "회로 구성", state: hasCircuit ? "available" : missing,
      detail: hasCompiled ? "저장된 컴파일 회로 정보가 있습니다." : hasCircuit ? "저장된 블록 연결 계획이 있습니다." : "저장된 회로·연결 정보를 확인할 수 없습니다." },
    { id: "measurement", label: "관측값", state: measured ? "available" : missing,
      detail: measured ? local ? "로컬 이상적 계산 결과입니다." : "완료된 IBM 실행의 관측 결과입니다."
        : hasShots ? "일부 측정 기록이 있지만 전체 실행 완료는 확인되지 않았습니다."
          : active ? "기록된 실행 상태를 기다리는 중입니다. 측정값은 아직 없습니다." : "완료된 실측·로컬 계산 근거가 없습니다." },
    { id: "comparison", label: "커널 비교", state: compared ? "available" : missing,
      detail: compared ? "선택한 실행의 유효한 커널 행렬이 있습니다." : "완료 상태·계산 출처·전체 커널 행렬이 필요합니다." },
  ];
}
