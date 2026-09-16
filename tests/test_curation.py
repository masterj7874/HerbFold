"""Synthetic unit fixtures only; these values are not experimental evidence."""

import json
from copy import deepcopy

from herbfold.curation import report_records


def row(**changes):
    return {
        "smiles": "CCO",
        "target_id": "SYNTHETIC_TEST_TARGET",
        "endpoint": "Kd",
        "value": 10,
        "unit": "nM",
        "relation": "=",
        "is_measured": True,
        "assay_id": "SYNTHETIC_ASSAY",
        "source": "test://synthetic-only",
        **changes,
    }


def test_exact_canonical_duplicates_are_proposed_once_with_original_indices():
    records = [row(), row(smiles="OCC"), row(smiles="CCCO")]
    untouched = deepcopy(records)
    report = report_records(records)
    assert records == untouched
    assert report["suggested_input_indices"] == [0, 2]
    assert report["excluded_input_indices"] == [1]
    assert report["duplicate_groups"][0]["classification"] == "identical_full_rows"
    assert report["duplicate_groups"][0]["input_indices"] == [0, 1]
    assert report["standardization_changes"][0]["field"] == "smiles"
    assert report["requires_review"] is True
    assert report == report_records(records)
    json.dumps(report, allow_nan=False)


def test_multiple_assays_or_conflicting_values_exclude_entire_group_from_proposal():
    records = [row(), row(), row(value=20, assay_id="SECOND_ASSAY"), row(smiles="CCN")]
    report = report_records(records)
    assert report["suggested_input_indices"] == [3]
    assert report["excluded_input_indices"] == [0, 1, 2]
    group = report["duplicate_groups"][0]
    assert group["classification"] == "requires_assay_review"
    assert group["differing_fields"] == ["assay_id", "value"]
    assert [record["value"] for record in group["rows"]] == [10, 10, 20]
    assert all(exclusion["category"] == "requires_assay_review" for exclusion in report["exclusions"])


def test_equal_values_from_distinct_sources_are_not_silently_collapsed():
    report = report_records([row(), row(source="test://different-provenance")])
    assert report["suggested_records"] == []
    assert report["duplicate_groups"][0]["differing_fields"] == ["source"]


def test_kd_and_ki_remain_separate_groups_with_no_endpoint_conversion():
    records = [row(), row(endpoint="Ki"), row(endpoint="IC50")]
    report = report_records(records)
    assert [record["endpoint"] for record in report["suggested_records"]] == ["Kd", "Ki"]
    assert report["duplicate_groups"] == []
    assert report["invalid_records"][0]["input_index"] == 2
    assert report["excluded_input_indices"] == [2]


def test_invalid_labels_are_reported_individually_and_never_selected():
    records = [row(is_measured=False), row(relation="<"), row(source=""), row(value=0), row(smiles="CCC")]
    report = report_records(records)
    assert report["input_count"] == 5
    assert report["valid_count"] == 1
    assert report["suggested_input_indices"] == [4]
    assert [item["input_index"] for item in report["invalid_records"]] == [0, 1, 2, 3]
    assert all(item["reason"] for item in report["invalid_records"])


def test_suggestions_are_independent_copies_and_noop_input_needs_no_review():
    records = [row()]
    report = report_records(records)
    assert report["requires_review"] is False
    report["suggested_records"][0]["value"] = 999
    assert records[0]["value"] == 10
    assert report_records([])["suggested_records"] == []
