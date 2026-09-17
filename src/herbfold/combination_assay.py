"""Descriptive comparison of user-supplied, matched inhibition measurements.

No values are inferred from molecular structure. Bliss and HSA are reference
models, not clinical efficacy or statistical significance estimators.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

REFERENCE = "https://academic.oup.com/nar/article/48/W1/W488/5815821"


class InhibitionPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    concentration_a: float = Field(ge=0, le=1e9, strict=True)
    concentration_b: float = Field(ge=0, le=1e9, strict=True)
    inhibition_a: float = Field(ge=0, le=1, strict=True)
    inhibition_b: float = Field(ge=0, le=1, strict=True)
    inhibition_combination: float = Field(ge=0, le=1, strict=True)


class CombinationAssayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    component_a: str = Field(min_length=1, max_length=200)
    component_b: str = Field(min_length=1, max_length=200)
    assay_context: str = Field(min_length=1, max_length=1000)
    source: str = Field(min_length=1, max_length=1000)
    concentration_unit: Literal["nM", "uM", "mg/mL", "ug/mL"] = "uM"
    matched_conditions: Literal[True]
    points: list[InhibitionPoint] = Field(min_length=1, max_length=96)

    @field_validator("matched_conditions", mode="before")
    @classmethod
    def require_matched_conditions(cls, value):
        if value is not True:
            raise ValueError("동일한 실험 조건임을 확인해야 합니다.")
        return value


def evaluate_combination(request: CombinationAssayRequest) -> dict:
    if request.component_a.casefold() == request.component_b.casefold():
        raise ValueError("서로 다른 두 성분의 이름을 입력해 주세요.")
    points = []
    for index, row in enumerate(request.points):
        a, b, observed = row.inhibition_a, row.inhibition_b, row.inhibition_combination
        bliss = a + b - a * b
        hsa = max(a, b)
        points.append({
            "index": index + 1,
            **row.model_dump(),
            "bliss_expected": round(bliss, 10),
            "hsa_expected": round(hsa, 10),
            "bliss_excess_percentage_points": round(100 * (observed - bliss), 8),
            "hsa_excess_percentage_points": round(100 * (observed - hsa), 8),
            "interpretation": "양수는 해당 기준보다 높은 억제율, 음수는 낮은 억제율입니다. 통계적 유의성은 계산하지 않았습니다.",
        })
    return {
        "schema_version": 1,
        "status": "calculated_from_user_supplied_measurements",
        "input": request.model_dump(),
        "points": points,
        "point_count": len(points),
        "formulas": {"bliss": "a + b - a*b", "hsa": "max(a, b)", "excess": "100*(observed - expected) percentage points"},
        "source_verified": False,
        "reference_url": REFERENCE,
        "limitations": [
            "입력 자료는 사용자가 제공했으며 원자료의 실측 여부와 품질을 독립 확인하지 않았습니다.",
            "각 행의 단독 측정과 병용 측정은 동일한 농도, 세포계, 노출 시간, endpoint 및 대조군 정규화를 사용해야 합니다.",
            "Bliss는 독립 작용을 가정하고, HSA는 가장 큰 단독 억제율을 기준으로 합니다. 모델에 따라 차이가 날 수 있습니다.",
            "반복 측정의 오차, 유의성, 독성, 약물동태 및 임상적 효능은 평가하지 않았습니다. 계산값만으로 병용 효과를 확정할 수 없습니다.",
        ],
    }


def make_router():
    router = APIRouter(tags=["design-pipeline"])

    @router.post("/api/design-pipeline/combination-assay")
    def compare(request: CombinationAssayRequest):
        return evaluate_combination(request)

    return router
