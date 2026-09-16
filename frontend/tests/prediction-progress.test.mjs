// Synthetic timing fixtures only; these records are never served to the application.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/predictionProgress.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { elapsedSeconds, formatElapsed, gpuMemoryWarning, predictionExecutionProgress, predictionExecutionTimeline, recordedExecutionSteps, recordedStageDetail } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

test("elapsed time changes with the clock and does not invent a time for unknown or future starts", () => {
  const start = "2026-01-01T00:00:00Z", now = Date.parse(start);
  assert.equal(elapsedSeconds(start, now), 0);
  assert.equal(elapsedSeconds(start, now + 465721), 465.721);
  for (const value of [undefined, null, "", "invalid", "2026-01-01T00:00:01Z"]) assert.equal(elapsedSeconds(value, now), null);
});

test("measured durations preserve zero and hours while invalid values remain unrecorded", () => {
  assert.equal(formatElapsed(0), "0초");
  assert.equal(formatElapsed(465.721), "7분 45초");
  assert.equal(formatElapsed(3601), "1시간 0분 1초");
  for (const value of [null, undefined, -1, NaN, Infinity]) assert.equal(formatElapsed(value), "미기록");
});

test("completed stage duration comes from successful finished execution, never a pending or failed stage", () => {
  const complete = { name: "data_pipeline", started_at: "2026-01-01T00:00:00Z", finished_at: "2026-01-01T00:07:46Z", return_code: 0, elapsed_seconds: 465.721 };
  const steps = recordedExecutionSteps({ execution: { steps: [complete, { ...complete, name: "inference", return_code: 1 }, { name: "inference", elapsed_seconds: 10 }] } });
  assert.equal(steps[0].completed, true);
  assert.equal(steps[0].elapsedSeconds, 465.721);
  assert.equal(steps[1].completed, false);
  assert.equal(steps[1].elapsedSeconds, null);
  assert.equal(steps[2].completed, false);
  assert.equal(steps[2].elapsedSeconds, null);
});

test("older successful records can use recorded timestamps without estimating missing durations", () => {
  const step = { name: "inference", started_at: "2026-01-01T00:00:00Z", finished_at: "2026-01-01T00:01:00Z", return_code: 0 };
  assert.equal(recordedExecutionSteps({ execution: { steps: [step] } })[0].elapsedSeconds, 60);
  assert.equal(recordedExecutionSteps({ execution: { steps: [{ ...step, started_at: "invalid" }] } })[0].elapsedSeconds, null);
  assert.equal(recordedExecutionSteps({ execution: { steps: [{ ...step, finished_at: "invalid" }] } })[0].completed, false);
});

test("device display uses recorded execution settings and tolerates absent legacy metadata", () => {
  const steps = recordedExecutionSteps({ execution: { steps: [
    { name: "inference", command: ["python", "--jax_backend=gpu"] },
    { name: "inference", command: ["python", "--jax_backend=cpu"] },
    { name: "inference" },
    { name: "unknown" },
    null,
  ] } });
  assert.deepEqual(steps.map((step) => step.device), ["GPU", "CPU", null]);
  for (const value of [null, {}, { execution: null }, { execution: { steps: "bad" } }]) assert.deepEqual(recordedExecutionSteps(value), []);
});

test("GPU memory warning is displayed only from a recorded diagnostic, without inventing job failure", () => {
  const warning = { code: "gpu_memory_pressure", message: "Synthetic unit diagnostic", observed_at: "2026-01-01T00:01:00Z" };
  assert.deepEqual(gpuMemoryWarning({ runtime_warning: warning }), { message: warning.message, observedAt: warning.observed_at });
  assert.equal(gpuMemoryWarning({ runtime_warning: { ...warning, observed_at: "bad" } }).observedAt, null);
  for (const value of [null, {}, { runtime_warning: "bad" }, { runtime_warning: { ...warning, code: "other" } }, { runtime_warning: { ...warning, message: "" } }]) assert.equal(gpuMemoryWarning(value), null);
});

test("a fresh GET does not turn a stale worker heartbeat into fresh process evidence", () => {
  const now = Date.parse("2026-01-01T00:10:00Z");
  const result = { execution: { started_at: "2026-01-01T00:00:00Z", last_checked_at: "2026-01-01T00:08:00Z" } };
  const stage = { name: "data_pipeline", started_at: "2026-01-01T00:00:01Z", checked_at: "2026-01-01T00:08:00Z" };
  const progress = predictionExecutionProgress(result, stage, "running", new Date(now).toISOString(), now);
  assert.equal(progress.responseAge, 0);
  assert.equal(progress.heartbeatAge, 120);
  assert.equal(progress.heartbeatState, "stale");
  assert.equal(progress.active, true, "An old heartbeat is not an invented terminal failure");
  assert.equal(progress.totalSeconds, 600);
});

