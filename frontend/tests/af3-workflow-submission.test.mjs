import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
const source = await readFile(new URL("../src/lib/af3WorkflowSubmission.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const helper = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
const values = new Map();
globalThis.localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
const request = { request_id: helper.newRequestId(), compound: { id: "fixture", name: "Synthetic control", smiles: "CCO" }, target_accession: "P35354", seeds: [0], msa_mode: "none", exploratory_ack: true, execute: false };

test("request identity and exact inputs survive browser state recreation for lost acknowledgment lookup", () => {
  assert.equal(helper.savePendingAF3(request), true);
  const restored = helper.readPendingAF3();
  assert.deepEqual(restored, request);
  assert.equal(restored.seeds[0], 0);
  assert.equal(restored.execute, false);
});
test("a late acknowledgment cannot erase a newer unacknowledged request", () => {
  const later = { ...request, request_id: helper.newRequestId() };
  helper.savePendingAF3(later);
  helper.clearPendingAF3(request.request_id);
  assert.deepEqual(helper.readPendingAF3(), later);
  helper.clearPendingAF3(later.request_id);
  assert.equal(helper.readPendingAF3(), null);
});
test("corrupt or denied browser storage cannot crash workflow navigation", () => {
  localStorage.setItem("herbfold:af3-workflow:pending", "malformed json");
  assert.equal(helper.readPendingAF3(), null);
  const old = globalThis.localStorage;
  globalThis.localStorage = { getItem: () => { throw Error("denied"); }, setItem: () => { throw Error("denied"); } };
  assert.equal(helper.readPendingAF3(), null);
  assert.equal(helper.savePendingAF3(request), false);
  helper.clearPendingAF3(request.request_id);
  globalThis.localStorage = old;
});
test("ordinary HTTP browser contexts without randomUUID still produce distinct version 4 request IDs", () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { value: undefined, configurable: true });
  try {
    const ids = new Set(Array.from({length: 100}, helper.newRequestId));
    assert.equal(ids.size, 100);
    for (const id of ids) assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "crypto", descriptor);
    else delete globalThis.crypto;
  }
});
