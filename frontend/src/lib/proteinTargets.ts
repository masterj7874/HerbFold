export type ProteinTarget = {
  accession: string;
  name: string;
  gene: string | null;
  organism: string;
  taxon_id?: number | null;
  length: number;
  sequence: string;
  sequence_sha256: string;
  source: string;
  retrieved_at?: string;
  registered_at?: string;
  reviewed?: boolean | null;
  sequence_version?: number | null;
  entry_version?: number | null;
};

export const normalizeTargetAccession = (value: string) => value.trim().toUpperCase();
export const validTargetAccession = (value: string) => /^[A-Z0-9][A-Z0-9-]{1,19}$/.test(normalizeTargetAccession(value));

export function mergeProteinTargets(previous: ProteinTarget[], incoming: ProteinTarget[]): ProteinTarget[] {
  const records = new Map(previous.map((record) => [record.accession, record]));
  for (const record of incoming) records.set(record.accession, record);
  return [...records.values()].sort((a, b) => a.accession.localeCompare(b.accession));
}

export function targetSequencePreview(record: ProteinTarget): { text: string; shown: number; truncated: boolean } {
  const sequence = record.sequence.slice(0, 120);
  return { text: sequence.match(/.{1,10}/g)?.join(" ") || "", shown: sequence.length, truncated: record.sequence.length > sequence.length };
}

export const targetEntryUrl = (accession: string) => `https://www.uniprot.org/uniprotkb/${encodeURIComponent(accession)}/entry`;

// A later draft or selection invalidates even a late response from a transport
// that ignored abort. Only the currently requested accession may be applied.
export function createTargetRequestGuard() {
  let revision = 0;
  let controller: AbortController | null = null;
  const invalidate = () => { revision++; controller?.abort(); controller = null; };
  return {
    invalidate,
    begin() {
      invalidate();
      const currentRevision = revision;
      const currentController = new AbortController();
      controller = currentController;
      return {
        signal: currentController.signal,
        isCurrent: () => revision === currentRevision && !currentController.signal.aborted,
      };
    },
  };
}
