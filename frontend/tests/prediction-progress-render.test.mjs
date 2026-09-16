// Synthetic server-rendered UI fixtures; never served as scientific results.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { pathToFileURL } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const compile = (source) => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const dataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const dependencies = {};
for (const name of ["predictionEvidence", "predictionProgress"]) dependencies[`../lib/${name}`] = dataUrl(compile(await readFile(new URL(`../src/lib/${name}.ts`, import.meta.url), "utf8")));
for (const name of ["react", "react/jsx-runtime", "lucide-react"]) dependencies[name] = pathToFileURL(require.resolve(name)).href;
const source = compile(await readFile(new URL("../src/components/AF3EvidencePanel.tsx", import.meta.url), "utf8"));
const { AF3StageEvidence } = await import(dataUrl(source.replace(/from "([^"]+)"/g, (_, name) => `from ${JSON.stringify(dependencies[name] || name)}`)));

test("completed calculations render ended stages and measured time with no spinning indicator", () => {
  const finished = "2026-01-01T00:18:00Z";
  const entry = {
    job: { id: "synthetic-completed", status: "completed", result: { execution: {
      started_at: "2026-01-01T00:00:00Z", finished_at: finished,
      steps: [{ name: "data_pipeline", return_code: 0, finished_at: finished, elapsed_seconds: 897.93 }, { name: "inference", return_code: 0, finished_at: finished, elapsed_seconds: 177.95 }],
    } } },
    stage: { name: "completed", finished_at: finished }, requested: { msa_mode: "search" },
  };
  // A stale parent live prop must not turn an already completed record into waiting UI.
  const html = renderToStaticMarkup(React.createElement(AF3StageEvidence, { entry, live: true, checkedAt: null }));
  assert.match(html, /계산과 출력 처리 종료/);
  assert.match(html, /18분 0초/);
  assert.match(html, /14분 57초/);
  assert.match(html, /2분 57초/);
  assert.equal((html.match(/data-state="completed"/g) || []).length, 3);
  assert.doesNotMatch(html, /\bspin\b|data-state="active"|프로세스 확인 기록 대기/);
});

test("fresh data delivery cannot hide an old execution heartbeat in the rendered panel", () => {
  const now = Date.now(), start = new Date(now - 600000).toISOString(), heartbeat = new Date(now - 120000).toISOString();
  const entry = {
    job: { id: "synthetic-stale", status: "running", result: { execution: { started_at: start, last_checked_at: heartbeat } } },
    stage: { name: "data_pipeline", started_at: start, checked_at: heartbeat }, requested: { msa_mode: "search" },
  };
  const html = renderToStaticMarkup(React.createElement(AF3StageEvidence, { entry, live: true, checkedAt: new Date(now).toISOString() }));
  assert.match(html, /data-heartbeat-state="stale"/);
  assert.match(html, /최근 실행 확인 없음/);
  assert.match(html, /실행 프로세스 마지막 확인/);
  assert.match(html, /화면 데이터 최근 수신/);
  assert.match(html, /실패로 확정된 상태는 아니며/);
});
