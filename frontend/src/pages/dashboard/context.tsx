import { createContext, useContext } from "react";
import type { Dashboard } from "../../api/types";

export interface DashCtx {
  rid: number;
  dash: Dashboard;
  reload: () => void;
  canEdit: boolean; // owner or manager
  isOwner: boolean;
}

export const DashboardContext = createContext<DashCtx | null>(null);

export function useDash(): DashCtx {
  const ctx = useContext(DashboardContext);
  if (!ctx) throw new Error("useDash must be used inside the dashboard");
  return ctx;
}
