import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/predictionPolling.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { pollPrediction, predictionRequest, PredictionRequestTimeout } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("a terminal status is delivered while the independent log response is still hung", { timeout: 1000 }, async (t) => {
  const controller = new AbortController();
  t.after(() => controller.abort());
  let statusRequests = 0, logErrors = 0;
  const statuses = [];
  let resolveCompleted;
  const completed = new Promise((resolve) => { resolveCompleted = resolve; });
  let resolveLogError;
  const logError = new Promise((resolve) => { resolveLogError = resolve; });
  pollPrediction({
    signal: controller.signal, intervalMs: 1,
    request: async () => ++statusRequests === 1 ? "running" : "completed",
    onValue: (status) => { statuses.push(status); if (status === "completed") resolveCompleted(); },
    onError: (problem) => assert.fail(problem.message),
    shouldRepeat: (status) => status === "running",
  });
  pollPrediction({
    signal: controller.signal, timeoutMs: 100,
    request: () => new Promise(() => {}),
    onValue: () => assert.fail("A hung log has no result"),
    onError: (problem) => { assert.ok(problem instanceof PredictionRequestTimeout); logErrors++; resolveLogError(); },
    shouldRepeat: () => false,
  });
  await completed;
  assert.deepEqual(statuses, ["running", "completed"]);
  assert.equal(logErrors, 0, "Result availability must not wait for a log deadline");
  await logError;
  assert.equal(statusRequests, 2, "Terminal status stops its own polling loop");
});

test("a stalled status request times out, aborts its transport, and automatically recovers", { timeout: 1000 }, async (t) => {
  const controller = new AbortController();
  t.after(() => controller.abort());
  const events = [];
  let requests = 0, firstSignal;
  let resolveCompleted;
  const completed = new Promise((resolve) => { resolveCompleted = resolve; });
  pollPrediction({
    signal: controller.signal, intervalMs: 1, timeoutMs: 10,
    request: (signal) => { requests++; if (requests === 1) { firstSignal = signal; return new Promise(() => {}); } return Promise.resolve("completed"); },
    onValue: (value) => { events.push(value); resolveCompleted(); },
    onError: (problem) => { assert.ok(problem instanceof PredictionRequestTimeout); events.push("connection-timeout"); },
    shouldRepeat: () => false,
  });
  await completed;
  assert.deepEqual(events, ["connection-timeout", "completed"]);
  assert.equal(firstSignal.aborted, true);
  assert.equal(requests, 2);
});

test("changing the selected compound cancels old callbacks and retries, including late responses", { timeout: 1000 }, async () => {
  const oldSelection = new AbortController();
  let resolveOld, oldRequests = 0;
  const delivered = [];
  pollPrediction({
    signal: oldSelection.signal, intervalMs: 1, timeoutMs: 15,
    request: () => { oldRequests++; return new Promise((resolve) => { resolveOld = resolve; }); },
    onValue: (value) => delivered.push(value),
    onError: () => delivered.push("old-error"),
    shouldRepeat: () => true,
  });
  await sleep(0);
  oldSelection.abort();
  const newSelection = new AbortController();
  pollPrediction({
    signal: newSelection.signal, intervalMs: 1,
    request: async () => "new-compound-completed",
    onValue: (value) => delivered.push(value),
    onError: (problem) => assert.fail(problem.message),
    shouldRepeat: () => false,
  });
  resolveOld("old-compound-completed");
  await sleep(30);
  newSelection.abort();
  assert.deepEqual(delivered, ["new-compound-completed"]);
  assert.equal(oldRequests, 1);
});

test("a superseded mutation generation cannot replace current status or schedule another request", { timeout: 1000 }, async () => {
  const controller = new AbortController();
  let generation = 1, resolveOld, requests = 0;
  const delivered = [];
  pollPrediction({
    signal: controller.signal, isCurrent: () => generation === 1, intervalMs: 1,
    request: () => { requests++; return new Promise((resolve) => { resolveOld = resolve; }); },
    onValue: (value) => delivered.push(value),
    onError: (problem) => delivered.push(problem.message),
    shouldRepeat: () => true,
  });
  await sleep(0);
  generation = 2;
  resolveOld("prepared");
  await sleep(10);
  controller.abort();
  assert.deepEqual(delivered, []);
  assert.equal(requests, 1);
});

test("a mutation deadline reports an unknown response and never automatically resubmits", { timeout: 1000 }, async () => {
  let requests = 0, resolveLate, signal;
  await assert.rejects(predictionRequest((requestSignal) => {
    requests++; signal = requestSignal;
    return new Promise((resolve) => { resolveLate = resolve; });
  }, { timeoutMs: 10 }), PredictionRequestTimeout);
  assert.equal(signal.aborted, true);
  resolveLate("server-may-have-submitted");
  await sleep(20);
  assert.equal(requests, 1);
  assert.equal(await predictionRequest(async () => "subsequent-read-succeeds"), "subsequent-read-succeeds");
});
