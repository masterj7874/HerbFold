import { tr, localeCode, msg } from "../lib/i18n";
import {
  ArrowDownToLine,
  Box,
  ExternalLink,
  FileUp,
  FolderOpen,
  RefreshCw,
} from "lucide-react";
import { useState } from "react";
import { api, download, readArtifact } from "../lib/api";
import type { Job } from "../types/app";
import QuantumPanel from "./QuantumPanel";

export default function ArchivePanel({
  jobs,
  refresh,
  onViewStructure,
  notify,
}: {
  jobs: Job[];
  refresh: () => Promise<void>;
  onViewStructure: (jobId: string, file: string) => Promise<void>;
  notify: (message: string, error?: boolean) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [importing, setImporting] = useState(false);
  async function importFiles(files: FileList | null) {
    if (!files?.length) return;
    setImporting(true);
    try {
      const selected = Array.from(files);
      if (
        selected.length > 100 ||
        selected.reduce((total, file) => total + file.size, 0) > 40_000_000
      )
        throw new Error("AF3 파일은 100개, 합계 40 MB까지 가져올 수 있습니다.");
      if (new Set(selected.map((file) => file.name)).size !== selected.length)
        throw new Error(
          "파일 이름이 중복됩니다. 하나의 AF3 결과 폴더에서 파일을 선택하세요.",
        );
      const payload: Record<string, string> = {};
      for (const file of selected) payload[file.name] = await file.text();
      const job = await api<Job>("/af3/import", { files: payload });
      setExpanded(job.id);
      await refresh();
      if (job.status !== "completed")
        throw new Error(
          job.error ||
            job.result?.warnings?.join(" · ") ||
            "AF3 구조를 찾지 못했습니다. 같은 이름 접두사의 _model.cif와 _summary_confidences.json 파일을 함께 선택하세요.",
        );
      notify(
        `외부 AF3 구조 ${job.result.model_count}개를 가져왔습니다. 가져온 파일만으로 실제 실행 여부를 확인할 수 없습니다.`,
      );
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setImporting(false);
    }
  }
  async function refreshJobs() {
    try {
      await refresh();
    } catch (error) {
      notify((error as Error).message, true);
    }
  }
  async function viewStructure(job: Job, model: any) {
    try {
      await onViewStructure(job.id, `output/${model.structure_path}`);
    } catch (error) {
      notify((error as Error).message, true);
    }
  }
  const visible = jobs.filter(
    (job) =>
      filter === "all" ||
      (filter === "structure"
        ? job.kind.includes("af3") || job.kind.startsWith("alphafold")
        : filter === "quantum"
          ? ["quantum", "kernel_experiment"].includes(job.kind)
          : job.kind === filter),
  );
  return (
    <div className="archive-panel card">
      <div className="section-heading">
        <div>
          <span className="eyebrow">REPRODUCIBLE RESEARCH</span>
          <h3>{tr("입력에서 결과까지, 이어지는 기록")}</h3>
        </div>
        <div className="inline-actions">
          <label className="secondary-button import-button">
            <FileUp size={15} />{tr(" ")}
            {tr(importing ? "가져오는 중…" : "AF3 결과 가져오기")}
            <input
              type="file"
              accept=".json,.cif"
              multiple
              disabled={importing}
              onChange={(event) => {
                void importFiles(event.target.files);
                event.target.value = "";
              }}
            />
          </label>
          <button
            className="icon-button"
            onClick={refreshJobs}
            aria-label={tr("실행 기록 새로고침")}
          >
            <RefreshCw size={17} />
          </button>
        </div>
      </div>
      <div className="filter-tabs">
        {tr([
          ["all", "전체 기록"],
          ["structure", "단백질 구조"],
          ["quantum", "양자 실행"],
          ["model", "학습 모델"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={filter === key ? "active" : ""}
            onClick={() => setFilter(key)}
          >
            {tr(label)}
          </button>
        )))}
      </div>
      <div className="archive-table">
        <div className="archive-row table-head">
          <span>WORKFLOW / JOB</span>
          <span>STATUS</span>
          <span>CREATED</span>
          <span>ACTIONS</span>
        </div>
        {tr(visible.map((job) => (
          <div key={job.id}>
            <div className="archive-row">
              <div className="archive-name">
                <span className="file-icon">
                  <FolderOpen size={19} />
                </span>
                <div>
                  <strong>{tr(job.kind.replaceAll("_", " "))}</strong>
                  <small>{tr(job.id.slice(0, 16))}</small>
                </div>
              </div>
              <span className={`status-pill ${job.status}`}>{tr(job.status)}</span>
              <time>{tr(new Date(job.created).toLocaleString(localeCode()))}</time>
              <div className="inline-actions">
                <button
                  className="text-button"
                  onClick={() =>
                    setExpanded(expanded === job.id ? null : job.id)
                  }
                >
                  {tr("결과 보기 ")}<ExternalLink size={13} />
                </button>
                <button
                  className="icon-button"
                  aria-label={tr(msg("{0} 내보내기", job.id))}
                  onClick={() => download(job, `${job.kind}-${job.id}.json`)}
                >
                  <ArrowDownToLine size={15} />
                </button>
              </div>
            </div>
            {tr(expanded === job.id && (
              <div className="archive-detail">
                {tr(job.error && <p className="field-warning">{tr(job.error)}</p>)}
                {tr(job.result?.models?.map((model: any, i: number) => (
                  <div key={i} className="structure-file">
                    <div>
                      <Box size={18} />
                      <span>
                        <strong>{tr(model.name || model.structure_path)}</strong>
                        <small>
                          ipTM {tr(model.metrics?.iptm ?? "—")} ·{tr(" ")}
                          {tr(model.metrics?.has_clash
                            ? "충돌 감지"
                            : "구조 신뢰도")}
                        </small>
                      </span>
                    </div>
                    <button
                      className="secondary-button"
                      onClick={() => viewStructure(job, model)}
                    >
                      {tr("3D 구조 열기 ")}<ExternalLink size={13} />
                    </button>
                    <button
                      className="icon-button"
                      aria-label={tr("mmCIF 다운로드")}
                      onClick={async () => {
                        try {
                          download(
                            await readArtifact(
                              job.id,
                              `output/${model.structure_path}`,
                            ),
                            model.structure_path.split("/").pop()!,
                            "chemical/x-cif",
                          );
                        } catch (error) {
                          notify((error as Error).message, true);
                        }
                      }}
                    >
                      <ArrowDownToLine size={15} />
                    </button>
                  </div>
                )))}
                {tr(job.kind === "quantum" && <QuantumPanel value={{ ...job.result, mode: job.result?.mode || job.payload.mode, status: job.result?.status || job.status, sample_ids: job.result?.sample_ids || job.payload.sample_ids, sample_labels: job.result?.sample_labels || job.payload.sample_labels }} />)}
                {tr(job.kind === "quantum" ? <details><summary>{tr("전체 저장 JSON")}</summary><pre>{JSON.stringify(job.result, null, 2)}</pre></details> : <pre>{JSON.stringify(job.result, null, 2)}</pre>)}
              </div>
            ))}
          </div>
        )))}
      </div>
      {tr(!visible.length && (
        <div className="empty-large">
          <FolderOpen size={35} />
          <p>{tr("이 유형의 실행 기록이 없습니다.")}</p>
        </div>
      ))}
    </div>
  );
}

