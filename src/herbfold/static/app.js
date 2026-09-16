'use strict';
const $ = id => document.getElementById(id);
const state = { catalog: [], selected: new Set(), discovery: null, af3Job: null, token: '', descriptors: {}, quantumJob: null };
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (n,d=2) => typeof n === 'number' ? n.toFixed(d) : '—';
const pretty = obj => `<details class="json-toggle"><summary>전체 결과 JSON</summary><pre>${esc(JSON.stringify(obj,null,2))}</pre></details>`;
function notify(message,error=false){const el=$('notice');el.textContent=message;el.className=error?'error':'';el.hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>el.hidden=true,error?12000:5000);}
async function api(path,body,options={}){
  const headers = {...(body!==undefined?{'Content-Type':'application/json'}:{}),...(state.token?{'Authorization':`Bearer ${state.token}`}:{})};
  const response = await fetch('/api'+path,{method:body!==undefined?'POST':'GET',headers,body:body===undefined?undefined:JSON.stringify(body),...options});
  if(!response.ok){let data;try{data=await response.json()}catch{data={detail:response.statusText}};throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));}
  return response.json();
}
function on(id,fn){$(id).addEventListener('click',async()=>{const el=$(id);el.disabled=true;try{await fn()}catch(error){notify(error.message,true)}finally{el.disabled=false}})}
function tab(id){document.querySelectorAll('.page').forEach(e=>e.classList.toggle('active',e.id===id));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.tab===id));$('page-name').textContent=document.querySelector(`.nav[data-tab="${id}"]`).textContent.replace(/\d+/g,'').trim();window.scrollTo(0,0);if(id==='jobs')loadJobs().catch(e=>notify(e.message,true));}
document.querySelectorAll('.nav').forEach(button=>button.addEventListener('click',()=>tab(button.dataset.tab)));
function download(data,name){const url=URL.createObjectURL(new Blob([typeof data==='string'?data:JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function artifact(jobId,path){const response=await fetch(`/api/jobs/${jobId}/artifacts/${path}`,{headers:state.token?{'Authorization':`Bearer ${state.token}`}:{}});if(!response.ok)throw Error('파일을 불러오지 못했습니다.');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=path.split('/').pop();a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function moleculeImage(img,smiles){try{const response=await fetch('/api/molecules/svg',{method:'POST',headers:{'Content-Type':'application/json',...(state.token?{'Authorization':`Bearer ${state.token}`}:{})},body:JSON.stringify({smiles})});if(response.ok){const url=URL.createObjectURL(await response.blob());img.onload=()=>URL.revokeObjectURL(url);img.src=url}}catch{img.alt='분자 구조 로드 실패'}}
function selectionCount(){$('selected-count').textContent=`${state.selected.size}개 선택됨`}
function card(molecule,generated=false){
  const d=molecule.descriptors||state.descriptors[molecule.id]||{};
  const el=document.createElement('article');el.className='compound-card'+(state.selected.has(molecule.id)?' selected':'');
  const herbal=molecule.category==='herbal';
  el.innerHTML=`<div class="card-top"><span class="tag ${!herbal&&!generated?'drug':''}">${generated?'GENERATED CANDIDATE':herbal?'NATURAL PRODUCT':'DRUG REFERENCE'}</span>${generated?'':`<input type="checkbox" aria-label="${esc(molecule.name_ko||molecule.name)} 선택" ${state.selected.has(molecule.id)?'checked':''}>`}</div><img class="mol-image" alt="${esc(molecule.name||molecule.id)} 분자 구조"><h3>${esc(molecule.name_ko||molecule.name||molecule.id)}</h3><span class="name-en">${esc(molecule.name||'BRICS recombination')}</span><div class="card-data"><div><span>MW</span><b>${fmt(d.molecular_weight,1)}</b></div><div><span>LogP</span><b>${fmt(d.logp,1)}</b></div><div><span>QED</span><b>${fmt(d.qed)}</b></div></div><div class="card-links">${molecule.source_url?`<a href="${esc(molecule.source_url)}" target="_blank" rel="noreferrer">PubChem ↗</a>`:`<span>입력 대비 새 구조</span>`}<button>AF3 입력 →</button></div>${generated?`<p class="source-note">Parent similarity ${fmt(molecule.max_parent_similarity)} · ${d.alerts?.length||0} structure alerts</p>`:''}`;
  if(molecule.botanical_examples?.length){const note=document.createElement('p');note.className='source-note';note.textContent=molecule.botanical_examples.map(b=>b.herb_name_ko||b.herb_ko||b.botanical_name||b.species||'기원 정보').join(' · ');el.append(note);}
  moleculeImage(el.querySelector('img'),molecule.smiles);
  el.querySelector('input')?.addEventListener('change',e=>{e.target.checked?state.selected.add(molecule.id):state.selected.delete(molecule.id);el.classList.toggle('selected',e.target.checked);selectionCount()});
  el.querySelector('button').addEventListener('click',()=>{$('ligand-smiles').value=molecule.smiles;$('af3-name').value=molecule.id.replace(/[^A-Za-z0-9_.-]/g,'_');tab('structure')});
  return el;
}
async function loadCatalog(){state.catalog=await api('/catalog');await Promise.all(state.catalog.map(async m=>state.descriptors[m.id]=await api('/molecules/describe',{smiles:m.smiles})));if(!state.selected.size){state.selected.add(state.catalog.find(m=>m.category==='herbal').id);state.selected.add(state.catalog.find(m=>m.category==='drug').id)}renderCatalog();$('catalog-count').textContent=state.catalog.length;}
function renderCatalog(){$('catalog').replaceChildren(...state.catalog.map(m=>card(m)));selectionCount()}
async function health(){const data=await api('/health');const af=data.alphafold;$('af3-version').textContent=`Official v${af.expected_version||af.version||'3.0.4'}`;$('af3-status').innerHTML=`<span class="state ${af.ready?'ready':'blocked'}">${af.ready?'실행 준비됨':'설정 확인 필요'}</span><p class="helper">${esc((af.blockers||[]).join(' · '))}</p>${pretty(af)}`;}
on('auth-button',async()=>{state.token=prompt('서버의 HERBFOLD_API_TOKEN 값을 입력하세요. 이 탭 메모리에만 보관합니다.')||'';await Promise.all([health(),loadCatalog()]);notify('API 연결을 확인했습니다.')});
on('pubchem-search',async()=>{if(!$('pubchem-category').value)throw Error('불러올 분자의 분류를 직접 선택하세요. PubChem 구조 정보만으로 천연물 기원을 판정하지 않습니다.');const m=await api('/sources/pubchem?query='+encodeURIComponent($('pubchem-query').value));m.id='pubchem_'+m.cid;m.name_ko=m.name;m.source_url=m.source;m.category=$('pubchem-category').value;m.user_imported=true;m.category_provenance='user_declared';state.descriptors[m.id]=await api('/molecules/describe',{smiles:m.smiles});state.catalog.push(m);state.selected.add(m.id);renderCatalog();notify(`${m.name} 분자를 추가했습니다. 사용자 분자는 비교·후보 생성에 사용할 수 있습니다.`)});
on('discover-button',async()=>{
  const compounds=state.catalog.filter(m=>state.selected.has(m.id));if(compounds.length<2)throw Error('분자를 두 개 이상 선택하세요.');
  notify('구조를 비교하고 후보 분자를 계산하고 있습니다.');
  const custom=compounds.some(m=>m.user_imported);
  const job=await api('/workflows',{...(custom?{compounds}:{compound_ids:compounds.map(m=>m.id)}),max_candidates:Number($('candidate-count').value),protein_sequence:$('protein-sequence').value.replace(/\s/g,''),target_id:$('uniprot-id').value||'custom'});
  const result=job.result;state.discovery=result;
  $('discovery-results').hidden=false;
  $('comparison').innerHTML=result.comparison.map(pair=>{const sim=pair.tanimoto_similarity??pair.tanimoto??pair.similarity??0;return `<div class="result-line"><div>${esc(pair.herbal_name||pair.herbal?.name||pair.herbal_id||pair.compound_a||'천연물')} <small>×</small> ${esc(pair.drug_name||pair.drug?.name||pair.drug_id||pair.compound_b||'약물')}</div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(0,Math.min(100,sim*100))}%"></div></div><strong>${fmt(sim,3)}</strong></div>`}).join('')||'<p class="helper">비교할 천연물·약물 쌍이 없습니다.</p>';
  $('comparison').insertAdjacentHTML('beforeend',pretty(result.comparison));
  $('candidate-grid').replaceChildren(...result.candidates.map(m=>card(m,true)));
  if(!result.candidates.length)$('candidate-grid').innerHTML='<div class="panel helper">선택한 입력의 BRICS 조각으로 필터를 통과한 후보가 없습니다. 다른 부모 분자를 선택하세요.</div>';
  notify(`${result.candidates.length}개 후보 구조 생성 완료. 결합 친화도는 아직 평가하지 않았습니다.`);
});
on('export-discovery',()=>download(state.discovery,'herbfold-discovery.json'));
on('uniprot-fetch',async()=>{const target=await api('/sources/uniprot/'+encodeURIComponent($('uniprot-id').value));$('protein-sequence').value=target.sequence;$('target-info').textContent=`${target.name} · ${target.organism} · ${target.sequence.length} aa · ${target.accession}`;notify('UniProt에서 실제 단백질 서열을 가져왔습니다.')});
function showAF3(job){state.af3Job=job;$('structure-viewer-panel').hidden=true;$('af3-result').hidden=false;const result=job.result||{};$('af3-result').innerHTML=`<div class="section-title compact"><h2>작업 ${esc(job.id.slice(0,10))}</h2><span class="state ${esc(job.status)}">${esc(job.status)}</span></div>${job.error?`<p class="helper">${esc(job.error)}</p>`:''}${result.blockers?.length?`<p class="helper">${esc(result.blockers.join(' · '))}</p>`:''}<div class="inline"><button id="af3-download-input" class="secondary">입력 JSON ↓</button><button id="af3-execute" class="primary" ${!result.runnable||job.status!=='prepared'?'disabled':''}>실제 AF3 실행 →</button><button id="af3-refresh" class="secondary">상태 확인</button></div>${(result.models||[]).map(m=>`<div class="result-line"><div>${esc(m.name||m.structure_path||'AF3 model')}<br><small>ipTM ${fmt(m.metrics?.iptm,3)} · pTM ${fmt(m.metrics?.ptm,3)} · 구조 신뢰도 ${m.metrics?.has_clash?'· ⚠ 충돌 감지':''}</small></div></div>`).join('')}${pretty(result)}`;
  if(result.models?.length){$('af3-result').insertAdjacentHTML('beforeend',result.models.map((m,i)=>`<button class="secondary top-gap" data-model-index="${i}">모델 ${i+1} · 3D 보기</button>`).join(''));document.querySelectorAll('[data-model-index]').forEach(el=>el.addEventListener('click',()=>viewStructure(job,result.models[Number(el.dataset.modelIndex)]).catch(e=>notify(e.message,true))));}
  on('af3-download-input',()=>artifact(job.id,'fold_input.json'));on('af3-execute',async()=>showAF3(await api('/af3/'+job.id+'/execute',{})));on('af3-refresh',async()=>showAF3(await api('/jobs/'+job.id)));
}
on('af3-prepare',async()=>{const seeds=$('af3-seeds').value.split(',').map(x=>Number(x.trim()));showAF3(await api('/af3/prepare',{name:$('af3-name').value,proteins:[{id:'A',sequence:$('protein-sequence').value}],ligands:[{id:'B',smiles:$('ligand-smiles').value}],seeds,msa_mode:$('msa-mode').value}));notify('AF3 입력과 실행 계획을 저장했습니다.')});
on('af3-import',async()=>{const files={};for(const file of $('af3-files').files)files[file.name]=await file.text();showAF3(await api('/af3/import',{files}))});
on('backends-fetch',async()=>{const result=await api('/quantum/backends');const backend=(result.backends||[]).find(b=>b.name===result.selected_max_backend);$('backend-status').innerHTML=backend?`<div class="badge-large">${backend.usable_qubits||backend.num_qubits} <small>available qubits</small></div><strong>${esc(backend.name)}</strong><p class="helper">${(result.backends||[]).map(b=>`${esc(b.name)} · ${b.usable_qubits} q`).join('<br>')}</p>${pretty(result)}`:`<p>${esc(result.status)}</p>${pretty(result)}`;});
$('quantum-mode').addEventListener('change',()=>{$('quantum-cost').textContent=$('quantum-mode').value==='ibm'?'실제 IBM QPU에 제출합니다. 최대 64회로 / 총 65,536 shots / 4작업. 초 단위 한도는 작업당 QPU 한도이며 전체 비용 상한이 아닙니다. 계정 잔여 할당량을 확인하세요.':'로컬 예시는 입력한 수치로 계산한 커널입니다. 결합 친화도 예측값이 아닙니다.';});
on('quantum-from-selection',()=>{const molecules=state.catalog.filter(m=>state.selected.has(m.id));$('quantum-features').value=JSON.stringify(molecules.map(m=>{const d=state.descriptors[m.id];return [d.molecular_weight/500,d.logp/5,d.tpsa/150,d.hbd/5,d.hba/10,d.qed]}),null,2);notify('MW/500, LogP/5, TPSA/150, HBD/5, HBA/10, QED 특징을 입력했습니다.')});
function quantumRequest(){return {kernel_method:'projected',features:JSON.parse($('quantum-features').value),mode:$('quantum-mode').value,qubits:$('quantum-mode').value==='ibm'?'max':4,shots:Number($('quantum-shots').value),max_execution_time:Number($('quantum-time').value),max_circuits:Number($('quantum-circuits').value),max_total_shots:Number($('quantum-total-shots').value),max_jobs:16}}
function quantumValue(value){if(typeof value!=='number'||!Number.isFinite(value))return '—';if(value===0)return '0';return Math.abs(value)<.0001||Math.abs(value)>=1e6?value.toExponential(4):Number(value.toPrecision(6)).toString();}
function showQuantum(result,job=null){
  $('quantum-result').hidden=false;
  const raw=result.kernel;
  const kernel=Array.isArray(raw)&&raw.length&&raw.every(row=>Array.isArray(row)&&row.length===raw.length&&row.every(Number.isFinite))?raw:null;
  const method=result.kernel_method||result.plan?.kernel_method||result.metadata?.kernel_method||'fidelity';
  const projected=method==='projected';
  const collapsed=!projected&&kernel&&kernel.every((row,i)=>row[i]===0);
  const measured=result.hardware_executed===true;
  const mode=result.mode||result.plan?.mode||result.metadata?.mode||job?.payload?.mode;
  const origin=measured&&mode==='ibm'?(projected?'IBM 관측량으로 계산한 커널':'IBM 전역 반환 확률'):mode==='local'?'로컬 이상적 계산':'실행 근거 미확인';
  const observations=(result.jobs||[]).flatMap(item=>item.observations||[]).filter(row=>row.pair&&row.zero_counts!==undefined);
  $('quantum-result').innerHTML=`<h2>${kernel?(projected?'관측량 기반 분자 특징 커널':'전역 Fidelity kernel'):'실행 계획 · 상태'}</h2><p class="helper">${esc(origin)} · ${projected?'X·Y·Z 기대값의 거리로 계산합니다. 대각선 1은 RBF 정의값이며 자기 충실도의 실측값이 아닙니다.':'원시 all-zero 반환 확률과 대각선 측정을 보존합니다.'} 친화도·약효·안전성이나 양자 우위를 검증한 값이 아닙니다.</p>${collapsed?'<p class="helper">⚠ 동일 입력의 대각선까지 0으로 관측되어 이 전역 측정으로 분자 차이를 구분하지 못했습니다. 잡음·회로 폭·shots의 한계를 검토해야 하며, 약효가 0이라는 뜻은 아닙니다.</p>':''}${kernel?`<div class="kernel" style="grid-template-columns:repeat(${kernel.length},1fr)">${kernel.flatMap((row,i)=>row.map((v,j)=>`<div title="${esc(`분자 ${i+1} × ${j+1}: ${quantumValue(v)}`)}" style="background:rgba(126,158,80,${.08+.8*Math.max(0,Math.min(1,v))})">${quantumValue(v)}${projected&&i===j?'<small> · 정의값</small>':''}</div>`)).join('')}</div>`:''}${observations.length?`<details><summary>실제 관측 횟수·Wilson 95% 구간</summary>${observations.map(row=>`<p>분자 ${row.pair[0]+1} × ${row.pair[1]+1}: ${quantumValue(row.zero_counts)} / ${quantumValue(row.shots)} shots · [${(row.wilson_95||[]).map(quantumValue).join(', ')}]</p>`).join('')}</details>`:''}${job?.status==='submitted'||job?.status==='running'?'<button id="quantum-refresh" class="secondary">IBM 결과 새로고침</button>':''}${pretty(result)}`;
  if($('quantum-refresh'))on('quantum-refresh',async()=>{const next=await api('/quantum/'+job.id+'/refresh',{});showQuantum(next.result,next)});
}

on('quantum-plan',async()=>showQuantum(await api('/quantum/plan',quantumRequest())));
on('quantum-run',async()=>{notify('커널 작업을 처리하고 있습니다.');const job=await api('/quantum/run',quantumRequest());state.quantumJob=job;$('kernel-job-id').value=job.id;showQuantum(job.result,job);notify(job.status==='completed'?'로컬 커널 계산 완료':'IBM 작업이 제출되었습니다. 실행 기록에서 확인할 수 있습니다.')});
on('chembl-fetch',async()=>{const result=await api(`/sources/chembl/${encodeURIComponent($('chembl-target').value)}?endpoint=${$('endpoint').value}&limit=100`);$('records-json').value=JSON.stringify(result.records,null,2);notify(`실측 ${result.records.length}개를 가져왔습니다. 중복 및 assay 비교 가능성을 확인한 후 평가하세요.`)});
$('csv-file').addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;const result=await api('/data/csv',undefined,{method:'POST',headers:{'Content-Type':'text/csv',...(state.token?{'Authorization':`Bearer ${state.token}`}:{})},body:await file.text()});$('records-json').value=JSON.stringify(result.records,null,2);notify(`${result.records.length}개 CSV 레코드 로드 완료`)}catch(error){notify(error.message,true)}});
function recordsRequest(){return {records:JSON.parse($('records-json').value),endpoint:$('endpoint').value,split:$('split-mode').value}}
function showValidation(data,prediction=false){$('validation-result').hidden=false;$('validation-result').innerHTML=`<h2>${prediction?'모델 예측 결과 · 실측 아님':'실측 데이터 평가 결과'}</h2><pre>`+esc(JSON.stringify(data,null,2))+'</pre>';}
on('evaluate',async()=>{const job=await api('/benchmark/evaluate',recordsRequest());showValidation(job.result);notify('보류 그룹에서 성능 평가를 완료했습니다.')});
on('train',async()=>{const job=await api('/models/train',recordsRequest());$('model-id').value=job.id;showValidation(job.result);notify('평가와 모델 저장을 완료했습니다.')});
on('predict',async()=>showValidation(await api('/models/predict',{model_id:$('model-id').value,queries:JSON.parse($('queries-json').value)}),true));
async function loadJobs(){const jobs=await api('/jobs');$('jobs-list').innerHTML=jobs.length?`<table><thead><tr><th>JOB</th><th>WORKFLOW</th><th>STATUS</th><th>CREATED</th><th></th></tr></thead><tbody>${jobs.map(j=>`<tr><td>${esc(j.id.slice(0,10))}</td><td>${esc(j.kind)}</td><td><span class="state ${esc(j.status)}">${esc(j.status)}</span></td><td>${esc(new Date(j.created).toLocaleString('ko-KR'))}</td><td><button data-job="${j.id}">결과 보기 ↗</button></td></tr>`).join('')}</tbody></table>`:'<div class="empty">아직 저장된 실행이 없습니다. 첫 분자 탐색을 시작하세요.</div>';document.querySelectorAll('[data-job]').forEach(el=>el.addEventListener('click',async()=>{try{const job=await api('/jobs/'+el.dataset.job);if(job.kind==='alphafold'||job.kind==='af3_import'||job.kind==='alphafold_smoke'){tab('structure');showAF3(job)}else if(job.kind==='quantum'){tab('quantum');showQuantum(job.result,job)}else{download(job,'herbfold-'+job.id.slice(0,10)+'.json')}}catch(e){notify(e.message,true)}}));}
on('jobs-refresh',loadJobs);
Promise.all([health(),loadCatalog()]).catch(error=>notify(error.message,true));

async function viewStructure(job,model){
  if(!model.structure_path)throw Error('이 모델에 mmCIF 파일이 없습니다.');
  const path='output/'+model.structure_path;
  const response=await fetch(`/api/jobs/${job.id}/artifacts/${path}`,{headers:state.token?{'Authorization':`Bearer ${state.token}`}:{}});
  if(!response.ok)throw Error('구조 파일을 불러오지 못했습니다.');
  const content=await response.text();$('structure-viewer-panel').hidden=false;$('structure-quality').textContent=(model.metrics?.has_clash||model.metrics?.iptm==null||model.metrics.iptm<.6)?'품질 점검 불합격 · 충돌 또는 낮은 구조 신뢰도로 학습 연결에서 제외됩니다. 원자 좌표는 그대로 표시합니다.':'구조 신뢰도는 결합 친화도가 아닙니다. 잔기·리간드 배치와 생물학적 맥락을 검토하세요.';$('structure-viewer').replaceChildren();
  const viewer=$3Dmol.createViewer($('structure-viewer'),{backgroundColor:'#fbfcf8'});viewer.addModel(content,'cif');viewer.setStyle({},{cartoon:{color:'#789e63'}});viewer.addStyle({atom:'CA'},{sphere:{radius:.5,color:'#789e63'}});viewer.setStyle({hetflag:true},{stick:{radius:.22},sphere:{radius:.45}});viewer.zoomTo();viewer.render();
  state.structure={jobId:job.id,path};state.viewer=viewer;
  $('structure-viewer-panel').scrollIntoView({behavior:'smooth'});
}
on('pocket-features',async()=>{if(!state.structure)return;const result=await api(`/af3/${state.structure.jobId}/pocket?file=${encodeURIComponent(state.structure.path)}&ligand_chain=B`);$('pocket-result').hidden=false;$('pocket-result').textContent=JSON.stringify(result,null,2)});
on('kernel-evaluate',async()=>{notify('실측 데이터에서 양자 커널과 고전 커널을 같은 분할로 비교하고 있습니다.');const job=await api('/quantum/experiments/local',recordsRequest());showValidation(job.result.evaluation);});
on('kernel-prepare',async()=>{const job=await api('/quantum/experiments/prepare',recordsRequest());state.kernelExperiment=job;$('kernel-experiment-id').value=job.id;const n=job.result.features.length;const requiredCircuits=3*n+5;const requiredShots=requiredCircuits*Number($('quantum-shots').value);const budgetInsufficient=requiredCircuits>Number($('quantum-circuits').value)||requiredShots>Number($('quantum-total-shots').value);$('quantum-features').value=JSON.stringify(job.result.features,null,2);$('quantum-mode').value='ibm';$('quantum-mode').dispatchEvent(new Event('change'));showValidation(job.result);download(job,'kernel-experiment-'+job.id+'.json');notify(`분할과 특징을 저장했습니다. 관측량 기반 projected 계산에는 ${requiredCircuits}회로 / ${requiredShots.toLocaleString()} shots가 필요합니다. ${budgetInsufficient?'현재 입력한 예산이 부족합니다. 상한을 검토하세요.':'현재 입력한 회로·shot 예산 안에 있습니다.'} 입력된 예산은 변경하지 않았습니다.`);});

on('audit-records',async()=>{state.curation=await api('/data/audit',recordsRequest());showValidation(state.curation);$('apply-curation').hidden=false;notify('원본을 보존한 점검 보고서를 생성했습니다. 제안 레코드를 검토한 후 적용할 수 있습니다.');});
on('apply-curation',()=>{if(!state.curation)return;$('records-json').value=JSON.stringify(state.curation.suggested_records,null,2);$('apply-curation').hidden=true;notify('모호한 중복 그룹을 제외한 제안을 적용했습니다. assay 간 비교 가능성은 연구자가 검토해야 합니다.');});
on('kernel-finish',async()=>{const job=await api('/quantum/experiments/'+$('kernel-experiment-id').value+'/evaluate',{quantum_job_id:$('kernel-job-id').value});showQuantum(job.result);});

on('attach-structures',async()=>{const result=await api('/data/structures',recordsRequest());$('records-json').value=JSON.stringify(result.records,null,2);showValidation(result);notify('실제 AF3 좌표와 출처를 연결했습니다. 실측 라벨은 보존됩니다.');});
