import {
  ArrowDownToLine,
  ArrowRight,
  CheckCheck,
  Database,
  FileInput,
  FlaskConical,
  Layers3,
  LoaderCircle,
  Microscope,
  Play,
  ScanLine,
  Upload,
} from "lucide-react";
import { useState } from "react";
import { api, download, formatNumber } from "../lib/api";
import type { Compound } from "../types/app";

export default function DataPanel({
  candidates,
  notify,
}: {
  candidates: Compound[];
  notify: (text: string, error?: boolean) => void;
}) {
  const [recordsText, setRecordsText] = useState("");
  const [target, setTarget] = useState("CHEMBL230");
  const [endpoint, setEndpoint] = useState("Ki");
  const [split, setSplit] = useState("scaffold");
  const [result, setResult] = useState<any>(null);
  const [audit, setAudit] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [modelId, setModelId] = useState("");
  const [queries, setQueries] = useState("");
  const [resultType, setResultType] = useState("evaluation");
  const count = (() => {
    try {
      const value = JSON.parse(recordsText);
      return Array.isArray(value) ? value.length : 0;
    } catch {
      return 0;
    }
  })();
  function updateRecords(value: string) {
    setRecordsText(value);
    setAudit(null);
  }
  function request() {
    return { records: JSON.parse(recordsText), endpoint, split, seed: 42 };
  }
  async function execute(key: string, task: () => Promise<void>) {
    setBusy(key);
    try {
      await task();
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy(null);
    }
  }
  async function importCSV(file: File | undefined) {
    if (!file) return;
    const text = await file.text();
    const response = await api("/data/csv", undefined, {
      method: "POST",
      body: text,
    });
    updateRecords(JSON.stringify(response.records, null, 2));
    setResult(response);
    setResultType("source");
    notify(`${response.records.length}개 레코드를 불러왔습니다.`);
  }
  async function loadExample() {
    const data = await api("/evidence/example");
    updateRecords(JSON.stringify(data.records, null, 2));
    setTarget(data.provenance.target.target_chembl_id);
    setEndpoint(data.protocol.endpoint);
    setSplit(data.protocol.split);
    setResult(data);
    setResultType("source");
    notify(
      `ChEMBL PTGS2 ${data.protocol.endpoint} 실측 예제 ${data.records.length}개를 가져왔습니다. 여러 assay를 포함한 기술 검증용 데이터입니다.`,
    );
  }
  const metrics = result?.metrics || result?.evaluation?.metrics || {};
  return (
    <div className="evidence-page">
      <div className="evidence-summary">
        <div>
          <Database size={21} />
          <span>IMPORTED RECORDS</span>
          <strong>
            {count}
            <small> records</small>
          </strong>
        </div>
        <div>
          <FlaskConical size={21} />
          <span>MEASURED ENDPOINT</span>
          <strong>
            {endpoint}
            <small> separate labels</small>
          </strong>
        </div>
        <div>
          <ScanLine size={21} />
          <span>EVALUATION SPLIT</span>
          <strong>
            {split === "scaffold_target" ? "Scaffold + target" : split}
            <small> seed 42</small>
          </strong>
        </div>
      </div>
      <div className="evidence-columns">
        <div className="card evidence-input">
          <div className="section-heading">
            <div>
              <span className="eyebrow">01 / SOURCE DATA</span>
              <h3>실측 근거를 연결하세요</h3>
            </div>
            <FileInput size={22} />
          </div>
          <div className="form-grid">
            <label>
              ChEMBL target
              <input
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              />
            </label>
            <label>
              실측 종말점
              <select
                value={endpoint}
                onChange={(event) => setEndpoint(event.target.value)}
              >
                <option>Kd</option>
                <option>Ki</option>
              </select>
            </label>
          </div>
          <div className="inline-actions">
            <button
              className="secondary-button grow"
              disabled={!!busy}
              onClick={() =>
                execute("chembl", async () => {
                  const data = await api(
                    `/sources/chembl/${encodeURIComponent(target.trim().toUpperCase())}?endpoint=${encodeURIComponent(endpoint)}&limit=100`,
                  );
                  updateRecords(JSON.stringify(data.records, null, 2));
                  setTarget(target.trim().toUpperCase());
                  setResult(data);
                  setResultType("source");
                  notify(
                    `ChEMBL 실측 ${data.records.length}개를 가져왔습니다.`,
                  );
                })
              }
            >
              <Database size={15} /> ChEMBL 가져오기
            </button>
            <button
              className="secondary-button"
              disabled={!!busy}
              onClick={() => execute("example", loadExample)}
            >
              PTGS2 실측 예제
            </button>
          </div>
          <label className="file-drop">
            <Upload size={20} />
            <span>
              <strong>CSV 파일 불러오기</strong>
              <small>원본 값과 출처를 함께 보존합니다.</small>
            </span>
            <input
              type="file"
              accept=".csv"
              disabled={!!busy}
              onChange={(event) =>
                execute("csv", () => importCSV(event.target.files?.[0]))
              }
            />
          </label>
          <p className="field-help">
            smiles · target_id · endpoint · value · unit · relation ·
            is_measured · source가 필요합니다. Kd와 Ki를 혼합하지 않습니다.
          </p>
          <label>
            레코드 편집 <small>{count} rows</small>
            <textarea
              className="code-textarea"
              value={recordsText}
              disabled={!!busy}
              onChange={(event) => updateRecords(event.target.value)}
              rows={10}
              placeholder="측정 레코드 JSON"
            />
          </label>
          <div className="inline-actions">
            <button
              className="secondary-button grow"
              disabled={!!busy || !count}
              onClick={() =>
                execute("audit", async () => {
                  const data = await api("/data/audit", request());
                  setAudit(data);
                  setResult(data);
                  setResultType("audit");
                })
              }
            >
              <CheckCheck size={15} /> 중복·assay 점검
            </button>
            <button
              className="secondary-button"
              disabled={!!busy || !count}
              onClick={() =>
                execute("structures", async () => {
                  const data = await api("/data/structures", request());
                  updateRecords(JSON.stringify(data.records, null, 2));
                  setResult(data);
                  setResultType("structures");
                })
              }
            >
              <Layers3 size={15} /> AF3 특징 연결
            </button>
          </div>
          {audit && Array.isArray(audit.suggested_records) && (
            <>
              <p className="field-help">
                입력 {audit.input_count}개 · 유효 {audit.valid_count}개 · 제안{" "}
                {audit.suggested_count}개 · 제외{" "}
                {audit.excluded_input_indices.length}개. 제외 사유와 변경 내용을
                검증 결과에서 검토한 후 적용하세요.
              </p>
              <button
                className="text-button"
                onClick={() => {
                  updateRecords(
                    JSON.stringify(audit.suggested_records, null, 2),
                  );
                  notify(
                    "제안 레코드를 적용했습니다. 제외 항목과 assay 비교 가능성을 확인하세요.",
                  );
                }}
              >
                점검 제안 {audit.suggested_records.length}개 적용{" "}
                <ArrowRight size={14} />
              </button>
            </>
          )}
          <p className="field-help">
            AF3 특징 연결은 각 레코드의 af3_job_id와 정확한 protein_sequence가
            필요합니다. 완료된 로컬 실행의 분자·서열·구조 해시와 품질을
            검증합니다.
          </p>
          <div className="subsection-line" />
          <div className="section-heading">
            <div>
              <span className="eyebrow">02 / EVALUATION</span>
              <h3>예측의 적용 범위를 확인합니다</h3>
            </div>
          </div>
          <label>
            평가 분할
            <select
              value={split}
              onChange={(event) => setSplit(event.target.value)}
            >
              <option value="scaffold">Scaffold 분리 · 단일 표적</option>
              <option value="scaffold_target">
                Scaffold + target 동시 분리
              </option>
              <option value="target">Target 분리</option>
            </select>
          </label>
          <div className="inline-actions">
            <button
              className="secondary-button grow"
              disabled={!!busy || !count}
              onClick={() =>
                execute("evaluate", async () => {
                  const job = await api("/benchmark/evaluate", request());
                  setResult(job.result);
                  setResultType("evaluation");
                })
              }
            >
              <Microscope size={16} /> 평가
            </button>
            <button
              className="primary-button grow"
              disabled={!!busy || !count}
              onClick={() =>
                execute("train", async () => {
                  const job = await api("/models/train", request());
                  setModelId(job.result.model_id);
                  setResult(job.result);
                  setResultType("evaluation");
                  notify("보류 데이터 평가 후 모델을 저장했습니다.");
                })
              }
            >
              {busy === "train" ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Play size={15} />
              )}{" "}
              평가 · 학습
            </button>
          </div>
          <button
            className="text-button"
            disabled={!!busy || !count}
            onClick={() =>
              execute("quantum", async () => {
                const job = await api("/quantum/experiments/local", request());
                setResult(job.result.evaluation);
                setResultType("quantum");
              })
            }
          >
            실측 8–24개로 로컬 4큐빗 · RBF 모델 비교 <ArrowRight size={14} />
          </button>
        </div>
        <div className="evidence-right">
          <div className="card">
            <div className="section-heading">
              <div>
                <span className="eyebrow">EVIDENCE & OUTCOME</span>
                <h3>
                  {resultType === "prediction"
                    ? "모델 예측 · 실측 아님"
                    : resultType === "source"
                      ? "가져온 데이터 · 출처"
                      : "검증 결과"}
                </h3>
              </div>
              {result && (
                <button
                  className="icon-button"
                  aria-label="검증 결과 내보내기"
                  onClick={() => download(result, "evidence-result.json")}
                >
                  <ArrowDownToLine size={17} />
                </button>
              )}
            </div>
            {busy && (
              <div className="processing-line">
                <LoaderCircle className="spin" size={17} />
                <span>데이터를 처리하고 있습니다.</span>
              </div>
            )}
            {result ? (
              <>
                {Object.keys(metrics).length > 0 && (
                  <div className="metric-grid">
                    {Object.entries(metrics)
                      .slice(0, 6)
                      .map(([key, value]) => (
                        <div key={key}>
                          <small>{`${key.replaceAll("_", " ")}${typeof value === "object" && value ? " · RMSE" : ""}`}</small>
                          <strong>
                            {typeof value === "number"
                              ? formatNumber(value, 3)
                              : typeof value === "object" && value
                                ? formatNumber((value as any).rmse, 3)
                                : String(value)}
                          </strong>
                        </div>
                      ))}
                  </div>
                )}
                <pre className="results-json">
                  {JSON.stringify(result, null, 2)}
                </pre>
              </>
            ) : (
              <div className="empty-large">
                <ScanLine size={42} />
                <h4>근거가 있는 예측을 위해</h4>
                <p>
                  데이터를 불러오고 분할을 선택하면
                  <br />
                  실측값에 대한 오차와 적용 한계를 확인할 수 있습니다.
                </p>
              </div>
            )}
          </div>
          <div className="card prediction-card">
            <span className="eyebrow">03 / CANDIDATE PREDICTION</span>
            <h3>후보를 실측 학습 모델로 평가</h3>
            <label>
              저장된 모델 ID
              <input
                value={modelId}
                onChange={(event) => setModelId(event.target.value)}
                placeholder="모델 학습 후 자동 입력"
              />
            </label>
            <button
              className="text-button"
              disabled={!candidates.length}
              onClick={() =>
                setQueries(
                  JSON.stringify(
                    candidates.map((c) => ({
                      smiles: c.smiles,
                      target_id: target.trim().toUpperCase(),
                    })),
                    null,
                    2,
                  ),
                )
              }
            >
              생성 후보 {candidates.length}개를 질의로 사용{" "}
              <ArrowRight size={14} />
            </button>
            <label>
              예측 질의
              <textarea
                className="code-textarea"
                rows={5}
                value={queries}
                onChange={(event) => setQueries(event.target.value)}
                placeholder='[{"smiles":"...","target_id":"CHEMBL230"}]'
              />
            </label>
            <button
              className="primary-button wide"
              disabled={!!busy || !modelId || !queries}
              onClick={() =>
                execute("predict", async () => {
                  const data = await api("/models/predict", {
                    model_id: modelId,
                    queries: JSON.parse(queries),
                  });
                  setResult(data);
                  setResultType("prediction");
                })
              }
            >
              후보 친화도 예측 <ArrowRight size={16} />
            </button>
            <p className="field-help">
              학습 범위를 벗어난 후보는 외삽입니다. 계산값은 효능·독성·실험
              결합값을 보장하지 않습니다.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
