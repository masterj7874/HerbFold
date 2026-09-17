/** Reviewed, local display translations. Research inputs and records remain unchanged. */
import core from "../i18n/en-core.json";
import research from "../i18n/en-research.json";
import design from "../i18n/en-design.json";
import backend from "../i18n/en-backend.json";
import extra from "../i18n/en-extra.json";

export type Language = "ko" | "en";
export const LANGUAGE_STORAGE_KEY = "herbfold.language.v1";
const sourceMessages: Record<string, string> = { ...research, ...design, ...core, ...backend, ...extra };
const decodeEntities = (text: string) => text.replace(/&(amp|lt|gt|quot|apos|nbsp);/g, (_, key: string) => ({ amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0" }[key] || key));
export const englishMessages: Record<string, string> = Object.fromEntries(Object.entries(sourceMessages).map(([key, value]) => [decodeEntities(key).replace(/\s+/g, " ").trim(), value]));
const normalize = (text: string) => text.replace(/\s+/g, " ").trim();
const listeners = new Set<() => void>();
function readLanguage(): Language {
  try { return localStorage.getItem(LANGUAGE_STORAGE_KEY) === "en" ? "en" : "ko"; } catch { return "ko"; }
}
let language: Language = readLanguage();
export const getLanguage = (): Language => language;
export const localeCode = () => language === "en" ? "en-US" : "ko-KR";
export const subscribeLanguage = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export function setLanguage(next: Language, persist = true): void {
  if (next !== "en" && next !== "ko") return;
  if (persist) { try { localStorage.setItem(LANGUAGE_STORAGE_KEY, next); } catch { /* Session switching still works. */ } }
  if (typeof document !== "undefined") document.documentElement.lang = next;
  if (next === language) return;
  language = next;
  listeners.forEach(listener => listener());
}
if (typeof document !== "undefined") document.documentElement.lang = language;
if (typeof window !== "undefined") window.addEventListener("storage", event => {
  if (event.key === LANGUAGE_STORAGE_KEY) setLanguage(event.newValue === "en" ? "en" : "ko", false);
});

const escapeRegex = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const templates = Object.entries(englishMessages).filter(([key]) => /\{\d+\}/.test(key) && !/\{\d+\}\s*\{\d+\}/.test(key)).flatMap(([key, value]) => {
  const fixed = key.replace(/\{\d+\}/g, "");
  // Short fixed text binds numeric arguments only; free text requires a distinctive Korean phrase (4+ syllables) or a long fixed part so user data is never guessed.
  const numericOnly = fixed.length < 18 && (fixed.match(/[가-힣]/g) || []).length < 4;
  const indices: string[] = [];
  let cursor = 0, pattern = "";
  for (const match of key.matchAll(/\{(\d+)\}/g)) {
    pattern += escapeRegex(key.slice(cursor, match.index)) + (numericOnly ? "([\\d.,+−:-]+)" : "([\\s\\S]*?)");
    indices.push(match[1]); cursor = match.index! + match[0].length;
  }
  pattern += escapeRegex(key.slice(cursor));
  return [{ regex: new RegExp("^" + pattern + "$"), indices, value, weight: fixed.length }];
}).sort((a, b) => b.weight - a.weight);
const isRawLocation = (text: string) => /^(?:\/|[A-Za-z]:[\\/]|https?:\/\/|file:)/.test(text.trim());
const argumentText = (value: unknown, target: Language) => {
  const text = String(value);
  return target === "en" && !isRawLocation(text) ? (englishMessages[normalize(text)] ?? text) : text;
};

/** Explicit messages retain argument boundaries, even for adjacent/reordered values. */
export function msg(source: string, ...values: unknown[]): string {
  const template = language === "en" ? (englishMessages[normalize(source)] ?? source) : source;
  return template.replace(/\{(\d+)\}/g, (token, index) => Number(index) < values.length ? String(values[Number(index)]) : token);
}
const cache = new Map<string, string>();

export function translateText(text: string, target: Language = language): string {
  if (target === "ko" || !/[가-힣]/.test(text) || isRawLocation(text)) return text;
  const cached = cache.get(text); if (cached !== undefined) return cached;
  const key = normalize(text);
  let translated = englishMessages[key];
  if (translated === undefined && key.length < 3000) {
    for (const template of templates) {
      const match = template.regex.exec(key); if (!match) continue;
      const values: Record<string, string> = {};
      template.indices.forEach((index, position) => { values[index] = argumentText(match[position + 1], target); });
      translated = template.value.replace(/\{(\d+)\}/g, (placeholder, index) => values[index] ?? placeholder);
      break;
    }
  }
  if (translated === undefined) return text;
  const leading = text.match(/^\s*/)?.[0] || "", trailing = text.match(/\s*$/)?.[0] || "";
  const output = leading + translated + trailing;
  if (cache.size > 6000) cache.clear();
  cache.set(text, output);
  return output;
}

/** Apply only at React display boundaries; never to form values, IDs, files or API bodies. */
export function tr<T>(value: T): T {
  if (typeof value === "string") return translateText(value) as T;
  if (Array.isArray(value)) return value.map(item => tr(item)) as T;
  return value;
}
