// Isolated normalization fixtures; these are never used as scientific records.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
const source = await readFile(new URL("../src/lib/discoveryCompounds.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { normalizeDiscoveryCompound } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

test("database identity, canonical structure and provenance survive selection", () => {
  const row = { id: 23, display_name: " Example ", canonical_smiles: "C[C@H](O)C(=O)O", sources: [{ source_id: "lotus-2026-04", external_id: "fixture-23", organism: "fixture taxon", source_url: "https://example.org/23", metadata: { citation: "fixture" } }], descriptors: { molecular_weight: 90.08 } };
  const result = normalizeDiscoveryCompound(row);
  assert.equal(result.id, "discovery_23");
  assert.equal(result.name, "Example");
  assert.equal(result.smiles, row.canonical_smiles);
  assert.deepEqual(result.sources, row.sources);
  assert.deepEqual(result.provenance, row.sources);
  assert.deepEqual(result.descriptors, row.descriptors);
  assert.equal(result.source_url, "https://example.org/23");
  assert.equal(result.category, "natural_product");
  assert.equal(result.herbal_association_verified, false);
  assert.equal(result.generated, false);
  assert.equal(row.id, 23);
  assert.equal(normalizeDiscoveryCompound({ ...row, id: result.id }).id, result.id);
});

test("aggregate source kinds cover provenance omitted from the preview", () => {
  const row = { id: "mixed", canonical_smiles: "CCO", source_kinds: ["natural_product", "drug"], sources: Array.from({ length: 10 }, (_, index) => ({ source_id: "lotus-2026-04", external_id: String(index) })) };
  const natural = normalizeDiscoveryCompound(row, { kind: "natural_product" });
  const drug = normalizeDiscoveryCompound(row, { kind: "drug" });
  assert.equal(natural.category, "natural_product");
  assert.equal(drug.category, "drug");
  assert.equal(drug.id, natural.id);
  assert.equal(drug.smiles, natural.smiles);
  assert.equal(drug.source_classification, "drug_reference_record");
  assert.equal(normalizeDiscoveryCompound(row, { source: "chembl-approved" }).category, "drug");
  assert.deepEqual(drug.source_kinds, ["natural_product", "drug"]);
  assert.match(drug.source_scope, /both natural-product and drug-reference/);
});

test("unknown and reference-only records do not become natural products or herbal records", () => {
  for (const sources of [[], [{ source_id: "pubchem" }], [{ source_id: "unknown" }], [{ source_id: "imppat" }]]) {
    const result = normalizeDiscoveryCompound({ id: "unknown", canonical_smiles: "CO", sources }, { kind: "natural_product", source: "lotus-2026-04" });
    assert.equal(result.category, "candidate");
    assert.equal(result.source_classification, "unclassified_reference_record");
    assert.equal(result.generated, false);
    assert.equal(result.herbal_association_verified, false);
  }
});

test("older source records classify conservatively and context cannot override conflicting evidence", () => {
  const row = { id: "drug", canonical_smiles: "CN", provenance: [{ source: "chembl-approved", source_url: "https://example.org/drug" }] };
  const result = normalizeDiscoveryCompound(row, { kind: "natural_product" });
  assert.equal(result.category, "drug");
  assert.deepEqual(result.provenance, row.provenance);
  assert.match(result.source_scope, /does not establish current marketing authorization/);
  assert.equal(normalizeDiscoveryCompound({ ...row, source_kinds: ["natural_product"] }).category, "natural_product");
  assert.equal(normalizeDiscoveryCompound({ ...row, provenance: [{ source_id: "new-reviewed-source", source_kind: "drug" }] }).category, "drug");
});

test("selected provenance wins for links, while non-web URLs are rejected", () => {
  const row = { id: "urls", canonical_smiles: "CC", sources: [{ source_id: "lotus-2026-04", source_url: "javascript:alert(1)" }, { source_id: "coconut-2026-09", source_url: "https://example.org/natural" }, { source_id: "chembl-approved", url: "https://example.org/drug" }] };
  const result = normalizeDiscoveryCompound(row, { source: "chembl-approved" });
  assert.equal(result.source_url, "https://example.org/drug");
  assert.equal(result.category, "drug");
  assert.equal(normalizeDiscoveryCompound({ ...row, sources: row.sources.slice(0, 1) }).source_url, undefined);
  assert.equal(normalizeDiscoveryCompound(row, { source: "lotus-2026-04" }).source_url, "https://example.org/natural");
});

test("submitted names fit the analysis bound without losing long source labels or chemical structure", () => {
  const fullName = "x".repeat(500);
  const row = { id: "long-name", display_name: fullName, canonical_smiles: "CCO", sources: [{ source_id: "lotus-2026-04", name: fullName }] };
  const result = normalizeDiscoveryCompound(row);
  assert.equal(result.name.length, 200);
  assert.ok(result.name.endsWith("…"));
  assert.equal(result.display_name, fullName);
  assert.equal(result.sources[0].name, fullName);
  assert.equal(result.smiles, row.canonical_smiles);
  assert.equal(normalizeDiscoveryCompound({ ...row, name: "Short source name" }).name, "Short source name");
  assert.ok(normalizeDiscoveryCompound({ ...row, display_name: "💧".repeat(210) }).name.length <= 200);
});
