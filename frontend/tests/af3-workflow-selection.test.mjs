import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/af3WorkflowSelection.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { trustedAF3WorkflowResult, rememberAF3Workflow, rememberedAF3Workflow } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const completed = () => ({
  id: "workflow-a", status: "completed", job_id: "job-a",
  request: { canonical_smiles: "CCO", target_accession: "P35354", msa_mode: "search", seeds: [1, 42] },
  prediction: {
    job: { id: "job-a", status: "completed", result: { execution_verified: true } },
    requested: { canonical_smiles: "CCO", target_accession: "P35354", msa_mode: "search", seeds: [1, 42] },
    output_validation: { identity_verified: true, status: "geometry_warning", quality_pass: false },
  },
});

test("verified output handoff preserves the same job, ligand and target", () => {
  assert.equal(trustedAF3WorkflowResult(completed()), true);
  const reordered = completed(); reordered.request.seeds.reverse(); assert.equal(trustedAF3WorkflowResult(reordered), true);
  for (const mutate of [
    value => { value.job_id = "job-b"; },
    value => { value.request.canonical_smiles = "CCN"; },
    value => { value.request.target_accession = "P12345"; },
    value => { value.request.canonical_smiles = ""; },
    value => { value.request.msa_mode = "none"; },
    value => { value.request.seeds = [2]; },
    value => { value.request.seeds = []; },
  ]) { const value = completed(); mutate(value); assert.equal(trustedAF3WorkflowResult(value), false); }
});

test("request and job completion alone cannot authorize an unverified result", () => {
  for (const mutate of [
    value => { value.status = "running"; },
    value => { value.status = "validating"; },
    value => { value.status = "postprocessing"; },
    value => { value.prediction.job.status = "failed"; },
    value => { value.prediction.job.result.execution_verified = false; },
    value => { value.prediction.job.result.execution_verified = "true"; },
    value => { value.prediction.output_validation.identity_verified = false; },
    value => { value.prediction.output_validation.identity_verified = "true"; },
    value => { value.prediction.output_validation = null; },
    value => { value.prediction = null; },
  ]) { const value = completed(); mutate(value); assert.equal(trustedAF3WorkflowResult(value), false); }
  assert.equal(trustedAF3WorkflowResult(null), false);
});

test("selection persistence is optional and rejects invalid stored identifiers", () => {
  const prior = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  try {
    let stored = null;
    Object.defineProperty(globalThis, "localStorage", { configurable: true, value: { getItem: () => stored, setItem: (_key, value) => { stored = value; } } });
    rememberAF3Workflow("workflow-a");
    assert.equal(rememberedAF3Workflow(), "workflow-a");
    stored = "../../different-resource";
    assert.equal(rememberedAF3Workflow(), null);
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("storage unavailable"); } });
    assert.equal(rememberedAF3Workflow(), null);
    assert.doesNotThrow(() => rememberAF3Workflow("workflow-a"));
  } finally { if (prior) Object.defineProperty(globalThis, "localStorage", prior); else delete globalThis.localStorage; }
});
