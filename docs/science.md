# HerbFold 과학적 근거와 검증 범위

이 플랫폼은 한약 관련 천연물과 시판 의약품 성분의 구조를 비교하고, 계산 후보를 만들고, AlphaFold 3 복합체 구조와 실측 결합 데이터를 함께 연구하기 위한 도구다. **신약 효능·안전성, 결합력 향상, 임상적 유효성 또는 양자 우위가 검증된 제품은 아니다.** 코드가 정상 실행되는 것과 과학적 가설이 검증되는 것은 별도 결과다.

## 제공 논문 점검

검토 대상은 사용자가 제공한 `beyond_docking_precision_prediction_of_h.pdf`, 제목 *Beyond Docking: Precision Prediction of Herb-Protein Affinity via AlphaFold3-Driven Co-Folding*이다. 아래 페이지는 PDF 자체의 페이지를 기준으로 한다. 이 문서는 원고의 연구 방향을 구현 요구로 참고했지만, 아래 미확정 내용을 플랫폼의 실측 성능으로 복사하지 않았다.

| 원고에서 확인한 내용 | 해석 및 구현 처리 |
| --- | --- |
| 1쪽 초록은 CoRE MOF 구조 8,295건과 health chatter 1,000건을 데이터로 설명한다. 3쪽 방법은 ChEMBL/BindingDB 단백질–리간드 실측 데이터와 `[N-TOTAL]`을 기술한다. | MOF 구조나 온라인 게시물은 단백질–리간드 Kd의 대체 정답이 아니다. 최종 분석에 실제 사용한 데이터 목록과 분할이 제공되지 않아 같은 실험을 재현할 수 없다. |
| 1·4·7쪽에 `[DATA-URL]`, 7쪽에 `[CODE-URL]`이 남아 있다. | HerbalAffinity-3D의 해당 버전과 원래 코드·실험 로그를 이 파일만으로 검증할 수 없다. 별도의 공개 저장소가 없다고 단정하지는 않는다. |
| 1쪽은 Spearman 0.68, 4–5쪽은 Pearson 0.82 등의 성능을 보고한다. | 서로 다른 지표 자체가 모순은 아니다. 다만 원본 예측·실측값, 표본 수, 분할 명단, 계산 로그가 없으므로 재계산할 수 없다. 해당 수치와 유의확률은 기본값이나 데모 성능에 사용하지 않는다. |
| 3쪽은 pLDDT/PAE를 바탕으로 낮은 에너지 구조를 선택한다고 서술한다. | 신뢰도 지표는 에너지나 Kd가 아니다. AF3 출력의 구조 신뢰도와 별도의 실측 데이터로 학습한 친화도 회귀를 구분한다. |
| 7쪽 저자 기여의 이니셜은 표지의 저자 이름과 대응이 불분명하다. | 원고가 정리 중일 가능성이 있어 서지·기여·재현 정보의 최종 확인이 필요하다. 저자 신원이나 연구 부정행위를 추정하지 않는다. |

따라서 이 저장소는 해당 논문의 성능 재현물이라고 주장하지 않는다. 기존 원고의 원데이터가 확보되면 동일한 endpoint, 표적, 표본 제외 기준, 분할, 기준 모델을 재구성해 비교할 수 있다.

## 구조 예측과 결합력의 구분

