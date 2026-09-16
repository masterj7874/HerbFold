import type { QuantumResult } from "../types/quantum";

export function quantumNumber(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  if (Math.abs(value) < 0.0001 || Math.abs(value) >= 1e6) return value.toExponential(4);
  return Number(value.toPrecision(6)).toString();
}

export function validKernel(value: unknown): value is number[][] {
  return Array.isArray(value) && value.length > 0 && value.every(row => Array.isArray(row)
    && row.length === value.length && row.every(cell => typeof cell === "number" && Number.isFinite(cell)));
}

export function quantumMethod(value: QuantumResult) {
  return value.kernel_method || value.plan?.kernel_method || value.metadata?.kernel_method || "fidelity";
}

export function quantumOrigin(value: QuantumResult) {
  const mode = value.mode || value.plan?.mode || value.metadata?.mode;
  if (value.hardware_executed === true && mode === "ibm") return "hardware";
  if (mode === "local" && value.hardware_executed !== true) return "local";
  return "unverified";
}

export function globalKernelCollapsed(value: QuantumResult) {
  if (quantumMethod(value) !== "fidelity" || quantumOrigin(value) !== "hardware" || !validKernel(value.kernel)) return false;
  return value.kernel.every((row, i) => row[i] === 0);
}

export function pairObservation(value: QuantumResult, a: number, b: number) {
  return value.jobs?.flatMap(job => job.observations || []).find(row => row.pair
    && ((row.pair[0] === a && row.pair[1] === b) || (row.pair[0] === b && row.pair[1] === a)));
}

export function readQuantumSelection(key: string): string | null {
  try { const value = sessionStorage.getItem(`herbfold.quantum.${key}`); return value && /^[a-f0-9]{32}$/.test(value) ? value : null; } catch { return null; }
}

export function saveQuantumSelection(key: string, id: string) {
  try { sessionStorage.setItem(`herbfold.quantum.${key}`, id); } catch { /* UI preferences are optional. */ }
}
