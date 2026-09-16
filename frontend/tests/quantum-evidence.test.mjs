// Isolated unit fixtures, never submitted to a QPU or served as scientific data.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
const source = await readFile(new URL("../src/lib/quantumEvidence.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { quantumNumber, validKernel, quantumOrigin, globalKernelCollapsed, pairObservation } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

test("a real zero, missing measurement and small nonzero values remain distinct", () => {
  assert.equal(quantumNumber(0), "0");
  assert.equal(quantumNumber(-0), "0");
  for (const value of [null, undefined, NaN, Infinity, "0", false]) assert.equal(quantumNumber(value), "—");
  for (const value of [0.000004, -0.00000008, 0.00011, 0.0037374]) {
    assert.notEqual(quantumNumber(value), "0");
    assert.ok(Math.abs(Number(quantumNumber(value)) / value - 1) < 0.00001);
  }
});
test("an incomplete or nonfinite matrix is not a valid measurement matrix", () => {
  assert.equal(validKernel([[0, 0], [0, 0]]), true);
  for (const value of [null, [], [[0, 1], [0]], [[0, null], [0, 0]], [[0, NaN], [0, 0]]]) assert.equal(validKernel(value), false);
});
test("hardware labeling requires a real execution flag and IBM provenance", () => {
  assert.equal(quantumOrigin({ mode: "ibm", hardware_executed: true }), "hardware");
  assert.equal(quantumOrigin({ mode: "ibm", hardware_executed: null }), "unverified");
  assert.equal(quantumOrigin({ hardware_executed: true }), "unverified");
  assert.equal(quantumOrigin({ metadata: { mode: "local" } }), "local");
});
test("zero global self-return warns without misclassifying projected defined diagonals or local states", () => {
  const raw = { mode: "ibm", hardware_executed: true, kernel: [[0, 0], [0, 0]] };
  assert.equal(globalKernelCollapsed(raw), true);
  assert.equal(globalKernelCollapsed({ ...raw, kernel_method: "projected" }), false);
  assert.equal(globalKernelCollapsed({ ...raw, mode: "local", hardware_executed: false }), false);
  assert.equal(globalKernelCollapsed({ ...raw, kernel: [[1, 0], [0, 1]] }), false);
  assert.deepEqual(raw.kernel, [[0, 0], [0, 0]]);
});
test("symmetric displayed cells resolve the exact recorded pair with its original counts and CI", () => {
  const observed = { pair: [0, 1], zero_counts: 0, shots: 128, fidelity: 0, wilson_95: [0, 0.029136956273508416] };
  const value = { jobs: [{ job_id: "isolated-fixture", observations: [observed] }] };
  assert.equal(pairObservation(value, 1, 0), observed);
  assert.equal(pairObservation(value, 0, 1), observed);
  assert.equal(pairObservation(value, 0, 0), undefined);
});
