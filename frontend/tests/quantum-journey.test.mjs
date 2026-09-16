// Isolated data-transform fixtures only; never submitted to a QPU or served as research observations.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
const source = await readFile(new URL("../src/lib/quantumJourney.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { readBlocks, encodingAngles, gateAngles, readBloch, quantumProgress } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-12, `${a} != ${b}`);

test("encoding repeats bounded descriptors when d < q and averages strided bounded columns when d >= q", () => {
  const a = 2 * Math.atan(0.5), b = 2 * Math.atan(-1);
  assert.deepEqual(encodingAngles([0.5, -1], 5), [a, b, a, b, a]);
  assert.deepEqual(encodingAngles([0.5, -1], 2), [a, b]);
  const result = encodingAngles([0, 1, 2, 3, 4], 2);
  close(result[0], (2 * Math.atan(0) + 2 * Math.atan(2) + 2 * Math.atan(4)) / 3);
  close(result[1], (2 * Math.atan(1) + 2 * Math.atan(3)) / 2);
  assert.notEqual(result[0], 2 * Math.atan(2)); // Averaging before atan is a different operation.
  for (const [features, q] of [[[], 4], [[NaN], 4], [[Infinity], 4], [["1"], 4], [[1], 0], [[1], 2.5], [[1], 100001]]) {
    assert.deepEqual(encodingAngles(features, q), []);
  }
});

test("gate angles respect archived noncontiguous block order and block-local layer offsets", () => {
  const angles = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6];
  const block = { logical_qubits: [4, 1, 5], edges: [[4, 1], [1, 5]] };
  assert.deepEqual(gateAngles(angles, block, 0), [
    { logical: 4, ry: 0.5, rz: 0.1 }, { logical: 1, ry: 0.2, rz: 0.3 }, { logical: 5, ry: 0.6, rz: 0.25 },
  ]);
  assert.deepEqual(gateAngles(angles, block, 1), [
    { logical: 4, ry: 0.2, rz: 0.25 }, { logical: 1, ry: 0.6, rz: 0.1 }, { logical: 5, ry: 0.5, rz: 0.3 },
  ]);
  assert.deepEqual(gateAngles(angles, { logical_qubits: [4], edges: [] }, 7), [{ logical: 4, ry: 0.5, rz: 0.25 }]);
  assert.deepEqual(gateAngles(angles, block, -1), []);
  assert.deepEqual(gateAngles(angles, block, 8), []);
  assert.deepEqual(gateAngles(angles, { logical_qubits: [6], edges: [] }, 0), []);
});

test("archived blocks must cover every logical qubit exactly once and contain only valid internal edges", () => {
  const blocks = [{ logical_qubits: [2, 0], edges: [[2, 0]] }, { logical_qubits: [1], edges: [] }];
  assert.deepEqual(readBlocks({ plan: { n_qubits: 3, blocks } }), blocks);
  assert.deepEqual(readBlocks({ metadata: { n_qubits: 3, blocks } }), blocks);
  const bad = [
    [{ logical_qubits: [0, 1], edges: [[0, 1]] }], // Missing logical 2.
    [{ logical_qubits: [0, 1, 2], edges: [[0, 1.5]] }],
    [{ logical_qubits: [0, 1, 2], edges: [[0, 3]] }],
    [{ logical_qubits: [0, 1, 2], edges: [[0, 0]] }],
    [{ logical_qubits: [0, 1, 2], edges: [[0, 1], [1, 0]] }],
    [{ logical_qubits: [0, 1], edges: [[0, 2]] }, { logical_qubits: [2], edges: [] }],
    [{ logical_qubits: [0, 1], edges: [] }, { logical_qubits: [1, 2], edges: [] }],
    [{ logical_qubits: [0, 0, 1, 2], edges: [] }],
    [{ logical_qubits: [0, 1, 2], edges: [[0, 1, 2]] }],
  ];
  for (const candidate of bad) assert.deepEqual(readBlocks({ plan: { n_qubits: 3, blocks: candidate } }), []);
  for (const missing of [undefined, null, {}, { plan: { n_qubits: 3 } }]) assert.deepEqual(readBlocks(missing), []);
});

test("Bloch values follow explicit axis order and retain raw norms above one", () => {
  const value = { projected_features: { axes: ["Z", "X", "Y"], values: [[[0.8, 0.8, 0.2]]] } };
  const result = readBloch(value, 0, 0);
  assert.deepEqual(result, { x: 0.8, y: 0.2, z: 0.8, norm: Math.hypot(0.8, 0.2, 0.8) });
  assert.ok(result.norm > 1);
  assert.deepEqual(value.projected_features.values[0][0], [0.8, 0.8, 0.2]);
  assert.deepEqual(readBloch({ projected_features: { axes: ["X", "Y", "Z"], values: [[[0, 0, 0]]] } }, 0, 0), { x: 0, y: 0, z: 0, norm: 0 });
  for (const bad of [undefined, null, {},
    { projected_features: { axes: ["X", "X", "Z"], values: [[[0, 0, 0]]] } },
    { projected_features: { axes: ["X", "Y", "Z"], values: [[[null, 0, 0]]] } },
    { projected_features: { axes: ["X", "Y", "Z"], values: [[[NaN, 0, 0]]] } },
    { projected_features: { axes: ["X", "Y", "Z"], values: [[[1.01, 0, 0]]] } },
    { projected_features: { axes: ["X", "Y", "Z"], values: [[[0, 0]]] } },
  ]) assert.equal(readBloch(bad, 0, 0), null);
  assert.equal(readBloch(value, -1, 0), null);
  assert.equal(readBloch(value, 1, 0), null);
  assert.equal(readBloch(value, 0, 0.5), null);
});

test("progress describes stored evidence and never upgrades pending, failed or unverified execution to completed measurements", () => {
  const base = {
    status: "running", mode: "ibm", hardware_executed: null,
    features: [[0.1, 0.2], [0.3, 0.4]],
    plan: { n_qubits: 2, encoding: "atan_fold_or_repeat_v1", blocks: [{ logical_qubits: [0, 1], edges: [[0, 1]] }] },
  };
  const state = value => Object.fromEntries(quantumProgress(value).map(stage => [stage.id, stage.state]));
  assert.deepEqual(state(base), { descriptors: "available", encoding: "available", circuit: "available", measurement: "pending", comparison: "pending" });
  assert.equal(state({ ...base, kernel: [[1, 0.7], [0.7, 1]] }).measurement, "pending");
  assert.equal(state({ ...base, status: "completed", kernel: [[1, 0.7], [0.7, 1]] }).measurement, "unavailable");
  assert.equal(state({ ...base, status: "completed", hardware_executed: true }).measurement, "unavailable");
  const measured = { ...base, status: "completed", hardware_executed: true, kernel: [[0, 0], [0, 0]] };
  assert.equal(state(measured).measurement, "available"); // True observed global zeros remain measurements.
  assert.equal(state(measured).comparison, "available");
  assert.equal(state({ ...measured, status: "failed" }).measurement, "error");
  assert.equal(state({ ...measured, status: "partial_submission" }).comparison, "error");
  const local = quantumProgress({ ...measured, mode: "local", hardware_executed: false });
  assert.equal(local.find(stage => stage.id === "measurement").detail, "로컬 이상적 계산 결과입니다.");
  assert.ok(quantumProgress(null).every(stage => stage.state === "unavailable"));
  assert.ok(quantumProgress(measured).every(stage => !("percent" in stage) && !("duration" in stage)));
  assert.equal(state({ ...base, plan: { ...base.plan, encoding: "unknown_future_encoding" } }).encoding, "pending");
});
