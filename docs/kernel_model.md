# AF3 구조 특징과 양자 커널을 연결한 친화도 회귀 실험

`kernel_model.py`는 실제 측정값으로 선언된 `Kd` 또는 `Ki` 레코드에 대해 양자 fidelity kernel ridge regression을 학습하고, 같은 특징·같은 분할을 사용하는 classical RBF kernel ridge와 비교한다. 선택적으로 각 레코드의 `structure_features`에 AF3 복합체에서 추출한 접촉 수·최소 거리 등의 수치를 넣어 **분자 + 표적 + 구조 특징 → 양자 커널 → 측정 pKd/pKi 회귀**를 실행할 수 있다. 구조 특징이 없으면 분자/단백질 특징 실험으로 표시한다.

이 모듈은 제공된 수치가 AF3에서 실제 추출되었는지 외부 증명하지 않는다. 동일한 구조 추출 설정, AF3 작업 산출물, 원래 assay 출처를 연구자가 보존해야 한다. 측정된 레이블이 없으면 친화도 모델이나 성능 수치를 생성하지 않는다. 테스트 코드 안의 수치는 모두 합성 단위테스트 fixture이며 실제 연구 결과가 아니다.

## 데이터와 누출 방지

레코드는 `smiles`, `target_id`, `endpoint`, 양수 `value`, 농도 `unit`, `relation="="`, `is_measured=true`, `source`를 요구한다. `Kd`와 `Ki`는 별도 실험으로 처리하며 IC50은 임의로 변환하지 않는다. 표준화된 동일 ligand/target 중복을 거부한다. 8–24개의 선택된 측정 레코드가 필요하다.

`benchmark.prepare_records`와 동일한 분자 descriptor/Morgan fingerprint, 표적 특징, 구조 schema 검증을 재사용한다. `protein_sequence`는 모든 행에 있거나 모든 행에 없어야 한다. 구조 특징도 모든 선택 행의 키가 정확히 일치해야 한다. 단백질 서열이 없으면 훈련에서 알려진 target에 대한 one-hot 특징을 사용하며, 평가에서 처음 나타난 target을 전체 데이터로 미리 학습한 vocabulary에 끼워 넣지 않는다.

`scaffold`, `target`, `scaffold_target` 중 선택한 그룹으로 먼저 분할한다. `scaffold_target`은 같은 scaffold나 target으로 연결되는 행을 한 그룹으로 묶는다. 최소 훈련 4행·시험 2행을 요구한다. 표적을 분리하는 실험에는 모든 행의 단백질 서열이 필요하다. scaffold 전용 분할은 표적을 공유할 수 있고 target 전용 분할은 scaffold를 공유할 수 있으므로 overlap audit를 함께 제공한다.

feature schema와 StandardScaler는 **훈련 행에서만** 학습한다. 시험 행의 값이나 레이블로 스케일, vocabulary, alpha, gamma를 선택하지 않는다. 이 원칙은 [scikit-learn의 데이터 누출 지침](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)과 [StandardScaler의 훈련 통계 정의](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.StandardScaler.html)에 따른다.

## 고정된 비교 방법

