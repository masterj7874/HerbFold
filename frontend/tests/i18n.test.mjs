import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { i18nModuleUrl } from "./i18n-loader.mjs";
const memory = new Map();
globalThis.localStorage = { getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) };
globalThis.document = { documentElement: { lang: "" } };
const i18n = await import(await i18nModuleUrl());

test("language preference persists and subscribers update only on changes, with no browser reload", () => {
  let calls = 0; const unsubscribe = i18n.subscribeLanguage(() => calls++);
  i18n.setLanguage("en");
  assert.equal(i18n.getLanguage(), "en");
  assert.equal(document.documentElement.lang, "en");
  assert.equal(memory.get(i18n.LANGUAGE_STORAGE_KEY), "en");
  i18n.setLanguage("en"); assert.equal(calls, 1);
  i18n.setLanguage("ko"); assert.equal(calls, 2);
  assert.equal(i18n.localeCode(), "ko-KR");
  unsubscribe(); i18n.setLanguage("en"); assert.equal(calls, 2);
});

test("raw research values, objects, zeros, inactive flags and unknown names are never rewritten", () => {
  i18n.setLanguage("en");
  const record = { smiles: "C[C@H](O)F", job_id: "raw-job-id", label: 0, active: false, name: "연구자가 입력한 원문" };
  assert.equal(i18n.tr(record), record);
  for (const value of [0, false, null, undefined, "C[C@H](O)F", "P35354", "연구자가 입력한 원문"]) assert.equal(i18n.tr(value), value);
  assert.equal(record.name, "연구자가 입력한 원문");
});

test("exact UI translation switches both ways, preserving original Korean and whitespace", () => {
  const raw = "  AlphaFold 스튜디오 ";
  assert.equal(i18n.translateText(raw, "en"), "  AlphaFold Studio ");
  assert.equal(i18n.translateText(raw, "ko"), raw);
  assert.equal(i18n.translateText("퀘르세틴", "en"), "Quercetin");
  i18n.setLanguage("ko"); assert.equal(i18n.tr("에이전트 분석"), "에이전트 분석");
});

test("all translated templates retain their placeholder positions and runtime values", () => {
  for (const [key, translated] of Object.entries(i18n.englishMessages)) {
    const a = [...key.matchAll(/\{\d+\}/g)].map(v => v[0]).sort();
    const b = [...translated.matchAll(/\{\d+\}/g)].map(v => v[0]).sort();
    assert.deepEqual(b, a, key);
    if (!a.length) continue;
    const values = Object.fromEntries(a.map((placeholder, n) => [placeholder, `VALUE${n}`]));
    const input = key.replace(/\{\d+\}/g, token => values[token]);
    const expected = translated.replace(/\{\d+\}/g, token => values[token]);
    i18n.setLanguage("en");
    const args = [...key.matchAll(/\{(\d+)\}/g)].reduce((all, m) => { all[Number(m[1])] = values[m[0]]; return all; }, []);
    assert.equal(i18n.msg(key, ...args), expected, key);
  }
});

test("reviewed catalogs contain no empty or Korean English values", async () => {
  for (const group of ["core", "research", "design", "backend", "extra"]) {
    const catalog = JSON.parse(await readFile(new URL(`../src/i18n/en-${group}.json`, import.meta.url), "utf8"));
    for (const [key,value] of Object.entries(catalog)) {
      assert.ok(value.trim(), key); assert.doesNotMatch(value, /[가-힣]/, key);
    }
  }
});

test("blocked storage still allows an in-memory language switch", () => {
  const old = globalThis.localStorage;
  globalThis.localStorage = { setItem() { throw new Error("Storage unavailable"); } };
  i18n.setLanguage("en"); assert.equal(i18n.getLanguage(), "en");
  i18n.setLanguage("ko"); assert.equal(i18n.getLanguage(), "ko");
  globalThis.localStorage = old;
});


test("generic possessives and long imported text are preserved rather than guessed or recursively translated", () => {
  const originals = ["홍길동의 실험", "/data/연구의 구조.cif", "https://example.org/연구/구조.cif", "의 ".repeat(3000) + "끝"];
  for (const original of originals) assert.equal(i18n.translateText(original, "en"), original);
  assert.equal(i18n.translateText("18분 0초", "en"), "18 min 0 sec");
});


test("explicit messages preserve user labels and raw paths even when they match a UI key", () => {
  i18n.setLanguage("en");
  for (const raw of ["완료", "홍길동의 실험", "/data/연구/구조.cif", "C[C@H](O)F"]) {
    assert.equal(i18n.msg("{0} 구조 보기", raw), `View ${raw} structure`);
  }
});
