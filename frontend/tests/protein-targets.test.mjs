import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/proteinTargets.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { createTargetRequestGuard, mergeProteinTargets, normalizeTargetAccession, targetEntryUrl, targetSequencePreview, validTargetAccession } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

function fixture(accession, changes = {}) {
  return { accession, name: "Synthetic test protein", gene: null, organism: "Synthetic fixture", length: 5, sequence: "MAMAM", sequence_sha256: "fixture-hash", source: "https://www.uniprot.org/", ...changes };
}

test("ID normalization preserves isoform suffixes and rejects malformed UI input", () => {
  assert.equal(normalizeTargetAccession(" p00533-2 "), "P00533-2");
  assert.equal(validTargetAccession(" p00533-2 "), true);
  for (const value of ["", "A", "P00533/P35354", "javascript:alert(1)", "P00533 P35354", "A".repeat(21)]) {
    assert.equal(validTargetAccession(value), false);
  }
});

test("rapid ID changes cannot apply an older registration response even when transport ignores abort", async () => {
  const guard = createTargetRequestGuard();
  const applied = [];
  let finishOld, finishNew;
  const oldRequest = guard.begin();
  const oldResponse = new Promise((resolve) => { finishOld = resolve; }).then((record) => {
    if (oldRequest.isCurrent()) applied.push(record.accession);
  });
  const newRequest = guard.begin();
  const newResponse = new Promise((resolve) => { finishNew = resolve; }).then((record) => {
    if (newRequest.isCurrent()) applied.push(record.accession);
  });
  finishNew(fixture("P00533"));
  finishOld(fixture("P35354"));
  await Promise.all([oldResponse, newResponse]);
  assert.equal(oldRequest.signal.aborted, true);
  assert.deepEqual(applied, ["P00533"]);
});

test("a draft change or unmount invalidates an in-flight response without changing the applied target", () => {
  const guard = createTargetRequestGuard();
  const original = guard.begin();
  guard.invalidate();
  assert.equal(original.isCurrent(), false);
  assert.equal(original.signal.aborted, true);
  const retryOfSameId = guard.begin();
  assert.equal(retryOfSameId.isCurrent(), true);
  assert.equal(original.isCurrent(), false, "Returning to the same ID must not revive its first request");
});

test("a delayed saved-target list preserves a newly registered record and refreshes matching metadata", () => {
  const initial = fixture("P35354", { name: "Original name", entry_version: 1 });
  const registered = fixture("P00533", { name: "Newly registered", reviewed: true });
  const updated = fixture("P35354", { name: "Updated name", entry_version: 2, organism: "Updated organism" });
  const records = mergeProteinTargets([initial, registered], [updated]);
  assert.deepEqual(records.map((record) => record.accession), ["P00533", "P35354"]);
  assert.equal(records[0], registered);
  assert.equal(records[1].entry_version, 2);
  assert.equal(records[1].organism, "Updated organism");
  assert.deepEqual(mergeProteinTargets(records, []), records);
});

test("sequence previews preserve residues and accurately label truncation", () => {
  const record = fixture("P00533", { sequence: "M" + "ACDEFGHIKL".repeat(12), length: 121 });
  const preview = targetSequencePreview(record);
  assert.equal(preview.shown, 120);
  assert.equal(preview.truncated, true);
  assert.equal(preview.text.replaceAll(" ", ""), record.sequence.slice(0, 120));
  assert.deepEqual(targetSequencePreview(fixture("P35354")), { text: "MAMAM", shown: 5, truncated: false });
});

test("source links stay on UniProt and encode the accession as a path component", () => {
  assert.equal(targetEntryUrl("P00533-2"), "https://www.uniprot.org/uniprotkb/P00533-2/entry");
  assert.equal(new URL(targetEntryUrl("//untrusted.example/#x")).host, "www.uniprot.org");
  assert.match(targetEntryUrl("//untrusted.example/#x"), /%2F%2Funtrusted\.example%2F%23x/);
});
