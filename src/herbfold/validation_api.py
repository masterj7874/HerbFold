"""Read-only validation reports, with an indexed, bounded candidate view."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from fastapi import APIRouter, Query


class ValidationReports:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        self._signature = None

    def _read(self, relative: str) -> dict:
        path = self.root / relative
        if not path.is_file():
            return {"status": "not_run"}
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                raise ValueError("Expected report object")
            return value
        except (OSError, ValueError):
            return {"status": "report_unavailable", "reason": "Report could not be read; no results inferred."}

    def summary(self) -> dict:
        return {
            "scale": self._read("scale/scale-progress.json"),
            "biology": self._read("bio-validation/summary.json"),
            "safety": self._read("tox21/summary.json"),
            "scope": {
                "experimental_validation_performed": False,
                "clinical_efficacy_established": False,
                "human_safety_established": False,
                "candidate_cohort": "Prior campaign snapshot plus an explicitly sampled scale cohort; unselected scale candidates are not bioassessed.",
            },
            "cohort": {key: value for key, value in self._read("candidate-assessment-inputs.receipt.json").items()
                       if key != "sample_rowids"},
        }

    def _connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.root / "validation-results.sqlite3", timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _refresh(self):
        paths = [self.root / "bio-validation/candidates.jsonl", self.root / "tox21/candidates.jsonl"]
        signature = tuple((path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None for path in paths)
        with self._lock:
            if signature == self._signature:
                return
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("""CREATE TABLE IF NOT EXISTS candidates (
                    smiles TEXT PRIMARY KEY, id TEXT NOT NULL, biology TEXT, safety TEXT,
                    measured INTEGER DEFAULT 0, predicted INTEGER DEFAULT 0,
                    alerts INTEGER DEFAULT 0, abstained INTEGER DEFAULT 0
                )""")
                connection.execute("CREATE INDEX IF NOT EXISTS validation_candidate_id ON candidates(id)")
                connection.execute("DELETE FROM candidates")
                for kind, path in zip(("biology", "safety"), paths, strict=True):
                    if not path.is_file():
                        continue
                    with path.open() as stream:
                        for line in stream:
                            row = json.loads(line)
                            evidence = row.get("evidence", [])
                            predictions = row.get("predictions", [])
                            measured = any(e.get("status") == "exact_measured" for e in evidence) or bool(row.get("observed_assays"))
                            predicted = any(e.get("status") == "predicted" for e in evidence + predictions)
                            abstained = any(e.get("status") == "abstained" for e in evidence + predictions)
                            alerts = row.get("structural_alerts", {})
                            has_alert = any(alerts.values()) if isinstance(alerts, dict) else bool(alerts)
                            # kind is one of the two literal internal column names above.
                            connection.execute(f"""INSERT INTO candidates
                                (smiles,id,{kind},measured,predicted,alerts,abstained) VALUES(?,?,?,?,?,?,?)
                                ON CONFLICT(smiles) DO UPDATE SET {kind}=excluded.{kind},
                                measured=max(measured,excluded.measured), predicted=max(predicted,excluded.predicted),
                                alerts=max(alerts,excluded.alerts), abstained=max(abstained,excluded.abstained)
                            """, (row["smiles"], str(row["id"]), json.dumps(row, ensure_ascii=False),
                                  int(measured), int(predicted), int(has_alert), int(abstained)))
            self._signature = signature

    def candidates(self, *, limit: int = 50, offset: int = 0, search: str = "", status: str = "all") -> dict:
        self._refresh()
        clauses, parameters = [], []
        if search:
            clauses.append("(id LIKE ? ESCAPE '\\' OR smiles LIKE ? ESCAPE '\\')")
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.extend([f"%{escaped}%"] * 2)
        column = {"measured": "measured", "predicted": "predicted", "alerts": "alerts", "abstained": "abstained"}.get(status)
        if column:
            clauses.append(f"{column}=1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            total = connection.execute("SELECT count(*) FROM candidates" + where, parameters).fetchone()[0]
            rows = connection.execute("SELECT * FROM candidates" + where + " ORDER BY id,smiles LIMIT ? OFFSET ?",
                                      [*parameters, limit, offset]).fetchall()
        items = []
        for row in rows:
            item = json.loads(row["biology"]) if row["biology"] else {
                "id": row["id"], "smiles": row["smiles"], "evidence": [], "structural_alerts": {},
            }
            item["safety"] = json.loads(row["safety"]) if row["safety"] else None
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset,
                "scope": "Computational assessment of a fixed candidate snapshot; no experimental candidate validation."}


def make_router(store) -> APIRouter:
    router = APIRouter(prefix="/api/validation", tags=["validation"])
    reports = ValidationReports(store.root / "validation")

    @router.get("/summary")
    def summary():
        return reports.summary()

    @router.get("/candidates")
    def candidates(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   search: str = Query("", max_length=160),
                   status: str = Query("all", pattern="^(all|measured|predicted|alerts|abstained)$")):
        return reports.candidates(limit=limit, offset=offset, search=search, status=status)

    return router
