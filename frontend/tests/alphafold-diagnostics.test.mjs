// Isolated UI diagnostics fixtures; these are never served as predictions.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
const source = await readFile(new URL("../src/lib/alphaFoldDiagnostics.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { diagnosticNumber, diagnosticLink, summarizeConfidence, sceneConfidence, currentDiagnosticScene, diagnosticMatchesScene, af3ClashLabel } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

const scene = () => ({ source: "alphafold3_prediction", atoms: [
  { id: 1, confidence: 90, is_protein: true, chain_id: "A" },
  { id: 2, confidence: 70, is_protein: true, chain_id: "A" },
  { id: 3, confidence: 40, is_ligand: true },
  { id: 4, confidence: 100, is_ligand: true }, // Unselected cofactor.
], metadata: { job_id: "actual-id-fixture", sha256: "sha-fixture", confidence_kind: "pLDDT", selected_ligand_atom_ids: [3],
  selection: { target_accession: "P35354", canonical_smiles: "CCO", target_chains: ["A"] } } });

test("missing, out-of-range and experimental B factors never become AF3 confidence", () => {
  assert.equal(diagnosticNumber(0, 0, 1), 0);
  for (const value of [undefined, null, "0", NaN, Infinity, -1, 101]) assert.equal(diagnosticNumber(value, 0, 100), null);
  const experimental = scene(); experimental.source = "experimental_pdb";
  experimental.atoms[0].b_factor = 99;
  assert.equal(sceneConfidence(experimental).all.mean, null);
  const excluded = scene(); excluded.metadata.prediction_eligible = false;
  assert.equal(sceneConfidence(excluded).all.count, 0);
  assert.deepEqual(summarizeConfidence([null, 0, 100, NaN, 101]), { count: 2, total: 5, mean: 50, min: 0, max: 100 });
});

test("selected-ligand pLDDT excludes unrelated cofactors and preserves partial coverage", () => {
  assert.equal(sceneConfidence(scene()).protein.mean, 80);
  assert.equal(sceneConfidence(scene()).ligand.mean, 40);
  const missing = scene(); missing.atoms[2].confidence = null;
  assert.deepEqual(sceneConfidence(missing).ligand, { count: 0, total: 1, mean: null, min: null, max: null });
  delete missing.metadata.selected_ligand_atom_ids;
  assert.equal(sceneConfidence(missing).ligand.total, 0);
});

test("target pLDDT excludes other protein chains and requires target chain mapping", () => {
  const value = scene();
  value.atoms.push({ id: 9, confidence: 0, is_protein: true, chain_id: "Z" });
  assert.equal(sceneConfidence(value).protein.mean, 80);
  assert.equal(sceneConfidence(value).protein.count, 2);
  delete value.metadata.selection.target_chains;
  assert.equal(sceneConfidence(value).protein.mean, null);
});

test("changing source, target or loading status immediately removes previous diagnostics", () => {
  const value = scene();
  assert.equal(currentDiagnosticScene(value, value.source, "P35354"), value);
  assert.equal(currentDiagnosticScene(value, "experimental_pdb", "P35354"), null);
  assert.equal(currentDiagnosticScene(value, value.source, "Q12809"), null);
  assert.equal(currentDiagnosticScene(value, value.source, "P35354", true), null);
});

test("a diagnostic result must match the displayed job, CIF, ligand and target together", () => {
  const value = scene();
  const report = { job_id: value.metadata.job_id, structure_sha256: value.metadata.sha256,
    requested: { canonical_smiles: "CCO", target_accession: "P35354" } };
  assert.equal(diagnosticMatchesScene(report, value, "P35354"), true);
  for (const wrong of [
    { ...report, job_id: "late-other-job" },
    { ...report, structure_sha256: "other-sample-from-same-job" },
    { ...report, requested: { ...report.requested, canonical_smiles: "CO" } },
    { ...report, requested: { ...report.requested, target_accession: "Q12809" } },
  ]) assert.equal(diagnosticMatchesScene(wrong, value, "P35354"), false);
  value.metadata.artifact = "output/sample-1.cif";
  assert.equal(diagnosticMatchesScene({ ...report, artifact: "output/sample-2.cif" }, value, "P35354"), false);
  assert.equal(diagnosticMatchesScene({ ...report, artifact: value.metadata.artifact }, value, "P35354"), true);
});

test("absence of the major-clash flag never claims full geometry validation", () => {
  assert.equal(af3ClashLabel(false), "대규모 충돌 플래그 없음");
  assert.equal(af3ClashLabel(true), "대규모 원자 충돌 경고");
  assert.equal(af3ClashLabel(undefined), "— · 검사값 미기록");
  assert.equal(diagnosticLink("javascript:alert(1)"), null);
  assert.equal(diagnosticLink("https://www.rcsb.org/structure/5IKR"), "https://www.rcsb.org/structure/5IKR");
});
