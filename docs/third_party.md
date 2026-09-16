# 외부 구성요소와 출처

HerbFold 자체 코드, upstream AF3 소스, 모델 파라미터, 공개 데이터의 이용조건을 각각 확인해야 합니다. 모델 파라미터와 전체 유전 데이터베이스를 이 프로젝트에 포함하거나 재배포하지 않습니다.

| 구성요소 | 보존 위치 | 출처/버전 |
|---|---|---|
| AlphaFold 3 | external/alphafold3 (gitignore) | Google DeepMind v3.0.4, commit 85c4d20505fd5cef05eac22b534d4e793971ae69 |
| AF3 파라미터 | 기존 사용자 디렉터리, .env 경로만 | 별도 모델/출력 이용약관 |
| 3Dmol.js | src/herbfold/static/vendor/3Dmol-min.js | https://3dmol.org/build/3Dmol-min.js, 2026-09-07 |
| 3Dmol 라이선스 | src/herbfold/static/vendor/3Dmol-LICENSE.txt | https://raw.githubusercontent.com/3dmol/3Dmol.js/master/LICENSE |
| 분자 구조 | data/compounds.json | 각 항목 PubChem CID/출처/조회일 포함 |
| 단백질 예시 | data/ptgs2.json | UniProt P35354, 출처/조회일 포함 |
| 친화도 검증 | data/benchmarks/ptgs2_ki.json | ChEMBL assay/activity/document 출처 포함 |
| Python 의존성 | uv.lock | 버전/해시가 고정된 독립 가상환경 |

3Dmol-min.js SHA256: `37ece6ae5a8577978db3771c013127464c3a560a1e5f47e85b85bd57da91ed86`.
원본 라이선스 SHA256: `4c6eaaed856f3f28a3b1a98e74f4a8a71618de7d51ea4155c29f6f793bcef861`.

Google Fonts는 선택적 온라인 글꼴입니다. 연결이 없어도 시스템 글꼴로 화면을 사용할 수 있습니다. 공개 데이터의 원자료 라이선스와 귀속 조건은 각 제공기관 문서를 따릅니다.
