/** Persist only request identity and research inputs, never API credentials. */
export type AF3LaunchRequest = {
  request_id: string;
  compound: { id: string; name: string; smiles: string; category?: "herbal" | "natural_product" | "drug" | "candidate" };
  target_accession: string;
  msa_mode: "search" | "none";
  seeds: number[];
  exploratory_ack: boolean;
  execute: boolean;
};
const PENDING_KEY = "herbfold:af3-workflow:pending";

export function newRequestId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const h = [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export function readPendingAF3(): AF3LaunchRequest | null {
  try {
    const value = JSON.parse(localStorage.getItem(PENDING_KEY) || "null");
    return value && typeof value.request_id === "string" && typeof value.compound?.smiles === "string"
      && typeof value.target_accession === "string" && Array.isArray(value.seeds) ? value : null;
  } catch { return null; }
}
export function savePendingAF3(value: AF3LaunchRequest): boolean {
  try { localStorage.setItem(PENDING_KEY, JSON.stringify(value)); return true; } catch { return false; }
}
export function clearPendingAF3(requestId: string) {
  try { if (readPendingAF3()?.request_id === requestId) localStorage.removeItem(PENDING_KEY); } catch { /* Storage may be restricted. */ }
}
