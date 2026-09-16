# IBM Quantum fidelity kernel

**2026-09-08 갱신:** 새 API와 분석 화면의 기본은 큐빗별 X·Y·Z를 측정하는 `kernel_method="projected"`입니다. 기존 전역 fidelity의 0 행렬은 보존하고, 동일한 네 입력을 156큐빗 전체의 작은 연결 블록에서 별도로 측정했습니다. 실제 17회로×1,024 shots의 비대각 커널은 0.947082–0.993291, 별도 동일입력 반복값은 0.997848이었고 QPU 사용은 7초입니다. 새 커널의 대각 1은 RBF 정의에 따른 값입니다. 특정 큐빗의 준비·판독 오차가 최대 38.57%여서 큐빗별 오류와 재표집 범위의 한계를 함께 제공합니다. [새 계산 방법·실측 검증](quantum-projected-validation.md), [독립 원시 자료 감사](quantum-projected-independent-audit.json)

아래는 호환을 유지한 **기존 fidelity 방법과 역사적 검증**의 설명입니다. Python library의 기본은 기존대로 fidelity이고 `kernel_method="projected"`를 명시하면 새 경로를 사용합니다. 기존 저장 결과를 새 방법의 결과로 덮어쓰지 않습니다.

기존 fidelity 방법은 논문 §4.1의 `K(x, y) = |<φ(x)|φ(y)>|²`를 계산한다. 이 값은 입력 특징의 유사도이며 결합 에너지, pKd, 약효를 직접 나타내지 않는다. 논문의 양자 우위·정확도 수치를 재현했다고 주장하지 않는다. 결합 친화도 예측에는 같은 실험 endpoint와 단위를 가진 실제 측정값, 훈련 데이터, 외부 검증이 따로 필요하다.

## 구현과 입력

`src/herbfold/quantum.py`는 Qiskit 회로를 실제로 구성한다. 분자/복합체 특징 행렬은 유한한 숫자로 이루어진 직사각형 배열이어야 한다. 서로 다른 물리 단위의 descriptor를 사용한다면 **훈련 데이터에서만** 스케일러를 학습하고 검증·테스트 데이터에는 같은 변환을 적용한다.

각 입력을 `2 atan(x)`로 제한하고, 특징이 큐빗보다 많으면 strided column group의 평균으로 모든 열을 반영한다. 큐빗이 특징보다 많으면 특징을 반복 배치한다. 각 레이어의 모든 큐빗에 `Ry`, `Rz` 데이터 회전을 넣고 `CZ`로 연결한다. 이는 논문의 fidelity 정의를 실행하는 구체적인 연구용 feature map이며 논문에 설명되지 않은 학습 가중치를 만들어 쓰지 않는다. 회로 깊이와 인코딩 방식은 실험 기록에 보존한다. 데이터 반복이 정보량 증가나 양자 우위를 뜻하지는 않는다.

로컬에서는 선형 체인, IBM에서는 실제 연결 그래프의 spanning forest를 사용한다. 고장 큐빗·고장 2큐빗 연결을 제외하고 건강한 물리 큐빗에 초기 layout을 고정한다. 따라서 로컬·하드웨어의 topology가 다를 수 있으며 동일한 수치가 나와야 하는 실험으로 취급하면 안 된다. 하드웨어 결과에는 사용한 연결 개수·연결 성분 개수·회로 깊이를 기록한다.

