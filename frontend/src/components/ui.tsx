import type { ReactNode } from "react";
import type { Outcome } from "../api/types";

export const OUTCOME_LABELS: Record<Outcome, string> = {
  PRESENT: "Present",
  POSSIBLE_CROSS_CONTACT: "Possible cross-contact",
  UNKNOWN: "Unknown",
  NOT_LISTED_IN_VERIFIED_RECORDS: "Not listed in verified records",
};

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  return <span className={`badge o-${outcome}`}>{OUTCOME_LABELS[outcome]}</span>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden /> {label}
    </div>
  );
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="alert error" role="alert">
      {msg}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small className="muted">{hint}</small>}
    </label>
  );
}

export function formatDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
