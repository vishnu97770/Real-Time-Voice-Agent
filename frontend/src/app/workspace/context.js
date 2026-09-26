import { createContext, useContext } from "react";

export const WorkspaceContext = createContext(null);

// { serverAvailable, status, error, agents, contacts, workflows, jobs, calls, organization, now,
//   refresh(), agentById(id) }
export function useWorkspace() {
  const value = useContext(WorkspaceContext);

  if (!value) throw new Error("useWorkspace must be used inside <WorkspaceProvider>");

  return value;
}
