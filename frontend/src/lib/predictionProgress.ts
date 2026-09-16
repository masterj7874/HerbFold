const record = (value: unknown): Record<string, unknown> | null => value != null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const validSeconds = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;

export function elapsedSeconds(startedAt: unknown, now: number): number | null {
  if (typeof startedAt !== "string" || !startedAt.trim() || !Number.isFinite(now)) return null;
  const start = Date.parse(startedAt);
  return Number.isFinite(start) && start <= now ? (now - start) / 1000 : null;
}

export function formatElapsed(seconds: number | null): string {
  if (!validSeconds(seconds)) return "미기록";
  const total = Math.floor(seconds), hours = Math.floor(total / 3600), minutes = Math.floor(total / 60) % 60, remainder = total % 60;
  return [hours ? `${hours}시간` : "", hours || minutes ? `${minutes}분` : "", `${remainder}초`].filter(Boolean).join(" ");
}

export type RecordedExecutionStep = {
  name: "data_pipeline" | "inference";
  completed: boolean;
  elapsedSeconds: number | null;
  device: "CPU" | "GPU" | "Apple GPU" | null;
  startedAt: string | null;
  finishedAt: string | null;
  failed: boolean;
};

/** Use recorded execution facts; a running or failed step is never a completed stage. */
export function recordedExecutionSteps(result: unknown): RecordedExecutionStep[] {
  const steps = record(record(result)?.execution)?.steps;
  if (!Array.isArray(steps)) return [];
  return steps.flatMap((value): RecordedExecutionStep[] => {
    const step = record(value);
    if (!step || (step.name !== "data_pipeline" && step.name !== "inference")) return [];
    const finish = typeof step.finished_at === "string" ? Date.parse(step.finished_at) : NaN;
    const completed = step.return_code === 0 && Number.isFinite(finish);
    const command = Array.isArray(step.command) ? step.command : [];
    const backend = command.find((part) => typeof part === "string" && part.startsWith("--jax_backend="))?.split("=")[1];
    const device = backend === "gpu" || backend === "cuda" ? "GPU" : backend === "cpu" ? "CPU" : backend === "mps" ? "Apple GPU" : null;
    return [{
      name: step.name,
      completed,
      elapsedSeconds: completed ? validSeconds(step.elapsed_seconds) ? step.elapsed_seconds : elapsedSeconds(step.started_at, finish) : null,
      device,
      startedAt: validTimestamp(step.started_at),
      finishedAt: validTimestamp(step.finished_at),
      failed: Number.isFinite(finish) && typeof step.return_code === "number" && step.return_code !== 0,
    }];
  });
}

const validTimestamp = (value: unknown): string | null => typeof value === "string" && Number.isFinite(Date.parse(value)) ? value : null;
const activeStatuses = new Set(["running", "queued", "preparing", "submitted"]);

export function predictionExecutionProgress(result: unknown, stageValue: unknown, status: string, checkedAt: string | null, now: number) {
  const execution = record(record(result)?.execution), stage = record(stageValue);
  const active = activeStatuses.has(status);
  const finishedAt = active ? null : validTimestamp(stage?.finished_at) || validTimestamp(execution?.finished_at);
  const totalSeconds = finishedAt ? elapsedSeconds(execution?.started_at, Date.parse(finishedAt)) : active ? elapsedSeconds(execution?.started_at, now) : null;
  const stageStart = validTimestamp(stage?.started_at);
  const stageSeconds = active ? elapsedSeconds(stageStart, now) : validSeconds(stage?.elapsed_seconds) ? stage.elapsed_seconds : null;
  const recordedHeartbeat = validTimestamp(stage?.checked_at) || validTimestamp(execution?.last_checked_at);
  // A previous CPU stage's heartbeat does not establish that the GPU stage is alive.
  const heartbeatAt = recordedHeartbeat && (!stageStart || Date.parse(recordedHeartbeat) >= Date.parse(stageStart)) ? recordedHeartbeat : null;
  const heartbeatAge = elapsedSeconds(heartbeatAt, now);
  return {
    active,
    totalSeconds,
    stageSeconds,
    finishedAt,
    heartbeatAt,
    heartbeatAge,
    heartbeatState: !active ? "finished" : status !== "running" ? "waiting" : heartbeatAge === null ? "unrecorded" : heartbeatAge >= 30 ? "stale" : "recent",
    responseAge: elapsedSeconds(checkedAt, now),
  };
}

