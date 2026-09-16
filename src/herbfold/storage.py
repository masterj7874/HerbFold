"""SQLite journal and restricted artifact access for a local research workstation."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path


def utcnow():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.getenv("HERBFOLD_DATA_DIR", "runtime")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "jobs.sqlite3"
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT, status TEXT, created TEXT, updated TEXT, payload TEXT, result TEXT, error TEXT)"
            )

    def connect(self):
        connection = sqlite3.connect(self.db, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, kind, payload):
        job_id = uuid.uuid4().hex
        now = utcnow()
        with self.connect() as c:
            c.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
                (job_id, kind, "prepared", now, now, json.dumps(payload, allow_nan=False), "null", None),
            )
        self.directory(job_id).mkdir()
        return self.get(job_id)

    def get(self, job_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Job not found")
        result = dict(row)
        for key in ("payload", "result"):
            result[key] = json.loads(result[key])
        return result

    def list(self):
        with self.connect() as c:
            ids = c.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT 100").fetchall()
        return [self.get(row[0]) for row in ids]

    def update(self, job_id, status, result=None, error=None):
        with self.connect() as c:
            c.execute(
                "UPDATE jobs SET status=?,updated=?,result=?,error=? WHERE id=?",
                (status, utcnow(), json.dumps(result, allow_nan=False), error, job_id),
            )
        return self.get(job_id)

    def claim(self, job_id):
        with self.connect() as c:
            cursor = c.execute(
                "UPDATE jobs SET status='queued',updated=? WHERE id=? AND status='prepared'",
                (utcnow(), job_id),
            )
        return cursor.rowcount == 1

    def directory(self, job_id):
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("Invalid job ID")
        return self.root / job_id

    def artifact(self, job_id, relative):
        directory = self.directory(job_id).resolve()
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError("Artifact not found within this job")
        return path

    def write(self, job_id, filename, data):
        path = self.directory(job_id) / filename
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        return {"file": filename, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
