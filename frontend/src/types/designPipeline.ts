export type DesignMode = "combination" | "hybrid" | "transform";
export type DesignInput = {
  id: string;
  name: string;
  smiles: string;
  category: "herbal" | "natural_product" | "drug";
  source_url?: string;
  descriptors?: Record<string, unknown>;
  evidence?: DesignEvidence[];
};
export type DesignEvidence = string | {
  status?: string;
  endpoint?: string;
  label?: string | number | boolean;
  title?: string;
  message?: string;
  summary?: string;
  text?: string;
  value?: unknown;
  unit?: string;
  source_url?: string;
  [key: string]: unknown;
};
export type DesignCandidate = {
  id: string;
  name: string;
  smiles?: string;
  kind: string;
  parents: Array<string | DesignInput>;
  components?: DesignInput[];
  descriptors?: Record<string, unknown>;
  descriptor_delta?: Record<string, unknown>;
  evidence?: DesignEvidence[];
  expected_effects?: DesignEvidence[];
  limitations?: string[];
  [key: string]: unknown;
};
export type DesignRequest = {
  name: string;
  mode: DesignMode;
  compounds: DesignInput[];
  target_accession: string;
  max_candidates: number;
  transformations: string[];
  seed: number;
};
export type DesignStage = {
  id: string;
  label: string;
  agent: string;
  status: string;
  summary?: string;
  started?: string;
  finished?: string;
  depends_on?: string[];
};
export type DesignRun = {
  id: string;
  name: string;
  status: string;
  created: string;
  updated: string;
  request: DesignRequest;
  stages: DesignStage[];
  events: Array<{ seq: number; stage: string; status: string; message: string; time: string }>;
  result: {
    candidates: DesignCandidate[];
    summary: string | Record<string, unknown>;
    limitations?: string[];
    [key: string]: unknown;
  } | null;
  error?: string | null;
};
export type DesignOptions = {
  max_candidates?: number;
  limits?: { max_candidates?: number; max_compounds?: number };
  modes?: Array<{ id: DesignMode; label: string; description?: string }>;
  transformations?: Array<{ id: string; label: string; description?: string }>;
  stages?: Array<{ id: string; label: string; agent: string; depends_on?: string[] }>;
  [key: string]: unknown;
};
