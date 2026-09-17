/** Extract source-language display copy for the local, reviewed EN/KO catalog. */
import fs from 'node:fs';
import path from 'node:path';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';
const groups = {
 core: ['App', 'StudioWorkspaceHeader','StudioCompoundLibrary','ComparisonWorkspace','StructureSelection','MoleculeViewer','AlphaFoldDiagnostics','AF3CalculationPanel','AF3EvidencePanel','AF3WorkflowLauncher','AF3WorkflowWorkspace','predictionProgress','alphaFoldDiagnostics','af3WorkflowSelection','discoveryCompounds','proteinTargets'],
 research: ['QuantumStudio','QuantumPanel','QuantumJourney','QuantumBlochSphere','AnalysisPanel','ArchivePanel','DataPanel','quantumJourney','quantumEvidence'],
 design: ['DrugDesignPipeline','CombinationAssayPanel','DiscoveryPanel','ValidationPanel'],
};
export function jsxText(text) {
 const lines = text.split(/\r\n|\n|\r/); let last = 0;
 lines.forEach((line,i)=>{if (/[^ \t]/.test(line)) last=i;});
 return lines.map((line,i)=>{let s=line.replace(/\t/g,' ');if(i) s=s.replace(/^ +/,'');if(i!==lines.length-1)s=s.replace(/ +$/,'');return s?s+(i!==last?' ':''):'';}).join('');
}
const normalize=s=>s.replace(/\s+/g,' ').trim();
const files=fs.readdirSync('frontend/src/components').filter(p=>p.endsWith('.tsx')).map(p=>'frontend/src/components/'+p)
 .concat(fs.readdirSync('frontend/src/lib').filter(p=>p.endsWith('.ts')).map(p=>'frontend/src/lib/'+p),['frontend/src/App.tsx']);
fs.mkdirSync('tmp/i18n_20260917',{recursive:true});
for(const [group,names] of Object.entries(groups)) {
 const found=new Map();
 function add(text,file){text=normalize(text);if(/[가-힣]/.test(text)){if(!found.has(text))found.set(text,[]);if(!found.get(text).includes(file))found.get(text).push(file);}}
 for(const file of files.filter(file=>names.includes(path.basename(file).replace(/\.tsx?$/,'')))) {
  const source=ts.createSourceFile(file,fs.readFileSync(file,'utf8'),ts.ScriptTarget.Latest,true,file.endsWith('x')?ts.ScriptKind.TSX:ts.ScriptKind.TS);
  function visit(node){
   if(ts.isJsxText(node))add(jsxText(node.text),file);
   else if(ts.isStringLiteral(node)||ts.isNoSubstitutionTemplateLiteral(node))add(node.text,file);
   else if(ts.isTemplateExpression(node))add(node.head.text+node.templateSpans.map((span,i)=>`{${i}}`+span.literal.text).join(''),file);
   ts.forEachChild(node,visit);
  } visit(source);
 }
 fs.writeFileSync(`tmp/i18n_20260917/${group}-strings.json`,JSON.stringify([...found].map(([text,files])=>({text,files})),null,2)+'\n');
 console.log(group,found.size);
}
