import { readFile } from "node:fs/promises";
import ts from "typescript";
export async function i18nModuleUrl() {
  let source = await readFile(new URL("../src/lib/i18n.ts", import.meta.url), "utf8");
  for (const name of ["core", "research", "design", "backend", "extra"]) {
    const data = JSON.parse(await readFile(new URL(`../src/i18n/en-${name}.json`, import.meta.url), "utf8"));
    source = source.replace(`import ${name} from "../i18n/en-${name}.json";`, `const ${name} = ${JSON.stringify(data)};`);
  }
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
