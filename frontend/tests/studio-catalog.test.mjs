import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/studioCatalog.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { mergeStudioCatalog } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

test("refreshing the built-in catalog retains selected DB and custom structures", () => {
  const db = { id: "discovery_123", smiles: "CCO", category: "natural_product", sources: [{ source_id: "lotus-2026-04" }] };
  const custom = { id: "custom_test", smiles: "CCN", category: "drug" };
  const base = { id: "aspirin", smiles: "fixture", category: "drug" };
  const existing = [base, db, custom];
  const refreshed = { ...base, name: "Refreshed name" };
  const merged = mergeStudioCatalog(existing, [refreshed]);
  const selectedIds = [db.id, base.id];
  assert.deepEqual(merged.filter((compound) => selectedIds.includes(compound.id)), [refreshed, db]);
  assert.equal(merged.find((compound) => compound.id === custom.id), custom);
  assert.deepEqual(existing, [base, db, custom]);
});

test("revisiting a DB identity updates its context without duplicating the analysis input", () => {
  const natural = { id: "discovery_9", smiles: "CCO", category: "natural_product" };
  const drug = { ...natural, category: "drug", source_kinds: ["natural_product", "drug"] };
  assert.deepEqual(mergeStudioCatalog([natural], [drug]), [drug]);
  assert.deepEqual(mergeStudioCatalog([], [natural, drug]), [drug]);
});
