import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
}
const { comparisonInputKey } = await load("../src/lib/comparisonSelection.ts");
const { workspaceFromHash } = await load("../src/lib/workspaceNavigation.ts");

test("comparison results remain current across reordering but not changed inputs", () => {
  const a = { id: "a", smiles: "CCO", name: "A" };
  const b = { id: "b", smiles: "CCN", name: "B" };
  const key = comparisonInputKey([a, b]);
  assert.equal(comparisonInputKey([b, a]), key);
  assert.notEqual(comparisonInputKey([a]), key);
  assert.notEqual(comparisonInputKey([a, { ...b, smiles: "CCC" }]), key);
  assert.notEqual(comparisonInputKey([a, { ...b, name: "Renamed" }]), key);
});

test("comparison has a separate direct route and the primary route remains AlphaFold", () => {
  assert.equal(workspaceFromHash("#comparison"), "comparison");
  assert.equal(workspaceFromHash("#/comparison"), "comparison");
  assert.equal(workspaceFromHash(""), "alphafold");
  assert.equal(workspaceFromHash("#studio"), "alphafold");
});
