export const WORKSPACE_TABS = ["alphafold", "comparison", "quantum", "discovery", "validation", "agents", "evidence", "archive"] as const;
export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];

export function workspaceFromHash(hash: string): WorkspaceTab {
  const value = hash.replace(/^#\/?/, "");
  if (value === "studio") return "alphafold";
  return WORKSPACE_TABS.includes(value as WorkspaceTab) ? value as WorkspaceTab : "alphafold";
}

export function readWorkspace(): WorkspaceTab {
  return typeof window === "undefined" ? "alphafold" : workspaceFromHash(window.location.hash);
}
