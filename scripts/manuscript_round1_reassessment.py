"""Apply the supplied arithmetic to a separate, explicitly internal R1 recheck."""
from pathlib import Path
from fractions import Fraction
import json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research/manuscript/round1/reassessment"


def total(scores):
    weights = {"A": Fraction(40, 100), "B": Fraction(25, 100),
               "C": Fraction(25, 100), "D": Fraction(10, 100)}
    means = {k: sum(Fraction(v) for n, v in scores.items() if n.startswith(k)) /
             sum(n.startswith(k) for n in scores) for k in weights}
    value = (sum(weights[k] * means[k] for k in weights) - 1) / 4 * 100
    return value, means


def main():
    original = dict(zip(
        ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "C1", "C2", "C3", "C4", "D1", "D2"],
        [4, 3, 3, 4, 3, 3, 3, 2, 4, 3, 4, 3, 4, 4]))
    raw = {}
    for name in ("scientific", "positioning", "presentation"):
        source = json.loads((OUT / f"{name}.json").read_text())
        for key, item in source["scores"].items():
            raw[key] = item["score"] if isinstance(item, dict) else item
    assert set(raw) == set(original)
    calibrated = dict(raw)
    calibrated["C4"] = 3
    old, old_means = total(original)
    new, new_means = total(calibrated)
    raw_total, _ = total(raw)
    assert old == Fraction(1381, 24)
    record = {
        "date": "2026-09-09", "assessment_type": "Internal AI-assisted diagnostic recheck; not external peer review",
        "not_a_new_official_R_score": True,
        "rubric_limit": "Only the user-supplied meta-review, formula and quoted anchors were available; the complete frozen rubric and original six specialist reports were not supplied.",
        "target_score": 90, "target_achieved": False,
        "original_supplied_scores": original, "original_exact": str(old), "original_display": round(float(old), 1),
        "raw_internal_panel_scores": raw, "raw_internal_formula_display": round(float(raw_total), 1),
        "calibrations": [{"criterion": "C4", "raw": 4, "calibrated": 3,
            "reason": "The presentation audit scored review-draft apparatus. The supplied original submission criterion separately requires confirmed study-specific author declarations. Those remain unresolved, so the original revision-level 3 is retained rather than rewarding a change of scope."}],
        "internal_diagnostic_scores": calibrated,
        "internal_exact": str(new), "internal_display": round(float(new), 1),
        "section_means_exact": {k: str(v) for k, v in new_means.items()},
        "formula": "([0.40*A_mean + 0.25*B_mean + 0.25*C_mean + 0.10*D_mean] - 1) / 4 * 100",
        "independent_validation_limits": [
            "Separate agents inspected the revision and independently recomputed selected values, but all are AI assistants in this session, not journal reviewers.",
            "Hash agreement and CPU replay test reproducibility of the distributed observations, not independent experimental authenticity or biological validity.",
            "No acceptance probability, new journal verdict, author approval or 90-point attainment is inferred."
        ],
        "unresolved": [
            "R1-5: actual CRediT roles, all-author approval, funding and competing interests await author confirmation.",
            "R1-2: local review access is supplied; public release rights/licence remain author-owned.",
            "B1/B2: independently demonstrated comparative system advance and external-task utility remain absent.",
            "Prospective prediction-policy benefit, selected-ligand pose accuracy, therapeutic efficacy and quantum advantage are not established or claimed."
        ]
    }
    (OUT / "internal-score-calculation.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    rows = "\n".join(f"| {key} | {original[key]} | {calibrated[key]} |" for key in original)
    report = f"""# R1 수정 후 내부 진단 — 외부 심사 결과가 아님

원고·보충자료·심사답변서와 실제 재현 패키지를 대폭 수정했다. 별도 담당 에이전트의 검토 및 수치 재계산 결과를 제공된 원래 산식에 대입한 내부 진단값은 **{float(new):.1f}/100**이다. 사용자가 제공한 원래 심사 점수는 **{float(old):.1f}/100**으로 보존했다. **90점 달성을 주장하지 않는다.** 전체 동결 평가척도와 원래 여섯 전문 심사보고서를 받지 못했으므로 이 값은 공식 재심사 R-Score나 같은 심사위원의 전후 비교가 아니다.

| 기준 | 제공된 R0 | 내부 R1 진단 |
|---|---:|---:|
{rows}

발표·문서 담당의 원시 C4는 검토용 원고 기준 4였지만, 원래 제출 기준에서는 저자 소유의 확정 진술이 여전히 필요하므로 3으로 조정했다. 원시 합산값 {float(raw_total):.1f}를 제출 준비도 점수로 대체하지 않았다. 계산은 manuscript_round1_reassessment.py와 JSON에 분수 형태로 남겨 반올림 전에 계산한다.

실질적으로 보완된 항목은 가장 가까운 시스템과의 근거 있는 비교, 원고의 주장과 실행 검증의 연결, 누락됐던 실제 첨부자료, 59개 정책 항목과 분기별 적용 순서, 모든 아홉 assay 그룹 및 열두 Tox21 endpoint, scaffold 단위 불확실성, 고정 RF의 별도 CPU 재구성, 원자료 연결 및 문서 도판 검사이다. 주입한 오류 사례, 실제 측정 사례, 전체 데이터베이스와 분산한 선택 스냅샷을 분리했다.

90점까지의 차이는 문구나 표 개수만으로 해결되지 않는다. B1/B2의 핵심은 기존 출처·워크플로 시스템보다 어떤 독립적인 공학적 이점과 실제 사용자 과제의 이익을 제공하는지에 대한 추가 근거다. 현재 아카이브의 좁은 사례와 구성요소의 조합으로 이를 대신하지 않았다. 향후 그 주장을 확대하려면 사전에 과제·분모·평가 규칙을 고정한 비교 재현/사용자 과제 연구가 필요하다. 예측 정책의 미래 정확도 개선을 주장하려면 완전히 분리된 평가 자료가 필요하다. 현재의 제한된 방법론 주장에 새로운 임상·효능·양자 우위 실험을 일괄 요구하지 않는다.

R1-5의 실제 저자 기여·최종 승인·연구비·이해상충과 R1-2의 공개 배포 권리는 저자가 확인해야 한다. 검토용 로컬 묶음은 제공하지만 공개 라이선스나 제출 승인을 추정하지 않는다. 새로운 외부 심사나 저널 게재 확률은 계산하지 않았다. 자세한 근거는 scientific, positioning, presentation 보고서와 replay-report 및 final-source-replay 기록을 참조한다.
"""
    (OUT / "internal-reassessment-ko.md").write_text(report)
    print(json.dumps({"original": float(old), "internal": float(new), "target_achieved": False}))


if __name__ == "__main__":
    main()