AF3의 직접 산출물은 생체분자 복합체의 좌표와 구조 신뢰도다. 리간드 결합 자세 평가와 결합 자유에너지·Kd 평가를 구분해야 한다. 원 논문은 여러 종류의 복합체 구조 예측을 평가한다. [AlphaFold 3 원논문](https://www.nature.com/articles/s41586-024-07487-w)

공식 출력 문서는 `ranking_score`를 예측 **구조** 순위에 사용하도록 정의한다. pLDDT, PAE, pTM, ipTM을 pKd나 kcal/mol로 바꾸는 보정식은 본 플랫폼에 없다. 실제 좌표에서 추출한 접촉 원자 수·거리도 기하학적 특성이며 실측 친화도는 아니다. [AF3 공식 출력 문서](https://github.com/google-deepmind/alphafold3/blob/main/docs/output.md)

예측 결과가 나오면 입력 분자 정체성, 전하, 입체화학, 금속·보조인자, 단백질 construct, 결합 자세와 비정상 원자 접촉을 검토해야 한다. 표적·리간드별 신뢰도와 여러 seed의 일관성을 함께 기록한다. 정적인 한 복합체 구조만으로 용액 내 거동, 여러 성분의 상승효과, 약동학, 부작용 또는 임상 효과를 판정하지 않는다.

## 화합물 카탈로그와 구조 표준화

`data/compounds.json`은 다음 **구조 예시** 7개를 제공한다. 2026-09-07에 PubChem PUG REST의 `IsomericSMILES,IUPACName`을 조회해 받은 구조 문자열(응답 키 `SMILES`)과 CID를 보존했다. 효능, 임상시험 또는 표적별 결합 수치는 카탈로그에 넣지 않았다. `herbal` 분류는 천연물 구조 비교군을 뜻하며 특정 한약재 배치의 함량이나 추출물 조성을 뜻하지 않는다.

| 분류 | 성분 | 구조 식별자 |
| --- | --- | --- |
| 천연물 | 퀘르세틴 | [PubChem 5280343](https://pubchem.ncbi.nlm.nih.gov/compound/5280343) |
| 천연물 | 바이칼레인 | [PubChem 5281605](https://pubchem.ncbi.nlm.nih.gov/compound/5281605) |
| 천연물 | 루테올린 | [PubChem 5280445](https://pubchem.ncbi.nlm.nih.gov/compound/5280445) |
| 천연물 | 베르베린 | [PubChem 2353](https://pubchem.ncbi.nlm.nih.gov/compound/2353) |
| 의약품 성분 비교군 | 아스피린 | [PubChem 2244](https://pubchem.ncbi.nlm.nih.gov/compound/2244) |
| 의약품 성분 비교군 | 이부프로펜 | [PubChem 3672](https://pubchem.ncbi.nlm.nih.gov/compound/3672) |
| 의약품 성분 비교군 | 셀레콕시브 | [PubChem 2662](https://pubchem.ncbi.nlm.nih.gov/compound/2662) |

카탈로그의 `botanical_examples`는 근거가 확인된 일부 기원 식물 연결도 제공한다. 바이칼레인–황금(*Scutellaria baicalensis*), 루테올린–더덕(*Codonopsis lanceolata*)는 각 PubChem record description의 성분 보고를 따른다. 베르베린–황련(*Coptis chinensis*)은 유전체·HPLC 원저의 보고를 따른다. 이 연결은 해당 식물에 성분이 보고되었다는 뜻이며 특정 약재의 함량·순도·임상 효과를 의미하지 않는다. [황련 원저](https://pmc.ncbi.nlm.nih.gov/articles/PMC8166882/)

`describe_molecule(smiles)`는 실제 RDKit 계산으로 분자량, Crippen logP, TPSA, HBD/HBA, 회전 결합 수, 형식 전하, QED, Lipinski 위반 수, PAINS/Brenk 경고, Murcko scaffold와 미지정 입체중심을 반환한다. 화학 구조에서 계산한 값이며 실험 ADME나 독성 자료가 아니다. [RDKit 공식 사용 문서](https://www.rdkit.org/docs/GettingStartedInPython.html)

표준화는 `Cleanup + FragmentParent`이며 전하와 지정된 입체화학을 유지한다. 원문 SMILES도 출력한다. 염의 상대 이온이나 여러 fragment는 parent 선택 과정에서 제외되었다고 알린다. 자동 중성화·pH별 protonation·tautomer 탐색·입체이성질체 열거를 수행하지 않는다. 이부프로펜의 예시 구조는 특정 입체이성질체를 지정하지 않으므로 미지정 입체중심 경고가 나온다. 따라서 입력이 실제 실험 물질의 염형·이성질체와 일치하는지 확인해야 한다.

`compare_compounds()`의 Tanimoto는 Morgan fingerprint(radius 2, 2,048 bits, chirality 포함)의 **구조 유사도**다. 같은 적응증, 같은 표적, 더 강한 효능 또는 대체 복용 가능성을 뜻하지 않는다.

## 후보 생성의 의미

`generate_candidates(parent_smiles, max_candidates=20)`는 두 부모씩 BRICS fragment를 재조합한다. 입력 순서를 바꾸어도 같은 RDKit 버전에서는 결정적으로 작동하며, 요청당 부모 16개, 결과 100개, 열거 제품 최대 10,000개, 부모 쌍당 500개, 재조합 깊이 2로 제한한다. BRICS 자체는 분할과 재조합 알고리즘이며 자동 합성 검증은 아니다. [RDKit BRICS API](https://www.rdkit.org/docs/source/rdkit.Chem.BRICS.html)

후보는 분자 sanitization을 통과하고, 두 부모 각각에서 유래한 구별되는 fragment가 제품의 분해 결과에도 존재해야 한다. 출력의 `parent_fragments`가 그 근거다. 이는 fragment 계보이며 원자 단위 합성 경로나 실험으로 확인한 합성 가능성이 아니다. 입력의 표준화 구조와 정확히 같은 제품은 제외한다.

계산량과 극단적 분자를 줄이기 위한 기본 범위는 MW 120–650, logP −2–6, TPSA ≤180, QED ≥0.25다. 이 수치는 플랫폼의 탐색 휴리스틱이며 신약성 판정 기준이 아니다. 경고 구조도 기본적으로 표시하되 자동 제외하지 않으며 `reject_alerts=True`로 제외할 수 있다. 필터 통과가 안전성 보증은 아니다.

`novel_relative_to_inputs`는 제공된 입력 집합에 없는 구조라는 뜻만 가진다. PubChem/ChEMBL 전체 검색, 특허 신규성, 독창성, 합성 성공, 표적 결합, 우월한 약효를 보증하지 않는다. 비교 대상보다 QED가 높아도 신약 개발 성공 확률이 검증된 것은 아니다. 한약 유래 후보를 만들 때에는 최소 하나의 확인된 천연물 부모와 원하는 의약품 비교 부모를 선택한다.

## 실측 데이터와 endpoint 규칙

학습용 레코드는 `smiles,target_id,endpoint,value,unit,relation,is_measured,source`를 요구한다. 선택 항목은 `assay_id,protein_sequence,structure_features`다. CSV에서는 `is_measured`에 `true`를 사용한다. `source`에는 원 논문 DOI, 측정 accession 또는 추적 가능한 데이터베이스 레코드를 넣는다. 플랫폼은 출처가 있고 연구자가 실측이라고 선언한 행만 받지만, 해당 논문의 진실성이나 측정 프로토콜을 자동 검증하지는 않는다.

예시 CSV 헤더만 제공하며 미측정 물질에 임의 친화도를 붙이지 않는다:

```csv
smiles,target_id,endpoint,value,unit,relation,is_measured,source,assay_id,protein_sequence
```

`endpoint="Kd"` 또는 `"Ki"`를 선택하면 다른 endpoint는 제외하고 개수를 보고한다. `relation="="`만 허용하며 `<`, `>`, 범위, censored 측정은 정확한 값으로 취급하지 않는다. 단위는 M, mM, uM/µM/μM, nM, pM만 허용하며 `pKd = −log10(Kd [M])`, `pKi = −log10(Ki [M])`로 변환한다. 예를 들어 1 nM은 pKd 9다. IC50는 이 방법으로 Kd로 변환하지 않는다.

ChEMBL의 pChEMBL 필드는 여러 종류의 activity를 함께 표현할 수 있으므로 pChEMBL만 가져와 endpoint를 생략하면 안 된다. `standard_type`, `standard_relation`, `standard_units`, target/assay/document ID와 원래 출처를 보존해야 한다. [ChEMBL 공식 activity 설명](https://chembl.gitbook.io/chembl-interface-documentation/frequently-asked-questions/chembl-data-questions)

BindingDB도 Kd, Ki, IC50 등의 데이터를 제공하므로 같은 구분이 필요하다. DB 레코드가 존재한다는 사실만으로 실험 조건이 서로 비교 가능하다고 간주하지 않는다. [BindingDB 데이터베이스 논문](https://pmc.ncbi.nlm.nih.gov/articles/PMC11701568/)

동일한 표준화 리간드–표적 쌍이 여러 번 나오면 거부한다. 서로 다른 assay를 무조건 평균내면 안 되므로 동일 construct, 단백질 상태, 용매·온도·pH, 측정법의 비교 가능성을 확인한 뒤 연구자가 반복 측정을 정리한다. 표적 construct나 변이체가 다르면 명확히 다른 `target_id`를 사용한다.

`curation.report_records(records)`와 `POST /api/data/audit`는 원본을 수정하지 않고 점검 보고서를 만든다. `duplicate_groups`에 원본 행 번호·assay·출처·값을 보여 주며, 행 번호는 0부터 시작한다. SMILES 등 명시된 표준화 후 전체 내용이 동일한 행만 하나를 남기는 제안을 한다. 같은 리간드–표적–endpoint에 값·assay·출처·기타 메타데이터가 다르면 그 그룹 전체를 제안에서 제외하고 연구자의 검토를 요구한다. 평균값이나 첫 번째 assay를 임의로 선택하지 않는다. 잘못된 행의 이유와 제외 인덱스, 표준화 변경 내용도 모두 반환한다. 화면의 제안 적용 버튼을 눌러야 `suggested_records`가 입력창에 적용되며, 이 제안 자체가 assay 비교 가능성이나 출처 진실성의 검증은 아니다.

## 학습·예측·평가

`train_model(records, endpoint="Kd")`는 descriptor 8개, Morgan 256 bits, 단백질 feature, 선택적 구조 feature에 `StandardScaler + Ridge(alpha=10)`를 적합한다. 비교 화면의 2,048-bit 유사도와 회귀 모델의 256-bit fingerprint는 목적과 크기가 다르며 모델 메타데이터에 명시한다. scaler는 학습 데이터로만 적합한다. JSON 모델에는 feature 순서·수치 계수·스케일·학습 범위·출처·RDKit 버전·학습 데이터 SHA-256을 보존한다. pickle은 사용하지 않는다.

모든 행에 `protein_sequence`가 있으면 아미노산 조성 20개와 log 길이를 사용한다. 이는 표적 구조를 충분히 표현하는 생물학적 모델이 아니며, 미지의 표적에 대한 예측은 탐색적이다. sequence가 없으면 표적 one-hot을 사용하고 **학습에 없던 표적의 예측을 거부**한다. sequence는 일부 행에만 넣을 수 없고, 같은 표적 ID에 다른 서열을 지정할 수도 없다.

`structure_features`를 사용하려면 모든 학습·예측 행에서 같은 키와 유한한 숫자가 있어야 한다. AF3/mmCIF에서 같은 방법과 cutoff로 계산한 feature를 사용한다. 실측 친화도나 사후 실험 정보를 feature에 넣으면 정답 누출이므로 금지한다. 플랫폼은 feature 출처 자체를 자동 판별할 수 없다.

`predict_model(model, query_records)` 또는 `predict_records(training_records, query_records)`는 `predicted_pactivity`와 `is_measured=False`를 반환한다. 학습 분자와의 최대 Tanimoto는 적용 범위 검토용이며 보정된 확률·신뢰구간이 아니다. 학습된 모델만으로도 출력은 가능하지만 별도의 평가 없이는 검증된 성능을 뜻하지 않는다.

`evaluate_records(records, split="scaffold_target")`의 기본 분할은 같은 scaffold **또는** 같은 target을 공유하는 행을 연결 성분으로 묶어 두 집단을 동시에 분리한다. 공유 관계가 모두 이어져 독립 그룹이 없어지면 평가를 중단한다. 임의 random split으로 대체하지 않는다. 표적 분리에는 모든 행의 sequence가 필요하다. 최소 8개 측정쌍, 4개 학습 행, 2개 시험 행은 소프트웨어 최소 조건이며 논문 수준의 충분한 표본 수가 아니다.

명시적 `split="scaffold"`는 한 표적 내 신규 scaffold 일반화를 평가하며 표적은 공유될 수 있다. `split="target"`는 신규 표적을 평가하며 scaffold가 공유될 수 있다. 반환값에 실제 겹친 scaffold·target·ligand·ligand-target 쌍과 원본 train/test 행 번호를 모두 포함한다. 무고리 구조는 모두 `ACYCLIC` 한 그룹으로 보수적으로 묶는다. 표적 ID 분리는 낮은 서열 유사도나 단백질 family 분리를 보장하지 않는다.

실측 시험 행에서 MAE/RMSE/R²/Pearson/Spearman을 계산하고 학습 평균 예측 기준선도 함께 출력한다. 상수값 때문에 정의되지 않는 상관계수는 `null`이다. 시험 독립 그룹이 3개 이상인 경우에만 그룹 bootstrap 500회의 탐색적 MAE/RMSE 95% 구간을 출력한다. hyperparameter 탐색, 여러 seed 중 최상 점수 선택, 시험 데이터를 이용한 모델 선택은 수행하지 않는다. 단일 held-out 결과를 외부 전향 검증, AF3 성능, 양자 향상 또는 임상 성능이라고 표시하지 않는다.

테스트 코드의 활동값은 **단위 테스트 전용 합성 fixture**다. 데이터베이스, 과학적 학습 데이터 또는 제품 성능 예시로 제공하지 않는다. 실제 신약 연구의 다음 근거는 적절히 큐레이션한 실측 비교 데이터와 독립적인 생물학적·약학적 검증에서 얻어야 한다.

## 실제 공개 데이터로 수행한 기술적 점검

2026-09-07에는 합성 테스트 데이터와 별도로 ChEMBL 37에서 **사람 PTGS2/COX-2의 데이터베이스 보고 Ki**를 가져와 실제 수치 계산을 수행했다. 표적은 CHEMBL230/UniProt P35354다. 데이터와 출처는 [data/benchmarks/ptgs2_ki.json](../data/benchmarks/ptgs2_ki.json), 전체 점검·분할·결과는 [docs/data_verification.json](data_verification.json)에 저장되어 있다. [ChEMBL 표적 레코드](https://www.ebi.ac.uk/chembl/api/data/target/CHEMBL230.json)

원본 조회는 Ki, 정확한 관계 `=`, nM로 제한했으며 27개 활동 레코드를 반환했다. 각 레코드의 값·단위·endpoint·구조·assay·document를 공식 activity API와 다시 대조했다. 원본에는 중복쌍이 없었고 18개 scaffold와 9개 assay가 있었다. 결과를 보기 전에 다음 추가 조건을 정했다: 사람 종이 명시되고, 직접 표적 매핑 D·confidence 9·binding assay B이며, assay 설명에 기원이 불명확하다고 적힌 경우를 제외한다. 이 조건으로 homolog 매핑 1행과 기원 불명확 5행을 제외해 **21행·12 scaffold·5 assay**를 남겼다. 제외된 6행과 그 사유도 데이터 파일에 보존했다.

21행 모두를 사용해 seed 42, scaffold 분할, test fraction 0.25로 한 번만 실행했다. 학습은 17행, 시험은 4행이다. 표적은 공유되며 일부 assay도 공유된다. 여러 assay의 측정 조건과 경쟁·비경쟁 등 저해 기작을 통일하지 않았으므로 **혼합 assay를 이용한 외부 데이터 처리·계산 점검**으로 해석해야 한다. 모든 원논문의 실험 절차를 재검토한 표준화 데이터셋, 표적 분리 평가, 전향적 검증 또는 임상 검증은 아니다. 원본의 Ki 표기를 유지하며 이를 일률적인 열역학적 Kd로 해석하지 않는다.

| 고정된 모델 | MAE (pKi) | RMSE (pKi) | R² | Pearson r | Spearman ρ |
| --- | ---: | ---: | ---: | ---: | ---: |
| Descriptor + Ridge | 1.7181 | 1.9743 | −1.3430 | 0.6805 | 0.8000 |
| 로컬 4-qubit fidelity kernel + Ridge | 1.7445 | 1.9268 | −1.2315 | 0.5421 | 0.8000 |
| 고전 RBF kernel + Ridge | 1.6762 | 1.8847 | −1.1351 | 0.5624 | 0.8000 |
| 학습 평균 기준선 | 1.7878 | 1.9346 | −1.2498 | 정의 안 됨 | 정의 안 됨 |

로컬 양자 회로 커널은 고전 RBF보다 RMSE가 높았고, 모든 모델의 R²가 음수였다. 시험 표본이 4개뿐이므로 높은 순위 상관계수만 골라 성공이라고 해석하면 안 된다. 이 결과는 유용한 일반화 성능이나 양자 우위를 보여 주지 않는다. 사용한 회로는 정확한 로컬 statevector 계산(4 qubit, 2 layers, shots 0)이며 **물리 IBM QPU를 실행하지 않았고 AF3 구조 feature도 포함하지 않았다.** 코드·데이터 연결의 실제 작동을 확인한 결과다.

실행 기록은 `runtime/1270376f5f2642319003903b3266ca78/`에 입력 데이터, 고정 프로토콜, descriptor 평가, 커널 실험, 커널 행렬과 평가를 각각 보존한다. 데이터 부분은 ChEMBL의 **CC BY-SA 3.0** 조건에 따라 출처·수정 내용을 명시했다. 저장소 코드 라이선스와 별도의 데이터 라이선스다. [ChEMBL 공식 라이선스 설명](https://chembl.gitbook.io/chembl-interface-documentation/frequently-asked-questions/general-questions)

고정된 데이터를 같은 설정으로 재계산하려면:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from herbfold import benchmark, kernel_model

records = json.loads(Path('data/benchmarks/ptgs2_ki.json').read_text())['records']
settings = dict(endpoint='Ki', split='scaffold', test_fraction=0.25, seed=42)
print(benchmark.evaluate_records(records, **settings)['metrics'])
print(kernel_model.run_local_experiment(records, qubits=4, layers=2, **settings)['evaluation']['metrics'])
PY
```
