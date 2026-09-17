/** One-time, idempotent React source migration: translate display boundaries only.
 * Controlled values, option identities, handlers, scientific records and request bodies remain raw.
 */
import fs from 'node:fs';
import path from 'node:path';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';
const decode = text => text.replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (raw,entity) => {
 const named={amp:'&',lt:'<',gt:'>',quot:'"',apos:"'",nbsp:'\u00a0'};
 if(entity[0]==='#'){const n=entity[1].toLowerCase()==='x'?parseInt(entity.slice(2),16):parseInt(entity.slice(1),10);return n>0&&n<=0x10ffff?String.fromCodePoint(n):raw;}
 return named[entity] ?? raw;
});
function jsxText(text){const lines=text.split(/\r\n|\n|\r/);let last=0;lines.forEach((s,i)=>{if(/[^ \t]/.test(s))last=i;});return decode(lines.map((s,i)=>{s=s.replace(/\t/g,' ');if(i)s=s.replace(/^ +/,'');if(i!==lines.length-1)s=s.replace(/ +$/,'');return s?s+(i!==last?' ':''):'';}).join(''));}
const attributes=new Set(['title','aria-label','aria-description','aria-valuetext','placeholder','alt']);
const files=['frontend/src/App.tsx',...fs.readdirSync('frontend/src/components').filter(f=>f.endsWith('.tsx')&&f!=='LanguageSwitcher.tsx').map(f=>'frontend/src/components/'+f)];
const changed=[];
for(const file of files){
 const source=fs.readFileSync(file,'utf8');const tree=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);const edits=[];let count=0;
 const wrapped=node=>ts.isCallExpression(node)&&ts.isIdentifier(node.expression)&&node.expression.text==='tr';
 function wrap(node){if(wrapped(node))return;edits.push({start:node.getStart(tree),end:node.getStart(tree),value:'tr('},{start:node.end,end:node.end,value:')'});count++;}
 function rawParent(node){for(let p=node.parent;p;p=p.parent){if(ts.isJsxElement(p)&&['pre','textarea'].includes(p.openingElement.tagName.getText(tree)))return true;if(ts.isFunctionLike(p))break;}return false;}
 function visit(node){
  if(ts.isJsxText(node)&&/[가-힣]/.test(node.text)&&!rawParent(node)){const value=jsxText(node.text);edits.push({start:node.getStart(tree),end:node.end,value:`{tr(${JSON.stringify(value)})}`});count++;}
  if(ts.isJsxExpression(node)&&node.expression&&!node.dotDotDotToken&&!rawParent(node)&&[ts.SyntaxKind.JsxElement,ts.SyntaxKind.JsxFragment].includes(node.parent.kind))wrap(node.expression);
  if(ts.isJsxAttribute(node)&&attributes.has(node.name.getText(tree))&&node.initializer){
   const value=node.initializer;
   if(ts.isStringLiteral(value)&&/[가-힣]/.test(value.text)){edits.push({start:value.getStart(tree),end:value.end,value:'{tr('+JSON.stringify(decode(value.text))+')}'});count++;}
   else if(ts.isJsxExpression(value)&&value.expression)wrap(value.expression);
  }
  if(ts.isJsxElement(node)&&node.openingElement.tagName.getText(tree)==='option'&&!node.openingElement.attributes.properties.some(p=>ts.isJsxAttribute(p)&&p.name.getText(tree)==='value')){
   const child=node.children.filter(c=>!ts.isJsxText(c)||c.text.trim());
   if(child.length===1){const text=ts.isJsxText(child[0])?JSON.stringify(jsxText(child[0].text)):ts.isJsxExpression(child[0])&&child[0].expression?'{'+child[0].expression.getText(tree)+'}':null;
    if(text){edits.push({start:node.openingElement.tagName.end,end:node.openingElement.tagName.end,value:' value='+text});count++;}
   }
  }
  ts.forEachChild(node,visit);
 }visit(tree);
 let output=source;for(const edit of edits.sort((a,b)=>b.start-a.start||b.end-a.end))output=output.slice(0,edit.start)+edit.value+output.slice(edit.end);
 const needsLocale=output.includes('"ko-KR"');if(needsLocale)output=output.replaceAll('"ko-KR"','localeCode()');
 if(count||needsLocale){
  const prefix=file.endsWith('/App.tsx')?'./lib/i18n':'../lib/i18n';
  if(!source.includes(`from "${prefix}"`))output=`import { ${[count?'tr':null,needsLocale?'localeCode':null].filter(Boolean).join(', ')} } from "${prefix}";\n`+output;
  fs.writeFileSync(file,output);changed.push({file,displayBoundaries:count,localeFormatting:needsLocale});
 }
}
fs.writeFileSync('tmp/i18n_20260917/display-migration.json',JSON.stringify(changed,null,2));console.log('Updated',changed.length,'React modules;',changed.reduce((s,r)=>s+r.displayBoundaries,0),'display boundaries.');
