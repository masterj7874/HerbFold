import type { Compound } from "../types/app";

export function comparisonInputKey(compounds: Compound[]): string {
  return JSON.stringify(compounds.map(({ id, smiles, name }) => [id, smiles, name])
    .sort((left, right) => left[0].localeCompare(right[0])));
}
