import json

from herbfold.validation_api import ValidationReports


def test_unrun_report_does_not_invent_success(tmp_path):
    reports = ValidationReports(tmp_path)
    assert reports.summary()["scale"]["status"] == "not_run"
    assert reports.summary()["scope"]["human_safety_established"] is False
    assert reports.candidates()["total"] == 0


def test_merge_pagination_and_refresh_preserve_evidence_types(tmp_path):
    for name in ("bio-validation", "tox21"):
        (tmp_path / name).mkdir()
    bio = tmp_path / "bio-validation/candidates.jsonl"
    bio.write_text(json.dumps({"id": "A", "smiles": "CCO", "evidence": [
        {"status": "exact_measured", "target": "PTGS2"}], "structural_alerts": {"PAINS": ["test"]}}) + "\n")
    safety = tmp_path / "tox21/candidates.jsonl"
    safety.write_text(json.dumps({"id": "A", "smiles": "CCO", "predictions": [
        {"endpoint": "SR-p53", "status": "abstained", "score": None}]}) + "\n")
    reports = ValidationReports(tmp_path)
    page = reports.candidates(limit=1)
    assert page["total"] == 1
    assert page["items"][0]["safety"]["predictions"][0]["score"] is None
    assert reports.candidates(status="predicted")["total"] == 0
    assert reports.candidates(status="measured")["total"] == 1
    assert reports.candidates(status="alerts")["total"] == 1
    assert reports.candidates(offset=1)["items"] == []
    with bio.open("a") as stream:
        stream.write(json.dumps({"id": "B", "smiles": "CCC", "evidence": []}) + "\n")
    assert reports.candidates()["total"] == 2
    assert reports.candidates(search="%")["total"] == 0
