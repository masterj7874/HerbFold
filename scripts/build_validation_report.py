#!/usr/bin/env python3
"""Assemble measured local artifacts into the user-facing validation report."""
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def main():
    scale = read("docs/scale-validation-results.json")
    if not math.isclose(scale["elapsed_seconds"], scale["preparation_seconds"] + scale["generation_seconds"], abs_tol=1e-6):
        raise RuntimeError("Scale total time differs from its measured preparation/generation components")
    if scale["attempted"] != sum(scale[key] for key in ("retained_unique", "duplicates", "rejected", "known_parent_matches")):
        raise RuntimeError("Scale attempt accounting does not reconcile")
    biology = read("runtime/validation/bio-validation/summary.json")
    safety = read("runtime/validation/tox21/summary.json")
    cohort = read("runtime/validation/candidate-assessment-inputs.receipt.json")
    if biology["status"] != "completed" or safety["status"] != "completed":
        raise RuntimeError("Both completed biological assessments are required")
    if not (biology["input_snapshot"]["sha256"] == safety["candidates"]["input_sha256"] == cohort["input_sha256"]):
        raise RuntimeError("Candidate assessment snapshots differ")
    if cohort["scale_population"] != scale["retained_unique"]:
        raise RuntimeError("Cohort predates the final scale population")
    if cohort["scale_structure_digest"] != scale["audit"]["sorted_structure_digest_sha256"]:
        raise RuntimeError("Cohort and audited scale structure populations differ")
    af3 = read("docs/af3_verification.json")
    quantum = read("docs/quantum_verification.json")
    af3_checks = []
    for attempt in af3.get("attempts", []):
        path = Path(attempt.get("structure", ""))
        af3_checks.append({"job_id": attempt["job_id"], "path": str(path),
            "artifact_exists": path.is_file(), "structure_sha256_matches": path.is_file() and
            hashlib.sha256(path.read_bytes()).hexdigest() == attempt.get("structure_sha256"),
            "metrics": attempt.get("metrics"), "original_elapsed_seconds": attempt.get("elapsed_seconds")})
    if not af3_checks or not all(row["structure_sha256_matches"] for row in af3_checks):
        raise RuntimeError("The earlier AF3 structure artifact could not be verified")
    source_files = ["docs/scale-validation-results.json", "runtime/validation/bio-validation/summary.json",
                    "runtime/validation/bio-validation/candidates.jsonl", "runtime/validation/tox21/summary.json",
                    "runtime/validation/tox21/candidates.jsonl", "runtime/validation/candidate-assessment-inputs.jsonl"]
    summary = {
        "checked_at": datetime.now(timezone.utc).isoformat(), "scope": "Measured computation and retrospective dataset assessment",
        "scale": scale, "biology": biology, "safety": safety,
        "cohort": {key: value for key, value in cohort.items() if key != "sample_rowids"},
        "previous_af3_artifacts_rechecked": af3_checks,
        "previous_quantum_result": {"backend": quantum["backend"], "kernel": quantum["kernel"],
                                    "source": "docs/quantum_verification.json", "new_submission": False},
        "experimental_efficacy_established": False, "human_safety_established": False,
        "source_artifact_sha256": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in source_files},
        "software_verification": read("docs/validation-software-verification.json")
            if Path("docs/validation-software-verification.json").is_file() else None,
    }
    Path("docs/validation-results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    counts, candidates = biology["counts"], safety["candidates"]
    qualified = [model for model in biology["models"] if model["quality_status"] == "qualified"]
    model_rows = []
    for model in biology["models"]:
        metrics = model.get("metrics", {}).get(model.get("selected_model"), {}).get("test", {})
        mae = f"{metrics['mae']:.3f}" if metrics.get("mae") is not None else "평가 자료 부족"
        model_rows.append(f"| {model['target']} {model['endpoint']} | {model.get('assay_id', '자료 없음')} | {model['data_counts']['unique_structures']} / {model['data_counts']['scaffolds']} | {model['quality_status']} | {mae} |")
    endpoint_rows = []
    for endpoint in safety["endpoints"]:
        metrics = endpoint["metrics"]
        interval = metrics.get("roc_auc_ci95")
        ci = f"{interval[0]:.3f}–{interval[1]:.3f}" if interval else "미산출"
        endpoint_rows.append(f"| {endpoint['endpoint']} | {endpoint['train_count']:,} / {endpoint['test_count']:,} | {metrics['roc_auc']:.3f} ({ci}) | {metrics['average_precision']:.3f} / {metrics['prevalence']:.3f} | {endpoint['candidate_predictions']:,} |")
    text = f"""# 실제 생성 성능·약효·안전성 계산 검증

검증일: 2026-09-08. **수백만 개의 화학 구조 생성·저장은 확인했습니다. 후보의 약효와 사람에서의 안전성은 입증되지 않았습니다.** 이 보고서는 실제 실행 파일과 공개 실험 자료를 검토한 결과이며, 실험실에서 후보를 시험한 보고서가 아닙니다.

## 실제 생성 성능

| 항목 | 실측 결과 |
|---|---:|
| 서로 다른 조각 조합 생성 시도 | {scale['attempted']:,}회 |
| RDKit 구조 검사 통과 제안 | {scale['sanitized']:,}건 |
| 중복·물성·원본 일치·두 부모 계보 검사 후 저장 | **{scale['retained_unique']:,}종** |
| 원본 조각 준비 | {scale['preparation_seconds']:.2f}초 |
| 누적 활성 생성·저장 시간 | {scale['generation_seconds']:.2f}초 |
| 준비 + 생성 | {scale['elapsed_seconds']:.2f}초 |
| 생성·저장 처리량 | 약 {scale['retained_unique'] / scale['generation_seconds']:,.0f}종/초 |
| 작업자 | CPU {scale['workers']}개 프로세스 |
| 관측 최대 부모·자식 프로세스 RSS 합 | {scale['sampled_peak_aggregate_rss_mb'] / 1024:.2f} GiB |
| 실제 SQLite 저장 크기 | {scale['database_bytes'] / 1024**3:.2f} GiB |
| 현재 조각 집합의 조합 상한 | {scale['compatible_pair_slots_upper_bound']:,}회 |

측정 장비는 Threadripper 7980X(64코어/128스레드), 메모리 약 251 GiB입니다. GPU·AlphaFold·IBM QPU·LLM 추론 시간은 이 생성 속도에 포함되지 않습니다. 시간은 실제 배치 실행의 누적 활성 시간으로, 실행 사이의 대기와 사후 감사·약효/독성 모델 평가는 별도입니다. RSS 합은 샘플링 값이며 공유 메모리가 중복 집계될 수 있습니다.

**1억 개 생성 성능은 검증되지 않았습니다.** 이번 단일 결합 BRICS 조합 공간 자체가 1억 회보다 작습니다. 500만 회의 처리량을 연장한 산술 추정은 실제 1억 회 실행이나 1억 종의 고유 구조를 보장하지 않습니다. 순회 후반 조각 종류에 따라 소요 시간과 탈락 비율도 달라집니다.

원본 후보 {3_701_743:,}종에서 사후 전수 계보 검사로 동일 원본에서만 두 조각을 가져온 3종을 제외했습니다. 향후 생성에도 두 부모가 서로 다른 표준화 구조인지 검사합니다. 저장 행 수·중복 제약·물성 열은 전수 확인하고, 500종은 구조를 다시 생성해 원본 조각 계보와 비교했습니다. 생성된 구조가 모두 실제 합성 가능한 물질이거나 특허상 신규 물질이라는 뜻은 아닙니다. [원본 성능·감사 기록](scale-validation-results.json)

## 후보 평가 범위

기존 두 캠페인의 고유 후보 전부 **{cohort['prior_unique_count']:,}종**과, 최종 대규모 저장 집합에서 고정 난수 시드로 편향 없이 뽑은 **{cohort['scale_sample_count']:,}종**을 평가했습니다. 중복 {cohort['overlap']}종을 제외한 실제 평가 대상은 **{cohort['combined_unique_count']:,}종**입니다. 표본 밖의 수백만 후보에 약효·안전성 평가가 끝났다고 표시하지 않습니다.

샘플은 QED나 예측 결과를 보고 골랐던 후보가 아닙니다. SQLite 행을 순회하는 균등 reservoir sampling을 사용했고 시드·선택 행 ID·집합 해시를 보존했습니다. 입력 SHA-256: `{cohort['input_sha256']}`.

## COX-2 약효 관련 근거와 hERG 관련 평가

ChEMBL의 실제 실험 기록 **{biology['curation']['input_rows']:,}건**을 내려받아 사람 단일 표적, 높은 표적 배정 신뢰도, 정확한 농도·측정값, 변이·중복·충돌 여부를 확인했습니다. **{biology['curation']['retained_rows']:,}건**이 남았습니다. Ki, Kd, IC50과 서로 다른 assay는 혼합하지 않았습니다. ChEMBL의 신뢰도 9는 표적 배정에 대한 신뢰도로, 개별 실험의 정확성이나 임상 효능을 보장하지 않습니다. [ChEMBL 공식 설명](https://chembl.gitbook.io/chembl-interface-documentation/frequently-asked-questions/chembl-data-questions)

| 표적·측정 | 선택 assay | 구조 수 / scaffold 수 | 판정 | 독립 평가 MAE(p단위) |
|---|---|---:|---|---:|
{chr(10).join(model_rows)}

COX-2는 비교 가능한 assay 내부의 구조·scaffold 다양성이 부족해 후보 예측을 보류했습니다. 큰 데이터베이스 전체 행 수를 그대로 학습 가능한 독립 표본 수로 취급하지 않았습니다.

hERG에서 기준을 통과한 {len(qualified)}개 모델은 **[3H]dofetilide 경쟁 결합 시험**의 측정값을 예측한 것입니다. 실제 이온 전류 측정이나 부정맥·사람 안전성 검증이 아닙니다. 모델 선택, 보정, 최종 시험 집합은 scaffold별로 분리했습니다. 후보에 적용할 때는 학습 구조와의 유사도 기준을 다시 적용했습니다.

평가 후보에서 데이터베이스의 동일 표준화 구조 실측 기록이 있는 후보는 **{counts['exact_measured_candidates']}종**, 기준을 만족해 새 COX-2/hERG 수치 예측을 낸 후보는 **{counts['predicted_candidates']}종**입니다. 실측 일치는 기존 문헌 기록과의 일치이며 이번에 수행한 실험이 아닙니다. 염·입체화학·제형과 노출 조건은 원자료를 추가 확인해야 합니다.

## Tox21 독성 관련 시험

Tox21 원자료 {safety['dataset']['raw_rows']:,}행에서 {safety['dataset']['curated_structures']:,}종을 정리했습니다. 결측값은 음성으로 바꾸지 않았고, 동일 표준화 구조에서 충돌하는 라벨은 해당 시험 학습에서 제외했습니다. Tox21은 12개 수용체 신호·세포 스트레스 경로 시험의 활성 여부를 다룹니다. [NIH 시험 설명](https://tripod.nih.gov/tox21/challenge/about.jsp), [공식 DeepChem 데이터 로더](https://github.com/deepchem/deepchem/blob/master/deepchem/molnet/load_function/tox21_datasets.py)

Morgan 지문·고정 Random Forest·scaffold 분리 시험 집합을 사용했습니다. 아래 ROC-AUC는 이번 분할의 독립 평가값이며 공인 벤치마크 순위가 아닙니다. 괄호는 scaffold 그룹 bootstrap 95% 구간입니다. 활성 비율이 낮으므로 PR-AUC(AP)와 양성 비율 기준선도 함께 기록했습니다. 점수는 교정되지 않은 시험 활성 추정치이며 사람의 독성 발생 확률이 아닙니다.

| 시험 | 학습 / 평가 표본 | ROC-AUC (95% 구간) | AP / 양성 비율 | 후보 예측 허용 수 |
|---|---:|---:|---:|---:|
{chr(10).join(endpoint_rows)}

후보 **{candidates['out_of_domain']:,}종**은 12개 시험 전부에서 학습 범위를 벗어났습니다. 적어도 한 시험의 점수를 제시할 수 있었던 후보는 **{candidates['with_any_prediction']:,}종**입니다. 새로운 대규모 후보 표본만 보면 {candidates['cohorts']['scale_uniform_sample']['total']:,}종 중 **{candidates['cohorts']['scale_uniform_sample']['out_of_domain']:,}종**이 범위 밖입니다. 예측 보류·낮은 점수·시험 음성은 안전성을 뜻하지 않습니다.

PAINS 또는 Brenk 구조 경고가 있는 후보는 **{counts['alerted_candidates']:,}종**, 기존 원본 카탈로그와 동일 구조인 후보는 **{counts['catalog_exact_matches']:,}종**입니다. 구조 경고는 추가 검토 항목이며 독성 확정 판정이 아닙니다. 경고가 없다는 이유로 안전하다고 판정하지 않습니다.

## AlphaFold 3·IBM 결과의 해석

기존 AF3 3.0.4의 COX-2–이부프로펜 실행 파일과 해시를 다시 확인했습니다. MSA/템플릿 없이 수행한 실행 점검은 pTM/ipTM 0.39, clash 발생, ranking −99.11로 구조 품질을 통과하지 못했습니다. 이 구조를 약효·친화도 근거로 사용하지 않습니다. 이번 후보 집합의 대규모 AF3 결합 구조 검증은 수행되지 않았습니다. 공식 MSA/템플릿 데이터베이스도 현재 설치되지 않았습니다. [기존 AF3 실행 기록](af3_verification.json), [공식 AF3 출력 정의](https://github.com/google-deepmind/alphafold3/blob/main/docs/output.md)

기존 IBM ibm_fez 156큐빗 실행은 원본 분자 커널이 전부 0이었습니다. 실제 최대 큐빗 실행 기록은 있으나 유용한 친화도 정보나 고전 계산 대비 우위가 입증되지 않았습니다. 이번 검증에서는 새 QPU 작업을 제출하지 않았습니다. [기존 IBM 실측 기록](quantum_verification.json)

## 실제 약효·안전성을 입증하려면

다음은 **아직 수행하지 않은** 연구 단계입니다. 먼저 표적·적응증과 후보의 정체성, 입체화학, 합성·순도·안정성을 확인해야 합니다. COX-2에 대한 별도 결합 시험과 기능 시험, 세포에서의 작용·선택성, 비특이적 반응 여부를 확인하고, 그 결과와 독립적으로 독성·대사·노출 자료를 확보해야 합니다. hERG 결합 추정만으로 심장 안전성을 대신하지 않으며 실제 채널 기능·세포독성·유전독성·대사·장기 독성 등 필요한 시험 범위는 적응증과 개발 단계에 맞춰 정해야 합니다. 최종적인 사람에서의 유효성·안전성은 적절한 비임상·임상 근거가 필요합니다.

현재 결과로는 후보의 치료 효과 순위나 ‘안전한 신약’ 목록을 만들 수 없습니다. 데이터 부족과 적용 범위 이탈을 해결하는 실험·데이터 확보가 후보 수를 늘리는 것보다 다음 검증의 우선 과제입니다.

실험실 발견, 비임상 연구, 사람에서의 임상 연구는 서로 다른 검증 단계입니다. [FDA의 신약 개발 단계 설명](https://www.fda.gov/patients/learn-about-drug-and-device-approvals/drug-development-process)

## 재현과 화면

앱의 **성능·약효 검증** 메뉴에서 실제 생성 수, 모델 평가, 후보별 근거·구조 경고·예측 보류, 3D 분자 구조를 확인할 수 있습니다. [전체 기계 판독 보고서](validation-results.json), [API 검증](validation-api-verification.json), [화면 검증](validation-ui-verification.json)

코드 검사 결과와 실행 범위는 [소프트웨어 검증 기록](validation-software-verification.json)에 따로 보존합니다. 소프트웨어 테스트 통과는 후보 약효·안전성 검증 통과와 구분합니다.

```bash
uv run python scripts/benchmark_discovery_scale.py --workers 16 --attempts 5000000 --max-seconds 1800
uv run python scripts/prepare_validation_cohort.py
uv run python scripts/validate_candidate_bioactivity.py --candidates runtime/validation/candidate-assessment-inputs.jsonl
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 uv run python scripts/validate_tox21.py --candidates runtime/validation/candidate-assessment-inputs.jsonl
uv run python scripts/build_validation_report.py
```

성능 측정을 새로 재현할 때는 새 `--root` 경로를 사용해야 합니다. 기존 경로는 완료한 커서에서 재개하므로 같은 500만 회를 다시 실행한 기록으로 볼 수 없습니다. 원본 자료·모델·개별 예측·분할 명세는 `runtime/validation/`에 보존합니다. `.joblib` 모델은 이 프로젝트가 생성한 로컬 파일만 사용하고 외부에서 받은 직렬화 파일을 불러오지 마세요.
"""
    Path("docs/validation-report.md").write_text(text)
    print(json.dumps({"report": "docs/validation-report.md", "population": scale["retained_unique"],
                      "assessed": counts["candidates"], "input_sha256": cohort["input_sha256"]}))


if __name__ == "__main__":
    main()
