import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ReviewItem, ReviewKind } from "../api/types";
import { ErrorAlert, formatDate, Spinner } from "./ui";

export const REVIEW_KIND: Record<ReviewKind, string> = {
  low_confidence_product: "Product identified with low confidence",
  multiple_product_matches: "Several products matched",
  unreadable_label: "Label could not be read",
  incomplete_label: "Label may be incomplete",
  low_confidence_dish: "Dish matched with low confidence",
  multiple_dish_matches: "Several dishes matched",
  unmatched_dish: "Dish not on the menu",
};

type Candidate = { name?: string; barcode?: string; confidence?: number; similarity?: number };

function Details({ d }: { d: Record<string, unknown> }) {
  const cands = (d.candidates ?? d.closest) as Candidate[] | undefined;
  return (
    <div style={{ marginTop: "0.3rem" }}>
      {cands && cands.length > 0 && (
        <small className="muted">
          Candidates: {cands.map((c) => `${c.name}${c.confidence != null ? ` (${Math.round(c.confidence * 100)}%)` : c.similarity != null ? ` (${Math.round(c.similarity * 100)}%)` : ""}`).join(", ")}
        </small>
      )}
      {typeof d.text === "string" && d.text && <p className="mono" style={{ margin: "0.3rem 0" }}>{d.text}</p>}
      {Array.isArray(d.image_issues) && d.image_issues.length > 0 && <div><small className="muted">Photo issues: {(d.image_issues as string[]).join("; ")}</small></div>}
      {typeof d.confidence === "number" && <div><small className="muted">Match confidence: {Math.round((d.confidence as number) * 100)}%</small></div>}
      {typeof d.ocr_confidence === "number" && <div><small className="muted">Reading confidence: {Math.round((d.ocr_confidence as number) * 100)}%</small></div>}
    </div>
  );
}

/** Shared by the restaurant dashboard (its own items) and the admin area (all items). */
export function ReviewQueue({ base, canDecide, intro }: { base: string; canDecide: boolean; intro: string }) {
  const [tab, setTab] = useState<"open" | "resolved" | "dismissed">("open");
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = () => {
    setItems(null);
    api<ReviewItem[]>(`${base}?status=${tab}`).then(setItems).catch(setError);
  };
  useEffect(load, [base, tab]); // eslint-disable-line react-hooks/exhaustive-deps

  const decide = async (id: number, status: "resolved" | "dismissed") => {
    setBusy(id);
    setError(null);
    try {
      await api(`${base}/${id}/decision`, { method: "POST", json: { status, note: notes[id] ?? "" } });
      load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <p className="muted">{intro}</p>
      <div className="tabs" role="tablist">
        {(["open", "resolved", "dismissed"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
        ))}
      </div>
      <ErrorAlert error={error} />
      {!items && !error && <Spinner />}
      {items?.length === 0 && <p className="muted">Nothing here.</p>}
      {items?.map((i) => (
        <div className="card" key={i.id}>
          <div className="spread">
            <strong>{REVIEW_KIND[i.kind] ?? i.kind}</strong>
            <span className="badge neutral">{i.occurrences}×</span>
          </div>
          <p style={{ margin: "0.3rem 0" }}>{i.summary}</p>
          <small className="muted">First seen {formatDate(i.first_seen)} · last {formatDate(i.last_seen)}</small>
          <Details d={i.details} />
          {i.status === "open" ? (
            canDecide && (
              <div className="row" style={{ marginTop: "0.5rem" }}>
                <input aria-label="Resolution note" placeholder="What did you do? (optional)" value={notes[i.id] ?? ""} onChange={(e) => setNotes({ ...notes, [i.id]: e.target.value })} style={{ flex: 1, minWidth: 180 }} />
                <button className="primary small" disabled={busy === i.id} onClick={() => decide(i.id, "resolved")}>Resolved</button>
                <button className="small" disabled={busy === i.id} onClick={() => decide(i.id, "dismissed")}>Dismiss</button>
              </div>
            )
          ) : (
            <div><small className="muted">{i.status} {formatDate(i.resolved_at)}{i.resolution_note && ` · "${i.resolution_note}"`}</small></div>
          )}
        </div>
      ))}
    </div>
  );
}
