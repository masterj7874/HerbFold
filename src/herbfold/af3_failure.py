"""Bounded AF3 child diagnostics; allocator retry messages are not terminal errors."""

from __future__ import annotations

import os
from pathlib import Path

LOG_SCAN_BYTES = 64 * 1024
LOG_EVIDENCE_CHARS = 8 * 1024


def _gpu_allocation_warning(line):
    lower = line.lower()
    return any(token in lower for token in ("cuda_error_out_of_memory", "cuda out of memory")) or (
        any(token in lower for token in ("out of memory", "failed to allocate"))
        and any(token in lower for token in ("gpu_", "gpu memory", "device memory", "cuda"))
    )


def _stage_log_tail(path, start):
    try:
        with Path(path).open("rb") as handle:
            end = handle.seek(0, 2)
            handle.seek(min(end, max(start, end - LOG_SCAN_BYTES)))
            return handle.read(LOG_SCAN_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def has_gpu_allocation_warning(log_path, *, log_start=0):
    """Observe allocation warnings without declaring a live inference failed."""
    return any(_gpu_allocation_warning(line) for line in _stage_log_tail(log_path, log_start).splitlines())


class AllocationRetryWatchdog:
    """Budget only sustained *fresh* allocation retries with no other log progress.

    Unknown output, unreadable/replaced/truncated logs and read gaps larger than
    the scan budget all reset observation. Silence alone cannot trigger a stop.
    This is an execution policy limit, not a diagnosis of a fatal CUDA error.
    """

    def __init__(self, timeout_seconds, *, log_start=0):
        self.timeout_seconds = timeout_seconds
        self.offset = log_start
        self.identity = None
        self.since = None
        self.last_warning = None

    def _reset(self):
        self.since = self.last_warning = None
        return False

    def observe(self, log_path, now):
        if not self.timeout_seconds:
            return False
        try:
            with Path(log_path).open("rb") as handle:
                stat = os.fstat(handle.fileno())
                identity = (stat.st_dev, stat.st_ino)
                end = handle.seek(0, 2)
                if (
                    (self.identity is not None and identity != self.identity)
                    or end < self.offset
                    or end - self.offset > LOG_SCAN_BYTES
                ):
                    self.identity, self.offset = identity, end
                    return self._reset()
                self.identity = identity
                handle.seek(self.offset)
                text = handle.read(LOG_SCAN_BYTES).decode("utf-8", errors="replace")
                self.offset = handle.tell()
        except OSError:
            return self._reset()
        warned = False
        for line in text.splitlines():
            stripped = line.strip()
            if _gpu_allocation_warning(stripped) and any(
                marker in stripped for marker in ("cuda_executor.cc:", "bfc_allocator.cc:")
            ):
                warned = True
            elif not stripped or stripped == "Current allocation summary follows.":
                continue
            elif stripped.startswith("If the cause is memory fragmentation maybe the environment variable"):
                continue
            elif "bfc_allocator.cc:" in stripped and set(stripped.rsplit("]", 1)[-1]) <= set(" *x_"):
                continue
            else:
                return self._reset()
        if not warned:
            if self.last_warning is not None and now - self.last_warning > 30:
                self._reset()
            return False
        if self.last_warning is None or now - self.last_warning > 30:
            self.since = now
        self.last_warning = now
        return now - self.since >= self.timeout_seconds


def describe_process_failure(log_path, stage, return_code, *, log_start=0, timed_out=False, device=None):
    """Describe a child that has *already* failed/stopped, without changing its state.

    CUDA allocators often emit errors before succeeding with a smaller allocation.
    Those messages alone must neither stop a live child nor establish the cause of
    a later unrelated failure. Require a terminal exception for an OOM diagnosis.
    """
    if return_code == 0 and not timed_out:
        raise ValueError("A successful process must not be classified as a failure")
    tail = _stage_log_tail(log_path, log_start)
    allocation_warning = False
    fatal_oom = None
    for line in tail.splitlines():
        lower = line.lower().strip()
        gpu_memory = _gpu_allocation_warning(line)
        allocation_warning |= gpu_memory
        memory_failure = any(
            token in lower for token in ("out of memory", "failed to allocate", "allocating")
        )
        terminal_exception = any(
            token in lower
            for token in ("xlaruntimeerror:", "resourceexhaustederror:", "torch.outofmemoryerror:")
        ) or (lower.startswith("resource_exhausted:") or ("runtimeerror:" in lower and gpu_memory))
        # Both the data pipeline and inference can run on CPU. A generic host
        # allocation error must not be reported as a CUDA memory problem.
        if terminal_exception and memory_failure and (gpu_memory or (stage == "inference" and device == "gpu")):
            fatal_oom = line[-1500:]
    if timed_out:
        code = "execution_timeout"
        message = "AF3 실행이 제한 시간(AF3_TIMEOUT_SECONDS)을 초과해 중단되었습니다. 실행 로그를 확인한 뒤 새 계산으로 다시 실행하세요."
    elif fatal_oom:
        code = "gpu_out_of_memory"
        message = (
            "GPU 메모리가 부족해 AF3 구조 계산이 종료되었습니다. "
            "GPU 메모리를 확보하거나 사용할 GPU를 변경한 뒤 새 계산으로 다시 실행하세요. "
            "기존 입력과 실행 로그는 보존됩니다."
        )
    else:
        code = "process_exit"
        message = f"AF3 {stage} exited with code {return_code}; inspect run.log."
    if allocation_warning and code != "gpu_out_of_memory":
        message += " 로그에 GPU 메모리 할당 경고가 있습니다. GPU 사용량도 확인하세요."
    return {
        "code": code,
        "message": message,
        "stage": stage,
        "return_code": return_code,
        "gpu_allocation_warning": allocation_warning,
        "evidence": fatal_oom,
        "log_tail": tail[-LOG_EVIDENCE_CHARS:],
    }
