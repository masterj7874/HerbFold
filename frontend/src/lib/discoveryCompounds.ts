import type { Compound } from "../types/app";

export type DiscoveryKind = "natural_product" | "drug";
export type DiscoveryProvenance = {
  source_id?: string;
  source?: string;
  source_name?: string;
  source_url?: string;
  url?: string;
  kind?: string;
  source_kind?: string;
  [key: string]: unknown;
};
export type DiscoveryCompound = {
  id: string | number;
  canonical_smiles: string;
  display_name?: string;
  name?: string;
  sources?: DiscoveryProvenance[];
  provenance?: DiscoveryProvenance[];
  source_kinds?: string[];
  descriptors?: Record<string, unknown>;
  [key: string]: unknown;
};

// Only reviewed source identifiers establish a category on older API responses.
const SOURCE_KINDS: Record<string, DiscoveryKind> = {
  "coconut-2026-09": "natural_product",
  "lotus-2026-04": "natural_product",
  "chembl-approved": "drug",
};

function isKind(value: unknown): value is DiscoveryKind {
  return value === "natural_product" || value === "drug";
}

function externalUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}

/** Preserve database identity and source records without inferring herbal use. */
export function normalizeDiscoveryCompound(
  row: DiscoveryCompound,
  context: { kind?: DiscoveryKind | ""; source?: string } = {},
): Compound {
  const provenance = row.provenance?.length ? row.provenance : row.sources ?? [];
  const records = [...(row.sources ?? []), ...(row.provenance ?? [])];
  // The API aggregates kinds over every source record, beyond its ten-row preview.
  const kinds = new Set<DiscoveryKind>((row.source_kinds ?? []).filter(isKind));
  if (!kinds.size) {
    for (const record of records) {
      const kind = record.source_kind ?? record.kind ?? SOURCE_KINDS[record.source_id ?? record.source ?? ""];
      if (isKind(kind)) kinds.add(kind);
    }
  }
  const preferredKind = context.kind || SOURCE_KINDS[context.source ?? ""];
  const kind = preferredKind && kinds.has(preferredKind) ? preferredKind
    : kinds.has("natural_product") ? "natural_product" : kinds.has("drug") ? "drug" : undefined;
  const source = records.find((record) => (record.source_id ?? record.source) === context.source && externalUrl(record.source_url ?? record.url))
    ?? records.find((record) => externalUrl(record.source_url ?? record.url));
  const id = String(row.id);
  const fullName = row.display_name?.trim() || row.name?.trim() || `구조 ${row.id}`;
  const alternateName = row.name?.trim();
  const name = fullName.length <= 200 ? fullName
    : alternateName && alternateName.length <= 200 ? alternateName
    : `${fullName.slice(0, 199).replace(/[\uD800-\uDBFF]$/, "")}…`;
  return {
    ...row,
    id: id.startsWith("discovery_") ? id : `discovery_${id}`,
    name,
    display_name: row.display_name || row.name || fullName,
    smiles: row.canonical_smiles,
    category: kind ?? "candidate",
    generated: false,
    provenance,
    source_kinds: [...kinds],
    source_url: externalUrl(source?.source_url ?? source?.url),
    source_classification: kind === "drug" ? "drug_reference_record"
      : kind === "natural_product" ? "natural_product_record" : "unclassified_reference_record",
    source_scope: kinds.size > 1 ? "Recorded by both natural-product and drug-reference sources; herbal occurrence, efficacy and current marketing authorization are not inferred"
      : kind === "drug" ? "Drug reference record; approved history does not establish current marketing authorization"
      : kind === "natural_product" ? "Source-reported natural product; specific herbal occurrence and efficacy are not inferred"
      : "Database reference record; natural occurrence, herbal use and drug status are not established",
    herbal_association_verified: false,
  };
}
