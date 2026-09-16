// Synthetic unit fixtures only; these records are never served to the application.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/predictionEvidence.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { comparisonEntry, conditionDifferences, measuredMetric, metricDifference } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

function fixture(mode, overrides = {}) {
  return {
    requested: { msa_mode: mode, canonical_smiles: "synthetic-unit-fixture", target_sequence_sha256: "fixture-sequence", af3_commit: "fixture-code", seeds: [1], execution_profile: { num_diffusion_samples: 5, num_recycles: 10 }, submission_context: { parameter_stat_fingerprint_sha256: "fixture-stat" } },
    job: { id: `fixture-${mode}`, status: "completed", created: "2026-01-01T00:00:00Z", result: { execution_verified: true } },
    output_validation: { identity_verified: true, status: "identity_verified_quality_unassessed", quality_pass: null, summary_metrics: { ptm: 0.2, iptm: 0.37 } },
    ...overrides,
  };
}

test("pending MSA result and absent metrics do not become zero or a measured delta", () => {
  const before = fixture("none");
  const pending = fixture("search", { job: { status: "running", result: { execution_verified: true } } });
  assert.equal(measuredMetric(pending, "ptm"), null);
  assert.equal(metricDifference(before, pending, "ptm"), null);
  assert.equal(measuredMetric(fixture("search", { output_validation: { identity_verified: true } }), "ptm"), null);
});

test("a real zero metric remains zero and decreases are reported without clamping", () => {
  const before = fixture("none");
  const after = fixture("search", { output_validation: { identity_verified: true, summary_metrics: { ptm: 0 } } });
  assert.equal(measuredMetric(after, "ptm"), 0);
  assert.equal(metricDifference(before, after, "ptm"), -0.2);
});

test("unverified execution and identity failure cannot supply comparison confidence", () => {
  const unverified = fixture("search", { job: { status: "completed", result: { execution_verified: false } } });
  const mismatch = fixture("search", { output_validation: { identity_verified: false, summary_metrics: { ptm: 0.99 } } });
  assert.equal(measuredMetric(unverified, "ptm"), null);
  assert.equal(measuredMetric(mismatch, "ptm"), null);
});

test("quality unassessed and geometry warnings preserve measured metrics without certifying accuracy", () => {
  const warning = fixture("search", { output_validation: { identity_verified: true, quality_pass: false, status: "geometry_warning", summary_metrics: { ptm: 0.31 } } });
  assert.equal(measuredMetric(warning, "ptm"), 0.31);
  assert.equal(warning.output_validation.quality_pass, false);
});

test("current selected mode stays explicit and opposite mode favors identity-verified output", () => {
  const current = fixture("none");
  const verified = fixture("search");
  const newerFailed = fixture("search", { job: { id: "fixture-failure", status: "failed", created: "2026-01-02T00:00:00Z" } });
  assert.equal(comparisonEntry([current, verified, newerFailed], current, "none"), current);
  assert.equal(comparisonEntry([current, verified, newerFailed], current, "search"), verified);
  assert.equal(comparisonEntry([current, verified, newerFailed], newerFailed, "search"), newerFailed);
});

test("condition changes and missing provenance are distinguished; seed order is immaterial", () => {
  const before = fixture("none"), after = fixture("search");
  before.requested.seeds = [2, 1]; after.requested.seeds = [1, 2];
  assert.deepEqual(conditionDifferences(before, after), { different: [], unknown: [] });
  after.requested.execution_profile.num_recycles = 3;
  delete after.requested.target_sequence_sha256;
  assert.deepEqual(conditionDifferences(before, after), { different: ["재순환 횟수"], unknown: ["표적 서열"] });
});
