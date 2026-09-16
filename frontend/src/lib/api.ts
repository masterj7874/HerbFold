let apiToken = "";
export function setApiToken(value: string) {
  apiToken = value;
}

export async function api<T = any>(
  path: string,
  body?: unknown,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
  };
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    ...options,
  });
  if (!response.ok) {
    let detail: any;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = response.statusText;
    }
    throw new Error(
      typeof detail === "string"
        ? detail
        : JSON.stringify(detail ?? `HTTP ${response.status}`),
    );
  }
  return response.json() as Promise<T>;
}

export async function readArtifact(
  jobId: string,
  filename: string,
): Promise<string> {
  const encoded = filename.split("/").map(encodeURIComponent).join("/");
  const response = await fetch(`/api/jobs/${jobId}/artifacts/${encoded}`, {
    headers: apiToken ? { Authorization: `Bearer ${apiToken}` } : {},
  });
  if (!response.ok) throw new Error("결과 파일을 불러오지 못했습니다.");
  return response.text();
}

export async function fetchText(path: string, body?: unknown): Promise<string> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error("구조 파일을 생성하지 못했습니다.");
  return response.text();
}

export function download(
  value: unknown,
  name: string,
  type = "application/json",
) {
  const blob = new Blob(
    [typeof value === "string" ? value : JSON.stringify(value, null, 2)],
    { type },
  );
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const formatNumber = (value: unknown, decimals = 2) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(decimals)
    : "—";