const detailNames = new Set(["feature_preparation", "model_inference", "result_extraction", "writing_outputs", "job_finished", "msa_search", "template_search"]);
export function recordedStageDetail(stageValue: unknown): { label: string; observedAt: string; name: string; activity: string | null; completedDatabases: string[] } | null {
  const detail = record(record(stageValue)?.detail), observedAt = validTimestamp(detail?.observed_at);
  if (!detail || detail.source !== "run.log" || typeof detail.name !== "string" || !detailNames.has(detail.name) || typeof detail.label !== "string" || !detail.label.trim() || !observedAt) return null;
  const completedDatabases = Array.isArray(detail.completed_databases) && detail.completed_databases.length <= 4 ? [...new Set(detail.completed_databases.filter((value): value is string => typeof value === "string" && ["BFD", "MGnify", "UniRef90", "UniProt"].includes(value)))] : [];
  return { label: detail.label.slice(0, 300), observedAt, name: detail.name, activity: typeof detail.activity === "string" && detail.activity.trim() ? detail.activity.slice(0, 300) : null, completedDatabases };
}

export type ExecutionTimelineItem = {
  name: string;
  label: string;
  state: "completed" | "active" | "pending" | "skipped" | "failed";
  note: string;
  elapsedSeconds: number | null;
};

export function predictionExecutionTimeline(result: unknown, stageValue: unknown, status: string, msaMode: string): ExecutionTimelineItem[] {
  const stage = record(stageValue), features = record(record(result)?.msa_features);
  const steps = recordedExecutionSteps(result);
  const makeStep = (name: RecordedExecutionStep["name"], label: string): ExecutionTimelineItem => {
    const step = [...steps].reverse().find((item) => item.name === name);
    const active = activeStatuses.has(status) && stage?.name === name;
    return { name, label, state: step?.completed ? "completed" : step?.failed ? "failed" : active ? "active" : "pending", note: step?.completed ? "완료" : step?.failed ? "종료 · 오류" : active ? "현재 단계" : activeStatuses.has(status) ? "대기" : "단계 기록 없음", elapsedSeconds: step?.elapsedSeconds ?? null };
  };
  const search = makeStep("data_pipeline", "CPU · MSA·템플릿 검색");
  if (msaMode === "none") Object.assign(search, { state: "skipped", note: "생략한 계산 조건" });
  else if (features?.status === "combined_uncached") Object.assign(search, { state: "skipped", note: "AF3 추론과 통합 실행" });
  else if (features?.status === "ready" && features.cache_hit === true && search.state !== "completed") Object.assign(search, { state: "skipped", note: "기존 검색 결과 재사용" });
  const inference = makeStep("inference", features?.status === "combined_uncached" ? "통합 MSA 검색·AF3 추론" : "AF3 · 구조 추론");
  const outputActive = activeStatuses.has(status) && stage?.name === "output_validation";
  const output: ExecutionTimelineItem = { name: "output_validation", label: "출력 파일 처리·성분 확인", state: status === "completed" ? "completed" : outputActive ? "active" : "pending", note: status === "completed" ? "처리 종료" : outputActive ? "현재 단계" : activeStatuses.has(status) ? "대기" : "단계 기록 없음", elapsedSeconds: null };
  return [search, inference, output];
}

export function gpuMemoryWarning(result: unknown): { message: string; observedAt: string | null } | null {
  const warning = record(record(result)?.runtime_warning);
  if (warning?.code !== "gpu_memory_pressure" || typeof warning.message !== "string" || !warning.message.trim()) return null;
  return { message: warning.message, observedAt: typeof warning.observed_at === "string" && Number.isFinite(Date.parse(warning.observed_at)) ? warning.observed_at : null };
}