test("fresh worker heartbeats are distinct from missing or delayed screen responses", () => {
  const now = Date.parse("2026-01-01T00:10:00Z");
  const result = { execution: { last_checked_at: "2026-01-01T00:09:55Z" } };
  const progress = predictionExecutionProgress(result, { name: "inference" }, "running", null, now);
  assert.equal(progress.heartbeatState, "recent");
  assert.equal(progress.heartbeatAge, 5);
  assert.equal(progress.responseAge, null);
  assert.equal(predictionExecutionProgress({}, {}, "running", new Date(now).toISOString(), now).heartbeatState, "unrecorded");
});

test("a previous search heartbeat never establishes the newly started inference process", () => {
  const now = Date.parse("2026-01-01T00:10:00Z");
  const result = { execution: { last_checked_at: "2026-01-01T00:09:50Z" } };
  const stage = { name: "inference", started_at: "2026-01-01T00:09:55Z" };
  const progress = predictionExecutionProgress(result, stage, "running", new Date(now).toISOString(), now);
  assert.equal(progress.heartbeatState, "unrecorded");
  assert.equal(progress.heartbeatAt, null);
});

test("completed execution freezes total duration and never remains active due to heartbeat age", () => {
  const result = { execution: { started_at: "2026-01-01T00:00:00Z", finished_at: "2026-01-01T00:17:56Z", last_checked_at: "2026-01-01T00:17:40Z" } };
  const stage = { name: "completed", finished_at: "2026-01-01T00:17:57Z" };
  for (const now of [Date.parse("2026-01-01T01:00:00Z"), Date.parse("2026-01-02T01:00:00Z")]) {
    const progress = predictionExecutionProgress(result, stage, "completed", null, now);
    assert.equal(progress.active, false);
    assert.equal(progress.heartbeatState, "finished");
    assert.equal(progress.totalSeconds, 1077);
  }
  assert.equal(predictionExecutionProgress({}, { name: "completed" }, "completed", null, Date.now()).totalSeconds, null);
});

test("completed timeline retains measured stages while skipped search does not invent a duration", () => {
  const finish = "2026-01-01T00:17:57Z";
  const result = { execution: { steps: [
    { name: "data_pipeline", elapsed_seconds: 897.93, return_code: 0, finished_at: finish },
    { name: "inference", elapsed_seconds: 177.95, return_code: 0, finished_at: finish },
  ] } };
  const timeline = predictionExecutionTimeline(result, { name: "completed" }, "completed", "search");
  assert.deepEqual(timeline.map((item) => item.state), ["completed", "completed", "completed"]);
  assert.deepEqual(timeline.map((item) => item.elapsedSeconds), [897.93, 177.95, null]);
  assert.equal(timeline.some((item) => item.state === "active"), false);
  const reused = predictionExecutionTimeline({ msa_features: { status: "ready", cache_hit: true } }, { name: "inference" }, "running", "search");
  assert.equal(reused[0].state, "skipped");
  assert.equal(reused[0].elapsedSeconds, null);
  assert.equal(reused[1].state, "active");
});

test("log stage details require a recognized observed event and never fabricate a phase from unknown text", () => {
  const detail = { name: "model_inference", label: "모델 추론", observed_at: "2026-01-01T00:10:00Z", source: "run.log" };
  assert.equal(recordedStageDetail({ detail }).label, "모델 추론");
  const search = recordedStageDetail({ detail: { ...detail, name: "msa_search", label: "단백질 서열 검색", completed_databases: ["BFD", "UniProt", "UniProt", "guessed"] } });
  assert.deepEqual(search.completedDatabases, ["BFD", "UniProt"]);
  assert.deepEqual(recordedStageDetail({ detail: { ...detail, completed_databases: Array(100).fill("BFD") } }).completedDatabases, []);
  for (const value of [{}, { detail: { ...detail, name: "guessed_eta" } }, { detail: { ...detail, observed_at: "invalid" } }, { detail: { ...detail, source: "estimated" } }]) assert.equal(recordedStageDetail(value), null);
});
