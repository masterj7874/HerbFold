# HerbFold Astra v0.2.0 검증 — 2026-09-07

새 React UI, 실제 분자 그래프, GPT-6 Astra 역할별 오케스트레이터를 통합했습니다. 기존의 RDKit·실측 Kd/Ki·공식 AF3·IBM Runtime 계산 모듈과 실제 작업 기록을 보존했습니다.

## 실행 결과

| 검증 항목 | 실제 결과 |
| --- | --- |
| Python 전체 테스트 | 185개 통과, 의존 라이브러리 deprecation 경고 2개 |
| Ruff / TypeScript | 통과 |
| Vite 빌드 / npm audit | 통과 / 취약점 0개 |
| Wheel / sdist | 생성 완료, UI·검증 예제 포함, 환경 비밀 키와 node_modules 제외 확인 |
| 실제 Astra 분석 | `6261f526e20240a3881377a0992ae650`, 8단계 completed |
| 실제 모델 | OpenAI Responses API `gpt-6-astra`; 다른 모델 대체 없음 |
| 사용량 | 8회 호출, 입력 23,189 / 출력 5,264토큰, 원본 조회 3건 |
| 입력 / 후보 | 바이칼레인 + aspirin → BRICS 계보가 검증된 후보 4개 |
| 구조 단계 | PTGS2 서열 조회 후 후보 1개의 AF3 입력 준비; 이번 분석의 추론 실행 0회 |
| 양자 단계 | 실제 로컬 4큐빗 statevector, 모체 2개 + 후보 4개의 6×6 커널 |
| 브라우저 | 성분·후보·원자·결합·거리·실측 평가·분석·기록·모바일 흐름 통과 |
| IBM 가용 장비 조회 | 156큐빗 장비 3대, 현재 최대 폭 선택 `ibm_marrakesh`; 읽기 전용 확인 |
| 원자 거리 | 원본 좌표 1.397725461 Å → 표시 1.398 Å |
| 키보드 접근성 | 초기 초점, Tab 순환, 배경 inert, Escape, 초점·스크롤 복원 통과 |
| 개발 프록시 | 동일 출처 POST 200, 외부 출처 POST 403 확인 |

처음 실행에서는 근거·화학 전문 에이전트가 아직 시작하지 않은 후속 작업까지 자기 역할로 해석하고 중단했습니다. 역할별 범위를 명확하게 수정한 뒤 **명시적으로 재개**했습니다. 원래 도구 결과와 조정자의 계획을 재사용했고, 이전 판단·사건은 삭제하지 않았습니다. 재개 후 후보 설계·계산·독립 검토·한국어 보고서가 원래 8회 호출 예산 안에서 완료됐습니다. [기계 판독 검증 기록](astra-verification.json), [실제 계산·보고서](astra-research-report.json), [UI 검증](astra-ui-verification.json), [원자 클릭·거리·접근성 검증](astra-viewer-verification.json), [단백질→원자 확대 검증](astra-protein-verification.json)을 보존했습니다.

## 구조의 의미와 남은 실험

RDKit 구조는 ETKDGv3 + MMFF94s/UFF로 생성한 배좌입니다. 표시되는 kcal/mol 값은 해당 배좌의 내부 에너지이며 결합 에너지가 아닙니다. PDB 5IKR은 실제 COX-2–mefenamic acid 실험 복합체로, 새 후보의 검증된 결합 구조로 표시하지 않습니다. mmCIF 원자 좌표와 CCD 결합 정의에 따라 렌더링하며, 확대해 결합 차수와 이웃을 확인할 수 있습니다.

기존 실제 AF3 GPU 점검 결과는 pTM/ipTM 0.39와 충돌·비정상 결합거리 때문에 품질 불합격입니다. 새 프로그램은 그 좌표를 보존하고 경고합니다. 전체 MSA/템플릿 데이터베이스는 아직 설치되지 않았으므로 기본 검색 추론은 명시적으로 차단됩니다. MSA 없는 탐색 실행 경로와 공식 AF3 v3.0.4 런타임은 기존 실제 GPU 실행으로 검증됐습니다. [기존 AF3 실행 보고서](af3_verification.json).

IBM은 접근 가능한 정상 장비의 사용 가능 큐빗 수를 실행 시 조회하고 최대 폭을 선택합니다. 기존 `ibm_fez` 156큐빗 실행은 실제 IBM 작업 ID와 측정값으로 검증돼 있습니다. 이번 UI/오케스트레이터 통합 검증에서 QPU를 추가 제출하지 않았습니다. 해당 전체 폭 실행의 0 커널은 잡음·정보 소실을 보여주었으며, 최대 큐빗이 예측 개선이나 양자 우위를 보장하지 않습니다. [기존 IBM 실행 보고서](quantum_verification.json).

후보의 표적 결합, 효능, 독성, 합성 가능성, 특허 신규성은 미검증입니다. 실측 데이터 기반 친화도 API와 평가 화면을 제공하지만 자동으로 가짜 친화도나 임상 효능을 붙이지 않습니다. 제공 논문의 미공개 데이터·코드와 수치 불일치 때문에 논문의 성능을 완전히 재현했다고 주장하지 않습니다. [논문 검토](science.md).

## 재검증

```bash
uv run pytest -q
uv run ruff check src tests scripts
./scripts/build_frontend.sh
./scripts/run.sh
# 별도 터미널: Playwright와 시스템 Chrome이 있는 Python 환경
python scripts/verify_astra_ui.py
```

브라우저 스크립트는 Local 모드만 실행하며 LLM·GPU·QPU 작업을 제출하지 않습니다. 실제 모델 호출은 새 분석에서 Astra 모드를 선택해야 합니다. 각 분석의 질문·선택 분자·도구 결과가 모델 API로 전송되며 실제 사용량과 응답 ID가 기록됩니다.
