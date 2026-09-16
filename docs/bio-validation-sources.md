# COX-2·hERG 후보 검토: 실측 근거와 계산 검증

이 단계는 후보 구조를 공개 실측 기록과 대조하고, 개별 assay 자료로 학습한 모델의 적용 가능성을 확인합니다. **신약 효능, 인체 안전성, 합성 가능성 또는 새 실험을 통한 검증을 판정하지 않습니다.** PAINS/BRENK 경고와 QED 같은 구조 지표를 효능·안전성의 증거로 바꾸지 않습니다. QED는 이 단계의 학습 정답이나 통과 기준에 사용하지 않습니다.

## 원천과 표적 확인

2026-09-08 확인한 ChEMBL 공개 API의 버전은 **ChEMBL 37, 릴리스 2026-05-01**입니다. [ChEMBL API 문서](https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services)의 페이지·필터 기능을 사용하고, 원본 JSON마다 URL·수집 시각·SHA256을 보존합니다. [ChEMBL 데이터 라이선스](https://www.ebi.ac.uk/chembldb/)는 CC BY-SA 3.0이며 원천 데이터에 해당 출처와 라이선스를 기록합니다.

| 용도 | ChEMBL 표적 | 직접 확인한 UniProt | 표적 조건 |
| --- | --- | --- | --- |
| COX-2 관련 assay 활성 | [CHEMBL230](https://www.ebi.ac.uk/chembl/api/data/target/CHEMBL230.json) | P35354 | Homo sapiens, SINGLE PROTEIN |
| hERG 관련 assay 반응 | [CHEMBL240](https://www.ebi.ac.uk/chembl/api/data/target/CHEMBL240.json) | **Q12809** | Homo sapiens, SINGLE PROTEIN |

각 표적 구성 성분의 accession까지 확인하며, 다른 accession이면 실행을 중단합니다. hERG/KCNH2를 P51787로 연결하지 않습니다.

정확한 관계 `=`, 표준 단위 `nM`라는 조회 조건에서 COX-2 Ki 27건·Kd 8건·IC50 6,393건, hERG IC50 12,010건을 내려받았습니다. 합계 **18,438개 활동 기록**과 대응하는 **2,610개 assay 메타데이터**입니다. 이 조회 조건에서 반환된 자료는 모두 수집했으며, 다른 단위·종·표적·활성 endpoint나 검열된 상·하한 값까지 전수 수집했다는 뜻은 아닙니다.

## 정제와 assay 구분

활동 기록과 assay의 표적 ID가 모두 일치하고, 사람 표적에 직접 대응하는 confidence score 9인 경우만 남깁니다. 이 점수는 **assay와 표적의 연결 신뢰도**이며 측정 자체의 재현성이나 임상 효과를 보증하지 않습니다. 변이 표적, 원천의 오류·중복 플래그, 유효하지 않은 구조·농도, 구간으로 보고된 측정은 제외합니다. [ChEMBL confidence score·활동 자료 설명](https://chembl.gitbook.io/chembl-interface-documentation/frequently-asked-questions/chembl-data-questions)

정제 후 **12,983개 기록**을 모델 검토·직접 대조에 사용합니다. 제외 수와 원본은 `assay-curation.json`에서 확인할 수 있습니다. 같은 canonical parent 구조·표적·endpoint·assay의 완전히 같은 수치는 활동 ID를 모아 중복 제거합니다. 수치가 다른 동일 assay 반복 기록 471개는 평균내지 않고 모델 학습에서 제외하며, 충돌 원본을 별도로 보존합니다.

**Ki, Kd, IC50와 서로 다른 assay ID를 합치지 않습니다.** 값은 각 endpoint 안에서만 `pEndpoint = 9 − log10(value_nM)`로 표현합니다. pIC50를 pKi나 해리 상수로 변환하지 않습니다. ChEMBL의 B/F/A/T 분류와 실제 assay 설명을 함께 남깁니다. 같은 B 분류여도 실험법이 같다고 가정하지 않습니다.

원천과 후보의 구조는 RDKit Cleanup + FragmentParent로 정리하고 전하·입체화학을 유지합니다. 따라서 `exact_measured`는 같은 **정규화된 parent 구조**에 이미 보고된 기록입니다. 새 후보로 제출된 물질을 이번에 실험실에서 측정했다는 뜻이 아닙니다. 원본 입력 snapshot과 원천 구조도 남깁니다.

## 모델과 사전 기준

각 표적·endpoint·assay 종류에서 정제 구조 수가 가장 많은 assay 하나를 값의 좋고 나쁨과 무관하게 선택합니다. 서로 다른 assay를 합쳐 표본 수를 부풀리지 않습니다. 최소 고유 구조 120개와 Murcko scaffold 12개를 요구합니다.

학습, 모델 선택, 오차 보정, 최종 평가의 **네 부분에 scaffold가 겹치지 않도록** 분리합니다. 대략적인 비율은 50/15/15/20이며 그룹 크기 때문에 실제 표본 수는 달라집니다. 작은 분할을 무작위 구조 분할로 대체하지 않습니다. 이런 구조 분할은 일반적인 무작위 분할보다 새로운 구조로의 일반화를 엄격하게 평가하려는 방법입니다. [MoleculeNet 원논문](https://pmc.ncbi.nlm.nih.gov/articles/PMC5868307/)

- 특징: 입체화학을 포함한 Morgan radius 2, 2,048 bits.
- 비교: 학습 집합 중앙값, Ridge(alpha 10), Random Forest(128 trees, leaf 최소 3).
- 모델 선택: 모델 선택용 분할의 MAE만 사용. 보정 집합과 최종 시험 집합으로 모델을 고르지 않음.
- 적용 영역: 학습 구조에 대한 최대 Tanimoto 유사도 0.5 이상.
- 보정·시험의 적용 영역 표본: 각각 최소 20개.
- 시험 기준: MAE 0.75 p단위 이하, 중앙값 기준보다 MAE 5% 이상 개선, R² 양수.
- 구간 기준: 보정 절대 오차의 유한 표본 분위수로 명목 90% 구간 계산. 반폭 1.0 p단위 이하, 독립 시험에서 관측된 포함률 80% 이상.

이 수치들은 구현에서 미리 선언한 연구용 진행 기준이며 규제 기준이나 임상 검증 기준은 아닙니다. 보정 구간은 분포가 바뀐 새로운 화합물에 자동으로 90% 확률을 보장하지 않습니다. 이 프로그램은 독립 시험에서 실제 관측한 포함률을 보고합니다. [분포 이동과 conformal prediction에 관한 원논문](https://proceedings.neurips.cc/paper/2019/file/8fb21ee7a2207526da55a679f0332de2-Paper.pdf)

## 실제 assay 평가 결과

COX-2에서 구조 수가 가장 큰 선택 assay는 126구조였지만 scaffold가 8개여서 충분성 기준을 통과하지 못했습니다. Ki·Kd와 다른 선택 assay도 같은 기준을 충족하지 못해 **COX-2 예측 모델을 승인하지 않았습니다.** 이는 후보가 효과 없다는 판정이 아니라 해당 모델로 효능을 정량화할 근거가 부족하다는 뜻입니다.

hERG의 [CHEMBL1827362](https://www.ebi.ac.uk/chembl/api/data/assay/CHEMBL1827362.json)는 150구조·94 scaffold를 포함해 실제 평가했습니다. **방사성 dofetilide의 경쟁적 결합 치환 assay**이며, 심장 안전성 시험이나 채널 전류 측정으로 이름을 바꾸지 않습니다. 분할은 학습 84, 모델 선택 15, 보정 27, 최종 시험 24구조였습니다.

| 독립 시험 지표 | 학습 중앙값 | 선택된 Random Forest |
| --- | ---: | ---: |
| 전체 시험 MAE, n=24 | 0.595 | 0.463 |
| 전체 시험 R² | −0.021 | 0.218 |
| 적용 영역 시험 MAE, n=23 | 0.565 | 0.422 |
| 적용 영역 시험 R² | −0.005 | 0.294 |

모델 선택용 MAE가 더 낮았던 Random Forest를 선택했습니다. Ridge의 전체 시험 MAE 0.454가 나중에 더 좋게 나왔더라도 시험 결과를 보고 모델을 바꾸지 않았습니다. 보정 영역은 25구조, 시험 영역은 23구조였으며 구간 반폭은 **±0.648 pIC50**, 시험 포함률은 **21/23 = 91.3%**였습니다. 작은 시험 표본과 이 결합 assay의 범위 안에서만 해석해야 합니다.

개별 후보가 적용 영역 밖이면 이 모델 자체가 진행 기준을 통과했더라도 `abstained`를 반환합니다. 학습 유사도를 높이려고 독성이 강한 방향으로 후보를 최적화하지 않습니다.

## 후보 결과와 실행

최종 평가 입력은 기존 후보 5,741개 전부와 계보를 수정한 3,701,740개 구조 모집단의 균등 표본 10,000개입니다. 두 집단의 공통 구조 1개를 제거하여 **15,740개**를 실제 평가했습니다. 고정 입력 SHA256은 `3a0bca08099cfe490a0de506b0ba600dcb642d2af3f2fad10b488dad2014049b`이며 원천 캐시를 이용한 최종 계산은 92.868초 걸렸습니다.

| 최종 후보 검토 | 실제 결과 |
| --- | ---: |
| 평가한 고유 구조 | 15,740 |
| 유효하지 않은 구조 | 0 |
| PAINS 또는 BRENK 검토 경고 | 9,080 |
| PAINS 경고 | 1,130 |
| BRENK 경고 | 8,829 |
| 원천 컬렉션과 정확한 canonical 구조 일치 | 241 |
| 기존 COX-2 IC50 측정 기록과 일치 | 3 |
| 적용 영역 기준을 통과한 새로운 정량 예측 | **0** |

PAINS와 BRENK 수는 겹칠 수 있습니다. 기존 COX-2 실측 이력 3개는 서로 다른 assay에서 보고된 동일 구조의 기록이며 새 실험이 아닙니다. hERG 모델에 대한 후보의 최대 학습 유사도는 **0.3774**로, 가장 가까운 후보도 기준 0.5에 도달하지 못했습니다. 따라서 모든 후보의 hERG 정량 예측을 보류했습니다. 모델 시험 성능이 기준을 넘었다고 새로운 천연물 공간에 무조건 적용하지 않은 결과입니다.

이 결과만으로 후보의 효능이나 인체 안전성이 검증되었다고 말할 수 없습니다. 수백만 구조의 저장 모집단과 평가한 고정 표본을 구분하며 전체 모집단을 실측·예측 검증했다고 주장하지 않습니다. 입력 구성과 추출 방식은 `runtime/validation/candidate-assessment-inputs.receipt.json`에 남습니다.

```bash
.venv/bin/python scripts/validate_candidate_bioactivity.py \
  --candidates runtime/validation/candidate-assessment-inputs.jsonl \
  --output runtime/validation/bio-validation
```

- `summary.json`: 표적, 원천 수, 후보·집단별 집계, 적용 영역, 모델 결과, 실제 입력 SHA256.
- `candidates.jsonl`: 후보별 PAINS/BRENK 경고, 원천 구조 일치, assay별 실측 기록 또는 예측·보류 근거.
- `model-metrics.json`: 모든 분할의 구조·활동 ID, 기준 비교, 실제 독립 시험 예측과 정답.
- `curated-records.json`, `assay-curation.json`, `sources/`: 정제 결과와 제외·충돌 근거, 원본 JSON·SHA256 영수증.

후보 파일은 `.part`에 완성한 뒤 원자적으로 교체합니다. summary도 실행 중·실패·완료 상태를 원자적으로 기록하므로 중간 파일을 완료된 결과로 읽지 않습니다. 수치 연산은 최대 두 개 스레드를 사용하고 LLM·AlphaFold·QPU 작업을 제출하지 않습니다.

[RDKit FilterCatalog](https://www.rdkit.org/docs/source/rdkit.Chem.FilterCatalog.html)의 PAINS/BRENK 일치는 구조·간섭 가능성 검토를 위한 신호입니다. 경고가 없다고 안전성을 확인한 것이 아니며, 경고가 있다고 곧바로 인체 독성을 입증한 것도 아닙니다. 다른 assay·독성 endpoint에 대한 평가 결과는 해당 별도 보고서에서 확인해야 합니다.

관련 테스트 9개가 endpoint·assay 분리, 충돌 반복 기록 배제, 저신뢰·변이·구간 기록 배제, 네 분할의 scaffold 비중복, 보정 분위수, 약한 모델 거부, 적용 영역 밖 예측 보류, 원자적 파일 교체와 실패 상태를 확인합니다. 테스트와 Ruff 검사를 통과했으며 실제 최종 후보 행 수·입력 SHA256·출력 SHA256도 확인했습니다.
