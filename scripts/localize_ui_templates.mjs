/** Preserve dynamic argument boundaries in display-only template strings. */
import fs from 'node:fs';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';
const protectedFiles=new Set(['AnalysisPanel','DataPanel','QuantumStudio','QuantumPanel','QuantumJourney','QuantumBlochSphere','StudioCompoundLibrary','AF3WorkflowWorkspace']);
const attributes=new Set(['title','aria-label','aria-description','aria-valuetext','placeholder','alt']);
const files=['frontend/src/App.tsx',...fs.readdirSync('frontend/src/components').filter(f=>f.endsWith('.tsx')&&!protectedFiles.has(f.slice(0,-4))).map(f=>'frontend/src/components/'+f)];
let count=0;
for(const file of files){
 const source=fs.readFileSync(file,'utf8'), tree=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let used=false;
 function display(node){for(let p=node.parent;p;p=p.parent){if(ts.isJsxAttribute(p))return attributes.has(p.name.getText(tree));if(ts.isJsxExpression(p)&&[ts.SyntaxKind.JsxElement,ts.SyntaxKind.JsxFragment].includes(p.parent.kind))return true;}return false;}
 function key(node){return node.head.text+node.templateSpans.map((s,i)=>`{${i}}`+s.literal.text).join('');}
 function render(node){
  if(ts.isTemplateExpression(node)&&/[가-힣]/.test(key(node))&&display(node)){used=true;count++;return `msg(${JSON.stringify(key(node))}, ${node.templateSpans.map(s=>render(s.expression)).join(', ')})`;}
  const edits=[];ts.forEachChild(node,child=>{const value=render(child);if(value!==child.getText(tree))edits.push({start:child.getStart(tree)-node.getStart(tree),end:child.end-node.getStart(tree),value});});
  let value=node.getText(tree);for(const edit of edits.sort((a,b)=>b.start-a.start))value=value.slice(0,edit.start)+edit.value+value.slice(edit.end);return value;
 }
 let output=render(tree);if(used){output=output.replace(/import \{ ([^}]+) \} from "([^"\n]*\/i18n)";/,(_,names,from)=>`import { ${names.includes('msg')?names:names+', msg'} } from "${from}";`);fs.writeFileSync(file,output+'\n');}
}console.log('Explicit display templates:',count);
