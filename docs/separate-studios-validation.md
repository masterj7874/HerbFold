# 양자·AlphaFold 스튜디오 분리와 구조 진단

2026-09-08. 양자 특징 분석과 AlphaFold 구조 예측을 독립 메뉴·주소·선택 상태를 가진 작업 공간으로 분리했습니다. 기존 계산 기록과 원시 측정값은 유지합니다.

| 작업 공간 | 주소 | 확인할 내용 |
| --- | --- | --- |
| AlphaFold 스튜디오 | http://127.0.0.1:8873/#alphafold | 성분·단백질 표적, 원자 3D 구조, 해당 sample의 pTM·ipTM·pLDDT·PAE, MSA와 구조 검사 |
| 양자 스튜디오 | http://127.0.0.1:8873/#quantum | 실제 입력 특징과 순서, 원본·연결된 계산, IBM 관측값·오차·반복 대조·고전 기준 |

## 독립 작업 공간

AlphaFold 스튜디오에서는 단백질의 UniProt ID를 직접 적용하고 선택 성분의 계산·기록을 엽니다. 적용하지 않은 표적 변경이 있으면 이전 표적의 기록을 여는 버튼을 비활성화합니다. 원자 확대·축소와 선택, 자유 분자·실험 구조·AF3 예측 출처 선택, 표준 MSA 계산과 기록 비교 기능을 유지합니다.

양자 스튜디오는 분석별 보기와 최신 양자 실행 최대 50개의 기록 보기를 제공합니다. 입력 분자·기술자·전처리·특징 해시를 확인하고, 저장된 입력으로 양자 단계만 별도 계산할 수 있습니다. 원본 fidelity와 새 projected 결과를 구분합니다. 개별 실행에서 연결된 원본 분석으로 이동하거나 결과 JSON을 내보낼 수 있습니다.

각 스튜디오의 선택은 다른 스튜디오에 임의로 적용되지 않습니다. URL 직접 열기, 브라우저 뒤로가기, 새로고침 후 선택 복원을 지원합니다. 기존 `#studio` 주소는 AlphaFold 공간으로 연결합니다. 모바일에서는 메뉴를 아래로 옮겨 작업 영역의 폭을 확보했습니다.

## 같은 구조 파일에 연결한 진단

읽기 전용 `GET /api/molecular/predictions/{job_id}/diagnostics`를 추가했습니다. `file`, `smiles`, `target_accession`으로 현재 선택을 대조합니다. 다른 분자·표적·등록 파일 요청은 거부하며, 선택 파일이 검증되지 않으면 다른 sample로 대체하지 않습니다.

확인 항목은 다음과 같습니다.

- 등록 CIF SHA-256, 전체 표적 서열 해시, 선택 리간드의 원자·결합 그래프
- 같은 sample의 pTM·ipTM·충돌 플래그
- 표적 단백질 원자와 선택 리간드 원자의 pLDDT 각각의 평균·최소·최대·개수
- 같은 confidence 파일의 원자 순서·pLDDT, 토큰 사슬·잔기 수, 방향별 PAE
- 해당 작업 시점의 MSA·템플릿·가중치 검사 기록과 현재 실행 환경의 구분

PAE는 표적 사슬에 정렬해 리간드를 평가하는 방향과 그 반대 방향을 따로 표시합니다. 평균·최소·최대와 토큰 쌍의 개수를 함께 제공합니다. 현재 저장된 전체 PAE 배열은 0.1 Å로 직렬화되어 있으므로, 요약 JSON의 더 세밀한 값과 대조할 때 그 정밀도 차이를 반영합니다.

과거 confidence JSON에는 최초 저장 시점의 SHA가 등록되지 않은 경우가 있습니다. 현재 바이트의 SHA와 파일 이름·원자 대응·요약값 일관성 검사는 수행하지만, 이를 독립적인 출처 인증으로 표시하지 않습니다. [공식 AF3 출력 정의](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/output.md)

## 검증 범위와 해석

실제 아스피린·퀘르세틴의 MSA 사용/생략 작업 4개, 등록된 구조 파일 24개에서 선택 일치와 PAE 진단을 확인했습니다. 각 작업은 5개 sample과 최상위 sample의 복사본 1개를 포함하므로 24개가 서로 독립적인 예측은 아닙니다. [실제 등록 파일 점검](af3-selected-diagnostics-verification.json)

진단 화면의 단백질 pLDDT는 표적 단백질의 **원자별 평균**입니다. 이전 비교 보고서의 Cα 원자 평균과 분모가 다릅니다. MSA 행 수 역시 독립적인 상동 서열 수나 Neff를 의미하지 않습니다.

이 변경은 잘못 연결된 결과를 방지하고 구조·측정의 근거를 더 자세히 검토하기 위한 것입니다. UI 분리 자체가 예측 정확도를 높였다는 실험 결과는 아닙니다. pTM·ipTM·pLDDT·PAE는 구조 모델의 내부 신뢰도이며, 양자 커널은 분자 특징 유사도입니다. 이 지표들을 합산한 임의의 약효·안전성 또는 질병 진단 점수는 만들지 않습니다. [IBM projected kernel 정의](https://quantum.cloud.ibm.com/docs/en/tutorials/projected-quantum-kernels)

전체 Python 테스트 574개와 프런트엔드 검사 17개가 통과했습니다. 실제 저장 결과를 이용한 브라우저 검증·빌드와 원시 파일 대조의 최종 기록은 [소프트웨어 검증](separate-studios-software-verification.json)과 [화면 검증](separate-studios-ui-verification.json)에 저장합니다. 이 변경의 검증을 위해 새 AF3·IBM·LLM 계산을 제출하지 않았습니다.

```bash
.venv/bin/python -m pytest -q
node --test frontend/tests/*.test.mjs
cd frontend && npm run build
```

브라우저 연결이 없는 로컬 환경에서 기존 실측 자료로 화면 검사를 재현하려면 프로젝트 루트에서 `python scripts/verify_separate_studios_ui.py`를 실행합니다. 이 검사기는 실행·준비·신규 계산 API를 차단하며 기존 자료의 조회와 화면 이동만 검사합니다.
