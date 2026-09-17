'use strict';
const $ = id => document.getElementById(id);
const state = { catalog: [], selected: new Set(), discovery: null, af3Job: null, token: '', descriptors: {}, quantumJob: null, language: 'ko', health: null, backends: null, quantumView: null, validationView: null, structureModel: null, jobs: null };
// Display translations only (Korean source text -> English). Research data (molecule/herb names, SMILES, job IDs, sequences, JSON) is never translated.
const LANGUAGE_KEY = 'herbfold.language.v1';
const I18N = {
  // Sidebar / navigation
  '연구 메뉴': 'Research menu',
  '분자 탐색': 'Molecular discovery',
  '실측 데이터 검증': 'Measured data validation',
  '실행 기록': 'Run history',
  '구조에서 가설로.': 'From structure to hypothesis.',
  '가설에서 검증으로.': 'From hypothesis to validation.',
  'API 문서 ↗': 'API docs ↗',
  'API 토큰 설정': 'Set API token',
  '언어 선택': 'Language',
  // Discovery page
  '천연물에서 시작하는': 'New discoveries',
  '새로운 발견.': 'from natural products.',
  '한약 성분과 기존 약물을 비교하고, 새로운 후보 구조를 탐색하세요.': 'Compare herbal constituents with existing drugs and explore new candidate structures.',
  'AlphaFold 3와 양자 커널로 검증 가능한 연구 흐름을 만듭니다.': 'Build a verifiable research workflow with AlphaFold 3 and quantum kernels.',
  '출처를 기록한 분자': 'Molecules with recorded sources',
  '공식 버전 확인 중': 'Checking official version',
  '접근 가능한 최대 큐빗 자동 선택': 'Automatically selects the maximum accessible qubits',
  '실측 종말점별 분리 평가': 'Evaluated separately by measured endpoint',
  '비교할 성분을 선택하세요': 'Select compounds to compare',
  'PubChem 이름 또는 CID': 'PubChem name or CID',
  'PubChem 검색': 'PubChem search',
  '불러올 분자 분류': 'Category for the imported molecule',
  '분류 선택': 'Select category',
  '천연물 · 사용자 지정': 'Natural product · User-defined',
  '약물 · 사용자 지정': 'Drug · User-defined',
  '분자 불러오기 ↗': 'Import molecule ↗',
  '0개 선택됨': '0 selected',
  '{0}개 선택됨': '{0} selected',
  '천연물과 기존 약물을 각각 하나 이상 선택하세요.': 'Select at least one natural product and one existing drug.',
  '후보 수': 'Candidate count',
  '비교 · 후보 생성': 'Compare · Generate candidates',
  '구조 유사도와 후보 분자': 'Structural similarity and candidate molecules',
  '연구 결과 JSON ↓': 'Results JSON ↓',
  'Tanimoto는 구조 유사도입니다. 생성된 후보의 신규성은 선택한 입력 분자에 대한 것이며, 결합력·합성 가능성·특허 신규성을 보장하지 않습니다.': 'Tanimoto is a structural similarity. The novelty of generated candidates is relative to the selected input molecules and does not guarantee binding, synthetic feasibility or patent novelty.',
  // Structure page
  '단백질과 리간드를 함께 예측합니다. 구조 신뢰도와 결합 친화도는 별도로 평가합니다.': 'Predicts the protein and ligand together. Structure confidence and binding affinity are evaluated separately.',
  '복합체 입력': 'Complex input',
  '실험 이름': 'Experiment name',
  '서열 불러오기': 'Fetch sequence',
  '단백질 서열 · Chain A': 'Protein sequence · Chain A',
  '아미노산 서열을 입력하거나 UniProt에서 불러오세요.': 'Enter an amino-acid sequence or fetch one from UniProt.',
  '리간드 SMILES · Chain B': 'Ligand SMILES · Chain B',
  '분자 카드에서 AF3 입력을 선택할 수 있습니다.': 'Select AF3 input from a molecule card.',
  'MSA / 템플릿': 'MSA / templates',
  '공식 데이터베이스 검색': 'Official database search',
  'MSA·템플릿 없이 실행 (탐색용)': 'Run without MSA or templates (exploratory)',
  '모델 시드': 'Model seeds',
  '입력 검증 · 실행 준비 →': 'Validate input · Prepare run →',
  '실행 환경': 'Execution environment',
  '확인 중…': 'Checking…',
  '모델 파라미터는 공식 접근 승인이 필요합니다. 상업 목적 사용 전 파라미터와 출력 이용약관을 확인하세요.': 'Model parameters require official access approval. Review the parameter and output terms of use before any commercial use.',
  '외부 AF3 결과 가져오기': 'Import external AF3 results',
  '공식 *_model.cif와 *_summary_confidences.json을 함께 선택하세요.': 'Select the official *_model.cif and *_summary_confidences.json files together.',
  '결과 가져오기': 'Import results',
  '복합체 3D 구조': 'Complex 3D structure',
  '결합 부위 특징 추출': 'Extract binding-site features',
  '드래그로 회전 · 스크롤로 확대. 초록색 단백질과 원소별 색상의 리간드.': 'Drag to rotate · Scroll to zoom. Protein in green, ligand colored by element.',
  // Quantum page
  '분자 특징을': 'Molecular features',
  '양자 상태로.': 'as quantum states.',
  '접근 가능한 정상 장비의 최대 큐빗을 사용합니다. 큐빗 수와 예측 성능의 관계는 실측 평가가 필요합니다.': 'Uses the maximum qubits of an accessible operational device. The relationship between qubit count and predictive performance requires measured evaluation.',
  '실행 구성': 'Run configuration',
  '로컬 statevector · 4 qubits': 'Local statevector · 4 qubits',
  'IBM 실제 QPU · 최대 사용 가능 큐빗': 'Actual IBM QPU · Maximum available qubits',
  '작업당 최대 실행 시간 (초)': 'Maximum run time per job (s)',
  '최대 회로 수': 'Maximum circuits',
  '전체 shots 한도': 'Total shots limit',
  '특징 행렬 · 행 하나당 분자 하나': 'Feature matrix · One molecule per row',
  '선택 분자의 물성 특징으로 채우기 ↗': 'Fill from property features of selected molecules ↗',
  '큐빗 · 실행 예산 확인': 'Check qubits · Run budget',
  '커널 계산 →': 'Compute kernel →',
  '로컬 예시는 입력한 수치로 계산한 커널입니다. 결합 친화도 예측값이 아닙니다.': 'The local example is a kernel computed from the entered values. It is not a binding affinity prediction.',
  '실제 IBM QPU에 제출합니다. 최대 64회로 / 총 65,536 shots / 4작업. 초 단위 한도는 작업당 QPU 한도이며 전체 비용 상한이 아닙니다. 계정 잔여 할당량을 확인하세요.': 'Submits to an actual IBM QPU. Up to 64 circuits / 65,536 total shots / 4 jobs. The limit in seconds is a per-job QPU limit, not an overall cost cap. Check the remaining quota of your account.',
  'IBM 장비': 'IBM devices',
  '연결 확인': 'Check connection',
  '연결 확인으로 계정에서 사용 가능한 장비와 최대 큐빗을 조회합니다.': 'Check connection to look up the devices and maximum qubits available to your account.',
  'IBM 커널의 실측 평가': 'Measured evaluation of IBM kernels',
  '준비된 실험 ID': 'Prepared experiment ID',
  '검증 화면에서 IBM 실험 준비': 'Prepare an IBM experiment in the validation view',
  '완료된 커널 작업 ID': 'Completed kernel job ID',
  'IBM 결과 수신 후 작업 ID': 'Job ID after IBM results are received',
  '보류 데이터에서 양자 · RBF 비교': 'Quantum · RBF comparison on held-out data',
  // Validation page
  '예측을': 'Predictions',
  '검증 가능한 결과로.': 'into verifiable results.',
  '실측 Kd 또는 Ki만 사용합니다. Scaffold/target 분할, 중복 검사와 평가 기록을 제공합니다.': 'Uses measured Kd or Ki only. Provides scaffold/target splits, duplicate checks and evaluation records.',
  '데이터 불러오기': 'Load data',
  '종말점': 'Endpoint',
  'ChEMBL 실측 데이터 가져오기': 'Import ChEMBL measured data',
  '또는 CSV 가져오기': 'or import CSV',
  '필수 열: smiles, target_id, endpoint, value, unit, relation, is_measured, source. relation은 =, is_measured는 true. Kd와 Ki를 섞지 않습니다.': 'Required columns: smiles, target_id, endpoint, value, unit, relation, is_measured, source. relation must be = and is_measured must be true. Do not mix Kd and Ki.',
  '레코드 JSON': 'Records JSON',
  '검증된 AF3 구조 특징 연결 ↗': 'Link validated AF3 structure features ↗',
  '구조 연결 시 각 레코드에 af3_job_id와 정확한 protein_sequence를 지정하세요. 분자·서열·해시와 구조 품질을 확인합니다.': 'To link structures, set af3_job_id and the exact protein_sequence on each record. Molecule, sequence, hash and structure quality are checked.',
  '중복 · assay · 실측 라벨 점검': 'Check duplicates · assays · measured labels',
  '점검된 제안 레코드를 적용 ↗': 'Apply audited suggested records ↗',
  '평가 분할': 'Evaluation split',
  'Scaffold 분리 · 단일 표적 연구': 'Scaffold split · Single-target study',
  'Scaffold + target 동시 분리': 'Scaffold + target split',
  'Target 분리': 'Target split',
  '평가만 실행': 'Evaluate only',
  '평가 · 모델 학습 →': 'Evaluate · Train model →',
  '결합 친화도 모델': 'Binding affinity model',
  '물성·분자 지문·표적 정보를 사용하는 Ridge 기준 모델입니다. 실측 라벨 없이 결합 친화도를 생성하지 않습니다. 동일 스키마의 실제 구조 특징을 추가해 AF3 기반 모델을 비교할 수 있습니다.': 'A Ridge baseline model using properties, molecular fingerprints and target information. It does not generate binding affinity without measured labels. Add actual structure features with the same schema to compare an AF3-based model.',
  '학습된 모델 ID': 'Trained model ID',
  '학습 후 자동 입력': 'Filled automatically after training',
  '예측 질의 JSON': 'Prediction queries JSON',
  '실측 학습 모델로 예측 →': 'Predict with the measured-data model →',
  '양자 · 고전 모델 비교': 'Quantum · Classical model comparison',
  '실측 8–24개를 같은 분할로 평가합니다. 선택적 AF3 구조 특징을 포함할 수 있습니다. 아래 버튼은 로컬 4큐빗 계산입니다.': 'Evaluates 8–24 measurements on the same split. Optional AF3 structure features can be included. The button below runs a local 4-qubit calculation.',
  '실측 데이터로 양자 · RBF 비교': 'Quantum · RBF comparison on measured data',
  'IBM 실험용 특징 · 분할 준비 ↗': 'Prepare features · split for an IBM experiment ↗',
  // Jobs page / footer
  '실행': 'Run',
  '기록.': 'history.',
  '새로고침 ↻': 'Refresh ↻',
  '입력·실행 상태·결과 파일이 로컬 작업별로 보존됩니다.': 'Inputs, run status and result files are preserved per local job.',
  '후보 탐색 → 구조 예측 → 실측 검증': 'Candidate discovery → Structure prediction → Measured validation',
  // Dynamic messages (app.js)
  '전체 결과 JSON': 'Full results JSON',
  '파일을 불러오지 못했습니다.': 'Could not load the file.',
  '분자 구조 로드 실패': 'Molecular structure failed to load',
  '{0} 선택': 'Select {0}',
  '{0} 분자 구조': '{0} molecular structure',
  '입력 대비 새 구조': 'New structure relative to inputs',
  'AF3 입력 →': 'AF3 input →',
  '기원 정보': 'Origin record',
  '실행 준비됨': 'Ready to run',
  '설정 확인 필요': 'Configuration required',
  '서버의 HERBFOLD_API_TOKEN 값을 입력하세요. 이 탭 메모리에만 보관합니다.': 'Enter the HERBFOLD_API_TOKEN value of the server. It is kept only in the memory of this tab.',
  'API 연결을 확인했습니다.': 'API connection confirmed.',
  '불러올 분자의 분류를 직접 선택하세요. PubChem 구조 정보만으로 천연물 기원을 판정하지 않습니다.': 'Select a category for the imported molecule. PubChem structure data alone does not establish natural-product origin.',
  '{0} 분자를 추가했습니다. 사용자 분자는 비교·후보 생성에 사용할 수 있습니다.': 'Added {0}. User-imported molecules can be used for comparison and candidate generation.',
  '분자를 두 개 이상 선택하세요.': 'Select at least two molecules.',
  '구조를 비교하고 후보 분자를 계산하고 있습니다.': 'Comparing structures and computing candidate molecules.',
  '천연물': 'Natural product',
  '약물': 'Drug',
  '비교할 천연물·약물 쌍이 없습니다.': 'No natural product–drug pairs to compare.',
  '선택한 입력의 BRICS 조각으로 필터를 통과한 후보가 없습니다. 다른 부모 분자를 선택하세요.': 'No candidates from the BRICS fragments of the selected inputs passed the filters. Select different parent molecules.',
  '{0}개 후보 구조 생성 완료. 결합 친화도는 아직 평가하지 않았습니다.': 'Generated {0} candidate structures. Binding affinity has not been evaluated yet.',
  'UniProt에서 실제 단백질 서열을 가져왔습니다.': 'Retrieved the actual protein sequence from UniProt.',
  '작업 {0}': 'Job {0}',
  '입력 JSON ↓': 'Input JSON ↓',
  '실제 AF3 실행 →': 'Run actual AF3 →',
  '상태 확인': 'Check status',
  'ipTM {0} · pTM {1} · 구조 신뢰도': 'ipTM {0} · pTM {1} · Structure confidence',
  '· ⚠ 충돌 감지': '· ⚠ Clash detected',
  '모델 {0} · 3D 보기': 'Model {0} · View 3D',
  'AF3 입력과 실행 계획을 저장했습니다.': 'Saved the AF3 input and execution plan.',
  'MW/500, LogP/5, TPSA/150, HBD/5, HBA/10, QED 특징을 입력했습니다.': 'Filled in MW/500, LogP/5, TPSA/150, HBD/5, HBA/10 and QED features.',
  'IBM 관측량으로 계산한 커널': 'Kernel computed from IBM observables',
  'IBM 전역 반환 확률': 'IBM global return probability',
  '로컬 이상적 계산': 'Local ideal calculation',
  '실행 근거 미확인': 'Execution evidence unconfirmed',
  '관측량 기반 분자 특징 커널': 'Observable-based molecular feature kernel',
  '전역 Fidelity kernel': 'Global fidelity kernel',
  '실행 계획 · 상태': 'Execution plan · Status',
  'X·Y·Z 기대값의 거리로 계산합니다. 대각선 1은 RBF 정의값이며 자기 충실도의 실측값이 아닙니다.': 'Computed from the distance between X·Y·Z expectation values. The diagonal is 1 by the RBF definition, not a measured self-fidelity.',
  '원시 all-zero 반환 확률과 대각선 측정을 보존합니다.': 'Raw all-zero return probabilities and diagonal measurements are preserved.',
  '친화도·약효·안전성이나 양자 우위를 검증한 값이 아닙니다.': 'These values do not validate affinity, efficacy, safety or quantum advantage.',
  '⚠ 동일 입력의 대각선까지 0으로 관측되어 이 전역 측정으로 분자 차이를 구분하지 못했습니다. 잡음·회로 폭·shots의 한계를 검토해야 하며, 약효가 0이라는 뜻은 아닙니다.': '⚠ Even the diagonal for identical inputs was observed as 0, so this global measurement could not distinguish the molecules. Review the limits of noise, circuit width and shots; this does not mean efficacy is zero.',
  '분자 {0} × {1}: {2}': 'Molecule {0} × {1}: {2}',
  '· 정의값': '· Defined value',
  '실제 관측 횟수·Wilson 95% 구간': 'Actual observation counts · Wilson 95% interval',
  '분자 {0} × {1}: {2} / {3} shots · [{4}]': 'Molecule {0} × {1}: {2} / {3} shots · [{4}]',
  'IBM 결과 새로고침': 'Refresh IBM results',
  '커널 작업을 처리하고 있습니다.': 'Processing the kernel job.',
  '로컬 커널 계산 완료': 'Local kernel calculation complete',
  'IBM 작업이 제출되었습니다. 실행 기록에서 확인할 수 있습니다.': 'The IBM job has been submitted. Check it in run history.',
  '실측 {0}개를 가져왔습니다. 중복 및 assay 비교 가능성을 확인한 후 평가하세요.': 'Imported {0} measurements. Check duplicates and assay comparability before evaluating.',
  '{0}개 CSV 레코드 로드 완료': 'Loaded {0} CSV records',
  '모델 예측 결과 · 실측 아님': 'Model predictions · Not measured',
  '실측 데이터 평가 결과': 'Measured data evaluation results',
  '보류 그룹에서 성능 평가를 완료했습니다.': 'Completed performance evaluation on the held-out group.',
  '평가와 모델 저장을 완료했습니다.': 'Completed evaluation and saved the model.',
  '결과 보기 ↗': 'View results ↗',
  '아직 저장된 실행이 없습니다. 첫 분자 탐색을 시작하세요.': 'No saved runs yet. Start your first molecular discovery.',
  '이 모델에 mmCIF 파일이 없습니다.': 'This model has no mmCIF file.',
  '구조 파일을 불러오지 못했습니다.': 'Could not load the structure file.',
  '품질 점검 불합격 · 충돌 또는 낮은 구조 신뢰도로 학습 연결에서 제외됩니다. 원자 좌표는 그대로 표시합니다.': 'Failed quality check · Excluded from training links because of a clash or low structure confidence. Atom coordinates are displayed as is.',
  '구조 신뢰도는 결합 친화도가 아닙니다. 잔기·리간드 배치와 생물학적 맥락을 검토하세요.': 'Structure confidence is not binding affinity. Review residue and ligand placement and the biological context.',
  '실측 데이터에서 양자 커널과 고전 커널을 같은 분할로 비교하고 있습니다.': 'Comparing quantum and classical kernels on measured data using the same split.',
  '분할과 특징을 저장했습니다. 관측량 기반 projected 계산에는 {0}회로 / {1} shots가 필요합니다. {2} 입력된 예산은 변경하지 않았습니다.': 'Saved the split and features. The observable-based projected calculation requires {0} circuits / {1} shots. {2} The entered budget was not changed.',
  '현재 입력한 예산이 부족합니다. 상한을 검토하세요.': 'The entered budget is insufficient. Review the limits.',
  '현재 입력한 회로·shot 예산 안에 있습니다.': 'Within the entered circuit and shot budget.',
  '원본을 보존한 점검 보고서를 생성했습니다. 제안 레코드를 검토한 후 적용할 수 있습니다.': 'Generated an audit report that preserves the originals. Review the suggested records before applying them.',
  '모호한 중복 그룹을 제외한 제안을 적용했습니다. assay 간 비교 가능성은 연구자가 검토해야 합니다.': 'Applied the suggestions, excluding ambiguous duplicate groups. Cross-assay comparability must be reviewed by the researcher.',
  '실제 AF3 좌표와 출처를 연결했습니다. 실측 라벨은 보존됩니다.': 'Linked actual AF3 coordinates and sources. Measured labels are preserved.'
};
/** Both recorded names stay on the card; English mode leads with the recorded English name. Records are unchanged. */
function primaryName(molecule){return state.language==='en'?(molecule.name||molecule.name_ko||molecule.id):(molecule.name_ko||molecule.name||molecule.id);}
function secondaryName(molecule){const other=state.language==='en'?molecule.name_ko:molecule.name;return other&&other!==primaryName(molecule)?other:(molecule.name?'':'BRICS recombination');}
function t(text,...values){const key=String(text).replace(/\s+/g,' ').trim();let out=state.language==='en'?(I18N[key]??text):text;if(values.length)out=String(out).replace(/\{(\d+)\}/g,(m,i)=>Number(i)<values.length?String(values[i]):m);return out;}
const locale=()=>state.language==='en'?'en-US':'ko-KR';
let memoryLanguage='ko';
function readLanguage(){try{const v=localStorage.getItem(LANGUAGE_KEY);if(v==='en'||v==='ko')return v;}catch{}return memoryLanguage;}
function writeLanguage(lang){memoryLanguage=lang;try{localStorage.setItem(LANGUAGE_KEY,lang)}catch{}}
const i18nNodes=[],i18nAttrs=[];
function captureStatic(){document.querySelectorAll('[data-i18n]').forEach(root=>{const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node;while((node=walker.nextNode()))if(/[가-힣]/.test(node.nodeValue))i18nNodes.push({node,text:node.nodeValue.trim()});[root,...root.querySelectorAll('*')].forEach(el=>['placeholder','aria-label','title','alt'].forEach(attr=>{const v=el.getAttribute(attr);if(v&&/[가-힣]/.test(v))i18nAttrs.push({el,attr,text:v});}));});}
function applyStatic(){i18nNodes.forEach(({node,text})=>{if(!node.isConnected)return;const lead=node.nodeValue.match(/^\s*/)[0],trail=node.nodeValue.match(/\s*$/)[0];node.nodeValue=lead+t(text)+trail;});i18nAttrs.forEach(({el,attr,text})=>{if(el.isConnected)el.setAttribute(attr,t(text))});}
function updatePageName(){const active=document.querySelector('.nav.active');const name=active?[...active.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.nodeValue).join('').trim():'';$('page-name').textContent=name;document.title=name?`HerbFold · ${name}`:'HerbFold';}
function renderQuantumCost(){$('quantum-cost').textContent=t($('quantum-mode').value==='ibm'?'실제 IBM QPU에 제출합니다. 최대 64회로 / 총 65,536 shots / 4작업. 초 단위 한도는 작업당 QPU 한도이며 전체 비용 상한이 아닙니다. 계정 잔여 할당량을 확인하세요.':'로컬 예시는 입력한 수치로 계산한 커널입니다. 결합 친화도 예측값이 아닙니다.');}
function renderDynamic(){selectionCount();renderQuantumCost();if(state.catalog.length)renderCatalog();if(state.discovery)renderDiscovery();if(state.health)renderHealth();if(state.af3Job)renderAF3();if(state.backends)renderBackends();if(state.quantumView)renderQuantum();if(state.validationView)renderValidation();if(state.structureModel)renderStructureQuality();if(state.jobs)renderJobs();}
function applyLanguage(){document.documentElement.lang=state.language;applyStatic();document.querySelectorAll('.lang-switch [data-lang]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.lang===state.language)));updatePageName();renderDynamic();}
function setLanguage(lang,persist=true){if(lang!=='en'&&lang!=='ko')return;if(persist)writeLanguage(lang);state.language=lang;applyLanguage();}
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (n,d=2) => typeof n === 'number' ? n.toFixed(d) : '—';
const pretty = obj => `<details class="json-toggle"><summary>${t('전체 결과 JSON')}</summary><pre>${esc(JSON.stringify(obj,null,2))}</pre></details>`;
function notify(message,error=false){const el=$('notice');el.textContent=message;el.className=error?'error':'';el.hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>el.hidden=true,error?12000:5000);}
async function api(path,body,options={}){
  const headers = {...(body!==undefined?{'Content-Type':'application/json'}:{}),...(state.token?{'Authorization':`Bearer ${state.token}`}:{})};
  const response = await fetch('/api'+path,{method:body!==undefined?'POST':'GET',headers,body:body===undefined?undefined:JSON.stringify(body),...options});
  if(!response.ok){let data;try{data=await response.json()}catch{data={detail:response.statusText}};throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));}
  return response.json();
}
function on(id,fn){$(id).addEventListener('click',async()=>{const el=$(id);el.disabled=true;try{await fn()}catch(error){notify(error.message,true)}finally{el.disabled=false}})}
function tab(id){document.querySelectorAll('.page').forEach(e=>e.classList.toggle('active',e.id===id));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.tab===id));updatePageName();window.scrollTo(0,0);if(id==='jobs')loadJobs().catch(e=>notify(e.message,true));}
document.querySelectorAll('.nav').forEach(button=>button.addEventListener('click',()=>tab(button.dataset.tab)));
function download(data,name){const url=URL.createObjectURL(new Blob([typeof data==='string'?data:JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function artifact(jobId,path){const response=await fetch(`/api/jobs/${jobId}/artifacts/${path}`,{headers:state.token?{'Authorization':`Bearer ${state.token}`}:{}});if(!response.ok)throw Error(t('파일을 불러오지 못했습니다.'));const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=path.split('/').pop();a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
const svgCache=new Map();// smiles -> object URL promise, so re-rendering cards (e.g. on a language switch) does not refetch images
function moleculeImage(img,smiles){if(!svgCache.has(smiles))svgCache.set(smiles,fetch('/api/molecules/svg',{method:'POST',headers:{'Content-Type':'application/json',...(state.token?{'Authorization':`Bearer ${state.token}`}:{})},body:JSON.stringify({smiles})}).then(async response=>response.ok?URL.createObjectURL(await response.blob()):null));svgCache.get(smiles).then(url=>{if(url)img.src=url;else svgCache.delete(smiles)}).catch(()=>{svgCache.delete(smiles);img.alt=t('분자 구조 로드 실패')});}
function selectionCount(){$('selected-count').textContent=t('{0}개 선택됨',state.selected.size)}
function card(molecule,generated=false){
  const d=molecule.descriptors||state.descriptors[molecule.id]||{};
  const el=document.createElement('article');el.className='compound-card'+(state.selected.has(molecule.id)?' selected':'');
  const herbal=molecule.category==='herbal';
  el.innerHTML=`<div class="card-top"><span class="tag ${!herbal&&!generated?'drug':''}">${generated?'GENERATED CANDIDATE':herbal?'NATURAL PRODUCT':'DRUG REFERENCE'}</span>${generated?'':`<input type="checkbox" aria-label="${esc(t('{0} 선택',primaryName(molecule)))}" ${state.selected.has(molecule.id)?'checked':''}>`}</div><img class="mol-image" alt="${esc(t('{0} 분자 구조',molecule.name||molecule.id))}"><h3>${esc(primaryName(molecule))}</h3><span class="name-en">${esc(secondaryName(molecule))}</span><div class="card-data"><div><span>MW</span><b>${fmt(d.molecular_weight,1)}</b></div><div><span>LogP</span><b>${fmt(d.logp,1)}</b></div><div><span>QED</span><b>${fmt(d.qed)}</b></div></div><div class="card-links">${molecule.source_url?`<a href="${esc(molecule.source_url)}" target="_blank" rel="noreferrer">PubChem ↗</a>`:`<span>${esc(t('입력 대비 새 구조'))}</span>`}<button>${esc(t('AF3 입력 →'))}</button></div>${generated?`<p class="source-note">Parent similarity ${fmt(molecule.max_parent_similarity)} · ${d.alerts?.length||0} structure alerts</p>`:''}`;
  if(molecule.botanical_examples?.length){const note=document.createElement('p');note.className='source-note';note.textContent=molecule.botanical_examples.map(b=>b.herb_name_ko||b.herb_ko||b.botanical_name||b.species||t('기원 정보')).join(' · ');el.append(note);}
  moleculeImage(el.querySelector('img'),molecule.smiles);
  el.querySelector('input')?.addEventListener('change',e=>{e.target.checked?state.selected.add(molecule.id):state.selected.delete(molecule.id);el.classList.toggle('selected',e.target.checked);selectionCount()});
  el.querySelector('button').addEventListener('click',()=>{$('ligand-smiles').value=molecule.smiles;$('af3-name').value=molecule.id.replace(/[^A-Za-z0-9_.-]/g,'_');tab('structure')});
  return el;
}
async function loadCatalog(){state.catalog=await api('/catalog');await Promise.all(state.catalog.map(async m=>state.descriptors[m.id]=await api('/molecules/describe',{smiles:m.smiles})));if(!state.selected.size){state.selected.add(state.catalog.find(m=>m.category==='herbal').id);state.selected.add(state.catalog.find(m=>m.category==='drug').id)}renderCatalog();$('catalog-count').textContent=state.catalog.length;}
function renderCatalog(){$('catalog').replaceChildren(...state.catalog.map(m=>card(m)));selectionCount()}
function renderHealth(){const af=state.health.alphafold;$('af3-version').textContent=`Official v${af.expected_version||af.version||'3.0.4'}`;$('af3-status').innerHTML=`<span class="state ${af.ready?'ready':'blocked'}">${esc(t(af.ready?'실행 준비됨':'설정 확인 필요'))}</span><p class="helper">${esc((af.blockers||[]).join(' · '))}</p>${pretty(af)}`;}
async function health(){state.health=await api('/health');renderHealth();}
on('auth-button',async()=>{state.token=prompt(t('서버의 HERBFOLD_API_TOKEN 값을 입력하세요. 이 탭 메모리에만 보관합니다.'))||'';await Promise.all([health(),loadCatalog()]);notify(t('API 연결을 확인했습니다.'))});
on('pubchem-search',async()=>{if(!$('pubchem-category').value)throw Error(t('불러올 분자의 분류를 직접 선택하세요. PubChem 구조 정보만으로 천연물 기원을 판정하지 않습니다.'));const m=await api('/sources/pubchem?query='+encodeURIComponent($('pubchem-query').value));m.id='pubchem_'+m.cid;m.name_ko=m.name;m.source_url=m.source;m.category=$('pubchem-category').value;m.user_imported=true;m.category_provenance='user_declared';state.descriptors[m.id]=await api('/molecules/describe',{smiles:m.smiles});state.catalog.push(m);state.selected.add(m.id);renderCatalog();notify(t('{0} 분자를 추가했습니다. 사용자 분자는 비교·후보 생성에 사용할 수 있습니다.',m.name))});
function renderDiscovery(){
  const result=state.discovery;
  $('discovery-results').hidden=false;
  $('comparison').innerHTML=result.comparison.map(pair=>{const sim=pair.tanimoto_similarity??pair.tanimoto??pair.similarity??0;return `<div class="result-line"><div>${esc(pair.herbal_name||pair.herbal?.name||pair.herbal_id||pair.compound_a||t('천연물'))} <small>×</small> ${esc(pair.drug_name||pair.drug?.name||pair.drug_id||pair.compound_b||t('약물'))}</div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(0,Math.min(100,sim*100))}%"></div></div><strong>${fmt(sim,3)}</strong></div>`}).join('')||`<p class="helper">${esc(t('비교할 천연물·약물 쌍이 없습니다.'))}</p>`;
  $('comparison').insertAdjacentHTML('beforeend',pretty(result.comparison));
  $('candidate-grid').replaceChildren(...result.candidates.map(m=>card(m,true)));
  if(!result.candidates.length)$('candidate-grid').innerHTML=`<div class="panel helper">${esc(t('선택한 입력의 BRICS 조각으로 필터를 통과한 후보가 없습니다. 다른 부모 분자를 선택하세요.'))}</div>`;
}
on('discover-button',async()=>{
  const compounds=state.catalog.filter(m=>state.selected.has(m.id));if(compounds.length<2)throw Error(t('분자를 두 개 이상 선택하세요.'));
  notify(t('구조를 비교하고 후보 분자를 계산하고 있습니다.'));
  const custom=compounds.some(m=>m.user_imported);
  const job=await api('/workflows',{...(custom?{compounds}:{compound_ids:compounds.map(m=>m.id)}),max_candidates:Number($('candidate-count').value),protein_sequence:$('protein-sequence').value.replace(/\s/g,''),target_id:$('uniprot-id').value||'custom'});
  const result=job.result;state.discovery=result;
  renderDiscovery();
  notify(t('{0}개 후보 구조 생성 완료. 결합 친화도는 아직 평가하지 않았습니다.',result.candidates.length));
});
on('export-discovery',()=>download(state.discovery,'herbfold-discovery.json'));
on('uniprot-fetch',async()=>{const target=await api('/sources/uniprot/'+encodeURIComponent($('uniprot-id').value));$('protein-sequence').value=target.sequence;$('target-info').textContent=`${target.name} · ${target.organism} · ${target.sequence.length} aa · ${target.accession}`;notify(t('UniProt에서 실제 단백질 서열을 가져왔습니다.'))});
function renderAF3(){const job=state.af3Job;$('af3-result').hidden=false;const result=job.result||{};$('af3-result').innerHTML=`<div class="section-title compact"><h2>${esc(t('작업 {0}',job.id.slice(0,10)))}</h2><span class="state ${esc(job.status)}">${esc(job.status)}</span></div>${job.error?`<p class="helper">${esc(job.error)}</p>`:''}${result.blockers?.length?`<p class="helper">${esc(result.blockers.join(' · '))}</p>`:''}<div class="inline"><button id="af3-download-input" class="secondary">${esc(t('입력 JSON ↓'))}</button><button id="af3-execute" class="primary" ${!result.runnable||job.status!=='prepared'?'disabled':''}>${esc(t('실제 AF3 실행 →'))}</button><button id="af3-refresh" class="secondary">${esc(t('상태 확인'))}</button></div>${(result.models||[]).map(m=>`<div class="result-line"><div>${esc(m.name||m.structure_path||'AF3 model')}<br><small>${esc(t('ipTM {0} · pTM {1} · 구조 신뢰도',fmt(m.metrics?.iptm,3),fmt(m.metrics?.ptm,3)))} ${m.metrics?.has_clash?esc(t('· ⚠ 충돌 감지')):''}</small></div></div>`).join('')}${pretty(result)}`;
  if(result.models?.length){$('af3-result').insertAdjacentHTML('beforeend',result.models.map((m,i)=>`<button class="secondary top-gap" data-model-index="${i}">${esc(t('모델 {0} · 3D 보기',i+1))}</button>`).join(''));document.querySelectorAll('[data-model-index]').forEach(el=>el.addEventListener('click',()=>viewStructure(job,result.models[Number(el.dataset.modelIndex)]).catch(e=>notify(e.message,true))));}
  on('af3-download-input',()=>artifact(job.id,'fold_input.json'));on('af3-execute',async()=>showAF3(await api('/af3/'+job.id+'/execute',{})));on('af3-refresh',async()=>showAF3(await api('/jobs/'+job.id)));
}
function showAF3(job){state.af3Job=job;$('structure-viewer-panel').hidden=true;renderAF3();}
on('af3-prepare',async()=>{const seeds=$('af3-seeds').value.split(',').map(x=>Number(x.trim()));showAF3(await api('/af3/prepare',{name:$('af3-name').value,proteins:[{id:'A',sequence:$('protein-sequence').value}],ligands:[{id:'B',smiles:$('ligand-smiles').value}],seeds,msa_mode:$('msa-mode').value}));notify(t('AF3 입력과 실행 계획을 저장했습니다.'))});
on('af3-import',async()=>{const files={};for(const file of $('af3-files').files)files[file.name]=await file.text();showAF3(await api('/af3/import',{files}))});
function renderBackends(){const result=state.backends;const backend=(result.backends||[]).find(b=>b.name===result.selected_max_backend);$('backend-status').innerHTML=backend?`<div class="badge-large">${backend.usable_qubits||backend.num_qubits} <small>available qubits</small></div><strong>${esc(backend.name)}</strong><p class="helper">${(result.backends||[]).map(b=>`${esc(b.name)} · ${b.usable_qubits} q`).join('<br>')}</p>${pretty(result)}`:`<p>${esc(result.status)}</p>${pretty(result)}`;}
on('backends-fetch',async()=>{state.backends=await api('/quantum/backends');renderBackends();});
$('quantum-mode').addEventListener('change',renderQuantumCost);
on('quantum-from-selection',()=>{const molecules=state.catalog.filter(m=>state.selected.has(m.id));$('quantum-features').value=JSON.stringify(molecules.map(m=>{const d=state.descriptors[m.id];return [d.molecular_weight/500,d.logp/5,d.tpsa/150,d.hbd/5,d.hba/10,d.qed]}),null,2);notify(t('MW/500, LogP/5, TPSA/150, HBD/5, HBA/10, QED 특징을 입력했습니다.'))});
function quantumRequest(){return {kernel_method:'projected',features:JSON.parse($('quantum-features').value),mode:$('quantum-mode').value,qubits:$('quantum-mode').value==='ibm'?'max':4,shots:Number($('quantum-shots').value),max_execution_time:Number($('quantum-time').value),max_circuits:Number($('quantum-circuits').value),max_total_shots:Number($('quantum-total-shots').value),max_jobs:16}}
function quantumValue(value){if(typeof value!=='number'||!Number.isFinite(value))return '—';if(value===0)return '0';return Math.abs(value)<.0001||Math.abs(value)>=1e6?value.toExponential(4):Number(value.toPrecision(6)).toString();}
function renderQuantum(){
  const {result,job}=state.quantumView;
  $('quantum-result').hidden=false;
  const raw=result.kernel;
  const kernel=Array.isArray(raw)&&raw.length&&raw.every(row=>Array.isArray(row)&&row.length===raw.length&&row.every(Number.isFinite))?raw:null;
  const method=result.kernel_method||result.plan?.kernel_method||result.metadata?.kernel_method||'fidelity';
  const projected=method==='projected';
  const collapsed=!projected&&kernel&&kernel.every((row,i)=>row[i]===0);
  const measured=result.hardware_executed===true;
  const mode=result.mode||result.plan?.mode||result.metadata?.mode||job?.payload?.mode;
  const origin=t(measured&&mode==='ibm'?(projected?'IBM 관측량으로 계산한 커널':'IBM 전역 반환 확률'):mode==='local'?'로컬 이상적 계산':'실행 근거 미확인');
  const observations=(result.jobs||[]).flatMap(item=>item.observations||[]).filter(row=>row.pair&&row.zero_counts!==undefined);
  $('quantum-result').innerHTML=`<h2>${esc(t(kernel?(projected?'관측량 기반 분자 특징 커널':'전역 Fidelity kernel'):'실행 계획 · 상태'))}</h2><p class="helper">${esc(origin)} · ${esc(t(projected?'X·Y·Z 기대값의 거리로 계산합니다. 대각선 1은 RBF 정의값이며 자기 충실도의 실측값이 아닙니다.':'원시 all-zero 반환 확률과 대각선 측정을 보존합니다.'))} ${esc(t('친화도·약효·안전성이나 양자 우위를 검증한 값이 아닙니다.'))}</p>${collapsed?`<p class="helper">${esc(t('⚠ 동일 입력의 대각선까지 0으로 관측되어 이 전역 측정으로 분자 차이를 구분하지 못했습니다. 잡음·회로 폭·shots의 한계를 검토해야 하며, 약효가 0이라는 뜻은 아닙니다.'))}</p>`:''}${kernel?`<div class="kernel" style="grid-template-columns:repeat(${kernel.length},1fr)">${kernel.flatMap((row,i)=>row.map((v,j)=>`<div title="${esc(t('분자 {0} × {1}: {2}',i+1,j+1,quantumValue(v)))}" style="background:rgba(126,158,80,${.08+.8*Math.max(0,Math.min(1,v))})">${quantumValue(v)}${projected&&i===j?`<small> ${esc(t('· 정의값'))}</small>`:''}</div>`)).join('')}</div>`:''}${observations.length?`<details><summary>${esc(t('실제 관측 횟수·Wilson 95% 구간'))}</summary>${observations.map(row=>`<p>${esc(t('분자 {0} × {1}: {2} / {3} shots · [{4}]',row.pair[0]+1,row.pair[1]+1,quantumValue(row.zero_counts),quantumValue(row.shots),(row.wilson_95||[]).map(quantumValue).join(', ')))}</p>`).join('')}</details>`:''}${job?.status==='submitted'||job?.status==='running'?`<button id="quantum-refresh" class="secondary">${esc(t('IBM 결과 새로고침'))}</button>`:''}${pretty(result)}`;
  if($('quantum-refresh'))on('quantum-refresh',async()=>{const next=await api('/quantum/'+job.id+'/refresh',{});showQuantum(next.result,next)});
}
function showQuantum(result,job=null){state.quantumView={result,job};renderQuantum();}

on('quantum-plan',async()=>showQuantum(await api('/quantum/plan',quantumRequest())));
on('quantum-run',async()=>{notify(t('커널 작업을 처리하고 있습니다.'));const job=await api('/quantum/run',quantumRequest());state.quantumJob=job;$('kernel-job-id').value=job.id;showQuantum(job.result,job);notify(t(job.status==='completed'?'로컬 커널 계산 완료':'IBM 작업이 제출되었습니다. 실행 기록에서 확인할 수 있습니다.'))});
on('chembl-fetch',async()=>{const result=await api(`/sources/chembl/${encodeURIComponent($('chembl-target').value)}?endpoint=${$('endpoint').value}&limit=100`);$('records-json').value=JSON.stringify(result.records,null,2);notify(t('실측 {0}개를 가져왔습니다. 중복 및 assay 비교 가능성을 확인한 후 평가하세요.',result.records.length))});
$('csv-file').addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;const result=await api('/data/csv',undefined,{method:'POST',headers:{'Content-Type':'text/csv',...(state.token?{'Authorization':`Bearer ${state.token}`}:{})},body:await file.text()});$('records-json').value=JSON.stringify(result.records,null,2);notify(t('{0}개 CSV 레코드 로드 완료',result.records.length))}catch(error){notify(error.message,true)}});
function recordsRequest(){return {records:JSON.parse($('records-json').value),endpoint:$('endpoint').value,split:$('split-mode').value}}
function renderValidation(){const {data,prediction}=state.validationView;$('validation-result').hidden=false;$('validation-result').innerHTML=`<h2>${esc(t(prediction?'모델 예측 결과 · 실측 아님':'실측 데이터 평가 결과'))}</h2><pre>`+esc(JSON.stringify(data,null,2))+'</pre>';}
function showValidation(data,prediction=false){state.validationView={data,prediction};renderValidation();}
on('evaluate',async()=>{const job=await api('/benchmark/evaluate',recordsRequest());showValidation(job.result);notify(t('보류 그룹에서 성능 평가를 완료했습니다.'))});
on('train',async()=>{const job=await api('/models/train',recordsRequest());$('model-id').value=job.id;showValidation(job.result);notify(t('평가와 모델 저장을 완료했습니다.'))});
on('predict',async()=>showValidation(await api('/models/predict',{model_id:$('model-id').value,queries:JSON.parse($('queries-json').value)}),true));
function renderJobs(){const jobs=state.jobs;$('jobs-list').innerHTML=jobs.length?`<table><thead><tr><th>JOB</th><th>WORKFLOW</th><th>STATUS</th><th>CREATED</th><th></th></tr></thead><tbody>${jobs.map(j=>`<tr><td>${esc(j.id.slice(0,10))}</td><td>${esc(j.kind)}</td><td><span class="state ${esc(j.status)}">${esc(j.status)}</span></td><td>${esc(new Date(j.created).toLocaleString(locale()))}</td><td><button data-job="${j.id}">${esc(t('결과 보기 ↗'))}</button></td></tr>`).join('')}</tbody></table>`:`<div class="empty">${esc(t('아직 저장된 실행이 없습니다. 첫 분자 탐색을 시작하세요.'))}</div>`;document.querySelectorAll('[data-job]').forEach(el=>el.addEventListener('click',async()=>{try{const job=await api('/jobs/'+el.dataset.job);if(job.kind==='alphafold'||job.kind==='af3_import'||job.kind==='alphafold_smoke'){tab('structure');showAF3(job)}else if(job.kind==='quantum'){tab('quantum');showQuantum(job.result,job)}else{download(job,'herbfold-'+job.id.slice(0,10)+'.json')}}catch(e){notify(e.message,true)}}));}
async function loadJobs(){state.jobs=await api('/jobs');renderJobs();}
on('jobs-refresh',loadJobs);

async function viewStructure(job,model){
  if(!model.structure_path)throw Error(t('이 모델에 mmCIF 파일이 없습니다.'));
  const path='output/'+model.structure_path;
  const response=await fetch(`/api/jobs/${job.id}/artifacts/${path}`,{headers:state.token?{'Authorization':`Bearer ${state.token}`}:{}});
  if(!response.ok)throw Error(t('구조 파일을 불러오지 못했습니다.'));
  const content=await response.text();$('structure-viewer-panel').hidden=false;state.structureModel=model;renderStructureQuality();$('structure-viewer').replaceChildren();
  const viewer=$3Dmol.createViewer($('structure-viewer'),{backgroundColor:'#fbfcf8'});viewer.addModel(content,'cif');viewer.setStyle({},{cartoon:{color:'#789e63'}});viewer.addStyle({atom:'CA'},{sphere:{radius:.5,color:'#789e63'}});viewer.setStyle({hetflag:true},{stick:{radius:.22},sphere:{radius:.45}});viewer.zoomTo();viewer.render();
  state.structure={jobId:job.id,path};state.viewer=viewer;
  $('structure-viewer-panel').scrollIntoView({behavior:'smooth'});
}
function renderStructureQuality(){const model=state.structureModel;$('structure-quality').textContent=t((model.metrics?.has_clash||model.metrics?.iptm==null||model.metrics.iptm<.6)?'품질 점검 불합격 · 충돌 또는 낮은 구조 신뢰도로 학습 연결에서 제외됩니다. 원자 좌표는 그대로 표시합니다.':'구조 신뢰도는 결합 친화도가 아닙니다. 잔기·리간드 배치와 생물학적 맥락을 검토하세요.');}
on('pocket-features',async()=>{if(!state.structure)return;const result=await api(`/af3/${state.structure.jobId}/pocket?file=${encodeURIComponent(state.structure.path)}&ligand_chain=B`);$('pocket-result').hidden=false;$('pocket-result').textContent=JSON.stringify(result,null,2)});
on('kernel-evaluate',async()=>{notify(t('실측 데이터에서 양자 커널과 고전 커널을 같은 분할로 비교하고 있습니다.'));const job=await api('/quantum/experiments/local',recordsRequest());showValidation(job.result.evaluation);});
on('kernel-prepare',async()=>{const job=await api('/quantum/experiments/prepare',recordsRequest());state.kernelExperiment=job;$('kernel-experiment-id').value=job.id;const n=job.result.features.length;const requiredCircuits=3*n+5;const requiredShots=requiredCircuits*Number($('quantum-shots').value);const budgetInsufficient=requiredCircuits>Number($('quantum-circuits').value)||requiredShots>Number($('quantum-total-shots').value);$('quantum-features').value=JSON.stringify(job.result.features,null,2);$('quantum-mode').value='ibm';$('quantum-mode').dispatchEvent(new Event('change'));showValidation(job.result);download(job,'kernel-experiment-'+job.id+'.json');notify(t('분할과 특징을 저장했습니다. 관측량 기반 projected 계산에는 {0}회로 / {1} shots가 필요합니다. {2} 입력된 예산은 변경하지 않았습니다.',requiredCircuits,requiredShots.toLocaleString(locale()),t(budgetInsufficient?'현재 입력한 예산이 부족합니다. 상한을 검토하세요.':'현재 입력한 회로·shot 예산 안에 있습니다.')));});

on('audit-records',async()=>{state.curation=await api('/data/audit',recordsRequest());showValidation(state.curation);$('apply-curation').hidden=false;notify(t('원본을 보존한 점검 보고서를 생성했습니다. 제안 레코드를 검토한 후 적용할 수 있습니다.'));});
on('apply-curation',()=>{if(!state.curation)return;$('records-json').value=JSON.stringify(state.curation.suggested_records,null,2);$('apply-curation').hidden=true;notify(t('모호한 중복 그룹을 제외한 제안을 적용했습니다. assay 간 비교 가능성은 연구자가 검토해야 합니다.'));});
on('kernel-finish',async()=>{const job=await api('/quantum/experiments/'+$('kernel-experiment-id').value+'/evaluate',{quantum_job_id:$('kernel-job-id').value});showQuantum(job.result);});

on('attach-structures',async()=>{const result=await api('/data/structures',recordsRequest());$('records-json').value=JSON.stringify(result.records,null,2);showValidation(result);notify(t('실제 AF3 좌표와 출처를 연결했습니다. 실측 라벨은 보존됩니다.'));});

// Language: default Korean, shared with the React app through localStorage (in-memory fallback when storage is blocked).
document.querySelectorAll('.lang-switch [data-lang]').forEach(button=>button.addEventListener('click',()=>setLanguage(button.dataset.lang)));
window.addEventListener('storage',event=>{if(event.key===LANGUAGE_KEY)setLanguage(event.newValue==='en'?'en':'ko',false)});
captureStatic();
setLanguage(readLanguage(),false);
Promise.all([health(),loadCatalog()]).catch(error=>notify(error.message,true));
