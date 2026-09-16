export class PredictionRequestTimeout extends Error {
  constructor() {
    super("서버 응답 시간이 초과되었습니다.");
    this.name = "PredictionRequestTimeout";
  }
}

// The deadline includes reading the response body, and also settles requests
// whose transport does not respond to abort (for example a stalled stream).
export function predictionRequest<T>(request: (signal: AbortSignal) => Promise<T>, {
  signal, timeoutMs = 15_000,
}: { signal?: AbortSignal; timeoutMs?: number } = {}): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const controller = new AbortController();
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      callback();
    };
    const abort = () => {
      finish(() => reject(signal?.reason || new DOMException("Request aborted", "AbortError")));
      controller.abort();
    };
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener("abort", abort, { once: true });
    timer = setTimeout(() => {
      finish(() => reject(new PredictionRequestTimeout()));
      controller.abort();
    }, timeoutMs);
    Promise.resolve().then(() => request(controller.signal)).then(
      (value) => finish(() => resolve(value)),
      (problem) => finish(() => reject(problem)),
    );
  });
}

// Run each endpoint independently: a missing log must never delay a terminal
// job status. Errors remain retryable without retaining an unbounded request.
export function pollPrediction<T>({ signal, request, onValue, onError, shouldRepeat, isCurrent = () => true, timeoutMs = 15_000, intervalMs = 2_500 }: {
  signal: AbortSignal;
  request: (signal: AbortSignal) => Promise<T>;
  onValue: (value: T) => void;
  onError: (problem: Error) => void;
  shouldRepeat: (value: T) => boolean;
  isCurrent?: () => boolean;
  timeoutMs?: number;
  intervalMs?: number;
}): void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const current = () => !signal.aborted && isCurrent();
  const clear = () => clearTimeout(timer);
  signal.addEventListener("abort", clear, { once: true });
  async function poll() {
    if (!current()) { signal.removeEventListener("abort", clear); return; }
    let repeat = true;
    try {
      const value = await predictionRequest(request, { signal, timeoutMs });
      if (!current()) return;
      onValue(value);
      repeat = shouldRepeat(value);
    } catch (problem) {
      if (!current()) return;
      onError(problem instanceof Error ? problem : new Error(String(problem)));
    } finally {
      if (repeat && current()) timer = setTimeout(() => void poll(), intervalMs);
      else signal.removeEventListener("abort", clear);
    }
  }
  void poll();
}