`local_kernel`은 16큐빗 이하에서 정확한 statevector 내적을 계산한다. 하드웨어 폭의 회로는 로컬 statevector에 전달하지 않는다. IBM에서는 `U(y)† U(x)`를 먼저 파라미터화해 backend ISA로 컴파일하고 이후 특징값을 바인딩한다. 전체 측정 문자열이 0일 확률을 fidelity로 읽는다. 같은 입력 쌍의 대각 원소도 실제 측정하고, 강제로 1로 바꾸지 않는다. 이 측정법은 [IBM의 quantum kernel 예제](https://quantum.cloud.ibm.com/docs/en/tutorials/quantum-kernel-training)에 따른다.

## 최대 큐빗 정책

`mode="ibm", qubits="max"` 또는 IBM 기본값은 **계정에서 접근 가능하고 현재 operational인 실제 장비 중 건강한 큐빗 수가 가장 큰 장비**를 선택한다. 동률이면 대기 작업 수, backend 이름 순서로 선택한다. 장비명이나 광고된 최대 큐빗 수를 하드코딩하지 않는다. 선택된 모든 큐빗을 데이터 회전에 사용한다. `qubits=8`처럼 명시하면 작은 비교 실험도 할 수 있다. 백엔드 검색은 [공식 QiskitRuntimeService API](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/qiskit-runtime-service)를 사용한다.

2026-09-07 16:40 KST에 이 워크스페이스의 저장된 계정으로 읽기 전용 조회를 수행했다. 접근 가능한 operational 장비는 `ibm_marrakesh`, `ibm_fez`, `ibm_kingston`이며 각각 156개 건강한 큐빗이었다. 당시 대기 작업이 가장 적은 `ibm_marrakesh`가 선택되었다. 이는 해당 시점의 계정 접근 범위이며 IBM 전체 장비 최대치라는 뜻은 아니다. 이후 아래와 같이 실제 최대 큐빗 QPU 실행도 완료했다.

16:43 KST의 재조회에서는 대기열 변화에 따라 `ibm_fez`가 선택되었다. 실제 장비의 target을 받아 156큐빗 overlap 회로를 ISA로 컴파일하는 예비 검증에 성공했다. 156개 물리 큐빗 모두 활성 상태였고, 깊이는 45, 2큐빗 CZ 연산은 620개, 측정은 156개였다. 예비 계획의 기본 QPU 시간 상한은 120초였으며, 아래 실제 검증에서는 더 작은 30초 상한을 사용했다.

## 완료된 실제 156큐빗 검증

2026-09-07 16:59:59 KST에 `ibm_fez`의 156큐빗 전체를 사용하는 작업 **`daf6tvm42tqs73avi9u0`**를 제출했고, 완료 상태 `DONE`과 측정 결과를 회수했다. 제출 전에 공식 Runtime `instances()`/`usage()`로 현재 인스턴스가 **Open/free**, 사용 한도 600초, 사용량 19초, 잔여 581초임을 확인했다. 해당 무료 인스턴스를 명시적으로 고정한 뒤 1작업·3회로·회로당 1,024 shots·QPU 시간 상한 30초로 실행했다. 실제 `job.usage()`는 **3.0초**였고 잔여 무료 사용량은 578초였다. Open 인스턴스의 무료 조건은 [IBM 공식 인스턴스 설명](https://quantum.cloud.ibm.com/docs/en/guides/instances)을 확인했다.

입력은 PubChem 구조에서 계산한 퀘르세틴·루테올린의 분자량, logP, TPSA, HBD, HBA, QED였다. 행렬의 세 상삼각 회로 모두 all-zero 관측이 **0/1,024**여서 원본 커널은 `[[0, 0], [0, 0]]`이다. 각 확률의 Wilson 95% 구간은 `[0, 0.0037374]`였다. 실제 측정 bitstring 폭은 156이고, 최소 Hamming weight가 각각 7·9·7이므로 all-zero 문자열이 관측되지 않았음을 별도로 확인했다.

**최대 폭 하드웨어 실행 성공과 유용한 친화도 예측 성공은 구분해야 한다.** 같은 입력의 이상적 대각 원소가 1인 데 비해 이 실행의 대각 원소도 0으로 관측되어, 이 깊이·폭·shot 수에서 전역 fidelity를 유용하게 추정하지 못했다. 이를 결합 친화도 0, 약효 없음, 또는 양자 우위의 증거로 해석하지 않는다. 측정값을 대체하거나 다른 QPU 작업을 자동 재실행하지 않았다.

검증 근거와 정확한 특징·예산·job ID는 [실제 QPU 검증 기록](quantum_verification.json)에 있다. 플랫폼 Store 작업 ID는 `ad63801d6a444fe08452bcacc7dfbf53`이며 원본 manifest는 `runtime/ad63801d6a444fe08452bcacc7dfbf53/quantum.json`에 보존되어 있다. `scripts/verify_quantum.py`는 기본적으로 저장된 결과를 읽고, `--refresh`는 기존 작업만 조회한다. 이미 시도한 검증을 `--execute`로 다시 제출하는 것을 거부한다.

## 인증과 비용 범위

저장된 Qiskit Runtime 계정을 자동 사용하거나 서버 실행 환경에서 다음 변수를 제공한다. 토큰은 API 요청 본문, Git 저장소, 출력 파일에 넣지 않는다.

```bash
# 실제 비밀값은 셸 기록 대신 비밀 관리 도구 등으로 환경에 주입한다.
export IBM_QUANTUM_INSTANCE='your-instance-crn'
# IBM_QUANTUM_TOKEN 또는 QISKIT_IBM_TOKEN: IBM Cloud API key
# QISKIT_IBM_INSTANCE도 IBM_QUANTUM_INSTANCE의 별칭으로 지원한다.
# IBM_QUANTUM_ACCOUNT: 특정 저장된 account name 선택(선택 사항)
```

`ibm_quantum_platform` 채널을 사용하며 토큰이 없으면 저장된 계정을 읽는다. 계정 파일을 수정하거나 새 토큰을 저장하지 않는다. 인스턴스를 지정하면 계정의 어느 서비스 인스턴스를 사용할지 명확히 고정할 수 있다. 상세 방식은 [IBM 인증 API 문서](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/qiskit-runtime-service)에 설명되어 있다.

행 수 `N`에 대해 대각선을 포함한 `N(N+1)/2`개의 회로를 실행한다. 기본 제한은 전체 64회로, 회로당 1,024 shots, 작업당 16회로, 최대 4작업, 전체 65,536 shots다. 한 행렬의 모든 회로를 예산 안에서 실행하며 예산이 부족할 때 일부 쌍을 임의로 생략하지 않는다. 이를 넘는 요청은 네트워크 조회·작업 제출 전에 거부한다. 전체 회로 수의 절대 상한은 4,096이다.

각 Runtime 작업에 `max_execution_time=120`을 지정한다. 이 값은 **작업당 QPU 사용 시간의 상한**이지 대기 시간을 포함한 wall time이나 통화 단위 지출 한도가 아니다. 계획에는 작업 수 × 시간 상한을 함께 제공한다. 실제 계정 quota와 가격은 이 모듈이 알 수 없으며 `quota_status`에 확인되지 않았음을 표시한다. IBM이 적용하는 시간 제한은 [공식 실행 시간 문서](https://quantum.cloud.ibm.com/docs/en/guides/max-execution-time)를 따른다.

## API 사용

```python
from herbfold.quantum import (
    inspect_backends, plan_quantum, local_kernel, submit_kernel, retrieve_kernel,
)

features = [[0.1, 0.2, 0.3], [0.4, -0.2, 0.8]]
inventory = inspect_backends()                       # 읽기 전용
plan = plan_quantum(features, mode="ibm", qubits="max")
local_result = local_kernel(features, n_qubits=3)     # QPU 사용 없음

# execute=False 기본값: 계획만 반환하며 파일과 Runtime job을 생성하지 않는다.
preview = submit_kernel(features, "runs/quantum/example.json")

# 실제 사용자가 실행을 요청한 경우에만 호출한다.
submitted = submit_kernel(
    features, "runs/quantum/example.json", execute=True,
    qubits="max", shots=1024, max_circuits=64,
    circuits_per_job=16, max_jobs=4, max_total_shots=65536,
    max_execution_time=120,
)
# 즉시 반환되는 JSON에 각 Runtime job ID와 정확한 (행, 열) 순서가 들어 있다.
result = retrieve_kernel("runs/quantum/example.json") # 기다리지 않고 한 번 조회
if result["status"] == "completed":
    matrix = result["kernel"]
```

제출은 [SamplerV2의 job mode](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/sampler-v2)를 사용한다. 각 작업의 PUB 순서는 `(0,0), (0,1), …, (1,1), …`로 고정된다. `SamplerV2.run` 직후 반환되는 ID를 0600 권한의 manifest에 매번 저장하고, 완료될 때까지 기다리지 않는다. 결과 조회는 [RuntimeJobV2의 상태·결과 API](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/runtime-job-v2)를 사용한다.

기존 manifest 경로로 다시 제출하면 거부한다. 제출 중 일부 작업만 접수된 경우 `partial_submission`과 알려진 모든 job ID를 보존한다. 통신 실패 때 서버가 작업을 접수했는지 불명확할 수 있으므로 자동 재시도하지 않는다. IBM 작업 목록과 manifest를 확인한 뒤 새 실험 여부를 결정해야 한다. 실패·누락된 결과는 0 또는 예측값으로 채우지 않는다.

하드웨어 결과에는 all-zero count, 실제 shots, Wilson 95% 구간, 평균 대각선, 최소 고유값을 제공한다. 잡음 때문에 행렬이 PSD가 아닐 수 있다. 코드가 이를 숨기거나 자동으로 PSD로 바꾸지 않는다. 지도학습에 보정 커널을 쓰려면 보정 방법을 별도 실험으로 명시하고 독립적인 classical baseline과 비교해야 한다.

## 검증 범위

`pytest -q tests/test_quantum.py`는 정확한 Gram matrix의 대칭·대각선·PSD, 전체 입력 열 반영, 16큐빗 제한, 예산 사전 거부, 접근 가능 최대 장비 선택, 비밀 정보 없는 오류, 실제 Qiskit 133큐빗 FakeTorino의 전체 폭 ISA 컴파일, 3큐빗 실제 StatevectorSampler 결과의 비동기 제출/복구 계약, 일부 제출 실패의 job ID 보존을 검증한다. Fake 장비 검증과 로컬 sampler 검증은 실 QPU 실행이나 양자 이점 검증이 아니다.

공식 문서 확인일: 2026-09-07. 프로젝트의 잠금 환경 `uv run pytest -q tests/test_quantum.py`에서 21개 테스트가 통과했다(Qiskit 2.5.2, qiskit-ibm-runtime 0.49.0). 기존 시스템 환경(Qiskit 2.2.1, qiskit-ibm-runtime 0.42.0)에서도 최초 20개 테스트가 통과했다. 현재 공식 문서의 SamplerV2/RuntimeService 인터페이스를 사용하며 최신 버전에서 별칭이 추가된 서비스 클래스를 필수로 요구하지 않는다.
