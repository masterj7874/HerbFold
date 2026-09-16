import type { Compound } from "../types/app";

// Refreshing the small built-in catalog must retain imported and selected DB rows.
export function mergeStudioCatalog(existing: Compound[], incoming: Compound[]): Compound[] {
  const compounds = new Map(existing.map((compound) => [compound.id, compound]));
  for (const compound of incoming) compounds.set(compound.id, compound);
  return [...compounds.values()];
}
