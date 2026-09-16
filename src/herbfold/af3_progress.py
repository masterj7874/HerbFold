"""Observe explicit AF3 log events without estimating completion or GPU activity."""

from __future__ import annotations

import os
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path

READ_LIMIT_BYTES = 64 * 1024
LINE_LIMIT_BYTES = 4096
MSA_DATABASES = {
    "bfd-first_non_consensus_sequences.fasta": "BFD",
    "mgy_clusters_2022_05.fa": "MGnify",
    "uniref90_2022_05.fa": "UniRef90",
    "uniprot_all_2021_04.fa": "UniProt",
}


class ProgressObserver:
    """Read new bytes only and retain the last observed phase across quiet polls.

    AF3 print lines have no timestamp. ``observed_at`` is our observation time,
    not a claimed phase-start time. Unknown output and missing logs provide no
    evidence and never change job status or erase previously observed progress.
    """

    def __init__(self, stage, *, log_start=0):
        self.stage = stage
        self.offset = log_start
        self.identity = None
        self.pending = b""
        self.discard_partial = False
        self.detail = None
        self.rank = (-1, -1, -1)
        self.seeds = []
        self.completed_databases = []

    def _event(self, line):
        if self.stage == "data_pipeline":
            if line == "Running data pipeline...":
                return (10, 0, 0), {"name": "msa_search", "label": "단백질 서열 검색"}
            launch = re.search(r'subprocess_utils\.py:\d+\] Launching subprocess "(.+)"$', line)
            finish = re.search(
                r"subprocess_utils\.py:\d+\] Finished (Jackhmmer|Hmmbuild|Hmmsearch|Hmmalign)(?: \(([A-Za-z0-9_.-]{1,128})\))? in ([0-9]+(?:\.[0-9]+)?) seconds$",
                line,
            )
            if launch:
                try:
                    command = shlex.split(launch.group(1))
                except ValueError:
                    return None
                if not command:
                    return None
                tool = Path(command[0]).name.lower()
                if tool not in {"jackhmmer", "hmmbuild", "hmmsearch", "hmmalign"}:
                    return None
                database = Path(command[-1]).name if tool in {"jackhmmer", "hmmsearch"} else None
                if database and not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", database):
                    database = None
                event, duration = "search_started", None
            elif finish:
                tool, database = finish.group(1).lower(), finish.group(2)
                event, duration = "search_completed", float(finish.group(3))
                if not 0 <= duration <= 604800:
                    return None
            else:
                tool = None
            if tool:
                msa = tool == "jackhmmer"
                label = "단백질 서열 검색" if msa else "템플릿 검색"
                activity = (
                    f"{MSA_DATABASES[database]} 서열 검색"
                    if msa and database in MSA_DATABASES
                    else {
                        "jackhmmer": "단백질 서열 검색",
                        "hmmbuild": "템플릿 검색 준비",
                        "hmmsearch": "PDB 구조 서열 검색"
                        if database and database.startswith("pdb_seqres_")
                        else "템플릿 구조 서열 검색",
                        "hmmalign": "템플릿 서열 정렬",
                    }[tool]
                )
                detail = {
                    "name": "msa_search" if msa else "template_search",
                    "label": label,
                    "tool": tool,
                    "event": event,
                    "activity": f"{activity} {'시작' if event == 'search_started' else '완료'}",
                }
                if msa and event == "search_completed" and database in MSA_DATABASES:
                    display = MSA_DATABASES[database]
                    if display not in self.completed_databases:
                        self.completed_databases.append(display)
                if database:
                    detail["database"] = database
                if duration is not None:
                    detail["duration_seconds"] = duration
                return (10 if msa else 20, 0, 0), detail
            if line.startswith("Writing model input JSON to "):
                return (30, 0, 0), {"name": "writing_outputs", "label": "검색 결과 저장"}
        elif self.stage == "inference":
            if re.fullmatch(r"Featurising data with (?:\d+ seed\(s\)|seed \d+)\.{1,3}", line):
                return (10, 0, 0), {"name": "feature_preparation", "label": "모델 입력 특징 생성"}
            if re.fullmatch(
                r"Running model inference and extracting output structure samples with \d+ seed\(s\)\.\.\.",
                line,
            ):
                return (20, 0, 0), {"name": "model_inference", "label": "AF3 모델 추론"}
            seed_match = re.fullmatch(
                r"(Running model inference|Extracting inference results) with seed (\d+)\.\.\.", line
            )
            if seed_match:
                seed = int(seed_match.group(2))
                if seed > 2**32 - 1:
                    return None
                extracting = seed_match.group(1).startswith("Extracting")
                if seed not in self.seeds:
                    if len(self.seeds) >= 100:
                        return None
                    self.seeds.append(seed)
                ordinal = self.seeds.index(seed) + 1
                return (20, ordinal, int(extracting)), {
                    "name": "result_extraction" if extracting else "model_inference",
                    "label": "예측 구조 추출" if extracting else "AF3 모델 추론",
                    "seed": seed,
                    "seed_ordinal": ordinal,
                }
            if re.fullmatch(r"Writing outputs with \d+ seed\(s\)\.\.\.", line):
                return (30, 0, 0), {"name": "writing_outputs", "label": "예측 구조 파일 저장"}
        if re.fullmatch(r"Fold job \S+ done, output written to .+", line):
            return (40, 0, 0), {
                "name": "job_finished",
                "label": "검색 결과 파일 저장 완료"
                if self.stage == "data_pipeline"
                else "구조 출력 저장 완료",
            }
        return None

    def observe(self, path, observed_at):
        try:
            with Path(path).open("rb") as handle:
                stat = os.fstat(handle.fileno())
                identity = (stat.st_dev, stat.st_ino)
                end = handle.seek(0, 2)
                if (self.identity is not None and identity != self.identity) or end < self.offset:
                    self.offset, self.pending, self.discard_partial = 0, b"", False
                self.identity = identity
                if end - self.offset > READ_LIMIT_BYTES:
                    self.offset, self.pending, self.discard_partial = end - READ_LIMIT_BYTES, b"", True
                handle.seek(self.offset)
                chunk = handle.read(READ_LIMIT_BYTES)
                self.offset = handle.tell()
                log_updated_at = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
        except (OSError, ValueError, OverflowError):
            return self.detail
        if self.discard_partial:
            _, newline, chunk = chunk.partition(b"\n")
            if not newline:
                return self.detail
            self.discard_partial = False
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        if len(self.pending) > LINE_LIMIT_BYTES:
            self.pending, self.discard_partial = b"", True
        for raw in lines:
            if len(raw) > LINE_LIMIT_BYTES:
                continue
            event = self._event(raw.decode("utf-8", errors="replace").strip())
            if event is None:
                continue
            rank, detail = event
            if rank < self.rank:
                continue
            if self.stage == "data_pipeline":
                detail["completed_databases"] = list(self.completed_databases)
                detail["completed_database_count"] = len(self.completed_databases)
            comparable = {
                key: value
                for key, value in (self.detail or {}).items()
                if key not in {"observed_at", "log_updated_at", "source", "timestamp_kind"}
            }
            if detail == comparable:
                continue
            self.rank = rank
            self.detail = {
                **detail,
                "observed_at": observed_at,
                "log_updated_at": log_updated_at,
                "source": "run.log",
                "timestamp_kind": "observed",
            }
        return self.detail
