# AlphaFold 3 실행 연결

**현재 설정 — 2026-09-08:** 공식 v3.0.4 README가 안내하는 Google 저장소에서
학습된 파라미터를 직접 내려받아 `AF3_MODEL_DIR=/home/jerisuh/models/af3-google-20260604`를
적용하고 API 서버를 재시작했다. 파일 크기 1,020,545,840바이트, Google CRC32C 일치,
전체 405개 레코드의 공식 스키마·수치 검사 통과를 확인했다. 기존 시험 파일은 보존했다.
[공식 다운로드 안내](https://github.com/google-deepmind/alphafold3/tree/v3.0.4#obtaining-model-parameters),
[취득·무결성 기록](af3-google-weights-acquisition.json), [독립 전체 검사](af3-google-weights-validation.json).
이후 공식 전체 9종 MSA·템플릿 DB의 설치·검증과 `AF3_DB_DIR` 설정을 완료했다.
운영 서버는 표준 검색 준비 검사를 통과하며, 전체 COX-2 검색에서 unpaired 11,229행,
paired 17,801행과 템플릿 4개를 얻었다. 아스피린과 퀘르세틴을 각각 별도 GPU 추론했고
최상위 pTM/ipTM은 0.90/0.88과 0.89/0.85이다. 퀘르세틴은 검증된 단백질 피처를
재사용하면서 선택 리간드의 구조를 별도로 계산했다. 두 물질의 실제 검색·추론·비교와
정확도 해석 범위는 [MSA 검증 보고서](af3-msa-validation.md),
[새 작업 기록](af3-msa-selected-predictions.json), [전체 DB 구성](af3-full-msa-setup.md)에 있다.

**보존한 MSA 미사용 기준 계산:** 아스피린과 퀘르세틴은 각각 실제 GPU 실행을 완료해 5개씩 총 10개 표본을 저장했다.
1 seed, 5 diffusion samples, 10 recycles, 사람 PTGS2 604개 잔기와 각 리간드를 사용했고
MSA/template은 명시적으로 생략했다. 두 작업은 각각 약 164.59초, 162.59초에 종료했다.
선택 물질과 실제 출력의 전체 원자·결합 그래프 및 표적 서열은 일치한다. 낮은 pTM
(0.20/0.21), ipTM(0.37/0.32)을 갖는 탐색 출력이며, 구조 정확도·약효·안전성은 검증하지 않았다.
처음 표시된 34개의 긴 결합 경고는 RDKit ILE 이름 템플릿의 CG2–CD1 연결 오류에서
발생했다. 공식 CG1–CD1 연결로 수정 후 두 모델의 긴/짧은 결합 경고는 0개이다.
모든 원본 좌표와 해시는 그대로이며 `quality_pass=null`은 전반적인 품질 미평가를 뜻한다.
[ILE 보정 기록](af3-ile-template-correction.json), [원본 구조 검사](af3-trained-structure-quality.json).

> **2026-09-08 정정:** 기존 `/home/jerisuh/models/af3.bin.zst`는 64바이트의
> 영(0) 식별자와 모든 비메타데이터 텐서의 균등분포가 공식 문서의 무작위 성능 시험용
> 파라미터 생성 방식과 일치한다. 학습된 모델의 출처는 확인되지 않았다. 아래의 기존
> 이부프로펜 실행 기록은 소프트웨어 실행만 입증하며, 생물학적 AF3 예측으로 사용할 수
> 없다. 해당 작업은 `quarantined`, `prediction_eligible=false`로 표시하고 원본 좌표와
> 실행 로그는 보존했다. [검사 기록](af3-parameter-audit.json),
> [공식 성능 시험용 파라미터 설명](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/model_parameters.md).
> 이 판정은 과거 시험 파일에 대한 것이며, 새로 취득한 공식 파일의 검사와 구분한다.

현재 Blackwell/JAX 0.10.2 환경에서는 작은 독립 수치 실험에서도 JIT 연산 오차가
재현됐다. AF3 자식 프로세스에만 `AF3_XLA_FLAGS=--xla_gpu_autotune_level=3`을 적용해
같은 실험이 통과하도록 했다. 이는 무작위 가중치를 학습된 가중치로 바꾸지 않으며,
이전 분자 출력의 모든 오류 원인을 확정하는 증거도 아니다.
[수치 검사](af3-numerics-verification.json),
[관련 JAX 이슈](https://github.com/jax-ml/jax/issues/39336).

이 플랫폼은 **AlphaFold 3 v3.0.4**와 `alphafold3` JSON **schema 4**를 사용한다. 2026-09-07에 공식 GitHub release/API와 태그의 소스 코드를 직접 확인했다. 정확한 커밋은 `85c4d20505fd5cef05eac22b534d4e793971ae69`이다. 검색 결과에는 이전 3.0.3이 최신으로 남아 있어, 배포 때는 [공식 release](https://github.com/google-deepmind/alphafold3/releases/tag/v3.0.4)를 기준으로 삼았다. 이 플랫폼의 버전 고정은 추후 자동으로 바뀌지 않는다. 새 release를 검토한 뒤 어댑터·입출력 테스트와 함께 갱신한다.

AF3는 단백질–리간드 복합체의 **구조**를 예측한다. `ipTM`, `pTM`, `ranking_score`는 구조 신뢰도이며, Kd/Ki/IC50나 결합 에너지 또는 약효가 아니다. 후보 간 수치 비교는 구조 품질 관리에 사용할 수 있지만, 보정된 결합력 비교로 해석할 수 없다. [공식 출력 설명](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/output.md)

## 코드·가중치·상업화의 구분

3.0.3부터 AF3 **소스 코드**는 Apache 2.0이다. Google 제공 **모델 가중치와 그 출력**에는 별도의 이용 조건이 적용되며, 현재 표준 가중치는 비상업 기관의 비상업 용도에 한정된다. 상업 기관을 위한 연구도 그 허용 범위에 포함되지 않는다. 신약의 상업화를 진행하려면 프로젝트에 적용되는 별도 권한을 확인해야 한다. 플랫폼 소스 코드의 설치가 AF3 가중치나 상업 사용 권한을 제공하지는 않는다. 플랫폼은 동의를 대신하거나 제한된 가중치를 재배포하지 않는다. [공식 가중치 조건](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/WEIGHTS_TERMS_OF_USE.md), [코드 라이선스](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/LICENSE)

가중치는 [공식 README의 다운로드 안내](https://github.com/google-deepmind/alphafold3/tree/v3.0.4#obtaining-model-parameters)에 있는 Google 직접 제공 파일을 `AF3_MODEL_DIR`에 둔다. `.bin` 또는 `.bin.zst`와 지원되는 분할 파일을 사용할 수 있다. 플랫폼은 제한된 CPU 검사로 파라미터 형식과 시험용 식별자를 확인한다. 이번 취득에서는 추가로 Google CRC32C, 전체 SHA-256, 모든 레코드의 공식 스키마 일치를 검사했으며 결과를 별도 기록했다.

## 설치 및 하드웨어

권장 대규모 실행 환경은 NVIDIA GPU가 있는 Linux이다. 공식 가이드의 A100/H100 80 GB 실험은 최대 5,120 토큰을 다루며, MSA 검색은 큰 RAM과 디스크를 요구한다. 데이터베이스 전체 설치에는 최대 약 1 TB를 계획하고 실제 대상과 잔여 공간을 확인한다. 작은 분자·단백질 입력도 모델 파라미터는 필요하다. **3.0.4부터 CPU 실행도 가능**하지만 느리고 메모리를 많이 사용할 수 있다. Apple MPS는 커뮤니티 JAX 플러그인 기반의 실험적 경로다. 여러 GPU의 VRAM이 자동으로 합쳐지는 것은 아니다. [설치 가이드](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/installation.md), [3.0.4 변경 사항](https://github.com/google-deepmind/alphafold3/releases/tag/v3.0.4)

네이티브 설치 예시:

```bash
git clone --branch v3.0.4 --depth 1 https://github.com/google-deepmind/alphafold3.git external/alphafold3
cd external/alphafold3
uv venv --python 3.12
uv sync --locked --no-dev
uv run build_data
uv run python run_alphafold_data_test.py
```

GPU 실행에 필요한 JAX/CUDA 의존성은 공식 lockfile과 해당 하드웨어용 설치 지침을 따른다. HMMER의 `jackhmmer`, `nhmmer`, `hmmalign`, `hmmsearch`, `hmmbuild`가 작업 프로세스의 `PATH`에 있어야 한다. 데이터베이스 원본은 [공식 `fetch_databases.sh`](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/fetch_databases.sh)의 고정 목록을 사용한다. 플랫폼에서는 `uv run --locked --extra databases python scripts/install_af3_databases.py --directory /absolute/path/to/af3_databases`로 재개 가능한 다운로드·전체 검증·완료 manifest를 생성한다. 단순 다운로드나 일부 경로만으로는 표준 검색 준비를 통과하지 않는다. 세부 절차는 [MSA 운영 문서](af3-calculation-workflow.md)를 따른다.

서버 환경 변수 예시(절대 경로로 교체):

```dotenv
AF3_RUNNER=native
AF3_REPO_DIR=/srv/herbfold/external/alphafold3
AF3_PYTHON=/srv/herbfold/external/alphafold3/.venv/bin/python
AF3_MODEL_DIR=/srv/af3/models
AF3_DB_DIR=/srv/af3/public_databases
AF3_DEVICE=gpu
AF3_NUM_DIFFUSION_SAMPLES=5
AF3_NUM_RECYCLES=10
AF3_MAX_TEMPLATE_DATE=2021-09-30
AF3_FLASH_ATTENTION=triton
AF3_CUDA_VISIBLE_DEVICES=1
AF3_PREALLOCATE=false
```

이 서버에서 전역 `LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64:`가 공식 환경의 CUDA 12.9 라이브러리와 충돌하여 cuSPARSE를 불러오지 못했다. `execution_environment()`는 자식 AF3 프로세스에서만 이 변수를 제거하고 GPU 메모리 사전 할당을 끈다. 네이티브 GPU는 `AF3_CUDA_VISIBLE_DEVICES`로 선택하며, 다른 작업의 사용량을 확인하고 설정한다. 특별한 라이브러리 경로가 필요한 관리자는 `AF3_LD_LIBRARY_PATH`로 명시할 수 있다. 전체 환경 변수는 작업 파일에 저장하지 않는다. [JAX 설치 지침](https://docs.jax.dev/en/latest/installation.html), [메모리 설정](https://docs.jax.dev/en/latest/gpu_memory_allocation.html)

CPU 실행은 `AF3_DEVICE=cpu`로 바꾼다. 어댑터가 `--jax_backend=cpu --flash_attention_implementation=xla`를 함께 넣는다. MPS도 `xla`를 사용하며 Docker에서는 지원하지 않는다. 네이티브 실행 명령에는 **3.0.4 실제 플래그인 `--num_diffusion_samples`**를 쓴다. `AF3_MAX_TEMPLATE_DATE`는 템플릿 누출을 통제하는 날짜이며, 기본값은 upstream과 같은 `2021-09-30`이다. 연구의 시간 분할에 맞추어 명시하고 기록한다.

Docker를 선택하면 동일 태그 소스에서 커밋 정보를 넣어 빌드한다:

```bash
cd external/alphafold3
docker build \
  --label org.opencontainers.image.revision=85c4d20505fd5cef05eac22b534d4e793971ae69 \
  --label org.opencontainers.image.version=3.0.4 \
  -t herbfold-af3:3.0.4 -f docker/Dockerfile .
```

서버에서 `AF3_RUNNER=docker`, `AF3_DOCKER_IMAGE=herbfold-af3:3.0.4`를 설정하고 모델·DB 경로도 제공한다. GPU 컨테이너에는 NVIDIA Container Toolkit이 필요하다. 입력/가중치/DB는 읽기 전용으로 마운트하고 출력만 쓰기 가능하게 한다. 네트워크는 차단한다. 준비 시 `docker image inspect`의 이미지 ID를 기록하고 실행 argv에서도 그 불변 ID를 사용한다. 태그만 같고 커밋 라벨이 다른 이미지는 사전 점검을 통과하지 못한다.

## Python 연결 계약

```python
from pathlib import Path
from herbfold.alphafold import (
    AF3Config, build_input, prepare_job, parse_outputs, safe_output_path,
    execution_environment,
)

fold_input = build_input(
    name="target_quercetin",
    proteins=[{"id": "A", "sequence": "ACDEFGHIKLMNPQRSTVWY"}],
    ligands=[{"id": "B", "smiles": "O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12"}],
    seeds=[1, 2, 3],
    msa_mode="search",
)
plan = prepare_job(Path("data/jobs/example"), fold_input, AF3Config.from_env())
# The platform's background worker executes plan["command"] with shell=False,
# cwd=plan["cwd"], env=execution_environment(), only if plan["runnable"] is True.
# It then records the process exit status and artifacts.
# Once the process exits successfully:
results = parse_outputs(plan["output_dir"])
# Serve only paths resolved with safe_output_path(plan["output_dir"], relative).
```

위 짧은 단백질 서열은 인터페이스 예시이며 검증된 생물학적 표적이나 약효 실험이 아니다. 실제 분석에서는 확인된 표적 서열과 출처를 넣는다.

`build_input(name, proteins, ligands, seeds=None, msa_mode="search")`는 단백질과 연결된 SMILES 리간드를 지원한다. `id`에는 대문자 문자열 또는 동일 분자의 여러 사슬 ID 목록을 쓸 수 있다. 생략하면 사용하지 않은 ID를 자동 배정한다. 단백질 서열은 표준 20개 잔기와 `X`를 허용한다. `description`은 schema 4에 맞게 보존된다. RDKit으로 SMILES를 검증하고 이성질체 정보를 유지한다. 프로톤화 상태와 지정되지 않은 입체화학은 연구자가 검토해야 하며, 플랫폼이 임의로 최적 상태를 주장하지 않는다. [입력 형식](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/input.md)

- `msa_mode="search"`: MSA/템플릿 필드를 생략하고 정식 검색 파이프라인을 실행한다.
- `msa_mode="none"`: `unpairedMsa=""`, `pairedMsa=""`, `templates=[]`와 `--run_data_pipeline=false`를 사용한다. DB 없이 실행할 수 있으나 정보가 줄어 정확도가 저하될 수 있다.
- 시드는 명시적 uint32 범위 `0..4294967295`, 기본 `[1]`이다. 중복은 거절한다. 매 시드당 기본 5개 샘플을 만든다. 동일 시드라도 GPU/라이브러리/버전 차이에 따른 완전한 비트 재현성을 보장하지 않는다.
- 플랫폼 한도는 100개 시드, 총 128개 사슬, 단백질 잔기 총 10,000개, SMILES 20,000자이다. 이는 요청 검증 한도이며 실제 하드웨어에서 처리할 수 있다는 의미가 아니다.
- 단백질–리간드 요청에 임의 로컬 경로, 사용자 CCD, RNA/DNA, 공유결합 또는 외부 MSA 필드를 넣으면 거절한다. 이 고급 형식이 필요하면 upstream AF3의 별도 관리된 워크플로를 사용한다.

`capabilities(config=None, msa_mode="search", probe_runtime=False)`는 설정·실행 파일·가중치 파일 존재와 버전 출처를 점검한다. `probe_runtime=True`는 지정한 Python에서 실제 AF3와 JAX를 import하고 디바이스를 확인한다. `prepare_job`은 이 상세 점검을 수행한다. DB 전체의 정확성이나 메모리 적합성은 사전 점검만으로 보장되지 않는다.

`prepare_job`은 실행할 수 없어도 `fold_input.json`, `af3_manifest.json`을 저장하고 `blockers`를 반환한다. 매니페스트에는 입력 SHA256, 태그/커밋, 엔트리포인트 SHA256, 수정 여부, 런타임·디바이스, 샘플 수, 시드, 템플릿 날짜, argv가 기록된다. 서로 다른 입력은 같은 작업 디렉터리에 덮어쓰지 않는다. HTTP 요청은 실행 파일/경로/플래그를 지정할 수 없으며 서버 설정만 사용한다.

`parse_outputs`는 실제 디렉터리에서 `*_summary_confidences.json`과 대응하는 `*_model.cif`를 찾고 점수 범위와 사슬 배열을 확인한다. 순위가 가장 높은 복사본과 개별 seed/sample을 구분한다. JSON의 `null`은 신뢰도 부재로 남기고 clash는 경고한다. 결과가 없으면 구조나 결합력을 만들어내지 않는다. 파일만으로 실제 AF3 실행을 입증할 수 없으므로 파서의 `execution_verified=False`를 유지하고 작업 실행 기록과 함께 판단한다. 경로는 상대 경로이며 다운로드 시에도 `safe_output_path`로 symlink 이탈과 경로 탐색을 다시 검사한다.

## 확인한 것과 남는 검증

39개 어댑터 단위 테스트는 schema/시드/SMILES/입체화학, 셸 없이 구성한 native·Docker 명령, MSA 설정, 매니페스트, 실제 형식의 confidence/mmCIF 가져오기, 잘못된/누락 점수와 경로 이탈을 다룬다. 테스트에 쓰는 작은 mmCIF는 **합성 테스트 fixture**이며 과학적 예측 결과가 아니다.

실제로 설치한 공식 AF3 3.0.4 파서 `Input.from_json`에 검색 모드와 MSA-free 모드 입력을 각각 넣어 모두 성공했다. homomer 사슬, chiral SMILES, description, uint32 양 끝 시드를 포함했다. 기존 정식 형식의 가중치 파일을 연결한 상세 사전 점검도 GPU에서 통과했고, 작은 JAX 배열 연산을 실제 CUDA 디바이스에서 확인했다. 이 점검 자체를 단백질 구조 추론 성공으로 해석하지 않는다.

AF3 추론의 최종 검증에는 정식 가중치, 대상 입력에 맞는 계산 자원, MSA 검색 시 전체 DB가 필요하다. 가중치가 제공되면 작은 알려진 복합체부터 정식 실행하여 구조 아티팩트와 작업 로그를 검증한 뒤 범위를 늘린다. 한약 성분과 기준 양약을 같은 표적·시드·MSA·템플릿 날짜 조건에서 비교하고, 결합력 검증은 별도의 실험 레이블과 데이터 분할을 사용해야 한다.


## 실제 추론 검증 (2026-09-07)

`scripts/verify_af3.py`로 human PTGS2 (UniProt P35354, 604 aa)와 PubChem ibuprofen을 실제 GPU에서 추론했다. seed 1, sample 1, recycles 3, MSA/템플릿 없음의 **실행 점검용 설정**이며, 총 166.16초 후 정상 종료했다. 출력 mmCIF와 confidence JSON, 단백질–리간드 좌표 기반 포켓 특성 가져오기를 확인했다. 상세 기록과 파일 해시는 [af3_verification.json](af3_verification.json)에 있다.

결과 pTM/ipTM은 모두 0.39, ranking score는 -99.11이며 significant clash가 보고되었다. **소프트웨어 실행은 검증되었지만 이 구조는 품질 검토를 통과하지 못하며 결합력 분석 근거로 사용하면 안 된다.** MSA-free 단일 샘플 결과를 생물학적 효능이나 플랫폼의 예측 정확도 검증으로 제시하지 않는다.

실제 3.0.4 출력에서 문서와 다른 점도 확인했다. `summary_confidences.json`의 `chain_ids`에 사슬당 하나가 아니라 토큰마다 ID가 반복된다. pinned upstream `confidence_types.py:194`도 token_chain_ids를 그대로 쓰고 있다. 어댑터는 연속된 동일 ID를 원래 순서대로 한 번씩 정리하고 사슬별 점수 행렬 크기를 검증한다. 두 가지 회귀 테스트로 이 실제 출력 호환성을 확인했다. 원본 출력 파일은 수정하지 않았다.


## 실제 AF3 구조를 측정·예측 행의 특성으로 연결

`herbfold.features.enrich_records(records, store, min_iptm=0.6)`는 각 행이 명시한 `af3_job_id`의 실제 구조에서 기하 특성을 계산한다. `af3_model_index`는 선택 사항인 0부터 시작하는 모델 번호이며, 생략하면 저장된 최상위 구조 복사본을 사용한다. 모든 행에는 `smiles`와 `protein_sequence`가 필요하다. 실험 행과 아직 라벨이 없는 질의 행 모두 사용할 수 있고, 기존 측정값·단위·endpoint는 변경하지 않는다.

완료되고 실행이 확인된 native AF3 작업만 허용하며, 정확히 단백질 A와 리간드 B 하나씩이어야 한다. 입력 파일 SHA256과 작업 payload, 단백질 서열, 입체화학·전하를 포함한 canonical SMILES, 저장된 구조 SHA256을 확인한다. 파일만 가져온 작업, 다른 표적/리간드, 추가 사슬, 잘못된 경로와 수정된 구조는 거절한다.

기본 품질 조건은 `has_clash=False`, `ipTM>=0.6`이다. 이는 구조 품질을 거르는 **휴리스틱**이며 통과해도 결합이나 친화도가 검증되는 것은 아니다. 값이 없거나 조건을 만족하지 못하면 행 번호와 함께 실패하고 부분 결과를 반환하지 않는다. 이번 PTGS2 smoke 결과는 clash/낮은 신뢰도로 이 단계에서 거절되는 것을 실제 통합 테스트로 확인했다.

성공한 행에는 mmCIF에서 계산한 유한한 `structure_features`와 입력·구조 해시, 작업/모델, 품질 조건을 기록한 `structure_provenance`가 붙는다. 직접 입력된 기존 구조 특성은 이 검증된 계산값으로 교체한다. 동일한 특성 체계를 학습·평가·추론에 사용하고, 실험 친화도 레이블은 별도 검증해야 한다.