두 회귀기는 동일한 훈련·시험 행과 동일한 표준화 입력을 사용한다. alpha는 1.0으로 사전 고정한다. RBF gamma는 `1 / 입력 특징 수`로 고정하며, 시험 결과를 보고 최적값을 고르지 않는다. 레이블 중심값은 훈련 pactivity 평균만 사용한다. 예측식은 `훈련 평균 + K_test,train (K_train,train + alpha I)^-1 (y_train - 훈련 평균)`이다. 이것은 [kernel ridge regression의 precomputed kernel 방식](https://scikit-learn.org/stable/modules/generated/sklearn.kernel_ridge.KernelRidge.html)을 직접 선형대수로 계산한 구현이다.

양자 측정이 잡음을 포함하면 훈련 kernel이 PSD가 아닐 수 있다. 이 경우 **훈련 블록만** 고유분해해 양의 고유공간에 투영한다. 훈련 블록은 `V+ Λ+ V+ᵀ`, 시험–훈련 블록은 `K_test,train V+ V+ᵀ`로 보정한다. 시험–시험 블록으로 고유공간을 학습하지 않는다. 투영 여부와 제거한 음의 고유값 수를 결과에 표시하며, 원본 QPU manifest를 수정하지 않는다. 이는 이 플랫폼에서 명시적으로 선택한 잡음 처리 방법이며 양자 우위를 보장하는 조치가 아니다.

결과에는 quantum, classical RBF, 훈련 평균 baseline 각각의 MAE·RMSE·R²·Pearson·Spearman 및 각 시험 행의 예측이 들어간다. 변화가 없어 정의할 수 없는 지표는 null로 표시한다. 작은 단일 holdout의 차이를 신뢰구간, 양자 우위, 전향적 검증 또는 약효로 해석하지 않는다.

## 로컬 실행

```python
from herbfold.kernel_model import run_local_experiment

# records는 출처가 있는 실제 측정 행을 파일/API에서 읽어 제공한다.
result = run_local_experiment(
    records, endpoint="Kd", split="scaffold_target",
    test_fraction=0.25, seed=42, qubits=4, layers=2,
)
experiment = result["experiment"]
quantum_artifact = result["quantum"]
comparison = result["evaluation"]
```

로컬 statevector는 16큐빗 이하로 제한된다. 24행의 전체 Gram matrix는 300개 상삼각 쌍이다. 로컬 회로 예산은 이 표본 수로 제한하며 하드웨어 크기의 statevector 계산을 시도하지 않는다.

## IBM 작업 연결

```python
from herbfold.kernel_model import prepare_experiment, evaluate_experiment
from herbfold.quantum import plan_quantum, submit_kernel, retrieve_kernel

experiment = prepare_experiment(records, endpoint="Kd", split="scaffold")
features = experiment["features"]

# 아래 숫자는 실행 상한의 명시 예시다. 계정 quota/가격은 따로 확인한다.
budget = dict(qubits="max", shots=1024, max_circuits=300,
              circuits_per_job=32, max_jobs=10, max_total_shots=307200,
              max_execution_time=120)
plan = plan_quantum(features, mode="ibm", **budget)

# prepared experiment JSON을 고정 보존한 뒤 사용자가 실제 실행을 요청한다.
submitted = submit_kernel(features, "runs/kernel-ibm.json", execute=True, **budget)
completed = retrieve_kernel("runs/kernel-ibm.json")
if completed["status"] == "completed":
    comparison = evaluate_experiment(experiment, completed)
```

실제 API의 제출 예산은 별도로 지정한다. 표본 16행은 136회로여서 quantum 모듈 기본 64회로 예산을 초과한다. 이 상황에서 일부 쌍만 생성하거나 빈 kernel을 채우지 않으며 명시적으로 예산을 늘려야 한다.

`experiment_sha256`은 특징·레이블·분할·전처리 설정을 묶고, `feature_sha256`은 QPU에 전달한 정확한 숫자 행렬과 결과를 연결한다. `evaluate_experiment`에 전체 local/IBM 결과 객체를 전달하면 특징 fingerprint와 IBM 완료 상태를 검사한다. bare `NxN` 행렬도 받을 수 있지만 이때 출처 검증 불가를 결과에 명시한다. `train_indices`/`test_indices`는 준비된 feature matrix의 행 번호이고, `input_train_indices`/`input_test_indices`는 endpoint 제외 전 원본 데이터의 행 번호다.

## 검증

`uv run pytest -q tests/test_kernel_model.py`는 훈련 전용 스케일링, 시험 구조값 변경에 대한 훈련 특징 불변성, 시험 레이블 변경에 대한 예측 불변성, 실제 Qiskit 로컬 fidelity와 회귀 연결, 시험–시험 kernel 미사용, 잡음 PSD 정책, 결과 fingerprint, 레코드·행렬·표본·큐빗 제한을 검증한다. 공개/외부 실제 affinity dataset에 대한 성능 평가는 이 구현 검증과 별도의 연구 작업이다.
